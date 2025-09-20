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

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "esp_log.h"
#include "driver/uart.h"
#include "driver/gpio.h"
#include "obd.h"
#include "elm327.h"
#include "config_server.h"
#include "hw_config.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

/* Defines and Constants */
// #define TAG                     __func__
#define TAG                     "OBD"

#define check_command(a,b)      (strcmp(a, b) == 0)
#define GET_VOLTAGE_CMD         "ATRV\r"
#define GET_SLEEP_CONFIG_CMD    "STSLCS\r"
#define BUF_SIZE               1024

/* Global Variables */
static QueueHandle_t battery_voltage_queue;
static StaticQueue_t battery_voltage_queue_struct;
static uint8_t battery_voltage_queue_storage[sizeof(float)];

/* Helper Functions for Parsing */
static int parse_on_off(const char* str)
{
    return (strcmp(str, "ON") == 0) ? 1 : 0;
}

static int parse_high_low(const char* str)
{
    return (strcmp(str, "HIGH") == 0) ? 1 : 0;
}

static uint32_t parse_time(const char* time_str)
{
    uint32_t time_value = 0;
    char unit[5];

    if (sscanf(time_str, "%lu %4s", &time_value, unit) == 2)
    {
        ESP_LOGI(TAG, "Parsed time: %lu %s\n", time_value, unit);
        
        if (strcmp(unit, "ms") == 0)
        {
            return time_value;
        }
        else if (strcmp(unit, "s") == 0)
        {
            return time_value;
        }
    }

    ESP_LOGI(TAG, "Time parsing failed for: %s\n", time_str);
    return 0;
}

/* STSLCS Configuration Parsing Functions */
static void parse_line(const char* line, stslcs_config_t* config)
{
    char buffer[256];
    char enable_buffer[10];
    char level_buffer[10];
    char time_buffer[50];
    float voltage = 0;

    ESP_LOGI(TAG, "Parsing line: %s", line);

    if (strstr(line, "CTRL MODE"))
    {
        sscanf(line, "CTRL MODE: %s", config->ctrl_mode);
        ESP_LOGI(TAG, "CTRL MODE: %s", config->ctrl_mode);
    }
    else if (strstr(line, "PWR_CTRL"))
    {
        sscanf(line, "PWR_CTRL: %*[^=]= %s", buffer);
        config->pwr_ctrl = parse_high_low(buffer);
        ESP_LOGI(TAG, "PWR_CTRL: %d", config->pwr_ctrl);
    }
    else if (strstr(line, "UART_SLEEP"))
    {
        sscanf(line, "UART_SLEEP: %[^,], %49[^\n]", enable_buffer, time_buffer);
        config->uart_sleep.en = parse_on_off(enable_buffer);
        config->uart_sleep.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "UART_SLEEP: en=%d, time=%lu ms", config->uart_sleep.en, config->uart_sleep.time);
    }
    else if (strstr(line, "UART_WAKE"))
    {
        sscanf(line, "UART_WAKE: %[^,], %lu-%lu", enable_buffer, &config->uart_wake.min_time, &config->uart_wake.max_time);
        config->uart_wake.en = parse_on_off(enable_buffer);
        ESP_LOGI(TAG, "UART_WAKE: en=%d, min_time=%lu us, max_time=%lu us", 
                config->uart_wake.en, config->uart_wake.min_time, config->uart_wake.max_time);
    }
    else if (strstr(line, "EXT_INPUT"))
    {
        sscanf(line, "EXT_INPUT: %s", level_buffer);
        config->ext_input.level = parse_high_low(level_buffer);
        ESP_LOGI(TAG, "EXT_INPUT: level=%d", config->ext_input.level);
    }
    else if (strstr(line, "EXT_SLEEP"))
    {
        sscanf(line, "EXT_SLEEP: %[^,], %[^,], FOR %49[^\n]", enable_buffer, level_buffer, time_buffer);
        config->ext_sleep.en = parse_on_off(enable_buffer);
        config->ext_sleep.level = parse_high_low(level_buffer);
        config->ext_sleep.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "EXT_SLEEP: en=%d, level=%d, time=%lu ms", 
                config->ext_sleep.en, config->ext_sleep.level, config->ext_sleep.time);
    }
    else if (strstr(line, "EXT_WAKE"))
    {
        sscanf(line, "EXT_WAKE: %[^,], %[^,], FOR %49[^\n]", enable_buffer, level_buffer, time_buffer);
        config->ext_wake.en = parse_on_off(enable_buffer);
        config->ext_wake.level = parse_high_low(level_buffer);
        config->ext_wake.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "EXT_WAKE: en=%d, level=%d, time=%lu ms", 
                config->ext_wake.en, config->ext_wake.level, config->ext_wake.time);
    }
    else if (strstr(line, "VL_SLEEP"))
    {
        sscanf(line, "VL_SLEEP: %[^,], <%fV FOR %49[^\n]", enable_buffer, &voltage, time_buffer);
        config->vl_sleep.en = parse_on_off(enable_buffer);
        config->vl_sleep.voltage = voltage;
        config->vl_sleep.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "VL_SLEEP: en=%d, voltage=%.2f V, time=%lu s", 
                config->vl_sleep.en, config->vl_sleep.voltage, config->vl_sleep.time);
    }
    else if (strstr(line, "VL_WAKE"))
    {
        if (sscanf(line, "VL_WAKE: %[^,], >!%fV FOR %49[^\n]", enable_buffer, &voltage, time_buffer) != 3)
        {
            sscanf(line, "VL_WAKE: %[^,], >%fV FOR %49[^\n]", enable_buffer, &voltage, time_buffer);
        }
        config->vl_wake.en = parse_on_off(enable_buffer);
        config->vl_wake.voltage = voltage;
        config->vl_wake.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "VL_WAKE: en=%d, voltage=%.2f V, time=%lu ms", 
                config->vl_wake.en, config->vl_wake.voltage, config->vl_wake.time);
    }
    else if (strstr(line, "VCHG WAKE"))
    {
        sscanf(line, "VCHG WAKE: %[^,], %fV IN %49[^\n]", enable_buffer, &voltage, time_buffer);
        config->vchg_wake.en = parse_on_off(enable_buffer);
        config->vchg_wake.voltage_change = voltage;
        config->vchg_wake.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "VCHG_WAKE: en=%d, voltage_change=%.2f V, time=%lu ms", 
                config->vchg_wake.en, config->vchg_wake.voltage_change, config->vchg_wake.time);
    }
}

