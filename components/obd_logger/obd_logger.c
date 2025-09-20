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
#include <sys/unistd.h>
#include <time.h>
#include <math.h>
#include <float.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/event_groups.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_littlefs.h"
#include "sqlite3.h"
#include "esp_timer.h"
#include "string.h"
#include "rtcm.h"
#include "obd_logger.h"
#include "cJSON.h"
#include "obd_logger_iface.h"
#include "obd_logger_db_manager.h"

#define TAG                                 "OBD_LOGGER"
#define OBD_LOGGERR_TASK_STACK_SIZE         (1024*8)

// Define max number of parameters
#define MAX_PARAMS 50

#define OBD_LOGGER_INITIALIZED_BIT  BIT0
#define OBD_LOGGER_ENABLED_BIT      BIT1
#define OBD_LOGGER_DISABLED_BIT     BIT2

// Create param_data table
const char *sql_param_data = 
    "CREATE TABLE IF NOT EXISTS param_data ("
    "timestamp INTEGER, "
    "param_id INTEGER, "
    "value REAL"
    ");";

// Create param_info table
const char *sql_param_info = 
    "CREATE TABLE IF NOT EXISTS param_info ("
    "Id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "Name VARCHAR(50) UNIQUE, "
    "Type VARCHAR(50), "
    "Data JSON"
    ");";

// Define a structure for the parameter lookup table
typedef struct {
    int id;
    char name[50];
    char type[50];
} param_lookup_t;

// Global lookup table
static param_lookup_t param_lookup[MAX_PARAMS];
static int param_count = 0;

static sqlite3 *db_file = NULL;
static SemaphoreHandle_t db_mutex = NULL;
static char db_path[128] = {0};
static uint32_t logger_period = 0;
static uint32_t obd_logger_params_count = 0;
static obd_logger_get_params_cb_t obd_logger_get_params = NULL;
static EventGroupHandle_t obd_logger_event_group = NULL;
static StaticEventGroup_t obd_logger_event_group_buffer;

/**
 * @brief Enable OBD logging
 */
void obd_logger_enable(void) {
    if (obd_logger_event_group != NULL) {
        xEventGroupSetBits(obd_logger_event_group, OBD_LOGGER_ENABLED_BIT);
        xEventGroupClearBits(obd_logger_event_group, OBD_LOGGER_DISABLED_BIT);
        ESP_LOGI(TAG, "OBD logging enabled");
    }
}

/**
 * @brief Disable OBD logging
 */
void obd_logger_disable(void) {
    if (obd_logger_event_group != NULL) {
        xEventGroupSetBits(obd_logger_event_group, OBD_LOGGER_DISABLED_BIT);
        xEventGroupClearBits(obd_logger_event_group, OBD_LOGGER_ENABLED_BIT);
        ESP_LOGI(TAG, "OBD logging disabled");
    }
}

/**
 * @brief Check if OBD logging is enabled
 * @return true if enabled, false if disabled
 */
bool obd_logger_is_enabled(void) {
    if (obd_logger_event_group != NULL) {
        EventBits_t bits = xEventGroupGetBits(obd_logger_event_group);
        return (bits & OBD_LOGGER_ENABLED_BIT) != 0;
    }
    return false;
}

/**
 * @brief Set OBD logger as initialized
 */
void obd_logger_set_initialized(void) {
    if (obd_logger_event_group != NULL) {
        xEventGroupSetBits(obd_logger_event_group, OBD_LOGGER_INITIALIZED_BIT);
        ESP_LOGI(TAG, "OBD logger marked as initialized");
    }
}

/**
 * @brief Clear OBD logger initialized status
 */
void obd_logger_clear_initialized(void) {
    if (obd_logger_event_group != NULL) {
        xEventGroupClearBits(obd_logger_event_group, OBD_LOGGER_INITIALIZED_BIT);
        ESP_LOGI(TAG, "OBD logger initialization status cleared");
    }
}

/**
 * @brief Check if OBD logger is initialized
 * @return true if initialized, false if not initialized
 */
bool obd_logger_is_initialized(void) {
    if (obd_logger_event_group != NULL) {
        EventBits_t bits = xEventGroupGetBits(obd_logger_event_group);
        return (bits & OBD_LOGGER_INITIALIZED_BIT) != 0;
    }
    return false;
}


/**
 * @brief Default callback function for SQLite operations
 * 
 * @param data User data passed to callback
 * @param argc Number of columns in result
 * @param argv Array of result values
 * @param azColName Array of column names
 * @return int Always returns 0 to continue
 */
static int obd_logger_ex_cb(void *data, int argc, char **argv, char **azColName) {
    int i;

    for (i = 0; i < argc; i++) {
        ESP_LOGI(TAG, "%s = %s", azColName[i], argv[i] ? argv[i] : "NULL");
    }
    ESP_LOGI(TAG, "---");
    return 0;
}

/**
 * @brief Open a SQLite database file
 * 
 * @param filename Path to the database file
 * @param db Pointer to SQLite database handle
 * @return int SQLITE_OK on success, error code on failure
 */
