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

#ifndef OBD_LOGGER_DB_MANAGER_H
#define OBD_LOGGER_DB_MANAGER_H

#include "esp_err.h"
#include "cJSON.h"
#include <time.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DB_MAX_SIZE_DEFAULT          (4 * 1024 * 1024)   // 4 MB default size limit
#define DB_MAX_FILES_DEFAULT         100                 // Default max number of DB files to keep
#define DB_INDEX_FILENAME            "db_index.json"     // Default name for the index file
#define DB_FILENAME_PREFIX           "obd_log_"          // Prefix for DB files
#define DB_FILENAME_EXTENSION        ".db"               // Extension for DB files

// Database file status
typedef enum {
    DB_FILE_ACTIVE = 0,        // Currently active DB file
    DB_FILE_ARCHIVED,          // Archived/completed DB file
    DB_FILE_CORRUPTED          // Potentially corrupted DB file
} db_file_status_t;

// DB manager configuration structure
typedef struct {
    uint32_t max_size_bytes;       // Maximum size of DB file before rotation (default 4MB)
    uint32_t max_db_files;         // Maximum number of DB files to keep
    char base_path[64];           // Base path for storing DB files
    bool check_size_before_write;  // Whether to check size before each write operation
} obd_db_manager_config_t;

// DB file info structure
typedef struct {
    char filename[64];             // DB filename
    time_t created_time;           // Creation timestamp
    uint32_t size_bytes;           // Current file size in bytes
    db_file_status_t status;       // File status
} obd_db_file_info_t;

/**
 * @brief Event types for DB manager callback
 */
typedef enum {
    OBD_DB_ROTATION_STARTED,       // DB rotation started
    OBD_DB_ROTATION_COMPLETED,     // DB rotation completed successfully
    OBD_DB_ROTATION_FAILED,        // DB rotation failed
    OBD_DB_SIZE_WARNING            // DB approaching size limit
} obd_db_event_t;

/**
 * @brief Callback function type for DB manager events
 */
typedef void (*obd_db_event_cb_t)(obd_db_event_t event, void* event_data);

/**
 * @brief Initialize the OBD logger DB manager
 * 
 * @param config Configuration for the DB manager
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_init(const obd_db_manager_config_t *config);

/**
 * @brief Get the path to the current active database file
 * 
 * @param path Buffer to store the path
 * @param max_len Maximum length of the buffer
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_get_current_path(char *path, size_t max_len);

/**
 * @brief Force a database rotation regardless of current size
 * 
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_force_rotation(void);

/**
 * @brief Check if database rotation is needed and rotate if necessary
 * 
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_check_and_rotate(void);

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
                                       size_t *num_files);

/**
 * @brief Get information about a specific database file
 * 
 * @param filename Name of the database file
 * @param file_info Pointer to store file information
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_get_file_info(const char *filename, 
                                       obd_db_file_info_t *file_info);

/**
 * @brief Register a callback function for DB manager events
 * 
 * @param callback Callback function
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_register_callback(obd_db_event_cb_t callback);

/**
 * @brief Delete old database files when exceeding max_db_files
 * 
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_cleanup_old_files(void);

/**
 * @brief Get the current database file size
 * 
 * @param size_bytes Pointer to store the size in bytes
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_get_current_size(uint32_t *size_bytes);

/**
 * @brief Deinitialize the OBD logger DB manager
 * 
 * @return esp_err_t ESP_OK on success, error code otherwise
 */
esp_err_t obd_db_manager_deinit(void);

#ifdef __cplusplus
}
#endif

#endif /* OBD_LOGGER_DB_MANAGER_H */