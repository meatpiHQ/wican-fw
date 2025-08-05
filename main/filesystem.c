 /* This file is part of the WiCAN project.
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

#include "filesystem.h"
#include "esp_log.h"
#include <sys/unistd.h>
#include <sys/stat.h>
#include "esp_vfs_fat.h"
#include "esp_littlefs.h"
#include "wear_levelling.h"

static const char *TAG = "filesystem";
static uint8_t initialized = 0;

static bool delete_config_file(const char* file_path) 
{
    FILE *f = fopen(file_path, "r");
    if (f != NULL) 
    {
        // File exists, close it
        fclose(f);
        // Delete the file
        if (unlink(file_path) == 0) 
        {
            ESP_LOGI(TAG, "%s deleted successfully", file_path);
            return true;
        } 
        else 
        {
            ESP_LOGE(TAG, "Failed to delete %s", file_path);
            return false;
        }
    } 
    else 
    {
        ESP_LOGI(TAG, "%s does not exist", file_path);
        return true;  // Not an error if file doesn't exist
    }
}

void filesystem_delete_config_files(void)
{
    if (!initialized) 
    {
        ESP_LOGE(TAG, "Filesystem not initialized, cannot delete config files");
        return;
    }
    ESP_LOGI(TAG, "Deleting configuration files...");

    delete_config_file(FS_MOUNT_POINT"/config.json");
    delete_config_file(FS_MOUNT_POINT"/car_data.json");
    delete_config_file(FS_MOUNT_POINT"/auto_pid.json");
    delete_config_file(FS_MOUNT_POINT"/mqtt_canfilt.json");

    ESP_LOGI(TAG, "Configuration files deleted");
}

void filesystem_init(void)
{
    if (initialized) 
    {
        ESP_LOGW(TAG, "Filesystem already initialized");
        return;
    }

    #ifdef USE_FATFS
    static wl_handle_t s_wl_handle = WL_INVALID_HANDLE;
    ESP_LOGI(TAG, "Initializing FAT filesystem");

    const esp_vfs_fat_mount_config_t mount_config = {
        .max_files = 4,
        .format_if_mount_failed = true,
        .allocation_unit_size = CONFIG_WL_SECTOR_SIZE,
        .use_one_fat = false,
    };

    esp_err_t ret = esp_vfs_fat_spiflash_mount_rw_wl(FS_MOUNT_POINT"", "storage", &mount_config, &s_wl_handle);
    if (ret != ESP_OK) 
    {
        ESP_LOGE(TAG, "Failed to mount FATFS (%s)", esp_err_to_name(ret));
        return;
    }
    
    ESP_LOGI(TAG, "FAT filesystem mounted successfully");
    initialized = 1;
    #else
    ESP_LOGI(TAG, "Initializing LittleFS filesystem");
    
    esp_vfs_littlefs_conf_t conf = {
        .base_path = FS_MOUNT_POINT,
        .partition_label = "storage",
        .format_if_mount_failed = true,
        .dont_mount = false,
    };
    
    esp_err_t ret = esp_vfs_littlefs_register(&conf);
    if (ret != ESP_OK) 
    {
        ESP_LOGE(TAG, "Failed to mount LittleFS (%s)", esp_err_to_name(ret));
        return;
    }
    
    ESP_LOGI(TAG, "LittleFS filesystem mounted successfully");

    size_t total = 0, used = 0;
    ret = esp_littlefs_info(conf.partition_label, &total, &used);
    if (ret != ESP_OK)
    {
            ESP_LOGE(TAG, "Failed to get LittleFS partition information (%s)", esp_err_to_name(ret));
    }
    else
    {
            ESP_LOGI(TAG, "Partition size: total: %d, used: %d", total, used);
    }
    initialized = 1;
    #endif
}
