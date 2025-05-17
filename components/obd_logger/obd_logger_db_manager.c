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
#include <sys/unistd.h>
#include <sys/stat.h>
#include <dirent.h>
#include <time.h>
#include <errno.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_err.h"
#include "cJSON.h"
#include "esp_littlefs.h"
#include "esp_timer.h"
#include "obd_logger_db_manager.h"
#include "rtcm.h"

#define TAG "OBD_DB_MANAGER"

// Private data structure for DB manager
typedef struct {
    obd_db_manager_config_t config;
    char current_db_path[128];
    char index_file_path[128];
    obd_db_event_cb_t event_callback;
    SemaphoreHandle_t db_manager_mutex;
    bool initialized;
    uint32_t rotation_counter;
} obd_db_manager_t;

static obd_db_manager_t db_manager = {0};

// Forward declarations of private functions
static esp_err_t create_new_db_file(bool is_init);
static esp_err_t update_json_index(void);
static esp_err_t load_json_index(void);
static esp_err_t get_file_size(const char *path, uint32_t *size);
static void sort_db_files_by_creation_time(obd_db_file_info_t *files, size_t count);
static void generate_db_filename(char *filename, size_t max_len);
static esp_err_t delete_file(const char *path);
static esp_err_t notify_event(obd_db_event_t event, void *event_data);
static esp_err_t extract_timestamp_from_filename(const char *filename, char *timestamp, size_t max_len);

