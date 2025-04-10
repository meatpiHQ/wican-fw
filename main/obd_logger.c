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
#include "sqllib.h"
#include "db_iface.h"
#include "esp_timer.h"
#include "string.h"

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

// static int exec_callback(void *data, int argc, char **argv, char **azColName) 
// {
//     // Callback function for SQLite execution
//     return 0;
// }

// Define common OBD parameters
const char *params[][3] = {
    // {name, type, data_json}
    {"RPM", "NUMERIC", "{\"unit\":\"rpm\",\"pid\":\"0C\",\"formula\":\"(A*256+B)/4\"}"},
    {"SPEED", "NUMERIC", "{\"unit\":\"km/h\",\"pid\":\"0D\",\"formula\":\"A\"}"},
    {"ENGINE_TEMP", "NUMERIC", "{\"unit\":\"°C\",\"pid\":\"05\",\"formula\":\"A-40\"}"},
    {"THROTTLE", "NUMERIC", "{\"unit\":\"%\",\"pid\":\"11\",\"formula\":\"A*100/255\"}"},
    {"FUEL_LEVEL", "NUMERIC", "{\"unit\":\"%\",\"pid\":\"2F\",\"formula\":\"A*100/255\"}"},
    {"RPM", "NUMERIC", "{\"unit\":\"rpm\",\"pid\":\"0C\",\"formula\":\"(A*256+B)/4\"}"}
    // Add more parameters as needed
};

static esp_err_t init_param(void)
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
    char query[256];
    for (int i = 0; i < sizeof(params)/sizeof(params[0]); i++) 
    {
        snprintf(query, sizeof(query), 
                 "INSERT OR IGNORE INTO param_info (Name, Type, Data) VALUES ('%s', '%s', '%s');", 
                 params[i][0], params[i][1], params[i][2]);
        db_exec(db_file, query);
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
    ESP_LOGI(TAG, "Loaded %d parameters into lookup table in %lld microseconds", param_count, end_time - start_time);
    return ESP_OK;
}

static esp_err_t init_db_tables(void)
{
    if (xSemaphoreTake(db_mutex, portMAX_DELAY) == pdTRUE)
    {
        // Initialize the SQLite database connection
        if (db_open(db_path, &db_file))
        {
            ESP_LOGE(TAG, "Failed to open database");
            db_path[0] = '\0';
            xSemaphoreGive(db_mutex);
            return ESP_FAIL;
        }

        // Execute the SQL statements
        if(db_exec(db_file, sql_param_data))
        {
            ESP_LOGE(TAG, "Failed to create param_data table");
            sqlite3_close(db_file);
            db_file = NULL;
            db_path[0] = '\0';
            xSemaphoreGive(db_mutex);
            return ESP_FAIL;
        }

        if(db_exec(db_file, sql_param_info))
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

static void obd_logger_task(void *pvParameters)
{
    // Initialize the SQLite database connection
    
    if(init_db_tables() == ESP_FAIL)
    {
        ESP_LOGE(TAG, "Failed to initialize database tables");
        vTaskDelete(NULL);
    }
    if(init_param() == ESP_FAIL)
    {
        ESP_LOGE(TAG, "Failed to initialize parameter lookup table");
        vTaskDelete(NULL);
    }
    while (1)
    {
        if (xSemaphoreTake(db_mutex, portMAX_DELAY) == pdTRUE)
        {


            xSemaphoreGive(db_mutex);
        }
        
        // Task delay to prevent watchdog triggers
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

esp_err_t odb_logger_init(const char *path)
{
    db_mutex = xSemaphoreCreateMutex();
    if (db_mutex == NULL)
    {
        ESP_LOGE(TAG, "Failed to create mutex");
        return ESP_FAIL;
    }
    sqlite3_initialize();
    sprintf(db_path, "%s/obd_logger.db", path);

    ESP_LOGI(TAG, "Database path: %s", db_path);
    ESP_LOGI(TAG, "Creating OBD logger task");

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

    return ESP_OK;
}