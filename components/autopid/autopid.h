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

#define AUTOPID_BUFFER_SIZE (1024*4)
#define QUEUE_SIZE 10

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

typedef enum
{
    DEST_DEFAULT,
    DEST_MQTT_TOPIC,
    DEST_MQTT_WALLBOX,
    DEST_HTTP,
    DEST_HTTPS,
    DEST_ABRP_API,
    DEST_MAX
}destination_type_t;

// Group destination entry (multi-destination support)
typedef struct {
    destination_type_t type;    // Destination type
    char *destination;          // URL / topic / etc.
    uint32_t cycle;             // Publish cycle (ms) or 0 for event based
    char *api_token;            // Optional API/Bearer token (HTTP/HTTPS/ABRP)
    char *cert_set;             // Certificate set name for HTTPS ("default" for built-in)
    bool enabled;               // Whether this destination is active
    int64_t publish_timer;   // Internal: next publish expiration timer (0 = not scheduled / immediate)
    uint32_t consec_failures;   // Internal: consecutive failure counter
    uint32_t backoff_ms;        // Internal: current backoff delay extension (ms)
} group_destination_t;

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
}pid_data_t;

typedef struct 
{
    pid_data_t *pids;
    uint32_t pid_count;
    char* custom_init;
    char* standard_init;
    char* specific_init;
    char* selected_car_model;
    char* grouping;
    destination_type_t group_destination_type;
    char* group_destination;    //"destination"
    // Multi-destination support
    group_destination_t *destinations;   // Array of destinations (nullable)
    uint32_t destinations_count;         // Number of entries in destinations
    bool pid_std_en;
    bool pid_custom_en;
    bool pid_specific_en;
    char* std_ecu_protocol;
    char* vehicle_model;
    bool ha_discovery_en;
    uint32_t cycle;     //To be removed when std pid gets its own period
    SemaphoreHandle_t mutex;
}all_pids_t;

typedef struct 
{
    char *json_str;              // Pointer to a dynamically allocated string
    SemaphoreHandle_t mutex; // Mutex to protect access to the data
} autopid_data_t;

void autopid_parser(char *str, uint32_t len, QueueHandle_t *q, char* cmd_str);
void autopid_init(char* id, bool enable_logging, uint32_t logging_period);
char *autopid_data_read(void);
bool autopid_get_ecu_status(void);
char* autopid_get_config(void);
esp_err_t autopid_find_standard_pid(uint8_t protocol, char *available_pids, uint32_t available_pids_size) ;
char *autopid_get_value_by_name(char* name);
void autopid_publish_all_destinations(void); // New multi-destination publisher
#endif
