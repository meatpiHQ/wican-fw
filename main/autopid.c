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
#include "freertos/event_groups.h"
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
#include <math.h>
#include "obd2_standard_pids.h"
#include "wc_timer.h"
#include <float.h>
#include "hw_config.h"

#define TAG __func__

#define TEMP_BUFFER_LENGTH  32
#define ECU_CONNECTED_BIT			BIT0

static char auto_pid_buf[BUFFER_SIZE];
static QueueHandle_t autopidQueue;
static char* device_id;
static EventGroupHandle_t xautopid_event_group = NULL;
static all_pids_t* all_pids = NULL;
static response_t elm327_response;

//Helper functions
// Custom printer function to format numbers with 2 decimal places
char* formatNumberPrecision(double num) 
{
    static char buf[32];
    snprintf(buf, sizeof(buf), "%.2f", num);
    
    // Remove trailing zeros after decimal point
    size_t len = strlen(buf);
    if (strchr(buf, '.')) 
    {
        while (len > 0 && buf[len-1] == '0') 
        {
            buf[--len] = '\0';
        }
        if (len > 0 && buf[len-1] == '.') 
        {
            buf[--len] = '\0';
        }
    }
    return buf;
}

// Recursively limit decimal precision in the JSON structure
void limitJsonDecimalPrecision(cJSON* item) 
{
    if (!item) return;
    
    // If current item is a number, modify its value
    if (cJSON_IsNumber(item))
    {
        char* formatted = formatNumberPrecision(item->valuedouble);
        item->valuedouble = atof(formatted);
        item->valuestring = NULL;  // Force cJSON to use valuedouble
    }
    
    // Process all children
    cJSON* child = item->child;
    while (child)
    {
        limitJsonDecimalPrecision(child);
        child = child->next;
    }
}



const std_pid_t* get_pid_from_string(const char* pid_string)
{
    char pid_hex[3];
    uint8_t pid_value;
    
    // Extract first 2 characters (hex PID)
    strncpy(pid_hex, pid_string, 2);
    pid_hex[2] = '\0';
    
    // Convert hex string to integer
    pid_value = (uint8_t)strtol(pid_hex, NULL, 16);
    
    // Get the base PID info
    const std_pid_t* pid_info = get_pid(pid_value);
    if (!pid_info)
    {
        return NULL;
    }
    
    // If there's a parameter name specified after the dash
    if (strchr(pid_string, '-'))
    {
        const char* param_name = strchr(pid_string, '-') + 1;
        
        // For all PIDs, verify the parameter exists
        bool param_found = false;
        for (int i = 0; i < pid_info->num_params; i++)
        {
            if (strcmp(pid_info->params[i].name, param_name) == 0)
            {
                param_found = true;
                break;
            }
        }
        if (!param_found)
        {
            return NULL;
        }
    }
    
    return pid_info;
}

static esp_err_t extract_signal_value(const uint8_t* data, 
                                        uint8_t data_length, 
                                        const std_parameter_t* param,
                                        float* result) 
{
    // Validate input parameters
    if (!data || !param || !result) {
        return ESP_ERR_INVALID_ARG;
    }

    // Calculate which bytes we need
    uint8_t start_byte = param->bit_start / 8;
    uint8_t bytes_needed = (param->bit_length + 7) / 8;

    // Validate data length
    if (start_byte + bytes_needed > data_length) {
        return ESP_ERR_INVALID_SIZE;
    }

    // Extract raw value (Motorola format)
    uint32_t raw_value = 0;
    for (uint8_t i = 0; i < bytes_needed; i++) {
        raw_value = (raw_value << 8) | data[start_byte + i];
    }

    // Apply bit mask for the signal length
    uint32_t mask = (1ULL << param->bit_length) - 1;
    raw_value &= mask;

    // Calculate and limit physical value
    float physical_value = (float)raw_value * param->scale + param->offset;
    
    if (physical_value < param->min) {
        physical_value = param->min;
    }
    if (physical_value > param->max) {
        physical_value = param->max;
    }

    *result = physical_value;
    return ESP_OK;
}

static void merge_response_frames(uint8_t* data, uint32_t length, uint8_t* merged_frame) {
    // Initialize merged frame with first 7 bytes
    for(int i = 0; i < 7; i++) {
        merged_frame[i] = data[i];
    }
    
    // Process subsequent frames
    for(int frame = 7; frame < length; frame += 7) {
        // Perform bitwise OR for each byte position
        for(int byte = 0; byte < 7 && (frame + byte) < length; byte++) {
            merged_frame[byte] |= data[frame + byte];
        }
    }
}