/**
 * @brief Initialize the OBD logger DB manager
 * 
 * @param config Configuration for the DB manager
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_init(const obd_db_manager_config_t *config)
{
    if (config == NULL) {
        ESP_LOGE(TAG, "Null configuration provided");
        return ESP_ERR_INVALID_ARG;
    }

    if (db_manager.initialized) {
        ESP_LOGW(TAG, "DB manager already initialized");
        return ESP_OK;
    }

    // Copy configuration
    memcpy(&db_manager.config, config, sizeof(obd_db_manager_config_t));

    // Set defaults if needed
    if (db_manager.config.max_size_bytes == 0) {
        db_manager.config.max_size_bytes = DB_MAX_SIZE_DEFAULT;
    }
    
    if (db_manager.config.max_db_files == 0) {
        db_manager.config.max_db_files = DB_MAX_FILES_DEFAULT;
    }

    // Create mutex
    db_manager.db_manager_mutex = xSemaphoreCreateMutex();
    if (db_manager.db_manager_mutex == NULL) {
        ESP_LOGE(TAG, "Failed to create mutex");
        return ESP_FAIL;
    }

    // Prepare index file path
    snprintf(db_manager.index_file_path, sizeof(db_manager.index_file_path), 
             "%s/%s", db_manager.config.base_path, DB_INDEX_FILENAME);
    ESP_LOGI(TAG, "Index file path: %s", db_manager.index_file_path);

    // Load existing index if available, otherwise create new DB file
    esp_err_t ret = load_json_index();
    if (ret != ESP_OK) {
        ESP_LOGI(TAG, "No valid index file found, creating new database");
        ret = create_new_db_file(true);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to create initial database file");
            vSemaphoreDelete(db_manager.db_manager_mutex);
            return ret;
        }
    }

    db_manager.initialized = true;
    db_manager.rotation_counter = 0;
    ESP_LOGI(TAG, "OBD Logger DB Manager initialized successfully");
    ESP_LOGI(TAG, "Current DB file: %s", db_manager.current_db_path);
    ESP_LOGI(TAG, "Max file size: %"PRIu32" bytes", db_manager.config.max_size_bytes);
    ESP_LOGI(TAG, "Max DB files: %"PRIu32, db_manager.config.max_db_files);

    return ESP_OK;
}

/**
 * @brief Get the path to the current active database file
 * 
 * @param path Buffer to store the path
 * @param max_len Maximum length of the buffer
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_get_current_path(char *path, size_t max_len)
{
    if (!db_manager.initialized) {
        ESP_LOGE(TAG, "DB manager not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (path == NULL || max_len == 0) {
        ESP_LOGE(TAG, "Invalid buffer provided");
        return ESP_ERR_INVALID_ARG;
    }

    if (xSemaphoreTake(db_manager.db_manager_mutex, portMAX_DELAY) != pdTRUE) {
        ESP_LOGE(TAG, "Failed to take mutex");
        return ESP_FAIL;
    }

    if (strlen(db_manager.current_db_path) >= max_len) {
        ESP_LOGE(TAG, "Buffer too small for path");
        xSemaphoreGive(db_manager.db_manager_mutex);
        return ESP_ERR_INVALID_SIZE;
    }

    strcpy(path, db_manager.current_db_path);
    xSemaphoreGive(db_manager.db_manager_mutex);
    return ESP_OK;
}

/**
 * @brief Force a database rotation regardless of current size
 * 
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_force_rotation(void)
{
    if (!db_manager.initialized) {
        ESP_LOGE(TAG, "DB manager not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    ESP_LOGI(TAG, "Forcing database rotation");
    notify_event(OBD_DB_ROTATION_STARTED, NULL);

    if (xSemaphoreTake(db_manager.db_manager_mutex, portMAX_DELAY) != pdTRUE) {
        ESP_LOGE(TAG, "Failed to take mutex");
        return ESP_FAIL;
    }

    // Store the old path for the index update
    char old_path[128];
    strcpy(old_path, db_manager.current_db_path);
    
    // Record the rotation time using RTCM
    // We don't need to explicitly store this as it will be used
    // in update_json_index when adding the "ended" field
    
    // Create a new database file
    esp_err_t ret = create_new_db_file(false);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create new database file");
        xSemaphoreGive(db_manager.db_manager_mutex);
        notify_event(OBD_DB_ROTATION_FAILED, NULL);
        return ret;
    }

    // Update the JSON index file
    ret = update_json_index();
    
    xSemaphoreGive(db_manager.db_manager_mutex);
    
    // Clean up old files if we exceed the maximum
    obd_db_manager_cleanup_old_files();
    
    db_manager.rotation_counter++;
    ESP_LOGI(TAG, "Database rotation completed. New file: %s", db_manager.current_db_path);
    
    notify_event(OBD_DB_ROTATION_COMPLETED, NULL);
    return ret;
}

/**
 * @brief Check if database rotation is needed and rotate if necessary
 * 
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_check_and_rotate(void)
{
    if (!db_manager.initialized) {
        ESP_LOGE(TAG, "DB manager not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    uint32_t current_size = 0;
    esp_err_t ret = obd_db_manager_get_current_size(&current_size);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get current database size");
        return ret;
    }

    // Check if we need to rotate based on size
    if (current_size >= db_manager.config.max_size_bytes) {
        ESP_LOGI(TAG, "Database size limit reached (%"PRIu32" bytes). Rotating...", current_size);
        return obd_db_manager_force_rotation();
    } else if (current_size >= (db_manager.config.max_size_bytes * 90 / 100)) {
        // If the file is at 90% of max size, send a warning
        ESP_LOGW(TAG, "Database approaching size limit (%"PRIu32" bytes, 90%% of max)", current_size);
        notify_event(OBD_DB_SIZE_WARNING, &current_size);
    }

    return ESP_OK;
}

/**
 * @brief Get a list of all database files
 * 
 * @param db_files Pointer to array of file info structures to fill
 * @param max_files Maximum number of entries in the array
 * @param num_files Pointer to store the actual number of files found
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_get_file_list(obd_db_file_info_t *db_files, 
                                      size_t max_files, 
                                      size_t *num_files)
{
    if (!db_manager.initialized) {
        ESP_LOGE(TAG, "DB manager not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (db_files == NULL || num_files == NULL || max_files == 0) {
        ESP_LOGE(TAG, "Invalid buffer provided");
        return ESP_ERR_INVALID_ARG;
    }

    *num_files = 0;

    // Open the directory
    DIR *dir = opendir(db_manager.config.base_path);
    if (!dir) {
        ESP_LOGE(TAG, "Failed to open directory %s: %s", 
                db_manager.config.base_path, strerror(errno));
        return ESP_FAIL;
    }

    // Read all entries
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL && *num_files < max_files) {
        if (entry->d_type == DT_REG) {  // Regular file
            size_t prefix_len = strlen(DB_FILENAME_PREFIX);
            size_t ext_len = strlen(DB_FILENAME_EXTENSION);
            size_t name_len = strlen(entry->d_name);

            // Check if filename matches our pattern
            if (name_len > prefix_len + ext_len &&
                strncmp(entry->d_name, DB_FILENAME_PREFIX, prefix_len) == 0 &&
                strcmp(entry->d_name + name_len - ext_len, DB_FILENAME_EXTENSION) == 0) {
                
                // Get file info
                char full_path[320];
                snprintf(full_path, sizeof(full_path), "%s/%s", 
                        db_manager.config.base_path, entry->d_name);
                
                struct stat st;
                if (stat(full_path, &st) == 0) {
                    strncpy(db_files[*num_files].filename, entry->d_name, sizeof(db_files[*num_files].filename) - 1);
                    db_files[*num_files].filename[sizeof(db_files[*num_files].filename) - 1] = '\0';
                    db_files[*num_files].created_time = st.st_mtime;
                    db_files[*num_files].size_bytes = st.st_size;
                    
                    // Extract current filename from path for comparison
                    char *current_filename = strrchr(db_manager.current_db_path, '/');
                    if (current_filename) {
                        current_filename++; // Skip the slash
                        // Compare just the filenames, not full paths
                        if (strcmp(entry->d_name, current_filename) == 0) {
                            db_files[*num_files].status = DB_FILE_ACTIVE;
                        } else {
                            db_files[*num_files].status = DB_FILE_ARCHIVED;
                        }
                    } else {
                        // Fallback if no slash in path
                        db_files[*num_files].status = DB_FILE_ARCHIVED;
                    }
                    
                    (*num_files)++;
                }
            }
        }
    }

    closedir(dir);

    // Sort files by creation time (newest first)
    if (*num_files > 1) {
        sort_db_files_by_creation_time(db_files, *num_files);
    }

    return ESP_OK;
}

/**
 * @brief Get information about a specific database file
 * 
 * @param filename Name of the database file
 * @param file_info Pointer to store file information
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_get_file_info(const char *filename, 
                                      obd_db_file_info_t *file_info)
{
    if (!db_manager.initialized) {
        ESP_LOGE(TAG, "DB manager not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (filename == NULL || file_info == NULL) {
        ESP_LOGE(TAG, "Invalid arguments");
        return ESP_ERR_INVALID_ARG;
    }

    char full_path[128];
    snprintf(full_path, sizeof(full_path), "%s/%s", 
            db_manager.config.base_path, filename);
    
    struct stat st;
    if (stat(full_path, &st) != 0) {
        ESP_LOGE(TAG, "Failed to get file info for %s: %s", 
                filename, strerror(errno));
        return ESP_FAIL;
    }
    
    strncpy(file_info->filename, filename, sizeof(file_info->filename) - 1);
    file_info->filename[sizeof(file_info->filename) - 1] = '\0';
    file_info->created_time = st.st_mtime;
    file_info->size_bytes = st.st_size;
    
    // Extract current filename from path and compare only filenames
    char *current_filename = strrchr(db_manager.current_db_path, '/');
    if (current_filename) {
        current_filename++; // Skip the slash
        if (strcmp(filename, current_filename) == 0) {
            file_info->status = DB_FILE_ACTIVE;
        } else {
            file_info->status = DB_FILE_ARCHIVED;
        }
    } else {
        // Fallback if no slash in path
        file_info->status = DB_FILE_ARCHIVED;
    }

    return ESP_OK;
}

/**
 * @brief Register a callback function for DB manager events
 * 
 * @param callback Callback function
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_register_callback(obd_db_event_cb_t callback)
{
    if (!db_manager.initialized) {
        ESP_LOGE(TAG, "DB manager not initialized");
        return ESP_ERR_INVALID_STATE;
    }
    
    db_manager.event_callback = callback;
    return ESP_OK;
}

/**
 * @brief Delete old database files when exceeding max_db_files
 * 
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_cleanup_old_files(void)
{
    if (!db_manager.initialized) {
        ESP_LOGE(TAG, "DB manager not initialized");
        return ESP_ERR_INVALID_STATE;
    }
    
    // Get a list of all database files
    obd_db_file_info_t *db_files = malloc(sizeof(obd_db_file_info_t) * db_manager.config.max_db_files * 2);
    if (db_files == NULL) {
        ESP_LOGE(TAG, "Failed to allocate memory for file list");
        return ESP_ERR_NO_MEM;
    }
    
    size_t num_files = 0;
    esp_err_t ret = obd_db_manager_get_file_list(db_files, 
                                               db_manager.config.max_db_files * 2, 
                                               &num_files);
    
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get file list");
        free(db_files);
        return ret;
    }
    
    // If we have more files than allowed, delete the oldest ones
    if (num_files > db_manager.config.max_db_files) {
        ESP_LOGI(TAG, "Found %u DB files, limit is %lu. Cleaning up oldest files.", 
                num_files, db_manager.config.max_db_files);
        
        // Sort files by creation time (newest first)
        sort_db_files_by_creation_time(db_files, num_files);
        
        // Delete older files
        for (size_t i = db_manager.config.max_db_files; i < num_files; i++) {
            // Skip active file (shouldn't be in the deletable range, but just to be safe)
            if (db_files[i].status == DB_FILE_ACTIVE) {
                ESP_LOGW(TAG, "Skipping deletion of active file: %s", db_files[i].filename);
                continue;
            }
            
            char file_path[128];
            snprintf(file_path, sizeof(file_path)+2, "%s/%s", 
                    db_manager.config.base_path, db_files[i].filename);
            
            ESP_LOGI(TAG, "Deleting old DB file: %s (created: %s, size: %"PRIu32" bytes)", 
                    db_files[i].filename, 
                    ctime(&db_files[i].created_time), // Note: ctime adds a newline
                    db_files[i].size_bytes);
            
            if (delete_file(file_path) != ESP_OK) {
                ESP_LOGW(TAG, "Failed to delete file: %s", file_path);
            }
        }
        
        // Update the JSON index after cleanup
        update_json_index();
    } else {
        ESP_LOGD(TAG, "No cleanup needed. Current files: %u, limit: %lu", 
                num_files, db_manager.config.max_db_files);
    }
    
    free(db_files);
    return ESP_OK;
}

/**
 * @brief Get the current database file size
 * 
 * @param size_bytes Pointer to store the size in bytes
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_get_current_size(uint32_t *size_bytes)
{
    if (!db_manager.initialized) {
        ESP_LOGE(TAG, "DB manager not initialized");
        return ESP_ERR_INVALID_STATE;
    }
    
    if (size_bytes == NULL) {
        ESP_LOGE(TAG, "Invalid argument");
        return ESP_ERR_INVALID_ARG;
    }
    
    if (xSemaphoreTake(db_manager.db_manager_mutex, portMAX_DELAY) != pdTRUE) {
        ESP_LOGE(TAG, "Failed to take mutex");
        return ESP_FAIL;
    }
    
    esp_err_t ret = get_file_size(db_manager.current_db_path, size_bytes);
    
    xSemaphoreGive(db_manager.db_manager_mutex);
    return ret;
}

/**
 * @brief Deinitialize the OBD logger DB manager
 * 
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_deinit(void)
{
    if (!db_manager.initialized) {
        ESP_LOGW(TAG, "DB manager already deinitialized");
        return ESP_OK;
    }
    
    if (db_manager.db_manager_mutex != NULL) {
        vSemaphoreDelete(db_manager.db_manager_mutex);
        db_manager.db_manager_mutex = NULL;
    }
    
    db_manager.initialized = false;
    ESP_LOGI(TAG, "OBD Logger DB Manager deinitialized");
    
    return ESP_OK;
}

/*****************************************
 * Private helper functions
 *****************************************/

