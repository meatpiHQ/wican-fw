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
#include "obd_logger.h"
#include "hw_config.h"
#include "dev_status.h"
#include "https_client_mgr.h"
#include "cert_manager.h"
#include <time.h>

// #define TAG __func__
#define TAG "AUTO_PID"
// #define TAG __func__
#define TAG "AUTO_PID"

#define TEMP_BUFFER_LENGTH  32
#define ECU_CONNECTED_BIT			BIT0

static char* auto_pid_buf;
static QueueHandle_t autopidQueue;
static char* device_id;
static EventGroupHandle_t xautopid_event_group = NULL;
static all_pids_t* all_pids = NULL;
static response_t elm327_response;
static autopid_data_t autopid_data;
static QueueHandle_t protocolnumberQueue = NULL;

#if HARDWARE_VER == WICAN_PRO
static char* elm327_autopid_cmd_buffer;
static uint32_t elm327_autopid_cmd_buffer_len = 0;
static int64_t elm327_autopid_last_cmd_time = 0;
#endif

static const uint8_t autopid_protocol_header_length[] = {
    0,  // 0: Automatic
    3,  // 1: SAE J1850 PWM (41.6 kbaud)
    3,  // 2: SAE J1850 VPW (10.4 kbaud) 
    3,  // 3: ISO 9141-2 (5 baud init, 10.4 kbaud)
    3,  // 4: ISO 14230-4 KWP (5 baud init, 10.4 kbaud)
    3,  // 5: ISO 14230-4 KWP (fast init, 10.4 kbaud)
    0,  // 6: ISO 15765-4 CAN (11 bit ID, 500 kbaud)
    9,  // 7: ISO 15765-4 CAN (29 bit ID, 500 kbaud)
    0,  // 8: ISO 15765-4 CAN (11 bit ID, 250 kbaud)
    9,  // 9: ISO 15765-4 CAN (29 bit ID, 250 kbaud)
    9,  // A: SAE J1939 CAN (29 bit ID, 250 kbaud)
    0,  // B: USER1 CAN (11 bit ID, 125 kbaud)
    0   // C: USER2 CAN (29 bit ID, 50 kbaud)
};

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
// strdup_psram
static char* strdup_psram(const char* s)
{
    if (!s) return NULL;

    size_t len = strlen(s) + 1;
    char* copy = heap_caps_malloc(len, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!copy) return NULL;

    memcpy(copy, s, len);
    return copy;
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

esp_err_t autopid_set_protocol_number(int32_t protocol_value)
{
    if (protocolnumberQueue == NULL) {
        ESP_LOGE(TAG, "Protocol queue not initialized");
        return ESP_FAIL;
    }

    // Clear any existing value in the queue
    xQueueReset(protocolnumberQueue);
    
    // Add the new protocol value
    if (xQueueSend(protocolnumberQueue, &protocol_value, pdMS_TO_TICKS(100)) != pdPASS) {
        ESP_LOGE(TAG, "Failed to set protocol number: %ld", protocol_value);
        return ESP_FAIL;
    }
    
    ESP_LOGI(TAG, "Protocol number set to: %ld", protocol_value);
    return ESP_OK;
}

esp_err_t autopid_get_protocol_number(int32_t *protocol_value)
{
    if (protocolnumberQueue == NULL) {
        ESP_LOGE(TAG, "Protocol queue not initialized");
        return ESP_FAIL;
    }
    
    if (protocol_value == NULL) {
        ESP_LOGE(TAG, "Invalid protocol_value pointer");
        return ESP_FAIL;
    }

    // Peek at the value without removing it from the queue
    if (xQueuePeek(protocolnumberQueue, protocol_value, 0) != pdPASS) {
        ESP_LOGW(TAG, "No protocol number available in queue");
        return ESP_FAIL;
    }
    
    ESP_LOGD(TAG, "Retrieved protocol number: %ld", *protocol_value);
    return ESP_OK;
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
    response_t *response = NULL;
    uint32_t supported_pids = 0;
    cJSON *root = cJSON_CreateObject();
    cJSON *pid_array = cJSON_CreateArray();
    uint8_t selected_protocol = 0;
    static const char *supported_protocols[] = {"ATTP0\r",               // Protocol 0 
                                                "ATTP6\rATSH7DF\rATCRA\r",      // Protocol 6
                                                "ATTP7\rATSH18DB33F1\rATCRA\r", // Protocol 7
                                                "ATTP8\rATSH7DF\rATCRA\r",      // Protocol 8
                                                "ATTP9\rATSH18DB33F1\rATCRA\r", // Protocol 9
                                                };

    if(protocol >= 6 && protocol <= 9) 
    {
        selected_protocol = protocol - 5;  //supported_protocols index, 6-5 = 1, 7-5 = 2, 8-5 = 3, 9-5 = 4
    }
    else
    {
        selected_protocol = 0;
    }

    response = (response_t *)malloc(sizeof(response_t)); 
    memset(response, 0, sizeof(response_t));

    if (response == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for response");
        return ESP_ERR_NO_MEM;
    }

    xSemaphoreTake(all_pids->mutex, portMAX_DELAY);
    
    if((protocol >= 6 && protocol <= 9) || protocol == 0) 
    {
        ESP_LOGI(TAG, "Setting protocol %d", protocol);

        static const char *elm327_config = "atm0\rate0\rath1\ratl0\rats1\ratst96\r";
        // elm327_process_cmd((uint8_t*)elm327_config, strlen(elm327_config), &frame, &autopidQueue);
        // while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS);
        elm327_process_cmd((uint8_t*)elm327_config , strlen(elm327_config), &autopidQueue, elm327_autopid_cmd_buffer, &elm327_autopid_cmd_buffer_len, &elm327_autopid_last_cmd_time, NULL);
        const char* protocol_cmds = supported_protocols[selected_protocol];
        ESP_LOGI(TAG, "Sending protocol commands: %s", protocol_cmds);
        // elm327_process_cmd((uint8_t*)protocol_cmds, strlen(protocol_cmds), &frame, &autopidQueue);
        // while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS);
        elm327_process_cmd((uint8_t*)protocol_cmds , strlen(protocol_cmds), &autopidQueue, elm327_autopid_cmd_buffer, &elm327_autopid_cmd_buffer_len, &elm327_autopid_last_cmd_time, NULL);
        ESP_LOGI(TAG, "Protocol %d set successfully", selected_protocol);
    }
    else
    {
        ESP_LOGE(TAG, "Invalid protocol number: %d", selected_protocol);
        // elm327_unlock();
        free(response);
        xSemaphoreGive(all_pids->mutex);
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

	uint8_t proto_number = 0;

    if(protocol == 0){
        if(elm327_get_protocol_number(&proto_number) == ESP_OK){
            ESP_LOGI(TAG, "Protocol number: %u", proto_number);
            autopid_set_protocol_number(proto_number);
        }
    }

    while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(100)) == pdPASS);
    ESP_LOGI(TAG, "Starting PID support command processing");
    for (int i = 0; i < sizeof(pid_support_cmds)/sizeof(pid_support_cmds[0]); i++) {
        ESP_LOGI(TAG, "Processing PID support command: %s", pid_support_cmds[i]);

        if (elm327_process_cmd((uint8_t*)pid_support_cmds[i] , strlen(pid_support_cmds[i]), &autopidQueue, elm327_autopid_cmd_buffer, &elm327_autopid_cmd_buffer_len, &elm327_autopid_last_cmd_time, autopid_parser) != 0) {
            ESP_LOGW(TAG, "Failed to process PID support command: %s", pid_support_cmds[i]);
            continue;
        }
        
        if (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(10000)) == pdPASS) {
            ESP_LOGI(TAG, "Raw response length: %lu", response->length);
            ESP_LOG_BUFFER_HEX(TAG, response->data, response->length);

            // Skip mode byte (0x41) and PID byte
            if ((strstr((char*)response->data, "error") == NULL) && response->length >= 7) {
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
            if (response->length >= 7) {
                ESP_LOGW(TAG, "No response received for PID support command: %s", pid_support_cmds[i]);
            }else {
                ESP_LOGW(TAG, "Recvied error response: %s", response->data);
            }
        }
    }

    ESP_LOGI(TAG, "Adding PIDs to JSON object");
    cJSON_AddItemToObject(root, "std_pids", pid_array);

    // Convert to string and cleanup
    char *json_str = cJSON_PrintUnformatted(root);
    if (json_str) {
        ESP_LOGI(TAG, "JSON string created, length: %zu", strlen(json_str));
        if (strlen(json_str) < available_pids_size) {
            strcpy(available_pids, json_str);
            free(json_str);
            cJSON_Delete(root);

            // ESP_LOGI(TAG, "Restoring protocol settings");
            // elm327_process_cmd((uint8_t*)restore_cmd, strlen(restore_cmd), &frame, &autopidQueue);
            // while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS);
            
            free(response);
            xSemaphoreGive(all_pids->mutex);
            return ESP_OK;
        }
        ESP_LOGW(TAG, "JSON string too long for buffer");
        free(json_str);
    } else {
        ESP_LOGE(TAG, "Failed to create JSON string");
    }

    ESP_LOGI(TAG, "Restoring protocol settings");
    // elm327_process_cmd((uint8_t*)restore_cmd, strlen(restore_cmd), &frame, &autopidQueue);
    // while (xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS);
    
    cJSON_Delete(root);
    free(response);
    // elm327_unlock();
    xSemaphoreGive(all_pids->mutex);
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

static void autopid_data_update(all_pids_t *pids)
{
    if (!pids || !pids->mutex) {
        ESP_LOGE(TAG, "Invalid all_pids or mutex");
        return;
    }
    
    //take autopid_data mutex
    if (xSemaphoreTake(autopid_data.mutex, portMAX_DELAY) == pdTRUE) {
        //free old data
        if (autopid_data.json_str != NULL) {
            free(autopid_data.json_str);
            autopid_data.json_str = NULL;
        }

        cJSON *root = cJSON_CreateObject();
        if (root) {
            for (uint32_t i = 0; i < all_pids->pid_count; i++) {
                pid_data_t *curr_pid = &all_pids->pids[i];
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
            autopid_data.json_str = cJSON_PrintUnformatted(root);
            cJSON_Delete(root);
        }

        //release mutex
        xSemaphoreGive(autopid_data.mutex);
    }
}

char *autopid_data_read(void)
{
    //json_str must be freed by the caller
    static char *json_str = NULL;
    
    if (!autopid_data.mutex) {
        ESP_LOGE(TAG, "Invalid mutex");
        return NULL;
    }

    if (xSemaphoreTake(autopid_data.mutex, portMAX_DELAY) == pdTRUE) {
        if (autopid_data.json_str != NULL) {
            json_str = (char *)heap_caps_malloc(strlen(autopid_data.json_str) + 1, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
            if (json_str != NULL) {
                strcpy(json_str, autopid_data.json_str);
            }
        } else {
            json_str = NULL;
        }
        xSemaphoreGive(autopid_data.mutex);
    }
    return json_str;
}

char *autopid_get_value_by_name(char* name)
{
    //json_str must be freed by the caller
    char *result_json = NULL;
    
    if (!autopid_data.mutex) {
        ESP_LOGE(TAG, "Invalid mutex");
        return NULL;
    } else if (name == NULL || strlen(name) == 0) {
        ESP_LOGE(TAG, "Invalid name");
        return NULL;
    }

    if (xSemaphoreTake(autopid_data.mutex, portMAX_DELAY) == pdTRUE) {
        if (autopid_data.json_str != NULL) {
            char *json_copy = (char *)heap_caps_malloc(strlen(autopid_data.json_str) + 1, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
            if (json_copy != NULL) {
                strcpy(json_copy, autopid_data.json_str);
                
                cJSON *root = cJSON_Parse(json_copy);
                if (root) {
                    cJSON *item = cJSON_GetObjectItem(root, name);
                    if (item) {
                        cJSON *result = cJSON_CreateObject();
                        cJSON *value_copy = cJSON_Duplicate(item, 1);
                        cJSON_AddItemToObject(result, name, value_copy);
                        result_json = cJSON_PrintUnformatted(result);
                        cJSON_Delete(result);
                    }
                    cJSON_Delete(root);
                }
                heap_caps_free(json_copy);
            }
        }
        xSemaphoreGive(autopid_data.mutex);
    }
    return result_json;
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
                pid_data_t *curr_pid = &all_pids->pids[i];
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


// Map destination_type_t to string for logging
static const char* dest_type_str(destination_type_t t){
    switch(t){
        case DEST_MQTT_TOPIC: return "MQTT_Topic";
        case DEST_MQTT_WALLBOX: return "MQTT_WallBox";
        case DEST_HTTP: return "HTTP";
        case DEST_HTTPS: return "HTTPS";
        case DEST_ABRP_API: return "ABRP_API";
        default: return "Default";
    }
}

// Build ABRP payload wrapper if needed
static char* build_abrp_payload(const char *raw_json, const char *car_model){
    if(!raw_json) return NULL;
    // Format: {"tlm":<raw_json>[,"car_model":"..." ]}
    size_t raw_len = strlen(raw_json);
    size_t car_len = car_model? strlen(car_model):0;
    size_t extra = car_model? (car_len + 14):0; // ,"car_model":""
    size_t total = raw_len + extra + 10; // wrapper overhead
    char *buf = heap_caps_malloc(total, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!buf) return NULL;
    if(car_model && car_len){
        snprintf(buf, total, "{\"tlm\":%s,\"car_model\":\"%s\"}", raw_json, car_model);
    }else{
        snprintf(buf, total, "{\"tlm\":%s}", raw_json);
    }
    return buf;
}



/// @brief 
/// @param  


void autopid_publish_all_destinations(void){
    if(!all_pids){
        ESP_LOGE(TAG, "autopid_publish_all_destinations: all_pids NULL");
        return;
    }
    if(strcmp("enable", all_pids->grouping) != 0){
        return; // grouping disabled
    }

    // Get snapshot JSON (caller must free)
    char *raw_json = autopid_data_read();
    if(!raw_json){
        ESP_LOGW(TAG, "No autopid data to publish");
        return;
    }

    // Current time not directly needed with wc_timer; timers store absolute expiry in us

    // Legacy single destination path if no multi-destinations parsed
    // if(all_pids->destinations_count == 0){
    //     if(all_pids->group_destination_type == DEST_MQTT_TOPIC){
    //         if(all_pids->group_destination && strlen(all_pids->group_destination)>0){
    //             mqtt_publish(all_pids->group_destination, raw_json, 0, 0, 1);
    //         }else{
    //             mqtt_publish(config_server_get_mqtt_rx_topic(), raw_json, 0, 0, 1);
    //         }
    //     }
    //     free(raw_json);
    //     return;
    // }

    for(uint32_t i=0;i<all_pids->destinations_count;i++){
        group_destination_t *gd = &all_pids->destinations[i];
        if(!gd->enabled) continue;

        // Determine base cycle and current backoff adjusted cycle
        uint32_t base_cycle = gd->cycle > 0 ? gd->cycle : all_pids->cycle;
        uint32_t effective_cycle = base_cycle;
        if(gd->backoff_ms && gd->backoff_ms > base_cycle){
            effective_cycle = gd->backoff_ms; // apply backoff delay
        }
        // If we have an effective cycle (>0) use publish_timer; if timer not set (0) schedule immediate publish
        if(effective_cycle > 0){
            if(gd->publish_timer != 0 && !wc_timer_is_expired(&gd->publish_timer)){
                // Not yet time
                continue;
            }
        }

        const char *dest = gd->destination? gd->destination: "";
        switch(gd->type){
            case DEST_MQTT_TOPIC:
            case DEST_DEFAULT: // treat as MQTT default topic
                if(dest[0] != '\0'){
                    mqtt_publish(dest, raw_json, 0, 0, 1);
                }else{
                    mqtt_publish(config_server_get_mqtt_rx_topic(), raw_json, 0, 0, 1);
                }
                ESP_LOGI(TAG, "Published MQTT (%s) to %s", dest_type_str(gd->type), dest[0]? dest: config_server_get_mqtt_rx_topic());
                break;
            case DEST_MQTT_WALLBOX: {
                // For now replicate same JSON publish (future: custom format)
                if(dest[0] != '\0'){
                    mqtt_publish(dest, raw_json, 0, 0, 1);
                }else{
                    mqtt_publish(config_server_get_mqtt_rx_topic(), raw_json, 0, 0, 1);
                }
                ESP_LOGI(TAG, "Published MQTT WallBox to %s", dest[0]? dest: config_server_get_mqtt_rx_topic());
                break; }
            case DEST_HTTP:
            case DEST_HTTPS:
            case DEST_ABRP_API: {
                // Build URL (ABRP adds token as query param if provided)
                char *url = NULL;
                if((gd->type == DEST_ABRP_API || gd->type == DEST_HTTP || gd->type == DEST_HTTPS) && gd->api_token && strlen(gd->api_token)>0){
                    const char *sep = strchr(dest,'?')? "&":"?";
                    url = heap_caps_malloc(strlen(dest)+strlen(gd->api_token)+16, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
                    if(url) sprintf(url, "%s%stoken=%s", dest, sep, gd->api_token);
                }else{
                    url = dest[0]? strdup_psram(dest): NULL;
                }
                if(!url){
                    ESP_LOGW(TAG, "Destination %u missing URL", i); break; }

                // Build body (ABRP wrapper) use JSON body for all
                char *body = NULL;
                if(gd->type == DEST_ABRP_API){
                    body = build_abrp_payload(raw_json, all_pids->vehicle_model);
                }else{
                    body = strdup_psram(raw_json);
                }
                if(!body){
                    ESP_LOGE(TAG, "Failed to allocate body"); free(url); break; }

                https_client_mgr_config_t cfg = {0};
                cfg.url = url;
                // Log destination URL plus current system time (epoch + ISO8601 UTC) for TLS timing diagnostics
                time_t _now = 0; time(&_now);
                struct tm _utc; gmtime_r(&_now, &_utc);
                char _tbuf[32]; strftime(_tbuf, sizeof(_tbuf), "%Y-%m-%dT%H:%M:%SZ", &_utc);
                ESP_LOGI(TAG, "HTTP(S) dest %u URL: %s (epoch=%ld utc=%s)", i, url, (long)_now, _tbuf);
                cfg.timeout_ms = 10000;
                if(gd->type == DEST_HTTPS || gd->type == DEST_ABRP_API){
                    // HTTPS: attempt cert set if provided and not default
                    if(gd->cert_set && strcmp(gd->cert_set, "default")!=0){
                        size_t ca_len=0, cli_len=0, key_len=0;
                        const char *ca = cert_manager_get_set_ca_ptr(gd->cert_set, &ca_len);
                        const char *cli = cert_manager_get_set_client_cert_ptr(gd->cert_set, &cli_len);
                        const char *key = cert_manager_get_set_client_key_ptr(gd->cert_set, &key_len);
                        if(ca){ 
                            cfg.cert_pem = ca; cfg.cert_len = ca_len; 
                            ESP_LOGI(TAG, "Using CA cert from set '%s'", gd->cert_set);
                        }
                        else { 
                            cfg.use_crt_bundle = true; 
                            ESP_LOGW(TAG, "No CA cert in set '%s', using built-in bundle", gd->cert_set);
                        }
                        if(cli && key){ 
                            cfg.client_cert_pem = cli; cfg.client_cert_len = cli_len; cfg.client_key_pem = key; cfg.client_key_len = key_len; 
                            ESP_LOGI(TAG, "Using client cert+key from set '%s'", gd->cert_set);
                        }
                        else { /* optional */ }
                    }else{
                        cfg.use_crt_bundle = true; // use built-in bundle
                        ESP_LOGW(TAG, "No CA cert in set '%s', using built-in bundle", gd->cert_set);
                    }
                }else{
                    // HTTP (non TLS): set bundle flag false
                }

                // Build headers
                const char *headers[4] = {0};
                char *auth_hdr = NULL;
                headers[0] = "Content-Type: application/json";
                int h_idx = 1;
                if(gd->api_token && strlen(gd->api_token)>0 && gd->type != DEST_ABRP_API){
                    auth_hdr = heap_caps_malloc(strlen(gd->api_token)+32, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
                    if(auth_hdr){ sprintf(auth_hdr, "Authorization: Bearer %s", gd->api_token); headers[h_idx++] = auth_hdr; }
                }
                headers[h_idx] = NULL;

                https_client_mgr_response_t resp = {0};
                // If host is raw IPv4 address, skip CN verification to allow self-signed IP certs
                if(url){
                    const char *host_start = strstr(url, "://");
                    host_start = host_start ? host_start + 3 : url;
                    // host ends at '/' or end
                    char host_buf[64] = {0};
                    size_t hi=0;
                    while(host_start[hi] && host_start[hi] != '/' && host_start[hi] != ':' && hi < sizeof(host_buf)-1){ host_buf[hi] = host_start[hi]; hi++; }
                    bool is_ip = true;
                    for(size_t k=0;k<hi;k++){ if( (host_buf[k] < '0' || host_buf[k] > '9') && host_buf[k] != '.') { is_ip = false; break; } }
                    if(is_ip){ cfg.skip_common_name = true; }
                }

                esp_err_t err = https_client_mgr_request(&cfg, HTTPS_METHOD_POST, body, strlen(body), headers, &resp);
                if(err == ESP_OK){
                    ESP_LOGI(TAG, "HTTP(S) dest %u status %d success=%d", i, resp.status_code, resp.is_success);
                    if(resp.is_success){
                        gd->consec_failures = 0;
                        gd->backoff_ms = 0;
                    } else {
                        gd->consec_failures++;
                    }
                }else{
                    ESP_LOGE(TAG, "HTTP(S) dest %u request failed: %s", i, esp_err_to_name(err));
                    gd->consec_failures++;
                }
                // Backoff logic: after 3 failures, double backoff up to 2x base_cycle (or 60s min cap)
                if(gd->consec_failures >= 3){
                    uint32_t min_cap = 60000; // 60s cap baseline
                    uint32_t next = (gd->backoff_ms? gd->backoff_ms : base_cycle);
                    if(next < min_cap) next = next * 2; // exponential until min_cap
                    if(next < min_cap) next = min_cap; // ensure floor
                    // limit to 2 * base_cycle if that is bigger than min_cap
                    uint32_t max_backoff = base_cycle * 2;
                    if(max_backoff < min_cap) max_backoff = min_cap; // keep at least min_cap
                    if(next > max_backoff) next = max_backoff;
                    gd->backoff_ms = next;
                }
                https_client_mgr_free_response(&resp);
                if(auth_hdr) free(auth_hdr);
                free(body);
                free(url);
                break; }
            default:
                break;
        }
        // Reschedule timer if periodic
        if(effective_cycle > 0){
            wc_timer_set(&gd->publish_timer, effective_cycle); // schedule next expiry
        }else{
            gd->publish_timer = 0; // event-based; remains immediate until triggered differently
        }
        // Yield a bit after network operations
        vTaskDelay(pdMS_TO_TICKS(5));
    }

    free(raw_json);
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
        xSemaphoreGive(all_pids->mutex);
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

    if (buffer == NULL || response == NULL) {
        ESP_LOGE(TAG, "Invalid buffer or response pointer");
        return;
    }

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

    int32_t current_protocol_number = -1;

    if(autopid_get_protocol_number(&current_protocol_number) == ESP_OK)
    {
        ESP_LOGI(TAG, "Current protocol number: %ld", current_protocol_number);
    }
    else
    {
        ESP_LOGE(TAG, "Failed to get protocol number");
    }
    
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
            if(current_protocol_number == -1 || (current_protocol_number > sizeof(autopid_protocol_header_length )))
            {
                // Handle different header formats
                switch (header_length) {
                    case 2: 
                        data_start += 9;
                        ESP_LOGW(TAG, "2-byte header format: Adjusted data_start by 9");
                        break;
                    case 3:
                    case 8:
                        ESP_LOGW(TAG, "%d-byte header format: No adjustment needed", header_length);
                        break;
                    default:
                        ESP_LOGW(TAG, "Unexpected header length: %d, skipping frame", header_length);
                        frame = strtok(NULL, "\r\n");
                        continue;
                }
            }
            else
            {
                // Adjust data_start based on protocol number
                ESP_LOGI(TAG, "Adjusting data_start based on protocol number: %ld, header length: + %d", current_protocol_number, autopid_protocol_header_length[current_protocol_number]);
                data_start += (uint8_t)autopid_protocol_header_length[current_protocol_number];
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
                    lowest_header_data = (uint8_t*)heap_caps_malloc(current_length, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
                } else {
                    lowest_header_data = (uint8_t*)heap_caps_realloc(lowest_header_data, current_length, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
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
            lowest_header_data = NULL;
        }
        ESP_LOGI(TAG, "Null priority data set - frames: %d, all headers same: %d", 
                frame_count, all_headers_same);
        } else {
            response->priority_data = lowest_header_data;
            response->priority_data_len = lowest_header_length;
            
            if (response->priority_data != NULL && response->priority_data_len > 0) {
                ESP_LOGI(TAG, "Priority data set - length: %u, starting with byte: 0x%02X", 
                        response->priority_data_len, 
                        response->priority_data[0]);
            } else {
                ESP_LOGI(TAG, "Priority data set but empty or invalid - length: %u", 
                        response->priority_data_len);
            }
        }
    
    ESP_LOGI(TAG, "Parsing complete. Headers - Lowest: 0x%lX, Highest: 0x%lX, Total frames: %d, Total bytes: %lu, Priority data length: %u",
            lowest_header, highest_header, frame_count, response->length, response->priority_data_len);
}

static void append_to_buffer(char *buffer, const char *new_data) 
{
    if (strlen(buffer) + strlen(new_data) < AUTOPID_BUFFER_SIZE) 
    {
        strcat(buffer, new_data);
    }
    else
    {
        ESP_LOGE(TAG, "Failed add data to buffer");
    }
}

void autopid_parser(char *str, uint32_t len, QueueHandle_t *q, char* cmd_str)
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
            #if HARDWARE_VER == WICAN_PRO
            // elm327_process_cmd((uint8_t *)str_send, cmd_len, &autopidQueue, elm327_autopid_cmd_buffer, &elm327_autopid_cmd_buffer_len, &elm327_autopid_last_cmd_time, autopid_parser);
            elm327_run_command((char*)str_send, cmd_len, 1000, &autopidQueue, NULL);
            #else
            twai_message_t tx_msg;
            elm327_process_cmd((uint8_t *)str_send, cmd_len, &tx_msg, &autopidQueue);
            #endif
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
        pid_data_t *curr_pid = &all_pids->pids[i];
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
    static char default_init[] = "ati\rate0\rath1\ratl0\rats1\ratm0\ratst96\r";
    wc_timer_t ecu_check_timer;
    wc_timer_t group_cycle_timer;
    static bool autopid_data_grouping_en = false;

    if (strcmp("enable", all_pids->grouping) == 0)
    {
        autopid_data_grouping_en = true;
    }

    ESP_LOGI(TAG, "Autopid Task Started");
    
    vTaskDelay(pdMS_TO_TICKS(100));
    send_commands(default_init, 50);

    // while(config_server_mqtt_en_config() == 1 && !mqtt_connected())
    // {
    //     vTaskDelay(pdMS_TO_TICKS(2000));
    // }

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
    
    if(strcmp(all_pids->std_ecu_protocol, "0") == 0) 
    {
        ESP_LOGI(TAG, "Protocol is Auto");
        
        // elm327_get_protocol_number
        uint8_t auto_protocol_number = 0;
        if(elm327_get_protocol_number(&auto_protocol_number) == ESP_OK){
            autopid_set_protocol_number(auto_protocol_number);
            
            // Free existing standard_init if allocated
            if(all_pids->standard_init) {
                free(all_pids->standard_init);
            }
            
            // Allocate new memory for the protocol string
            char protocol_str[16];
            snprintf(protocol_str, sizeof(protocol_str), "ATTP%01X\r", auto_protocol_number);
            all_pids->standard_init = strdup_psram(protocol_str);
        }else{
            ESP_LOGE(TAG, "Failed to get protocol number");
        }
        
        ESP_LOGI(TAG, "Protocol number: %u", auto_protocol_number);
    }

    ESP_LOGI(TAG, "Autopid Start loop");
    ESP_LOGI(TAG, "Total PIDs: %lu", all_pids->pid_count);

    // Initialize timers
    wc_timer_set(&ecu_check_timer, 2000);
    wc_timer_set(&group_cycle_timer, all_pids->cycle);

    while(1) 
    {
        static pid_type_t previous_pid_type = PID_MAX;

        if(dev_status_is_sleeping())
        {
            ESP_LOGI(TAG, "Device is sleeping, waiting for wakeup");
            obd_logger_disable();
            dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);
            ESP_LOGI(TAG, "Device awake, resuming autopid task");
            obd_logger_enable();
        }
        
        if(!dev_status_is_autopid_enabled())
        {
            ESP_LOGI(TAG, "Autopid is disabled, waiting for enable");
            obd_logger_disable();
            dev_status_wait_for_bits(DEV_AUTOPID_ENABLED_BIT, portMAX_DELAY);
            ESP_LOGI(TAG, "Autopid enabled, resuming autopid task");
            obd_logger_enable();
            send_commands(default_init, 50);
            vTaskDelay(pdMS_TO_TICKS(100));
        }
        // elm327_lock();
        xSemaphoreTake(all_pids->mutex, portMAX_DELAY);
        
        // Loop through all PIDs
        for(uint32_t i = 0; i < all_pids->pid_count; i++) 
        {
            pid_data_t *curr_pid = &all_pids->pids[i];
            // Skip if PID type not enabled
            if((curr_pid->pid_type == PID_STD && !all_pids->pid_std_en) ||
            (curr_pid->pid_type == PID_CUSTOM && !all_pids->pid_custom_en) ||
            (curr_pid->pid_type == PID_SPECIFIC && !all_pids->pid_specific_en))
            {
                continue;
            }

            // Loop through parameters
            for(uint32_t p = 0; p < curr_pid->parameters_count; p++) 
            {
                parameter_t *param = &curr_pid->parameters[p];
                
                // Check parameter timer
                if(wc_timer_is_expired(&param->timer)) 
                {
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

                    ESP_LOGI(TAG, "Processing parameter: %s", param->name);
                    // Reset timer with parameter period
                    wc_timer_set(&param->timer, param->period);

                    if(curr_pid->cmd != NULL && strlen(curr_pid->cmd) > 0) 
                    {
                        // twai_message_t tx_msg;

                        if(curr_pid->pid_type == PID_CUSTOM || curr_pid->pid_type == PID_SPECIFIC) 
                        {
                            if(curr_pid->init != NULL && strlen(curr_pid->init) > 0)
                            {
                                send_commands(curr_pid->init, 2);
                            }
                        }

                        ESP_LOGI(TAG, "Executing command: %s", curr_pid->cmd);
                        // if(elm327_process_cmd((uint8_t*)curr_pid->cmd, 
                        //                     strlen(curr_pid->cmd), 
                        //                     &tx_msg, 
                        //                     &autopidQueue) == ESP_OK)
                        #if HARDWARE_VER == WICAN_PRO
                        if(elm327_process_cmd((uint8_t*)curr_pid->cmd , strlen(curr_pid->cmd), &autopidQueue, elm327_autopid_cmd_buffer, &elm327_autopid_cmd_buffer_len, &elm327_autopid_last_cmd_time, autopid_parser) == ESP_OK)
                        #else
                        if(elm327_process_cmd((uint8_t*)pid_req[i].pid_command , strlen(pid_req[i].pid_command), &tx_msg, &autopidQueue) == ESP_OK)
                        #endif
                        {
                            ESP_LOGI(TAG, "Command processed successfully");
                            
                            if(xQueueReceive(autopidQueue, &elm327_response, pdMS_TO_TICKS(12000)) == pdPASS)
                            {
                                ESP_LOGI(TAG, "Response received, length: %lu", elm327_response.length);
                                ESP_LOG_BUFFER_HEXDUMP(TAG, elm327_response.data, 1, ESP_LOG_INFO);
                                if(strstr((char*)elm327_response.data, "error") == NULL &&
                                    strstr((char*)elm327_response.data, "SEARCHING") == NULL &&
                                    strstr((char*)elm327_response.data, "UNABLE TO CONNECT") == NULL)
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

        autopid_data_update(all_pids);
        // elm327_unlock();
        xSemaphoreGive(all_pids->mutex);
        vTaskDelay(pdMS_TO_TICKS(10));

        if (autopid_data_grouping_en)
        {
            autopid_publish_all_destinations();
            // wc_timer_set(&group_cycle_timer, all_pids->cycle);
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

// Helper function to replace all occurrences of "ATSP" with "ATTP" in a string
static void replace_atsp_with_attp(char *str) {
    if (!str) return;
    
    char *atsp_pos = strstr(str, "ATSP");
    while (atsp_pos != NULL) {
        // Replace SP with TP
        atsp_pos[2] = 'T';
        atsp_pos[3] = 'P';
        // Look for the next occurrence
        atsp_pos = strstr(atsp_pos + 4, "ATSP");
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
    
    ESP_LOGI(TAG, "Allocating memory for %d pids...", total_pids);
    all_pids_t* all_pids = (all_pids_t*)calloc(1, sizeof(all_pids_t));
    if (!all_pids) return NULL;
    
    if(total_pids == 0) {
        ESP_LOGE(TAG, "No PIDs found in car_data.json or auto_pid.json");
        return all_pids;
    }

    all_pids->pids = (pid_data_t*)calloc(total_pids, sizeof(pid_data_t));
    if (!all_pids->pids) {
        free(all_pids);
        return NULL;
    }
    int pid_index = 0;
    
    ESP_LOGI(TAG, "Loading auto_pid.json pids...");
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
            cJSON* group_destination_item = cJSON_GetObjectItem(root, "destination"); // legacy single destination
            cJSON* group_dest_type_item = cJSON_GetObjectItem(root, "group_dest_type"); // legacy single type
            cJSON* destinations_array = cJSON_GetObjectItem(root, "destinations");      // new multi-destination array
            cJSON* group_api_token_item = cJSON_GetObjectItem(root, "group_api_token"); // legacy api token

            if (init_item && init_item->valuestring) {
                all_pids->custom_init = strdup_psram(init_item->valuestring);
                if (all_pids->custom_init) {
                    // Replace semicolons with carriage returns
                    for (size_t j = 0; j < strlen(all_pids->custom_init); j++) {
                        if (all_pids->custom_init[j] == ';') {
                            all_pids->custom_init[j] = '\r';
                        }
                    }
                    replace_atsp_with_attp(all_pids->custom_init);
                }
            } else {
                all_pids->custom_init = NULL;
            }

            all_pids->grouping = (grouping_item && grouping_item->valuestring && strlen(grouping_item->valuestring) > 1) ? strdup_psram(grouping_item->valuestring) : strdup_psram("disable");
            all_pids->vehicle_model = car_model_item ? strdup_psram(car_model_item->valuestring) : NULL;
            all_pids->std_ecu_protocol = ecu_protocol_item ? strdup_psram(ecu_protocol_item->valuestring) : NULL;
            all_pids->ha_discovery_en = ha_discovery_item ? (strcmp(ha_discovery_item->valuestring, "enable") == 0) : false;

            if(cycle_item && cycle_item->valuestring && strlen(cycle_item->valuestring) > 0)
                all_pids->cycle = atoi(cycle_item->valuestring);
            else if(cycle_item && cycle_item->valueint)
                all_pids->cycle = cycle_item->valueint;
            else
                all_pids->cycle = 10000;

            all_pids->pid_std_en = standard_pids_item ? (strcmp(standard_pids_item->valuestring, "enable") == 0) : false;
            all_pids->pid_specific_en = specific_pids_item ? (strcmp(specific_pids_item->valuestring, "enable") == 0) : false;
            all_pids->group_destination = group_destination_item ? strdup_psram(group_destination_item->valuestring) : NULL;
            // Map legacy group_dest_type to enum (for backward compatibility)
            if(group_dest_type_item && group_dest_type_item->valuestring){
                const char *t = group_dest_type_item->valuestring;
                if(strcmp(t, "MQTT_Topic") == 0){
                    all_pids->group_destination_type = DEST_MQTT_TOPIC;
                }else if(strcmp(t, "MQTT_WallBox") == 0){
                    all_pids->group_destination_type = DEST_MQTT_WALLBOX;
                }else if(strcmp(t, "HTTP") == 0){
                    all_pids->group_destination_type = DEST_HTTP;
                }else if(strcmp(t, "HTTPS") == 0){
                    all_pids->group_destination_type = DEST_HTTPS;
                }else if(strcmp(t, "ABRP_API") == 0){
                    all_pids->group_destination_type = DEST_ABRP_API;
                }else{
                    all_pids->group_destination_type = DEST_DEFAULT;
                }
            }else{
                all_pids->group_destination_type = DEST_DEFAULT;
            }

            // Parse new destinations array (up to a reasonable max, e.g. 6)
            all_pids->destinations = NULL;
            all_pids->destinations_count = 0;
            if(destinations_array && cJSON_IsArray(destinations_array)){
                int count = cJSON_GetArraySize(destinations_array);
                if(count > 0){
                    all_pids->destinations = calloc(count, sizeof(group_destination_t));
                    if(all_pids->destinations){
                        all_pids->destinations_count = count;
                        for(int di=0; di<count; di++){
                            cJSON *d = cJSON_GetArrayItem(destinations_array, di);
                            if(!d) continue;
                            group_destination_t *gd = &all_pids->destinations[di];
                            cJSON *type_item = cJSON_GetObjectItem(d, "type");
                            cJSON *dest_item = cJSON_GetObjectItem(d, "destination");
                            cJSON *cycle_item2 = cJSON_GetObjectItem(d, "cycle");
                            cJSON *api_token_item = cJSON_GetObjectItem(d, "api_token");
                            cJSON *cert_set_item = cJSON_GetObjectItem(d, "cert_set");
                            cJSON *enabled_item = cJSON_GetObjectItem(d, "enabled");

                            // type mapping
                            const char *type_str = type_item && cJSON_IsString(type_item)? type_item->valuestring: "Default";
                            if(strcmp(type_str, "MQTT_Topic") == 0){
                                gd->type = DEST_MQTT_TOPIC;
                            }else if(strcmp(type_str, "MQTT_WallBox") == 0){
                                gd->type = DEST_MQTT_WALLBOX;
                            }else if(strcmp(type_str, "HTTP") == 0){
                                gd->type = DEST_HTTP;
                            }else if(strcmp(type_str, "HTTPS") == 0){
                                gd->type = DEST_HTTPS;
                            }else if(strcmp(type_str, "ABRP_API") == 0){
                                gd->type = DEST_ABRP_API;
                            }else{
                                gd->type = DEST_DEFAULT;
                            }

                            gd->destination = dest_item && cJSON_IsString(dest_item)? strdup_psram(dest_item->valuestring): NULL;

                            // Ensure scheme prefix for HTTP/HTTPS destinations if missing
                            if (gd->destination && (gd->type == DEST_HTTP || gd->type == DEST_HTTPS)) {
                                bool has_http  = (strncmp(gd->destination, "http://", 7)  == 0);
                                bool has_https = (strncmp(gd->destination, "https://", 8) == 0);
                                if (!has_http && !has_https) {
                                    const char *prefix = (gd->type == DEST_HTTPS) ? "https://" : "http://";
                                    size_t new_len = strlen(prefix) + strlen(gd->destination) + 1;
                                    char *with_prefix = (char*)malloc(new_len);
                                    if (with_prefix) {
                                        strcpy(with_prefix, prefix);
                                        strcat(with_prefix, gd->destination);
                                        free(gd->destination);
                                        gd->destination = with_prefix;
                                    }
                                }
                            }

                            if(cycle_item2 && cycle_item2->valuestring && strlen(cycle_item2->valuestring) > 0)
                                gd->cycle = (uint32_t)atoi(cycle_item2->valuestring);
                            else if(cycle_item2 && cycle_item2->valueint)
                                gd->cycle = (uint32_t)cycle_item2->valueint;
                            else
                                gd->cycle = 10000;

                            gd->api_token = api_token_item && cJSON_IsString(api_token_item)? strdup_psram(api_token_item->valuestring): NULL;
                            gd->cert_set = cert_set_item && cJSON_IsString(cert_set_item)? strdup_psram(cert_set_item->valuestring): strdup_psram("default");
                            gd->enabled = enabled_item && cJSON_IsBool(enabled_item)? cJSON_IsTrue(enabled_item): true;
                            gd->publish_timer = 0; // immediate eligibility
                            gd->consec_failures = 0;
                            gd->backoff_ms = 0;
                        }
                        // If legacy single destination fields exist but array was empty previously, we keep them separate.
                    }
                }
            }else{
                // No new destinations array: fabricate one from legacy fields if present
                if(all_pids->group_destination || group_dest_type_item){
                    all_pids->destinations = calloc(1, sizeof(group_destination_t));
                    if(all_pids->destinations){
                        all_pids->destinations_count = 1;
                        all_pids->destinations[0].type = all_pids->group_destination_type;
                        all_pids->destinations[0].destination = all_pids->group_destination ? strdup_psram(all_pids->group_destination) : NULL;
                        // Ensure scheme prefix for legacy single HTTP/HTTPS destination if missing
                        if (all_pids->destinations[0].destination && (all_pids->destinations[0].type == DEST_HTTP || all_pids->destinations[0].type == DEST_HTTPS)) {
                            bool has_http  = (strncmp(all_pids->destinations[0].destination, "http://", 7)  == 0);
                            bool has_https = (strncmp(all_pids->destinations[0].destination, "https://", 8) == 0);
                            if (!has_http && !has_https) {
                                const char *prefix = (all_pids->destinations[0].type == DEST_HTTPS) ? "https://" : "http://";
                                size_t new_len = strlen(prefix) + strlen(all_pids->destinations[0].destination) + 1;
                                char *with_prefix = (char*)malloc(new_len);
                                if (with_prefix) {
                                    strcpy(with_prefix, prefix);
                                    strcat(with_prefix, all_pids->destinations[0].destination);
                                    free(all_pids->destinations[0].destination);
                                    all_pids->destinations[0].destination = with_prefix;
                                }
                            }
                        }
                        all_pids->destinations[0].cycle = all_pids->cycle;
                        all_pids->destinations[0].api_token = group_api_token_item && group_api_token_item->valuestring ? strdup_psram(group_api_token_item->valuestring) : NULL;
                        all_pids->destinations[0].cert_set = strdup_psram("default");
                        all_pids->destinations[0].enabled = true;
                        all_pids->destinations[0].publish_timer = 0;
                        all_pids->destinations[0].consec_failures = 0;
                        all_pids->destinations[0].backoff_ms = 0;
                    }
                }
            }
            
            // Load custom pids
            cJSON* pids = cJSON_GetObjectItem(root, "pids");
            if (pids) {
                cJSON* pid;
                cJSON_ArrayForEach(pid, pids) {
                    pid_data_t* curr_pid = &all_pids->pids[pid_index];
                    
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
                                replace_atsp_with_attp(curr_pid->init);
                            } else {
                                ESP_LOGE(TAG, "Failed to allocate memory for init");
                            }
                        }
                    }

                    if(period_item && period_item->valuestring && strlen(period_item->valuestring) > 0)
                        curr_pid->period = atoi(period_item->valuestring);
                    else if(period_item && period_item->valueint)
                        curr_pid->period = (uint32_t)period_item->valueint;
                    else
                        curr_pid->period = 10000; // Default to 10 seconds if not specified

                    curr_pid->rxheader = rxheader_item ? strdup_psram(rxheader_item->valuestring) : NULL;
                    curr_pid->pid_type = PID_CUSTOM;

                    curr_pid->parameters_count = 1;
                    curr_pid->parameters = (parameter_t*)calloc(1, sizeof(parameter_t));
                    if (curr_pid->parameters) {
                        curr_pid->parameters->name = name_item ? strdup_psram(name_item->valuestring) : NULL;
                        curr_pid->parameters->expression = expr_item ? strdup_psram(expr_item->valuestring) : NULL;
                        curr_pid->parameters->period = period_item ? atoi(period_item->valuestring) : 0;
                        curr_pid->parameters->destination = send_to_item ? strdup_psram(send_to_item->valuestring) : NULL;
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
                            strdup_psram(unit_item->valuestring) : strdup_psram("none");
                        curr_pid->parameters->class = class_item && class_item->valuestring ? 
                            strdup_psram(class_item->valuestring) : strdup_psram("none");
                    }
                    
                    pid_index++;
                }
            }
            
            // Load standard pids
            cJSON* std_pids = cJSON_GetObjectItem(root, "std_pids");
            if (std_pids) {
                cJSON* pid;
                cJSON_ArrayForEach(pid, std_pids) {
                    pid_data_t* curr_pid = &all_pids->pids[pid_index];
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

                        curr_pid->parameters->name = name_item ? strdup_psram(name_item->valuestring) : NULL;
                        curr_pid->parameters->period = period_item ? atoi(period_item->valuestring) : 10000;
                        curr_pid->parameters->destination = send_to_item ? strdup_psram(send_to_item->valuestring) : NULL;
                        curr_pid->parameters->destination_type = type_item && type_item->valuestring ? 
                            (strcmp(type_item->valuestring, "MQTT_Topic") == 0 ? DEST_MQTT_TOPIC :
                            strcmp(type_item->valuestring, "MQTT_WallBox") == 0 ? DEST_MQTT_WALLBOX :
                            DEST_DEFAULT) : DEST_DEFAULT;
                        curr_pid->parameters->timer = 0;
                        curr_pid->parameters->value = FLT_MAX;
                        curr_pid->parameters->min = FLT_MAX;
                        curr_pid->parameters->max = FLT_MAX;
                        curr_pid->parameters->sensor_type = sensor_type_item ? 
                            (strcmp(sensor_type_item->valuestring, "binary") == 0 ? BINARY_SENSOR : SENSOR) : SENSOR;
                            
                        curr_pid->rxheader = rxheader_item ? strdup_psram(rxheader_item->valuestring) : NULL;

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

                        if(is_protocol_68 || is_protocol_79)
                        {
                            if(curr_pid->rxheader != NULL && strlen(curr_pid->rxheader) > 0)
                            {
                                ESP_LOGI(TAG, "Setting up STD init buffer with protocol: %s, SH value: %s, RX header: %s", all_pids->std_ecu_protocol, sh_value, curr_pid->rxheader);
                                snprintf(std_init_buf, sizeof(std_init_buf), "ATTP%s\rATSH%s\rATCRA%s\r",
                                                            all_pids->std_ecu_protocol, sh_value, curr_pid->rxheader);
                            }
                            else
                            {
                                ESP_LOGI(TAG, "Setting up STD init buffer with protocol: %s, SH value: %s", all_pids->std_ecu_protocol, sh_value);
                                snprintf(std_init_buf, sizeof(std_init_buf), "ATTP%s\rATSH%s\rATCRA\r",
                                                            all_pids->std_ecu_protocol, sh_value);                        
                            }
                            all_pids->standard_init = strdup_psram(std_init_buf);
                        }
                        else
                        {
                            all_pids->standard_init = strdup_psram("ATTP0\r  ");
                        }

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
                                        curr_pid->parameters->class = strdup_psram(pid_info->params[i].class);
                                        curr_pid->parameters->unit = strdup_psram(pid_info->params[i].unit);
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
                        all_pids->specific_init = strdup_psram(init_item->valuestring);
                        if (init_item && init_item->valuestring) {
                            all_pids->specific_init = strdup_psram(init_item->valuestring);
                            if (all_pids->specific_init) {
                                // First replace semicolons with carriage returns
                                for (size_t j = 0; j < strlen(all_pids->specific_init); j++) {
                                    if (all_pids->specific_init[j] == ';') {
                                        all_pids->specific_init[j] = '\r';
                                    }
                                }
                                replace_atsp_with_attp(all_pids->specific_init);
                            }
                        } else {
                            all_pids->specific_init = NULL;
                        }
                    }
                    
                    cJSON* pids = cJSON_GetObjectItem(car, "pids");

                    if (pids) 
                    {
                        cJSON* pid;
                        
                        cJSON_ArrayForEach(pid, pids) 
                        {
                            pid_data_t* curr_pid = &all_pids->pids[pid_index];
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
                                        replace_atsp_with_attp(curr_pid->init);
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
                                    curr_pid->parameters[param_index].name = name_item ? strdup_psram(name_item->valuestring) : NULL;

                                    cJSON* expr_item = cJSON_GetObjectItem(param, "expression");
                                    curr_pid->parameters[param_index].expression = expr_item ? strdup_psram(expr_item->valuestring) : NULL;

                                    cJSON* unit_item = cJSON_GetObjectItem(param, "unit");
                                    curr_pid->parameters[param_index].unit = unit_item && unit_item->valuestring ? 
                                        strdup_psram(unit_item->valuestring) : strdup_psram("none");

                                    cJSON* class_item = cJSON_GetObjectItem(param, "class");
                                    curr_pid->parameters[param_index].class = class_item && class_item->valuestring ? 
                                        strdup_psram(class_item->valuestring) : strdup_psram("none");

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
                                    curr_pid->parameters[param_index].destination = send_to_item ? strdup_psram(send_to_item->valuestring) : strdup_psram("none");

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

static void autopid_init_obd_logger(uint32_t log_period)
{
    ESP_LOGI(TAG, "Initializing Autopid OBD logger...");

    // Prepare parameters from autopid for the OBD logger
    obd_param_entry_t *params = NULL;
    size_t param_count = 0;

    // Allocate memory for all possible parameters
    size_t max_params = 0;
    for (uint32_t i = 0; i < all_pids->pid_count; i++) {
        max_params += all_pids->pids[i].parameters_count;
    }
    
    params = malloc(sizeof(obd_param_entry_t) * max_params);
    if (!params) {
        ESP_LOGE(TAG, "Failed to allocate memory for OBD logger parameters");
        return;
    }
    
    // Convert autopid parameters to OBD logger format
    for (uint32_t i = 0; i < all_pids->pid_count; i++) {
        pid_data_t *pid = &all_pids->pids[i];
        
        for (uint32_t j = 0; j < pid->parameters_count; j++) {
            parameter_t *param = &pid->parameters[j];
            
            // Create metadata JSON
            char *metadata = malloc(1024*4);
            if (!metadata) {
                continue;
            }
            
            // snprintf(metadata, 256, 
            //         "{\"unit\":\"%s\",\"min\":%f,\"max\":%f,\"period\":%lu}", 
            //         param->unit ? param->unit : "", 
            //         param->min, param->max, param->period);
            snprintf(metadata, 1024*4, 
                "{\"unit\":\"%s\",\"period\":%lu}", 
                param->unit ? param->unit : "", param->period);
    
            // Add parameter to list
            params[param_count].name = strdup_psram(param->name);
            params[param_count].type = "NUMERIC";
            params[param_count].metadata = metadata;
            param_count++;
        }
    }
    
    // Initialize OBD logger with these parameters
    // obd_logger_init_params(params, param_count);
    
    //create directory if not exists
    if (mkdir(DB_ROOT_PATH"/"DB_DIR_NAME, 0755) != 0) {
        // Ignore error if directory already exists
        if (errno != EEXIST) {
            ESP_LOGE(TAG, "Failed to create directory %s: %s", DB_ROOT_PATH"/"DB_DIR_NAME, strerror(errno));
        }
    } else {
        ESP_LOGI(TAG, "Created directory: %s", DB_ROOT_PATH"/"DB_DIR_NAME);
    }


    static obd_logger_t obd_logger = {
        .path = DB_ROOT_PATH"/"DB_DIR_NAME,
        .db_filename = DB_ROOT_PATH"/"DB_DIR_NAME"/"DB_DIR_NAME,
        .obd_logger_get_params_cb = autopid_data_read
    };
    obd_logger.period_sec = log_period;
    obd_logger.obd_logger_params = params;
    obd_logger.obd_logger_params_count = param_count;
        
    if(odb_logger_init(&obd_logger) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize OBD logger");
        return;
    }

    // Free allocated memory
    for (size_t i = 0; i < param_count; i++) {
        free((void*)params[i].name);
        free((void*)params[i].metadata);
    }
    free(params);

    ESP_LOGI(TAG, "OBD logger initialized with %zu parameters", param_count);
}

void print_pids(all_pids_t* all_pids) {
    const char* pid_type_str[] = {"Standard", "Custom", "Specific"};
    
    printf("Total PIDs: %lu\n", all_pids->pid_count);
    printf("Custom Init: %s\n", all_pids->custom_init);
    printf("Specific Init: %s\n", all_pids->specific_init);
    
    for (int i = 0; i < all_pids->pid_count; i++) {
        pid_data_t* pid = &all_pids->pids[i];
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

void autopid_init(char* id, bool enable_logging, uint32_t logging_period)
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

    #if HARDWARE_VER == WICAN_PRO
    auto_pid_buf = (char *)heap_caps_malloc(AUTOPID_BUFFER_SIZE, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(auto_pid_buf == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate auto_pid_buf in PSRAM");
        return;
    }
    memset(auto_pid_buf, 0, AUTOPID_BUFFER_SIZE);

    elm327_autopid_cmd_buffer = (char *)heap_caps_malloc(AUTOPID_BUFFER_SIZE, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(elm327_autopid_cmd_buffer == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate elm327_autopid_cmd_buffer in PSRAM");
        return;
    }
    memset(elm327_autopid_cmd_buffer, 0, AUTOPID_BUFFER_SIZE);
    #else
    auto_pid_buf = (char *)malloc(AUTOPID_BUFFER_SIZE);     
    #endif

    // Define static queue storage and structure
    static StaticQueue_t autopidQueue_buffer;
    static uint8_t* autopidQueue_storage;

    // Allocate queue storage in PSRAM
    autopidQueue_storage = (uint8_t *)heap_caps_malloc(QUEUE_SIZE * sizeof(response_t), MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (autopidQueue_storage == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate queue storage in PSRAM");
        return;
    }
    memset(autopidQueue_storage, 0, QUEUE_SIZE * sizeof(response_t));
    // Create a static queue
    autopidQueue = xQueueCreateStatic(QUEUE_SIZE, 
                                     sizeof(response_t), 
                                     autopidQueue_storage, 
                                     &autopidQueue_buffer);
    if (autopidQueue == NULL)
    {
        ESP_LOGE(TAG, "Failed to create static queue");
        return;
    }

    // Define static queue storage and structure for protocol number
    static StaticQueue_t protocolQueue_buffer;
    static uint8_t protocolQueue_storage[1 * sizeof(int32_t)];

    // Create a static queue for protocol number
    protocolnumberQueue = xQueueCreateStatic(1, sizeof(int32_t), protocolQueue_storage, &protocolQueue_buffer);

    if(protocolnumberQueue == NULL)
    {
        ESP_LOGE(TAG, "Failed to create static protocol queue");
        return;
    }
    autopid_set_protocol_number(-1); // Initialize with -1

    all_pids = load_all_pids();
    
    if (all_pids)
    {
        all_pids->mutex = xSemaphoreCreateMutex();

        // if(all_pids->pid_count > 0){
        //     print_pids(all_pids); //broken
        // }

        if (!all_pids->mutex)
        {
            ESP_LOGE(TAG, "Failed to create all_pids mutex");
            return;
        }
        autopid_data.mutex = xSemaphoreCreateMutex();
        if (!autopid_data.mutex)
        {
            ESP_LOGE(TAG, "Failed to create autopid_data mutex");
            return;
        }
    }
    else
    {
        ESP_LOGE(TAG, "all_pids is NULL");
        return;
    }

    if(all_pids->pid_count == 0)
    {
        ESP_LOGE(TAG, "No PIDs found in car_data.json or auto_pid.json");
        return;
    }
    // autopid_load_config(config_str);
    // // char *desired_car_model = "Toyota Camry";
    // if(car.car_specific_en && car.selected_car_model != NULL)d
    // {
    //     autopid_load_car_specific(car.selected_car_model);
    // }
    // else
    // {
    //     car.pid_count = 0;
    // }

    if(dev_status_is_bit_set(DEV_SDCARD_MOUNTED_BIT)){
        ESP_LOGI(TAG, "SD Card mounted");
    }
    else
    {
        ESP_LOGI(TAG, "SD Card not mounted");
    }
    
    // Initialize OBD logger if enabled and SD card is mounted
    if(enable_logging && dev_status_is_bit_set(DEV_SDCARD_MOUNTED_BIT)){
        xSemaphoreTake(all_pids->mutex, portMAX_DELAY);
        autopid_init_obd_logger(logging_period);
        xSemaphoreGive(all_pids->mutex);
    }

    static StackType_t *autopid_task_stack;
    static StaticTask_t autopid_task_buffer;
    
    // Allocate stack memory in PSRAM
    autopid_task_stack = heap_caps_malloc(5000 * sizeof(StackType_t), MALLOC_CAP_SPIRAM);
    if(autopid_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate autopid_task stack in PSRAM");
        return;
    }
    memset(autopid_task_stack, 0, 5000 * sizeof(StackType_t));
    // Check if memory allocation was successful
    if (autopid_task_stack != NULL){
        // Create task with static allocation
        xTaskCreateStatic(autopid_task, "autopid_task", 5000, (void *)AF_INET, 5, 
                         autopid_task_stack, &autopid_task_buffer);
    }
    else 
    {
        // Handle memory allocation failure
        ESP_LOGE(TAG, "Failed to allocate autopid_task stack in PSRAM");
    }

    if(dev_status_is_smartconnect_enabled())
    {
        dev_status_clear_autopid_enabled(); //controller by smartconnect
        ESP_LOGI(TAG, "Autopid is controlled by SmartConnect, disabling autopid");
    }
    else
    {
        dev_status_set_autopid_enabled();
        ESP_LOGI(TAG, "Autopid enabled");
    }
}