esp_err_t autopid_find_standard_pid(uint8_t protocol, char *available_pids, uint32_t available_pids_size) 
{
    twai_message_t frame;
    response_t *response = NULL;
    uint32_t supported_pids = 0;
    cJSON *root = cJSON_CreateObject();
    cJSON *pid_array = cJSON_CreateArray();
    uint8_t current_protocol = elm327_get_current_protocol()-'0';
    uint32_t current_txheader = elm327_get_identifier();
    uint32_t current_rxheader = elm327_get_rx_address();
    char restore_cmd[64];
    static const char *supported_protocols[] = {"ATSP6\rATSH7DF\rATCRA\r",
                                                "ATSP7\rATSH18DB33F1\rATCRA\r",
                                                "ATSP8\rATSH7DF\rATCRA\r",
                                                "ATSP9\rATSH18DB33F1\rATCRA\r",
                                                };

    response = (response_t *)malloc(sizeof(response_t)); 
    
    if (response == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for response");
        return ESP_ERR_NO_MEM;
    }

    if(current_rxheader == 0)
    {
        snprintf(restore_cmd, sizeof(restore_cmd), "ATSP%u\rATSH%03lX\r",
                current_protocol, 
                current_txheader);
    }
    else if (current_txheader <= 0x7FF) 
    {
        snprintf(restore_cmd, sizeof(restore_cmd), "ATSP%u\rATSH%03lX\rATCRA%03lX\r",
                current_protocol, 
                current_txheader,
                current_rxheader);
    }
    else
    {
        snprintf(restore_cmd, sizeof(restore_cmd), "ATSP%u\rATSH%08lX\rATCRA%08lX\r",
                current_protocol,
                current_txheader, 
                current_rxheader);
    }

    elm327_lock();

    if(protocol >= 6 && protocol <= 9) 
    {
        ESP_LOGI(TAG, "Setting protocol %d", protocol);

        static const char *elm327_config = "ate0\rath1\ratl0\rats1\ratst96\r";
        elm327_process_cmd((uint8_t*)elm327_config, strlen(elm327_config), &frame, &autopidQueue);
        while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS);

        const char* protocol_cmds = supported_protocols[protocol-6];
        ESP_LOGI(TAG, "Sending protocol commands: %s", protocol_cmds);
        elm327_process_cmd((uint8_t*)protocol_cmds, strlen(protocol_cmds), &frame, &autopidQueue);
        while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS);
        ESP_LOGI(TAG, "Protocol %d set successfully", protocol);
    }
    else
    {
        ESP_LOGE(TAG, "Invalid protocol number: %d", protocol);
        elm327_unlock();
        free(response);
        return ESP_FAIL;
    }
    
    xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000));

    const char *pid_support_cmds[] = {
        "0100\r",  // PIDs 0x01-0x20
        "0120\r",  // PIDs 0x21-0x40
        "0140\r",  // PIDs 0x41-0x60
        "0160\r",  // PIDs 0x61-0x80
        "0180\r",  // PIDs 0x81-0xA0
        "01A0\r",  // PIDs 0xA1-0xC0
    };

    ESP_LOGI(TAG, "Starting PID support command processing");
    for (int i = 0; i < sizeof(pid_support_cmds)/sizeof(pid_support_cmds[0]); i++) {
        ESP_LOGI(TAG, "Processing PID support command: %s", pid_support_cmds[i]);
        if (elm327_process_cmd((uint8_t*)pid_support_cmds[i], strlen(pid_support_cmds[i]), &frame, &autopidQueue) != 0) {
            ESP_LOGW(TAG, "Failed to process PID support command: %s", pid_support_cmds[i]);
            continue;
        }

    if (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS) {
        ESP_LOGI(TAG, "Raw response length: %lu", response->length);
        ESP_LOG_BUFFER_HEX(TAG, response->data, response->length);


        // Skip mode byte (0x41) and PID byte
        if (response->length >= 7) {
            uint8_t merged_frame[7] = {0};
            merge_response_frames(response->data, response->length, merged_frame);
            
            // Extract bitmap from merged frame
            supported_pids = (merged_frame[3] << 24) | 
                            (merged_frame[4] << 16) | 
                            (merged_frame[5] << 8) | 
                            merged_frame[6];
            
            ESP_LOGI(TAG, "Merged frame bitmap: 0x%08lx", supported_pids);

                for (int bit = 0; bit < 32; bit++) {
                    if (supported_pids & (1 << (31 - bit))) {
                        uint8_t pid = (i * 32) + bit + 1;
                        const std_pid_t* pid_info = get_pid(pid);
                        
                        if (pid_info) {
                            char pid_str[64];
                            
                            // If the PID has multiple parameters
                            if (pid_info->num_params > 1 && pid_info->params) {
                                ESP_LOGI(TAG, "Processing multi-parameter PID: %02X", pid);
                                // Add each parameter as a separate entry
                                for (int p = 0; p < pid_info->num_params; p++) {
                                    if (pid_info->params[p].name) {
                                        snprintf(pid_str, sizeof(pid_str), "%02X-%s", 
                                                pid, pid_info->params[p].name);
                                        ESP_LOGI(TAG, "PID %02X parameter %d supported: %s", 
                                                pid, p + 1, pid_str);
                                        cJSON_AddItemToArray(pid_array, cJSON_CreateString(pid_str));
                                    } else {
                                        ESP_LOGW(TAG, "PID %02X parameter %d has NULL name", pid, p + 1);
                                    }
                                }
                            } else if (pid_info->params && pid_info->params[0].name) {
                                // Single parameter PID
                                snprintf(pid_str, sizeof(pid_str), "%02X-%s", 
                                        pid, pid_info->params[0].name);
                                ESP_LOGI(TAG, "PID %02X supported: %s", pid, pid_str);
                                cJSON_AddItemToArray(pid_array, cJSON_CreateString(pid_str));
                            } else {
                                ESP_LOGW(TAG, "PID %02X has invalid or NULL parameters", pid);
                            }
                        }                        
                    }
                }
            } else {
                ESP_LOGW(TAG, "Response length too short: %lu", response->length);
            }
        } else {
            ESP_LOGW(TAG, "No response received for PID support command: %s", pid_support_cmds[i]);
        }
    }

    ESP_LOGI(TAG, "Adding PIDs to JSON object");
    cJSON_AddItemToObject(root, "std_pids", pid_array);

    // Convert to string and cleanup
    char *json_str = cJSON_PrintUnformatted(root);
    if (json_str) {
        ESP_LOGI(TAG, "JSON string created, length: %zu", strlen(json_str));
        if (strlen(json_str) < available_pids_size) {
            strncpy(available_pids, json_str, available_pids_size - 1);
            available_pids[available_pids_size - 1] = '\0'; // Ensure null-termination
            free(json_str);
            cJSON_Delete(root);

            ESP_LOGI(TAG, "Restoring protocol settings");
            elm327_process_cmd((uint8_t*)restore_cmd, strlen(restore_cmd), &frame, &autopidQueue);
            while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS);

            free(response);
            elm327_unlock();
            return ESP_OK;
        }
        ESP_LOGW(TAG, "JSON string too long for buffer");
        free(json_str);
    } else {
        ESP_LOGE(TAG, "Failed to create JSON string");
    }

    ESP_LOGI(TAG, "Restoring protocol settings");
    elm327_process_cmd((uint8_t*)restore_cmd, strlen(restore_cmd), &frame, &autopidQueue);
    while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS);
    
    cJSON_Delete(root);
    free(response);
    elm327_unlock();
    return ESP_FAIL;
}

// static void autopid_data_write(const char *new_data)
// {
//     if (autopid_data.mutex != NULL && xSemaphoreTake(autopid_data.mutex, portMAX_DELAY) == pdTRUE)
//     {
//         if (autopid_data.data != NULL)
//         {
//             free(autopid_data.data);
//             autopid_data.data = NULL;
//         }

//         autopid_data.data = (char *)malloc(strlen(new_data) + 1);
//         if (autopid_data.data == NULL)
//         {
//             ESP_LOGE("autopid_data_write", "Memory allocation failed");
//         }
//         else
//         {
//             strcpy(autopid_data.data, new_data);
//         }

//         xSemaphoreGive(autopid_data.mutex);
//     }
// }



// char *autopid_data_read(void)
// {
//     char *data_copy = NULL;

//     if(autopid_data.mutex != NULL)
//     {
//         if (xSemaphoreTake(autopid_data.mutex, portMAX_DELAY) == pdTRUE)
//         {
//             if (autopid_data.data != NULL)
//             {
//                 data_copy = (char *)malloc(strlen(autopid_data.data) + 1);
//                 if (data_copy != NULL)
//                 {
//                     strcpy(data_copy, autopid_data.data);
//                 }
//             }

//             xSemaphoreGive(autopid_data.mutex); 
//         }

//         return data_copy;
//     }
//     else
//     {
//         return NULL;
//     }
// }

char *autopid_data_read(void)
{
    static char *json_str = NULL;
    
    if (!all_pids || !all_pids->mutex) {
        ESP_LOGE(TAG, "Invalid all_pids or mutex");
        return NULL;
    }

    if (xSemaphoreTake(all_pids->mutex, portMAX_DELAY) == pdTRUE) {
        cJSON *root = cJSON_CreateObject();
        if (root) {
            for (uint32_t i = 0; i < all_pids->pid_count; i++) {
                pid_data2_t *curr_pid = &all_pids->pids[i];
                for (uint32_t j = 0; j < curr_pid->parameters_count; j++) {
                    parameter_t *param = &curr_pid->parameters[j];
                    if (param->name && param->value != FLT_MAX) {
                        if (param->sensor_type == BINARY_SENSOR) {
                            cJSON_AddStringToObject(root, param->name, param->value > 0 ? "on" : "off");
                        } else {
                            cJSON_AddNumberToObject(root, param->name, param->value);
                        }
                    }
                }
            }
            limitJsonDecimalPrecision(root);
            json_str = cJSON_PrintUnformatted(root);
            cJSON_Delete(root);
        }
        xSemaphoreGive(all_pids->mutex);
    }
    return json_str;
}

void autopid_data_publish(void) {
    if (!all_pids || !all_pids->mutex) {
        ESP_LOGE(TAG, "Invalid all_pids or mutex");
        return;
    }

    if (xSemaphoreTake(all_pids->mutex, portMAX_DELAY) == pdTRUE) {
        cJSON *root = cJSON_CreateObject();
        if (root) {
            for (uint32_t i = 0; i < all_pids->pid_count; i++) {
                pid_data2_t *curr_pid = &all_pids->pids[i];
                for (uint32_t j = 0; j < curr_pid->parameters_count; j++) {
                    parameter_t *param = &curr_pid->parameters[j];
                    if (param->name && param->value != FLT_MAX) {
                        if (param->sensor_type == BINARY_SENSOR) {
                            cJSON_AddStringToObject(root, param->name, param->value > 0 ? "on" : "off");
                        } else {
                            cJSON_AddNumberToObject(root, param->name, param->value);
                        }
                    }
                }
            }

            if (root->child) {
                limitJsonDecimalPrecision(root);
                char *json_str = cJSON_PrintUnformatted(root);
                if (json_str) {
                    if(all_pids->group_destination && strlen(all_pids->group_destination) > 0)
                    {
                        mqtt_publish(all_pids->group_destination, json_str, 0, 0, 1);
                        ESP_LOGI(TAG, "Published to %s", all_pids->group_destination);
                    }else{
                        mqtt_publish(config_server_get_mqtt_rx_topic(), json_str, 0, 0, 1);
                    }
                    free(json_str);
                }
            } else {
                ESP_LOGW(TAG, "No valid parameters found to publish");
            }

            cJSON_Delete(root);
        }
        xSemaphoreGive(all_pids->mutex);
    }
}

bool autopid_get_ecu_status(void)
{
	EventBits_t uxBits;
	if(xautopid_event_group != NULL)
	{
		uxBits = xEventGroupGetBits(xautopid_event_group);

		return (uxBits & ECU_CONNECTED_BIT);
	}
	else return false;
}

