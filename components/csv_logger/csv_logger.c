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
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
#include <dirent.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_attr.h"
#include "esp_http_server.h"
#include "cJSON.h"

#include "csv_logger.h"
#include "sdcard.h"
#include "vehicle.h"
#include "wc_timer.h"

static const char *TAG = "CSV_LOGGER";

#define CSV_LOGGER_QUEUE_LEN        256
#define CSV_LOGGER_TASK_STACK_SIZE  (1024 * 6)
// Small settle delay before auto-starting CSV at boot. NOTE: the historical "boot
// crash-loop" was NOT a timing/boot-window problem -- it was a task-publish race in
// csv_logger_init() (csv_queue was assigned AFTER xTaskCreateStatic, but the higher-prio
// writer task preempted and ran xQueueReceive(NULL) -> panic). Fixed by publishing the
// queue before creating the task. This delay is now just a modest margin.
#define CSV_LOGGER_DEFER_BOOT_MS    20000
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

// One-shot auto-start crash guard (RTC_NOINIT: survives a panic/brownout/watchdog/SW
// reset; wiped by a cold power cycle). Armed when a boot attempt starts; cleared by the
// writer task after it proves stable for 15s. If a boot finds it still armed, the
// previous attempt crashed -> skip CSV this boot so a CSV-startup fault can never
// boot-loop the device; it self-recovers on the next boot.
#define CSV_ATTEMPT_MAGIC 0xA11C0DE5u
RTC_NOINIT_ATTR static uint32_t csv_attempt_inprogress;

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
    int64_t csv_task_start_us = esp_timer_get_time();
    bool csv_guard_cleared = false;

    while (1)
    {
        if (!csv_guard_cleared && (esp_timer_get_time() - csv_task_start_us) > 15000000)
        {
            csv_attempt_inprogress = 0;   // survived the danger window; future boots may retry
            csv_guard_cleared = true;
            ESP_LOGI(TAG, "CSV auto-start proven stable (15s) - crash guard cleared");
        }
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

        // Log while ignition is on (engine running). The ignition state is derived from
        // battery voltage (sleep_mode_get_voltage > sleep_volt) with a 3s off-debounce
        // so a cranking dip doesn't split the file.
        bool logging_active = ignition_on;

        // Session close: logging stopped (ignition off / disabled) or SD was pulled.
        if (csv_session_active && (!logging_active || !sdcard_is_mounted()))
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

        if (!logging_active)
        {
            continue;   // not logging: discard samples (ignition off or harness disabled)
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
    cJSON_AddBoolToObject(root, "sd_mounted", sdcard_is_mounted());
    cJSON_AddBoolToObject(root, "session_active", csv_session_active);
    cJSON_AddStringToObject(root, "file", csv_session_active ? csv_file_path : "");
    cJSON_AddNumberToObject(root, "rows_written", csv_rows_written);
    cJSON_AddNumberToObject(root, "rows_dropped", csv_rows_dropped);
    cJSON_AddNumberToObject(root, "files_count", csv_files_count);
    char *out = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return out;
}

// ---- HTTP interface: status, file listing, and CSV download ----

static esp_err_t csv_status_handler(httpd_req_t *req)
{
    char *status = csv_logger_get_status_json();
    httpd_resp_set_type(req, "application/json");
    if (status == NULL)
    {
        httpd_resp_sendstr(req, "{\"running\":false}");
        return ESP_OK;
    }
    httpd_resp_sendstr(req, status);
    free(status);
    return ESP_OK;
}

static esp_err_t csv_list_handler(httpd_req_t *req)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL)
    {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_sendstr(req, "{\"files\":[]}");
        return ESP_OK;
    }
    cJSON *files = cJSON_AddArrayToObject(root, "files");

    // Missing dir (nothing logged yet) is not an error: report an empty list.
    DIR *dir = opendir(CSV_LOGGER_DIR);
    if (dir != NULL)
    {
        struct dirent *entry;
        char path[320];
        struct stat st;
        while ((entry = readdir(dir)) != NULL)
        {
            const char *ext = strrchr(entry->d_name, '.');
            if (ext == NULL || strcasecmp(ext, ".csv") != 0)
            {
                continue;
            }
            cJSON *item = cJSON_CreateObject();
            cJSON_AddStringToObject(item, "name", entry->d_name);
            snprintf(path, sizeof(path), "%s/%s", CSV_LOGGER_DIR, entry->d_name);
            if (stat(path, &st) == 0)
            {
                cJSON_AddNumberToObject(item, "size", (double)st.st_size);
            }
            cJSON_AddItemToArray(files, item);
        }
        closedir(dir);
    }

    char *out = cJSON_PrintUnformatted(root);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, (out != NULL) ? out : "{\"files\":[]}");
    if (out != NULL)
    {
        free(out);
    }
    cJSON_Delete(root);
    return ESP_OK;
}