/**
 * @brief Create a new database file
 * 
 * @param is_init True if this is during initialization, false for rotation
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
static esp_err_t create_new_db_file(bool is_init)
{
    char new_filename[64];
    generate_db_filename(new_filename, sizeof(new_filename));
    
    // Create full path
    char new_path[128];
    snprintf(new_path, sizeof(new_path), "%s/%s", 
            db_manager.config.base_path, new_filename);
    
    // Save the new path
    strncpy(db_manager.current_db_path, new_path, sizeof(db_manager.current_db_path) - 1);
    db_manager.current_db_path[sizeof(db_manager.current_db_path) - 1] = '\0';
    
    ESP_LOGI(TAG, "%s database file: %s", 
            is_init ? "Created initial" : "Created new", 
            db_manager.current_db_path);
    
    if (is_init) {
        // For initial creation, just create a basic index file
        // without using functions that check for initialization
        FILE *index_file = fopen(db_manager.index_file_path, "w");
        if (index_file == NULL) {
            ESP_LOGE(TAG, "Failed to create initial index file: %s", strerror(errno));
            return ESP_FAIL;
        }
        
        // Get timestamp for the file creation time
        char timestamp[32];
        
        if (rtcm_get_iso8601_time(timestamp, sizeof(timestamp)) != ESP_OK) {
            // If function fails, set a default timestamp
            strcpy(timestamp, "1970-01-01T00:00:00");
        }
        
        // Create a simple JSON structure manually
        char json_content[300];
        snprintf(json_content, sizeof(json_content), 
                "{"
                "\"current_db\":\"%s\","
                "\"databases\":["
                "{"
                "\"filename\":\"%s\","
                "\"created\":\"%s\","
                "\"size\":0,"
                "\"status\":\"active\""
                "}"
                "]"
                "}", 
                new_filename, new_filename, timestamp);
        
        fwrite(json_content, 1, strlen(json_content), index_file);
        fclose(index_file);
        
        return ESP_OK;
    } else {
        // For subsequent rotations, we can use the normal update
        return update_json_index();
    }
}

/**
 * @brief Extract ISO8601 timestamp from database filename
 * 
 * @param filename Database filename (format: prefix_YYYYMMDD_HHMMSS_SEQ.ext)
 * @param timestamp Output buffer for ISO8601 timestamp
 * @param max_len Maximum length of timestamp buffer
 * @return esp_err_t ESP_OK if successful, error code otherwise
 */
