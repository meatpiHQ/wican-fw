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

#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include  "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_log.h"
#include "driver/uart.h"
#include "string.h"
#include "driver/gpio.h"
#include "types.h"
#include "lwip/sockets.h"
#include "esp_log.h"
#include "elm327.h"
#include "obd.h"
#include "dev_status.h"

#define TAG         __func__

#if HARDWARE_VER == WICAN_USB_V100
static const int RX_BUF_SIZE = 1024;

static QueueHandle_t *xuart_tx_queue, *xuart_rx_queue;
static QueueHandle_t uart0_queue;

static void uart_rx_task(void *arg)
{
//    static xdev_buffer tx_buffer;
	static xdev_buffer rx_buffer;
    uart_event_t event;
    // static uint8_t dtmp[128];
    while (1)
    {
//    	xQueueReceive(*xuart_tx_queue, ( void * ) &tx_buffer, portMAX_DELAY);
//    	uart_write_bytes(UART_NUM_0, tx_buffer.ucElement, tx_buffer.usLen);
        if(xQueueReceive(uart0_queue, (void * )&event, portMAX_DELAY))
        {
            bzero(rx_buffer.ucElement, sizeof(rx_buffer.ucElement));
//            //ESP_LOGI(TAG, "uart[%d] event:", UART_NUM_0);
            switch(event.type)
            {
                //Event of UART receving data
                /*We'd better handler data event fast, there would be much more data events than
                other types of events. If we take too much time on data event, the queue might
                be full.*/
                case UART_DATA:
//                    //ESP_LOGI(TAG, "[UART DATA]: %d", event.size);
//                    uart_read_bytes(UART_NUM_0, dtmp, event.size, portMAX_DELAY);
//                    //ESP_LOGI(TAG, "[DATA EVT]:");
//                    uart_write_bytes(UART_NUM_0, (const char*) dtmp, event.size);
						rx_buffer.usLen = uart_read_bytes(UART_NUM_0, rx_buffer.ucElement, RX_BUF_SIZE, 1 / portTICK_PERIOD_MS);
						rx_buffer.dev_channel = DEV_UART;
						if(rx_buffer.usLen > 0)
						{
							xQueueSend(*xuart_rx_queue, ( void * ) &rx_buffer, portMAX_DELAY );
//							uart_write_bytes(UART_NUM_0, (const char*) rx_buffer.ucElement, rx_buffer.usLen);
						}
                    break;

                //Others
                default:
//                    //ESP_LOGI(__func__, "uart event type: %d", event.type);
//                		sprintf((char*)dtmp,"uart event type: %d", event.type);
//                		uart_write_bytes(UART_NUM_0, (const char*) dtmp, strlen((char*)dtmp));
                    break;
            }
        }
    }
}

static void uart_tx_task(void *arg)
{
    static xdev_buffer tx_buffer;

    while (1)
    {
    	xQueueReceive(*xuart_tx_queue, ( void * ) &tx_buffer, portMAX_DELAY);
    	uart_write_bytes(UART_NUM_0, tx_buffer.ucElement, tx_buffer.usLen);
//    	rx_buffer.usLen = uart_read_bytes(UART_NUM_0, rx_buffer.ucElement, RX_BUF_SIZE, 1 / portTICK_PERIOD_MS);
//    	rx_buffer.dev_channel = DEV_UART;
//    	if(rx_buffer.usLen > 0)
//    	{
//    		xQueueSend(*xuart_rx_queue, ( void * ) &rx_buffer, portMAX_DELAY );
//    	}
    }
}

