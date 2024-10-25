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

#define TAG                     __func__
#define check_command(a,b)      (strcmp(a, b) == 0)
#define GET_VOLTAGE_CMD         "ATRV\r"
#define GET_SLEEP_CONFIG_CMD    "STSLCS\r"
#define BUF_SIZE    1024

QueueHandle_t uart1_queue = NULL;
static QueueHandle_t xobd_cmd_queue = NULL;
SemaphoreHandle_t xuart1_semaphore = NULL;
static QueueHandle_t battery_voltage_queue;

// Helper function to parse ON/OFF to int
static int parse_on_off(const char* str)
{
    return (strcmp(str, "ON") == 0) ? 1 : 0;
}

// Helper function to parse HIGH/LOW to int
static int parse_high_low(const char* str)
{
    return (strcmp(str, "HIGH") == 0) ? 1 : 0;
}

// Helper function to parse time, stripping the "ms" or "s" from the numeric value
static uint32_t parse_time(const char* time_str)
{
    uint32_t time_value = 0;
    char unit[5];  // To hold the unit (e.g., "ms" or "s")

    // Use sscanf to extract the numeric value and the unit
    if (sscanf(time_str, "%lu %4s", &time_value, unit) == 2)
    {
        ESP_LOGI(TAG, "Parsed time: %lu %s\n", time_value, unit);  // Debugging output

        // Return the time value in milliseconds if the unit is "ms", otherwise convert seconds to milliseconds
        if (strcmp(unit, "ms") == 0)
        {
            return time_value;  // Already in milliseconds
        }
        else if (strcmp(unit, "s") == 0)
        {
            return time_value * 1000;  // Convert seconds to milliseconds
        }
    }

    // If the unit isn't recognized or parsing fails, return 0 as a default
    ESP_LOGI(TAG, "Time parsing failed for: %s\n", time_str);  // Debugging failed case
    return 0;
}

// Helper function to parse a single line of the response
static void parse_line(const char* line, stslcs_config_t* config)
{
    char buffer[256];
    char enable_buffer[10];
    char level_buffer[10];
    char time_buffer[50];
    float voltage = 0;

    ESP_LOGI(TAG, "Parsing line: %s", line);  // Debugging each line

    // Parse CTRL MODE
    if (strstr(line, "CTRL MODE"))
    {
        sscanf(line, "CTRL MODE: %s", config->ctrl_mode);
        ESP_LOGI(TAG, "CTRL MODE: %s", config->ctrl_mode);
    }
    // Parse PWR_CTRL
    else if (strstr(line, "PWR_CTRL"))
    {
        sscanf(line, "PWR_CTRL: %*[^=]= %s", buffer);
        config->pwr_ctrl = parse_high_low(buffer);
        ESP_LOGI(TAG, "PWR_CTRL: %d", config->pwr_ctrl);
    }
    // Parse UART_SLEEP
    else if (strstr(line, "UART_SLEEP"))
    {
        sscanf(line, "UART_SLEEP: %[^,], %49[^\n]", enable_buffer, time_buffer);
        config->uart_sleep.en = parse_on_off(enable_buffer);
        config->uart_sleep.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "UART_SLEEP: en=%d, time=%lu ms", config->uart_sleep.en, config->uart_sleep.time);
    }
    // Parse UART_WAKE
    else if (strstr(line, "UART_WAKE"))
    {
        sscanf(line, "UART_WAKE: %[^,], %lu-%lu", enable_buffer, &config->uart_wake.min_time, &config->uart_wake.max_time);
        config->uart_wake.en = parse_on_off(enable_buffer);
        ESP_LOGI(TAG, "UART_WAKE: en=%d, min_time=%lu us, max_time=%lu us", config->uart_wake.en, config->uart_wake.min_time, config->uart_wake.max_time);
    }
    // Parse EXT_INPUT
    else if (strstr(line, "EXT_INPUT"))
    {
        sscanf(line, "EXT_INPUT: %s", level_buffer);
        config->ext_input.level = parse_high_low(level_buffer);
        ESP_LOGI(TAG, "EXT_INPUT: level=%d", config->ext_input.level);
    }
    // Parse EXT_SLEEP
    else if (strstr(line, "EXT_SLEEP"))
    {
        sscanf(line, "EXT_SLEEP: %[^,], %[^,], FOR %49[^\n]", enable_buffer, level_buffer, time_buffer);
        config->ext_sleep.en = parse_on_off(enable_buffer);
        config->ext_sleep.level = parse_high_low(level_buffer);
        config->ext_sleep.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "EXT_SLEEP: en=%d, level=%d, time=%lu ms", config->ext_sleep.en, config->ext_sleep.level, config->ext_sleep.time);
    }
    // Parse EXT_WAKE
    else if (strstr(line, "EXT_WAKE"))
    {
        sscanf(line, "EXT_WAKE: %[^,], %[^,], FOR %49[^\n]", enable_buffer, level_buffer, time_buffer);
        config->ext_wake.en = parse_on_off(enable_buffer);
        config->ext_wake.level = parse_high_low(level_buffer);
        config->ext_wake.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "EXT_WAKE: en=%d, level=%d, time=%lu ms", config->ext_wake.en, config->ext_wake.level, config->ext_wake.time);
    }
    // Parse VL_SLEEP
    else if (strstr(line, "VL_SLEEP"))
    {
        sscanf(line, "VL_SLEEP: %[^,], <%fV FOR %49[^\n]", enable_buffer, &voltage, time_buffer);
        config->vl_sleep.en = parse_on_off(enable_buffer);
        config->vl_sleep.voltage = voltage;
        config->vl_sleep.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "VL_SLEEP: en=%d, voltage=%.2f V, time=%lu ms", config->vl_sleep.en, config->vl_sleep.voltage, config->vl_sleep.time);
    }
    // Parse VL_WAKE
    else if (strstr(line, "VL_WAKE"))
    {
        if (sscanf(line, "VL_WAKE: %[^,], >!%fV FOR %49[^\n]", enable_buffer, &voltage, time_buffer) != 3)
        {
            sscanf(line, "VL_WAKE: %[^,], >%fV FOR %49[^\n]", enable_buffer, &voltage, time_buffer);
        }
        config->vl_wake.en = parse_on_off(enable_buffer);
        config->vl_wake.voltage = voltage;
        config->vl_wake.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "VL_WAKE: en=%d, voltage=%.2f V, time=%lu ms", config->vl_wake.en, config->vl_wake.voltage, config->vl_wake.time);
    }
    // Parse VCHG WAKE
    else if (strstr(line, "VCHG WAKE"))
    {
        sscanf(line, "VCHG WAKE: %[^,], %fV IN %49[^\n]", enable_buffer, &voltage, time_buffer);
        config->vchg_wake.en = parse_on_off(enable_buffer);
        config->vchg_wake.voltage_change = voltage;
        config->vchg_wake.time = parse_time(time_buffer);
        ESP_LOGI(TAG, "VCHG_WAKE: en=%d, voltage_change=%.2f V, time=%lu ms", config->vchg_wake.en, config->vchg_wake.voltage_change, config->vchg_wake.time);
    }
}