static esp_err_t csv_download_handler(httpd_req_t *req)
{
    char query[160];
    char name[128];

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(query, "file", name, sizeof(name)) != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Missing 'file' query parameter");
        return ESP_FAIL;
    }

    // Only allow a bare *.csv basename from CSV_LOGGER_DIR -- no traversal/subdirs.
    const char *ext = strrchr(name, '.');
    if (strchr(name, '/') != NULL || strchr(name, '\\') != NULL ||
        strstr(name, "..") != NULL || ext == NULL || strcasecmp(ext, ".csv") != 0)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid file name");
        return ESP_FAIL;
    }

    char path[320];
    snprintf(path, sizeof(path), "%s/%s", CSV_LOGGER_DIR, name);

    FILE *f = fopen(path, "r");
    if (f == NULL)
    {
        httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "File not found");
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "text/csv");
    char disp[160];
    snprintf(disp, sizeof(disp), "attachment; filename=%s", name);
    httpd_resp_set_hdr(req, "Content-Disposition", disp);

    char *buf = malloc(2048);
    if (buf == NULL)
    {
        fclose(f);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
        return ESP_FAIL;
    }

    size_t n;
    esp_err_t ret = ESP_OK;
    while ((n = fread(buf, 1, 2048, f)) > 0)
    {
        if (httpd_resp_send_chunk(req, buf, n) != ESP_OK)
        {
            ret = ESP_FAIL;
            break;
        }
    }
    free(buf);
    fclose(f);
    httpd_resp_send_chunk(req, NULL, 0); // end of response
    return ret;
}

const httpd_uri_t csv_status_uri = {
    .uri = "/csv_status",
    .method = HTTP_GET,
    .handler = csv_status_handler,
    .user_ctx = NULL
};

const httpd_uri_t csv_list_uri = {
    .uri = "/csv_list",
    .method = HTTP_GET,
    .handler = csv_list_handler,
    .user_ctx = NULL
};

const httpd_uri_t csv_download_uri = {
    .uri = "/download_csv",
    .method = HTTP_GET,
    .handler = csv_download_handler,
    .user_ctx = NULL
};

esp_err_t csv_logger_init(void)
{
    if (csv_queue != NULL)
    {
        return ESP_OK;
    }

    // Hybrid design: the queue STORAGE lives in INTERNAL RAM, never PSRAM. auto_pid's
    // decode hot path (autopid.c:170 -> csv_logger_record -> xQueueSend) copies each
    // record into this storage from auto_pid's own task context. A flash-cache-disable
    // window (NVS commit, OTA, SD/SPI op) makes any PSRAM access from that context
    // fault; internal RAM is always reachable. ~22 KB at 256 deep -- the burst buffer
    // that lets us keep full decode-rate logging without dropping samples.
    static uint8_t *csv_queue_storage;
    csv_queue_storage = heap_caps_malloc(CSV_LOGGER_QUEUE_LEN * sizeof(csv_record_t),
                                         MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
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

    // Consumer task stack also in INTERNAL RAM. It runs a tight drain loop (250 ms
    // queue wait), far likelier to coincide with a flash-cache-disable window than the
    // stock logger's 10 s loop -- so a PSRAM stack here is a genuine fault risk. With
    // both queue and stack internal, no CSV code path ever depends on the PSRAM cache.
    static StackType_t *csv_task_stack;
    static StaticTask_t csv_task_buffer;
    csv_task_stack = heap_caps_malloc(CSV_LOGGER_TASK_STACK_SIZE, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (csv_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate task stack");
        vQueueDelete(q);
        heap_caps_free(csv_queue_storage);
        return ESP_FAIL;
    }

    // Publish the queue BEFORE creating the task. csv_logger_task runs at a HIGHER
    // priority (4) than the task that calls this (the deferred-init task is 3; app_main
    // is 1), so xTaskCreateStatic preempts to it IMMEDIATELY -- before any assignment
    // placed AFTER the create could run. If csv_queue were still NULL then, the task's
    // first xQueueReceive(NULL) panics. That race WAS the entire "auto-start boot crash";
    // the producer (csv_logger_record) also saw the NULL queue and silently no-op'd.
    // On-demand never hit it only because the httpd caller is higher prio than 4, so no
    // preempt. Publishing first makes the queue valid the instant the task runs; auto_pid
    // (the producer) simply buffers into it meanwhile.
    csv_queue = q;

    if (xTaskCreateStatic(csv_logger_task, "csv_logger", CSV_LOGGER_TASK_STACK_SIZE,
                          NULL, 4, csv_task_stack, &csv_task_buffer) == NULL)
    {
        ESP_LOGE(TAG, "Failed to create task");
        csv_queue = NULL;   // un-publish so producers stop and a later retry can re-init
        vQueueDelete(q);
        heap_caps_free(csv_queue_storage);
        heap_caps_free(csv_task_stack);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "CSV datalogger started");
    return ESP_OK;
}

static void csv_deferred_init_task(void *arg)
{
    // Modest settle margin before init. The boot crash was a publish race in
    // csv_logger_init() (now fixed); this delay is just headroom past the boot storm.
    vTaskDelay(pdMS_TO_TICKS(CSV_LOGGER_DEFER_BOOT_MS));
    if (csv_logger_init() != ESP_OK)
    {
        ESP_LOGE(TAG, "deferred CSV init failed");
    }
    vTaskDelete(NULL);
}

void csv_logger_init_deferred(void)
{
    // One-shot crash guard: if a prior boot armed an attempt and didn't survive long
    // enough to clear it, that attempt crashed -> skip CSV this boot so we can't
    // boot-loop. Cleared after 15s of stable logging (writer task) or by a cold power
    // cycle (RTC wiped).
    if (csv_attempt_inprogress == CSV_ATTEMPT_MAGIC)
    {
        ESP_LOGW(TAG, "Prior CSV auto-start attempt did not complete - skipping this boot to avoid a boot-loop");
        return;
    }
    csv_attempt_inprogress = CSV_ATTEMPT_MAGIC;   // arm the one-shot guard
    // Tiny internal-RAM stack: it only sleeps then calls csv_logger_init().
    if (xTaskCreate(csv_deferred_init_task, "csv_defer", 4096, NULL, 3, NULL) != pdPASS)
    {
        ESP_LOGE(TAG, "Failed to create deferred-init task");
    }
}
