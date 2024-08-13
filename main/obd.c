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

#define TAG                     __func__

 QueueHandle_t uart1_queue = NULL;
static QueueHandle_t xobd_cmd_queue = NULL;
SemaphoreHandle_t xuart1_semaphore = NULL;

void obd_log_response(char *buf, uint32_t size)
{
    for(int i = 0; i < size; i++)
    {
        if(buf[i] == '\r')
        {
            buf[i] = '\n';
        }
    }
    buf[size] = '\0';
    ESP_LOGI(TAG, "OBD response: %s", buf);
    for(int i = 0; i < size; i++)
    {
        if(buf[i] == '\n')
        {
            buf[i] = '\r';
        }
    }
}

static void obd_task(void *pvParameters)
{
    ESP_LOGI(TAG, "OBD Task started");
    obd_cmd_t obd_cmd;
    size_t buf_index = 0;
    uart_event_t event;
    // bool cmd_complete = false;

    while (true)
    {
        ESP_LOGI(TAG, "Waiting for command in xobd_queue");
        if (xQueueReceive(xobd_cmd_queue, (void*)&obd_cmd, portMAX_DELAY) == pdPASS)
        {
            ESP_LOGI(TAG, "Command received: %s", obd_cmd.cmd);
            int write_len = uart_write_bytes(UART_NUM_1, (const char*)obd_cmd.cmd, strlen(obd_cmd.cmd));
            ESP_LOGI(TAG, "Wrote %d bytes to UART", write_len);
            obd_cmd.obd_rsp->rsp_end_flag = 0;
            if (xSemaphoreTake(xuart1_semaphore, pdMS_TO_TICKS(obd_cmd.timeout_ms)) == pdTRUE)
            {
                while (!obd_cmd.obd_rsp->rsp_end_flag)
                {
                    ESP_LOGI(TAG, "Waiting for UART event");
                    if (xQueueReceive(uart1_queue, (void*)&event, pdMS_TO_TICKS(obd_cmd.timeout_ms)))
                    {
                        ESP_LOGI(TAG, "UART event received: %d", event.type);
                        if (event.type == UART_DATA)
                        {
                            int len = uart_read_bytes(UART_NUM_1, obd_cmd.obd_rsp->rsp_data + buf_index, event.size, pdMS_TO_TICKS(obd_cmd.timeout_ms));
                            ESP_LOGI(TAG, "Read %d bytes from UART", len);
                            buf_index += len;

                            if (buf_index >= 2 && obd_cmd.obd_rsp->rsp_data[buf_index - 2] == '\r' && obd_cmd.obd_rsp->rsp_data[buf_index - 1] == '>')
                            {
                                ESP_LOGI(TAG, "Command complete indicator '\\r>' found");
                                obd_cmd.obd_rsp->size = buf_index;
                                obd_cmd.obd_rsp->rsp_data[buf_index] = '\0';
                                xQueueSend(*(obd_cmd.rsp_queue), obd_cmd.obd_rsp, portMAX_DELAY); // Dereference rsp_queue
                                buf_index = 0;
                                obd_cmd.obd_rsp->rsp_end_flag = true;
                            }
                        }
                        else if (event.type == UART_FIFO_OVF)
                        {
                            ESP_LOGE(TAG, "HW FIFO Overflow");
                            uart_flush(UART_NUM_1);
                        }
                        else if (event.type == UART_BUFFER_FULL)
                        {
                            ESP_LOGE(TAG, "Ring Buffer Full");
                            uart_flush(UART_NUM_1);
                        }
                        else
                        {
                            ESP_LOGI(TAG, "Unhandled event type: %d", event.type);
                        }

                        // if (obd_cmd.obd_rsp->rsp_end_flag)
                        // {
                        //     ESP_LOGI(TAG, "Clearing UART buffer");
                        //     bzero(obd_cmd.obd_rsp->rsp_data, OBD_DATA_BUF_SIZE);
                        // }
                    }
                    else
                    {
                        ESP_LOGI(TAG, "UART receive timeout");
                        break; // Exit inner loop on timeout
                    }
                }
                xSemaphoreGive(xuart1_semaphore);
            }
        }
    }
}

#define BUF_SIZE    1024