// Main parsing function
static void parse_stslcs_response(const char* response, stslcs_config_t* config)
{
    ESP_LOGI(TAG, "Starting parsing of response...");
    
    // Split the response into lines and process each one
    char* response_copy = strdup(response);
    char* line = strtok(response_copy, "\r\n");
    while (line != NULL)
    {
        parse_line(line, config);
        line = strtok(NULL, "\r\n");
    }

    free(response_copy);  // Free the copied response
    ESP_LOGI(TAG, "Parsing complete.");
}

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
        // ESP_LOG_BUFFER_HEXDUMP(TAG, str, len, ESP_LOG_WARN);
        static stslcs_config_t config;
        parse_stslcs_response(str, &config);
        static char response_buffer[32];
        static uint32_t response_len = 0;
        static int64_t response_cmd_time = 0;
        
        if(config_server_get_sleep_config() == 1)
        {
            static char sleep_cmd[32] = {0};
            static char wake_cmd[32] = {0};
            float sleep_voltage = 0;
            ESP_LOGW(TAG, "Sleep mode enabled");

            if(config_server_get_sleep_volt(&sleep_voltage) != -1)
            {
                float tmp = 0.2;    //Temporary value

                if(config.uart_wake.en == 1 || config.vl_wake.en == 0 || config.vl_wake.voltage != sleep_voltage|| config.vl_sleep.en == 0 || config.vl_sleep.voltage != (sleep_voltage - tmp))
                {
                    if(strcmp(config.ctrl_mode, "ELM327") == 0)
                    {
                        elm327_process_cmd((uint8_t *)"ATPP 0E SV 7A\r", 0, NULL, response_buffer, &response_len, &response_cmd_time, NULL);
                        elm327_process_cmd((uint8_t *)"ATPP 0E ON\r", 0, NULL, response_buffer, &response_len, &response_cmd_time, NULL);
                        elm327_process_cmd((uint8_t *)"ATZ\r", 0, NULL, response_buffer, &response_len, &response_cmd_time, NULL);
                        vTaskDelay(pdMS_TO_TICKS(100));
                        ESP_LOGW(TAG, "Setting sleep mode to Native");
                    }
                    
                    sprintf(sleep_cmd, "STSLVLW >%.2f, 1\r", sleep_voltage);
                    sprintf(wake_cmd, "STSLVLS <%.2f, 120\r", sleep_voltage-tmp);
                    elm327_process_cmd((uint8_t *)sleep_cmd, 0, NULL, response_buffer, &response_len, &response_cmd_time, NULL);
                    elm327_process_cmd((uint8_t *)wake_cmd, 0, NULL, response_buffer, &response_len, &response_cmd_time, NULL);
                    elm327_process_cmd((uint8_t *)"STSLVl on,on\r", 0, NULL, response_buffer, &response_len, &response_cmd_time, NULL);
                    elm327_process_cmd((uint8_t *)"STSLU off, off\r", 0, NULL, response_buffer, &response_len, &response_cmd_time, NULL);
                    vTaskDelay(pdMS_TO_TICKS(10));
                    ESP_LOGW(TAG, "Setting sleep paramters");
                }   
            }             
            else
            {
                ESP_LOGE(TAG, "Failed to set sleep paramters");
            }
        }
        else
        {
            ESP_LOGW(TAG, "Sleep mode disabled");
            if(config.vl_wake.en == 1 || config.vl_sleep.en == 1)
            {
                elm327_process_cmd((uint8_t *)"STSLVl off,off\r", 0, NULL, response_buffer, &response_len, &response_cmd_time, NULL);
                elm327_process_cmd((uint8_t *)"ATZ\r", 0, NULL, response_buffer, &response_len, &response_cmd_time, NULL);
                vTaskDelay(pdMS_TO_TICKS(100));
            }
        }
    }
    else if(check_command(cmd_str, "STSBR2000000\r"))
    {
        uart_set_baudrate(UART_NUM_1, 2000000);
        printf("here\r\n");
    }
}

