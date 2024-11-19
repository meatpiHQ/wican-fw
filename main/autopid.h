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

#define BUFFER_SIZE 512
#define QUEUE_SIZE 10

typedef enum
{
    INIT_ELM327 = 0,
    CONNECT_NOTIFY,
    DISCONNECT_NOTIFY,
    READ_PID
} autopid_state_t;

typedef struct {
    uint8_t data[BUFFER_SIZE];
    uint32_t length;
} response_t;

typedef enum
{
    MQTT_TOPIC = 0,
    MQTT_WALLBOX
} send_to_type_t;

typedef struct {
    char *name;              // Name
    char *pid_init;          // PID init string
    char *pid_command;       // PID command string
    char *expression;        // Expression string
    char *destination;       // Example: file name or mqtt topic
    int64_t timer;              // Timer for managing periodic actions
    uint32_t period;            // Period in ms frequency of data collection or action
    uint8_t expression_type;    // Expression type evaluates data from sensors etc
    uint8_t type;               // Log type, could be MQTT or file-based
}__attribute__((aligned(1),packed)) pid_req_t ;

typedef enum
{
    SENSOR = 0,
    BINARY_SENSOR = 1,
} sensor_type_t;

typedef struct
{
    char *name;
    char *expression;
    char *unit;
    char *class;
    sensor_type_t sensor_type;
} parameter_data_t;

typedef struct
{
    char *pid;
    parameter_data_t *parameters;  
    int parameter_count;
    char *pid_init;
} pid_data_t;

typedef struct
{
    char *car_model;
    char *init;
    pid_data_t *pids;              
    int pid_count;
    int64_t cycle_timer;  
    char* selected_car_model;
    char* destination;
    char* grouping;
    uint32_t cycle;
    uint8_t car_specific_en;
    uint8_t ha_discovery_en;
} car_model_data_t;

typedef struct 
{
    char *data;              // Pointer to a dynamically allocated string
    SemaphoreHandle_t mutex; // Mutex to protect access to the data
} autopid_data_t;

void autopid_parser(char* str, uint32_t len, QueueHandle_t *q);
void autopid_init(char* id, char *config_str);
char *autopid_data_read(void);
bool autopid_get_ecu_status(void);
car_model_data_t *autopid_get_config(void);
#endif