static void parse_stslcs_response(const char* response, stslcs_config_t* config)
{
    ESP_LOGI(TAG, "Starting parsing of response...");
    
    char* response_copy = strdup(response);
    char* line = strtok(response_copy, "\r\n");
    while (line != NULL)
    {
        parse_line(line, config);
        line = strtok(NULL, "\r\n");
    }

    free(response_copy);
    ESP_LOGI(TAG, "Parsing complete.");
}

/* OBD Response Parsing Function */
static void obd_parse_response(char *str, uint32_t len, QueueHandle_t *q, char* cmd_str)
{
    if(strcmp(cmd_str, GET_VOLTAGE_CMD) == 0)
    {
        const unsigned char *ptr;
        float val;
        int buffer_index = 0;
        ESP_LOGI(TAG, "Response");
        
        if (str != NULL && len > 0)
        {
            ESP_LOG_BUFFER_HEXDUMP(TAG, str, len, ESP_LOG_WARN);   
            ptr = (unsigned char *)str;

            while (*ptr)
            {
                if (isdigit(*ptr) || *ptr == '.' || (*ptr == '-' && isdigit(*(ptr + 1))))
                {
                    while (isdigit(*ptr) || *ptr == '.')
                    {
                        str[buffer_index++] = *ptr++;
                    }
                    str[buffer_index] = '\0';
                    
                    val = atof((char*)str);
                    xQueueSend(battery_voltage_queue, &val, pdMS_TO_TICKS(500));
                    ESP_LOGW(TAG, "cmd: %s, val: %f", cmd_str, val);
                    return;
                }
                ptr++;
            }
            return;
        }
    }
    else if(check_command(cmd_str, GET_SLEEP_CONFIG_CMD))
    {
        static stslcs_config_t config;
        parse_stslcs_response(str, &config);
        static char response_buffer[32];
        static uint32_t response_len = 0;
        static int64_t response_cmd_time = 0;
        
        // if(config_server_get_sleep_config() == 1)
        {
            static char sleep_cmd[32] = {0};
            static char wake_cmd[32] = {0};
            float sleep_voltage;
            float wakeup_voltage;
            uint32_t sleep_time;
            if(config_server_get_sleep_config() == 1)
            {
                ESP_LOGW(TAG, "Sleep mode enabled");
            }
            else
            {
                ESP_LOGW(TAG, "Sleep mode disabled");
            }
            if(config_server_get_wakeup_volt(&wakeup_voltage) == -1)
            {
                wakeup_voltage = 13.5f;
                ESP_LOGE(TAG, "Failed to get sleep voltage");
            }
            if(config_server_get_sleep_volt(&sleep_voltage) == -1)
            {
                sleep_voltage = 13.2f;
                ESP_LOGE(TAG, "Failed to get wakeup voltage");
            }
            if(config_server_get_sleep_time(&sleep_time) == -1)
            {
                sleep_time = 120;
                ESP_LOGE(TAG, "Failed to get sleep time");
            }
            else
            {
                sleep_time *= 60; //change to sec
                sleep_time += 30;
            }
            
            if(strcmp(config.ctrl_mode, "ELM327") == 0)
            {
                elm327_process_cmd((uint8_t *)"ATPP 0E SV 7A\r", 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                elm327_process_cmd((uint8_t *)"ATPP 0E ON\r", 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                elm327_process_cmd((uint8_t *)"ATZ\r", 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                vTaskDelay(pdMS_TO_TICKS(100));
                ESP_LOGW(TAG, "Setting sleep mode to Native");
            }

            // if(config.uart_wake.en == 1 || config.vl_wake.en == 0 || 
            //    config.vl_wake.voltage != sleep_voltage || 
            //    config.vl_sleep.en == 0 || 
            //    config.vl_sleep.voltage != (sleep_voltage - tmp))
            if(config.uart_wake.en == 1 || config.uart_sleep.en == 1 || config.vl_wake.en == 1 || 
                config.vl_wake.voltage != wakeup_voltage || 
                config.vl_sleep.en == 1 || 
                config.vl_sleep.voltage != sleep_voltage ||
                config.vl_sleep.time != sleep_time)
            {
                sprintf(sleep_cmd, "STSLVLW >%.2f, 1\r", wakeup_voltage);
                sprintf(wake_cmd, "STSLVLS <%.2f, %lu\r", sleep_voltage, sleep_time);
                elm327_process_cmd((uint8_t *)sleep_cmd, 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                elm327_process_cmd((uint8_t *)wake_cmd, 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                elm327_process_cmd((uint8_t *)"STSLVl off,off\r", 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                elm327_process_cmd((uint8_t *)"STSLU off, off\r", 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                vTaskDelay(pdMS_TO_TICKS(10));

                elm327_process_cmd((uint8_t *)"ATPP 0F SV 95\r", 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                vTaskDelay(pdMS_TO_TICKS(10));
                elm327_process_cmd((uint8_t *)"ATPP 0F ON\r", 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                vTaskDelay(pdMS_TO_TICKS(10));
                elm327_process_cmd((uint8_t *)"STSLUIT 1200\r", 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                vTaskDelay(pdMS_TO_TICKS(10));

                elm327_process_cmd((uint8_t *)"ATZ\r", 0, NULL, response_buffer, 
                                &response_len, &response_cmd_time, NULL);
                vTaskDelay(pdMS_TO_TICKS(100));
                ESP_LOGW(TAG, "Setting sleep parameters");
                // elm327_disable_wake_commands();
            }               
        }
        // else
        // {
        //     ESP_LOGW(TAG, "Sleep mode disabled");
        //     if(config.vl_wake.en == 1 || config.vl_sleep.en == 1)
        //     {
        //         elm327_process_cmd((uint8_t *)"STSLVl off,off\r", 0, NULL, response_buffer, 
        //                         &response_len, &response_cmd_time, NULL);
        //         elm327_process_cmd((uint8_t *)"ATZ\r", 0, NULL, response_buffer, 
        //                         &response_len, &response_cmd_time, NULL);
        //         vTaskDelay(pdMS_TO_TICKS(100));
        //     }
        // }
    }
    else if(check_command(cmd_str, "STSBR2000000\r"))
    {
        uart_set_baudrate(UART_NUM_1, 2000000);
        printf("here\r\n");
    }
}

esp_err_t obd_get_voltage(float *val)
{
    static char response_buffer[32];
    static uint32_t response_len = 0;
    static int64_t response_cmd_time = 0;

    elm327_process_cmd((uint8_t *)GET_VOLTAGE_CMD, strlen(GET_VOLTAGE_CMD), NULL,
                      response_buffer, &response_len, &response_cmd_time, obd_parse_response);

    if (xQueueReceive(battery_voltage_queue, val, pdMS_TO_TICKS(1500)) == pdPASS)
    {
        return ESP_OK;
    }
    else
    {
        *val = 0;
        return ESP_FAIL;
    }
}

void obd_init(void)
{
    ESP_LOGI(TAG, "Initializing OBD");

    battery_voltage_queue = xQueueCreateStatic(1, sizeof(float), battery_voltage_queue_storage, &battery_voltage_queue_struct);

    float obd_voltage = 0;

    // obd_get_voltage(&obd_voltage);
    // ESP_LOGI(TAG, "obd_voltage: %0.1f", obd_voltage);
    vTaskDelay(pdMS_TO_TICKS(100));

    static char cmd_buffer[16];
    static uint32_t cmd_buffer_len = 0;
    static int64_t response_cmd_time = 0;
    ESP_LOGI(TAG, "Sending Sleep command");
    elm327_process_cmd((uint8_t *)GET_SLEEP_CONFIG_CMD, 
                      strlen(GET_SLEEP_CONFIG_CMD), 
                      NULL, 
                      cmd_buffer, 
                      &cmd_buffer_len, 
                      &response_cmd_time, 
                      obd_parse_response);
    ESP_LOGI(TAG, "Waiting for Sleep command response");                  
    vTaskDelay(pdMS_TO_TICKS(500));
}