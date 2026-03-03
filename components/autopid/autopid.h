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
#include "esp_err.h"
#include "wc_timer.h" 

#define AUTOPID_BUFFER_SIZE (1024*4)
#define QUEUE_SIZE 10

// Safety caps for user-configurable destinations (prevents stack/heap abuse via config JSON)
#define AUTOPID_MAX_DESTINATIONS 6
#define AUTOPID_MAX_DEST_QUERY_PARAMS 16

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

typedef enum
{
    PID_STD = 0,
    PID_CUSTOM = 1,
    PID_SPECIFIC = 2,
    PID_MAX
} pid_type_t;

typedef enum
{
    DEST_DEFAULT,
    DEST_MQTT_TOPIC,
    DEST_MQTT_WALLBOX,
    DEST_HTTP,
    DEST_HTTPS,
    DEST_ABRP_API,
    DEST_MAX
} destination_type_t;

typedef struct 
{
    char *name;
    char *expression;
    char *unit;
    char *class;
    uint32_t period; 
    float min;
    float max;
    sensor_type_t sensor_type;
    char* destination;
    destination_type_t destination_type;
    wc_timer_t timer; 
    float value;
    bool failed;
    bool enabled;

  bool onchange;        // update PID alway or when changes
    // [NEW] Update on Change Support
    char *update_mode;       // "always" or "onchange"
    float last_sent_value;   // Tracks previous value
} parameter_t;

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
    wc_timer_t period_timer;
} pid_data_t;

// [NEW] Detection Methods
typedef enum {
    DETECTION_ALWAYS,        
    DETECTION_VOLTAGE,       
    DETECTION_ADAPTIVE_RPM,
    DETECTION_MQTT
} detection_method_t;

// [NEW] Group Structure (References Master List)
typedef struct {
    char *name;
    bool enabled;
    char *init;                 
    detection_method_t detection_method; 
    uint32_t period;            
    wc_timer_t timer;           
    pid_data_t **pids;          // Array of POINTERS to the master list
    uint32_t pid_count;         
    uint32_t consecutive_errors;
    bool mqtt_active_flag;
    wc_timer_t mqtt_active_timer;   // <-- [NEW] Watchdog timer for MQTT activation
} pid_group_t;

// HTTP(S) auth types supported by https_client_mgr_request_with_auth
typedef enum {
    DEST_AUTH_NONE = 0,
    DEST_AUTH_BEARER,
    DEST_AUTH_API_KEY_HEADER,
    DEST_AUTH_API_KEY_QUERY,
    DEST_AUTH_BASIC
} dest_auth_type_t;

typedef struct {
    dest_auth_type_t type;
    char *bearer;
    char *api_key;
    char *api_key_header_name;
    char *api_key_query_name;
    char *basic_username;
    char *basic_password;
    struct {
        char *key;
        char *value;
    } *extra_query; 
    size_t extra_query_count; 
} dest_auth_config_t;

typedef struct {
    char *key;
    char *value;
} dest_query_kv_t;

typedef struct {
    destination_type_t type;
    char *destination;
    uint32_t cycle;
    char *api_token;
    char *cert_set;
    bool enabled;
    dest_auth_config_t auth;
    dest_query_kv_t *query_params;
    size_t query_params_count;
    wc_timer_t publish_timer;
    uint32_t consec_failures;
    uint32_t backoff_ms;
    uint32_t success_count;
    uint32_t fail_count;
    bool settings_sent; 
} group_destination_t;

typedef struct {
    uint32_t frame_id;
    bool is_extended;
    parameter_t *parameters;
    uint32_t parameters_count;
    bool is_vehicle_specific;
} can_filter_t;

typedef struct 
{
    // [NEW] Feature Flags & Groups
    bool use_groups;       
    pid_group_t *groups;
    uint32_t group_count;

    // [MASTER LIST] All PIDs live here (Legacy + Grouped)
    pid_data_t *pids; 
    uint32_t pid_count;

    // Global settings
    char* custom_init;
    char* standard_init;
    char* specific_init;
    char* vehicle_model;
    char* grouping;
    
    group_destination_t *destinations;
    size_t destinations_count;
    
    destination_type_t group_destination_type;
    char* group_destination;

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
    bool ha_discovery_en;
    uint32_t cycle;
    time_t last_successful_pid_time;
    
    can_filter_t *can_filters;
    uint32_t can_filters_count;
    char* webhook_data_mode;

    SemaphoreHandle_t mutex;
} autopid_config_t;

typedef struct 
{
    char *json_str;
    SemaphoreHandle_t mutex;
} autopid_data_t;

void autopid_parser(char *str, uint32_t len, QueueHandle_t *q, char* cmd_str);
void autopid_init(char* id, bool enable_logging, uint32_t logging_period);
char *autopid_data_read(void);
bool autopid_get_ecu_status(void);
char* autopid_get_config(void);
char *autopid_get_destinations_stats_json(void);
esp_err_t autopid_find_standard_pid(uint8_t protocol, char *available_pids, uint32_t available_pids_size);
esp_err_t autopid_set_protocol_number(int32_t protocol_value);
esp_err_t autopid_get_protocol_number(int32_t *protocol_value);
char *autopid_get_value_by_name(char* name);
void autopid_publish_all_destinations(void);
void autopid_app_reset_timer(void);
void autopid_set_group_mqtt_state(const char* group_name, bool active_state);

void parse_elm327_response(char *buffer, response_t *response);

// Shared lock for ELM327 access
bool autopid_lock(uint32_t timeout_ms);
void autopid_unlock(void);

#endif