static int obd_logger_db_open(const char *filename, sqlite3 **db) {
    int rc = sqlite3_open(filename, db);
    if (rc) {
        ESP_LOGE(TAG, "Can't open database: %s", sqlite3_errmsg(*db));
        return rc;
    } else {
        ESP_LOGI(TAG, "Opened database successfully: %s", filename);
    }
    return rc;
}

/**
 * @brief Execute a SQL statement
 * 
 * @param db SQLite database handle
 * @param sql SQL statement to execute
 * @return int SQLITE_OK on success, error code on failure
 */
static int obd_logger_db_exec(sqlite3 *db, const char *sql) {
    char *zErrMsg = 0;
    ESP_LOGD(TAG, "Executing SQL: %s", sql);
    
    int64_t start = esp_timer_get_time();
    int rc = sqlite3_exec(db, sql, obd_logger_ex_cb, NULL, &zErrMsg);
    
    if (rc != SQLITE_OK) {
        ESP_LOGE(TAG, "SQL error: %s", zErrMsg);
        sqlite3_free(zErrMsg);
    } else {
        ESP_LOGD(TAG, "SQL operation completed successfully");
    }
    
    ESP_LOGD(TAG, "SQL execution time: %lld ms", (esp_timer_get_time() - start) / 1000);
    return rc;
}

/**
 * @brief Handler for database manager events
 */
static void obd_logger_db_event_handler(obd_db_event_t event, void* event_data)
{
    switch (event) {
        case OBD_DB_ROTATION_STARTED:
            ESP_LOGI(TAG, "Database rotation started - closing current DB");
            if (db_file != NULL) {
                // Close the database but keep the mutex (will be released after rotation)
                if (xSemaphoreTake(db_mutex, portMAX_DELAY) == pdTRUE) {
                    sqlite3_close(db_file);
                    db_file = NULL;
                    // Note: We don't release the mutex here, it will be released
                    // after the new database is opened
                }
            }
            break;

        case OBD_DB_ROTATION_COMPLETED:
            {
                ESP_LOGI(TAG, "Database rotation completed - opening new DB");
                // Get the new database path
                char new_db_path[128];
                if (obd_db_manager_get_current_path(new_db_path, sizeof(new_db_path)) == ESP_OK) {
                    // Update the path
                    strncpy(db_path, new_db_path, sizeof(db_path) - 1);
                    db_path[sizeof(db_path) - 1] = '\0';
                    
                    // Open the new database
                    if (obd_logger_db_open(db_path, &db_file)) {
                        ESP_LOGE(TAG, "Failed to open new database after rotation");
                    } else {
                        // Initialize tables in the new database
                        obd_logger_db_exec(db_file, sql_param_data);
                        obd_logger_db_exec(db_file, sql_param_info);
                        
                        // Set performance-optimized journal mode
                        obd_logger_db_exec(db_file, "PRAGMA journal_mode = WAL;");
                        obd_logger_db_exec(db_file, "PRAGMA synchronous = NORMAL;");
                        obd_logger_db_exec(db_file, "PRAGMA cache_size = 20000;");
                    }
                    
                    // If we took the mutex during rotation start, release it now
                    xSemaphoreGive(db_mutex);
                }
            }
            break;

        case OBD_DB_ROTATION_FAILED:
            ESP_LOGE(TAG, "Database rotation failed");
            // If we took the mutex during rotation start, release it
            xSemaphoreGive(db_mutex);
            break;

        case OBD_DB_SIZE_WARNING:
            if (event_data) {
                uint32_t size = *(uint32_t*)event_data;
                ESP_LOGW(TAG, "Database approaching size limit: %"PRIu32" bytes", size);
            }
            break;
    }
}

/**
 * @brief Acquire a lock on the database mutex
 * 
 * Attempts to take the database mutex with a specified wait time. 
 * If the mutex is not initialized, logs an error message.
 * 
 * @param wait_ms Wait time in milliseconds
 * @return esp_err_t ESP_OK on success, ESP_FAIL on failure
 */
esp_err_t obd_logger_lock(uint32_t wait_ms) {
    if (db_mutex != NULL) {
        if (xSemaphoreTake(db_mutex, pdMS_TO_TICKS(wait_ms)) != pdTRUE) {
            ESP_LOGE(TAG, "Failed to take mutex");
            return ESP_FAIL;
        }
        return ESP_OK;
    } else {
        ESP_LOGE(TAG, "Mutex not initialized");
        return ESP_FAIL;
    }
}
/**
 * @brief Release the database mutex
 * 
 * Attempts to release the database mutex. If the mutex is not initialized,
 * logs an error message.
 */
void obd_logger_unlock(void) {
    if (db_mutex != NULL) {
        xSemaphoreGive(db_mutex);
    } else {
        ESP_LOGE(TAG, "Mutex not initialized");
    }
}

/**
 * @brief Initialize the database and create necessary tables
 * 
 * @param db_path Path to the database file
 * @return esp_err_t ESP_OK on success, ESP_FAIL on failure
 */