static esp_err_t extract_timestamp_from_filename(const char *filename, char *timestamp, size_t max_len)
{
    // Expected format: prefix_YYYYMMDD_HHMMSS_SEQ.ext (e.g., obd_log_20250516_151910_000.db)
    int year, month, day, hour, min, sec, seq;
    
    // Match the pattern in the filename
    int matched = sscanf(filename, DB_FILENAME_PREFIX "%4d%2d%2d_%2d%2d%2d_%3d" DB_FILENAME_EXTENSION, 
                         &year, &month, &day, &hour, &min, &sec, &seq);
    
    if (matched >= 6) { // At least date and time parts matched
        // Format as ISO8601
        snprintf(timestamp, max_len, "%04d-%02d-%02dT%02d:%02d:%02d", 
                 year, month, day, hour, min, sec);
        return ESP_OK;
    }
    
    // Fallback for older files or unexpected format
    ESP_LOGW(TAG, "Failed to parse timestamp from filename: %s", filename);
    strncpy(timestamp, "1980-01-01T00:00:00", max_len); // Safe default
    return ESP_FAIL;
}

/**
 * @brief Update the JSON index file with current database information
 * 
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
static esp_err_t update_json_index(void)
{
    obd_db_file_info_t *db_files = NULL;
    size_t num_files = 0;
    FILE *index_file = NULL;
    cJSON *root = NULL;
    cJSON *databases = NULL;
    esp_err_t ret = ESP_FAIL;

    if (!db_manager.initialized) {
        ESP_LOGE(TAG, "Cannot update index: DB manager not initialized");
        return ESP_FAIL;
    }

    // Get list of database files
    db_files = malloc(sizeof(obd_db_file_info_t) * db_manager.config.max_db_files * 2);
    if (db_files == NULL) {
        ESP_LOGE(TAG, "Failed to allocate memory for file list");
        return ESP_ERR_NO_MEM;
    }
    
    ret = obd_db_manager_get_file_list(db_files, 
                                     db_manager.config.max_db_files * 2, 
                                     &num_files);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get file list");
        free(db_files);
        return ret;
    }
    
    // Create JSON root object
    root = cJSON_CreateObject();
    if (root == NULL) {
        ESP_LOGE(TAG, "Failed to create JSON object");
        free(db_files);
        return ESP_FAIL;
    }
    
    // Add current database info
    char basename[64];
    // Extract filename from full path
    char *slash = strrchr(db_manager.current_db_path, '/');
    if (slash) {
        strncpy(basename, slash + 1, sizeof(basename) - 1);
        basename[sizeof(basename) - 1] = '\0';
    } else {
        strncpy(basename, db_manager.current_db_path, sizeof(basename) - 1);
        basename[sizeof(basename) - 1] = '\0';
    }
    
    cJSON_AddStringToObject(root, "current_db", basename);
    
    // Add array of all databases
    databases = cJSON_CreateArray();
    if (databases == NULL) {
        ESP_LOGE(TAG, "Failed to create databases array");
        cJSON_Delete(root);
        free(db_files);
        return ESP_FAIL;
    }
    
    cJSON_AddItemToObject(root, "databases", databases);
    
    // Add each database to the array
    for (size_t i = 0; i < num_files; i++) {
        cJSON *db_item = cJSON_CreateObject();
        if (db_item == NULL) {
            ESP_LOGE(TAG, "Failed to create database item");
            cJSON_Delete(root);
            free(db_files);
            return ESP_FAIL;
        }
        
        // Convert timestamp to string
        char time_str[32];
        if (extract_timestamp_from_filename(db_files[i].filename, time_str, sizeof(time_str)) != ESP_OK) {
            // If extraction fails, try to use the file's modified time as fallback
            time_t file_time = db_files[i].created_time;
            struct tm *timeinfo = localtime(&file_time);
            if (timeinfo) {
                strftime(time_str, sizeof(time_str), "%Y-%m-%dT%H:%M:%S", timeinfo);
            } else {
                // Last resort fallback
                strcpy(time_str, "1970-01-01T00:00:00");
            }
        }
        
        cJSON_AddStringToObject(db_item, "filename", db_files[i].filename);
        cJSON_AddStringToObject(db_item, "created", time_str);
        cJSON_AddNumberToObject(db_item, "size", db_files[i].size_bytes);
        
        // Add the ended timestamp for archived files
        if (db_files[i].status != DB_FILE_ACTIVE) {
            // For archived files, add the ended timestamp
            char ended_time_str[32];
            
            if (i > 0 && db_files[i-1].status == DB_FILE_ACTIVE) {
                // This is the most recently archived file
                // Use current RTCM time as the end time
                if (rtcm_get_iso8601_time(ended_time_str, sizeof(ended_time_str)) == ESP_OK) {
                    cJSON_AddStringToObject(db_item, "ended", ended_time_str);
                }
            } else if (i < num_files - 1) {
                // Use the creation time of the next newest file
                // Extract timestamp from the next file's name or use its creation time
                if (extract_timestamp_from_filename(db_files[i+1].filename, ended_time_str, sizeof(ended_time_str)) == ESP_OK) {
                    cJSON_AddStringToObject(db_item, "ended", ended_time_str);
                } else {
                    // Fallback to next file's creation timestamp
                    time_t ended_time = db_files[i+1].created_time;
                    struct tm *timeinfo = localtime(&ended_time);
                    if (timeinfo) {
                        strftime(ended_time_str, sizeof(ended_time_str), "%Y-%m-%dT%H:%M:%S", timeinfo);
                        cJSON_AddStringToObject(db_item, "ended", ended_time_str);
                    }
                }
            } else {
                // If this is the oldest file, use RTCM time
                if (rtcm_get_iso8601_time(ended_time_str, sizeof(ended_time_str)) == ESP_OK) {
                    cJSON_AddStringToObject(db_item, "ended", ended_time_str);
                }
            }
        }
        
        cJSON_AddStringToObject(db_item, "status", 
                                db_files[i].status == DB_FILE_ACTIVE ? "active" : 
                                db_files[i].status == DB_FILE_ARCHIVED ? "archived" : "corrupted");
        
        cJSON_AddItemToArray(databases, db_item);
    }
    
    // Convert to string
    char *json_str = cJSON_PrintUnformatted(root);
    if (json_str == NULL) {
        ESP_LOGE(TAG, "Failed to convert JSON to string");
        cJSON_Delete(root);
        free(db_files);
        return ESP_FAIL;
    }
    
    // Write to file
    index_file = fopen(db_manager.index_file_path, "w");
    if (index_file == NULL) {
        ESP_LOGE(TAG, "Failed to open index file for writing: %s", strerror(errno));
        free(json_str);
        cJSON_Delete(root);
        free(db_files);
        return ESP_FAIL;
    }
    
    size_t written = fwrite(json_str, 1, strlen(json_str), index_file);
    if (written != strlen(json_str)) {
        ESP_LOGE(TAG, "Failed to write index file: %s", strerror(errno));
        fclose(index_file);
        free(json_str);
        cJSON_Delete(root);
        free(db_files);
        return ESP_FAIL;
    }
    
    // Cleanup
    fclose(index_file);
    free(json_str);
    cJSON_Delete(root);
    free(db_files);
    
    ESP_LOGI(TAG, "JSON index file updated successfully");
    return ESP_OK;
}

/**
 * @brief Load database information from JSON index file
 * 
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
static esp_err_t load_json_index(void)
{
    FILE *index_file = NULL;
    cJSON *root = NULL;
    cJSON *current_db = NULL;
    long file_size = 0;
    char *json_buffer = NULL;
    
    // Try to open index file
    index_file = fopen(db_manager.index_file_path, "r");
    if (index_file == NULL) {
        ESP_LOGW(TAG, "Index file doesn't exist: %s", db_manager.index_file_path);
        return ESP_FAIL;
    }
    
    // Get file size
    fseek(index_file, 0, SEEK_END);
    file_size = ftell(index_file);
    fseek(index_file, 0, SEEK_SET);
    
    if (file_size <= 0) {
        ESP_LOGW(TAG, "Index file is empty");
        fclose(index_file);
        return ESP_FAIL;
    }
    
    // Allocate buffer for JSON
    json_buffer = (char *)malloc(file_size + 1);
    if (json_buffer == NULL) {
        ESP_LOGE(TAG, "Failed to allocate memory for JSON buffer");
        fclose(index_file);
        return ESP_ERR_NO_MEM;
    }
    
    // Read file into buffer
    size_t read_size = fread(json_buffer, 1, file_size, index_file);
    fclose(index_file);
    if (read_size != file_size) {
        ESP_LOGE(TAG, "Failed to read index file: expected %ld bytes, got %d", 
                file_size, read_size);
        free(json_buffer);
        return ESP_FAIL;
    }
    
    // Null-terminate the buffer
    json_buffer[read_size] = '\0';
    
    // Parse JSON
    root = cJSON_Parse(json_buffer);
    free(json_buffer); // We don't need the buffer anymore
    
    if (root == NULL) {
        const char *error_ptr = cJSON_GetErrorPtr();
        if (error_ptr != NULL) {
            ESP_LOGE(TAG, "JSON parse error at: %s", error_ptr);
        }
        return ESP_FAIL;
    }
    
    // Extract current database path
    current_db = cJSON_GetObjectItemCaseSensitive(root, "current_db");
    if (!cJSON_IsString(current_db) || current_db->valuestring == NULL) {
        ESP_LOGE(TAG, "Invalid or missing 'current_db' field in index file");
        cJSON_Delete(root);
        return ESP_FAIL;
    }
    
    // Create full path
    snprintf(db_manager.current_db_path, sizeof(db_manager.current_db_path),
             "%s/%s", db_manager.config.base_path, current_db->valuestring);
    
    // Verify that the database file exists
    struct stat st;
    if (stat(db_manager.current_db_path, &st) != 0) {
        ESP_LOGW(TAG, "Current database file does not exist: %s", db_manager.current_db_path);
        cJSON_Delete(root);
        return ESP_FAIL;
    }
    
    ESP_LOGI(TAG, "Loaded database info from index file");
    ESP_LOGI(TAG, "Current database: %s", db_manager.current_db_path);
    
    cJSON_Delete(root);
    return ESP_OK;
}

/**
 * @brief Get the size of a file
 * 
 * @param path Path to the file
 * @param size Pointer to store the size
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
static esp_err_t get_file_size(const char *path, uint32_t *size)
{
    if (path == NULL || size == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    
    struct stat st;
    if (stat(path, &st) != 0) {
        ESP_LOGE(TAG, "Failed to get file size: %s", strerror(errno));
        return ESP_FAIL;
    }
    
    *size = (uint32_t)st.st_size;
    return ESP_OK;
}

/**
 * @brief Sort database files by creation time (newest first)
 * 
 * @param files Array of file info structures
 * @param count Number of files
 */
