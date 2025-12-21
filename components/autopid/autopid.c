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
#include <ctype.h>
#include <string.h>
#include <strings.h>
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
#include "ha_webhooks.h"
#include "autopid_config.h"

// #define TAG __func__
#define TAG "AUTO_PID"
// #define TAG __func__
#define TAG "AUTO_PID"

#define TEMP_BUFFER_LENGTH 32
#define ECU_CONNECTED_BIT BIT0

static char *auto_pid_buf;
static QueueHandle_t autopidQueue;
static char *device_id;
static EventGroupHandle_t xautopid_event_group = NULL;
static StaticEventGroup_t xautopid_event_group_buffer;
static autopid_config_t *autopid_config = NULL;
static response_t elm327_response;
static autopid_data_t autopid_data = {.json_str = NULL, .mutex = NULL};
static QueueHandle_t protocolnumberQueue = NULL;
// Cached configuration JSON (built once after autopid_config is loaded)
static char *autopid_config_json = NULL;
static StaticTimer_t autopid_bit_set_timer_buffer;
static TimerHandle_t autopid_bit_set_timer_handle = NULL;

#if HARDWARE_VER == WICAN_PRO
static char *elm327_autopid_cmd_buffer;
static uint32_t elm327_autopid_cmd_buffer_len = 0;
static int64_t elm327_autopid_last_cmd_time = 0;
#endif

static const uint8_t autopid_protocol_header_length[] = {
    0, // 0: Automatic
    3, // 1: SAE J1850 PWM (41.6 kbaud)
    3, // 2: SAE J1850 VPW (10.4 kbaud)
    3, // 3: ISO 9141-2 (5 baud init, 10.4 kbaud)
    3, // 4: ISO 14230-4 KWP (5 baud init, 10.4 kbaud)
    3, // 5: ISO 14230-4 KWP (fast init, 10.4 kbaud)
    0, // 6: ISO 15765-4 CAN (11 bit ID, 500 kbaud)
    9, // 7: ISO 15765-4 CAN (29 bit ID, 500 kbaud)
    0, // 8: ISO 15765-4 CAN (11 bit ID, 250 kbaud)
    9, // 9: ISO 15765-4 CAN (29 bit ID, 250 kbaud)
    9, // A: SAE J1939 CAN (29 bit ID, 250 kbaud)
    0, // B: USER1 CAN (11 bit ID, 125 kbaud)
    0  // C: USER2 CAN (29 bit ID, 50 kbaud)
};

// Helper functions
//  Custom printer function to format numbers with 2 decimal places
char *formatNumberPrecision(double num)
{
    static char buf[32];
    snprintf(buf, sizeof(buf), "%.2f", num);

    // Remove trailing zeros after decimal point
    size_t len = strlen(buf);
    if (strchr(buf, '.'))
    {
        while (len > 0 && buf[len - 1] == '0')
        {
            buf[--len] = '\0';
        }
        if (len > 0 && buf[len - 1] == '.')
        {
            buf[--len] = '\0';
        }
    }
    return buf;
}
// strdup_psram
static char *strdup_psram(const char *s)
{
    if (!s)
        return NULL;

    size_t len = strlen(s) + 1;
    char *copy = heap_caps_malloc(len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!copy)
        return NULL;

    memcpy(copy, s, len);
    return copy;
}

// Recursively limit decimal precision in the JSON structure
void limitJsonDecimalPrecision(cJSON *item)
{
    if (!item)
        return;

    // If current item is a number, modify its value
    if (cJSON_IsNumber(item))
    {
        char *formatted = formatNumberPrecision(item->valuedouble);
        item->valuedouble = atof(formatted);
        item->valuestring = NULL; // Force cJSON to use valuedouble
    }

    // Process all children
    cJSON *child = item->child;
    while (child)
    {
        limitJsonDecimalPrecision(child);
        child = child->next;
    }
}

