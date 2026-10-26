/**
 * @file main_factory.c
 * @brief The device's factory reset: the settings partition, the data
 *        partition and NVS (the crash report and the backup key written
 *        back), never the SD card. See main_factory.h for the contract
 *        and the history.
 */
#include "main_factory.h"

#include <stdbool.h>

#include "esp_log.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "config_backup.h"
#include "filesystem.h"
#include "restart_tracker.h"
#include "settings_manager.h"

static const char *TAG = "main";

#define FR_TASK_STACK 8192 /* nvs_flash_init's page scan is the deep one */

typedef struct
{
    bool              bare;
    esp_err_t         err;
    SemaphoreHandle_t done;
} fr_job_t;

static bool s_busy; /* one reset at a time (the web route and the console) */

/* the first failure wins; every step still runs */
static void note(esp_err_t *first, esp_err_t err, const char *what)
{
    if (err == ESP_OK)
    {
        return;
    }

    ESP_LOGE(TAG, "factory reset: %s failed: %s", what, esp_err_to_name(err));

    if (*first == ESP_OK)
    {
        *first = err;
    }
}

/* safe mode's settings partition: raw, the owner never came up */
static esp_err_t erase_raw(const char *label)
{
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, label);

    if (part == NULL)
    {
        return ESP_ERR_NOT_FOUND;
    }

    return esp_partition_erase_range(part, 0, part->size);
}

static esp_err_t wipe(bool bare)
{
    esp_err_t first = ESP_OK;

    /* 1. the settings partition */
    note(&first, bare ? erase_raw("settings") : settings_manager_factory_reset(),
         "settings");

    /* 2. the data partition, unmounted first (filesystem_wipe) */
    note(&first, filesystem_wipe(), "data partition");

    /* 3. NVS: the key into RAM when this boot never read it (safe mode),
          the partition erased and made again, the crash report and the
          key written back. nvs_flash_erase() closes every open handle
          first; the WiFi driver's next write fails until the restart. */
    bool key = (config_backup_key_load() == ESP_OK);

    note(&first, nvs_flash_erase(), "nvs erase");

    esp_err_t err = nvs_flash_init();

    note(&first, err, "nvs init");

    if (err == ESP_OK)
    {
        (void)restart_tracker_restore_report(); /* NOT_FOUND: none stored */

        if (key)
        {
            note(&first, config_backup_key_rewrite(), "backup key");
        }
    }

    return first;
}

static void job_task(void *arg)
{
    fr_job_t *job = arg;

    job->err = wipe(job->bare);
    xSemaphoreGive(job->done);
    vTaskDelete(NULL);
}

static esp_err_t run(bool bare)
{
    static StaticSemaphore_t done_buf; /* internal: FreeRTOS object */

    if (s_busy)
    {
        return ESP_ERR_INVALID_STATE;
    }

    s_busy = true;

    fr_job_t job = { .bare = bare, .err = ESP_FAIL };

    job.done = xSemaphoreCreateBinaryStatic(&done_buf);

    ESP_LOGW(TAG, "factory reset: wiping the settings, the data partition "
             "and NVS (the SD card is kept)");

    /* its own task, an internal-RAM stack (the default heap): the flash
       erases run with the cache off */
    if (xTaskCreate(job_task, "factory_rst", FR_TASK_STACK, &job, 5, NULL) !=
        pdPASS)
    {
        ESP_LOGE(TAG, "factory reset: task alloc failed");
        s_busy = false;
        return ESP_ERR_NO_MEM;
    }

    xSemaphoreTake(job.done, portMAX_DELAY);

    if (job.err == ESP_OK)
    {
        ESP_LOGW(TAG, "factory reset: done, the restart runs the defaults");
    }

    s_busy = false;
    return job.err;
}

esp_err_t main_factory_reset(void)
{
    return run(false);
}

esp_err_t main_factory_reset_bare(void)
{
    return run(true);
}
