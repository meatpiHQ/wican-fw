#ifndef OBD_LOGGER_H
#define OBD_LOGGER_H

#include "esp_err.h"

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

// Initialize OBD logger with a database path
esp_err_t odb_logger_init(obd_logger_t *obd_logger);

// Initialize parameters in the OBD logger database
esp_err_t obd_logger_init_params(const obd_param_entry_t *param_entries, size_t count);

// Store a single parameter value
esp_err_t obd_logger_store_param(const char *param_name, float value);

// Store multiple parameter values at once
esp_err_t obd_logger_store_params(const param_value_t *params, size_t count);


#endif // OBD_LOGGER_H