esp_err_t autopid_set_protocol_number(int32_t protocol_value)
{
    if (protocolnumberQueue == NULL)
    {
        ESP_LOGE(TAG, "Protocol queue not initialized");
        return ESP_FAIL;
    }

    // Clear any existing value in the queue
    xQueueReset(protocolnumberQueue);

    // Add the new protocol value
    if (xQueueSend(protocolnumberQueue, &protocol_value, pdMS_TO_TICKS(100)) != pdPASS)
    {
        ESP_LOGE(TAG, "Failed to set protocol number: %ld", protocol_value);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Protocol number set to: %ld", protocol_value);
    return ESP_OK;
}

esp_err_t autopid_get_protocol_number(int32_t *protocol_value)
{
    if (protocolnumberQueue == NULL)
    {
        ESP_LOGE(TAG, "Protocol queue not initialized");
        return ESP_FAIL;
    }

    if (protocol_value == NULL)
    {
        ESP_LOGE(TAG, "Invalid protocol_value pointer");
        return ESP_FAIL;
    }

    // Peek at the value without removing it from the queue
    if (xQueuePeek(protocolnumberQueue, protocol_value, 0) != pdPASS)
    {
        ESP_LOGW(TAG, "No protocol number available in queue");
        return ESP_FAIL;
    }

    ESP_LOGD(TAG, "Retrieved protocol number: %ld", *protocol_value);
    return ESP_OK;
}

const std_pid_t *get_pid_from_string(const char *pid_string)
{
    char pid_hex[3];
    uint8_t pid_value;

    // Extract first 2 characters (hex PID)
    strncpy(pid_hex, pid_string, 2);
    pid_hex[2] = '\0';

    // Convert hex string to integer
    pid_value = (uint8_t)strtol(pid_hex, NULL, 16);

    // Get the base PID info
    const std_pid_t *pid_info = get_pid(pid_value);
    if (!pid_info)
    {
        return NULL;
    }

    // If there's a parameter name specified after the dash
    if (strchr(pid_string, '-'))
    {
        const char *param_name = strchr(pid_string, '-') + 1;

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

static esp_err_t extract_signal_value(const uint8_t *data,
                                      uint8_t data_length,
                                      const std_parameter_t *param,
                                      float *result)
{
    // Validate input parameters
    if (!data || !param || !result)
    {
        return ESP_ERR_INVALID_ARG;
    }

    // Calculate which bytes we need
    uint8_t start_byte = param->bit_start / 8;
    uint8_t bytes_needed = (param->bit_length + 7) / 8;

    // Validate data length
    if (start_byte + bytes_needed > data_length)
    {
        return ESP_ERR_INVALID_SIZE;
    }

    // Extract raw value (Motorola format)
    uint32_t raw_value = 0;
    for (uint8_t i = 0; i < bytes_needed; i++)
    {
        raw_value = (raw_value << 8) | data[start_byte + i];
    }

    // Apply bit mask for the signal length
    uint32_t mask = (1ULL << param->bit_length) - 1;
    raw_value &= mask;

    // Calculate and limit physical value
    float physical_value = (float)raw_value * param->scale + param->offset;

    if (physical_value < param->min)
    {
        physical_value = param->min;
    }
    if (physical_value > param->max)
    {
        physical_value = param->max;
    }

    *result = physical_value;
    return ESP_OK;
}

static void merge_response_frames(uint8_t *data, uint32_t length, uint8_t *merged_frame)
{
    // Initialize merged frame with first 7 bytes
    for (int i = 0; i < 7; i++)
    {
        merged_frame[i] = data[i];
    }

    // Process subsequent frames
    for (int frame = 7; frame < length; frame += 7)
    {
        // Perform bitwise OR for each byte position
        for (int byte = 0; byte < 7 && (frame + byte) < length; byte++)
        {
            merged_frame[byte] |= data[frame + byte];
        }
    }
}

esp_err_t autopid_find_standard_pid(uint8_t protocol, char *available_pids, uint32_t available_pids_size)
{
    if (autopid_config == NULL || autopid_config->mutex == NULL)
    {
        ESP_LOGE(TAG, "autopid_config not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    response_t *response = NULL;
    uint32_t supported_pids = 0;
    uint8_t selected_protocol = 0;
    static const char *supported_protocols[] = {
        "ATTP0\rATCRA\r",               // Protocol 0
        "ATTP6\rATSH7DF\rATCRA\r",      // Protocol 6
        "ATTP7\rATSH18DB33F1\rATCRA\r", // Protocol 7
        "ATTP8\rATSH7DF\rATCRA\r",      // Protocol 8
        "ATTP9\rATSH18DB33F1\rATCRA\r", // Protocol 9
    };

    if (protocol >= 6 && protocol <= 9)
    {
        selected_protocol = protocol - 5; // supported_protocols index, 6-5 = 1, 7-5 = 2, 8-5 = 3, 9-5 = 4
    }
    else
    {
        selected_protocol = 0;
    }

    response = (response_t *)heap_caps_malloc(sizeof(response_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    memset(response, 0, sizeof(response_t));

    if (response == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for response");

        return ESP_ERR_NO_MEM;
    }

    xSemaphoreTake(autopid_config->mutex, portMAX_DELAY);

    cJSON *root = cJSON_CreateObject();
    cJSON *pid_array = cJSON_CreateArray();

    if ((protocol >= 6 && protocol <= 9) || protocol == 0)
    {
        ESP_LOGI(TAG, "Setting protocol %d", protocol);

        static const char *elm327_config = "atws\ratm0\rate0\rath1\ratl0\rats1\ratst96\r";
        // elm327_process_cmd((uint8_t*)elm327_config, strlen(elm327_config), &frame, &autopidQueue);
        // while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS);
        elm327_process_cmd((uint8_t *)elm327_config, strlen(elm327_config), &autopidQueue, elm327_autopid_cmd_buffer, &elm327_autopid_cmd_buffer_len, &elm327_autopid_last_cmd_time, NULL);
        const char *protocol_cmds = supported_protocols[selected_protocol];
        ESP_LOGI(TAG, "Sending protocol commands: %s", protocol_cmds);
        // elm327_process_cmd((uint8_t*)protocol_cmds, strlen(protocol_cmds), &frame, &autopidQueue);
        // while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS);
        elm327_process_cmd((uint8_t *)protocol_cmds, strlen(protocol_cmds), &autopidQueue, elm327_autopid_cmd_buffer, &elm327_autopid_cmd_buffer_len, &elm327_autopid_last_cmd_time, NULL);
        ESP_LOGI(TAG, "Protocol %d set successfully", selected_protocol);
    }
    else
    {
        ESP_LOGE(TAG, "Invalid protocol number: %d", selected_protocol);
        // elm327_unlock();
        free(response);
        xSemaphoreGive(autopid_config->mutex);
        return ESP_FAIL;
    }

    xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(100));

    const char *pid_support_cmds[] = {
        "0100\r", // PIDs 0x01-0x20
        "0120\r", // PIDs 0x21-0x40
        "0140\r", // PIDs 0x41-0x60
        "0160\r", // PIDs 0x61-0x80
        "0180\r", // PIDs 0x81-0xA0
        "01A0\r", // PIDs 0xA1-0xC0
    };

    // uint8_t proto_number = 0;

    // if(protocol == 0){
    //     if(elm327_get_protocol_number(&proto_number) == ESP_OK){
    //         ESP_LOGI(TAG, "Protocol number: %u", proto_number);
    //         autopid_set_protocol_number(proto_number);
    //     }else{
    //         ESP_LOGW(TAG, "Failed to get protocol number");
    //     }
    // }

    while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(100)) == pdPASS)
        ;
    ESP_LOGI(TAG, "Starting PID support command processing");
    for (int i = 0; i < sizeof(pid_support_cmds) / sizeof(pid_support_cmds[0]); i++)
    {
        ESP_LOGI(TAG, "Processing PID support command: %s", pid_support_cmds[i]);

        if (elm327_process_cmd((uint8_t *)pid_support_cmds[i], strlen(pid_support_cmds[i]), &autopidQueue, elm327_autopid_cmd_buffer, &elm327_autopid_cmd_buffer_len, &elm327_autopid_last_cmd_time, autopid_parser) != 0)
        {
            ESP_LOGW(TAG, "Failed to process PID support command: %s", pid_support_cmds[i]);
            continue;
        }

        if (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(10000)) == pdPASS)
        {
            ESP_LOGI(TAG, "Raw response length: %lu", response->length);
            ESP_LOG_BUFFER_HEX(TAG, response->data, response->length);

            // Skip mode byte (0x41) and PID byte
            if ((strstr((char *)response->data, "error") == NULL) && response->length >= 7)
            {
                uint8_t merged_frame[7] = {0};
                merge_response_frames(response->data, response->length, merged_frame);

                // Extract bitmap from merged frame
                supported_pids = (merged_frame[3] << 24) |
                                 (merged_frame[4] << 16) |
                                 (merged_frame[5] << 8) |
                                 merged_frame[6];

                ESP_LOGI(TAG, "Merged frame bitmap: 0x%08lx", supported_pids);

                for (int bit = 0; bit < 32; bit++)
                {
                    if (supported_pids & (1 << (31 - bit)))
                    {
                        uint8_t pid = (i * 32) + bit + 1;
                        const std_pid_t *pid_info = get_pid(pid);

                        if (pid_info)
                        {
                            char pid_str[64];

                            // If the PID has multiple parameters
                            if (pid_info->num_params > 1 && pid_info->params)
                            {
                                ESP_LOGI(TAG, "Processing multi-parameter PID: %02X", pid);
                                // Add each parameter as a separate entry
                                for (int p = 0; p < pid_info->num_params; p++)
                                {
                                    if (pid_info->params[p].name)
                                    {
                                        snprintf(pid_str, sizeof(pid_str), "%02X-%s",
                                                 pid, pid_info->params[p].name);
                                        ESP_LOGI(TAG, "PID %02X parameter %d supported: %s",
                                                 pid, p + 1, pid_str);
                                        cJSON_AddItemToArray(pid_array, cJSON_CreateString(pid_str));
                                    }
                                    else
                                    {
                                        ESP_LOGW(TAG, "PID %02X parameter %d has NULL name", pid, p + 1);
                                    }
                                }
                            }
                            else if (pid_info->params && pid_info->params[0].name)
                            {
                                // Single parameter PID
                                snprintf(pid_str, sizeof(pid_str), "%02X-%s",
                                         pid, pid_info->params[0].name);
                                ESP_LOGI(TAG, "PID %02X supported: %s", pid, pid_str);
                                cJSON_AddItemToArray(pid_array, cJSON_CreateString(pid_str));
                            }
                            else
                            {
                                ESP_LOGW(TAG, "PID %02X has invalid or NULL parameters", pid);
                            }
                        }
                    }
                }
            }
            else
            {
                ESP_LOGW(TAG, "Response length too short: %lu", response->length);
            }
        }
        else
        {
            if (response->length >= 7)
            {
                ESP_LOGW(TAG, "No response received for PID support command: %s", pid_support_cmds[i]);
            }
            else
            {
                ESP_LOGW(TAG, "Recvied error response: %s", response->data);
            }
        }
    }

    ESP_LOGI(TAG, "Adding PIDs to JSON object");
    cJSON_AddItemToObject(root, "std_pids", pid_array);

    // Convert to string and cleanup
    char *json_str = cJSON_PrintUnformatted(root);
    if (json_str)
    {
        ESP_LOGI(TAG, "JSON string created, length: %zu", strlen(json_str));
        if (strlen(json_str) < available_pids_size)
        {
            strcpy(available_pids, json_str);
            free(json_str);
            cJSON_Delete(root);

            // ESP_LOGI(TAG, "Restoring protocol settings");
            // elm327_process_cmd((uint8_t*)restore_cmd, strlen(restore_cmd), &frame, &autopidQueue);
            // while (xQueueReceive(autopidQueue, response, pdMS_TO_TICKS(1000)) == pdPASS);

            free(response);
            xSemaphoreGive(autopid_config->mutex);
            return ESP_OK;
        }
        ESP_LOGW(TAG, "JSON string too long for buffer");
        free(json_str);
    }
    else
    {
        ESP_LOGE(TAG, "Failed to create JSON string");
    }

    ESP_LOGI(TAG, "Restoring protocol settings");
    // elm327_process_cmd((uint8_t*)restore_cmd, strlen(restore_cmd), &frame, &autopidQueue);
    // while (xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS);

    cJSON_Delete(root);
    free(response);
    // elm327_unlock();
    xSemaphoreGive(autopid_config->mutex);
    return ESP_FAIL;
}

static void autopid_data_update(autopid_config_t *pids)
{
    if (!pids || !pids->mutex)
    {
        ESP_LOGE(TAG, "Invalid autopid_config or mutex");
        return;
    }

    // take autopid_data mutex
    if (xSemaphoreTake(autopid_data.mutex, portMAX_DELAY) == pdTRUE)
    {
        // free old data
        if (autopid_data.json_str != NULL)
        {
            free(autopid_data.json_str);
            autopid_data.json_str = NULL;
        }

        cJSON *root = cJSON_CreateObject();
        if (root)
        {
            for (uint32_t i = 0; i < pids->pid_count; i++)
            {
                pid_data_t *curr_pid = &pids->pids[i];
                for (uint32_t j = 0; j < curr_pid->parameters_count; j++)
                {
                    parameter_t *param = &curr_pid->parameters[j];
                    if (param->name && param->value != FLT_MAX)
                    {
                        if (param->sensor_type == BINARY_SENSOR)
                        {
                            cJSON_AddStringToObject(root, param->name, param->value > 0 ? "on" : "off");
                        }
                        else
                        {
                            cJSON_AddNumberToObject(root, param->name, param->value);
                        }
                    }
                }
            }

            // Add CAN-filter (broadcast) parameters
            for (uint32_t fi = 0; fi < pids->can_filters_count; fi++)
            {
                can_filter_t *f = &pids->can_filters[fi];
                for (uint32_t pi = 0; pi < f->parameters_count; pi++)
                {
                    parameter_t *param = &f->parameters[pi];
                    if (param->name && param->value != FLT_MAX)
                    {
                        if (param->sensor_type == BINARY_SENSOR)
                        {
                            cJSON_AddStringToObject(root, param->name, param->value > 0 ? "on" : "off");
                        }
                        else
                        {
                            cJSON_AddNumberToObject(root, param->name, param->value);
                        }
                    }
                }
            }
            limitJsonDecimalPrecision(root);
            autopid_data.json_str = cJSON_PrintUnformatted(root);
            cJSON_Delete(root);
        }

        // release mutex
        xSemaphoreGive(autopid_data.mutex);
    }
}

char *autopid_data_read(void)
{
    // json_str must be freed by the caller
    static char *json_str = NULL;

    if (!autopid_data.mutex)
    {
        ESP_LOGE(TAG, "Invalid mutex, autopid_data not initialized");
        return NULL;
    }

    if (xSemaphoreTake(autopid_data.mutex, portMAX_DELAY) == pdTRUE)
    {
        if (autopid_data.json_str != NULL)
        {
            json_str = (char *)heap_caps_malloc(strlen(autopid_data.json_str) + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (json_str != NULL)
            {
                strcpy(json_str, autopid_data.json_str);
            }
        }
        else
        {
            json_str = NULL;
        }
        xSemaphoreGive(autopid_data.mutex);
    }
    return json_str;
}

char *autopid_get_value_by_name(char *name)
{
    // json_str must be freed by the caller
    char *result_json = NULL;

    if (autopid_data.mutex == NULL)
    {
        ESP_LOGE(TAG, "Invalid mutex, autopid_data not initialized");
        return NULL;
    }
    else if (name == NULL || strlen(name) == 0)
    {
        ESP_LOGE(TAG, "Invalid name");
        return NULL;
    }

    if (xSemaphoreTake(autopid_data.mutex, portMAX_DELAY) == pdTRUE)
    {
        if (autopid_data.json_str != NULL)
        {
            char *json_copy = (char *)heap_caps_malloc(strlen(autopid_data.json_str) + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (json_copy != NULL)
            {
                strcpy(json_copy, autopid_data.json_str);

                cJSON *root = cJSON_Parse(json_copy);
                if (root)
                {
                    cJSON *item = cJSON_GetObjectItem(root, name);
                    if (item)
                    {
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

void autopid_data_publish(void)
{
    if (!autopid_config || !autopid_config->mutex)
    {
        ESP_LOGE(TAG, "Invalid autopid_config or mutex");
        return;
    }

    if (xSemaphoreTake(autopid_config->mutex, portMAX_DELAY) == pdTRUE)
    {
        cJSON *root = cJSON_CreateObject();
        if (root)
        {
            for (uint32_t i = 0; i < autopid_config->pid_count; i++)
            {
                pid_data_t *curr_pid = &autopid_config->pids[i];
                for (uint32_t j = 0; j < curr_pid->parameters_count; j++)
                {
                    parameter_t *param = &curr_pid->parameters[j];
                    if (param->name && param->value != FLT_MAX)
                    {
                        if (param->sensor_type == BINARY_SENSOR)
                        {
                            cJSON_AddStringToObject(root, param->name, param->value > 0 ? "on" : "off");
                        }
                        else
                        {
                            cJSON_AddNumberToObject(root, param->name, param->value);
                        }
                    }
                }
            }

            // Add CAN-filter (broadcast) parameters
            for (uint32_t fi = 0; fi < autopid_config->can_filters_count; fi++)
            {
                can_filter_t *f = &autopid_config->can_filters[fi];
                for (uint32_t pi = 0; pi < f->parameters_count; pi++)
                {
                    parameter_t *param = &f->parameters[pi];
                    if (param->name && param->value != FLT_MAX)
                    {
                        if (param->sensor_type == BINARY_SENSOR)
                        {
                            cJSON_AddStringToObject(root, param->name, param->value > 0 ? "on" : "off");
                        }
                        else
                        {
                            cJSON_AddNumberToObject(root, param->name, param->value);
                        }
                    }
                }
            }

            if (root->child)
            {
                limitJsonDecimalPrecision(root);
                char *json_str = cJSON_PrintUnformatted(root);
                if (json_str)
                {
                    if (autopid_config->group_destination && strlen(autopid_config->group_destination) > 0)
                    {
                        mqtt_publish(autopid_config->group_destination, json_str, 0, 0, 1);
                        ESP_LOGI(TAG, "Published to %s", autopid_config->group_destination);
                    }
                    else
                    {
                        mqtt_publish(config_server_get_mqtt_rx_topic(), json_str, 0, 0, 1);
                    }
                    free(json_str);
                }
            }
            else
            {
                ESP_LOGW(TAG, "No valid parameters found to publish");
            }

            cJSON_Delete(root);
        }
        xSemaphoreGive(autopid_config->mutex);
    }
}

bool autopid_get_ecu_status(void)
{
    EventBits_t uxBits;
    if (xautopid_event_group != NULL)
    {
        uxBits = xEventGroupGetBits(xautopid_event_group);

        return (uxBits & ECU_CONNECTED_BIT);
    }
    else
        return false;
}

// Map destination_type_t to string for logging
static const char *dest_type_str(destination_type_t t)
{
    switch (t)
    {
    case DEST_MQTT_TOPIC:
        return "MQTT_Topic";
    case DEST_MQTT_WALLBOX:
        return "MQTT_WallBox";
    case DEST_HTTP:
        return "HTTP";
    case DEST_HTTPS:
        return "HTTPS";
    case DEST_ABRP_API:
        return "ABRP_API";
    default:
        return "Default";
    }
}

// URL encode a string for form data transmission
static char *url_encode_string(const char *input)
{
    if (!input)
        return NULL;

    size_t input_len = strlen(input);
    size_t encoded_len = input_len * 3 + 1; // worst case for URL encoding
    char *encoded = heap_caps_malloc(encoded_len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!encoded)
        return NULL;

    size_t j = 0;
    for (size_t k = 0; k < input_len && j < encoded_len - 1; k++)
    {
        char c = input[k];
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
            c == '-' || c == '_' || c == '.' || c == '~')
        {
            encoded[j++] = c;
        }
        else
        {
            if (j < encoded_len - 3)
            {
                sprintf(&encoded[j], "%%%02X", (unsigned char)c);
                j += 3;
            }
        }
    }
    encoded[j] = '\0';

    return encoded;
}

// Helper to copy/map a value from src to tlm with type normalization for ABRP
static void abrp_add_mapped(cJSON *src, cJSON *tlm, const char *from_key, const char *to_key)
{
    if (!src || !tlm || !from_key || !to_key)
        return;
    cJSON *it = cJSON_GetObjectItemCaseSensitive(src, from_key);
    if (!it)
        return;
    if (cJSON_IsNumber(it))
    {
        cJSON_AddNumberToObject(tlm, to_key, it->valuedouble);
    }
    else if (cJSON_IsBool(it))
    {
        cJSON_AddNumberToObject(tlm, to_key, cJSON_IsTrue(it) ? 1 : 0);
    }
    else if (cJSON_IsString(it) && it->valuestring)
    {
        const char *s = it->valuestring;
        if (strcasecmp(s, "on") == 0)
        {
            cJSON_AddNumberToObject(tlm, to_key, 1);
        }
        else if (strcasecmp(s, "off") == 0)
        {
            cJSON_AddNumberToObject(tlm, to_key, 0);
        }
        else
        {
            char *endp = NULL;
            double val = strtod(s, &endp);
            if (endp && endp != s)
            {
                cJSON_AddNumberToObject(tlm, to_key, val);
            }
            else
            {
                cJSON_AddStringToObject(tlm, to_key, s);
            }
        }
    }
}

// Build ABRP telemetry data: creates flat telemetry object (not wrapped) for URL encoding
static char *build_abrp_payload(const char *raw_json, const char *car_model)
{
    if (!raw_json)
        return NULL;

    // Parse incoming telemetry snapshot; if parsing fails, return NULL
    cJSON *src = cJSON_Parse(raw_json);
    if (!src)
    {
        ESP_LOGE(TAG, "Failed to parse raw JSON for ABRP telemetry");
        return NULL;
    }

    // Mapping between internal names and ABRP keys
    typedef struct
    {
        const char *from;
        const char *to;
    } param_map_t;
    static const param_map_t param_map[] = {
        {"SOC", "soc"},
        {"HV_W", "power"},
        {"SPEED", "speed"},
        {"CHARGING", "is_charging"},
        {"CHARGING_DC", "is_dcfc"},
        {"PARK_BRAKE", "is_parked"},
        {"HV_CAPACITY_KWH", "capacity"},
        {"HV_CAPACITY_R", "soe"},
        {"SOH", "soh"},
        {"TMP_A", "ext_temp"},
        {"BATT_TEMP", "batt_temp"},
        {"HV_V", "voltage"},
        {"HV_A", "current"},
        {"ODOMETER", "odometer"},
        {"RANGE", "est_battery_range"},
        {"T_CAB", "cabin_temp"},
        {"TYRE_P_FL", "tire_pressure_fl"},
        {"TYRE_P_FR", "tire_pressure_fr"},
        {"TYRE_P_RL", "tire_pressure_rl"},
        {"TYRE_P_RR", "tire_pressure_rr"},
    };

    // Destination tlm object
    cJSON *tlm = cJSON_CreateObject();
    if (!tlm)
    {
        cJSON_Delete(src);
        return NULL;
    }

    // Apply mapping
    for (size_t i = 0; i < sizeof(param_map) / sizeof(param_map[0]); i++)
    {
        abrp_add_mapped(src, tlm, param_map[i].from, param_map[i].to);
    }

    // Pass-through commonly accepted ABRP extras if already present
    const char *passthrough_keys[] = {"lat", "lon", "elevation"};
    for (size_t i = 0; i < sizeof(passthrough_keys) / sizeof(passthrough_keys[0]); i++)
    {
        const char *k = passthrough_keys[i];
        if (!cJSON_GetObjectItemCaseSensitive(tlm, k))
        {
            cJSON *it = cJSON_GetObjectItemCaseSensitive(src, k);
            if (it)
            {
                if (cJSON_IsNumber(it))
                    cJSON_AddNumberToObject(tlm, k, it->valuedouble);
                else if (cJSON_IsString(it) && it->valuestring)
                {
                    char *endp = NULL;
                    double v = strtod(it->valuestring, &endp);
                    if (endp && endp != it->valuestring)
                        cJSON_AddNumberToObject(tlm, k, v);
                    else
                        cJSON_AddStringToObject(tlm, k, it->valuestring);
                }
            }
        }
    }

    // Ensure utc present (seconds since epoch)
    if (!cJSON_GetObjectItemCaseSensitive(tlm, "utc"))
    {
        time_t now = 0;
        time(&now);
        cJSON_AddNumberToObject(tlm, "utc", (double)now);
    }
    // Ensure car_model inside tlm if provided and absent
    // if(car_model && *car_model && !cJSON_GetObjectItemCaseSensitive(tlm, "car_model")){
    //     cJSON_AddStringToObject(tlm, "car_model", car_model);
    // }

    // Return tlm object directly (not wrapped) for URL encoding as tlm parameter
    char *printed = cJSON_PrintUnformatted(tlm);
    char *buf_psram = NULL;
    if (printed)
    {
        buf_psram = heap_caps_malloc(strlen(printed) + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (buf_psram)
        {
            strcpy(buf_psram, printed);
        }
        free(printed);
    }
    cJSON_Delete(tlm);
    cJSON_Delete(src);
    return buf_psram; // may be NULL if allocation failed
}

/// @brief
/// @param

void autopid_publish_all_destinations(void)
{
    if (!autopid_config)
    {
        ESP_LOGE(TAG, "autopid_publish_all_destinations: autopid_config NULL");
        return;
    }
    if (strcmp("enable", autopid_config->grouping) != 0)
    {
        return; // grouping disabled
    }

    // Get snapshot JSON (caller must free)
    char *raw_json = autopid_data_read();
    if (!raw_json)
    {
        ESP_LOGW(TAG, "No autopid data to publish");
        return;
    }

    // Current time not directly needed with wc_timer; timers store absolute expiry in us

    // Legacy single destination path if no multi-destinations parsed
    // if(autopid_config->destinations_count == 0){
    //     if(autopid_config->group_destination_type == DEST_MQTT_TOPIC){
    //         if(autopid_config->group_destination && strlen(autopid_config->group_destination)>0){
    //             mqtt_publish(autopid_config->group_destination, raw_json, 0, 0, 1);
    //         }else{
    //             mqtt_publish(config_server_get_mqtt_rx_topic(), raw_json, 0, 0, 1);
    //         }
    //     }
    //     free(raw_json);
    //     return;
    // }

    for (uint32_t i = 0; i < autopid_config->destinations_count; i++)
    {
        group_destination_t *gd = &autopid_config->destinations[i];
        if (!gd->enabled)
            continue;

        // Determine base cycle and current backoff adjusted cycle
        uint32_t base_cycle = gd->cycle > 0 ? gd->cycle : 10000;
        uint32_t effective_cycle = base_cycle;
        if (gd->backoff_ms && gd->backoff_ms > base_cycle)
        {
            effective_cycle = gd->backoff_ms; // apply backoff delay
        }
        // If we have an effective cycle (>0) use publish_timer; if timer not set (0) schedule immediate publish
        if (effective_cycle > 0)
        {
            if (gd->publish_timer != 0 && !wc_timer_is_expired(&gd->publish_timer))
            {
                // Not yet time
                continue;
            }
        }

        const char *dest = gd->destination ? gd->destination : "";
        switch (gd->type)
        {
        case DEST_MQTT_TOPIC:
        case DEST_DEFAULT: // treat as MQTT default topic
            if (dest[0] != '\0')
            {
                mqtt_publish(dest, raw_json, 0, 0, 1);
            }
            else
            {
                mqtt_publish(config_server_get_mqtt_rx_topic(), raw_json, 0, 0, 1);
            }
            ESP_LOGI(TAG, "Published MQTT (%s) to %s", dest_type_str(gd->type), dest[0] ? dest : config_server_get_mqtt_rx_topic());
            break;
        case DEST_MQTT_WALLBOX:
        {
            // For now replicate same JSON publish (future: custom format)
            if (dest[0] != '\0')
            {
                mqtt_publish(dest, raw_json, 0, 0, 1);
            }
            else
            {
                mqtt_publish(config_server_get_mqtt_rx_topic(), raw_json, 0, 0, 1);
            }
            ESP_LOGI(TAG, "Published MQTT WallBox to %s", dest[0] ? dest : config_server_get_mqtt_rx_topic());
            break;
        }
        case DEST_HTTP:
        case DEST_HTTPS:
        {
            // Build URL for standard HTTP/HTTPS endpoints
            char *url = dest[0] ? strdup_psram(dest) : NULL;
            static bool settings_sent = false;

            if (!url)
            {
                ESP_LOGW(TAG, "Destination %u missing URL", i);
                break;
            }

            char *body = NULL;
            char *current_autopid_data = NULL;
            char *current_config_data = NULL;
            char *current_status_data = NULL;

            if (settings_sent == true)
            {
                // Wrap telemetry under "autopid_data" for standard HTTP/HTTPS after initial settings
                cJSON *root_obj = cJSON_CreateObject();
                if (root_obj)
                {
                    cJSON *auto_obj = NULL;
                    if (raw_json)
                    {
                        auto_obj = cJSON_Parse(raw_json);
                    }
                    if (!auto_obj)
                        auto_obj = cJSON_CreateObject();
                    cJSON_AddItemToObject(root_obj, "autopid_data", auto_obj);

                    char *printed = cJSON_PrintUnformatted(root_obj);
                    if (printed)
                    {
                        body = strdup_psram(printed);
                        free(printed);
                    }
                    cJSON_Delete(root_obj);
                }
            }
            else
            {
                // Use settings JSON body for standard HTTP/HTTPS
                current_status_data = config_server_get_status_json(true);
                current_config_data = autopid_get_config();
                current_autopid_data = strdup_psram(raw_json);

                cJSON *root_obj = cJSON_CreateObject();
                if (root_obj)
                {
                    cJSON *cfg_obj = NULL;
                    cJSON *sts_obj = NULL;
                    cJSON *auto_obj = NULL;

                    if (current_config_data)
                    {
                        cfg_obj = cJSON_Parse(current_config_data);
                    }
                    if (!cfg_obj)
                        cfg_obj = cJSON_CreateObject();

                    if (current_status_data)
                    {
                        sts_obj = cJSON_Parse(current_status_data);
                    }
                    if (!sts_obj)
                        sts_obj = cJSON_CreateObject();

                    if (current_autopid_data)
                    {
                        auto_obj = cJSON_Parse(current_autopid_data);
                    }
                    if (!auto_obj)
                        auto_obj = cJSON_CreateObject();

                    cJSON_AddItemToObject(root_obj, "config", cfg_obj);
                    cJSON_AddItemToObject(root_obj, "status", sts_obj);
                    cJSON_AddItemToObject(root_obj, "autopid_data", auto_obj);

                    char *printed = cJSON_PrintUnformatted(root_obj);
                    if (printed)
                    {
                        body = strdup_psram(printed);
                        free(printed);
                    }
                    cJSON_Delete(root_obj);
                }

                if (current_autopid_data)
                {
                    free(current_autopid_data);
                    current_autopid_data = NULL;
                }
                if (current_status_data)
                {
                    free(current_status_data);
                    current_status_data = NULL;
                }
            }

            if (!body)
            {
                ESP_LOGE(TAG, "Failed to allocate body");
                free(url);
                break;
            }

            https_client_mgr_config_t cfg = {0};
            cfg.url = url;
            cfg.timeout_ms = 2000;

            // Detect scheme from URL
            bool is_https_url = (strncasecmp(url, "https://", 8) == 0);
            if (is_https_url)
            {
                // HTTPS: use cert set if provided and not default, otherwise use built-in bundle
                if (gd->cert_set && strcmp(gd->cert_set, "default") != 0)
                {
                    size_t ca_len = 0, cli_len = 0, key_len = 0;
                    const char *ca = cert_manager_get_set_ca_ptr(gd->cert_set, &ca_len);
                    const char *cli = cert_manager_get_set_client_cert_ptr(gd->cert_set, &cli_len);
                    const char *key = cert_manager_get_set_client_key_ptr(gd->cert_set, &key_len);
                    if (ca)
                    {
                        cfg.cert_pem = ca;
                        cfg.cert_len = ca_len;
                        ESP_LOGI(TAG, "Using CA cert from set '%s' for HTTPS", gd->cert_set);
                    }
                    else
                    {
                        cfg.use_crt_bundle = true;
                        ESP_LOGW(TAG, "No CA cert in set '%s', using built-in bundle for HTTPS", gd->cert_set);
                    }
                    if (cli && key)
                    {
                        cfg.client_cert_pem = cli;
                        cfg.client_cert_len = cli_len;
                        cfg.client_key_pem = key;
                        cfg.client_key_len = key_len;
                        ESP_LOGI(TAG, "Using client cert+key from set '%s' for HTTPS", gd->cert_set);
                    }
                }
                else
                {
                    cfg.use_crt_bundle = true; // use built-in bundle for default/no cert set
                    ESP_LOGI(TAG, "Using built-in certificate bundle for HTTPS");
                }
            }

            // Build auth + content type for standard HTTP/HTTPS (JSON content)
            https_client_mgr_auth_t auth = {0};
            // Map extended auth types
            switch (gd->auth.type)
            {
            case DEST_AUTH_BEARER:
                if (gd->auth.bearer && strlen(gd->auth.bearer) > 0)
                {
                    auth.bearer_token = gd->auth.bearer;
                }
                break;
            case DEST_AUTH_API_KEY_HEADER:
                if (gd->auth.api_key && strlen(gd->auth.api_key) > 0)
                {
                    auth.api_key = gd->auth.api_key;
                    auth.api_key_header_name = gd->auth.api_key_header_name; // may be NULL -> defaults to x-api-key
                }
                break;
            case DEST_AUTH_API_KEY_QUERY:
                if (gd->auth.api_key && strlen(gd->auth.api_key) > 0 && gd->auth.api_key_query_name && strlen(gd->auth.api_key_query_name) > 0)
                {
                    auth.api_key = gd->auth.api_key;
                    auth.api_key_query_name = gd->auth.api_key_query_name;
                }
                break;
            case DEST_AUTH_BASIC:
                auth.basic_username = gd->auth.basic_username;
                auth.basic_password = gd->auth.basic_password;
                break;
            case DEST_AUTH_NONE:
            default:
                break;
            }
            // Legacy fallback: if no explicit auth configured but api_token exists, use as Bearer
            if (!auth.bearer_token && gd->api_token && strlen(gd->api_token) > 0 && gd->auth.type == DEST_AUTH_NONE)
            {
                auth.bearer_token = gd->api_token;
            }
            // Append any extra query params configured in destination
            if (gd->query_params && gd->query_params_count > 0)
            {
                // Build a temporary array compatible with https_client_mgr
                size_t qp_count = gd->query_params_count;
                https_client_mgr_query_kv_t *qp = alloca(sizeof(https_client_mgr_query_kv_t) * qp_count);
                if (qp)
                {
                    for (size_t qi = 0; qi < qp_count; ++qi)
                    {
                        qp[qi].key = gd->query_params[qi].key ? gd->query_params[qi].key : "";
                        qp[qi].value = gd->query_params[qi].value ? gd->query_params[qi].value : "";
                    }
                    auth.extra_query = qp;
                    auth.extra_query_count = qp_count;
                }
            }

            // For HTTPS, if host is raw IPv4 address, skip CN verification to allow self-signed IP certs
            if (url)
            {
                const char *host_start = strstr(url, "://");
                host_start = host_start ? host_start + 3 : url;
                // host ends at '/' or end
                char host_buf[64] = {0};
                size_t hi = 0;
                while (host_start[hi] && host_start[hi] != '/' && host_start[hi] != ':' && hi < sizeof(host_buf) - 1)
                {
                    host_buf[hi] = host_start[hi];
                    hi++;
                }
                bool is_ip = true;
                for (size_t k = 0; k < hi; k++)
                {
                    if ((host_buf[k] < '0' || host_buf[k] > '9') && host_buf[k] != '.')
                    {
                        is_ip = false;
                        break;
                    }
                }
                if (is_ip)
                {
                    cfg.skip_common_name = true;
                }
            }

            https_client_mgr_response_t resp = {0};
            esp_err_t err = https_client_mgr_request_with_auth(&cfg, HTTPS_METHOD_POST,
                                                               body, strlen(body),
                                                               "application/json",
                                                               &auth,
                                                               NULL,
                                                               &resp);

            bool ok = (err == ESP_OK && resp.is_success);
            if (ok)
            {
                ESP_LOGI(TAG, "HTTP(S) dest %u status %d success", i, resp.status_code);
                gd->consec_failures = 0;
                gd->backoff_ms = 0;
                // After initial successful settings push, switch to sending raw telemetry only
                if (!settings_sent)
                {
                    settings_sent = true;
                }
            }
            else
            {
                ESP_LOGE(TAG, "HTTP(S) dest %u request failed: %s", i, esp_err_to_name(err));
                gd->consec_failures++;
                // Apply backoff logic (same as ABRP)
                if (gd->consec_failures >= 3)
                {
                    uint32_t min_cap = 30000;
                    uint32_t next = (gd->backoff_ms ? gd->backoff_ms : base_cycle);
                    if (next < min_cap)
                        next = next * 2;
                    if (next < min_cap)
                        next = min_cap;
                    uint32_t max_backoff = base_cycle * 2;
                    if (max_backoff < min_cap)
                        max_backoff = min_cap;
                    if (next > max_backoff)
                        next = max_backoff;
                    gd->backoff_ms = next;
                }
            }

            https_client_mgr_free_response(&resp);
            free(body);
            free(url);
            break;
        }
        case DEST_ABRP_API:
        {
            // Build URL (ABRP uses form data in body, not URL parameters)
            char *url = dest[0] ? strdup_psram(dest) : NULL;
            if (!url)
            {
                ESP_LOGW(TAG, "Destination %u missing URL", i);
                break;
            }

            // Build body: ABRP uses URL-encoded form data, others use JSON
            char *body = NULL;
            if (gd->type == DEST_ABRP_API)
            {
                char *tlm_data = build_abrp_payload(raw_json, autopid_config->vehicle_model);
                if (!tlm_data)
                {
                    ESP_LOGE(TAG, "Failed to build ABRP telemetry data");
                    free(url);
                    break;
                }

                // URL encode the telemetry JSON for form data
                char *encoded_tlm = url_encode_string(tlm_data);
                if (!encoded_tlm)
                {
                    ESP_LOGE(TAG, "Failed to URL encode telemetry data");
                    free(tlm_data);
                    free(url);
                    break;
                }

                // Build form data body: token=xxx&tlm=encoded_json
                size_t body_len = (gd->api_token ? strlen(gd->api_token) : 0) + strlen(encoded_tlm) + 32;
                body = heap_caps_malloc(body_len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                if (body)
                {
                    if (gd->api_token && strlen(gd->api_token) > 0)
                    {
                        snprintf(body, body_len, "token=%s&tlm=%s", gd->api_token, encoded_tlm);
                    }
                    else
                    {
                        snprintf(body, body_len, "tlm=%s", encoded_tlm);
                    }
                }

                free(encoded_tlm);
                free(tlm_data);
            }
            else
            {
                body = strdup_psram(raw_json);
            }
            if (!body)
            {
                ESP_LOGE(TAG, "Failed to allocate body");
                free(url);
                break;
            }

            https_client_mgr_config_t cfg = {0};
            cfg.url = url;
            // Log destination URL plus current system time (epoch + ISO8601 UTC) for TLS timing diagnostics
            time_t _now = 0;
            time(&_now);
            struct tm _utc;
            gmtime_r(&_now, &_utc);
            char _tbuf[32];
            strftime(_tbuf, sizeof(_tbuf), "%Y-%m-%dT%H:%M:%SZ", &_utc);
            ESP_LOGI(TAG, "HTTP(S) dest %u URL: %s (epoch=%ld utc=%s)", i, url, (long)_now, _tbuf);
            cfg.timeout_ms = 2000;
            // ABRP API is always HTTPS - configure certificates appropriately
            // if(gd->cert_set && strcmp(gd->cert_set, "default") != 0){
            //     size_t ca_len = 0, cli_len = 0, key_len = 0;
            //     const char *ca = cert_manager_get_set_ca_ptr(gd->cert_set, &ca_len);
            //     const char *cli = cert_manager_get_set_client_cert_ptr(gd->cert_set, &cli_len);
            //     const char *key = cert_manager_get_set_client_key_ptr(gd->cert_set, &key_len);
            //     if(ca){
            //         cfg.cert_pem = ca; cfg.cert_len = ca_len;
            //         ESP_LOGI(TAG, "Using CA cert from set '%s' for ABRP", gd->cert_set);
            //     } else {
            //         cfg.use_crt_bundle = true;
            //         ESP_LOGW(TAG, "No CA cert in set '%s', using built-in bundle for ABRP", gd->cert_set);
            //     }
            //     if(cli && key){
            //         cfg.client_cert_pem = cli; cfg.client_cert_len = cli_len;
            //         cfg.client_key_pem = key; cfg.client_key_len = key_len;
            //         ESP_LOGI(TAG, "Using client cert+key from set '%s' for ABRP", gd->cert_set);
            //     }
            // } else {
            //     cfg.use_crt_bundle = true; // use built-in bundle for default/no cert set
            //     ESP_LOGI(TAG, "Using built-in certificate bundle for ABRP");
            // }
            cfg.use_crt_bundle = true; // ABRP always uses HTTPS with cert bundle by default
            // Build content-type; ABRP expects x-www-form-urlencoded and token in body, not Authorization header
            const char *content_type = (gd->type == DEST_ABRP_API)
                                           ? "application/x-www-form-urlencoded"
                                           : "application/json";

            https_client_mgr_response_t resp = {0};
            // For ABRP (always HTTPS), if host is raw IPv4 address, skip CN verification to allow self-signed IP certs
            if (url)
            {
                const char *host_start = strstr(url, "://");
                host_start = host_start ? host_start + 3 : url;
                // host ends at '/' or end
                char host_buf[64] = {0};
                size_t hi = 0;
                while (host_start[hi] && host_start[hi] != '/' && host_start[hi] != ':' && hi < sizeof(host_buf) - 1)
                {
                    host_buf[hi] = host_start[hi];
                    hi++;
                }
                bool is_ip = true;
                for (size_t k = 0; k < hi; k++)
                {
                    if ((host_buf[k] < '0' || host_buf[k] > '9') && host_buf[k] != '.')
                    {
                        is_ip = false;
                        break;
                    }
                }
                if (is_ip)
                {
                    cfg.skip_common_name = true;
                }
            }

            // No auth struct here for ABRP unless configured elsewhere; token is already included in body
            esp_err_t err = https_client_mgr_request_with_auth(&cfg, HTTPS_METHOD_POST,
                                                               body, strlen(body),
                                                               content_type,
                                                               NULL,
                                                               NULL,
                                                               &resp);
            bool ok = false;
            if (err == ESP_OK)
            {
                ESP_LOGI(TAG, "HTTP(S) dest %u status %d success=%d", i, resp.status_code, resp.is_success);
                if (gd->type == DEST_ABRP_API)
                {
                    // ABRP returns HTTP 200 even on logical errors; inspect JSON {status, result}
                    if (resp.data && resp.data_len > 0)
                    {
                        cJSON *jr = cJSON_Parse(resp.data);
                        if (jr)
                        {
                            cJSON *st = cJSON_GetObjectItemCaseSensitive(jr, "status");
                            const char *st_str = cJSON_IsString(st) ? st->valuestring : NULL;
                            ok = (st_str && (strcmp(st_str, "ok") == 0));
                            ESP_LOGI(TAG, "ABRP status: %s", st_str ? st_str : "<none>");
                            cJSON_Delete(jr);
                        }
                        else
                        {
                            ESP_LOGW(TAG, "ABRP response not JSON parseable");
                            ok = false;
                        }
                    }
                    else
                    {
                        ESP_LOGW(TAG, "ABRP empty response body");
                        ok = false;
                    }
                }
                else
                {
                    ok = resp.is_success;
                }
            }
            else
            {
                ESP_LOGE(TAG, "HTTP(S) dest %u request failed: %s", i, esp_err_to_name(err));
                ok = false;
            }

            if (ok)
            {
                gd->consec_failures = 0;
                gd->backoff_ms = 0;
            }
            else
            {
                gd->consec_failures++;
            }
            // Backoff logic: after 3 failures, double backoff up to 2x base_cycle (or 60s min cap)
            if (gd->consec_failures >= 3)
            {
                uint32_t min_cap = 60000; // 60s cap baseline
                uint32_t next = (gd->backoff_ms ? gd->backoff_ms : base_cycle);
                if (next < min_cap)
                    next = next * 2; // exponential until min_cap
                if (next < min_cap)
                    next = min_cap; // ensure floor
                // limit to 2 * base_cycle if that is bigger than min_cap
                uint32_t max_backoff = base_cycle * 2;
                if (max_backoff < min_cap)
                    max_backoff = min_cap; // keep at least min_cap
                if (next > max_backoff)
                    next = max_backoff;
                gd->backoff_ms = next;
            }
            https_client_mgr_free_response(&resp);
            free(body);
            free(url);
            break;
        }
        default:
            break;
        }
        // Reschedule timer if periodic
        if (effective_cycle > 0)
        {
            wc_timer_set(&gd->publish_timer, effective_cycle); // schedule next expiry
        }
        else
        {
            gd->publish_timer = 0; // event-based; remains immediate until triggered differently
        }
        // Yield a bit after network operations
        vTaskDelay(pdMS_TO_TICKS(5));
    }

    free(raw_json);
}

char *autopid_get_config(void)
{
    // Return cached JSON. If not yet generated, attempt a lazy build once.
    if (autopid_config_json == NULL)
    {
        // Build lazily if possible (defensive in case init wasn't called yet)
        if (!autopid_config || !autopid_config->mutex)
        {
            ESP_LOGE(TAG, "autopid_get_config: autopid_config not ready");
            return NULL;
        }

        // Build under mutex and cache
        if (xSemaphoreTake(autopid_config->mutex, portMAX_DELAY) == pdTRUE)
        {
            cJSON *parameters_object = cJSON_CreateObject();
            if (!parameters_object)
            {
                ESP_LOGE(TAG, "Failed to create JSON object");
                xSemaphoreGive(autopid_config->mutex);
                return NULL;
            }

            for (int i = 0; i < autopid_config->pid_count; i++)
            {
                for (int j = 0; j < autopid_config->pids[i].parameters_count; j++)
                {
                    parameter_t *param = &autopid_config->pids[i].parameters[j];
                    if ((autopid_config->pids[i].pid_type == PID_STD && !autopid_config->pid_std_en) ||
                        (autopid_config->pids[i].pid_type == PID_CUSTOM && !autopid_config->pid_custom_en) ||
                        (autopid_config->pids[i].pid_type == PID_SPECIFIC && !autopid_config->pid_specific_en))
                    {
                        continue;
                    }
                    if (!param || !param->name)
                        continue;

                    cJSON *parameter_details = cJSON_CreateObject();
                    if (!parameter_details)
                    {
                        ESP_LOGE(TAG, "Failed to create parameter JSON object");
                        continue;
                    }
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

            // Include CAN-filter (broadcast) parameter metadata
            for (uint32_t fi = 0; fi < autopid_config->can_filters_count; fi++)
            {
                can_filter_t *f = &autopid_config->can_filters[fi];
                for (uint32_t pi = 0; pi < f->parameters_count; pi++)
                {
                    parameter_t *param = &f->parameters[pi];
                    if (!param || !param->name)
                        continue;

                    cJSON *parameter_details = cJSON_CreateObject();
                    if (!parameter_details)
                        continue;

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

            char *json_tmp = cJSON_PrintUnformatted(parameters_object);
            if (json_tmp)
            {
                // Move to PSRAM-managed buffer
                autopid_config_json = strdup_psram(json_tmp);
                free(json_tmp);
            }
            cJSON_Delete(parameters_object);
            xSemaphoreGive(autopid_config->mutex);
        }
    }
    return autopid_config_json;
}

// Forward declarations (used by CAN-filter helpers below)
static void send_commands(char *commands, uint32_t delay_ms);
static void publish_parameter_mqtt(parameter_t *param);

static void send_can_filter_cmd(uint32_t frame_id)
{
    // Ensure current CAN protocol matches frame-id width before setting ATCRA.
    // For ISO15765 CAN:
    // - 6: 11-bit 500k, 7: 29-bit 500k
    // - 8: 11-bit 250k, 9: 29-bit 250k
    // If we're on a CAN protocol family, keep it consistent with the filter ID.
    int32_t current_protocol_number = -1;
    (void)autopid_get_protocol_number(&current_protocol_number);
    bool is_extended = (frame_id > 0x7FF);

    int32_t desired_protocol_number = -1;
    if (current_protocol_number == 6 || current_protocol_number == 7)
    {
        desired_protocol_number = is_extended ? 7 : 6;
    }
    else if (current_protocol_number == 8 || current_protocol_number == 9)
    {
        desired_protocol_number = is_extended ? 9 : 8;
    }

    if (desired_protocol_number != -1 && desired_protocol_number != current_protocol_number)
    {
        char proto_cmd[10];
        snprintf(proto_cmd, sizeof(proto_cmd), "ATTP%01lX\r", (unsigned long)desired_protocol_number);
        send_commands(proto_cmd, 2);
    }

    // ATCRA expects 3 hex digits for 11-bit, 8 hex digits for 29-bit.
    char cmd[20];
    if (frame_id <= 0x7FF)
    {
        snprintf(cmd, sizeof(cmd), "ATCRA%03lX\r", (unsigned long)frame_id);
    }
    else
    {
        snprintf(cmd, sizeof(cmd), "ATCRA%08lX\r", (unsigned long)frame_id);
    }
    send_commands(cmd, 2);
}

static void bytes_to_hex_str(char *out, size_t out_size, const uint8_t *data, size_t data_len)
{
    if (!out || out_size == 0)
        return;
    out[0] = '\0';
    if (!data || data_len == 0)
        return;

    size_t w = 0;
    for (size_t i = 0; i < data_len; i++)
    {
        if (w + 3 >= out_size)
            break;
        int n = snprintf(out + w, out_size - w, "%02X", (unsigned)data[i]);
        if (n <= 0)
            break;
        w += (size_t)n;
        if (i + 1 < data_len)
        {
            if (w + 2 >= out_size)
                break;
            out[w++] = ' ';
            out[w] = '\0';
        }
    }
}

static void process_can_filter_frame(can_filter_t *f, const response_t *rsp)
{
    if (!f || !rsp)
        return;
    if (rsp->length == 0)
        return;

    // Debug log raw response bytes for this filter (enable DEBUG log level to see)
    {
        const uint32_t max_log_bytes = 32;
        uint32_t n = rsp->length;
        if (n > max_log_bytes)
            n = max_log_bytes;
        char hex[3 * max_log_bytes + 1];
        bytes_to_hex_str(hex, sizeof(hex), rsp->data, n);
        ESP_LOGI(TAG, "CANFLT 0x%lX rsp_len=%lu data[%lu]=%s", (unsigned long)f->frame_id,
                 (unsigned long)rsp->length, (unsigned long)n, hex);
    }

    for (uint32_t pi = 0; pi < f->parameters_count; pi++)
    {
        parameter_t *param = &f->parameters[pi];
        if (!param || !param->expression)
            continue;
        if (!wc_timer_is_expired(&param->timer))
            continue;

        // Reset timer with parameter period
        wc_timer_set(&param->timer, (param->period == 0) ? 5000 : param->period);

        double result = 0;
        if (evaluate_expression((uint8_t *)param->expression, (uint8_t *)rsp->data, 0, &result))
        {
            if (param->min != FLT_MAX && result < param->min)
            {
                continue;
            }
            if (param->max != FLT_MAX && result > param->max)
            {
                continue;
            }

            param->failed = false;
            param->value = (float)(round(result * 100.0) / 100.0);
            ESP_LOGI(TAG, "CANFLT 0x%lX param=%s result=%.2f", (unsigned long)f->frame_id,
                     param->name ? param->name : "(null)", (double)param->value);
            publish_parameter_mqtt(param);
        }
        else
        {
            param->failed = true;
            ESP_LOGE(TAG, "CANFLT 0x%lX param=%s eval_failed", (unsigned long)f->frame_id,
                     param->name ? param->name : "(null)");
        }
    }
}

void parse_elm327_response(char *buffer, response_t *response)
{

    if (buffer == NULL || response == NULL)
    {
        ESP_LOGE(TAG, "Invalid buffer or response pointer");
        return;
    }

    ESP_LOGI(TAG, "Starting to parse ELM327 response. Input buffer: %s", buffer);

    int k = 0;
    int frame_count = 0;
    char *frame;
    char *data_start;
    uint32_t lowest_header = UINT32_MAX; // Initialize to maximum value
    uint32_t highest_header = 0;         // Track highest header
    uint32_t first_header = 0;
    bool all_headers_same = true;
    uint8_t *lowest_header_data = NULL; // Store the actual data pointer
    uint8_t lowest_header_length = 0;

    frame = strtok(buffer, "\r\n");
    ESP_LOGI(TAG, "First frame: %s", frame ? frame : "NULL");

    int32_t current_protocol_number = -1;

    if (autopid_get_protocol_number(&current_protocol_number) == ESP_OK)
    {
        ESP_LOGI(TAG, "Current protocol number: %ld", current_protocol_number);
    }
    else
    {
        ESP_LOGE(TAG, "Failed to get protocol number");
    }

    while (frame != NULL)
    {
        ESP_LOGD(TAG, "Processing frame %d: %s", frame_count + 1, frame);
        frame_count++;

        // Remove trailing '>' if present
        size_t len = strlen(frame);
        if (len > 0 && frame[len - 1] == '>')
        {
            frame[len - 1] = '\0';
            ESP_LOGV(TAG, "Removed trailing '>' from frame");
        }

        data_start = strchr(frame, ' ');
        if (data_start != NULL)
        {
            int header_length = data_start - frame;
            char header_str[9] = {0};
            strncpy(header_str, frame, header_length);
            uint32_t current_header = strtoul(header_str, NULL, 16);
            ESP_LOGD(TAG, "Frame %d header: 0x%lX (length: %d)", frame_count, current_header, header_length);

            // Track highest header
            if (current_header > highest_header)
            {
                ESP_LOGD(TAG, "New highest header found: 0x%lX (previous: 0x%lX)", current_header, highest_header);
                highest_header = current_header;
            }

            // Track first header and compare subsequent headers
            if (frame_count == 1)
            {
                first_header = current_header;
                ESP_LOGD(TAG, "First header set to: 0x%lX", first_header);
            }
            else if (current_header != first_header)
            {
                all_headers_same = false;
                ESP_LOGD(TAG, "Different header detected: 0x%lX != 0x%lX", current_header, first_header);
            }

            data_start++;
            if (current_protocol_number == -1 || (current_protocol_number > sizeof(autopid_protocol_header_length)))
            {
                // Handle different header formats
                switch (header_length)
                {
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
            while (*temp_data != '\0')
            {
                if (*temp_data == ' ')
                {
                    temp_data++;
                    continue;
                }
                if (strlen(temp_data) < 2)
                    break;
                current_length++;
                temp_data += 2;
            }

            // If this is the lowest header so far, store its data
            if (current_header < lowest_header)
            {
                ESP_LOGD(TAG, "New lowest header found: 0x%lX (previous: 0x%lX)", current_header, lowest_header);
                lowest_header = current_header;
                lowest_header_length = current_length;

                // Allocate space and copy data for lowest header frame
                if (lowest_header_data == NULL)
                {
                    lowest_header_data = (uint8_t *)heap_caps_malloc(current_length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                }
                else
                {
                    lowest_header_data = (uint8_t *)heap_caps_realloc(lowest_header_data, current_length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                }

                // Parse and store the data bytes for this frame
                int idx = 0;
                while (*current_data_start != '\0')
                {
                    if (*current_data_start == ' ')
                    {
                        current_data_start++;
                        continue;
                    }
                    if (strlen(current_data_start) < 2)
                        break;

                    char byte_str[3] = {current_data_start[0], current_data_start[1], 0};
                    lowest_header_data[idx++] = (unsigned char)strtol(byte_str, NULL, 16);
                    current_data_start += 2;
                }
                ESP_LOGD(TAG, "Stored %d bytes from lowest header frame", idx);
            }

            // Parse data bytes into main response buffer
            ESP_LOGV(TAG, "Starting data byte parsing at position: %s", data_start);
            while (*data_start != '\0')
            {
                if (*data_start == ' ')
                {
                    data_start++;
                    continue;
                }
                if (strlen(data_start) < 2)
                {
                    ESP_LOGW(TAG, "Incomplete byte at end of frame: %s", data_start);
                    break;
                }

                char byte_str[3] = {data_start[0], data_start[1], 0};
                response->data[k] = (unsigned char)strtol(byte_str, NULL, 16);
                ESP_LOGV(TAG, "Parsed byte %d: 0x%02X from %s", k, response->data[k], byte_str);
                k++;
                data_start += 2;
            }
        }
        else
        {
            ESP_LOGW(TAG, "No space delimiter found in frame: %s", frame);
        }
        frame = strtok(NULL, "\r\n");
    }

    response->length = k;

    // Set priority data based on frame count and header comparison
    if (frame_count <= 2 || all_headers_same)
    {
        response->priority_data = NULL;
        response->priority_data_len = 0;
        if (lowest_header_data != NULL)
        {
            free(lowest_header_data);
            lowest_header_data = NULL;
        }
        ESP_LOGI(TAG, "Null priority data set - frames: %d, all headers same: %d",
                 frame_count, all_headers_same);
    }
    else
    {
        response->priority_data = lowest_header_data;
        response->priority_data_len = lowest_header_length;

        if (response->priority_data != NULL && response->priority_data_len > 0)
        {
            ESP_LOGI(TAG, "Priority data set - length: %u, starting with byte: 0x%02X",
                     response->priority_data_len,
                     response->priority_data[0]);
        }
        else
        {
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

static bool autopid_buf_is_only_stopped(const char *buf)
{
    if (!buf)
        return true;

    const char *p = buf;
    while (*p)
    {
        // Skip whitespace and separators
        while (*p == '\r' || *p == '\n' || *p == ' ' || *p == '\t')
            p++;
        if (*p == '\0')
            break;

        // Find end of token/line
        const char *start = p;
        while (*p && *p != '\r' && *p != '\n')
            p++;
        size_t len = (size_t)(p - start);

        // Trim trailing spaces
        while (len > 0 && (start[len - 1] == ' ' || start[len - 1] == '\t'))
            len--;

        if (len == 0)
            continue;

        // ELM/STN chips often emit a lone "STOPPED" line when ATMA is terminated.
        if (!((len == 7 && strncmp(start, "STOPPED", 7) == 0) ||
              (len == 7 && strncmp(start, "stopped", 7) == 0)))
        {
            return false;
        }
    }

    return true;
}

// -----------------------
// ATMA (monitor-all) parsing
// -----------------------

static uint32_t autopid_atma_expected_frame_id = 0;
static bool autopid_atma_filter_enabled = false;

static void autopid_atma_set_expected_frame_id(uint32_t frame_id)
{
    autopid_atma_expected_frame_id = frame_id;
    autopid_atma_filter_enabled = (frame_id != 0);
}

static bool autopid_is_hex_n(const char *s, size_t n)
{
    if (!s || n == 0)
        return false;
    for (size_t i = 0; i < n; i++)
    {
        if (!isxdigit((unsigned char)s[i]))
            return false;
    }
    return true;
}

static bool autopid_hex_byte(const char *s, uint8_t *out)
{
    if (!s || !out)
        return false;
    if (!autopid_is_hex_n(s, 2))
        return false;
    char tmp[3] = {s[0], s[1], 0};
    char *endptr = NULL;
    long v = strtol(tmp, &endptr, 16);
    if (endptr == tmp)
        return false;
    if (v < 0 || v > 255)
        return false;
    *out = (uint8_t)v;
    return true;
}

static bool autopid_atma_parse_line(const char *line, size_t line_len, uint32_t *out_header, response_t *out_rsp)
{
    if (!line || line_len == 0 || !out_header || !out_rsp)
        return false;

    // Trim leading/trailing whitespace
    while (line_len > 0 && (line[0] == ' ' || line[0] == '\t'))
    {
        line++;
        line_len--;
    }
    while (line_len > 0 && (line[line_len - 1] == ' ' || line[line_len - 1] == '\t'))
        line_len--;
    if (line_len == 0)
        return false;

    // Ignore prompt / terminator lines
    if (line_len == 1 && line[0] == '>')
        return false;
    if (line_len >= 7 && (strncasecmp(line, "STOPPED", 7) == 0))
        return false;
    if (line_len >= 5 && (strncasecmp(line, "ERROR", 5) == 0))
        return false;
    if (line_len >= 7 && (strncasecmp(line, "NO DATA", 7) == 0))
        return false;

    // Tokenize by whitespace (in-place not allowed, so we scan)
    const char *p = line;
    const char *end = line + line_len;

    // Read first token
    while (p < end && (*p == ' ' || *p == '\t'))
        p++;
    const char *t1 = p;
    while (p < end && *p != ' ' && *p != '\t')
        p++;
    size_t t1_len = (size_t)(p - t1);
    if (t1_len == 0)
        return false;

    uint32_t header = 0;
    bool header_ok = false;

    // Case 1: header is contiguous hex: "123" (11-bit) or "000008C0" (29-bit)
    if ((t1_len == 3 || t1_len == 8) && autopid_is_hex_n(t1, t1_len))
    {
        char tmp[9] = {0};
        memcpy(tmp, t1, t1_len);
        header = (uint32_t)strtoul(tmp, NULL, 16);
        header_ok = true;
    }
    else if (t1_len == 2 && autopid_is_hex_n(t1, 2))
    {
        // Case 2: header is split into bytes: "00 00 08 C0" (extended)
        // or (less common) "01 23" (standard with leading zero).
        uint8_t b1 = 0, b2 = 0, b3 = 0, b4 = 0;
        const char *t2 = NULL, *t3 = NULL, *t4 = NULL;
        size_t t2_len = 0, t3_len = 0, t4_len = 0;

        // token 2
        while (p < end && (*p == ' ' || *p == '\t'))
            p++;
        t2 = p;
        while (p < end && *p != ' ' && *p != '\t')
            p++;
        t2_len = (size_t)(p - t2);

        // token 3
        while (p < end && (*p == ' ' || *p == '\t'))
            p++;
        t3 = p;
        while (p < end && *p != ' ' && *p != '\t')
            p++;
        t3_len = (size_t)(p - t3);

        // token 4
        while (p < end && (*p == ' ' || *p == '\t'))
            p++;
        t4 = p;
        while (p < end && *p != ' ' && *p != '\t')
            p++;
        t4_len = (size_t)(p - t4);

        // Try 4-byte extended header first (only accept if it matches expected filter when enabled)
        if (t2_len == 2 && t3_len == 2 && t4_len == 2 &&
            autopid_hex_byte(t1, &b1) && autopid_hex_byte(t2, &b2) &&
            autopid_hex_byte(t3, &b3) && autopid_hex_byte(t4, &b4))
        {
            uint32_t hdr32 = ((uint32_t)b1 << 24) | ((uint32_t)b2 << 16) | ((uint32_t)b3 << 8) | (uint32_t)b4;
            if (!autopid_atma_filter_enabled || hdr32 == autopid_atma_expected_frame_id)
            {
                header = hdr32;
                header_ok = true;
            }
        }

        // If not a matching 4-byte header, try 2-byte standard header (only if it matches expected)
        if (!header_ok && t2_len == 2)
        {
            if (autopid_hex_byte(t1, &b1) && autopid_hex_byte(t2, &b2))
            {
                uint32_t hdr16 = ((uint32_t)b1 << 8) | (uint32_t)b2;
                if ((!autopid_atma_filter_enabled && hdr16 <= 0x7FF) ||
                    (autopid_atma_filter_enabled && hdr16 == autopid_atma_expected_frame_id))
                {
                    header = hdr16;
                    header_ok = true;
                }
            }
        }
    }

    if (!header_ok)
        return false;

    // Filter out unrelated frames (important when frames are buffered before ATCRA takes effect)
    if (autopid_atma_filter_enabled && header != autopid_atma_expected_frame_id)
        return false;

    // Parse remaining tokens as data bytes
    memset(out_rsp, 0, sizeof(*out_rsp));
    out_rsp->priority_data = NULL;
    out_rsp->priority_data_len = 0;

    uint32_t out_idx = 0;
    while (p < end)
    {
        while (p < end && (*p == ' ' || *p == '\t'))
            p++;
        const char *bt = p;
        while (p < end && *p != ' ' && *p != '\t')
            p++;
        size_t bt_len = (size_t)(p - bt);
        if (bt_len == 0)
            continue;
        if (bt_len == 1 && bt[0] == '>')
            break;
        if (bt_len != 2 || !autopid_is_hex_n(bt, 2))
            continue;
        if (out_idx >= sizeof(out_rsp->data))
            break;
        uint8_t b = 0;
        if (!autopid_hex_byte(bt, &b))
            continue;
        out_rsp->data[out_idx++] = b;
    }

    out_rsp->length = out_idx;
    if (out_rsp->length == 0)
        return false;

    *out_header = header;
    return true;
}

static char autopid_atma_line_buf[192];
static size_t autopid_atma_line_pos = 0;

static void autopid_atma_parser_reset(void)
{
    autopid_atma_line_pos = 0;
    autopid_atma_line_buf[0] = '\0';
}

void autopid_atma_parser(char *str, uint32_t len, QueueHandle_t *q, char *cmd_str)
{
    (void)cmd_str;
    if (!str || len == 0 || !q)
        return;

    for (uint32_t i = 0; i < len; i++)
    {
        char c = str[i];
        if (c == '\r' || c == '\n')
        {
            if (autopid_atma_line_pos > 0)
            {
                // Process one complete line
                uint32_t header = 0;
                response_t rsp;
                if (autopid_atma_parse_line(autopid_atma_line_buf, autopid_atma_line_pos, &header, &rsp))
                {
                    if (xQueueSend(*q, &rsp, pdMS_TO_TICKS(20)) != pdPASS)
                    {
                        // Drop if queue is full; monitoring is time-sliced.
                    }
                }
                autopid_atma_line_pos = 0;
            }
        }
        else
        {
            if (autopid_atma_line_pos + 1 < sizeof(autopid_atma_line_buf))
            {
                autopid_atma_line_buf[autopid_atma_line_pos++] = c;
                autopid_atma_line_buf[autopid_atma_line_pos] = '\0';
            }
            else
            {
                // Line too long; reset to avoid runaway.
                autopid_atma_line_pos = 0;
            }
        }
    }
}

void autopid_parser(char *str, uint32_t len, QueueHandle_t *q, char *cmd_str)
{
    static response_t response;
    if (str != NULL && strlen(str) != 0)
    {
        ESP_LOGI(TAG, "%s", str);

        append_to_buffer(auto_pid_buf, str);

        if (strchr(str, '>') != NULL)
        {
            // If we only got the ATMA terminator, ignore it.
            if (autopid_buf_is_only_stopped(auto_pid_buf))
            {
                auto_pid_buf[0] = '\0';
                return;
            }

            if (strstr(str, "NO DATA") == NULL && strstr(str, "ERROR") == NULL)
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
                sprintf((char *)response.data, "error");
                response.length = strlen((char *)response.data);
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

// Detect protocol change commands (ATSPx / ATTPx), case-insensitive and tolerant to whitespace.
// Examples: "ATSP6\r", "at sp 6\r", "AT TP 7\r".
static bool try_parse_protocol_cmd(const char *cmd, int32_t *out_protocol)
{
    if (!cmd || !out_protocol)
        return false;

    const unsigned char *p = (const unsigned char *)cmd;
    while (*p && isspace(*p))
        p++;

    if (tolower(p[0]) != 'a' || tolower(p[1]) != 't')
        return false;

    p += 2;
    while (*p && isspace(*p))
        p++;

    if (tolower(*p) == 's')
    {
        p++;
        while (*p && isspace(*p))
            p++;
        if (tolower(*p) != 'p')
            return false;
        p++;
    }
    else if (tolower(*p) == 't')
    {
        // ATTP: accept both "ATTP" and whitespace-separated forms like "AT TP".
        // After the first 'T', we allow either immediate/space-separated 'P' (TP)
        // or another 'T' then 'P' (TTP).
        p++;
        while (*p && isspace(*p))
            p++;
        if (tolower(*p) == 'p')
        {
            p++;
        }
        else
        {
            if (tolower(*p) != 't')
                return false;
            p++;
            while (*p && isspace(*p))
                p++;
            if (tolower(*p) != 'p')
                return false;
            p++;
        }
    }
    else
    {
        return false;
    }

    while (*p && isspace(*p))
        p++;

    if (!isxdigit(*p))
        return false;

    char hex_buf[3] = {0};
    int hi = 0;
    while (*p && isxdigit(*p) && hi < 2)
    {
        hex_buf[hi++] = (char)*p;
        p++;
    }

    char *endptr = NULL;
    long v = strtol(hex_buf, &endptr, 16);
    if (endptr == hex_buf)
        return false;

    *out_protocol = (int32_t)v;
    return true;
}

static void send_commands(char *commands, uint32_t delay_ms)
{
    if (!commands)
        return;

    char *cmd_start = commands;
    char *cmd_end;

    while ((cmd_end = strchr(cmd_start, '\r')) != NULL)
    {
        size_t cmd_len = cmd_end - cmd_start + 1; // +1 to include '\r'
        char str_send[cmd_len + 1];               // +1 for null terminator
        strncpy(str_send, cmd_start, cmd_len);
        str_send[cmd_len] = '\0'; // Null-terminate the command string

        // Keep protocol tracking in sync if init strings switch protocol.
        int32_t new_protocol_number = -1;
        bool skip_send = false;
        if (try_parse_protocol_cmd(str_send, &new_protocol_number))
        {
            int32_t current_protocol_number = -1;
            if (autopid_get_protocol_number(&current_protocol_number) == ESP_OK &&
                current_protocol_number == new_protocol_number)
            {
                // Already in the requested protocol; avoid re-sending the command.
                skip_send = true;
            }
            else
            {
                // Update tracking early so downstream logic (header length etc.) stays consistent.
                autopid_set_protocol_number(new_protocol_number);
            }
        }

        if (!skip_send &&
            (strstr(str_send, "ath0") == NULL && strstr(str_send, "ATH0") == NULL && strstr(str_send, "at h0") == NULL && strstr(str_send, "AT H0") == NULL) &&
            (strstr(str_send, "ats0") == NULL && strstr(str_send, "ATS0") == NULL && strstr(str_send, "at s0") == NULL && strstr(str_send, "AT s0") == NULL) &&
            (strstr(str_send, "ate1") == NULL && strstr(str_send, "ATE1") == NULL && strstr(str_send, "at e1") == NULL && strstr(str_send, "AT E1") == NULL))
        {
#if HARDWARE_VER == WICAN_PRO
            // elm327_process_cmd((uint8_t *)str_send, cmd_len, &autopidQueue, elm327_autopid_cmd_buffer, &elm327_autopid_cmd_buffer_len, &elm327_autopid_last_cmd_time, autopid_parser);
            elm327_run_command((char *)str_send, cmd_len, 1000, &autopidQueue, NULL, false, 0);
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

static bool all_parameters_failed(autopid_config_t *autopid_config)
{
    if (!autopid_config)
        return true;

    bool any_success = false;

    xSemaphoreTake(autopid_config->mutex, portMAX_DELAY);
    for (uint32_t i = 0; i < autopid_config->pid_count; i++)
    {
        pid_data_t *curr_pid = &autopid_config->pids[i];
        for (uint32_t p = 0; p < curr_pid->parameters_count; p++)
        {
            if (!curr_pid->parameters[p].failed)
            {
                any_success = true;
                break;
            }
        }
        if (any_success)
            break;
    }
    xSemaphoreGive(autopid_config->mutex);

    return !any_success;
}

static void publish_parameter_mqtt(parameter_t *param)
{
    if (!param)
        return;

    char *payload = NULL;

    switch (param->destination_type)
    {
    case DEST_MQTT_TOPIC:
        // JSON format
        cJSON *param_json = cJSON_CreateObject();
        if (param_json)
        {
            if (param->sensor_type == BINARY_SENSOR)
            {
                cJSON_AddStringToObject(param_json, param->name, param->value > 0 ? "on" : "off");
            }
            else
            {
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

    if (payload)
    {
        // Publish to specified destination or default topic
        if (param->destination && strlen(param->destination) > 0)
        {
            mqtt_publish(param->destination, payload, 0, 0, 1);
            ESP_LOGI(TAG, "Published to %s", param->destination);
        }
        else
        {
            mqtt_publish(config_server_get_mqtt_rx_topic(), payload, 0, 0, 1);
        }
        free(payload);
    }
}

static void autopid_publish_task(void *pvParameters)
{
    ESP_LOGI(TAG, "Autopid Publish Task Started");
    for (;;)
    {
        // Only publish when autopid is enabled and STA is connected
        if (dev_status_is_autopid_enabled() && dev_status_is_sta_connected())
        {
            // Grouping must remain enabled at runtime
            if (autopid_config && autopid_config->grouping && strcmp("enable", autopid_config->grouping) == 0)
            {
                autopid_publish_all_destinations();
            }
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

static void autopid_webhook_task(void *pvParameters)
{
    ESP_LOGI(TAG, "Autopid Webhook Task Started");

    uint32_t last_post_time = 0;
    static char *prev_autopid_snapshot = NULL; // previous snapshot for diffing when sending only changes
    static char *prev_config_snapshot = NULL;  // previous config for diffing
    static char *prev_status_snapshot = NULL;  // previous status for diffing

    for (;;)
    {
        // Only post when autopid is enabled and STA is connected
        if (dev_status_is_autopid_enabled() && dev_status_is_sta_connected())
        {
            // Read webhook_data_mode setting from config
            bool send_full_data = false;
            if (autopid_config && autopid_config->webhook_data_mode)
            {
                send_full_data = (strcmp(autopid_config->webhook_data_mode, "full") == 0);
            }
            ha_webhook_config_t webhook_cfg = {0};
            esp_err_t err = ha_webhooks_get_config(&webhook_cfg);

            if (err == ESP_OK && webhook_cfg.enabled && webhook_cfg.url[0] != '\0')
            {
                uint32_t current_time = xTaskGetTickCount() / configTICK_RATE_HZ;
                uint32_t interval_sec = webhook_cfg.interval > 0 ? webhook_cfg.interval : 60;

                // Check if it's time to post
                if ((current_time - last_post_time) >= interval_sec)
                {
                    // Get current autopid data
                    char *raw_json = autopid_data_read();
                    if (raw_json)
                    {
                        char *url = strdup_psram(webhook_cfg.url);
                        if (url)
                        {
                            char *body = NULL;
                            char *current_config_data = NULL;
                            char *current_status_data = NULL;
                            char *current_autopid_data = NULL;

                            // Always send settings-style JSON: {config, status, autopid_data}
                            current_status_data = config_server_get_status_json(true);
                            current_config_data = autopid_get_config();
                            current_autopid_data = strdup_psram(raw_json);

                            cJSON *root_obj = cJSON_CreateObject();
                            if (root_obj)
                            {
                                cJSON *cfg_obj = NULL;
                                cJSON *sts_obj = NULL;
                                cJSON *auto_obj = NULL;

                                // Build config (full or diff)
                                cJSON *curr_cfg_src = NULL;
                                if (current_config_data)
                                {
                                    curr_cfg_src = cJSON_Parse(current_config_data);
                                }
                                if (!curr_cfg_src)
                                    curr_cfg_src = cJSON_CreateObject();
                                if (send_full_data)
                                {
                                    cfg_obj = cJSON_Duplicate(curr_cfg_src, true);
                                }
                                else
                                {
                                    cJSON *cfg_diff = cJSON_CreateObject();
                                    if (!cfg_diff)
                                        cfg_diff = cJSON_CreateObject();
                                    cJSON *prev_cfg_src = NULL;
                                    if (prev_config_snapshot)
                                    {
                                        prev_cfg_src = cJSON_Parse(prev_config_snapshot);
                                    }
                                    cJSON *it = NULL;
                                    cJSON_ArrayForEach(it, curr_cfg_src)
                                    {
                                        const char *key = it->string;
                                        if (!key)
                                            continue;
                                        bool changed = true;
                                        if (prev_cfg_src)
                                        {
                                            cJSON *prev_it = cJSON_GetObjectItemCaseSensitive(prev_cfg_src, key);
                                            if (prev_it)
                                            {
                                                if (cJSON_IsString(it) && cJSON_IsString(prev_it))
                                                {
                                                    const char *cs = it->valuestring ? it->valuestring : "";
                                                    const char *ps = prev_it->valuestring ? prev_it->valuestring : "";
                                                    changed = (strcmp(cs, ps) != 0);
                                                }
                                                else if (cJSON_IsNumber(it) && cJSON_IsNumber(prev_it))
                                                {
                                                    changed = (it->valuedouble != prev_it->valuedouble);
                                                }
                                                else if (cJSON_IsBool(it) && cJSON_IsBool(prev_it))
                                                {
                                                    int cv = cJSON_IsTrue(it) ? 1 : 0;
                                                    int pv = cJSON_IsTrue(prev_it) ? 1 : 0;
                                                    changed = (cv != pv);
                                                }
                                                else
                                                {
                                                    // Different types -> consider changed
                                                    changed = true;
                                                }
                                            }
                                        }
                                        if (changed)
                                        {
                                            if (cJSON_IsString(it) && it->valuestring)
                                                cJSON_AddStringToObject(cfg_diff, key, it->valuestring);
                                            else if (cJSON_IsNumber(it))
                                                cJSON_AddNumberToObject(cfg_diff, key, it->valuedouble);
                                            else if (cJSON_IsBool(it))
                                                cJSON_AddBoolToObject(cfg_diff, key, cJSON_IsTrue(it));
                                        }
                                    }
                                    cfg_obj = cfg_diff;
                                    if (prev_cfg_src)
                                        cJSON_Delete(prev_cfg_src);
                                    if (prev_config_snapshot)
                                    {
                                        free(prev_config_snapshot);
                                        prev_config_snapshot = NULL;
                                    }
                                    prev_config_snapshot = strdup_psram(current_config_data);
                                }
                                if (curr_cfg_src)
                                    cJSON_Delete(curr_cfg_src);

                                // Build status (full or diff)
                                cJSON *curr_sts_src = NULL;
                                if (current_status_data)
                                {
                                    curr_sts_src = cJSON_Parse(current_status_data);
                                }
                                if (!curr_sts_src)
                                    curr_sts_src = cJSON_CreateObject();
                                if (send_full_data)
                                {
                                    sts_obj = cJSON_Duplicate(curr_sts_src, true);
                                }
                                else
                                {
                                    cJSON *sts_diff = cJSON_CreateObject();
                                    if (!sts_diff)
                                        sts_diff = cJSON_CreateObject();
                                    cJSON *prev_sts_src = NULL;
                                    if (prev_status_snapshot)
                                    {
                                        prev_sts_src = cJSON_Parse(prev_status_snapshot);
                                    }
                                    cJSON *it2 = NULL;
                                    cJSON_ArrayForEach(it2, curr_sts_src)
                                    {
                                        const char *key = it2->string;
                                        if (!key)
                                            continue;
                                        bool changed = true;
                                        if (prev_sts_src)
                                        {
                                            cJSON *prev_it = cJSON_GetObjectItemCaseSensitive(prev_sts_src, key);
                                            if (prev_it)
                                            {
                                                if (cJSON_IsString(it2) && cJSON_IsString(prev_it))
                                                {
                                                    const char *cs = it2->valuestring ? it2->valuestring : "";
                                                    const char *ps = prev_it->valuestring ? prev_it->valuestring : "";
                                                    changed = (strcmp(cs, ps) != 0);
                                                }
                                                else if (cJSON_IsNumber(it2) && cJSON_IsNumber(prev_it))
                                                {
                                                    changed = (it2->valuedouble != prev_it->valuedouble);
                                                }
                                                else if (cJSON_IsBool(it2) && cJSON_IsBool(prev_it))
                                                {
                                                    int cv = cJSON_IsTrue(it2) ? 1 : 0;
                                                    int pv = cJSON_IsTrue(prev_it) ? 1 : 0;
                                                    changed = (cv != pv);
                                                }
                                                else
                                                {
                                                    changed = true;
                                                }
                                            }
                                        }
                                        if (changed)
                                        {
                                            if (cJSON_IsString(it2) && it2->valuestring)
                                                cJSON_AddStringToObject(sts_diff, key, it2->valuestring);
                                            else if (cJSON_IsNumber(it2))
                                                cJSON_AddNumberToObject(sts_diff, key, it2->valuedouble);
                                            else if (cJSON_IsBool(it2))
                                                cJSON_AddBoolToObject(sts_diff, key, cJSON_IsTrue(it2));
                                        }
                                    }
                                    sts_obj = sts_diff;
                                    if (prev_sts_src)
                                        cJSON_Delete(prev_sts_src);
                                    if (prev_status_snapshot)
                                    {
                                        free(prev_status_snapshot);
                                        prev_status_snapshot = NULL;
                                    }
                                    prev_status_snapshot = strdup_psram(current_status_data);
                                }
                                if (curr_sts_src)
                                    cJSON_Delete(curr_sts_src);

                                // Build autopid_data (full or only changed depending on __placeholder_flag__)
                                cJSON *curr_auto_src = NULL;
                                if (current_autopid_data)
                                {
                                    curr_auto_src = cJSON_Parse(current_autopid_data);
                                }
                                if (!curr_auto_src)
                                    curr_auto_src = cJSON_CreateObject();

                                if (send_full_data)
                                {
                                    // Send full autopid_data
                                    auto_obj = cJSON_Duplicate(curr_auto_src, true);
                                }
                                else
                                {
                                    // Send only changed values compared to previous snapshot
                                    cJSON *diff_obj = cJSON_CreateObject();
                                    if (!diff_obj)
                                        diff_obj = cJSON_CreateObject();

                                    cJSON *prev_src = NULL;
                                    if (prev_autopid_snapshot)
                                    {
                                        prev_src = cJSON_Parse(prev_autopid_snapshot);
                                    }
                                    // Iterate current keys and compare to previous
                                    cJSON *it = NULL;
                                    cJSON_ArrayForEach(it, curr_auto_src)
                                    {
                                        const char *key = it->string;
                                        if (!key)
                                            continue;
                                        double curr_val = 0;
                                        bool has_curr = false;
                                        if (cJSON_IsNumber(it))
                                        {
                                            curr_val = it->valuedouble;
                                            has_curr = true;
                                        }
                                        else if (cJSON_IsString(it) && it->valuestring)
                                        {
                                            has_curr = true;
                                        }
                                        else if (cJSON_IsBool(it))
                                        {
                                            curr_val = cJSON_IsTrue(it) ? 1.0 : 0.0;
                                            has_curr = true;
                                        }
                                        if (!has_curr)
                                            continue;

                                        bool changed = true;
                                        if (prev_src)
                                        {
                                            cJSON *prev_it = cJSON_GetObjectItemCaseSensitive(prev_src, key);
                                            if (prev_it)
                                            {
                                                if (cJSON_IsNumber(it) && cJSON_IsNumber(prev_it))
                                                {
                                                    double prev_val = prev_it->valuedouble;
                                                    changed = (curr_val != prev_val);
                                                }
                                                else if (cJSON_IsString(it) && cJSON_IsString(prev_it))
                                                {
                                                    const char *cs = it->valuestring ? it->valuestring : "";
                                                    const char *ps = prev_it->valuestring ? prev_it->valuestring : "";
                                                    changed = (strcmp(cs, ps) != 0);
                                                }
                                                else if (cJSON_IsBool(it) && cJSON_IsBool(prev_it))
                                                {
                                                    int cv = cJSON_IsTrue(it) ? 1 : 0;
                                                    int pv = cJSON_IsTrue(prev_it) ? 1 : 0;
                                                    changed = (cv != pv);
                                                }
                                            }
                                        }

                                        if (changed)
                                        {
                                            if (cJSON_IsNumber(it))
                                            {
                                                cJSON_AddNumberToObject(diff_obj, key, it->valuedouble);
                                            }
                                            else if (cJSON_IsString(it) && it->valuestring)
                                            {
                                                cJSON_AddStringToObject(diff_obj, key, it->valuestring);
                                            }
                                            else if (cJSON_IsBool(it))
                                            {
                                                cJSON_AddBoolToObject(diff_obj, key, cJSON_IsTrue(it));
                                            }
                                        }
                                    }

                                    auto_obj = diff_obj;
                                    if (prev_src)
                                        cJSON_Delete(prev_src);
                                    if (prev_autopid_snapshot)
                                    {
                                        free(prev_autopid_snapshot);
                                        prev_autopid_snapshot = NULL;
                                    }
                                    prev_autopid_snapshot = strdup_psram(current_autopid_data);
                                }
                                if (curr_auto_src)
                                    cJSON_Delete(curr_auto_src);

                                // Only add sections that have data (non-empty objects)
                                if (cfg_obj && cJSON_GetArraySize(cfg_obj) > 0)
                                {
                                    cJSON_AddItemToObject(root_obj, "config", cfg_obj);
                                }
                                else if (cfg_obj)
                                {
                                    cJSON_Delete(cfg_obj);
                                }

                                if (sts_obj && cJSON_GetArraySize(sts_obj) > 0)
                                {
                                    cJSON_AddItemToObject(root_obj, "status", sts_obj);
                                }
                                else if (sts_obj)
                                {
                                    cJSON_Delete(sts_obj);
                                }

                                if (auto_obj && cJSON_GetArraySize(auto_obj) > 0)
                                {
                                    cJSON_AddItemToObject(root_obj, "autopid_data", auto_obj);
                                }
                                else if (auto_obj)
                                {
                                    cJSON_Delete(auto_obj);
                                }

                                // Add mock GPS data
                                cJSON *gps = cJSON_CreateObject();
                                if (gps)
                                {
                                    cJSON_AddNumberToObject(gps, "latitude", 37.7749);
                                    cJSON_AddNumberToObject(gps, "longitude", -122.4194);
                                    cJSON_AddNumberToObject(gps, "accuracy", 10);
                                    cJSON_AddNumberToObject(gps, "altitude", 25.5);
                                    cJSON_AddNumberToObject(gps, "speed", 15.3);
                                    cJSON_AddNumberToObject(gps, "heading", 180);
                                    cJSON_AddItemToObject(root_obj, "gps", gps);
                                }

                                // Normalize precision across payload
                                limitJsonDecimalPrecision(root_obj);

                                char *printed = cJSON_PrintUnformatted(root_obj);
                                if (printed)
                                {
                                    body = strdup_psram(printed);
                                    free(printed);
                                }
                                cJSON_Delete(root_obj);
                            }

                            // Free only buffers we allocated locally.
                            // current_autopid_data is a strdup_psram of raw_json → safe to free.
                            if (current_autopid_data)
                            {
                                free(current_autopid_data);
                                current_autopid_data = NULL;
                            }
                            // current_status_data is returned heap buffer by config_server_get_status_json(true) → safe to free.
                            if (current_status_data)
                            {
                                free(current_status_data);
                                current_status_data = NULL;
                            }
                            // current_config_data comes from autopid_get_config() and is a cached global → DO NOT free here.

                            if (body)
                            {
                                https_client_mgr_config_t cfg = {0};
                                cfg.url = url;
                                cfg.timeout_ms = 5000;

                                // Detect scheme from URL
                                bool is_https_url = (strncasecmp(url, "https://", 8) == 0);
                                if (is_https_url)
                                {
                                    // Use built-in certificate bundle for HTTPS
                                    cfg.use_crt_bundle = true;
                                    ESP_LOGI(TAG, "Using built-in certificate bundle for webhook HTTPS");

                                    // For HTTPS, if host is raw IPv4 address, skip CN verification
                                    const char *host_start = strstr(url, "://");
                                    host_start = host_start ? host_start + 3 : url;
                                    char host_buf[64] = {0};
                                    size_t hi = 0;
                                    while (host_start[hi] && host_start[hi] != '/' && host_start[hi] != ':' && hi < sizeof(host_buf) - 1)
                                    {
                                        host_buf[hi] = host_start[hi];
                                        hi++;
                                    }
                                    bool is_ip = true;
                                    for (size_t k = 0; k < hi; k++)
                                    {
                                        if ((host_buf[k] < '0' || host_buf[k] > '9') && host_buf[k] != '.')
                                        {
                                            is_ip = false;
                                            break;
                                        }
                                    }
                                    if (is_ip)
                                    {
                                        cfg.skip_common_name = true;
                                    }
                                }

                                https_client_mgr_response_t resp = {0};
                                esp_err_t post_err = https_client_mgr_request_with_auth(&cfg, HTTPS_METHOD_POST,
                                                                                        body, strlen(body),
                                                                                        "application/json",
                                                                                        NULL,
                                                                                        NULL,
                                                                                        &resp);

                                bool ok = (post_err == ESP_OK && resp.is_success);
                                if (ok)
                                {
                                    ESP_LOGI(TAG, "Webhook POST success, status %d", resp.status_code);
                                    // printf("body: %s\n", body);
                                    last_post_time = current_time;
                                }
                                else
                                {
                                    ESP_LOGE(TAG, "Webhook POST failed: %s", esp_err_to_name(post_err));
                                }

                                https_client_mgr_free_response(&resp);
                                free(body);
                            }
                            free(url);
                        }
                        free(raw_json);
                    }
                }
            }
        }

        vTaskDelay(pdMS_TO_TICKS(1000)); // Check every second
    }
}

static void autopid_task(void *pvParameters)
{
    static char default_init[] = "ati\rate0\rath1\ratl0\rats1\ratm0\ratst96\r";
    wc_timer_t ecu_check_timer;
    static response_t monitor_rsp;

    ESP_LOGI(TAG, "Autopid Task Started");

    // while(config_server_mqtt_en_config() == 1 && !mqtt_connected())
    // {
    //     vTaskDelay(pdMS_TO_TICKS(2000));
    // }

    // if(config_server_mqtt_en_config() == 1 && autopid_config->ha_discovery_en)
    // {
    //     autopid_pub_discovery();
    // }
    if (!autopid_config)
    {
        ESP_LOGE(TAG, "autopid_config is NULL");
        return;
    }

    // xSemaphoreTake(autopid_config->mutex, portMAX_DELAY);
    // uint32_t total_params = 0;

    // for (uint32_t i = 0; i < autopid_config->pid_count; i++)
    // {
    //     total_params += autopid_config->pids[i].parameters_count;
    // }

    // bool *pid_failed = calloc(total_params, sizeof(bool));
    // xSemaphoreGive(autopid_config->mutex);

    if (strcmp(autopid_config->std_ecu_protocol, "0") == 0)
    {
        ESP_LOGI(TAG, "Protocol is Auto");

        // elm327_get_protocol_number
        uint8_t auto_protocol_number = 0;
        if (elm327_get_protocol_number(&auto_protocol_number) == ESP_OK)
        {
            autopid_set_protocol_number(auto_protocol_number);

            // Free existing standard_init if allocated
            if (autopid_config->standard_init)
            {
                free(autopid_config->standard_init);
            }

            // Allocate new memory for the protocol string
            char protocol_str[16];
            snprintf(protocol_str, sizeof(protocol_str), "ATTP%01X\r", auto_protocol_number);
            autopid_config->standard_init = strdup_psram(protocol_str);
        }
        else
        {
            ESP_LOGE(TAG, "Failed to get protocol number");
        }

        ESP_LOGI(TAG, "Protocol number: %u", auto_protocol_number);
    }

    vTaskDelay(pdMS_TO_TICKS(150));
    send_commands(default_init, 50);
    ESP_LOGI(TAG, "Autopid Start loop");
    ESP_LOGI(TAG, "Total PIDs: %lu", autopid_config->pid_count);

    // Initialize timers
    wc_timer_set(&ecu_check_timer, 2000);

    while (1)
    {
        static pid_type_t previous_pid_type = PID_MAX;

        dev_status_wait_for_bits(DEV_AUTOPID_ELM327_APP_BIT, portMAX_DELAY);

        if (dev_status_is_sleeping())
        {
            ESP_LOGI(TAG, "Device is sleeping, waiting for wakeup");
            obd_logger_disable();
            dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);
            ESP_LOGI(TAG, "Device awake, resuming autopid task");
            obd_logger_enable();
        }

        if (!dev_status_is_autopid_enabled())
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
        xSemaphoreTake(autopid_config->mutex, portMAX_DELAY);

        // Loop through all PIDs
        for (uint32_t i = 0; i < autopid_config->pid_count; i++)
        {
            pid_data_t *curr_pid = &autopid_config->pids[i];
            // Skip if PID type not enabled
            if ((curr_pid->pid_type == PID_STD && !autopid_config->pid_std_en) ||
                (curr_pid->pid_type == PID_CUSTOM && !autopid_config->pid_custom_en) ||
                (curr_pid->pid_type == PID_SPECIFIC && !autopid_config->pid_specific_en))
            {
                continue;
            }

            // Loop through parameters
            for (uint32_t p = 0; p < curr_pid->parameters_count; p++)
            {
                parameter_t *param = &curr_pid->parameters[p];

                // Check parameter timer
                if (wc_timer_is_expired(&param->timer))
                {
                    // autopid_data_write
                    if (curr_pid->pid_type != previous_pid_type)
                    {
                        // Send appropriate initialization based on new PID type
                        switch (curr_pid->pid_type)
                        {
                        case PID_CUSTOM:
                            if (autopid_config->custom_init && strlen(autopid_config->custom_init) > 0)
                            {
                                ESP_LOGI(TAG, "Sending custom init: %s, length: %d",
                                         autopid_config->custom_init, strlen(autopid_config->custom_init));
                                send_commands(autopid_config->custom_init, 2);
                            }
                            break;

                        case PID_STD:
                            if (autopid_config->standard_init && strlen(autopid_config->standard_init) > 0)
                            {
                                ESP_LOGI(TAG, "Sending standard init: %s, length: %d",
                                         autopid_config->standard_init, strlen(autopid_config->standard_init));
                                send_commands(autopid_config->standard_init, 2);
                            }
                            break;

                        case PID_SPECIFIC:
                            if (autopid_config->specific_init && strlen(autopid_config->specific_init) > 0)
                            {
                                ESP_LOGI(TAG, "Sending specific init: %s, length: %d",
                                         autopid_config->specific_init, strlen(autopid_config->specific_init));
                                send_commands(autopid_config->specific_init, 2);
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

                    if (curr_pid->cmd != NULL && strlen(curr_pid->cmd) > 0)
                    {
                        // twai_message_t tx_msg;

                        if (curr_pid->pid_type == PID_CUSTOM || curr_pid->pid_type == PID_SPECIFIC)
                        {
                            if (curr_pid->init != NULL && strlen(curr_pid->init) > 0)
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
                        if (elm327_process_cmd((uint8_t *)curr_pid->cmd, strlen(curr_pid->cmd), &autopidQueue, elm327_autopid_cmd_buffer, &elm327_autopid_cmd_buffer_len, &elm327_autopid_last_cmd_time, autopid_parser) == ESP_OK)
#else
                        if (elm327_process_cmd((uint8_t *)pid_req[i].pid_command, strlen(pid_req[i].pid_command), &tx_msg, &autopidQueue) == ESP_OK)
#endif
                        {
                            ESP_LOGI(TAG, "Command processed successfully");

                            if (xQueueReceive(autopidQueue, &elm327_response, pdMS_TO_TICKS(12000)) == pdPASS)
                            {
                                ESP_LOGI(TAG, "Response received, length: %lu", elm327_response.length);
                                ESP_LOG_BUFFER_HEXDUMP(TAG, elm327_response.data, 1, ESP_LOG_INFO);
                                if (strstr((char *)elm327_response.data, "error") == NULL &&
                                    strstr((char *)elm327_response.data, "SEARCHING") == NULL &&
                                    strstr((char *)elm327_response.data, "UNABLE TO CONNECT") == NULL)
                                {
                                    double result;

                                    param->failed = false;

                                    ESP_LOGI(TAG, "Response received, length: %lu", elm327_response.length);
                                    xEventGroupSetBits(xautopid_event_group, ECU_CONNECTED_BIT);
                                    // Process response based on PID type
                                    if (curr_pid->pid_type == PID_CUSTOM || curr_pid->pid_type == PID_SPECIFIC)
                                    {
                                        ESP_LOGI(TAG, "Processing custom/specific PID");
                                        if (param->expression &&
                                            evaluate_expression((uint8_t *)param->expression,
                                                                elm327_response.data, 0, &result))
                                        {
                                            if (param->min != FLT_MAX && result < param->min)
                                            {
                                                ESP_LOGW(TAG, "Parameter %s value %.2f below min %.2f - ignoring",
                                                         param->name, result, param->min);
                                            }
                                            else if (param->max != FLT_MAX && result > param->max)
                                            {
                                                ESP_LOGW(TAG, "Parameter %s value %.2f above max %.2f - ignoring",
                                                         param->name, result, param->max);
                                            }
                                            else
                                            {
                                                result = round(result * 100.0) / 100.0;
                                                ESP_LOGI(TAG, "Parameter %s result: %.2f",
                                                         param->name, result);
                                                param->value = result;
                                                autopid_config->last_successful_pid_time = time(NULL);
                                                publish_parameter_mqtt(param);
                                            }
                                        }
                                    }
                                    else if (curr_pid->pid_type == PID_STD)
                                    {
                                        ESP_LOGI(TAG, "Processing standard PID");
                                        if (curr_pid->pid_type == PID_STD)
                                        {
                                            const std_pid_t *pid_info = get_pid_from_string(param->name);
                                            if (pid_info)
                                            {
                                                ESP_LOGI(TAG, "Found PID info for: %s", param->name);
                                                // Find matching parameter in pid_info
                                                for (int p = 0; p < pid_info->num_params; p++)
                                                {
                                                    // Match parameter name after the dash
                                                    const char *param_name = strchr(param->name, '-');
                                                    if (param_name && strcmp(param_name + 1, pid_info->params[p].name) == 0)
                                                    {
                                                        esp_err_t err = ESP_FAIL;

                                                        ESP_LOGI(TAG, "Processing parameter: %s", pid_info->params[p].name);
                                                        if (elm327_response.priority_data != NULL && elm327_response.priority_data != 0)
                                                        {
                                                            err = extract_signal_value(
                                                                elm327_response.priority_data,     // Your CAN response data buffer
                                                                elm327_response.priority_data_len, // Length of your CAN response data
                                                                &pid_info->params[p],              // Parameter definition from pid_info
                                                                &param->value                      // Where to store the result
                                                            );
                                                        }
                                                        else
                                                        {
                                                            err = extract_signal_value(
                                                                elm327_response.data,   // Your CAN response data buffer
                                                                elm327_response.length, // Length of your CAN response data
                                                                &pid_info->params[p],   // Parameter definition from pid_info
                                                                &param->value           // Where to store the result
                                                            );
                                                        }

                                                        if (err != ESP_OK)
                                                        {
                                                            ESP_LOGE(TAG, "Failed to extract signal: %s", esp_err_to_name(err));
                                                            break;
                                                        }
                                                        param->value = roundf(param->value * 100.0) / 100.0;
                                                        ESP_LOGI(TAG, "Parameter %s result: %.2f %s",
                                                                 param->name,
                                                                 param->value,
                                                                 pid_info->params[p].unit);
                                                        param->value = roundf(param->value * 100.0) / 100.0;
                                                        autopid_config->last_successful_pid_time = time(NULL);
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
                        // Update pid data
                        autopid_data_update(autopid_config);
                        // pause 100ms between pid requests
                        xSemaphoreGive(autopid_config->mutex);
                        dev_status_wait_for_bits(DEV_AUTOPID_ELM327_APP_BIT, portMAX_DELAY);
                        vTaskDelay(pdMS_TO_TICKS(105));
                        xSemaphoreTake(autopid_config->mutex, portMAX_DELAY);
                    }
                    else
                    {
                        ESP_LOGE(TAG, "Failed, cmd is NULL");
                    }
                }
            }
        }

        // elm327_unlock();
        xSemaphoreGive(autopid_config->mutex);
        vTaskDelay(pdMS_TO_TICKS(100));

        // CAN filters monitor window (broadcast frames)
        // We time-slice ATMA per filter using ATCRA, then force init resend next cycle
        // to restore any RX filter required for normal PID responses.
        // CAN filters can come from either:
        // - custom filters in auto_pid.json (should still run even if Vehicle Specific PIDs are disabled)
        // - vehicle-profile filters in car_data.json (should be gated by Vehicle Specific PIDs toggle)
        if (autopid_config->can_filters_count > 0 && dev_status_is_autopid_enabled() && !dev_status_is_sleeping())
        {
            // Ensure we don't mix with pending PID responses
            while (xQueueReceive(autopidQueue, &monitor_rsp, 0) == pdPASS)
                ;

            xSemaphoreTake(autopid_config->mutex, portMAX_DELAY);

            bool did_monitor = false;
            for (uint32_t fi = 0; fi < autopid_config->can_filters_count; fi++)
            {
                can_filter_t *f = &autopid_config->can_filters[fi];
                if (f->is_vehicle_specific && !autopid_config->pid_specific_en)
                {
                    continue;
                }
                if (f->frame_id == 0 || f->parameters_count == 0)
                {
                    continue;
                }

                // Only monitor if at least one param is due
                bool any_due = false;
                for (uint32_t pi = 0; pi < f->parameters_count; pi++)
                {
                    if (wc_timer_is_expired(&f->parameters[pi].timer))
                    {
                        any_due = true;
                        break;
                    }
                }
                if (!any_due)
                {
                    continue;
                }
                ESP_LOGI(TAG, "Monitoring CAN filter frame_id=0x%X", f->frame_id);
                send_can_filter_cmd(f->frame_id);

                // Run ATMA for a bounded time slice; response_callback uses autopid_parser
                // which will enqueue one or more response_t frames into autopidQueue.
                // Stop as soon as we see the first frame line (faster than waiting the full timeout).
                ESP_LOGI(TAG, "Running ATMA for CAN filter monitoring");
                send_commands("ATCAF0\r" , 2);
                autopid_atma_parser_reset();
                autopid_atma_set_expected_frame_id(f->frame_id);
                elm327_run_command("ATMA\r", 0, 1100, &autopidQueue, autopid_atma_parser, true, f->frame_id);
                autopid_atma_set_expected_frame_id(0);
                send_commands("ATCAF1\r" , 2);
                did_monitor = true;

                // Process all captured frames as belonging to the currently-selected filter (ATCRA)
                while (xQueueReceive(autopidQueue, &monitor_rsp, 0) == pdPASS)
                {
                    process_can_filter_frame(f, &monitor_rsp);
                }
            }

            if (did_monitor)
            {
                // Clear receive address filter after monitoring
                send_commands("ATCRA\r", 2);

                // Update combined JSON snapshot including CAN-filter params
                autopid_data_update(autopid_config);

                // Force init resend for next request cycle (monitoring changes ATCRA)
                previous_pid_type = PID_MAX;
            }

            xSemaphoreGive(autopid_config->mutex);
        }

        if (wc_timer_is_expired(&ecu_check_timer))
        {
            if (all_parameters_failed(autopid_config))
            {
                xEventGroupClearBits(xautopid_event_group, ECU_CONNECTED_BIT);
                ESP_LOGW(TAG, "All parameters failed - ECU disconnected");
            }
            else
            {
                xEventGroupSetBits(xautopid_event_group, ECU_CONNECTED_BIT);
            }
            wc_timer_set(&ecu_check_timer, 2000); // Reset timer for next check
        }
    }
}

static void autopid_init_obd_logger(uint32_t log_period)
{
    ESP_LOGI(TAG, "Initializing Autopid OBD logger...");

    // Prepare parameters from autopid for the OBD logger
    obd_param_entry_t *params = NULL;
    size_t param_count = 0;

    // Allocate memory for all possible parameters
    size_t max_params = 0;
    for (uint32_t i = 0; i < autopid_config->pid_count; i++)
    {
        max_params += autopid_config->pids[i].parameters_count;
    }

    params = heap_caps_malloc(sizeof(obd_param_entry_t) * max_params, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!params)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for OBD logger parameters");
        return;
    }

    // Convert autopid parameters to OBD logger format
    for (uint32_t i = 0; i < autopid_config->pid_count; i++)
    {
        pid_data_t *pid = &autopid_config->pids[i];

        for (uint32_t j = 0; j < pid->parameters_count; j++)
        {
            parameter_t *param = &pid->parameters[j];

            // Create metadata JSON
            char *metadata = heap_caps_malloc(1024 * 4, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (!metadata)
            {
                continue;
            }

            // snprintf(metadata, 256,
            //         "{\"unit\":\"%s\",\"min\":%f,\"max\":%f,\"period\":%lu}",
            //         param->unit ? param->unit : "",
            //         param->min, param->max, param->period);
            snprintf(metadata, 1024 * 4,
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

    // create directory if not exists
    if (mkdir(DB_ROOT_PATH "/" DB_DIR_NAME, 0755) != 0)
    {
        // Ignore error if directory already exists
        if (errno != EEXIST)
        {
            ESP_LOGE(TAG, "Failed to create directory %s: %s", DB_ROOT_PATH "/" DB_DIR_NAME, strerror(errno));
        }
    }
    else
    {
        ESP_LOGI(TAG, "Created directory: %s", DB_ROOT_PATH "/" DB_DIR_NAME);
    }

    static obd_logger_t obd_logger = {
        .path = DB_ROOT_PATH "/" DB_DIR_NAME,
        .db_filename = DB_ROOT_PATH "/" DB_DIR_NAME "/" DB_DIR_NAME,
        .obd_logger_get_params_cb = autopid_data_read};
    obd_logger.period_sec = log_period;
    obd_logger.obd_logger_params = params;
    obd_logger.obd_logger_params_count = param_count;

    if (odb_logger_init(&obd_logger) != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to initialize OBD logger");
        return;
    }

    // Free allocated memory
    for (size_t i = 0; i < param_count; i++)
    {
        free((void *)params[i].name);
        free((void *)params[i].metadata);
    }
    free(params);

    ESP_LOGI(TAG, "OBD logger initialized with %zu parameters", param_count);
}

void print_pids(autopid_config_t *autopid_config)
{
    const char *pid_type_str[] = {"Standard", "Custom", "Specific"};

    printf("Total PIDs: %lu\n", autopid_config->pid_count);
    printf("Custom Init: %s\n", autopid_config->custom_init);
    printf("Specific Init: %s\n", autopid_config->specific_init);

    for (int i = 0; i < autopid_config->pid_count; i++)
    {
        pid_data_t *pid = &autopid_config->pids[i];
        printf("\nPID %d:\n", i + 1);
        printf("  Type: %s\n", pid_type_str[pid->pid_type]);
        printf("  Command: %s\n", pid->cmd ? pid->cmd : "NULL");
        printf("  Init: %s\n", pid->init ? pid->init : "NULL");
        printf("  Period: %lu\n", pid->period);

        printf("  Parameter Count: %lu\n", pid->parameters_count);
        if (pid->parameters)
        {
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

void autopid_app_reset_timer(void)
{
    if (autopid_bit_set_timer_handle != NULL)
    {
        if (xTimerReset(autopid_bit_set_timer_handle, 0) != pdPASS)
        {
            ESP_LOGE(TAG, "Failed to reset autopid bit set timer");
        }
        else
        {
            ESP_LOGI(TAG, "Autopid bit set timer reset");
        }
    }
}

static void autopid_app_setbit_timer_callback(TimerHandle_t xTimer)
{
    ESP_LOGI(TAG, "Timer callback called every 10 seconds");
    dev_status_set_bits(DEV_AUTOPID_ELM327_APP_BIT);
}

void autopid_init(char *id, bool enable_logging, uint32_t logging_period)
{
    device_id = id;
    // if(autopid_data.mutex == NULL)
    // {
    //     autopid_data.mutex = xSemaphoreCreateMutex();
    // }

    if (xautopid_event_group == NULL)
    {
        // Create a static event group for autopid
        xautopid_event_group = xEventGroupCreateStatic(&xautopid_event_group_buffer);
    }

#if HARDWARE_VER == WICAN_PRO
    auto_pid_buf = (char *)heap_caps_malloc(AUTOPID_BUFFER_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (auto_pid_buf == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate auto_pid_buf in PSRAM");
        return;
    }
    memset(auto_pid_buf, 0, AUTOPID_BUFFER_SIZE);

    elm327_autopid_cmd_buffer = (char *)heap_caps_malloc(AUTOPID_BUFFER_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (elm327_autopid_cmd_buffer == NULL)
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
    static uint8_t *autopidQueue_storage;

    // Allocate queue storage in PSRAM
    autopidQueue_storage = (uint8_t *)heap_caps_malloc(QUEUE_SIZE * sizeof(response_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
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

    if (protocolnumberQueue == NULL)
    {
        ESP_LOGE(TAG, "Failed to create static protocol queue");
        return;
    }
    autopid_set_protocol_number(-1); // Initialize with -1

    autopid_config = load_autopid_config();

    if (autopid_config)
    {
        autopid_config->mutex = xSemaphoreCreateMutex();

        // if(autopid_config->pid_count > 0){
        //     print_pids(autopid_config); //broken
        // }

        if (!autopid_config->mutex)
        {
            ESP_LOGE(TAG, "Failed to create autopid_config mutex");
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
        ESP_LOGE(TAG, "autopid_config is NULL");
        return;
    }

    if (autopid_config->pid_count == 0)
    {
        ESP_LOGE(TAG, "No PIDs found in car_data.json or auto_pid.json");
        return;
    }
    // Build and cache config JSON once after loading autopid_config
    (void)autopid_get_config();
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

    if (dev_status_is_bit_set(DEV_SDCARD_MOUNTED_BIT))
    {
        ESP_LOGI(TAG, "SD Card mounted");
    }
    else
    {
        ESP_LOGI(TAG, "SD Card not mounted");
    }

    // Initialize OBD logger if enabled and SD card is mounted
    if (enable_logging && dev_status_is_bit_set(DEV_SDCARD_MOUNTED_BIT))
    {
        xSemaphoreTake(autopid_config->mutex, portMAX_DELAY);
        autopid_init_obd_logger(logging_period);
        xSemaphoreGive(autopid_config->mutex);
    }

    static StackType_t *autopid_task_stack;
    static StaticTask_t autopid_task_buffer;
    static const size_t autopid_task_stack_size = (1024 * 10);
    // Allocate stack memory in PSRAM
    autopid_task_stack = heap_caps_malloc(autopid_task_stack_size, MALLOC_CAP_SPIRAM);
    if (autopid_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate autopid_task stack in PSRAM");
        return;
    }
    memset(autopid_task_stack, 0, autopid_task_stack_size);
    // Check if memory allocation was successful
    if (autopid_task_stack != NULL)
    {
        // Create task with static allocation
        xTaskCreateStatic(autopid_task, "autopid_task", autopid_task_stack_size, (void *)AF_INET, 5,
                          autopid_task_stack, &autopid_task_buffer);
    }
    else
    {
        // Handle memory allocation failure
        ESP_LOGE(TAG, "Failed to allocate autopid_task stack in PSRAM");
    }

    if (strcmp("enable", autopid_config->grouping) == 0)
    {
        static StackType_t *autopid_publish_task_stack;
        static StaticTask_t autopid_publish_task_buffer;
        static const size_t autopid_publish_task_stack_size = (1024 * 8);
        // Allocate stack memory in PSRAM
        autopid_publish_task_stack = heap_caps_malloc(autopid_publish_task_stack_size, MALLOC_CAP_SPIRAM);
        if (autopid_publish_task_stack == NULL)
        {
            ESP_LOGE(TAG, "Failed to allocate autopid_publish_task stack in PSRAM");
        }
        else
        {
            memset(autopid_publish_task_stack, 0, autopid_publish_task_stack_size);
            if (xTaskCreateStatic(autopid_publish_task, "autopid_publish_task", autopid_publish_task_stack_size,
                                  NULL, 5, autopid_publish_task_stack, &autopid_publish_task_buffer) != NULL)
            {
                ESP_LOGI(TAG, "Autopid publish task created");
            }
            else
            {
                ESP_LOGE(TAG, "Failed to create autopid_publish_task");
            }
        }
    }

    ha_webhooks_init();
    // Create webhook task
    static StackType_t *autopid_webhook_task_stack;
    static StaticTask_t autopid_webhook_task_buffer;
    static const size_t autopid_webhook_task_stack_size = (1024 * 20);
    // Allocate stack memory in PSRAM
    autopid_webhook_task_stack = heap_caps_malloc(autopid_webhook_task_stack_size, MALLOC_CAP_SPIRAM);
    if (autopid_webhook_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate autopid_webhook_task stack in PSRAM");
    }
    else
    {
        memset(autopid_webhook_task_stack, 0, autopid_webhook_task_stack_size);
        if (xTaskCreateStatic(autopid_webhook_task, "autopid_webhook_task", autopid_webhook_task_stack_size,
                              NULL, 5, autopid_webhook_task_stack, &autopid_webhook_task_buffer) != NULL)
        {
            ESP_LOGI(TAG, "Autopid webhook task created");
        }
        else
        {
            ESP_LOGE(TAG, "Failed to create autopid_webhook_task");
        }
    }

    if (dev_status_is_smartconnect_enabled())
    {
        dev_status_clear_autopid_enabled(); // controller by smartconnect
        ESP_LOGI(TAG, "Autopid is controlled by SmartConnect, disabling autopid");
    }
    else
    {
        dev_status_set_autopid_enabled();
        ESP_LOGI(TAG, "Autopid enabled");
    }
    dev_status_set_bits(DEV_AUTOPID_ELM327_APP_BIT);

    // Create a FreeRTOS static timer to call a function every 10 seconds
    if (autopid_bit_set_timer_handle == NULL)
    {
        autopid_bit_set_timer_handle = xTimerCreateStatic(
            "autopid_bit_set_timer",           // Timer name
            pdMS_TO_TICKS(10000),              // Period: 10 seconds
            pdTRUE,                            // Auto-reload
            NULL,                              // Timer ID
            autopid_app_setbit_timer_callback, // Callback function
            &autopid_bit_set_timer_buffer      // Static buffer
        );
        if (autopid_bit_set_timer_handle != NULL)
        {
            xTimerStart(autopid_bit_set_timer_handle, 0);
        }
        else
        {
            ESP_LOGE(TAG, "Failed to create static timer");
        }
    }
}