char* autopid_get_config(void)
{
    static char *response_str;

    // Check if all_pids and mutex are valid
    if (!all_pids || !all_pids->mutex) {
        ESP_LOGE(TAG, "Invalid all_pids or mutex");
        return NULL;
    }

    // Take mutex with timeout
    if (xSemaphoreTake(all_pids->mutex, portMAX_DELAY) != pdTRUE) {
        ESP_LOGE(TAG, "Failed to take mutex");
        return NULL;
    }
    
    cJSON *parameters_object = cJSON_CreateObject();
    if (!parameters_object)
    {
        ESP_LOGE(TAG, "Failed to create JSON object");
        return NULL;
    }

    // Iterate through all PIDs
    for (int i = 0; i < all_pids->pid_count; i++)
    {
        // For each PID, iterate through its parameters
        for (int j = 0; j < all_pids->pids[i].parameters_count; j++)
        {
            parameter_t *param = &all_pids->pids[i].parameters[j];
            
            if((all_pids->pids[i].pid_type == PID_STD && !all_pids->pid_std_en) ||
            (all_pids->pids[i].pid_type == PID_CUSTOM && !all_pids->pid_custom_en) ||
            (all_pids->pids[i].pid_type == PID_SPECIFIC && !all_pids->pid_specific_en))
            {
                continue;
            }
            // Skip if parameter name is NULL
            if (!param || !param->name) continue;
            
            cJSON *parameter_details = cJSON_CreateObject();
            if (!parameter_details)
            {
                ESP_LOGE(TAG, "Failed to create parameter JSON object");
                continue;
            }
            
            // Add class and unit if they exist
            if (param->class)
            {
                cJSON_AddStringToObject(parameter_details, "class", param->class);
            }
            if (param->unit)
            {
                cJSON_AddStringToObject(parameter_details, "unit", param->unit);
            }
            
            cJSON_AddItemToObject(parameters_object, param->name, parameter_details);
        }
    }

    // Convert to string and send response
    response_str = cJSON_PrintUnformatted(parameters_object);
    cJSON_Delete(parameters_object);
    xSemaphoreGive(all_pids->mutex);
    return response_str;
}

// void autopid_pub_discovery(void)
// {
//     char *discovery_str = NULL;
//     char *discovery_topic = NULL;
//     char *availability_topic = NULL;

//     for (int i = 0; i < car.pid_count; i++)
//     {
//         for (int j = 0; j < car.pids[i].parameter_count; j++)
//         {
//             // Check if the class is NULL or "none"
//             if (car.pids[i].parameters[j].class == NULL || strcasecmp(car.pids[i].parameters[j].class, "none") == 0)
//             {
//                 // Format discovery message without device_class
//                 if (asprintf(&discovery_str, 
//                              "{"
//                              "\"name\": \"%s\","
//                              "\"state_topic\": \"%s\","
//                              "\"unit_of_measurement\": \"%s\","
//                              "\"value_template\": \"{{ value_json.%s }}\","
//                              "\"unique_id\": \"%s_%s\","
//                              "\"availability_topic\": \"wican/%s/%s/availability\","
//                              "\"payload_available\": \"online\","
//                              "\"payload_not_available\": \"offline\""
//                              "}",
//                              car.pids[i].parameters[j].name,
//                              car.destination,
//                              car.pids[i].parameters[j].unit,
//                              car.pids[i].parameters[j].name,
//                              device_id,
//                              car.pids[i].parameters[j].name,
//                              device_id,
//                              car.pids[i].parameters[j].name) == -1)
//                 {
//                     // Handle error
//                     ESP_LOGE(TAG, "Error: Failed to allocate memory for discovery_str\n");
//                     return;
//                 }
//             }
//             else
//             {
//                 // Format discovery message with device_class
//                 if (asprintf(&discovery_str, 
//                              "{"
//                              "\"name\": \"%s\","
//                              "\"state_topic\": \"%s\","
//                              "\"unit_of_measurement\": \"%s\","
//                              "\"value_template\": \"{{ value_json.%s }}\","
//                              "\"device_class\": \"%s\","
//                              "\"unique_id\": \"%s_%s\","
//                              "\"availability_topic\": \"wican/%s/%s/availability\","
//                              "\"payload_available\": \"online\","
//                              "\"payload_not_available\": \"offline\""
//                              "}",
//                              car.pids[i].parameters[j].name,
//                              car.destination,
//                              car.pids[i].parameters[j].unit,
//                              car.pids[i].parameters[j].name,
//                              car.pids[i].parameters[j].class,
//                              device_id,
//                              car.pids[i].parameters[j].name,
//                              device_id,
//                              car.pids[i].parameters[j].name) == -1)
//                 {
//                     // Handle error
//                     ESP_LOGE(TAG, "Error: Failed to allocate memory for discovery_str\n");
//                     return;
//                 }
//             }

//             // Format discovery topic
//             if (asprintf(&discovery_topic, "homeassistant/%s/%s/%s/config",
//                          car.pids[i].parameters[j].sensor_type == BINARY_SENSOR ? "binary_sensor" : "sensor",
//                          device_id, car.pids[i].parameters[j].name) == -1)
//             {
//                 // Handle error
//                 ESP_LOGE(TAG, "Error: Failed to allocate memory for discovery_topic\n");
//                 free(discovery_str);
//                 return;
//             }

//             // Format availability topic
//             if (asprintf(&availability_topic, "wican/%s/%s/availability",
//                          device_id, car.pids[i].parameters[j].name) == -1)
//             {
//                 // Handle error
//                 ESP_LOGE(TAG, "Error: Failed to allocate memory for availability_topic\n");
//                 free(discovery_str);
//                 free(discovery_topic);
//                 return;
//             }

//             // Publish discovery message
//             mqtt_publish(discovery_topic, discovery_str, 0, 1, 1);

//             // Publish availability message
//             mqtt_publish(availability_topic, "online", 0, 1, 1);

//             // Clean up allocated memory
//             free(discovery_str);
//             free(discovery_topic);
//             free(availability_topic);

//             // Delay to avoid overwhelming the broker
//             vTaskDelay(pdMS_TO_TICKS(100));
//         }
//     }
// }

