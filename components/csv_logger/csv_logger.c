/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
 * Written by Ali Slim <ali@meatpi.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "cJSON.h"

#include "csv_logger.h"
#include "sdcard.h"
#include "vehicle.h"
#include "wc_timer.h"

static const char *TAG = "CSV_LOGGER";

#define CSV_LOGGER_QUEUE_LEN        256
#define CSV_LOGGER_TASK_STACK_SIZE  (1024 * 6)
#define CSV_LOGGER_ROTATE_BYTES     (8 * 1024 * 1024)
#define CSV_LOGGER_FLUSH_PERIOD_MS  1000
#define CSV_LOGGER_IGNITION_POLL_MS 500
// Ignition-off must persist this long before the session closes, so a voltage
// dip during cranking doesn't split the log file.
#define CSV_LOGGER_IGN_OFF_DEBOUNCE_MS  3000
#define CSV_LOGGER_SD_RETRY_MS      5000

typedef struct
{
    int64_t t_ms;                          // ms since boot, captured at sample time
    float value;
    char name[CSV_LOGGER_NAME_MAX];
    char unit[CSV_LOGGER_UNIT_MAX];
    char source[CSV_LOGGER_SOURCE_MAX];
} csv_record_t;

static QueueHandle_t csv_queue = NULL;
static StaticQueue_t csv_queue_buffer;

static FILE *csv_file = NULL;
static char csv_file_path[128];
static size_t csv_file_bytes = 0;
static uint32_t csv_rows_written = 0;
static uint32_t csv_rows_dropped = 0;
static uint32_t csv_files_count = 0;
static bool csv_session_active = false;
static int64_t csv_sd_retry_after_ms = 0;

static bool csv_time_is_valid(void)
{
    time_t now = time(NULL);
    struct tm tm_now;
    localtime_r(&now, &tm_now);
    return (tm_now.tm_year + 1900) >= 2020;
}

static void csv_sanitize_field(char *s)
{
    for (; *s; s++)
    {
        if (*s == ',' || *s == '"' || *s == '\r' || *s == '\n')
        {
            *s = '_';
        }
    }
}

static esp_err_t csv_open_new_file(void)
{
    struct stat st;
    if (stat(CSV_LOGGER_DIR, &st) != 0)
    {
        if (mkdir(CSV_LOGGER_DIR, 0775) != 0)
        {
            ESP_LOGE(TAG, "Failed to create %s", CSV_LOGGER_DIR);
            return ESP_FAIL;
        }
    }

    if (csv_time_is_valid())
    {
        time_t now = time(NULL);
        struct tm tm_now;
        localtime_r(&now, &tm_now);
        snprintf(csv_file_path, sizeof(csv_file_path), CSV_LOGGER_DIR "/%04d%02d%02d_%02d%02d%02d.csv",
                 tm_now.tm_year + 1900, tm_now.tm_mon + 1, tm_now.tm_mday,
                 tm_now.tm_hour, tm_now.tm_min, tm_now.tm_sec);
    }
    else
    {
        snprintf(csv_file_path, sizeof(csv_file_path), CSV_LOGGER_DIR "/unknown_time_%lld.csv",
                 esp_timer_get_time() / 1000);
    }

    csv_file = fopen(csv_file_path, "w");
    if (csv_file == NULL)
    {
        ESP_LOGE(TAG, "Failed to open %s", csv_file_path);
        return ESP_FAIL;
    }

    int n = fprintf(csv_file, "timestamp_ms,source,name,value,unit\n");
    csv_file_bytes = (n > 0) ? (size_t)n : 0;
    csv_files_count++;
    ESP_LOGI(TAG, "CSV log started: %s", csv_file_path);
    return ESP_OK;
}

static void csv_close_file(void)
{
    if (csv_file != NULL)
    {
        fflush(csv_file);
        fsync(fileno(csv_file));
        fclose(csv_file);
        ESP_LOGI(TAG, "CSV log closed: %s (%u rows, %u bytes)",
                 csv_file_path, (unsigned)csv_rows_written, (unsigned)csv_file_bytes);
        csv_file = NULL;
        csv_file_bytes = 0;
    }
}

