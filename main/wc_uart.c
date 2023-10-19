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
//#include "esp_log.h"
#include "driver/uart.h"
#include "string.h"
#include "driver/gpio.h"
#include "types.h"
#include "lwip/sockets.h"

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
        if(xQueueReceive(uart0_queue, (void * )&event, (portTickType)portMAX_DELAY))
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
        .baud_rate = 3000000,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    }; //Baudrate is limited by usb to uart bridge on dev kit
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


