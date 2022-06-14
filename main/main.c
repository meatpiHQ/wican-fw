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
#include  "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include <string.h>
#include "comm_server.h"
#include "lwip/sockets.h"
#include "driver/twai.h"
#include "types.h"
#include "config_server.h"
#include "realdash.h"
#include "slcan.h"
#include "can.h"
#include "ble.h"
#include "wifi_network.h"
#include "esp_mac.h"
#include "esp_ota_ops.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "gvret.h"
#include "sleep_mode.h"
#define TAG 		__func__
#define TX_GPIO_NUM             	0
#define RX_GPIO_NUM             	3
#define CONNECTED_LED_GPIO_NUM		8
#define ACTIVE_LED_GPIO_NUM			9
#define BLE_EN_PIN_NUM				5
#define PWR_LED_GPIO_NUM			7
#define GPIO_OUTPUT_PIN_SEL  ((1ULL<<CONNECTED_LED_GPIO_NUM) | (1ULL<<ACTIVE_LED_GPIO_NUM) | (1ULL<<PWR_LED_GPIO_NUM))
#define BLE_EN_PIN_SEL		(1ULL<<BLE_EN_PIN_NUM)
#define BLE_Enabled()		(!gpio_get_level(BLE_EN_PIN_NUM))

static QueueHandle_t xMsg_Tx_Queue, xMsg_Rx_Queue, xmsg_ws_tx_queue, xmsg_ble_tx_queue;
static xdev_buffer ucTCP_RX_Buffer;
static xdev_buffer ucTCP_TX_Buffer;

static uint8_t protocol = SLCAN;


static void process_led(bool state)
{
	static bool current_state;
	static int64_t last_change;

	if(!can_is_enabled())
	{
		gpio_set_level(ACTIVE_LED_GPIO_NUM, 1);
		current_state = 0;
		last_change = esp_timer_get_time();
	}

	if(esp_timer_get_time() - last_change < 20*1000)
	{
		return;
	}
	if(current_state != state)
	{
		last_change = esp_timer_get_time();
		current_state = state;
	}
	else
	{
		return;
	}
	if(state == 1)
	{
		gpio_set_level(ACTIVE_LED_GPIO_NUM, 0);
	}
	else
	{
		gpio_set_level(ACTIVE_LED_GPIO_NUM, 1);
	}
}

//TODO: make this pretty?
void send_to_host(char* str, uint32_t len, QueueHandle_t *q)
{
	if(len == 0)
	{
		ucTCP_TX_Buffer.usLen = strlen(str);
	}
	else
	{
		ucTCP_TX_Buffer.usLen = len;
	}
	memcpy(ucTCP_TX_Buffer.ucElement, str, ucTCP_TX_Buffer.usLen);
	xQueueSend( *q, ( void * ) &ucTCP_TX_Buffer, portMAX_DELAY );
//	ESP_LOGI(TAG, "%s", str);
}

static void can_tx_task(void *pvParameters)
{
	while(1)
	{
		twai_message_t tx_msg;

		xQueueReceive(xMsg_Rx_Queue, &ucTCP_RX_Buffer, portMAX_DELAY);

//		ESP_LOG_BUFFER_HEX(TAG, ucTCP_RX_Buffer.ucElement, ucTCP_RX_Buffer.usLen);

		uint8_t* msg_ptr = ucTCP_RX_Buffer.ucElement;
		int temp_len = ucTCP_RX_Buffer.usLen;

		if(protocol == SLCAN || config_server_ws_connected())
		{
			if(ucTCP_RX_Buffer.dev_channel == DEV_WIFI)
			{
				slcan_parse_str(msg_ptr, temp_len, &tx_msg, &xMsg_Tx_Queue);
			}
			else if(ucTCP_RX_Buffer.dev_channel == DEV_WIFI_WS)
			{
				slcan_parse_str(msg_ptr, temp_len, &tx_msg, &xmsg_ws_tx_queue);
			}
			else if(ucTCP_RX_Buffer.dev_channel == DEV_BLE)
			{
				slcan_parse_str(msg_ptr, temp_len, &tx_msg, &xmsg_ble_tx_queue);
			}
		}
		else if(protocol == REALDASH)
		{
			real_dash_parse_66(&tx_msg, ucTCP_RX_Buffer.ucElement);

			can_send(&tx_msg, portMAX_DELAY);
		}
		else if(protocol == SAVVYCAN)
		{
			gvret_parse(msg_ptr, temp_len, &tx_msg, &xMsg_Tx_Queue);
		}
	}
}

