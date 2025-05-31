#ifndef OBD_LOGGER_H
#define OBD_LOGGER_H

#include "esp_err.h"

#define DB_FILE_NAME    "obd_data.db"
#define DB_DIR_NAME     "obd_logs"
#define DB_ROOT_PATH    "/sdcard"

// Parameter entry structure
typedef struct {
    const char *name;     // Parameter name
    const char *type;     // Parameter type (usually "NUMERIC")
    const char *metadata; // JSON metadata string
} obd_param_entry_t;

// Structure to hold parameter name and value pairs for logging
typedef struct {
    const char *name;
    float value;
    float old_value;
    bool changed;
} param_value_t;

// Typedef for the get parameters callback function
typedef char* (*obd_logger_get_params_cb_t)(void);

// structure to hold logger init parameters
typedef struct{
    char* path;
    uint32_t period_sec;
    char* db_filename;
    obd_logger_get_params_cb_t obd_logger_get_params_cb;
    obd_param_entry_t *obd_logger_params;
    uint32_t obd_logger_params_count;
}obd_logger_t;

typedef int (*obd_logger_db_exec_cb)(void *data, int argc, char **argv, char **azColName);

esp_err_t odb_logger_init(obd_logger_t *obd_logger);
esp_err_t obd_logger_init_params(const obd_param_entry_t *param_entries, size_t count);
esp_err_t obd_logger_store_params(const param_value_t *params, size_t count);
esp_err_t obd_logger_lock(uint32_t wait_ms);
void obd_logger_unlock(void);
void obd_logger_lock_close(void);
void obd_logger_unlock_open(void);
int obd_logger_db_execute(char *sql, obd_logger_db_exec_cb callback, void *callback_arg);
void obd_logger_enable(void);
void obd_logger_disable(void);
bool obd_logger_is_enabled(void);
bool obd_logger_is_initialized(void);

#endif // OBD_LOGGER_H
