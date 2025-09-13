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
#include <dirent.h>
#include <errno.h>
#include <string.h>
#include "esp_vfs_fat.h"
#include "esp_littlefs.h"
#include "wear_levelling.h"

static const char *TAG = "filesystem";
static uint8_t initialized = 0;

// Recursively delete a path (file or directory). If it's a directory, delete all contents then the directory itself.
static esp_err_t delete_path_recursive(const char *path)
{
    struct stat st;
    if (stat(path, &st) != 0)
    {
        ESP_LOGW(TAG, "stat failed for %s (errno=%d)", path, errno);
        return ESP_FAIL;
    }

    if (S_ISDIR(st.st_mode))
    {
        DIR *dir = opendir(path);
        if (!dir)
        {
            ESP_LOGE(TAG, "opendir failed for %s (errno=%d)", path, errno);
            return ESP_FAIL;
        }

        struct dirent *entry;
        while ((entry = readdir(dir)) != NULL)
        {
            const char *name = entry->d_name;
            if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0)
                continue;

            char child_path[512];
            int n = snprintf(child_path, sizeof(child_path), "%s/%s", path, name);
            if (n <= 0 || n >= (int)sizeof(child_path))
            {
                ESP_LOGE(TAG, "Path too long while deleting: %s/%s", path, name);
                // continue trying other entries
                continue;
            }

            // Recursively delete child
            delete_path_recursive(child_path);
        }
        closedir(dir);

        // After deleting contents, remove the directory itself
        if (rmdir(path) != 0)
        {
            ESP_LOGE(TAG, "rmdir failed for %s (errno=%d)", path, errno);
            return ESP_FAIL;
        }
        ESP_LOGI(TAG, "Removed directory: %s", path);
        return ESP_OK;
    }
    else
    {
        if (unlink(path) != 0)
        {
            ESP_LOGE(TAG, "unlink failed for %s (errno=%d)", path, errno);
            return ESP_FAIL;
        }
        ESP_LOGI(TAG, "Removed file: %s", path);
        return ESP_OK;
    }
}

// Delete only the contents of a directory, without removing the directory itself
static esp_err_t delete_dir_contents(const char *dir_path)
{
    DIR *dir = opendir(dir_path);
    if (!dir)
    {
        ESP_LOGE(TAG, "opendir failed for %s (errno=%d)", dir_path, errno);
        return ESP_FAIL;
    }

    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL)
    {
        const char *name = entry->d_name;
        if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0)
            continue;

        char child_path[512];
        int n = snprintf(child_path, sizeof(child_path), "%s/%s", dir_path, name);
        if (n <= 0 || n >= (int)sizeof(child_path))
        {
            ESP_LOGE(TAG, "Path too long while deleting: %s/%s", dir_path, name);
            // skip this entry and continue
            continue;
        }

        // For each entry under dir_path, delete recursively (this removes files and subdirectories)
        delete_path_recursive(child_path);
    }
    closedir(dir);
    return ESP_OK;
}

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
    delete_config_file(FS_MOUNT_POINT"/vpn_config.json");

    ESP_LOGI(TAG, "Configuration files deleted");
}

void filesystem_delete_all(void)
{
    if (!initialized)
    {
        ESP_LOGE(TAG, "Filesystem not initialized, cannot delete all files");
        return;
    }

    ESP_LOGW(TAG, "Deleting ALL files and folders under %s", FS_MOUNT_POINT);
    esp_err_t res = delete_dir_contents(FS_MOUNT_POINT);
    if (res == ESP_OK)
    {
        ESP_LOGI(TAG, "All files and folders deleted under %s", FS_MOUNT_POINT);
    }
    else
    {
        ESP_LOGE(TAG, "Failed to delete some contents under %s", FS_MOUNT_POINT);
    }
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
