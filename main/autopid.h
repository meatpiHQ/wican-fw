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

#define BUFFER_SIZE 256
#define QUEUE_SIZE 10

typedef enum
{
    WAITING_FOR_START = 0,
    READING_LINES
} autopid_state_t;

typedef struct {
    uint8_t data[BUFFER_SIZE];
    size_t length;
} response_t;

typedef struct {
    char name[32];              // Name
    char pid_init[32];          // PID init string
    char pid_command[10];       // PID command string
    char expression[32];        // Expression string
    char destination[64];       // Example: file name or mqtt topic
    int64_t timer;              // Timer for managing periodic actions
    uint32_t period;            // Period in ms frequency of data collection or action
    uint8_t expression_type;    // Expression type evaluates data from sensors etc
    uint8_t type;               // Log type, could be MQTT or file-based
}__attribute__((aligned(1),packed)) pid_req_t ;

void autopid_mqtt_pub(char* str, uint32_t len, QueueHandle_t *q);
void autopid_init(char *config_str);
#endif