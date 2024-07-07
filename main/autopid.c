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

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include <string.h>
#include "driver/twai.h"
#include "esp_timer.h"
#include "esp_system.h" 
#include "lwip/sockets.h"
#include "elm327.h"
#include "autopid.h"
#include "expression_parser.h"
#include "mqtt.h"
#include "cJSON.h"
#include "config_server.h"

#define TAG __func__

#define RANDOM_MIN  5
#define RANDOM_MAX  50

static uint32_t auto_pid_header;
static uint8_t auto_pid_buf[BUFFER_SIZE];
static uint8_t auto_pid_data_len = 0;
static int buf_index = 0;
static autopid_state_t state = WAITING_FOR_START;
static QueueHandle_t autopidQueue;
static pid_req_t *pid_req;
static size_t num_of_pids = 0;
static char* initialisation = NULL;       // Initialisation string

								 															
void append_data(const char *token)
{
    if (isxdigit((int)token[0]) && isxdigit((int)token[1]) && strlen(token) == 2)
    {
        if (buf_index < BUFFER_SIZE - 3) // Ensure room for 2 hex chars and a space/null
        {
            memcpy(&auto_pid_buf[buf_index], token, 2);
            buf_index += 2;
            auto_pid_buf[buf_index++] = ' '; // Space for readability
        }
    }
}

void strip_line_endings(char *buffer)
{
    char *pos;
    if ((pos = strchr(buffer, '\r')) != NULL)
        *pos = '\0';
    if ((pos = strchr(buffer, '\n')) != NULL)
        *pos = '\0';
}

void autopid_parse_rsp(char *str)
{
    response_t response;
    
    if (strchr(str, '>') == NULL)
    {
        strip_line_endings(str);
    }

    switch (state)
    {
        case WAITING_FOR_START:
        {
            if (strchr(str, '>') != NULL)
            {
                ESP_LOGI(__func__, "Found end");
                ESP_LOG_BUFFER_HEXDUMP(TAG, str, strlen(str), ESP_LOG_INFO);
                
                strncpy((char *)response.data, str, BUFFER_SIZE);
                response.length = strlen(str);
                
                if (xQueueSend(autopidQueue, &response, pdMS_TO_TICKS(1000)) != pdPASS)
                {
                    ESP_LOGE(TAG, "Failed to send to queue");
                }
            }
            int8_t count = sscanf(str, "%lX %X %X %X %X %X %X %X %X", &auto_pid_header, (unsigned int *)&auto_pid_buf[0], (unsigned int *)&auto_pid_buf[1], (unsigned int *)&auto_pid_buf[2],
                                (unsigned int *)&auto_pid_buf[3], (unsigned int *)&auto_pid_buf[4], (unsigned int *)&auto_pid_buf[5],
                                (unsigned int *)&auto_pid_buf[6], (unsigned int *)&auto_pid_buf[7]);
            if (count >= 2 && count <= 9)
            {
                if (auto_pid_buf[0] == 0x10) // Indicates multi-frame response
                {
                    buf_index = count - 1;
                    auto_pid_data_len = auto_pid_buf[1];
                }
                else
                {
                    buf_index = count - 1;
                }
                state = READING_LINES;
            }
            else
            {
                buf_index = 0;
            }
            break;
        }
        case READING_LINES:
        {
            if (strchr(str, '>') != NULL)
            {
                ESP_LOGI(__func__, "Found response end, response: %s", str);
                state = WAITING_FOR_START;
                if (buf_index != 0)
                {
                    ESP_LOG_BUFFER_HEXDUMP(TAG, auto_pid_buf, buf_index, ESP_LOG_INFO);
                    
                    memcpy(response.data, auto_pid_buf, buf_index);
                    response.length = buf_index;
                    
                    if (xQueueSend(autopidQueue, &response, pdMS_TO_TICKS(1000)) != pdPASS)
                    {
                        ESP_LOGE(TAG, "Failed to send to queue");
                    }
                }
                break;
            }
            else
            {
                int8_t count = sscanf(str, "%lX %X %X %X %X %X %X %X %X", &auto_pid_header, (unsigned int *)&auto_pid_buf[buf_index], (unsigned int *)&auto_pid_buf[buf_index + 1], (unsigned int *)&auto_pid_buf[buf_index + 2],
                                    (unsigned int *)&auto_pid_buf[buf_index + 3], (unsigned int *)&auto_pid_buf[buf_index + 4], (unsigned int *)&auto_pid_buf[buf_index + 5],
                                    (unsigned int *)&auto_pid_buf[buf_index + 6], (unsigned int *)&auto_pid_buf[buf_index + 7]);
                if (count == 9)
                {
                    buf_index += 8;
                }
                else
                {
                    state = WAITING_FOR_START;
                }
                break;
            }
        }
    }
}

