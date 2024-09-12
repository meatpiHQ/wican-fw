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
#include "autopid.h"

#define TAG __func__

#define RANDOM_MIN          5
#define RANDOM_MAX          50
#define ECU_INIT_CMD        "0100\r"
#define TEMP_BUFFER_LENGTH 32

static char auto_pid_buf[BUFFER_SIZE];
static autopid_state_t  autopid_state = CONNECT_CHECK;
static QueueHandle_t autopidQueue;
static pid_req_t *pid_req;
static size_t num_of_pids = 0;
static char* initialisation = NULL;     
static car_model_data_t car;
static char* device_id;


void autopid_pub_discovery(void)
{
    char *discovery_str = NULL;
    char *discovery_topic = NULL;
    char *availability_topic = NULL;

    for (int i = 0; i < car.pid_count; i++)
    {
        for (int j = 0; j < car.pids[i].parameter_count; j++)
        {
            // Check if the class is NULL or "none"
            if (car.pids[i].parameters[j].class == NULL || strcasecmp(car.pids[i].parameters[j].class, "none") == 0)
            {
                // Format discovery message without device_class
                if (asprintf(&discovery_str, 
                             "{"
                             "\"name\": \"%s\","
                             "\"state_topic\": \"%s\","
                             "\"unit_of_measurement\": \"%s\","
                             "\"value_template\": \"{{ value_json.%s }}\","
                             "\"unique_id\": \"%s_%s\","
                             "\"availability_topic\": \"wican/%s/%s/availability\","
                             "\"payload_available\": \"online\","
                             "\"payload_not_available\": \"offline\""
                             "}",
                             car.pids[i].parameters[j].name,
                             car.destination,
                             car.pids[i].parameters[j].unit,
                             car.pids[i].parameters[j].name,
                             device_id,
                             car.pids[i].parameters[j].name,
                             device_id,
                             car.pids[i].parameters[j].name) == -1)
                {
                    // Handle error
                    ESP_LOGE(TAG, "Error: Failed to allocate memory for discovery_str\n");
                    return;
                }
            }
            else
            {
                // Format discovery message with device_class
                if (asprintf(&discovery_str, 
                             "{"
                             "\"name\": \"%s\","
                             "\"state_topic\": \"%s\","
                             "\"unit_of_measurement\": \"%s\","
                             "\"value_template\": \"{{ value_json.%s }}\","
                             "\"device_class\": \"%s\","
                             "\"unique_id\": \"%s_%s\","
                             "\"availability_topic\": \"wican/%s/%s/availability\","
                             "\"payload_available\": \"online\","
                             "\"payload_not_available\": \"offline\""
                             "}",
                             car.pids[i].parameters[j].name,
                             car.destination,
                             car.pids[i].parameters[j].unit,
                             car.pids[i].parameters[j].name,
                             car.pids[i].parameters[j].class,
                             device_id,
                             car.pids[i].parameters[j].name,
                             device_id,
                             car.pids[i].parameters[j].name) == -1)
                {
                    // Handle error
                    ESP_LOGE(TAG, "Error: Failed to allocate memory for discovery_str\n");
                    return;
                }
            }

            // Format discovery topic
            if (asprintf(&discovery_topic, "homeassistant/sensor/%s/%s/config",
                         device_id, car.pids[i].parameters[j].name) == -1)
            {
                // Handle error
                ESP_LOGE(TAG, "Error: Failed to allocate memory for discovery_topic\n");
                free(discovery_str);
                return;
            }

            // Format availability topic
            if (asprintf(&availability_topic, "wican/%s/%s/availability",
                         device_id, car.pids[i].parameters[j].name) == -1)
            {
                // Handle error
                ESP_LOGE(TAG, "Error: Failed to allocate memory for availability_topic\n");
                free(discovery_str);
                free(discovery_topic);
                return;
            }

            // Publish discovery message
            mqtt_publish(discovery_topic, discovery_str, 0, 1, 1);

            // Publish availability message
            mqtt_publish(availability_topic, "online", 0, 1, 1);

            // Clean up allocated memory
            free(discovery_str);
            free(discovery_topic);
            free(availability_topic);

            // Delay to avoid overwhelming the broker
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }
}




void parse_elm327_response(char *buffer, unsigned char *data, uint32_t *data_length)
{
    int k = 0;
    char *frame;
    char *data_start;

    // Split the frames by '\r' or '\r\n'
    frame = strtok(buffer, "\r\n");
    while (frame != NULL)
    {
        if (frame[strlen(frame) - 1] == '>')
        {
            frame[strlen(frame) - 1] = '\0';  // Remove the '>' from the last frame
        }

        // Find the first space, then take everything after it
        data_start = strchr(frame, ' ');
        if (data_start != NULL)
        {
            data_start++;  // Move past the space

            // Parse and store the data
            while (*data_start != '\0')
            {
                if (*data_start == ' ')  // Skip spaces
                {
                    data_start++;
                    continue;
                }
                char hex_byte[3] = {data_start[0], data_start[1], '\0'};
                data[k++] = (unsigned char) strtol(hex_byte, NULL, 16);
                data_start += 2;  // Move to the next hex pair
            }
        }

        frame = strtok(NULL, "\r\n");
    }

    *data_length = k;
}

static void append_to_buffer(char *buffer, const char *new_data) 
{
    if (strlen(buffer) + strlen(new_data) < BUFFER_SIZE) 
    {
        strcat(buffer, new_data);
    }
    else
    {
        ESP_LOGE(TAG, "Failed add data to buffer");
    }
}

void autopid_parser(char *str, uint32_t len, QueueHandle_t *q)
{
    static response_t response;
    if (str != NULL && strlen(str) != 0)
    {
        ESP_LOGI(__func__, "%s", str);

        append_to_buffer(auto_pid_buf, str);

        if (strchr(str, '>') != NULL) 
        {
            if(strstr(str, "NO DATA") == NULL && strstr(str, "ERROR") == NULL)
            {
                // Parse the accumulated buffer
                parse_elm327_response(auto_pid_buf,  response.data, &response.length);
                if (xQueueSend(autopidQueue, &response, pdMS_TO_TICKS(1000)) != pdPASS)
                {
                    ESP_LOGE(TAG, "Failed to send to queue");
                }
            }
            else
            {
                ESP_LOGE(__func__, "Error response: %s", auto_pid_buf);
            }
            // Clear the buffer after parsing
            auto_pid_buf[0] = '\0';
        }
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
        if ((strstr(str_send, "ath0") == NULL && strstr(str_send, "ATH0") == NULL && strstr(str_send, "at h0") == NULL && strstr(str_send, "AT H0") == NULL) &&
            (strstr(str_send, "ats0") == NULL && strstr(str_send, "ATS0") == NULL && strstr(str_send, "at s0") == NULL && strstr(str_send, "AT s0") == NULL) &&
            (strstr(str_send, "ate1") == NULL && strstr(str_send, "ATE1") == NULL && strstr(str_send, "at e1") == NULL && strstr(str_send, "AT E1") == NULL))
        {
            elm327_process_cmd((uint8_t *)str_send, cmd_len, &tx_msg, &autopidQueue);
        }
        
        cmd_start = cmd_end + 1; // Move to the start of the next command
        vTaskDelay(pdMS_TO_TICKS(delay_ms));
    }
}

static void autopid_task(void *pvParameters)
{
    static char default_init[] = "ati\rate0\rath1\ratl0\rats1\ratsp6\ratst96\r";
    static response_t response;
    twai_message_t tx_msg;
    static char* error_rsp = NULL;
    static char* error_topic = NULL;
    
    autopidQueue = xQueueCreate(QUEUE_SIZE, sizeof(response_t));
    if (autopidQueue == NULL)
    {
        ESP_LOGE(TAG, "Failed to create queue");
        vTaskDelete(NULL);
        return;
    }

    if(car.destination != NULL && asprintf(&error_topic, "%s/error", car.destination) == -1)
    {
        error_topic = car.destination;
    }

    vTaskDelay(pdMS_TO_TICKS(1000));
    send_commands(default_init, 50);

    while ((xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS));
    
    car.cycle_timer = 0;
    ESP_LOGI(TAG, "num_of_pids: %d", num_of_pids);

    while(!mqtt_connected())
    {
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
    
    if(car.ha_discovery_en)
    {
        autopid_pub_discovery();
    }
    
    while (1)
    {
        if((num_of_pids > 0 || (car.pid_count > 0 && car.car_specific_en))  && mqtt_connected())
        {
            switch(autopid_state)
            {
                case CONNECT_CHECK:
                {
                    if(initialisation != NULL && num_of_pids > 0 && strlen(initialisation) > 0) 
                    {
                        send_commands(initialisation, 100);
                        while ((xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS));
                        send_commands(ECU_INIT_CMD, 1000);
                        if(((xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS)))
                        {
                            autopid_state = CONNECT_NOTIFY;
                            ESP_LOGI(TAG, "State change --> CONNECT_NOTIFY");
                        }
                        else
                        {
                            vTaskDelay(pdMS_TO_TICKS(3000));
                        }
                    }

                    if( (car.pid_count > 0 && car.car_specific_en) && car.init != NULL && strlen(car.init) > 0 )
                    {
                        send_commands(car.init, 100);
                        while ((xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS));
                        send_commands(ECU_INIT_CMD, 1000);
                        if(((xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS)))
                        {
                            autopid_state = CONNECT_NOTIFY;
                            ESP_LOGI(TAG, "State change --> CONNECT_NOTIFY");
                        }
                        else
                        {
                            vTaskDelay(pdMS_TO_TICKS(3000));
                        }
                    }
                    break;
                }
                case CONNECT_NOTIFY:
                {
                    cJSON *rsp_json = cJSON_CreateObject();
                    char *response_str = NULL;

                    if (rsp_json != NULL) 
                    {
                        cJSON_AddStringToObject(rsp_json, "ecu_status", "online");
                        response_str = cJSON_PrintUnformatted(rsp_json);
                    }

                    if (response_str != NULL && strlen(response_str) > 0) 
                    {
                        mqtt_publish(config_server_get_mqtt_status_topic(), response_str, 0, 0, 0);
                        free(response_str);
                    }

                    if (rsp_json != NULL)
                    {
                        cJSON_Delete(rsp_json);
                    }

                    vTaskDelay(pdMS_TO_TICKS(1000));
                    autopid_state = READ_PID;
                    ESP_LOGI(TAG, "State change --> READ_PID");
                    break;
                }
                case DISCONNECT_NOTIFY:
                {
                    cJSON *rsp_json = cJSON_CreateObject();
                    char *response_str = NULL;

                    if (rsp_json != NULL) 
                    {
                        cJSON_AddStringToObject(rsp_json, "ecu_status", "offline");
                        response_str = cJSON_PrintUnformatted(rsp_json);
                    }

                    if (response_str != NULL && strlen(response_str) > 0) 
                    {
                        mqtt_publish(config_server_get_mqtt_status_topic(), response_str, 0, 0, 0);
                        free(response_str);
                    }

                    if (rsp_json != NULL)
                    {
                        cJSON_Delete(rsp_json);
                    }

                    vTaskDelay(pdMS_TO_TICKS(1000));
                    autopid_state = CONNECT_CHECK;
                    ESP_LOGI(TAG, "State change --> CONNECT_CHECK");
                    break;
                }

                case READ_PID:
                {
                    uint8_t pid_no_response = 0;
                    if(num_of_pids > 0)
                    {
                        for(uint32_t i = 0; i < num_of_pids; i++)
                        {
                            if( esp_timer_get_time() > pid_req[i].timer )
                            {
                                pid_req[i].timer = esp_timer_get_time() + pid_req[i].period*1000;
                                pid_req[i].timer += RANDOM_MIN + (esp_random() % (RANDOM_MAX - RANDOM_MIN + 1));

                                elm327_process_cmd((uint8_t*)pid_req[i].pid_command , strlen(pid_req[i].pid_command), &tx_msg, &autopidQueue);
                                ESP_LOGI(TAG, "Sending command: %s", pid_req[i].pid_command);
                                if (xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS)
                                {
                                    double result;
                                    static char hex_rsponse[256];

                                    ESP_LOGI(TAG, "Received response for: %s", pid_req[i].pid_command);
                                    ESP_LOGI(TAG, "Response length: %lu", response.length);
                                    ESP_LOG_BUFFER_HEXDUMP(TAG, response.data, response.length, ESP_LOG_INFO);
                                    if(evaluate_expression((uint8_t*)pid_req[i].expression, response.data, 0, &result))
                                    {
                                        cJSON *rsp_json = cJSON_CreateObject();
                                        char *response_str = NULL;

                                        if (rsp_json != NULL) 
                                        {
                                            for (size_t j = 0; j < response.length; ++j) 
                                            {
                                                sprintf(hex_rsponse + (j * 2), "%02X", response.data[j]);
                                            }
                                            hex_rsponse[response.length * 2] = '\0'; 

                                            // Add the name and result to the JSON object
                                            cJSON_AddNumberToObject(rsp_json, pid_req[i].name, result);
                                            cJSON_AddStringToObject(rsp_json, "raw", hex_rsponse);

                                            // Convert the cJSON object to a string
                                            response_str = cJSON_PrintUnformatted(rsp_json);
                                        }

                                        if (response_str != NULL) 
                                        {
                                            ESP_LOGI(TAG, "Expression result, Name: %s: %lf", pid_req[i].name, result);
                                            if(pid_req[i].destination != NULL && strlen(pid_req[i].destination) != 0)
                                            {
                                                mqtt_publish(pid_req[i].destination, response_str, 0, 0, 0);
                                            }
                                            else
                                            {
                                                //if destination is empty send to default
                                                mqtt_publish(config_server_get_mqtt_rx_topic(), response_str, 0, 0, 0);
                                            }
                                            free(response_str);
                                        }

                                        if (rsp_json != NULL)
                                        {
                                            cJSON_Delete(rsp_json);
                                        }

                                        vTaskDelay(pdMS_TO_TICKS(10));
                                    }
                                    else
                                    {
                                        ESP_LOGE(TAG, "Failed Expression: %s", pid_req[i].expression);
                                        if(asprintf(&error_rsp, "{\"error\": \"Failed Expression: %s\"}", pid_req[i].expression) != -1)
                                        {
                                            mqtt_publish(error_topic, error_rsp, 0, 0, 0);
                                            vTaskDelay(pdMS_TO_TICKS(10));
                                            free(error_rsp);
                                        }
                                    }
                                }
                                else
                                {
                                    ESP_LOGE(TAG, "Timeout waiting for response for: %s", pid_req[i].pid_command);
                                    if(asprintf(&error_rsp, "{\"error\": \"Timeout, pid: %s\"}", pid_req[i].pid_command) != -1)
                                    {
                                        mqtt_publish(error_topic, error_rsp, 0, 0, 0);
                                        vTaskDelay(pdMS_TO_TICKS(10));
                                        free(error_rsp);
                                    }
                                    pid_no_response = 1;
                                }
                            }
                            vTaskDelay(pdMS_TO_TICKS(2));
                        }
                    }

                    if((car.car_specific_en) && ( car.pid_count > 0 ) && ( esp_timer_get_time() > car.cycle_timer ))
                    {
                        car.cycle_timer = esp_timer_get_time() + car.cycle*1000; //in ms

                        cJSON *rsp_json = cJSON_CreateObject();
                        char *response_str = NULL;

                        for(uint32_t i = 0; i < car.pid_count; i++)
                        {
                            if(car.pids[i].pid_init != NULL && strlen(car.pids[i].pid_init) > 0)
                            {
                                elm327_process_cmd((uint8_t*)car.pids[i].pid_init , strlen(car.pids[i].pid_init), &tx_msg, &autopidQueue);
                                ESP_LOGI(TAG, "Sending car.pids[%lu].pid_init: %s", i, car.pids[i].pid_init);
                            }
                            if(car.pids[i].pid != NULL && strlen(car.pids[i].pid) > 0)
                            {
                                elm327_process_cmd((uint8_t*)car.pids[i].pid , strlen(car.pids[i].pid), &tx_msg, &autopidQueue);
                                ESP_LOGI(TAG, "Sending car.pids[%lu].pid: %s", i, car.pids[i].pid);
                                if (xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS)
                                {
                                    double result;
                                    static char hex_rsponse[256];
                                    
                                    ESP_LOGI(TAG, "Received response for: %s", car.pids[i].pid);
                                    ESP_LOGI(TAG, "Response length: %lu", response.length);
                                    ESP_LOG_BUFFER_HEXDUMP(TAG, response.data, response.length, ESP_LOG_INFO);

                                    for (uint32_t j = 0; j < car.pids[i].parameter_count; j++)
                                    {
                                        if(car.pids[i].parameters[j].expression != NULL && strlen(car.pids[i].parameters[j].expression) > 0)
                                        {
                                            if(evaluate_expression((uint8_t*)car.pids[i].parameters[j].expression, response.data, 0, &result))
                                            {
                                                if (rsp_json != NULL) 
                                                {
                                                    for (size_t j = 0; j < response.length; ++j) 
                                                    {
                                                        sprintf(hex_rsponse + (j * 2), "%02X", response.data[j]);
                                                    }
                                                    hex_rsponse[response.length * 2] = '\0'; 

                                                    // Add the name and result to the JSON object
                                                    cJSON_AddNumberToObject(rsp_json, car.pids[i].parameters[j].name, result);
                                                    ESP_LOGI(TAG, "Expression result, Name: %s: %lf", car.pids[i].parameters[j].name, result);
                                                }
                                            }
                                            else
                                            {
                                                ESP_LOGE(TAG, "Failed Expression: %s", car.pids[i].parameters[j].expression);
                                                if(asprintf(&error_rsp, "{\"error\": \"Failed Expression: %s\"}", car.pids[i].parameters[j].expression) != -1)
                                                {
                                                    mqtt_publish(error_topic, error_rsp, 0, 0, 0);
                                                    vTaskDelay(pdMS_TO_TICKS(10));
                                                    free(error_rsp);
                                                }
                                            }
                                        }
                                        else
                                        {
                                            ESP_LOGE(TAG, "Failed Expression: %s", car.pids[i].parameters[j].expression);
                                            if(asprintf(&error_rsp, "{\"error\": \"Failed Expression: %s\"}", car.pids[i].parameters[j].expression) != -1)
                                            {
                                                mqtt_publish(error_topic, error_rsp, 0, 0, 0);
                                                vTaskDelay(pdMS_TO_TICKS(10));
                                                free(error_rsp);
                                            }
                                        }
                                    }
                                }
                                else
                                {
                                    ESP_LOGE(TAG, "Timeout waiting for response for: %s", car.pids[i].pid);
                                    if(asprintf(&error_rsp, "{\"error\": \"Timeout, pid: %s\"}", car.pids[i].pid) != -1)
                                    {
                                        mqtt_publish(error_topic, error_rsp, 0, 0, 0);
                                        vTaskDelay(pdMS_TO_TICKS(10));
                                        free(error_rsp);
                                    }
                                    pid_no_response = 1;
                                }
                            }
                            else
                            {
                                ESP_LOGE(TAG, "PID Error");
                                if(asprintf(&error_rsp, "{\"error\": \"PID Error\"}") != -1)
                                {
                                    mqtt_publish(error_topic, error_rsp, 0, 0, 0);
                                    vTaskDelay(pdMS_TO_TICKS(10));
                                    free(error_rsp);
                                }
                                pid_no_response = 1;
                            }
                        }

                        if (rsp_json != NULL) 
                        {
                            response_str = cJSON_PrintUnformatted(rsp_json);
                            if (response_str != NULL) 
                            {
                                mqtt_publish(car.destination, response_str, 0, 0, 0);
                                free(response_str);
                            }
                            cJSON_Delete(rsp_json);
                        }
                    }
                    vTaskDelay(pdMS_TO_TICKS(10));
                    if(pid_no_response)
                    {
                        autopid_state = DISCONNECT_NOTIFY;
                        ESP_LOGI(TAG, "State change --> DISCONNECT_NOTIFY");
                    }

                    break;
                }
            }
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        else
        {
            autopid_state = CONNECT_CHECK;
            vTaskDelay(pdMS_TO_TICKS(2000));
        }
    }

    // Unreachable code after infinite loop removed for clarity.
}


static void autopid_load_config(char *config_str)
{
    cJSON *config_str_json = cJSON_Parse(config_str);
    if (config_str_json == NULL) 
    {
        ESP_LOGE(TAG, "Failed to parse config string");
        return;
    }
    ESP_LOGI(TAG, "Successfully parsed json config string");
    cJSON *init = cJSON_GetObjectItem(config_str_json, "initialisation");
    if (cJSON_IsString(init) && (init->valuestring != NULL) && strlen(init->valuestring) > 0) 
    {
        size_t len = strlen(init->valuestring) + 1; // +1 for the null terminator
        initialisation = (char*)malloc(len);
        if (initialisation == NULL) 
        {
            ESP_LOGE(TAG, "Failed to allocate memory for initialisation string");
            cJSON_Delete(config_str_json);
            return;
        }
        strncpy(initialisation, init->valuestring, len);
        ESP_LOGI(TAG, "initialisation: %s", initialisation);
        // Replace ';' with carriage return
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
        initialisation = NULL;
    }

    cJSON *car_model = cJSON_GetObjectItem(config_str_json, "car_model");
    if (cJSON_IsString(car_model) && (car_model->valuestring != NULL) && strlen(car_model->valuestring) > 0) 
    {
        size_t len = strlen(car_model->valuestring) + 1; // +1 for the null terminator
        car.selected_car_model = (char*)malloc(len);
        if (car.selected_car_model == NULL) 
        {
            ESP_LOGE(TAG, "Failed to allocate memory for selected_car_model string");
            cJSON_Delete(config_str_json);
            return;
        }
        strncpy(car.selected_car_model, car_model->valuestring, len);
        ESP_LOGI(TAG, "car.selected_car_model: %s, %u", car.selected_car_model, len);
    }
    else
    {
        car.selected_car_model = NULL;
    }
    
    cJSON *destination = cJSON_GetObjectItem(config_str_json, "destination");
    if (cJSON_IsString(destination) && (destination->valuestring != NULL) && strlen(destination->valuestring)) 
    {
        size_t len = strlen(destination->valuestring) + 1; // +1 for the null terminator
        car.destination = (char*)malloc(len);
        if (car.destination == NULL) 
        {
            ESP_LOGE(TAG, "Failed to allocate memory for destination string");
            cJSON_Delete(config_str_json);
            return;
        }
        strncpy(car.destination, destination->valuestring, len);
        ESP_LOGI(TAG, "car.destination: %s", car.destination);
    }
    else
    {
        car.destination = config_server_get_mqtt_rx_topic();
    }

    cJSON *car_specific = cJSON_GetObjectItem(config_str_json, "car_specific");
    if (cJSON_IsString(car_specific) && (car_specific->valuestring != NULL)) 
    {
        car.car_specific_en = (strcmp(car_specific->valuestring, "enable") == 0) ? 1 : 0;
        ESP_LOGI(TAG, "car.car_specific_en: %u", car.car_specific_en);
    }
    else
    {
        car.car_specific_en = 0;
    }

    cJSON *ha_discovery = cJSON_GetObjectItem(config_str_json, "ha_discovery");
    if (cJSON_IsString(ha_discovery) && (ha_discovery->valuestring != NULL)) 
    {
        car.ha_discovery_en = (strcmp(ha_discovery->valuestring, "enable") == 0) ? 1 : 0;
        ESP_LOGI(TAG, "car.ha_discovery_en: %u", car.ha_discovery_en);
    }
    else
    {
        car.ha_discovery_en = 0;
    }

    cJSON *grouping = cJSON_GetObjectItem(config_str_json, "grouping");
    if (cJSON_IsString(grouping) && (grouping->valuestring != NULL) && strlen(grouping->valuestring) > 0) 
    {
        size_t len = strlen(grouping->valuestring) + 1; // +1 for the null terminator
        car.grouping = (char*)malloc(len);
        if (car.grouping == NULL) 
        {
            ESP_LOGE(TAG, "Failed to allocate memory for grouping string");
            cJSON_Delete(config_str_json);
            return;
        }
        strncpy(car.grouping, grouping->valuestring, len);
        ESP_LOGI(TAG, "car.grouping: %s", car.grouping);
    }
    else
    {
        car.grouping = NULL;
    }

    cJSON *cycle = cJSON_GetObjectItem(config_str_json, "cycle");
    if (cJSON_IsString(cycle) && (cycle->valuestring != NULL) && strlen(cycle->valuestring) > 0) 
    {
        int cycle_val = atoi(cycle->valuestring);
        if (cycle_val >= 0) 
        {
            car.cycle = (uint32_t)cycle_val;
            ESP_LOGI(TAG, "car.cycle: %lu", car.cycle);
        }
        else
        {
            car.cycle = 5000;
            ESP_LOGW(TAG, "Invalid cycle value, defaulting to 5000");
        }
        if(car.cycle < 1000)
        {
            car.cycle = 1000;
        }
    }
    else
    {
        car.cycle = 5000;
    }

    cJSON *pids = cJSON_GetObjectItem(config_str_json, "pids");
    if (!cJSON_IsArray(pids)) 
    {
        ESP_LOGE(TAG, "Invalid pids array in config");
        cJSON_Delete(config_str_json);
        return;
    }

    num_of_pids = cJSON_GetArraySize(pids);
    if (num_of_pids == 0)
    {
        ESP_LOGW(TAG, "No PIDs found in config");
        cJSON_Delete(config_str_json);
        return;
    }

    pid_req = (pid_req_t *)malloc(sizeof(pid_req_t) * num_of_pids);
    if (pid_req == NULL) 
    {
        ESP_LOGE(TAG, "Failed to allocate memory for pid_req");
        cJSON_Delete(config_str_json);
        return;
    }

    for (int i = 0; i < num_of_pids; ++i) 
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

        if (cJSON_IsString(name) && name->valuestring && strlen(name->valuestring) > 0)
        {
            pid_req[i].name = strdup(name->valuestring);
        }
        else
        {
            pid_req[i].name = NULL;  // Assign an empty string if not available
        }

        if (cJSON_IsString(pid_init) && pid_init->valuestring && strlen(pid_init->valuestring))
        {
            pid_req[i].pid_init = strdup(pid_init->valuestring);
        }
        else
        {
            pid_req[i].pid_init = NULL;  // Assign an empty string if not available
        }

        if (cJSON_IsString(pid_command) && pid_command->valuestring && strlen(pid_command->valuestring))
        {
            pid_req[i].pid_command = malloc(strlen(pid_command->valuestring) + 2); // +2 for "\r\0"
            if (pid_req[i].pid_command != NULL)
            {
                strcpy(pid_req[i].pid_command, pid_command->valuestring);
                strcat(pid_req[i].pid_command, "\r");
            }
        }
        else
        {
            pid_req[i].pid_command = NULL;  // Assign an empty string if not available
        }

        if (cJSON_IsString(expression) && expression->valuestring && strlen(expression->valuestring) > 0)
        {
            pid_req[i].expression = strdup(expression->valuestring);
        }
        else
        {
            pid_req[i].expression = NULL;  // Assign an empty string if not available
        }

        if (cJSON_IsString(send_to) && send_to->valuestring && strlen(send_to->valuestring) > 0)
        {
            pid_req[i].destination = strdup(send_to->valuestring);
        }
        else
        {
            pid_req[i].destination = NULL;  // Assign an empty string if not available
        }

        if (cJSON_IsString(period) && period->valuestring && strlen(period->valuestring) > 0)
        {
            pid_req[i].period = (uint32_t)strtoul(period->valuestring, NULL, 10);
        }
        else
        {
            pid_req[i].period = 5000;
        }

        if (cJSON_IsString(type) && type->valuestring && strlen(type->valuestring) > 0)
        {
            pid_req[i].type = (strcmp(type->valuestring, "MQTT_Topic") == 0) ? 0 : 1;  // 0 for MQTT, 1 for file
        }

        pid_req[i].timer = 0;
    }

    cJSON_Delete(config_str_json);
}

static void autopid_load_car_specific(char* car_mod)
{
    const char *filepath = "/spiffs/car_data.json";
    uint8_t car_found_flag = 0;
    
    FILE *fd = fopen(filepath, "r");
    if (fd == NULL)
    {
        ESP_LOGE("JSON Parser", "File does not exist: %s", filepath);
        return;
    }

    // Get the file size
    fseek(fd, 0, SEEK_END);
    long filesize = ftell(fd);
    fseek(fd, 0, SEEK_SET);

    // Allocate memory to read the file
    char *json_string = (char *)malloc(filesize + 1);
    if (json_string == NULL)
    {
        ESP_LOGE("JSON Parser", "Failed to allocate memory");
        fclose(fd);
        return;
    }

    // Read the file into the buffer
    fread(json_string, 1, filesize, fd);
    json_string[filesize] = '\0'; // Null-terminate the string

    fclose(fd);

    // Parse the JSON
    cJSON *json = cJSON_Parse(json_string);
    if (json == NULL)
    {
        ESP_LOGE("JSON Parser", "Error parsing JSON");
        free(json_string);
        return;
    }

    // Find the "cars" array
    cJSON *cars = cJSON_GetObjectItemCaseSensitive(json, "cars");
    if (!cJSON_IsArray(cars))
    {
        ESP_LOGE("JSON Parser", "No 'cars' array found in JSON");
        free(json_string);
        cJSON_Delete(json);
        return;
    }

    // Iterate through the cars array to find the matching car model
    cJSON *car_item;
    cJSON_ArrayForEach(car_item, cars)
    {
        cJSON *car_model = cJSON_GetObjectItemCaseSensitive(car_item, "car_model");
        if (cJSON_IsString(car_model) && (car_model->valuestring != NULL) && strcmp(car_model->valuestring, car_mod) == 0)
        {
            // Found the matching car model, populate the global car structure
            car.car_model = strdup(car_model->valuestring);

            cJSON *init = cJSON_GetObjectItemCaseSensitive(car_item, "init");
            if (cJSON_IsString(init) && (init->valuestring != NULL))
            {
                car.init = strdup(init->valuestring);
                // Replace ';' with carriage return
                for (size_t i = 0; i < strlen(car.init); ++i) 
                {
                    if (car.init[i] == ';') 
                    {
                        car.init[i] = '\r';
                    }
                }
            }
            else
            {
                car.init = strdup("");  // Assign an empty string if not available
            }

            // Parse PIDs
            cJSON *pids = cJSON_GetObjectItemCaseSensitive(car_item, "pids");
            if (cJSON_IsArray(pids))
            {
                car.pid_count = cJSON_GetArraySize(pids);
                car.pids = malloc(car.pid_count * sizeof(pid_data_t));

                int i = 0;
                cJSON *pid_item;
                cJSON_ArrayForEach(pid_item, pids)
                {
                    // Parse pid
                    cJSON *pid = cJSON_GetObjectItemCaseSensitive(pid_item, "pid");
                    if (cJSON_IsString(pid) && (pid->valuestring != NULL))
                    {
                        car.pids[i].pid = strdup(pid->valuestring);
                        strcat(car.pids[i].pid, "\r");
                    }
                    else
                    {
                        car.pids[i].pid = strdup("");  // Assign an empty string if not available
                    }

                    // Parse pid_init
                    cJSON *pid_init = cJSON_GetObjectItemCaseSensitive(pid_item, "pid_init");
                    if (cJSON_IsString(pid_init) && (pid_init->valuestring != NULL))
                    {
                        car.pids[i].pid_init = malloc(strlen(pid_init->valuestring) + 2); // +2 for "\r\0" strdup(param_init->valuestring);
                        if(car.pids[i].pid_init != NULL)
                        {
                            strcpy(car.pids[i].pid_init, pid_init->valuestring);
                            strcat(car.pids[i].pid_init, "\r");
                        }
                    }
                    else
                    {
                        car.pids[i].pid_init = strdup("");  // Assign an empty string if not available
                    }

                    // Parse parameters
                    cJSON *parameters = cJSON_GetObjectItemCaseSensitive(pid_item, "parameters");
                    if (cJSON_IsArray(parameters))
                    {
                        car.pids[i].parameter_count = cJSON_GetArraySize(parameters);
                        car.pids[i].parameters = malloc(car.pids[i].parameter_count * sizeof(parameter_data_t));

                        int j = 0;
                        cJSON *parameter_item;
                        cJSON_ArrayForEach(parameter_item, parameters)
                        {
                            // Parse name
                            cJSON *name = cJSON_GetObjectItemCaseSensitive(parameter_item, "name");
                            if (cJSON_IsString(name) && (name->valuestring != NULL))
                            {
                                car.pids[i].parameters[j].name = strdup(name->valuestring);
                            }
                            else
                            {
                                car.pids[i].parameters[j].name = strdup("");  // Assign an empty string if not available
                            }

                            // Parse expression
                            cJSON *expression = cJSON_GetObjectItemCaseSensitive(parameter_item, "expression");
                            if (cJSON_IsString(expression) && (expression->valuestring != NULL))
                            {
                                car.pids[i].parameters[j].expression = strdup(expression->valuestring);
                            }
                            else
                            {
                                car.pids[i].parameters[j].expression = strdup("");  // Assign an empty string if not available
                            }

                            // Parse unit
                            cJSON *unit = cJSON_GetObjectItemCaseSensitive(parameter_item, "unit");
                            if (cJSON_IsString(unit) && (unit->valuestring != NULL))
                            {
                                car.pids[i].parameters[j].unit = strdup(unit->valuestring);
                            }
                            else
                            {
                                car.pids[i].parameters[j].unit = strdup("");  // Assign an empty string if not available
                            }
                            
                            // Parse class
                            cJSON *class = cJSON_GetObjectItemCaseSensitive(parameter_item, "class");
                            if (cJSON_IsString(class) && (class->valuestring != NULL))
                            {
                                car.pids[i].parameters[j].class = strdup(class->valuestring);
                            }
                            else
                            {
                                car.pids[i].parameters[j].class = strdup("");  // Assign an empty string if not available
                            }
                            j++;
                        }
                    }
                    i++;
                }
            }
            car_found_flag = 1;
            // Once we find and populate the matching car model, we can break out of the loop
            break;
        }
    }

    // Free JSON string buffer and cJSON object
    free(json_string);
    cJSON_Delete(json);

    if(car_found_flag == 1)
    {
        ESP_LOGI(TAG, "Car Model: %s", car.car_model);
        ESP_LOGI(TAG, "Init Command: %s", car.init);
        for (int i = 0; i < car.pid_count; i++)
        {
            ESP_LOGI(TAG, "PID: %s", car.pids[i].pid);
            for (int j = 0; j < car.pids[i].parameter_count; j++)
            {
                ESP_LOGI(TAG, "  Parameter Name: %s", car.pids[i].parameters[j].name);
                ESP_LOGI(TAG, "  Expression: %s", car.pids[i].parameters[j].expression);
                ESP_LOGI(TAG, "  Unit: %s", car.pids[i].parameters[j].unit);
            }
        }
    }
    else
    {
        car.selected_car_model = NULL;
        car.car_specific_en = 0;
    }
}

void autopid_init(char* id, char *config_str)
{
    device_id = id;
    autopid_load_config(config_str);
    // char *desired_car_model = "Toyota Camry";
    if(car.car_specific_en && car.selected_car_model != NULL)
    {
        autopid_load_car_specific(car.selected_car_model);
    }
    else
    {
        car.pid_count = 0;
    }
    

    
    xTaskCreate(autopid_task, "autopid_task", 1024 * 5, (void *)AF_INET, 5, NULL);
}