void obd_logger_lock_close(void) {
    if (db_mutex != NULL) {
        if (xSemaphoreTake(db_mutex, portMAX_DELAY) == pdTRUE) {
            if (db_file != NULL) {
                sqlite3_close(db_file);
                db_file = NULL;
            }
            // Note: mutex remains taken until obd_logger_unlock_open is called
        } else {
            ESP_LOGE(TAG, "Failed to take mutex");
        }
    } else {
        ESP_LOGE(TAG, "Mutex not initialized");
    }
}

/**
 * @brief Open the database and unlock the mutex
 * 
 * Opens the database using the specified path and then releases the database mutex.
 */
void obd_logger_unlock_open(void) {
    // Get the current path from the DB manager
    char current_path[128];
    if (obd_db_manager_get_current_path(current_path, sizeof(current_path)) == ESP_OK) {
        // Update the local path
        strncpy(db_path, current_path, sizeof(db_path) - 1);
        db_path[sizeof(db_path) - 1] = '\0';
        
        // Open the database
        obd_logger_db_open(db_path, &db_file);
    } else {
        ESP_LOGE(TAG, "Failed to get current database path");
    }
    
    obd_logger_unlock();
}

/**
 * @brief Execute a SQL statement on the database
 * 
 * @param sql SQL statement to execute
 * @param callback Optional callback function to process query results
 * @return int SQLITE_OK on success, error code on failure
 */
int obd_logger_db_execute(char *sql, sqlite3_callback callback, void *callback_arg) {
    char *zErrMsg = 0;
    int rc = sqlite3_exec(db_file, sql, callback, callback_arg, &zErrMsg);
    
    if (rc != SQLITE_OK) {
        ESP_LOGE(TAG, "SQL error: %s", zErrMsg);
        sqlite3_free(zErrMsg);
    }
    
    return rc;
}

/**
 * @brief Get the total number of entries in the database
 * 
 * @param count Pointer to store the count value
 * @return esp_err_t ESP_OK on success, ESP_FAIL on failure
 */