void parse_elm327_response(char *buffer, response_t *response) {
    ESP_LOGI(TAG, "Starting to parse ELM327 response. Input buffer: %s", buffer);
    
    int k = 0;
    int frame_count = 0;
    char *frame;
    char *data_start;
    uint32_t lowest_header = UINT32_MAX;  // Initialize to maximum value
    uint32_t highest_header = 0;          // Track highest header
    uint32_t first_header = 0;
    bool all_headers_same = true;
    uint8_t *lowest_header_data = NULL;   // Store the actual data pointer
    uint8_t lowest_header_length = 0;

    frame = strtok(buffer, "\r\n");
    ESP_LOGI(TAG, "First frame: %s", frame ? frame : "NULL");

    while (frame != NULL) {
        ESP_LOGD(TAG, "Processing frame %d: %s", frame_count + 1, frame);
        frame_count++;

        // Remove trailing '>' if present
        size_t len = strlen(frame);
        if (len > 0 && frame[len - 1] == '>') {
            frame[len - 1] = '\0';
            ESP_LOGV(TAG, "Removed trailing '>' from frame");
        }

        data_start = strchr(frame, ' ');
        if (data_start != NULL) {
            int header_length = data_start - frame;
            char header_str[9] = {0};
            strncpy(header_str, frame, header_length);
            uint32_t current_header = strtoul(header_str, NULL, 16);
            ESP_LOGD(TAG, "Frame %d header: 0x%lX (length: %d)", frame_count, current_header, header_length);
            
            // Track highest header
            if (current_header > highest_header) {
                ESP_LOGD(TAG, "New highest header found: 0x%lX (previous: 0x%lX)", current_header, highest_header);
                highest_header = current_header;
            }
            
            // Track first header and compare subsequent headers
            if (frame_count == 1) {
                first_header = current_header;
                ESP_LOGD(TAG, "First header set to: 0x%lX", first_header);
            } else if (current_header != first_header) {
                all_headers_same = false;
                ESP_LOGD(TAG, "Different header detected: 0x%lX != 0x%lX", current_header, first_header);
            }

            data_start++;

            // Handle different header formats
            switch (header_length) {
                case 2: 
                    data_start += 9;
                    ESP_LOGV(TAG, "2-byte header format: Adjusted data_start by 9");
                    break;
                case 3:
                case 8:
                    ESP_LOGV(TAG, "%d-byte header format: No adjustment needed", header_length);
                    break;
                default:
                    ESP_LOGW(TAG, "Unexpected header length: %d, skipping frame", header_length);
                    frame = strtok(NULL, "\r\n");
                    continue;
            }

            // Store start position for copying data
            char *current_data_start = data_start;
            int current_length = 0;

            // Count data bytes in current frame
            char *temp_data = data_start;
            while (*temp_data != '\0') {
                if (*temp_data == ' ') {
                    temp_data++;
                    continue;
                }
                if (strlen(temp_data) < 2) break;
                current_length++;
                temp_data += 2;
            }

            // If this is the lowest header so far, store its data
            if (current_header < lowest_header) {
                ESP_LOGD(TAG, "New lowest header found: 0x%lX (previous: 0x%lX)", current_header, lowest_header);
                lowest_header = current_header;
                lowest_header_length = current_length;
                
                // Allocate space and copy data for lowest header frame
                if (lowest_header_data == NULL) {
                    lowest_header_data = (uint8_t*)malloc(current_length);
                } else {
                    lowest_header_data = (uint8_t*)realloc(lowest_header_data, current_length);
                }
                
                // Parse and store the data bytes for this frame
                int idx = 0;
                while (*current_data_start != '\0') {
                    if (*current_data_start == ' ') {
                        current_data_start++;
                        continue;
                    }
                    if (strlen(current_data_start) < 2) break;
                    
                    char byte_str[3] = {current_data_start[0], current_data_start[1], 0};
                    lowest_header_data[idx++] = (unsigned char)strtol(byte_str, NULL, 16);
                    current_data_start += 2;
                }
                ESP_LOGD(TAG, "Stored %d bytes from lowest header frame", idx);
            }

            // Parse data bytes into main response buffer
            ESP_LOGV(TAG, "Starting data byte parsing at position: %s", data_start);
            while (*data_start != '\0') {
                if (*data_start == ' ') {
                    data_start++;
                    continue;
                }
                if (strlen(data_start) < 2) {
                    ESP_LOGW(TAG, "Incomplete byte at end of frame: %s", data_start);
                    break;
                }
                
                char byte_str[3] = {data_start[0], data_start[1], 0};
                response->data[k] = (unsigned char)strtol(byte_str, NULL, 16);
                ESP_LOGV(TAG, "Parsed byte %d: 0x%02X from %s", k, response->data[k], byte_str);
                k++;
                data_start += 2;
            }
        } else {
            ESP_LOGW(TAG, "No space delimiter found in frame: %s", frame);
        }
        frame = strtok(NULL, "\r\n");
    }

    response->length = k;
    
    // Set priority data based on frame count and header comparison
    if (frame_count <= 2 || all_headers_same) {
        response->priority_data = NULL;
        response->priority_data_len = 0;
        if (lowest_header_data != NULL) {
            free(lowest_header_data);
        }
        ESP_LOGI(TAG, "Null priority data set - frames: %d, all headers same: %d", 
                frame_count, all_headers_same);
    } else {
        response->priority_data = lowest_header_data;
        response->priority_data_len = lowest_header_length;
        ESP_LOGI(TAG, "Priority data set - length: %u, starting with byte: 0x%02X", 
                response->priority_data_len, 
                response->priority_data[0]);
    }
    
    ESP_LOGI(TAG, "Parsing complete. Headers - Lowest: 0x%lX, Highest: 0x%lX, Total frames: %d, Total bytes: %lu, Priority data length: %u",
            lowest_header, highest_header, frame_count, response->length, response->priority_data_len);
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
        ESP_LOGI(TAG, "%s", str);

        append_to_buffer(auto_pid_buf, str);

        if (strchr(str, '>') != NULL) 
        {
            if(strstr(str, "NO DATA") == NULL && strstr(str, "ERROR") == NULL)
            {
                // Parse the accumulated buffer
                parse_elm327_response(auto_pid_buf, &response);
                if (xQueueSend(autopidQueue, &response, pdMS_TO_TICKS(1000)) != pdPASS)
                {
                    ESP_LOGE(TAG, "Failed to send to queue");
                }
            }
            else
            {
                sprintf((char*)response.data, "error");
                response.length = strlen((char*)response.data);
                ESP_LOGE(TAG, "Error response: %s", auto_pid_buf);
                if (xQueueSend(autopidQueue, &response, pdMS_TO_TICKS(1000)) != pdPASS)
                {
                    ESP_LOGE(TAG, "Failed to send to queue");
                }
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
            while ((xQueueReceive(autopidQueue, &elm327_response, pdMS_TO_TICKS(10)) == pdPASS));
        }
        
        cmd_start = cmd_end + 1; // Move to the start of the next command
        vTaskDelay(pdMS_TO_TICKS(delay_ms));
    }
}


//////////////////


static bool all_parameters_failed(all_pids_t* all_pids) {
    if (!all_pids) return true;
    
    bool any_success = false;
    
    xSemaphoreTake(all_pids->mutex, portMAX_DELAY);
    for (uint32_t i = 0; i < all_pids->pid_count; i++) {
        pid_data2_t *curr_pid = &all_pids->pids[i];
        for (uint32_t p = 0; p < curr_pid->parameters_count; p++) {
            if (!curr_pid->parameters[p].failed) {
                any_success = true;
                break;
            }
        }
        if (any_success) break;
    }
    xSemaphoreGive(all_pids->mutex);
    
    return !any_success;
}

static void publish_parameter_mqtt(parameter_t *param) {
    if (!param) return;
    
    char *payload = NULL;
    
    switch(param->destination_type) {
        case DEST_MQTT_TOPIC:
            // JSON format
            cJSON *param_json = cJSON_CreateObject();
            if (param_json) {
                if (param->sensor_type == BINARY_SENSOR) {
                    cJSON_AddStringToObject(param_json, param->name, param->value > 0 ? "on" : "off");
                } else {
                    cJSON_AddNumberToObject(param_json, param->name, param->value);
                }
                limitJsonDecimalPrecision(param_json);
                payload = cJSON_PrintUnformatted(param_json);
                cJSON_Delete(param_json);
            }
            break;
            
        case DEST_MQTT_WALLBOX:
            // Simple value format
            asprintf(&payload, "%.2f", param->value);
            break;
        default:
            break;
    }

    if (payload) {
        // Publish to specified destination or default topic
        if (param->destination && strlen(param->destination) > 0) {
            mqtt_publish(param->destination, payload, 0, 0, 1);
            ESP_LOGI(TAG, "Published to %s", param->destination);
        } else {
            mqtt_publish(config_server_get_mqtt_rx_topic(), payload, 0, 0, 1);
        }
        free(payload);
    }
}