void autopid_mqtt_pub(char *str, uint32_t len, QueueHandle_t *q)
{
    if (strlen(str) != 0)
    {
        ESP_LOGI(__func__, "%s", str);
        // ESP_LOG_BUFFER_HEXDUMP(TAG, str, strlen(str), ESP_LOG_INFO);
        autopid_parse_rsp(str);
    }
}

static void send_commands(char *commands, uint32_t delay_ms)
{
    char *cmd_start = commands;
    char *cmd_end;
    twai_message_t tx_msg;
    
    while ((cmd_end = strchr(cmd_start, '\r')) != NULL) 
    {
        size_t cmd_len = cmd_end - cmd_start + 1; // +1 to include '\r'
        char str_send[cmd_len + 1]; // +1 for null terminator
        strncpy(str_send, cmd_start, cmd_len);
        str_send[cmd_len] = '\0'; // Null-terminate the command string

        elm327_process_cmd((uint8_t *)str_send, cmd_len, &tx_msg, &autopidQueue);

        cmd_start = cmd_end + 1; // Move to the start of the next command
        vTaskDelay(pdMS_TO_TICKS(delay_ms));
    }
}

static void autopid_task(void *pvParameters)
{
    static char default_init[] = "ati\rath1\rats1\ratsp6\r";
    static response_t response;
    twai_message_t tx_msg;

    autopidQueue = xQueueCreate(QUEUE_SIZE, sizeof(response_t));
    if (autopidQueue == NULL)
    {
        ESP_LOGE(TAG, "Failed to create queue");
        vTaskDelete(NULL);
        return;
    }

    vTaskDelay(pdMS_TO_TICKS(1000));
    send_commands(default_init, 50);
    send_commands(initialisation, 50);
    vTaskDelay(pdMS_TO_TICKS(1000));

    while ((xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS));
    
    for(uint32_t i = 0; i < num_of_pids; i++)
    {
        strcat(pid_req[i].pid_command, "\r");
    }

    while (1)
    {
        if(num_of_pids && mqtt_connected())
        {
            for(uint32_t i = 0; i < num_of_pids; i++)
            {
                if( esp_timer_get_time() > pid_req[i].timer )
                {
                    pid_req[i].timer = esp_timer_get_time() + pid_req[i].period*1000;
                    pid_req[i].timer = RANDOM_MIN + (esp_random() % (RANDOM_MAX - RANDOM_MIN + 1));

                    elm327_process_cmd((uint8_t*)pid_req[i].pid_command , strlen(pid_req[i].pid_command), &tx_msg, &autopidQueue);
                    ESP_LOGI(TAG, "Sending command: %s", pid_req[i].pid_command);
                    if (xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS)
                    {
                        double result;

                        ESP_LOGI(TAG, "Received response for: %s", pid_req[i].pid_command);
                        ESP_LOGI(TAG, "Response length: %d", response.length);
                        ESP_LOG_BUFFER_HEXDUMP(TAG, response.data, response.length, ESP_LOG_INFO);
                        if(evaluate_expression((uint8_t*)pid_req[i].expression, response.data, 0, &result))
                        {
                            cJSON *rsp_json = cJSON_CreateObject();
                            if (rsp_json == NULL) 
                            {
                                ESP_LOGI(TAG, "Failed to create cJSON object");
                                break;
                            }

                            // Add the name and result to the JSON object
                            cJSON_AddNumberToObject(rsp_json, pid_req[i].name, result);
                            
                            // Convert the cJSON object to a string
                            char *response_str = cJSON_PrintUnformatted(rsp_json);
                            if (response_str == NULL) 
                            {
                                ESP_LOGI(TAG, "Failed to print cJSON object");
                                cJSON_Delete(rsp_json); // Clean up cJSON object
                                break;
                            }

                            ESP_LOGI(TAG, "Expression result, Name: %s: %lf", pid_req[i].name, result);
                            if(strlen(pid_req[i].destination) != 0)
                            {
                                mqtt_publish(pid_req[i].destination, response_str, 0, 0, 0);
                            }
                            else
                            {
                                //if destination is empty send to default
                                mqtt_publish(config_server_get_mqtt_rx_topic(), response_str, 0, 0, 0);
                            }
                            
                            // Free the JSON string and cJSON object
                            free(response_str);
                            cJSON_Delete(rsp_json);
                            vTaskDelay(pdMS_TO_TICKS(10));
                        }
                        else
                        {
                            ESP_LOGE(TAG, "Failed Expression: %s", pid_req[i].expression);
                        }
                    }
                    else
                    {
                        ESP_LOGE(TAG, "Timeout waiting for response for: %s", pid_req[i].pid_command);
                    }
                }
            }
        }
        else
        {
            vTaskDelay(pdMS_TO_TICKS(2000));
        }
        // vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

static void autopid_load(char *config_str)
{
    cJSON *json = cJSON_Parse(config_str);
    if (json == NULL) 
    {
        ESP_LOGE(TAG, "Failed to parse config string");
        return;
    }

    cJSON *init = cJSON_GetObjectItem(json, "initialisation");
    if (cJSON_IsString(init) && (init->valuestring != NULL)) 
    {
        size_t len = strlen(init->valuestring) + 1; // +1 for the null terminator
        initialisation = (char*)malloc(len);
        if (initialisation == NULL) 
        {
            ESP_LOGE(TAG, "Failed to allocate memory for initialisation string");
            cJSON_Delete(json);
            return;
        }
        strncpy(initialisation, init->valuestring, len);
        //replace ';' with carriage return
        for (size_t i = 0; i < len; ++i) 
        {
            if (initialisation[i] == ';') 
            {
                initialisation[i] = '\r';
            }
        }
    } 
    else 
    {
        ESP_LOGE(TAG, "Invalid initialisation string in config");
        cJSON_Delete(json);
        return;
    }

    cJSON *pids = cJSON_GetObjectItem(json, "pids");
    if (!cJSON_IsArray(pids)) 
    {
        ESP_LOGE(TAG, "Invalid pids array in config");
        cJSON_Delete(json);
        return;
    }

    num_of_pids = cJSON_GetArraySize(pids);
    if(num_of_pids == 0)
    {
        return;
    }

    pid_req = (pid_req_t *)malloc(sizeof(pid_req_t) * num_of_pids);
    if (pid_req == NULL) 
    {
        ESP_LOGE(TAG, "Failed to allocate memory for pid_req");
        cJSON_Delete(json);
        return;
    }

    for (size_t i = 0; i < num_of_pids; ++i) 
    {
        cJSON *pid_item = cJSON_GetArrayItem(pids, i);
        if (!cJSON_IsObject(pid_item)) 
        {
            ESP_LOGE(TAG, "Invalid PID item in config");
            continue;
        }

        cJSON *name = cJSON_GetObjectItem(pid_item, "Name");
        cJSON *pid_init = cJSON_GetObjectItem(pid_item, "Init");
        cJSON *pid_command = cJSON_GetObjectItem(pid_item, "PID");
        cJSON *expression = cJSON_GetObjectItem(pid_item, "Expression");
        cJSON *period = cJSON_GetObjectItem(pid_item, "Period");
        cJSON *send_to = cJSON_GetObjectItem(pid_item, "Send_to");
        cJSON *type = cJSON_GetObjectItem(pid_item, "Type");

        strncpy(pid_req[i].name, name->valuestring, sizeof(pid_req[i].name) - 1);
        strncpy(pid_req[i].pid_init, pid_init->valuestring, sizeof(pid_req[i].pid_init) - 1);
        strncpy(pid_req[i].pid_command, pid_command->valuestring, sizeof(pid_req[i].pid_command) - 1);
        strncpy(pid_req[i].expression, expression->valuestring, sizeof(pid_req[i].expression) - 1);
        strncpy(pid_req[i].destination, send_to->valuestring, sizeof(pid_req[i].destination) - 1);
        pid_req[i].period = (uint32_t)strtoul(period->valuestring, NULL, 10);
        pid_req[i].type = (strcmp(type->valuestring, "MQTT_Topic") == 0) ? 0 : 1;  // Example: 0 for MQTT, 1 for file
        pid_req[i].timer = 0; 
    }

    cJSON_Delete(json);
}

void autopid_init(char *config_str)
{
    autopid_load(config_str);
    
    xTaskCreate(autopid_task, "autopid_task", 1024 * 5, (void *)AF_INET, 5, NULL);
}
