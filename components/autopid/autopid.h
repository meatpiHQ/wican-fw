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

#ifndef __AUTO_PID_H__
#define __AUTO_PID_H__

#include <stdbool.h>
#include <stdint.h>

#include <esp_err.h>
#include <time.h>

#define AUTOPID_BUFFER_SIZE (1024*4)
#define QUEUE_SIZE 10

// Safety caps for user-configurable destinations (prevents stack/heap abuse via config JSON)
#define AUTOPID_MAX_DESTINATIONS 6

typedef struct {
    uint8_t data[AUTOPID_BUFFER_SIZE];
    uint32_t length;
    uint8_t* priority_data;
    uint8_t  priority_data_len;
} response_t;

typedef enum
{
    SENSOR = 0,
    BINARY_SENSOR = 1,
} sensor_type_t;

////////////////

typedef enum
{
    PID_STD = 0,
    PID_CUSTOM = 1,
    PID_SPECIFIC = 2,
    PID_MAX
}pid_type_t;


typedef struct 
{
    char *name;
    char *expression;
    char *unit;
    char *class;
    bool enabled;
    uint32_t period; 
    float min;
    float max;
    sensor_type_t sensor_type;
    int64_t timer;
    float value;
    bool failed;
}parameter_t;

typedef struct 
{
    char* cmd;
    char* init;
    uint32_t period; 
    parameter_t *parameters;
    uint32_t parameters_count;
    pid_type_t pid_type;
    char* rxheader;
    bool enabled;
}pid_data_t;

// CAN filter configuration (broadcast frames parsing)
// Each filter refers to one CAN frame ID that may yield multiple parameters.
typedef struct
{
    uint32_t frame_id;
    bool is_extended; // inferred: frame_id > 0x7FF
    // True if this filter came from car_data.json (vehicle profile);
    // false if it came from auto_pid.json (custom filters).
    bool is_vehicle_specific;
    parameter_t *parameters;
    uint32_t parameters_count;
} can_filter_t;

typedef struct 
{
    pid_data_t *pids;
    uint32_t pid_count;
    // CAN filters (broadcast frames)
    can_filter_t *can_filters;
    uint32_t can_filters_count;
    // Calculated channels (Task #17): math over OTHER decoded channel values (source "CALC").
    // Reuses parameter_t (name/expression/unit/value/enabled/min/max); expression references
    // channel NAMES, not raw CAN bytes -- evaluated by autopid_eval_calculated_channels().
    parameter_t *calculated;
    uint32_t calculated_count;
    char* custom_init;
    char* standard_init;
    char* specific_init;
    char* selected_car_model;
    bool pid_std_en;
    bool pid_custom_en;
    bool pid_specific_en;
    // When enabled, pause Automate/AutoPID when battery voltage is below configured sleep voltage.
    // Stored in auto_pid.json as: disable_on_sleep_voltage = "enable"/"disable".
    bool disable_on_sleep_voltage;
    // Alternative low-voltage mode: when battery voltage is below configured sleep voltage,
    // disable PID requests (polling) but keep CAN filter monitoring active.
    // Stored in auto_pid.json as: disable_on_sleep_voltage = "disable_pid_requests".
    bool disable_pid_requests_on_sleep_voltage;

    // Alternative voltage mode: disable PID requests (polling) when battery voltage is below
    // a configurable threshold (separate from Power Saving -> Sleep Voltage).
    // CAN filter monitoring remains active.
    // Stored in auto_pid.json as: disable_on_sleep_voltage = "automate_threshold".
    bool disable_pid_requests_on_automate_threshold;

    // Voltage threshold used when disable_pid_requests_on_automate_threshold is enabled.
    // Stored in auto_pid.json as: pid_polling_min_voltage = <number>.
    float pid_polling_min_voltage;
    // When enabled, validate that each PID request's response matches the request (service + PID bytes)
    // using the command string (cmd_str) provided by the ELM command runner.
    bool pid_validation_en;
    char* std_ecu_protocol;
    char* vehicle_model;
    uint32_t cycle;     //To be removed when std pid gets its own period
    time_t last_successful_pid_time;  // Timestamp in seconds since epoch of last successful PID response
    SemaphoreHandle_t mutex;
} autopid_config_t;

typedef struct 
{
    char *json_str;              // Pointer to a dynamically allocated string
    SemaphoreHandle_t mutex; // Mutex to protect access to the data
} autopid_data_t;

void autopid_parser(char *str, uint32_t len, QueueHandle_t *q, char* cmd_str);
void autopid_init(char* id);

// FAST_LOG support (Task #18): parse auto_pid.json into the module config WITHOUT starting
// the AutoPID task or any ELM polling. Creates autopid_config + its mutex and registers the
// wide-CSV column provider, so the native-TWAI fast datalogger (components/fast_log) can
// read can_filters[]/pids[] and reuse autopid_lock()/the column provider. Returns the parsed
// config (NULL on failure). Idempotent: returns the existing config if already loaded.
// Unlike autopid_init() it does NOT require pid_count>0 (broadcast-only configs are valid).
autopid_config_t* autopid_load_config_only(void);
char *autopid_data_read(void);
bool autopid_get_ecu_status(void);
char* autopid_get_config(void);
esp_err_t autopid_find_standard_pid(uint8_t protocol, char *available_pids, uint32_t available_pids_size) ;

// Protocol tracking helpers used by AutoPID parsing and related modules.
esp_err_t autopid_set_protocol_number(int32_t protocol_value);
esp_err_t autopid_get_protocol_number(int32_t *protocol_value);

char *autopid_get_value_by_name(char* name);
void autopid_app_reset_timer(void);

// Shared lock for ELM327 access.
// The AutoPID task uses this mutex to serialize access to the ELM327 interface.
// Other modules (e.g. AutoPID HTTP test endpoint) should take this lock to
// prevent interleaved commands/responses.
bool autopid_lock(uint32_t timeout_ms);
void autopid_unlock(void);

// Calculated channels (Task #17): evaluate every configured calculated channel from the latest
// decoded source-channel values and record each as a wide-CSV column (source "CALC"). Called once
// per decode sweep by the native-TWAI loggers (poll_log/fast_log). Takes autopid_lock() internally
// (the caller must NOT already hold it); does no bus/SD/flash I/O; a no-op when no calculated
// channels are configured.
void autopid_eval_calculated_channels(void);
#endif
