#include <stdio.h>
#include <sys/unistd.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/event_groups.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_littlefs.h"
#include "sqlite3.h"
// #include "sqllib.h"
#include "esp_timer.h"
#include "string.h"
#include "rtcm.h"
#include "obd_logger.h"
#include "cJSON.h"

#define TAG                                 "OBD_LOGGER"
#define OBD_LOGGERR_TASK_STACK_SIZE         (1024*8)

// Define max number of parameters
#define MAX_PARAMS 50

// Create param_data table
const char *sql_param_data = 
    "CREATE TABLE IF NOT EXISTS param_data ("
    "DateTime TEXT, "
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


// Callback debug message
const char* data = "Callback function called";

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
    ESP_LOGI(TAG, "%s: ", (const char*)data);
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
int obd_logger_db_open(const char *filename, sqlite3 **db) {
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
int obd_logger_db_exec(sqlite3 *db, const char *sql) {
    char *zErrMsg = 0;
    ESP_LOGD(TAG, "Executing SQL: %s", sql);
    
    int64_t start = esp_timer_get_time();
    int rc = sqlite3_exec(db, sql, obd_logger_ex_cb, (void*)data, &zErrMsg);
    
    if (rc != SQLITE_OK) {
        ESP_LOGE(TAG, "SQL error: %s", zErrMsg);
        sqlite3_free(zErrMsg);
    } else {
        ESP_LOGI(TAG, "SQL operation completed successfully");
    }
    
    ESP_LOGI(TAG, "SQL execution time: %lld ms", (esp_timer_get_time() - start) / 1000);
    return rc;
}


esp_err_t obd_logger_store_param(const char *param_name, float value)
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
    
    // Get current timestamp from RTCM module
    uint8_t hour, min, sec;
    uint8_t year, month, day, weekday;
    
    esp_err_t ret = rtcm_get_time(&hour, &min, &sec);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get time from RTCM");
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    ret = rtcm_get_date(&year, &month, &day, &weekday);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get date from RTCM");
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    // Convert BCD format to decimal
    uint8_t hour_dec = ((hour >> 4) & 0x0F) * 10 + (hour & 0x0F);
    uint8_t min_dec = ((min >> 4) & 0x0F) * 10 + (min & 0x0F);
    uint8_t sec_dec = ((sec >> 4) & 0x0F) * 10 + (sec & 0x0F);
    uint8_t year_dec = ((year >> 4) & 0x0F) * 10 + (year & 0x0F);
    uint8_t month_dec = ((month >> 4) & 0x0F) * 10 + (month & 0x0F);
    uint8_t day_dec = ((day >> 4) & 0x0F) * 10 + (day & 0x0F);
    
    char timestamp[32];
    // Format: YYYY-MM-DD HH:MM:SS (assumed year is 20XX based on RTC module)
    snprintf(timestamp, sizeof(timestamp), "20%02d-%02d-%02d %02d:%02d:%02d", 
             year_dec, month_dec, day_dec, hour_dec, min_dec, sec_dec);
    
    // Prepare SQL statement to insert the data
    char query[256];
    snprintf(query, sizeof(query), 
             "INSERT INTO param_data (DateTime, param_id, value) VALUES ('%s', %d, %f);", 
             timestamp, param_id, value);
    
    if (obd_logger_db_exec(db_file, query) != SQLITE_OK) 
    {
        ESP_LOGE(TAG, "Failed to insert parameter data: %s", sqlite3_errmsg(db_file));
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    ESP_LOGI(TAG, "Stored parameter: %s = %f at %s", param_name, value, timestamp);
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
 * @brief Store multiple parameters in the database at the same time
 * 
 * @param params Array of parameter name and value pairs
 * @param count Number of parameters to store
 * @return esp_err_t ESP_OK on success, ESP_FAIL on failure
 */
esp_err_t obd_logger_store_params(const param_value_t *params, size_t count)
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
    
    // Get current timestamp from RTCM module
    uint8_t hour, min, sec;
    uint8_t year, month, day, weekday;
    
    esp_err_t ret = rtcm_get_time(&hour, &min, &sec);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get time from RTCM");
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    ret = rtcm_get_date(&year, &month, &day, &weekday);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get date from RTCM");
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    // Convert BCD format to decimal
    uint8_t hour_dec = ((hour >> 4) & 0x0F) * 10 + (hour & 0x0F);
    uint8_t min_dec = ((min >> 4) & 0x0F) * 10 + (min & 0x0F);
    uint8_t sec_dec = ((sec >> 4) & 0x0F) * 10 + (sec & 0x0F);
    uint8_t year_dec = ((year >> 4) & 0x0F) * 10 + (year & 0x0F);
    uint8_t month_dec = ((month >> 4) & 0x0F) * 10 + (month & 0x0F);
    uint8_t day_dec = ((day >> 4) & 0x0F) * 10 + (day & 0x0F);
    
    char timestamp[32];
    // Format: YYYY-MM-DD HH:MM:SS (assumed year is 20XX based on RTC module)
    snprintf(timestamp, sizeof(timestamp), "20%02d-%02d-%02d %02d:%02d:%02d", 
             year_dec, month_dec, day_dec, hour_dec, min_dec, sec_dec);
    
    // Begin transaction
    if (obd_logger_db_exec(db_file, "BEGIN TRANSACTION;") != SQLITE_OK) {
        ESP_LOGE(TAG, "Failed to begin transaction: %s", sqlite3_errmsg(db_file));
        xSemaphoreGive(db_mutex);
        return ESP_FAIL;
    }
    
    bool success = true;
    char query[256];
    
    // Process each parameter
    for (size_t i = 0; i < count; i++) {
        // Find the parameter ID from the lookup table
        int param_id = -1;
        for (int j = 0; j < param_count; j++) {
            if (strcmp(param_lookup[j].name, params[i].name) == 0) {
                param_id = param_lookup[j].id;
                break;
            }
        }
        
        if (param_id == -1) {
            ESP_LOGW(TAG, "Parameter '%s' not found in lookup table, skipping", params[i].name);
            continue;
        }
        
        // Prepare SQL statement to insert the data
        snprintf(query, sizeof(query), 
                 "INSERT INTO param_data (DateTime, param_id, value) VALUES ('%s', %d, %f);", 
                 timestamp, param_id, params[i].value);
        
        if (obd_logger_db_exec(db_file, query) != SQLITE_OK) {
            ESP_LOGE(TAG, "Failed to insert parameter data for '%s': %s", 
                     params[i].name, sqlite3_errmsg(db_file));
            success = false;
            break;
        }
        
        ESP_LOGI(TAG, "Stored parameter: %s = %f", params[i].name, params[i].value);
    }
    
    // Commit or rollback transaction based on success
    if (success) {
        if (obd_logger_db_exec(db_file, "COMMIT;") != SQLITE_OK) {
            ESP_LOGE(TAG, "Failed to commit transaction: %s", sqlite3_errmsg(db_file));
            obd_logger_db_exec(db_file, "ROLLBACK;");
            success = false;
        }
        else {
            ESP_LOGI(TAG, "Successfully stored %d parameters at %s", count, timestamp);
        }
    } else {
        obd_logger_db_exec(db_file, "ROLLBACK;");
        ESP_LOGE(TAG, "Rolling back transaction due to errors");
    }
    
    xSemaphoreGive(db_mutex);
    return success ? ESP_OK : ESP_FAIL;
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
    
    // Base query
    written = snprintf(query_ptr, remaining, 
                     "SELECT pd.DateTime, pd.value, pi.Name, pi.Type, pi.Data "
                     "FROM param_data pd "
                     "JOIN param_info pi ON pd.param_id = pi.Id "
                     "WHERE pd.param_id = %d", param_id);
    query_ptr += written;
    remaining -= written;
    
    // Add time range filters if provided
    if (start_time != NULL && remaining > 0) {
        written = snprintf(query_ptr, remaining, " AND pd.DateTime >= '%s'", start_time);
        query_ptr += written;
        remaining -= written;
    }
    
    if (end_time != NULL && remaining > 0) {
        written = snprintf(query_ptr, remaining, " AND pd.DateTime <= '%s'", end_time);
        query_ptr += written;
        remaining -= written;
    }
    
    // Add order and limit
    if (remaining > 0) {
        written = snprintf(query_ptr, remaining, " ORDER BY pd.DateTime DESC");
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
    const char *query = "SELECT DateTime FROM param_data ORDER BY DateTime DESC LIMIT 1;";
    
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
        // Get the datetime value
        const char *time_str = (const char *)sqlite3_column_text(stmt, 0);
        if (time_str)
        {
            strncpy(datetime, time_str, max_len - 1);
            datetime[max_len - 1] = '\0'; // Ensure null termination
            ESP_LOGI(TAG, "Latest datetime in database: %s", datetime);
        }
        else
        {
            ESP_LOGW(TAG, "No datetime value found");
        }
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
    // Initialize the SQLite database connection
    


    // static const obd_param_entry_t default_params[] = {
    //     {"RPM", "NUMERIC", "{\"unit\":\"rpm\"}"},
    //     {"SPEED", "NUMERIC", "{\"unit\":\"km/h\"}"},
    //     {"ENGINE_TEMP", "NUMERIC", "{\"unit\":\"°C\"}"},
    //     {"THROTTLE", "NUMERIC", "{\"unit\":\"%\"}"},
    //     {"FUEL_LEVEL", "NUMERIC", "{\"unit\":\"%\"}"}
    // };
    
    // if(obd_logger_init_params(default_params, sizeof(default_params)/sizeof(default_params[0])) == ESP_FAIL)
    // {
    //     ESP_LOGE(TAG, "Failed to initialize parameter lookup table");
    //     vTaskDelete(NULL);
    // }

    ESP_LOGI(TAG, "Testing time retrieval...");
    
    char db_time[32] = {0};
    char current_time[32] = {0};
    
    // Get the latest time from the database
    esp_err_t ret1 = obd_logger_get_latest_time(db_time, sizeof(db_time));

    if (ret1 == ESP_OK) {
        ESP_LOGI(TAG, "Latest database time: %s", db_time);
    } else {
        ESP_LOGW(TAG, "No data in database or error retrieving latest time");
    }

    // obd_logger_test_store_params();
    // example_query_usage();
    
    while (1)
    {

        // Get parameters from callback function
        if (obd_logger_get_params != NULL) {
            char* params_json = obd_logger_get_params();
            if (params_json != NULL) {
                ESP_LOGI(TAG, "Received parameters from callback: %s", params_json);
                
                // Parse the parameters from JSON and store them
                cJSON *root = cJSON_Parse(params_json);
                if (root != NULL) {
                    param_value_t param_values[obd_logger_params_count];
                    int valid_params = 0;
                    
                    // Iterate through all keys in the JSON object
                    cJSON *element = NULL;
                    cJSON_ArrayForEach(element, root) {
                        // element->string contains the key (param name)
                        // element->valuedouble contains the value (for numeric values)
                        if (cJSON_IsNumber(element) && valid_params < obd_logger_params_count) {
                            param_values[valid_params].name = element->string;
                            param_values[valid_params].value = (float)element->valuedouble;
                            valid_params++;
                        }
                    }
                    
                    if (valid_params > 0) {
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
        } else {
            ESP_LOGW(TAG, "Parameter callback function is not set");
        }
        
        // Wait for the configured period before getting parameters again
        // Use the logger_period value that was set during initialization
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
    sprintf(db_path, "%s/obd_logger.db", obd_logger->path);

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

    BaseType_t ret = xTaskCreate(obd_logger_task, 
                                "obd_logger", 
                                OBD_LOGGERR_TASK_STACK_SIZE, 
                                NULL, 5, NULL);
    if (ret != pdPASS) 
    {
        ESP_LOGE(TAG, "Failed to create task");
        vSemaphoreDelete(db_mutex);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "OBD logger task created successfully");
    ESP_LOGI(TAG, "OBD logger initialized successfully");
    return ESP_OK;
}