static void autopid_task(void *pvParameters)
{
    static char default_init[] = "ati\rate0\rath1\ratl0\rats1\ratsp6\ratst96\r";
    wc_timer_t ecu_check_timer;
    wc_timer_t group_cycle_timer;

    ESP_LOGI(TAG, "Autopid Task Started");
    
    vTaskDelay(pdMS_TO_TICKS(100));
    send_commands(default_init, 50);

    while(config_server_mqtt_en_config() == 1 && !mqtt_connected())
    {
        vTaskDelay(pdMS_TO_TICKS(2000));
    }

    // if(config_server_mqtt_en_config() == 1 && all_pids->ha_discovery_en)
    // {
    //     autopid_pub_discovery();
    // }
    if (!all_pids)
    {
        ESP_LOGE(TAG, "all_pids is NULL");
        return;
    } 

    // xSemaphoreTake(all_pids->mutex, portMAX_DELAY);
    // uint32_t total_params = 0;
    
    // for (uint32_t i = 0; i < all_pids->pid_count; i++) 
    // {
    //     total_params += all_pids->pids[i].parameters_count;
    // }

    // bool *pid_failed = calloc(total_params, sizeof(bool));
    // xSemaphoreGive(all_pids->mutex);
    


    ESP_LOGI(TAG, "Autopid Start loop");
    ESP_LOGI(TAG, "Total PIDs: %lu", all_pids->pid_count);

    while(1) 
    {
        static pid_type_t previous_pid_type = PID_MAX;

        elm327_lock();
        xSemaphoreTake(all_pids->mutex, portMAX_DELAY);
        
        // Loop through all PIDs
        for(uint32_t i = 0; i < all_pids->pid_count; i++) 
        {
            pid_data2_t *curr_pid = &all_pids->pids[i];
            // Skip if PID type not enabled
            if((curr_pid->pid_type == PID_STD && !all_pids->pid_std_en) ||
            (curr_pid->pid_type == PID_CUSTOM && !all_pids->pid_custom_en) ||
            (curr_pid->pid_type == PID_SPECIFIC && !all_pids->pid_specific_en))
            {
                continue;
            }
            // autopid_data_write
            if(curr_pid->pid_type != previous_pid_type) {
                // Send appropriate initialization based on new PID type
                switch(curr_pid->pid_type) {
                    case PID_CUSTOM:
                        if(all_pids->custom_init && strlen(all_pids->custom_init) > 0) {
                            ESP_LOGI(TAG, "Sending custom init: %s, length: %d", 
                                    all_pids->custom_init, strlen(all_pids->custom_init));
                            send_commands(all_pids->custom_init, 2);
                        }
                        break;
                        
                    case PID_STD:
                        if(all_pids->standard_init && strlen(all_pids->standard_init) > 0) {
                            ESP_LOGI(TAG, "Sending standard init: %s, length: %d", 
                                    all_pids->standard_init, strlen(all_pids->standard_init));
                            send_commands(all_pids->standard_init, 2);
                        }
                        break;
                        
                    case PID_SPECIFIC:
                        if(all_pids->specific_init && strlen(all_pids->specific_init) > 0) {
                            ESP_LOGI(TAG, "Sending specific init: %s, length: %d", 
                                    all_pids->specific_init, strlen(all_pids->specific_init));
                            send_commands(all_pids->specific_init, 2);
                        }
                        break;
                        
                    case PID_MAX:
                        break;
                }

                previous_pid_type = curr_pid->pid_type;
            }
            // Loop through parameters
            for(uint32_t p = 0; p < curr_pid->parameters_count; p++) 
            {
                parameter_t *param = &curr_pid->parameters[p];
                
                // Check parameter timer
                if(wc_timer_is_expired(&param->timer)) 
                {
                    ESP_LOGI(TAG, "Processing parameter: %s", param->name);
                    // Reset timer with parameter period
                    wc_timer_set(&param->timer, param->period);

                    if(curr_pid->cmd != NULL && strlen(curr_pid->cmd) > 0) 
                    {
                        twai_message_t tx_msg;

                        if(curr_pid->pid_type == PID_CUSTOM || curr_pid->pid_type == PID_SPECIFIC) 
                        {
                            if(curr_pid->init != NULL && strlen(curr_pid->init) > 0)
                            {
                                send_commands(curr_pid->init, 2);
                            }
                        }

                        ESP_LOGI(TAG, "Executing command: %s", curr_pid->cmd);
                        if(elm327_process_cmd((uint8_t*)curr_pid->cmd, 
                                            strlen(curr_pid->cmd), 
                                            &tx_msg, 
                                            &autopidQueue) == ESP_OK)
                        {
                            ESP_LOGI(TAG, "Command processed successfully");
                            
                            if(xQueueReceive(autopidQueue, &elm327_response, pdMS_TO_TICKS(1000)) == pdPASS)
                            {
                                ESP_LOGI(TAG, "Response received, length: %lu", elm327_response.length);
                                ESP_LOG_BUFFER_HEXDUMP(TAG, elm327_response.data, 1, ESP_LOG_INFO);
                                if(strstr((char*)elm327_response.data, "error") == NULL)
                                {
                                    double result;

                                    param->failed = false;

                                    ESP_LOGI(TAG, "Response received, length: %lu", elm327_response.length);
                                    xEventGroupSetBits(xautopid_event_group, ECU_CONNECTED_BIT);
                                    // Process response based on PID type
                                    if(curr_pid->pid_type == PID_CUSTOM || curr_pid->pid_type == PID_SPECIFIC) 
                                    {
                                        ESP_LOGI(TAG, "Processing custom/specific PID");
                                        if(param->expression && 
                                        evaluate_expression((uint8_t*)param->expression, 
                                                            elm327_response.data, 0, &result))
                                        {
                                            if (param->min != FLT_MAX && result < param->min) {
                                                ESP_LOGW(TAG, "Parameter %s value %.2f below min %.2f - ignoring", 
                                                        param->name, result, param->min);
                                            } else if (param->max != FLT_MAX && result > param->max) {
                                                ESP_LOGW(TAG, "Parameter %s value %.2f above max %.2f - ignoring", 
                                                        param->name, result, param->max);
                                            } else {
                                                result = round(result * 100.0) / 100.0;
                                                ESP_LOGI(TAG, "Parameter %s result: %.2f", 
                                                        param->name, result);
                                                param->value = result;
                                                publish_parameter_mqtt(param);
                                            }
                                        }
                                    }
                                    else if(curr_pid->pid_type == PID_STD) 
                                    {
                                        ESP_LOGI(TAG, "Processing standard PID");
                                        if(curr_pid->pid_type == PID_STD) 
                                        {
                                            const std_pid_t* pid_info = get_pid_from_string(param->name);
                                            if(pid_info)
                                            {
                                                ESP_LOGI(TAG, "Found PID info for: %s", param->name);
                                                // Find matching parameter in pid_info
                                                for(int p = 0; p < pid_info->num_params; p++)
                                                {
                                                    // Match parameter name after the dash
                                                    const char* param_name = strchr(param->name, '-');
                                                    if(param_name && strcmp(param_name + 1, pid_info->params[p].name) == 0)
                                                    {
                                                        esp_err_t err = ESP_FAIL;

                                                        ESP_LOGI(TAG, "Processing parameter: %s", pid_info->params[p].name);
                                                        if(elm327_response.priority_data != NULL && elm327_response.priority_data != 0)
                                                        {
                                                            err = extract_signal_value(
                                                                elm327_response.priority_data,           // Your CAN response data buffer
                                                                elm327_response.priority_data_len,         // Length of your CAN response data
                                                                &pid_info->params[p],    // Parameter definition from pid_info
                                                                &param->value            // Where to store the result
                                                            );
                                                        }
                                                        else
                                                        {
                                                            err = extract_signal_value(
                                                                elm327_response.data,           // Your CAN response data buffer
                                                                elm327_response.length,         // Length of your CAN response data
                                                                &pid_info->params[p],    // Parameter definition from pid_info
                                                                &param->value            // Where to store the result
                                                            );
                                                        }
 
                                                        if (err != ESP_OK) {
                                                            ESP_LOGE(TAG, "Failed to extract signal: %s", esp_err_to_name(err));
                                                            break;
                                                        }
                                                        param->value = roundf(param->value * 100.0) / 100.0;
                                                        ESP_LOGI(TAG, "Parameter %s result: %.2f %s", 
                                                                        param->name, 
                                                                        param->value, 
                                                                        pid_info->params[p].unit);
                                                        param->value = roundf(param->value * 100.0) / 100.0;
                                                        publish_parameter_mqtt(param);
                                                        break;
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                else
                                {   
                                    param->failed = true;
                                    ESP_LOGE(TAG, "Failed to process command: %s", curr_pid->cmd);
                                }
                            }
                            else
                            {
                                param->failed = true;
                                ESP_LOGE(TAG, "Failed Queue Receive: curr_pid->cmd timeout");
                            }
                        }
                        else 
                        {
                            ESP_LOGE(TAG, "Failed to process command: %s", curr_pid->cmd);
                        }
                    }
                    else 
                    {
                        ESP_LOGE(TAG, "Failed, cmd is NULL");
                    }
                }
            }
        }

        elm327_unlock();
        xSemaphoreGive(all_pids->mutex);
        vTaskDelay(pdMS_TO_TICKS(10));

        if (strcmp("enable", all_pids->grouping) == 0 && all_pids->group_destination_type == DEST_MQTT_TOPIC && wc_timer_is_expired(&group_cycle_timer))
        {
            wc_timer_set(&group_cycle_timer, all_pids->cycle);
            
            autopid_data_publish();
        }

        if (wc_timer_is_expired(&ecu_check_timer)) {
            if (all_parameters_failed(all_pids)) {
                xEventGroupClearBits(xautopid_event_group, ECU_CONNECTED_BIT);
                ESP_LOGW(TAG, "All parameters failed - ECU disconnected");
            } else {
                xEventGroupSetBits(xautopid_event_group, ECU_CONNECTED_BIT);
            }
            wc_timer_set(&ecu_check_timer, 2000); // Reset timer for next check
        }

    }


}


cJSON* parse_json_file(FILE* f) {
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    fseek(f, 0, SEEK_SET);

    char* buffer = malloc(fsize + 1);
    fread(buffer, fsize, 1, f);
    buffer[fsize] = 0;

    cJSON* root = cJSON_Parse(buffer);
    free(buffer);
    
    return root;
}

all_pids_t* load_all_pids(void){
    int total_pids = 0;
    int car_data_pids = 0;
    int auto_pids = 0;
    
    // Count car_data.json pids
    FILE* f = fopen(FS_MOUNT_POINT"/car_data.json", "r");
    if (f) {
        cJSON* root = parse_json_file(f);
        if (root) {
            cJSON* cars = cJSON_GetObjectItem(root, "cars");
            cJSON* car = cJSON_GetArrayItem(cars, 0);
            cJSON* pids = cJSON_GetObjectItem(car, "pids");
            car_data_pids = cJSON_GetArraySize(pids);
            cJSON_Delete(root);
        }
        fclose(f);
    }
    
    // Count auto_pid.json pids
    f = fopen(FS_MOUNT_POINT"/auto_pid.json", "r");
    if (f) {
        cJSON* root = parse_json_file(f);
        if (root) {
            cJSON* pids = cJSON_GetObjectItem(root, "pids");
            cJSON* std_pids = cJSON_GetObjectItem(root, "std_pids");
            auto_pids = cJSON_GetArraySize(pids) + cJSON_GetArraySize(std_pids);
            cJSON_Delete(root);
        }
        fclose(f);
    }

    total_pids = car_data_pids + auto_pids;
    
    all_pids_t* all_pids = (all_pids_t*)calloc(1, sizeof(all_pids_t));
    if (!all_pids) return NULL;
    
    all_pids->pids = (pid_data2_t*)calloc(total_pids, sizeof(pid_data2_t));
    if (!all_pids->pids) {
        free(all_pids);
        return NULL;
    }
    
    int pid_index = 0;
    
    // Load auto_pid.json pids
    f = fopen(FS_MOUNT_POINT"/auto_pid.json", "r");
    if (f) {
        cJSON* root = parse_json_file(f);
        if (root) {
            cJSON* init_item = cJSON_GetObjectItem(root, "initialisation");
            cJSON* grouping_item = cJSON_GetObjectItem(root, "grouping");
            cJSON* car_model_item = cJSON_GetObjectItem(root, "car_model");
            cJSON* ecu_protocol_item = cJSON_GetObjectItem(root, "ecu_protocol");
            cJSON* ha_discovery_item = cJSON_GetObjectItem(root, "ha_discovery");
            cJSON* cycle_item = cJSON_GetObjectItem(root, "cycle");
            cJSON* standard_pids_item = cJSON_GetObjectItem(root, "standard_pids");
            cJSON* specific_pids_item = cJSON_GetObjectItem(root, "car_specific");
            cJSON* group_destination_item = cJSON_GetObjectItem(root, "destination");
            cJSON* group_dest_type_item = cJSON_GetObjectItem(root, "group_dest_type");

            if (init_item && init_item->valuestring) {
                all_pids->custom_init = strdup(init_item->valuestring);
                if (all_pids->custom_init) {
                    // Replace semicolons with carriage returns
                    for (size_t j = 0; j < strlen(all_pids->custom_init); j++) {
                        if (all_pids->custom_init[j] == ';') {
                            all_pids->custom_init[j] = '\r';
                        }
                    }
                }
            } else {
                all_pids->custom_init = NULL;
            }

            all_pids->grouping = (grouping_item && grouping_item->valuestring && strlen(grouping_item->valuestring) > 1) ? strdup(grouping_item->valuestring) : strdup("disable");
            all_pids->vehicle_model = car_model_item ? strdup(car_model_item->valuestring) : NULL;
            all_pids->std_ecu_protocol = ecu_protocol_item ? strdup(ecu_protocol_item->valuestring) : NULL;
            all_pids->ha_discovery_en = ha_discovery_item ? (strcmp(ha_discovery_item->valuestring, "enable") == 0) : false;
            all_pids->cycle = cycle_item ? atoi(cycle_item->valuestring) : 10000;
            all_pids->pid_std_en = standard_pids_item ? (strcmp(standard_pids_item->valuestring, "enable") == 0) : false;
            all_pids->pid_specific_en = specific_pids_item ? (strcmp(specific_pids_item->valuestring, "enable") == 0) : false;
            all_pids->group_destination = group_destination_item ? strdup(group_destination_item->valuestring) : NULL;
            all_pids->group_destination_type = group_dest_type_item && group_dest_type_item->valuestring ?
                            (strcmp(group_dest_type_item->valuestring, "MQTT_Topic") == 0 ? DEST_MQTT_TOPIC :
                            DEST_DEFAULT) : DEST_DEFAULT;
            
            // Load custom pids
            cJSON* pids = cJSON_GetObjectItem(root, "pids");
            if (pids) {
                cJSON* pid;
                cJSON_ArrayForEach(pid, pids) {
                    pid_data2_t* curr_pid = &all_pids->pids[pid_index];
                    
                    cJSON* name_item = cJSON_GetObjectItem(pid, "Name");
                    cJSON* init_item = cJSON_GetObjectItem(pid, "Init");
                    cJSON* pid_item = cJSON_GetObjectItem(pid, "PID");
                    cJSON* expr_item = cJSON_GetObjectItem(pid, "Expression");
                    cJSON* period_item = cJSON_GetObjectItem(pid, "Period");
                    cJSON* type_item = cJSON_GetObjectItem(pid, "Type");
                    cJSON* send_to_item = cJSON_GetObjectItem(pid, "Send_to");
                    cJSON* sensor_type_item = cJSON_GetObjectItem(pid, "sensor_type");
                    cJSON* unit_item = cJSON_GetObjectItem(pid, "unit"); 
                    cJSON* class_item = cJSON_GetObjectItem(pid, "class");
                    cJSON* rxheader_item = cJSON_GetObjectItem(pid, "header");
                    cJSON* min_value_item = cJSON_GetObjectItem(pid, "MinValue");
                    cJSON* max_value_item = cJSON_GetObjectItem(pid, "MaxValue");

                    if(cJSON_GetArraySize(pids) > 0)
                    {
                        all_pids->pid_custom_en = true;
                    }
                    
                    curr_pid->cmd = pid_item ? (char*)malloc(strlen(pid_item->valuestring) + 2) : NULL;
                    if (curr_pid->cmd && pid_item && strlen(pid_item->valuestring) > 1)
                    {
                        strcpy(curr_pid->cmd, pid_item->valuestring);
                        strcat(curr_pid->cmd, "\r");
                    }                    

                    curr_pid->init = NULL;
                    if (init_item && init_item->valuestring) {
                        size_t init_len = strlen(init_item->valuestring);
                        
                        if (init_len > 0) {
                            curr_pid->init = (char*)malloc(init_len + 2);
                            if (curr_pid->init) {
                                strncpy(curr_pid->init, init_item->valuestring, init_len);
                                curr_pid->init[init_len] = '\0';
                                
                                // Replace semicolons with carriage returns
                                for (size_t j = 0; j < init_len; j++) {
                                    if (curr_pid->init[j] == ';') {
                                        curr_pid->init[j] = '\r';
                                    }
                                }
                            } else {
                                ESP_LOGE(TAG, "Failed to allocate memory for init");
                            }
                        }
                    }

                    curr_pid->period = period_item ? atoi(period_item->valuestring) : 10000;
                    curr_pid->rxheader = rxheader_item ? strdup(rxheader_item->valuestring) : NULL;
                    curr_pid->pid_type = PID_CUSTOM;

                    curr_pid->parameters_count = 1;
                    curr_pid->parameters = (parameter_t*)calloc(1, sizeof(parameter_t));
                    if (curr_pid->parameters) {
                        curr_pid->parameters->name = name_item ? strdup(name_item->valuestring) : NULL;
                        curr_pid->parameters->expression = expr_item ? strdup(expr_item->valuestring) : NULL;
                        curr_pid->parameters->period = period_item ? atoi(period_item->valuestring) : 0;
                        curr_pid->parameters->destination = send_to_item ? strdup(send_to_item->valuestring) : NULL;
                        curr_pid->parameters->timer = 0;
                        curr_pid->parameters->value = FLT_MAX;
                        curr_pid->parameters->min = (min_value_item && strlen(min_value_item->valuestring) > 0) ? atof(min_value_item->valuestring) : FLT_MAX;
                        curr_pid->parameters->max = (max_value_item && strlen(max_value_item->valuestring) > 0) ? atof(max_value_item->valuestring) : FLT_MAX;
                        curr_pid->parameters->destination_type = type_item && type_item->valuestring ? 
                            (strcmp(type_item->valuestring, "MQTT_Topic") == 0 ? DEST_MQTT_TOPIC :
                            strcmp(type_item->valuestring, "MQTT_WallBox") == 0 ? DEST_MQTT_WALLBOX :
                            DEST_DEFAULT) : DEST_DEFAULT;
                        curr_pid->parameters->sensor_type = sensor_type_item ? 
                            (strcmp(sensor_type_item->valuestring, "binary") == 0 ? BINARY_SENSOR : SENSOR) : SENSOR;
                        curr_pid->parameters->unit = unit_item && unit_item->valuestring ? 
                            strdup(unit_item->valuestring) : strdup("none");
                        curr_pid->parameters->class = class_item && class_item->valuestring ? 
                            strdup(class_item->valuestring) : strdup("none");
                    }
                    
                    pid_index++;
                }
            }
            
            // Load standard pids
            cJSON* std_pids = cJSON_GetObjectItem(root, "std_pids");
            if (std_pids) {
                cJSON* pid;
                cJSON_ArrayForEach(pid, std_pids) {
                    pid_data2_t* curr_pid = &all_pids->pids[pid_index];
                    curr_pid->pid_type = PID_STD;

                    char std_init_buf[64];
                    int is_protocol_68 = 1;
                    int is_protocol_79 = 0;
                    const char *sh_value = "";

                    curr_pid->parameters_count = 1;
                    curr_pid->parameters = (parameter_t*)calloc(1, sizeof(parameter_t));
                    if (curr_pid->parameters) {
                        cJSON* name_item = cJSON_GetObjectItem(pid, "Name");
                        cJSON* period_item = cJSON_GetObjectItem(pid, "Period");
                        cJSON* type_item = cJSON_GetObjectItem(pid, "Type");
                        cJSON* send_to_item = cJSON_GetObjectItem(pid, "Send_to");
                        cJSON* sensor_type_item = cJSON_GetObjectItem(pid, "sensor_type");
                        cJSON* rxheader_item = cJSON_GetObjectItem(pid, "ReceiveHeader");

                        curr_pid->parameters->name = name_item ? strdup(name_item->valuestring) : NULL;
                        curr_pid->parameters->period = period_item ? atoi(period_item->valuestring) : 10000;
                        curr_pid->parameters->destination = send_to_item ? strdup(send_to_item->valuestring) : NULL;
                        curr_pid->parameters->destination_type = type_item && type_item->valuestring ? 
                            (strcmp(type_item->valuestring, "MQTT_Topic") == 0 ? DEST_MQTT_TOPIC :
                            strcmp(type_item->valuestring, "MQTT_WallBox") == 0 ? DEST_MQTT_WALLBOX :
                            DEST_DEFAULT) : DEST_DEFAULT;
                        curr_pid->parameters->timer = 0;
                        curr_pid->parameters->value = FLT_MAX;
                        curr_pid->parameters->sensor_type = sensor_type_item ? 
                            (strcmp(sensor_type_item->valuestring, "binary") == 0 ? BINARY_SENSOR : SENSOR) : SENSOR;
                            
                        curr_pid->rxheader = rxheader_item ? strdup(rxheader_item->valuestring) : NULL;

                        if (all_pids->std_ecu_protocol)
                        {
                            // Check protocol type once
                            is_protocol_68 = (strcmp(all_pids->std_ecu_protocol, "6") == 0 || 
                                                strcmp(all_pids->std_ecu_protocol, "8") == 0);
                            is_protocol_79 = (strcmp(all_pids->std_ecu_protocol, "7") == 0 || 
                                                strcmp(all_pids->std_ecu_protocol, "9") == 0);
                        }
                        
                        if (is_protocol_68) 
                        {
                            sh_value = "7DF";
                        }
                        else if (is_protocol_79)
                        {
                            sh_value = "18DB33F1";
                        }

                        if(curr_pid->rxheader != NULL && strlen(curr_pid->rxheader) > 0)
                        {
                            ESP_LOGI(TAG, "Setting up STD init buffer with protocol: %s, SH value: %s, RX header: %s", all_pids->std_ecu_protocol, sh_value, curr_pid->rxheader);
                            snprintf(std_init_buf, sizeof(std_init_buf), "ATSP%s\rATSH%s\rATCRA%s\r",
                                                        all_pids->std_ecu_protocol, sh_value, curr_pid->rxheader);
                        }
                        else
                        {
                            ESP_LOGI(TAG, "Setting up STD init buffer with protocol: %s, SH value: %s", all_pids->std_ecu_protocol, sh_value);
                            snprintf(std_init_buf, sizeof(std_init_buf), "ATSP%s\rATSH%s\rATCRA\r",
                                                        all_pids->std_ecu_protocol, sh_value);                        
                        }
                        all_pids->standard_init = strdup(std_init_buf);

                        if(curr_pid->parameters->name != NULL && strlen(curr_pid->parameters->name) > 0)
                        {
                            const std_pid_t* pid_info = get_pid_from_string(curr_pid->parameters->name);
                            if(pid_info)
                            {
                                ESP_LOGI(TAG, "PID Info for %s:", curr_pid->parameters->name);
                                ESP_LOGI(TAG, "  Base name: %s", pid_info->base_name);
                                ESP_LOGI(TAG, "  Num params: %d", pid_info->num_params);
                                ESP_LOGI(TAG, "  Parameter details:");
                                for(int i = 0; i < pid_info->num_params; i++)
                                {
                                    ESP_LOGI(TAG, "    [%d] Name: %s, Unit: %s", i, pid_info->params[i].name, pid_info->params[i].unit);
                                    if(strcmp(pid_info->params[i].name, strchr(curr_pid->parameters->name, '-') + 1) == 0)
                                    {
                                        curr_pid->parameters->class = strdup(pid_info->params[i].class);
                                        curr_pid->parameters->unit = strdup(pid_info->params[i].unit);
                                        char pid_hex[3];
                                        strncpy(pid_hex, curr_pid->parameters->name, 2);
                                        pid_hex[2] = '\0';

                                        curr_pid->cmd = malloc(8); // "01XX1\r\0" needs 8 bytes
                                        if(curr_pid->cmd) {
                                            sprintf(curr_pid->cmd, "01%s\r", pid_hex);
                                        }
                                    }
                                }
                            }
                            else
                            {
                                ESP_LOGW(TAG, "No PID info found for %s", curr_pid->parameters->name);
                            }
                        }
                    }
                    
                    pid_index++;
                }
            }
            
            cJSON_Delete(root);
        }

        fclose(f);
    }
    
    f = fopen(FS_MOUNT_POINT"/car_data.json", "r");
    if (f) {
        cJSON* root = parse_json_file(f);
        if (root) {
            cJSON* cars = cJSON_GetObjectItem(root, "cars");
            if (cars) {
                cJSON* car = cJSON_GetArrayItem(cars, 0);
                if (car) {
                    cJSON* init_item = cJSON_GetObjectItem(car, "init");
                    if (init_item && init_item->valuestring) {
                        all_pids->specific_init = strdup(init_item->valuestring);
                        if (all_pids->specific_init) {
                            for (size_t j = 0; j < strlen(all_pids->specific_init); j++) {
                                if (all_pids->specific_init[j] == ';') {
                                    all_pids->specific_init[j] = '\r';
                                }
                            }
                        }
                    } else {
                        all_pids->specific_init = NULL;
                    }
                    
                    cJSON* pids = cJSON_GetObjectItem(car, "pids");

                    if (pids) 
                    {
                        cJSON* pid;
                        
                        cJSON_ArrayForEach(pid, pids) 
                        {
                            pid_data2_t* curr_pid = &all_pids->pids[pid_index];
                            cJSON* pid_item = cJSON_GetObjectItem(pid, "pid");
                            cJSON* init_item = cJSON_GetObjectItem(pid, "pid_init");

                            curr_pid->init = NULL;
                            if (init_item && init_item->valuestring) {
                                size_t init_len = strlen(init_item->valuestring);
                                
                                if (init_len > 0) {
                                    curr_pid->init = (char*)malloc(init_len + 2);
                                    if (curr_pid->init) {
                                        strncpy(curr_pid->init, init_item->valuestring, init_len);
                                        curr_pid->init[init_len] = '\0';
                                        
                                        // Replace semicolons with carriage returns
                                        for (size_t j = 0; j < init_len; j++) {
                                            if (curr_pid->init[j] == ';') {
                                                curr_pid->init[j] = '\r';
                                            }
                                        }
                                    } else {
                                        ESP_LOGE(TAG, "Failed to allocate memory for init");
                                    }
                                }
                            }

                                                        
                            curr_pid->cmd = NULL;

                            if (pid_item && pid_item->valuestring) {
                                size_t cmd_len = strlen(pid_item->valuestring);

                                if (cmd_len > 0) {
                                    curr_pid->cmd = (char*)malloc(cmd_len + 2);
                                    if (curr_pid->cmd) {
                                        strncpy(curr_pid->cmd, pid_item->valuestring, cmd_len);
                                        curr_pid->cmd[cmd_len] = '\r';
                                        curr_pid->cmd[cmd_len + 1] = '\0';
                                    } else {
                                        ESP_LOGE(TAG, "Failed to allocate memory for cmd");
                                    }
                                }
                            }

                            curr_pid->pid_type = PID_SPECIFIC;
                            
                            cJSON* params = cJSON_GetObjectItem(pid, "parameters");
                            if (params) 
                            {
                                int param_count = cJSON_GetArraySize(params);
                                curr_pid->parameters_count = param_count;  // Set the count
                                curr_pid->parameters = (parameter_t*)calloc(param_count, sizeof(parameter_t));
                                curr_pid->parameters->period = all_pids->cycle;
                                curr_pid->parameters->timer = 0;
                                curr_pid->parameters->value = FLT_MAX;
                                cJSON* param;
                                int param_index = 0;
                                cJSON_ArrayForEach(param, params) 
                                {
                                    cJSON* name_item = cJSON_GetObjectItem(param, "name");
                                    curr_pid->parameters[param_index].name = name_item ? strdup(name_item->valuestring) : NULL;

                                    cJSON* expr_item = cJSON_GetObjectItem(param, "expression");
                                    curr_pid->parameters[param_index].expression = expr_item ? strdup(expr_item->valuestring) : NULL;

                                    cJSON* unit_item = cJSON_GetObjectItem(param, "unit");
                                    curr_pid->parameters[param_index].unit = unit_item && unit_item->valuestring ? 
                                        strdup(unit_item->valuestring) : strdup("none");

                                    cJSON* class_item = cJSON_GetObjectItem(param, "class");
                                    curr_pid->parameters[param_index].class = class_item && class_item->valuestring ? 
                                        strdup(class_item->valuestring) : strdup("none");

                                    cJSON* sensor_type_item = cJSON_GetObjectItem(param, "sensor_type");
                                    curr_pid->parameters[param_index].sensor_type = sensor_type_item ? 
                                            (strcmp(sensor_type_item->valuestring, "binary") == 0 ? BINARY_SENSOR : SENSOR) : SENSOR;

                                    cJSON* min_item = cJSON_GetObjectItem(param, "min");
                                    curr_pid->parameters[param_index].min = (min_item && strlen(min_item->valuestring) > 0) ?  atof(min_item->valuestring) : FLT_MAX;

                                    cJSON* max_item = cJSON_GetObjectItem(param, "max");
                                    curr_pid->parameters[param_index].max = (max_item && strlen(max_item->valuestring) > 0) ?  atof(max_item->valuestring) : FLT_MAX;

                                    cJSON* period_item = cJSON_GetObjectItem(param, "period");
                                    curr_pid->parameters[param_index].period = period_item ? atof(period_item->valuestring) : FLT_MAX;

                                    cJSON* send_to_item = cJSON_GetObjectItem(param, "send_to");
                                    curr_pid->parameters[param_index].destination = send_to_item ? strdup(send_to_item->valuestring) : strdup("none");

                                    cJSON* destination_type_item = cJSON_GetObjectItem(param, "type");      //destination_type
                                    curr_pid->parameters[param_index].destination_type = 
                                        destination_type_item && destination_type_item->valuestring ? 
                                            (strcmp(destination_type_item->valuestring, "MQTT_Topic") == 0 ? DEST_MQTT_TOPIC :
                                            strcmp(destination_type_item->valuestring, "MQTT_WallBox") == 0 ? DEST_MQTT_WALLBOX :
                                            DEST_DEFAULT) : DEST_DEFAULT;

                                    param_index++;
                                }
                            }
                            pid_index++;
                        }
                        
                    }
                }
            }
            cJSON_Delete(root);
        }
        fclose(f);
    }
    
    all_pids->pid_count = total_pids;
    
    return all_pids;
}

void print_pids(all_pids_t* all_pids) {
    const char* pid_type_str[] = {"Standard", "Custom", "Specific"};
    
    printf("Total PIDs: %lu\n", all_pids->pid_count);
    printf("Custom Init: %s\n", all_pids->custom_init);
    printf("Specific Init: %s\n", all_pids->specific_init);
    
    for (int i = 0; i < all_pids->pid_count; i++) {
        pid_data2_t* pid = &all_pids->pids[i];
        printf("\nPID %d:\n", i + 1);
        printf("  Type: %s\n", pid_type_str[pid->pid_type]);
        printf("  Command: %s\n", pid->cmd ? pid->cmd : "NULL");
        printf("  Init: %s\n", pid->init ? pid->init : "NULL");
        printf("  Period: %lu\n", pid->period);
        
        printf("  Parameter Count: %lu\n", pid->parameters_count);
        if (pid->parameters) {
            printf("  Parameters:\n");
            printf("    Name: %s\n", pid->parameters->name ? pid->parameters->name : "NULL");
            printf("    Expression: %s\n", pid->parameters->expression ? pid->parameters->expression : "NULL");
            printf("    Unit: %s\n", pid->parameters->unit ? pid->parameters->unit : "NULL");
            printf("    Class: %s\n", pid->parameters->class ? pid->parameters->class : "NULL");
            printf("    Period: %lu\n", pid->parameters->period);
            printf("     Destination: %s\n", pid->parameters->destination ? pid->parameters->destination : "NULL");

        }
        printf("-------------------\n");
    }
}

void autopid_init(char* id)
{
    device_id = id;
    // if(autopid_data.mutex == NULL)
    // {
    //     autopid_data.mutex = xSemaphoreCreateMutex();
    // }
    
    if(xautopid_event_group == NULL)
    {
        xautopid_event_group = xEventGroupCreate();
    }

    autopidQueue = xQueueCreate(QUEUE_SIZE, sizeof(response_t));
    if (autopidQueue == NULL)
    {
        ESP_LOGE(TAG, "Failed to create queue");
        return;
    }

    all_pids = load_all_pids();
    
    if (all_pids)
    {
        all_pids->mutex = xSemaphoreCreateMutex();
        // print_pids(all_pids); broken
        if (!all_pids->mutex)
        {
            ESP_LOGE(TAG, "Failed to create all_pids mutex");
            return;
        }
    }
    else
    {
        ESP_LOGE(TAG, "all_pids is NULL");
        return;
    }

    // autopid_load_config(config_str);
    // // char *desired_car_model = "Toyota Camry";
    // if(car.car_specific_en && car.selected_car_model != NULL)
    // {
    //     autopid_load_car_specific(car.selected_car_model);
    // }
    // else
    // {
    //     car.pid_count = 0;
    // }

    xTaskCreate(autopid_task, "autopid_task", 5000, (void *)AF_INET, 5, NULL);

}