static void sort_db_files_by_creation_time(obd_db_file_info_t *files, size_t count)
{
    // Simple bubble sort for small arrays
    for (size_t i = 0; i < count - 1; i++) {
        for (size_t j = 0; j < count - i - 1; j++) {
            if (files[j].created_time < files[j + 1].created_time) {
                // Swap files
                obd_db_file_info_t temp = files[j];
                files[j] = files[j + 1];
                files[j + 1] = temp;
            }
        }
    }
}

/**
 * @brief Generate a new database filename with timestamp
 * 
 * @param filename Buffer to store the filename
 * @param max_len Maximum length of the buffer
 */
static void generate_db_filename(char *filename, size_t max_len)
{
    // Get current time from RTCM module
    uint8_t hour, min, sec;
    uint8_t year, month, day, weekday;
    
    // First try to get time from RTCM module
    esp_err_t ret = rtcm_get_time(&hour, &min, &sec);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Failed to get time from RTCM, using system time");
        time_t now = time(NULL);
        struct tm *timeinfo = localtime(&now);
        
        snprintf(filename, max_len, "%s%04d%02d%02d_%02d%02d%02d_%03lu%s",
                DB_FILENAME_PREFIX,
                timeinfo->tm_year + 1900, timeinfo->tm_mon + 1, timeinfo->tm_mday,
                timeinfo->tm_hour, timeinfo->tm_min, timeinfo->tm_sec,
                db_manager.rotation_counter % 1000,
                DB_FILENAME_EXTENSION);
        return;
    }
    
    ret = rtcm_get_date(&year, &month, &day, &weekday);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Failed to get date from RTCM, using system time");
        time_t now = time(NULL);
        struct tm *timeinfo = localtime(&now);
        
        snprintf(filename, max_len, "%s%04d%02d%02d_%02d%02d%02d_%03lu%s",
                DB_FILENAME_PREFIX,
                timeinfo->tm_year + 1900, timeinfo->tm_mon + 1, timeinfo->tm_mday,
                timeinfo->tm_hour, timeinfo->tm_min, timeinfo->tm_sec,
                db_manager.rotation_counter % 1000,
                DB_FILENAME_EXTENSION);
        return;
    }
    
    // Convert BCD format to decimal
    uint8_t hour_dec = ((hour >> 4) & 0x0F) * 10 + (hour & 0x0F);
    uint8_t min_dec = ((min >> 4) & 0x0F) * 10 + (min & 0x0F);
    uint8_t sec_dec = ((sec >> 4) & 0x0F) * 10 + (sec & 0x0F);
    uint8_t year_dec = ((year >> 4) & 0x0F) * 10 + (year & 0x0F);
    uint8_t month_dec = ((month >> 4) & 0x0F) * 10 + (month & 0x0F);
    uint8_t day_dec = ((day >> 4) & 0x0F) * 10 + (day & 0x0F);
    
    // Format the filename: prefix_YYYYMMDD_HHMMSS_SEQ.db
    snprintf(filename, max_len, "%s20%02d%02d%02d_%02d%02d%02d_%03lu%s",
            DB_FILENAME_PREFIX,
            year_dec, month_dec, day_dec,
            hour_dec, min_dec, sec_dec,
            db_manager.rotation_counter % 1000,
            DB_FILENAME_EXTENSION);
}

/**
 * @brief Delete a file
 * 
 * @param path Path to the file
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
static esp_err_t delete_file(const char *path)
{
    if (path == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    
    if (unlink(path) != 0) {
        ESP_LOGE(TAG, "Failed to delete file %s: %s", path, strerror(errno));
        return ESP_FAIL;
    }
    
    ESP_LOGI(TAG, "Deleted file: %s", path);
    return ESP_OK;
}

/**
 * @brief Notify event to registered callback
 * 
 * @param event Event type
 * @param event_data Event data (can be NULL)
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
static esp_err_t notify_event(obd_db_event_t event, void *event_data)
{
    if (db_manager.event_callback) {
        db_manager.event_callback(event, event_data);
        return ESP_OK;
    }
    return ESP_OK; // Not an error if no callback is registered
}