static bool csv_write_record(const csv_record_t *rec)
{
    char name[CSV_LOGGER_NAME_MAX];
    char unit[CSV_LOGGER_UNIT_MAX];
    char source[CSV_LOGGER_SOURCE_MAX];

    strlcpy(name, rec->name, sizeof(name));
    strlcpy(unit, rec->unit, sizeof(unit));
    strlcpy(source, rec->source, sizeof(source));
    csv_sanitize_field(name);
    csv_sanitize_field(unit);
    csv_sanitize_field(source);

    int n = fprintf(csv_file, "%lld,%s,%s,%.3f,%s\n", rec->t_ms, source, name, rec->value, unit);
    if (n < 0)
    {
        return false;
    }
    csv_file_bytes += (size_t)n;
    csv_rows_written++;
    return true;
}

static void csv_logger_task(void *pvParameters)
{
    csv_record_t rec;
    wc_timer_t flush_timer = 0;
    wc_timer_t ignition_poll_timer = 0;
    wc_timer_t ignition_off_timer = 0;
    bool ignition_on = false;
    bool ignition_off_pending = false;
    bool flush_pending = false;

    wc_timer_set(&flush_timer, CSV_LOGGER_FLUSH_PERIOD_MS);

    while (1)
    {
        bool got_record = (xQueueReceive(csv_queue, &rec, pdMS_TO_TICKS(250)) == pdPASS);

        // Track ignition with debounce on the off transition.
        if (wc_timer_is_expired(&ignition_poll_timer))
        {
            wc_timer_set(&ignition_poll_timer, CSV_LOGGER_IGNITION_POLL_MS);
            vehicle_ignition_state_t ign = vehicle_ignition_state();
            if (ign == VEHICLE_STATE_IGNITION_ON)
            {
                ignition_on = true;
                ignition_off_pending = false;
            }
            else if (ign == VEHICLE_STATE_IGNITION_OFF)
            {
                if (ignition_on && !ignition_off_pending)
                {
                    ignition_off_pending = true;
                    wc_timer_set(&ignition_off_timer, CSV_LOGGER_IGN_OFF_DEBOUNCE_MS);
                }
                else if (ignition_off_pending && wc_timer_is_expired(&ignition_off_timer))
                {
                    ignition_on = false;
                    ignition_off_pending = false;
                }
            }
            // VEHICLE_STATE_IGNITION_INVALID: keep last known state
        }

        // Session close: ignition went off or SD was pulled.
        if (csv_session_active && (!ignition_on || !sdcard_is_mounted()))
        {
            csv_close_file();
            csv_session_active = false;
        }

        if (!got_record)
        {
            // Idle: opportunistic flush so a power cut loses at most ~1s of data.
            if (csv_session_active && csv_file != NULL && flush_pending)
            {
                fflush(csv_file);
                fsync(fileno(csv_file));
                flush_pending = false;
            }
            continue;
        }

        if (!ignition_on)
        {
            continue;   // engine off: discard samples (autopid may still poll on charger)
        }

        // Session open, rate-limited if the SD card is missing or failing.
        if (!csv_session_active)
        {
            int64_t now_ms = esp_timer_get_time() / 1000;
            if (now_ms < csv_sd_retry_after_ms)
            {
                continue;
            }
            if (!sdcard_is_mounted() || csv_open_new_file() != ESP_OK)
            {
                csv_sd_retry_after_ms = now_ms + CSV_LOGGER_SD_RETRY_MS;
                continue;
            }
            csv_session_active = true;
            wc_timer_set(&flush_timer, CSV_LOGGER_FLUSH_PERIOD_MS);
        }

        if (!csv_write_record(&rec))
        {
            ESP_LOGE(TAG, "Write failed on %s, closing", csv_file_path);
            csv_close_file();
            csv_session_active = false;
            csv_sd_retry_after_ms = (esp_timer_get_time() / 1000) + CSV_LOGGER_SD_RETRY_MS;
            continue;
        }
        flush_pending = true;

        if (wc_timer_is_expired(&flush_timer))
        {
            wc_timer_set(&flush_timer, CSV_LOGGER_FLUSH_PERIOD_MS);
            fflush(csv_file);
            fsync(fileno(csv_file));
            flush_pending = false;
        }

        if (csv_file_bytes >= CSV_LOGGER_ROTATE_BYTES)
        {
            csv_close_file();
            if (csv_open_new_file() != ESP_OK)
            {
                csv_session_active = false;
                csv_sd_retry_after_ms = (esp_timer_get_time() / 1000) + CSV_LOGGER_SD_RETRY_MS;
            }
        }
    }
}