void wc_uart_init(QueueHandle_t *xTXp_Queue, QueueHandle_t *xRXp_Queue, uint8_t connected_led)
{
    const uart_config_t uart_config = {
        .baud_rate = 4000000,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };
    xuart_tx_queue = xTXp_Queue;
	xuart_rx_queue = xRXp_Queue;
    // We won't use a buffer for sending data.
//    uart_driver_install(UART_NUM_0, RX_BUF_SIZE * 2, 0, 0, NULL, 0);
	uart_driver_install(UART_NUM_0, RX_BUF_SIZE, RX_BUF_SIZE, 20, &uart0_queue, 0);
    uart_param_config(UART_NUM_0, &uart_config);
//																					output,			input
//    esp_err_t uart_set_pin(uart_port_t uart_num, int tx_io_num, int rx_io_num, int rts_io_num, int cts_io_num);
//    uart_set_pin(UART_NUM_0, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, 2, 10);
    uart_set_pin(UART_NUM_0, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    xTaskCreate(uart_tx_task, "uart_tx_task", 1024*2, (void*)AF_INET, 5, NULL);
    xTaskCreate(uart_rx_task, "uart_rx_task", 1024*2, (void*)AF_INET, 5, NULL);
}

#elif HARDWARE_VER == WICAN_PRO

static QueueHandle_t uart2_queue;

static void uart2_response(char *str, uint32_t len, QueueHandle_t *q, char* cmd_str)
{
    ESP_LOGI(TAG, "Response");
    if (str != NULL && len > 0)
    {
        ESP_LOGI(TAG, "Responding on UART2 with: %s", str);
        uart_write_bytes(UART_NUM_2, str, len);  // Send response to UART2
    }
}
static uint8_t* uart2_read_buffer __attribute__((aligned(4)));
static char* uart2_cmd_buffer __attribute__((aligned(4)));
static void uart2_event_task(void *pvParameters)
{
    uart_event_t event;
    static uint32_t uart2_cmd_buffer_len = 0;
    static int64_t uart2_last_cmd_time = 0;
    uart2_read_buffer = (uint8_t*) heap_caps_aligned_alloc(16,UART_BUF_SIZE, MALLOC_CAP_SPIRAM);
    uart2_cmd_buffer = (char*) heap_caps_aligned_alloc(16,UART_BUF_SIZE, MALLOC_CAP_SPIRAM);

    memset(uart2_read_buffer, 0, UART_BUF_SIZE);
    memset(uart2_cmd_buffer, 0, UART_BUF_SIZE);
    ESP_LOGW(TAG, "Start UART2 event task!");
    for (;;)
    {
        dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);
        // Waiting for UART event.
        if (xQueueReceive(uart2_queue, (void *)&event, (TickType_t)portMAX_DELAY))
        {
            bzero(uart2_read_buffer, UART_BUF_SIZE);  // Clear the buffer
            ESP_LOGI(TAG, "uart[%d] event:", UART_NUM_2);

            switch (event.type)
            {
                case UART_DATA:
                    ESP_LOGI(TAG, "[UART DATA]: %d bytes", event.size);
                    
                    // Read UART2 data into buffer
                    uint32_t data_len = uart_read_bytes(UART_NUM_2, uart2_read_buffer, event.size, portMAX_DELAY);
                    ESP_LOGI(TAG, "[DATA EVT] Data Length: %lu", data_len);

                    uart2_read_buffer[data_len] = '\0';  // Null-terminate the data
                    ESP_LOGI(TAG, "Data: %s", (char *)uart2_read_buffer);
                    // printf("Data: %s\r\n", (char *)uart2_read_buffer);

                    // Call elm327_process_cmd with required parameters
                    elm327_process_cmd(uart2_read_buffer, data_len, NULL, uart2_cmd_buffer, &uart2_cmd_buffer_len, &uart2_last_cmd_time, uart2_response);

                    break;

                default:
                    ESP_LOGI(TAG, "Unhandled UART event type: %d", event.type);
                    break;
            }
        }
    }
    vTaskDelete(NULL);
}

void wc_uart_init(void)
{
        uart_config_t uart2_config = {
        .baud_rate = 2000000,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    
    uart_driver_install(UART_NUM_2, UART_BUF_SIZE, UART_BUF_SIZE, 4, &uart2_queue, 0);
    uart_param_config(UART_NUM_2, &uart2_config);

    uart_set_pin(UART_NUM_2, GPIO_NUM_17, GPIO_NUM_18, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);

    // Allocate stack memory in PSRAM for the UART event task
    static StackType_t *uart2_event_task_stack;
    static StaticTask_t uart2_event_task_buffer;
    
    uart2_event_task_stack = heap_caps_malloc(2048*2, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    
    if (uart2_event_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate UART event task stack memory");
        return;
    }
    
    // Create static task
    TaskHandle_t uart2_task_handle = xTaskCreateStatic(
        uart2_event_task,
        "uart2_event_task",
        2048*2,
        NULL,
        5,
        uart2_event_task_stack,
        &uart2_event_task_buffer
    );
    
    if (uart2_task_handle == NULL)
    {
        ESP_LOGE(TAG, "Failed to create UART event task");
        heap_caps_free(uart2_event_task_stack);
        return;
    }
}
#endif