// Function to get voltage from OBD
int8_t obd_get_voltage(float *val)
{
    static char response_buffer[32];
    static uint32_t response_len = 0;
    static int64_t response_cmd_time = 0;

    elm327_process_cmd((uint8_t *)GET_VOLTAGE_CMD, strlen(GET_VOLTAGE_CMD), NULL, response_buffer, &response_len, &response_cmd_time, obd_parse_response);

    if (xQueueReceive(battery_voltage_queue, val, pdMS_TO_TICKS(500)) == pdPASS)
    {
        printf("Value received: %.2f\n", *val);
        return 1;
    }
    else
    {
        *val = 0;
        return -1;
    }
}

void obd_hardreset_chip(void)
{
    char* rsp_buffer = NULL;
    uint32_t rsp_len;

    gpio_set_level(OBD_RESET_PIN, 0);
    vTaskDelay(pdMS_TO_TICKS(5));
    gpio_set_level(OBD_RESET_PIN, 1);

    // vTaskDelay(pdMS_TO_TICKS(1000));
    // uart_set_baudrate(UART_NUM_1, 115200);
    // vTaskDelay(pdMS_TO_TICKS(100));
    // obd_write_cmd("ATI\r", rsp_buffer, &rsp_len, 2000);
    // obd_write_cmd("stsbr2000000\r", rsp_buffer, &rsp_len, 2000);
    // obd_read_rsp(rsp_buffer, &rsp_len, 1000);
    
    // vTaskDelay(pdMS_TO_TICKS(100));
    // uart_set_baudrate(UART_NUM_1, 2000000);
    // vTaskDelay(pdMS_TO_TICKS(100));
    // uart_flush(UART_NUM_1);
    // obd_write_cmd("\r", rsp_buffer, &rsp_len, 2000);
    // obd_write_cmd("\r", rsp_buffer, &rsp_len, 2000);
    // obd_write_cmd("ATI\r", rsp_buffer, &rsp_len, 2000);
}

void obd_send_cmd(char *cmd, QueueHandle_t *rsp_queue, obd_rsp_t *obd_rsp, uint32_t timeout_ms)
{
    ESP_LOGI(TAG, "Sending command: %s", cmd);
    
    obd_cmd_t *obd_cmd = (obd_cmd_t *)malloc(sizeof(obd_cmd_t));
    if (obd_cmd == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for obd_cmd");
        return;
    }

    strncpy(obd_cmd->cmd, cmd, OBD_CMD_BUF_SIZE - 1); // Copy command to obd_cmd structure
    obd_cmd->cmd[OBD_CMD_BUF_SIZE - 1] = '\0';        // Ensure null-terminated string
    obd_cmd->rsp_queue = rsp_queue;
    obd_cmd->obd_rsp = obd_rsp;
    obd_cmd->timeout_ms = timeout_ms;
    if (xQueueSend(xobd_cmd_queue, (void*)obd_cmd, portMAX_DELAY) != pdPASS)
    {
        ESP_LOGE(TAG, "Failed to send command to queue");
    }
    free(obd_cmd); // Free memory if send fails
}