void csv_logger_record(const char *name, float value, const char *unit, const char *source)
{
    if (csv_queue == NULL || name == NULL)
    {
        return;
    }

    csv_record_t rec;
    rec.t_ms = esp_timer_get_time() / 1000;
    rec.value = value;
    strlcpy(rec.name, name, sizeof(rec.name));
    strlcpy(rec.unit, (unit != NULL) ? unit : "", sizeof(rec.unit));
    strlcpy(rec.source, (source != NULL) ? source : "", sizeof(rec.source));

    if (xQueueSend(csv_queue, &rec, 0) != pdPASS)
    {
        csv_rows_dropped++;
    }
}

char *csv_logger_get_status_json(void)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL)
    {
        return NULL;
    }
    cJSON_AddBoolToObject(root, "running", csv_queue != NULL);
    cJSON_AddBoolToObject(root, "session_active", csv_session_active);
    cJSON_AddStringToObject(root, "file", csv_session_active ? csv_file_path : "");
    cJSON_AddNumberToObject(root, "rows_written", csv_rows_written);
    cJSON_AddNumberToObject(root, "rows_dropped", csv_rows_dropped);
    cJSON_AddNumberToObject(root, "files_count", csv_files_count);
    char *out = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return out;
}

esp_err_t csv_logger_init(void)
{
    if (csv_queue != NULL)
    {
        return ESP_OK;
    }

    static uint8_t *csv_queue_storage;
    csv_queue_storage = heap_caps_malloc(CSV_LOGGER_QUEUE_LEN * sizeof(csv_record_t),
                                         MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (csv_queue_storage == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate queue storage");
        return ESP_FAIL;
    }

    QueueHandle_t q = xQueueCreateStatic(CSV_LOGGER_QUEUE_LEN, sizeof(csv_record_t),
                                         csv_queue_storage, &csv_queue_buffer);
    if (q == NULL)
    {
        ESP_LOGE(TAG, "Failed to create queue");
        heap_caps_free(csv_queue_storage);
        return ESP_FAIL;
    }

    static StackType_t *csv_task_stack;
    static StaticTask_t csv_task_buffer;
    csv_task_stack = heap_caps_malloc(CSV_LOGGER_TASK_STACK_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (csv_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate task stack");
        vQueueDelete(q);
        heap_caps_free(csv_queue_storage);
        return ESP_FAIL;
    }

    if (xTaskCreateStatic(csv_logger_task, "csv_logger", CSV_LOGGER_TASK_STACK_SIZE,
                          NULL, 4, csv_task_stack, &csv_task_buffer) == NULL)
    {
        ESP_LOGE(TAG, "Failed to create task");
        vQueueDelete(q);
        heap_caps_free(csv_queue_storage);
        heap_caps_free(csv_task_stack);
        return ESP_FAIL;
    }

    // Publish the queue last: csv_logger_record() treats a non-NULL queue as "running".
    csv_queue = q;
    ESP_LOGI(TAG, "CSV datalogger started");
    return ESP_OK;
}