// Function to read response from OBD
void obd_read_rsp(char** rsp_buf, uint32_t *rsp_len, uint32_t timeout_ms)
{
    uart_event_t event;
    static uint8_t uart_read_buf[BUF_SIZE];
    uart_read_buf[0] = '\0';
    
    if (xQueueReceive(uart1_queue, (void*)&event, pdMS_TO_TICKS(timeout_ms)))
    {
        bzero(uart_read_buf, BUF_SIZE);
        
        switch (event.type) 
        {
            case UART_DATA:
                uart_read_bytes(UART_NUM_1, uart_read_buf, event.size, pdMS_TO_TICKS(timeout_ms));
                ESP_LOGI(TAG, "event.size: %u", event.size);
                ESP_LOG_BUFFER_HEXDUMP(TAG, uart_read_buf, event.size, ESP_LOG_INFO);

                (event.size) < (BUF_SIZE - 1) ? (uart_read_buf[event.size] = '\0') : (uart_read_buf[BUF_SIZE-1] = '\0');

                *rsp_buf = (char*)uart_read_buf;
                *rsp_len = event.size;
                break;

            case UART_FIFO_OVF:
                ESP_LOGE(TAG, "HW FIFO Overflow");
                uart_flush(UART_NUM_1);
                break;

            case UART_BUFFER_FULL:
                ESP_LOGE(TAG, "Ring Buffer Full");
                uart_flush(UART_NUM_1);
                break;

            default:
                ESP_LOGI(TAG, "Unhandled event type: %d", event.type);
                break;
        }
    }
}

// Function to write command to OBD and read response
void obd_write_cmd(char* cmd, char** rsp_buf, uint32_t *rsp_len, uint32_t timeout_ms)
{
    if(xuart1_semaphore != NULL)
    {
        if (xSemaphoreTake(xuart1_semaphore, pdMS_TO_TICKS(timeout_ms)) == pdTRUE)
        {
            ESP_LOG_BUFFER_HEXDUMP(TAG, cmd, strlen(cmd), ESP_LOG_INFO);
            uart_write_bytes(UART_NUM_1, (const char*)cmd, strlen(cmd));
            obd_read_rsp(rsp_buf, rsp_len, timeout_ms);
            obd_log_response(*rsp_buf, *rsp_len);
            xSemaphoreGive(xuart1_semaphore);
        }
    }
}

// Function to get voltage from OBD
int8_t obd_get_voltage(float *val)
{
    char* rsp_buffer = NULL;
    uint32_t rsp_len;
    const unsigned char *ptr;
    char buffer[20];
    int buffer_index = 0;

    obd_write_cmd("ATRV\r", &rsp_buffer, &rsp_len, 2000);
    
    if (rsp_buffer == NULL) 
    {
        return -1;
    }
    
    ptr = (unsigned char *)rsp_buffer;

    while (*ptr) 
    {
        if (isdigit(*ptr) || *ptr == '.' || (*ptr == '-' && isdigit(*(ptr + 1)))) 
        {
            while (isdigit(*ptr) || *ptr == '.') 
            {
                buffer[buffer_index++] = *ptr++;
            }
            buffer[buffer_index] = '\0';
            
                *val = atof((char*)buffer);
            return 1;
        }
        ptr++;
    }
    return -1;
}

void obd_hardreset_chip(void)
{
    char* rsp_buffer = NULL;
    uint32_t rsp_len;

    gpio_set_level(OBD_RESET_PIN, 0);
    vTaskDelay(pdMS_TO_TICKS(5));
    gpio_set_level(OBD_RESET_PIN, 1);

    obd_read_rsp(&rsp_buffer, &rsp_len, 2000);
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
    vTaskDelay(pdMS_TO_TICKS(1000));

    gpio_reset_pin(OBD_RESET_PIN);
    gpio_set_direction(OBD_RESET_PIN, GPIO_MODE_OUTPUT_OD);

    // gpio_set_level(41, 0);
    // vTaskDelay(pdMS_TO_TICKS(10));
    // gpio_set_level(41, 1);
    // vTaskDelay(pdMS_TO_TICKS(1000));
    
    uart_config_t uart1_config = {
        .baud_rate = 115200,
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


    xTaskCreate(obd_task, "obd_task", 1024*8, NULL, 5, NULL);

    static obd_rsp_t response; // Static to persist the data
    obd_send_cmd("ATI\r", &response_queue, &response, 2000); // Pass address of response_queue
    if (xQueueReceive(response_queue, (void*)&response, pdMS_TO_TICKS(2000)) == pdPASS)
    {
        obd_log_response(response.rsp_data, response.size);
    }
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