void obd_init(void)
{
    ESP_LOGI(TAG, "Initializing OBD");

    gpio_reset_pin(42);
    gpio_set_direction(42, GPIO_MODE_OUTPUT);
    gpio_set_level(42, 1);
    vTaskDelay(pdMS_TO_TICKS(100));

    battery_voltage_queue = xQueueCreate(1, sizeof(float));

    gpio_reset_pin(OBD_RESET_PIN);
    gpio_set_direction(OBD_RESET_PIN, GPIO_MODE_OUTPUT_OD);

    // gpio_set_level(41, 0);
    // vTaskDelay(pdMS_TO_TICKS(10));
    // gpio_set_level(41, 1);
    // vTaskDelay(pdMS_TO_TICKS(1000));
    
    uart_config_t uart1_config = {
        .baud_rate = 2000000,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    esp_err_t ret = uart_driver_install(UART_NUM_1, UART_BUF_SIZE, UART_BUF_SIZE, 20, &uart1_queue, 0);
    if (ret != ESP_OK) 
    {
        ESP_LOGE(TAG, "Failed to install UART driver: %s", esp_err_to_name(ret));
    }
    uart_param_config(UART_NUM_1, &uart1_config);
    uart_set_pin(UART_NUM_1, GPIO_NUM_16, GPIO_NUM_15, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);

    xobd_cmd_queue = xQueueCreate(OBD_QUEUE_SIZE, sizeof(obd_cmd_t));
    if (xobd_cmd_queue == NULL) 
    {
        ESP_LOGE(TAG, "Failed to create OBD command queue");
        return;
    }

    QueueHandle_t response_queue = xQueueCreate(OBD_QUEUE_SIZE, sizeof(obd_rsp_t));
    if (response_queue == NULL)
    {
        ESP_LOGE(TAG, "Failed to create response queue");
        return;
    }
    xuart1_semaphore = xSemaphoreCreateMutex();
    obd_hardreset_chip();
    
    float obd_voltage = 0;

    obd_get_voltage(&obd_voltage);
    ESP_LOGI(TAG, "obd_voltage: %0.1f", obd_voltage);
    vTaskDelay(pdMS_TO_TICKS(100));
// GET_SLEEP_CONFIG_CMD
    static char cmd_buffer[16];
    static uint32_t cmd_buffer_len = 0;
    static int64_t response_cmd_time = 0;

    elm327_process_cmd((uint8_t *)GET_SLEEP_CONFIG_CMD, strlen(GET_SLEEP_CONFIG_CMD), NULL, cmd_buffer, &cmd_buffer_len, &response_cmd_time, obd_parse_response);
    vTaskDelay(pdMS_TO_TICKS(100));
    // elm327_process_cmd((uint8_t *)"STSBR2000000\r", strlen("STSBR2000000\r"), NULL, cmd_buffer, &cmd_buffer_len, &response_cmd_time, obd_parse_response);
    // vTaskDelay(pdMS_TO_TICKS(10));
    // uart_set_baudrate(UART_NUM_1, 1000000);
    // vTaskDelay(pdMS_TO_TICKS(10));
    // elm327_process_cmd((uint8_t *)"ATI\r", strlen("ATI\r"), NULL, cmd_buffer, &cmd_buffer_len, &response_cmd_time, obd_parse_response);
    // xTaskCreate(obd_task, "obd_task", 1024*8, NULL, 5, NULL);

    // static obd_rsp_t response; // Static to persist the data
    // obd_send_cmd("ATI\r", &response_queue, &response, 2000); // Pass address of response_queue
    // if (xQueueReceive(response_queue, (void*)&response, pdMS_TO_TICKS(2000)) == pdPASS)
    // {
    //     obd_log_response(response.rsp_data, response.size);
    // }
    // obd_send_cmd("STSLCS\r", &response_queue, &response, 2000); // Pass address of response_queue
    // if (xQueueReceive(response_queue, (void*)&response, pdMS_TO_TICKS(2000)) == pdPASS)
    // {
    //     obd_log_response(response.rsp_data, response.size);
    // }

    // obd_send_cmd("stsbr2000000\r", &response_queue, &response, 100); // Pass address of response_queue
    // if (xQueueReceive(response_queue, (void*)&response, pdMS_TO_TICKS(100)) == pdPASS)
    // {
    //     obd_log_response(response.rsp_data, response.size);
    // }
    // vTaskDelay(pdMS_TO_TICKS(100));
    // uart_set_baudrate(UART_NUM_1, 2000000);
    // obd_send_cmd("ATI\r", &response_queue, &response, 2000); // Pass address of response_queue
    // if (xQueueReceive(response_queue, (void*)&response, pdMS_TO_TICKS(2000)) == pdPASS)
    // {
    //     obd_log_response(response.rsp_data, response.size);
    // }
}