esp_err_t obd_logger_get_total_entries(uint32_t *count)
{
    if (db_file == NULL) 
    {
        ESP_LOGE(TAG, "Database not initialized");
        return ESP_FAIL;
    }
    
    if (count == NULL)
    {
        ESP_LOGE(TAG, "Invalid pointer provided");
        return ESP_FAIL;
    }
    
    // Initialize count to 0
    *count = 0;
    
    if (xSemaphoreTake(db_mutex, portMAX_DELAY) != pdTRUE) 
    {
        ESP_LOGE(TAG, "Failed to take mutex");
        return ESP_FAIL;
    }
    
    // Prepare the query to count total entries
    const char *query = "SELECT COUNT(*) FROM param_data;";
    
    sqlite3_stmt *stmt;
    int rc = sqlite3_prepare_v2(db_file, query, -1, &stmt, NULL);
    
    if (rc != SQLITE_OK)
    {
        ESP_LOGE(TAG, "Failed to prepare statement: %s", sqlite3_errmsg(db_file));
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    // Execute the query
    if (sqlite3_step(stmt) == SQLITE_ROW)
    {
        // Get the count value
        *count = sqlite3_column_int(stmt, 0);
        ESP_LOGI(TAG, "Total entries in database: %lu", *count);
    }
    else
    {
        ESP_LOGW(TAG, "Failed to get count from database");
    }
    
    sqlite3_finalize(stmt);
    xSemaphoreGive(db_mutex);
    
    return ESP_OK;
}

esp_err_t obd_logger_init_params(const obd_param_entry_t *param_entries, size_t count)
{
    int64_t start_time = esp_timer_get_time();

    if (db_file == NULL) 
    {
        ESP_LOGE(TAG, "Database not initialized");
        return ESP_FAIL;
    }
    
    if (xSemaphoreTake(db_mutex, portMAX_DELAY) != pdTRUE) 
    {
        ESP_LOGE(TAG, "Failed to take mutex");
        return ESP_FAIL;
    }
    
    // Try to insert parameters - if they already exist, it will fail but that's fine
    char query[512];
    for (size_t i = 0; i < count; i++) 
    {
        snprintf(query, sizeof(query), 
                 "INSERT OR IGNORE INTO param_info (Name, Type, Data) VALUES ('%s', '%s', '%s');", 
                 param_entries[i].name, param_entries[i].type, param_entries[i].metadata);
        obd_logger_db_exec(db_file, query);
    }
    
    // Now query all parameters to build the lookup table
    sqlite3_stmt *stmt;
    const char *sql = "SELECT Id, Name, Type FROM param_info;";
    int rc = sqlite3_prepare_v2(db_file, sql, -1, &stmt, NULL);
    
    if (rc != SQLITE_OK)
    {
        ESP_LOGE(TAG, "Failed to prepare statement: %s", sqlite3_errmsg(db_file));
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    // Clear the lookup table
    memset(param_lookup, 0, sizeof(param_lookup));
    param_count = 0;
    
    // Populate the lookup table with all parameters
    while (sqlite3_step(stmt) == SQLITE_ROW && param_count < MAX_PARAMS)
    {
        param_lookup[param_count].id = sqlite3_column_int(stmt, 0);
        strncpy(param_lookup[param_count].name, (const char*)sqlite3_column_text(stmt, 1), sizeof(param_lookup[0].name) - 1);
        param_lookup[param_count].name[sizeof(param_lookup[0].name) - 1] = '\0'; // Ensure null termination
        strncpy(param_lookup[param_count].type, (const char*)sqlite3_column_text(stmt, 2), sizeof(param_lookup[0].type) - 1);
        param_lookup[param_count].type[sizeof(param_lookup[0].type) - 1] = '\0'; // Ensure null termination
        
        ESP_LOGI(TAG, "Loaded parameter: ID=%d, Name=%s, Type=%s", 
                param_lookup[param_count].id, 
                param_lookup[param_count].name,
                param_lookup[param_count].type);
        
        param_count++;
    }
    
    sqlite3_finalize(stmt);
    xSemaphoreGive(db_mutex);
    
    int64_t end_time = esp_timer_get_time();
    ESP_LOGI(TAG, "Loaded %d parameters into lookup table in %lld ms", param_count, (end_time - start_time)/1000);
    return ESP_OK;
}

/**
 * @brief Store multiple parameters in the database only if their values have changed significantly
 * 
 * @param params Array of parameter name and value pairs
 * @param count Number of parameters to store
 * @return esp_err_t ESP_OK on success, ESP_FAIL on failure
 */
esp_err_t obd_logger_store_params(const param_value_t *params, size_t count)
{
    ESP_LOGI(TAG, "obd_logger_store_params called with %d parameters", count);
    
    if (db_file == NULL || count == 0) 
    {
        ESP_LOGE(TAG, "Database not initialized or no parameters");
        return ESP_FAIL;
    }

    // First pass: count how many parameters have actually changed significantly
    size_t changed_count = 0;
    for (size_t i = 0; i < count; i++) {
        if (params[i].changed) {
            changed_count++;
        }
    }
    
    if (changed_count == 0) {
        ESP_LOGI(TAG, "No parameter values changed significantly (threshold: 0.001), skipping storage");
        return ESP_OK;
    }
    
    ESP_LOGI(TAG, "%d out of %d parameters have changed significantly", changed_count, count);

    // Check if database rotation is needed before writing
    obd_db_manager_check_and_rotate();

    if (xSemaphoreTake(db_mutex, portMAX_DELAY) != pdTRUE) 
    {
        ESP_LOGE(TAG, "Failed to take mutex");
        return ESP_FAIL;
    }
    
    time_t unix_timestamp = rtcm_get_unix_timestamp();
    
    int64_t start_time = esp_timer_get_time();
    int total_stored = 0;
    int rc;
    
    // Temporarily optimize SQLite for maximum performance during bulk insert
    sqlite3_exec(db_file, "PRAGMA synchronous = OFF;", NULL, NULL, NULL);
    sqlite3_exec(db_file, "PRAGMA journal_mode = MEMORY;", NULL, NULL, NULL);
    
    // Begin transaction
    rc = sqlite3_exec(db_file, "BEGIN TRANSACTION;", NULL, NULL, NULL);
    if (rc != SQLITE_OK) {
        ESP_LOGE(TAG, "Failed to begin transaction: %s", sqlite3_errmsg(db_file));
        sqlite3_exec(db_file, "PRAGMA synchronous = NORMAL;", NULL, NULL, NULL);
        sqlite3_exec(db_file, "PRAGMA journal_mode = WAL;", NULL, NULL, NULL);
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    // Prepare statement
    sqlite3_stmt *stmt;
    const char *sql = "INSERT INTO param_data (timestamp, param_id, value) VALUES (?, ?, ?);";
    rc = sqlite3_prepare_v2(db_file, sql, -1, &stmt, NULL);
    
    if (rc != SQLITE_OK) {
        ESP_LOGE(TAG, "Failed to prepare statement: %s", sqlite3_errmsg(db_file));
        sqlite3_exec(db_file, "ROLLBACK;", NULL, NULL, NULL);
        sqlite3_exec(db_file, "PRAGMA synchronous = NORMAL;", NULL, NULL, NULL);
        sqlite3_exec(db_file, "PRAGMA journal_mode = WAL;", NULL, NULL, NULL);
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    // Pre-bind the timestamp (same for all rows)
    sqlite3_bind_int64(stmt, 1, unix_timestamp);
    
    // Prepare for faster parameter lookup
    // We'll build a simple lookup array only for significantly changed parameters
    int *param_id_cache = malloc(count * sizeof(int));
    if (!param_id_cache) {
        sqlite3_finalize(stmt);
        sqlite3_exec(db_file, "ROLLBACK;", NULL, NULL, NULL);
        sqlite3_exec(db_file, "PRAGMA synchronous = NORMAL;", NULL, NULL, NULL);
        sqlite3_exec(db_file, "PRAGMA journal_mode = WAL;", NULL, NULL, NULL);
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    // Pre-lookup all parameter IDs to avoid repeated string comparisons
    for (size_t i = 0; i < count; i++) {
        param_id_cache[i] = -1;
        
        // Only lookup parameters that have changed significantly
        if (params[i].changed) {
            for (int j = 0; j < param_count; j++) {
                if (strcmp(param_lookup[j].name, params[i].name) == 0) {
                    param_id_cache[i] = param_lookup[j].id;
                    break;
                }
            }
            
            if (param_id_cache[i] == -1) {
                ESP_LOGW(TAG, "Parameter '%s' not found in lookup table", params[i].name);
            }
        }
    }

    
    // Insert only parameters that have changed significantly
    for (size_t i = 0; i < count; i++) {

        // Skip parameters that haven't changed or changed insignificantly
        if (!params[i].changed) {
            ESP_LOGD(TAG, "Skipping parameter '%s': changed flag is false", params[i].name);
            continue;
        }
        
        if (param_id_cache[i] == -1) {
            continue;  // Skip parameters not found in lookup table
        }
        
        ESP_LOGD(TAG, "Storing parameter '%s': %.6f -> %.6f ", 
                 params[i].name, params[i].old_value, params[i].value);
        
        // Bind parameter ID and value (timestamp is already bound)
        sqlite3_bind_int(stmt, 2, param_id_cache[i]);
        sqlite3_bind_double(stmt, 3, params[i].value);
        
        // Execute statement
        rc = sqlite3_step(stmt);
        if (rc != SQLITE_DONE) {
            ESP_LOGW(TAG, "Failed to insert parameter '%s': %s", 
                     params[i].name, sqlite3_errmsg(db_file));
        } else {
            total_stored++;
        }
        
        // Reset statement for next parameter (but keep timestamp binding)
        sqlite3_reset(stmt);
    }
    
    // Clean up
    sqlite3_finalize(stmt);
    free(param_id_cache);
    
    // Commit transaction
    rc = sqlite3_exec(db_file, "COMMIT;", NULL, NULL, NULL);
    if (rc != SQLITE_OK) {
        ESP_LOGE(TAG, "Failed to commit transaction: %s", sqlite3_errmsg(db_file));
        sqlite3_exec(db_file, "ROLLBACK;", NULL, NULL, NULL);
    }
    
    // Restore normal SQLite settings
    sqlite3_exec(db_file, "PRAGMA synchronous = NORMAL;", NULL, NULL, NULL);
    sqlite3_exec(db_file, "PRAGMA journal_mode = WAL;", NULL, NULL, NULL);
    
    int64_t end_time = esp_timer_get_time();
    ESP_LOGI(TAG, "Successfully stored %d significantly changed parameters out of %zu total in database. Storage time: %lld ms", 
             total_stored, count, (end_time - start_time) / 1000);
    
    xSemaphoreGive(db_mutex);
    return ESP_OK;
}

static esp_err_t init_db_tables(void)
{
    if (xSemaphoreTake(db_mutex, portMAX_DELAY) == pdTRUE)
    {
        // Initialize the SQLite database connection
        if (obd_logger_db_open(db_path, &db_file))
        {
            ESP_LOGE(TAG, "Failed to open database");
            db_path[0] = '\0';
            xSemaphoreGive(db_mutex);
            return ESP_FAIL;
        }

        // Execute the SQL statements
        if(obd_logger_db_exec(db_file, sql_param_data))
        {
            ESP_LOGE(TAG, "Failed to create param_data table");
            sqlite3_close(db_file);
            db_file = NULL;
            db_path[0] = '\0';
            xSemaphoreGive(db_mutex);
            return ESP_FAIL;
        }

        if(obd_logger_db_exec(db_file, sql_param_info))
        {
            ESP_LOGE(TAG, "Failed to create param_info table");
            sqlite3_close(db_file);
            db_file = NULL;
            db_path[0] = '\0';
            xSemaphoreGive(db_mutex);
            return ESP_FAIL;
        }
        
        // Set performance-optimized journal mode
        obd_logger_db_exec(db_file, "PRAGMA journal_mode = WAL;");
        
        // Reduce sync overhead (with some durability trade-offs)
        obd_logger_db_exec(db_file, "PRAGMA synchronous = NORMAL;");
        
        // Increase cache size to reduce disk I/O
        obd_logger_db_exec(db_file, "PRAGMA cache_size = 20000;");
        
        ESP_LOGI(TAG, "Database tables initialized");
        xSemaphoreGive(db_mutex);
    }
    return ESP_OK;
}

/**
 * @brief Callback for processing query results
 */
typedef int (*query_callback_t)(void *user_data, int argc, char **argv, char **col_name);

/**
 * @brief Query parameter values from the database
 * 
 * @param param_name Name of the parameter to query
 * @param start_time Optional start time filter (format: "YYYY-MM-DD HH:MM:SS"), NULL for no filter
 * @param end_time Optional end time filter (format: "YYYY-MM-DD HH:MM:SS"), NULL for no filter
 * @param limit Maximum number of records to return (0 for no limit)
 * @param callback Callback function to process results
 * @param user_data User data passed to callback
 * @return esp_err_t ESP_OK on success, ESP_FAIL on failure
 */
esp_err_t obd_logger_query_param(const char *param_name, const char *start_time, 
                                 const char *end_time, int limit,
                                 query_callback_t callback, void *user_data)
{
    if (db_file == NULL) 
    {
        ESP_LOGE(TAG, "Database not initialized");
        return ESP_FAIL;
    }
    
    if (xSemaphoreTake(db_mutex, portMAX_DELAY) != pdTRUE) 
    {
        ESP_LOGE(TAG, "Failed to take mutex");
        return ESP_FAIL;
    }
    
    // Find the parameter ID from the lookup table
    int param_id = -1;
    for (int i = 0; i < param_count; i++) 
    {
        if (strcmp(param_lookup[i].name, param_name) == 0) 
        {
            param_id = param_lookup[i].id;
            break;
        }
    }
    
    if (param_id == -1) 
    {
        ESP_LOGE(TAG, "Parameter '%s' not found in lookup table", param_name);
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    // Prepare SQL query with optional filters
    char query[512];
    char *query_ptr = query;
    int remaining = sizeof(query);
    int written;
    
    // Base query - convert timestamp to readable format in the query
    written = snprintf(query_ptr, remaining, 
                     "SELECT datetime(timestamp, 'unixepoch') as DateTime, pd.value, pi.Name, pi.Type, pi.Data "
                     "FROM param_data pd "
                     "JOIN param_info pi ON pd.param_id = pi.Id "
                     "WHERE pd.param_id = %d", param_id);
    query_ptr += written;
    remaining -= written;
    
    // Convert start_time and end_time strings to Unix timestamps if provided
    if (start_time != NULL && remaining > 0) {
        struct tm tm_start = {0};
        if (strptime(start_time, "%Y-%m-%d %H:%M:%S", &tm_start) != NULL) {
            time_t start_timestamp = mktime(&tm_start);
            written = snprintf(query_ptr, remaining, " AND pd.timestamp >= %lld", (long long)start_timestamp);
            query_ptr += written;
            remaining -= written;
        }
    }
    
    if (end_time != NULL && remaining > 0) {
        struct tm tm_end = {0};
        if (strptime(end_time, "%Y-%m-%d %H:%M:%S", &tm_end) != NULL) {
            time_t end_timestamp = mktime(&tm_end);
            written = snprintf(query_ptr, remaining, " AND pd.timestamp <= %lld", (long long)end_timestamp);
            query_ptr += written;
            remaining -= written;
        }
    }
    
    // Add order and limit
    if (remaining > 0) {
        written = snprintf(query_ptr, remaining, " ORDER BY pd.timestamp DESC");
        query_ptr += written;
        remaining -= written;
    }
    
    if (limit > 0 && remaining > 0) {
        written = snprintf(query_ptr, remaining, " LIMIT %d", limit);
        query_ptr += written;
        remaining -= written;
    }
    
    // Add terminating semicolon
    if (remaining > 0) {
        written = snprintf(query_ptr, remaining, ";");
    }
    
    // Ensure query is properly terminated
    query[sizeof(query) - 1] = '\0';
    
    ESP_LOGI(TAG, "Executing query: %s", query);
    
    // Execute the query
    char *err_msg = NULL;
    int rc = sqlite3_exec(db_file, query, callback, user_data, &err_msg);
    
    if (rc != SQLITE_OK) {
        ESP_LOGE(TAG, "SQL error: %s", err_msg);
        sqlite3_free(err_msg);
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    xSemaphoreGive(db_mutex);
    return ESP_OK;
}

/**
 * @brief Get the most recent timestamp from the database
 * 
 * @param datetime Buffer to store the datetime string
 * @param max_len Maximum length of the buffer
 * @return esp_err_t ESP_OK on success, ESP_FAIL on failure
 */
esp_err_t obd_logger_get_latest_time(char *datetime, size_t max_len)
{
    if (db_file == NULL) 
    {
        ESP_LOGE(TAG, "Database not initialized");
        return ESP_FAIL;
    }
    
    if (datetime == NULL || max_len == 0)
    {
        ESP_LOGE(TAG, "Invalid buffer provided");
        return ESP_FAIL;
    }
    
    // Set empty string as default return
    datetime[0] = '\0';
    
    if (xSemaphoreTake(db_mutex, portMAX_DELAY) != pdTRUE) 
    {
        ESP_LOGE(TAG, "Failed to take mutex");
        return ESP_FAIL;
    }
    
    // Prepare the query to get the latest timestamp
    const char *query = "SELECT timestamp FROM param_data ORDER BY timestamp DESC LIMIT 1;";
    
    sqlite3_stmt *stmt;
    int rc = sqlite3_prepare_v2(db_file, query, -1, &stmt, NULL);
    
    if (rc != SQLITE_OK)
    {
        ESP_LOGE(TAG, "Failed to prepare statement: %s", sqlite3_errmsg(db_file));
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    // Execute the query
    if (sqlite3_step(stmt) == SQLITE_ROW)
    {
        // Get the timestamp value
        time_t unix_time = sqlite3_column_int64(stmt, 0);
        struct tm *timeinfo = localtime(&unix_time);
        
        // Format the time as a string
        strftime(datetime, max_len, "%Y-%m-%d %H:%M:%S", timeinfo);
        ESP_LOGI(TAG, "Latest datetime in database: %s", datetime);
    }
    else
    {
        ESP_LOGW(TAG, "No records found in database");
    }
    
    sqlite3_finalize(stmt);
    xSemaphoreGive(db_mutex);
    
    // If we got a value, return success
    return (datetime[0] != '\0') ? ESP_OK : ESP_FAIL;
}

static void obd_logger_task(void *pvParameters)
{
    char db_time[32] = {0};
    
    // Get the latest time from the database
    esp_err_t ret1 = obd_logger_get_latest_time(db_time, sizeof(db_time));

    if (ret1 == ESP_OK) {
        ESP_LOGI(TAG, "Latest database time: %s", db_time);
    } else {
        ESP_LOGW(TAG, "No data in database or error retrieving latest time");
    }

    uint32_t entry_count = 0;
    if (obd_logger_get_total_entries(&entry_count) == ESP_OK) {
        ESP_LOGI(TAG, "Current database size: %lu entries", entry_count);
    }
    
    // Allocate arrays with proper string storage
    param_value_t *param_values = heap_caps_malloc(sizeof(param_value_t) * obd_logger_params_count, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    char (*param_names)[MAX_PARAMS] = heap_caps_malloc(sizeof(char[MAX_PARAMS]) * obd_logger_params_count, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (param_values == NULL || param_names == NULL) {
        ESP_LOGE(TAG, "Failed to allocate memory for parameters");
        if (param_values) heap_caps_free(param_values);
        if (param_names) heap_caps_free(param_names);
        vTaskDelete(NULL);
        return;
    }else{
        memset(param_values, 0, sizeof(param_value_t) * obd_logger_params_count);
        memset(param_names, 0, sizeof(char[MAX_PARAMS]) * obd_logger_params_count);
    }

    for(int i = 0; i < obd_logger_params_count; i++)
    {
        param_values[i].changed = false;
        param_values[i].old_value = FLT_MAX;
    }

    obd_logger_set_initialized();
    obd_logger_enable();
    
    while (1)
    {
        // Wait for logging to be enabled or check current state
        if (obd_logger_event_group != NULL) {
            EventBits_t bits = xEventGroupWaitBits(
                obd_logger_event_group,
                OBD_LOGGER_ENABLED_BIT,
                pdFALSE,        // Don't clear bits
                pdTRUE,         // Wait for any bit
                portMAX_DELAY   // Wait indefinitely
            );
        }
        
        // Get parameters from callback function
        if (obd_logger_get_params != NULL) {
            char* params_json = obd_logger_get_params();
            int64_t start_time = esp_timer_get_time();
            if (params_json != NULL) {
                ESP_LOGI(TAG, "Received parameters from callback: %s", params_json);
                
                // Parse the parameters from JSON and store them
                cJSON *root = cJSON_Parse(params_json);
                if (root != NULL) {
                    
                    if (param_values == NULL || param_names == NULL) {
                        ESP_LOGE(TAG, "Failed to allocate memory for parameters");
                        cJSON_Delete(root);
                        free(params_json);
                        continue;
                    }
                    
                    int valid_params = 0;
                    
                    // Iterate through all JSON object members
                    cJSON *element = root->child;
                    while (element != NULL && valid_params < obd_logger_params_count) {
                        if (element->string != NULL) {
                            ESP_LOGD(TAG, "Processing JSON element: %s, type: %d", 
                                    element->string, element->type);
                            
                            if (cJSON_IsNumber(element)) {
                                // Copy the parameter name to avoid issues with pointers
                                strncpy(param_names[valid_params], element->string, 49);
                                param_names[valid_params][49] = '\0';
                                param_values[valid_params].old_value = param_values[valid_params].value;
                                param_values[valid_params].name = param_names[valid_params];
                                param_values[valid_params].value = (float)element->valuedouble;
                                
                                ESP_LOGD(TAG, "Parsed parameter %d: %s = %f", 
                                         valid_params,
                                         param_values[valid_params].name, 
                                         param_values[valid_params].value);
                                
                                valid_params++;
                            }
                        }
                        element = element->next;
                    }
                    
                    ESP_LOGI(TAG, "Total valid parameters parsed: %d", valid_params);
                    
                    if (valid_params > 0) {

                        size_t changed_count = 0;
                        for (size_t i = 0; i < valid_params; i++) {
                            float diff = fabsf(param_values[i].value - param_values[i].old_value);
                            if ( diff > 0.001f) {
                                param_values[i].changed = true;
                                changed_count++;
                                ESP_LOGD(TAG, "Parameter %s changed: %.3f -> %.3f (diff: %.3f)", 
                                        param_values[i].name, param_values[i].old_value, 
                                        param_values[i].value, diff);
                            }else{
                                param_values[i].changed = false;
                                ESP_LOGD(TAG, "Parameter %s unchanged: %.3f (diff: %.3f)", 
                                        param_values[i].name, param_values[i].value, diff);
                            }
                        }

                        ESP_LOGI(TAG, "Storing in database changed parameters %d out of %d ", changed_count, valid_params);
                        
                        // Debug: Print all parameters before storing
                        for (int i = 0; i < valid_params; i++) {
                            ESP_LOGD(TAG, "Parameter %d: %s = %f", 
                                     i, param_values[i].name, param_values[i].value);
                        }
                        
                        esp_err_t result = obd_logger_store_params(param_values, valid_params);
                        if (result != ESP_OK) {
                            ESP_LOGE(TAG, "Failed to store parameters in database");
                        }
                    } else {
                        ESP_LOGW(TAG, "No valid parameters found in JSON");
                    }
                    
                    cJSON_Delete(root);
                } else {
                    ESP_LOGE(TAG, "Failed to parse parameters JSON");
                }
                
                free(params_json);
            }
            
            int64_t end_time = esp_timer_get_time();
            ESP_LOGI("EX_TIME", "Parameters processing and storage time: %lld ms", (end_time - start_time) / 1000);
        } else {
            ESP_LOGW(TAG, "Parameter callback function is not set");
        }
        
        // Wait for the configured period before getting parameters again
        uint32_t delay_period = (logger_period > 0) ? (logger_period * 1000) : 10000;
        vTaskDelay(pdMS_TO_TICKS(delay_period));
    }
}

esp_err_t odb_logger_init(obd_logger_t *obd_logger)
{
    db_mutex = xSemaphoreCreateMutex();
    if (db_mutex == NULL)
    {
        ESP_LOGE(TAG, "Failed to create mutex");
        return ESP_FAIL;
    }

    obd_logger_event_group = xEventGroupCreateStatic(&obd_logger_event_group_buffer);
    if (obd_logger_event_group == NULL)
    {
        ESP_LOGE(TAG, "Failed to create event group");
        vSemaphoreDelete(db_mutex);
        return ESP_FAIL;
    }

    logger_period = obd_logger->period_sec;
    obd_logger_params_count = obd_logger->obd_logger_params_count;
    if (obd_logger_params_count > MAX_PARAMS)
    {
        ESP_LOGW(TAG, "Too many parameters defined, max is %d", MAX_PARAMS);
        obd_logger_params_count = MAX_PARAMS;
    }

    if(obd_logger->obd_logger_get_params_cb != NULL)
    {
        obd_logger_get_params = obd_logger->obd_logger_get_params_cb;
    }
    else
    {
        ESP_LOGE(TAG, "Callback function not set");
        return ESP_FAIL;
    }

    sqlite3_initialize();
    // Initialize DB manager with configuration
    obd_db_manager_config_t db_config = {
        .max_size_bytes = 4 * 1024 * 1024,  // 4MB limit
        .max_db_files = 100,                // Keep last 100 database files
        .check_size_before_write = true     // Check size before writing
    };

    strncpy(db_config.base_path, obd_logger->path, sizeof(db_config.base_path) - 1);
    db_config.base_path[sizeof(db_config.base_path) - 1] = '\0';

    esp_err_t ret = obd_db_manager_init(&db_config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize DB manager");
        return ESP_FAIL;
    }
    
    // Register event callback
    obd_db_manager_register_callback(obd_logger_db_event_handler);
    
    // Get the current database path
    ret = obd_db_manager_get_current_path(db_path, sizeof(db_path));
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get current database path");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Database path: %s", db_path);
    ESP_LOGI(TAG, "Creating OBD logger task");

    if(init_db_tables() != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to initialize database tables");
        return ESP_FAIL;
    }

    if(obd_logger_init_params(obd_logger->obd_logger_params, obd_logger_params_count) != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to initialize parameter lookup table");
        return ESP_FAIL;
    }

    // Allocate stack memory in PSRAM for the task
    static StackType_t *obd_logger_task_stack;
    static StaticTask_t obd_logger_task_buffer;
    
    obd_logger_task_stack = heap_caps_malloc(OBD_LOGGERR_TASK_STACK_SIZE, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    
    if (obd_logger_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate task stack memory");
        vSemaphoreDelete(db_mutex);
        return ESP_FAIL;
    }
    
    // Create static task
    TaskHandle_t task_handle = xTaskCreateStatic(
        obd_logger_task,
        "obd_logger",
        OBD_LOGGERR_TASK_STACK_SIZE,
        NULL,
        5,
        obd_logger_task_stack,
        &obd_logger_task_buffer
    );
    
    if (task_handle == NULL)
    {
        ESP_LOGE(TAG, "Failed to create task");
        heap_caps_free(obd_logger_task_stack);
        vSemaphoreDelete(db_mutex);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "OBD logger task created successfully");
    ESP_LOGI(TAG, "OBD logger initialized successfully");

    obd_logger_iface_init();
    // obd_db_manager_force_rotation();
    
    return ESP_OK;
}