static void can_rx_task(void *pvParameters)
{
//	static uint32_t num_msg = 0;
//	static int64_t time_old = 0;

//	time_old = esp_timer_get_time();
	while(1)
	{
        twai_message_t rx_msg;
        esp_err_t ret = 0xFF;
        process_led(0);
        while(can_receive(&rx_msg, 0) ==  ESP_OK)
        {
//        	num_msg++;
//        	if(esp_timer_get_time() - time_old > 1000*1000)
//        	{
//        		time_old = esp_timer_get_time();
//
//        		ESP_LOGI(TAG, "msg %u/sec", num_msg);
//        		num_msg = 0;
//        	}

        	process_led(1);

			if(tcp_port_open() || ble_connected() || config_server_ws_connected())
			{
				memset(ucTCP_TX_Buffer.ucElement, 0, sizeof(ucTCP_TX_Buffer.ucElement));
				if(protocol == SLCAN || config_server_ws_connected())
				{
					ucTCP_TX_Buffer.usLen = slcan_parse_frame(ucTCP_TX_Buffer.ucElement, &rx_msg);
				}
				else if(protocol == REALDASH)
				{
					ucTCP_TX_Buffer.usLen = real_dash_set_66(&rx_msg, ucTCP_TX_Buffer.ucElement);
				}
				else if(protocol == SAVVYCAN)
				{
					ucTCP_TX_Buffer.usLen = gvret_parse_can_frame(ucTCP_TX_Buffer.ucElement, &rx_msg);
				}

				if(tcp_port_open())
				{
					xQueueSend( xMsg_Tx_Queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(2000) );
				}
				if(config_server_ws_connected())
				{
					xQueueSend( xmsg_ws_tx_queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(2000) );
				}
				if(ble_connected())
				{
					xQueueSend( xmsg_ble_tx_queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(2000) );
				}
			}
        }
        vTaskDelay(pdMS_TO_TICKS(1));
	}
}

void app_main(void)
{
	static uint8_t uid[33];

    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    gpio_config_t io_conf = {};
    //disable interrupt
    io_conf.intr_type = GPIO_INTR_DISABLE;
    //set as output mode
    io_conf.mode = GPIO_MODE_OUTPUT;
    //bit mask of the pins that you want to set,e.g.GPIO18/19
    io_conf.pin_bit_mask = GPIO_OUTPUT_PIN_SEL;
    //disable pull-down mode
    io_conf.pull_down_en = 0;
    //disable pull-up mode
    io_conf.pull_up_en = 0;
    //configure GPIO with the given settings
    gpio_config(&io_conf);

	gpio_set_level(CONNECTED_LED_GPIO_NUM, 1);
	gpio_set_level(ACTIVE_LED_GPIO_NUM, 1);

    xMsg_Rx_Queue = xQueueCreate(100, sizeof( xdev_buffer) );
    xMsg_Tx_Queue = xQueueCreate(100, sizeof( xdev_buffer) );
    xmsg_ws_tx_queue = xQueueCreate(100, sizeof( xdev_buffer) );
    xmsg_ble_tx_queue = xQueueCreate(100, sizeof( xdev_buffer) );
	config_server_start(&xmsg_ws_tx_queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM);
	slcan_init(&send_to_host);

	can_init();

	protocol = config_server_protocol();

	if(protocol == REALDASH)
	{
		int can_datarate = config_server_get_can_rate();
		if(can_datarate != -1)
		{
			can_set_bitrate(can_datarate);
		}
		else
		{
			ESP_LOGE(TAG, "error going to default CAN_500K");
			can_set_bitrate(CAN_500K);
		}
		if(config_server_get_can_mode() == CAN_NORMAL)
		{
			can_set_silent(0);
		}
		else
		{
			can_set_silent(1);
		}

		can_enable();
	}
	else if(protocol == SAVVYCAN)
	{
		gvret_init(&send_to_host);
		can_enable();
	}

	wifi_network_init();
	int32_t port = config_server_get_port();

	if(port == -1)
	{
		port = 3333;
	}
	if(config_server_get_port_type() == UDP_PORT)
	{
		tcp_server_init(port, &xMsg_Tx_Queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM, 1);
	}
	else
	{
		tcp_server_init(port, &xMsg_Tx_Queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM, 0);
	}

    if(config_server_get_ble_config())
    {
    	int pass = config_server_ble_pass();
        uint8_t derived_mac_addr[6] = {0};

        ESP_ERROR_CHECK(esp_read_mac(derived_mac_addr, ESP_MAC_WIFI_SOFTAP));
        sprintf((char *)uid,"WiC_%02x%02x%02x%02x%02x%02x",
                derived_mac_addr[0], derived_mac_addr[1], derived_mac_addr[2],
                derived_mac_addr[3], derived_mac_addr[4], derived_mac_addr[5]);
    	ble_init(&xmsg_ble_tx_queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM, pass, &uid[0]);
    }

    xTaskCreate(can_rx_task, "can_rx_task", 4096, (void*)AF_INET, 5, NULL);
    xTaskCreate(can_tx_task, "can_tx_task", 4096, (void*)AF_INET, 5, NULL);

    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_app_desc_t running_app_info;
    if (esp_ota_get_partition_description(running, &running_app_info) == ESP_OK)
    {
        ESP_LOGI(TAG, "Running firmware version: %s", running_app_info.version);
    }

    if(config_server_get_sleep_config())
    {
    	sleep_mode_init();
    }

    gpio_set_level(PWR_LED_GPIO_NUM, 1);
    esp_ota_mark_app_valid_cancel_rollback();
//    esp_log_level_set("*", ESP_LOG_INFO);
}

