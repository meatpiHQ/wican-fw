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
#include "ver.h"
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
#include "wc_uart.h"
#include "elm327.h"
#include "mqtt.h"

#define TAG 		__func__
#define TX_GPIO_NUM             	0
#define RX_GPIO_NUM             	3
#define CONNECTED_LED_GPIO_NUM		8
#define ACTIVE_LED_GPIO_NUM			9
#define BLE_EN_PIN_NUM				5
#define PWR_LED_GPIO_NUM			7
#define GPIO_OUTPUT_PIN_SEL  ((1ULL<<CONNECTED_LED_GPIO_NUM) | (1ULL<<ACTIVE_LED_GPIO_NUM) | (1ULL<<PWR_LED_GPIO_NUM) | (1ULL<<CAN_STDBY_GPIO_NUM))
#define BLE_EN_PIN_SEL		(1ULL<<BLE_EN_PIN_NUM)
#define BLE_Enabled()		(!gpio_get_level(BLE_EN_PIN_NUM))

static QueueHandle_t xMsg_Tx_Queue, xMsg_Rx_Queue, xmsg_ws_tx_queue, xmsg_ble_tx_queue, xmsg_uart_tx_queue, xmsg_obd_rx_queue, xmsg_mqtt_rx_queue;
static xdev_buffer ucTCP_RX_Buffer;
static xdev_buffer ucTCP_TX_Buffer;

static uint8_t protocol = SLCAN;

uint8_t project_hardware_rev;

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
	static xdev_buffer xsend_buffer;

	if(len == 0)
	{
		xsend_buffer.usLen = strlen(str);
	}
	else
	{
		xsend_buffer.usLen = len;
	}
	memcpy(xsend_buffer.ucElement, str, xsend_buffer.usLen);
	xQueueSend( *q, ( void * ) &xsend_buffer, portMAX_DELAY );

//	ESP_LOG_BUFFER_HEX(TAG, ucTCP_TX_Buffer.ucElement, xsend_buffer.usLen);
	memset(xsend_buffer.ucElement, 0, sizeof(xsend_buffer.ucElement));
	xsend_buffer.usLen = 0;
//	ESP_LOGI(TAG, "%s", str);
}

static void can_tx_task(void *pvParameters)
{
	while(1)
	{
		twai_message_t tx_msg;

		memset(ucTCP_RX_Buffer.ucElement,0, DEV_BUFFER_LENGTH);
		xQueueReceive(xMsg_Rx_Queue, &ucTCP_RX_Buffer, portMAX_DELAY);

		ESP_LOG_BUFFER_HEXDUMP(TAG, ucTCP_RX_Buffer.ucElement, ucTCP_RX_Buffer.usLen, ESP_LOG_INFO);

		uint8_t* msg_ptr = ucTCP_RX_Buffer.ucElement;
		int temp_len = ucTCP_RX_Buffer.usLen;

		if(config_server_ws_connected())
		{
			if(ucTCP_RX_Buffer.dev_channel == DEV_WIFI_WS)
			{
				slcan_parse_str(msg_ptr, temp_len, &tx_msg, &xmsg_ws_tx_queue);
			}
		}
		if(protocol == SLCAN)
		{
			if(ucTCP_RX_Buffer.dev_channel == DEV_WIFI)
			{
				slcan_parse_str(msg_ptr, temp_len, &tx_msg, &xMsg_Tx_Queue);
			}
			else if(ucTCP_RX_Buffer.dev_channel == DEV_BLE)
			{
				slcan_parse_str(msg_ptr, temp_len, &tx_msg, &xmsg_ble_tx_queue);
			}
			else if(ucTCP_RX_Buffer.dev_channel == DEV_UART)
			{
				if(!config_server_mqtt_en_config())
				{
					slcan_parse_str(msg_ptr, temp_len, &tx_msg, &xmsg_uart_tx_queue);
				}
			}
		}
		else if(protocol == REALDASH)
		{
			ESP_LOG_BUFFER_HEX(TAG, ucTCP_RX_Buffer.ucElement, ucTCP_RX_Buffer.usLen);

			if(real_dash_parse_66(&tx_msg, ucTCP_RX_Buffer.ucElement) == 0)
			{
				real_dash_parse_44(&tx_msg, ucTCP_RX_Buffer.ucElement, ucTCP_RX_Buffer.usLen);
			}

			tx_msg.self = 0;
			can_send(&tx_msg, portMAX_DELAY);
		}
		else if(protocol == SAVVYCAN)
		{
			gvret_parse(msg_ptr, temp_len, &tx_msg, &xMsg_Tx_Queue);
		}
		else if(protocol == OBD_ELM327)
		{
			if(ucTCP_RX_Buffer.dev_channel == DEV_WIFI)
			{
				elm327_process_cmd(msg_ptr, temp_len, &tx_msg, &xMsg_Tx_Queue);
			}
			else if(ucTCP_RX_Buffer.dev_channel == DEV_BLE)
			{
				elm327_process_cmd(msg_ptr, temp_len, &tx_msg, &xmsg_ble_tx_queue);
			}
		}
	}
}
#define HEAP_CAPS   (MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
static void can_rx_task(void *pvParameters)
{
//	static uint32_t num_msg = 0;
	static int64_t time_old = 0;
//	float bvoltage = 0;
//	time_old = esp_timer_get_time();
	while(1)
	{
        twai_message_t rx_msg;
//        esp_err_t ret = 0xFF;


//    	time_old = esp_timer_get_time();
//    	if((esp_timer_get_time() - time_old) > 1000000)
//    	{
//    		sleep_mode_get_voltage(&bvoltage);
//    		time_old = esp_timer_get_time();
//
//    		ESP_LOGI(TAG, "bvoltage: %f", bvoltage);
//    	}
        process_led(0);
    	if(esp_timer_get_time() - time_old > 1000*1000)
    	{
    		uint32_t free_heap = heap_caps_get_free_size(HEAP_CAPS);
    		time_old = esp_timer_get_time();
    		ESP_LOGI(TAG, "free_heap: %u", free_heap);
//        		ESP_LOGI(TAG, "msg %u/sec", num_msg);
//        		num_msg = 0;
    	}
        while(can_receive(&rx_msg, 0) ==  ESP_OK)
        {
//        	num_msg++;

        	process_led(1);

        	if(config_server_ws_connected())
        	{
        		ucTCP_TX_Buffer.usLen = slcan_parse_frame(ucTCP_TX_Buffer.ucElement, &rx_msg);
				if(config_server_ws_connected())
				{
					xQueueSend( xmsg_ws_tx_queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(2000) );
				}
        	}
        	//TODO: optimize, useless ifs
			if(tcp_port_open() || ble_connected() || project_hardware_rev == WICAN_USB_V100 || mqtt_connected())
			{
				memset(ucTCP_TX_Buffer.ucElement, 0, sizeof(ucTCP_TX_Buffer.ucElement));
				ucTCP_TX_Buffer.usLen = 0;

				if(protocol == SLCAN)
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
				else if(protocol == OBD_ELM327)
				{
					// Let elm327.c decide which messages to process
					xQueueSend( xmsg_obd_rx_queue, ( void * ) &rx_msg, pdMS_TO_TICKS(0) );
				}




				if(ucTCP_TX_Buffer.usLen != 0)
				{
					if(tcp_port_open())
					{
						xQueueSend( xMsg_Tx_Queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(2000) );
					}
					if(ble_connected())
					{
						xQueueSend( xmsg_ble_tx_queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(2000) );
					}
					else if(project_hardware_rev == WICAN_USB_V100)
					{
						if(!config_server_mqtt_en_config())
						{
							xQueueSend( xmsg_uart_tx_queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(0) );
						}
					}
				}
			}
			if(mqtt_connected())
			{
//					mqtt_publish_can(&rx_msg);
				xQueueSend( xmsg_mqtt_rx_queue, ( void * ) &rx_msg, pdMS_TO_TICKS(0) );
			}
        }
        vTaskDelay(pdMS_TO_TICKS(1));
	}
}
static uint8_t derived_mac_addr[6] = {0};
static uint8_t uid[33];
static uint8_t ble_uid[33];
void app_main(void)
{
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

    xMsg_Rx_Queue = xQueueCreate(32, sizeof( xdev_buffer) );
    xMsg_Tx_Queue = xQueueCreate(32, sizeof( xdev_buffer) );
    xmsg_ws_tx_queue = xQueueCreate(32, sizeof( xdev_buffer) );


//    xmsg_obd_rx_queue = xQueueCreate(100, sizeof( twai_message_t) );
	config_server_start(&xmsg_ws_tx_queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM);



    ESP_ERROR_CHECK(esp_read_mac(derived_mac_addr, ESP_MAC_WIFI_SOFTAP));
    sprintf((char *)ble_uid,"WiC_%02x%02x%02x%02x%02x%02x",
            derived_mac_addr[0], derived_mac_addr[1], derived_mac_addr[2],
            derived_mac_addr[3], derived_mac_addr[4], derived_mac_addr[5]);
    sprintf((char *)uid,"%02x%02x%02x%02x%02x%02x",
            derived_mac_addr[0], derived_mac_addr[1], derived_mac_addr[2],
            derived_mac_addr[3], derived_mac_addr[4], derived_mac_addr[5]);
	slcan_init(&send_to_host);

	int8_t can_datarate = config_server_get_can_rate();
	(can_datarate != -1) ? can_init(can_datarate):can_init(CAN_500K);

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

	protocol = config_server_protocol();
//	protocol = OBD_ELM327;

	if(protocol == REALDASH)
	{
//		int can_datarate = config_server_get_can_rate();
		if(can_datarate != -1)
		{
			can_set_bitrate(can_datarate);
		}
		else
		{
			ESP_LOGE(TAG, "error going to default CAN_500K");
			can_set_bitrate(CAN_500K);
		}

		can_enable();
	}
	else if(protocol == SAVVYCAN)
	{
		gvret_init(&send_to_host);
		can_enable();
	}
	else if(protocol == OBD_ELM327)
	{
//		can_init(CAN_500K);
		can_set_bitrate(can_datarate);
		can_enable();
		xmsg_obd_rx_queue = xQueueCreate(32, sizeof( twai_message_t) );
		elm327_init(&send_to_host, &xmsg_obd_rx_queue);
	}

	if(config_server_mqtt_en_config())
	{
		can_set_bitrate(can_datarate);
		xmsg_mqtt_rx_queue = xQueueCreate(32, sizeof( twai_message_t) );
		can_enable();
		mqtt_init((char*)&uid[0], CONNECTED_LED_GPIO_NUM, &xmsg_mqtt_rx_queue);
	}
//	else if(protocol == MQTT)
//	{
//		xmsg_mqtt_rx_queue = xQueueCreate(100, sizeof( twai_message_t) );
//		can_init(CAN_500K);
//		can_enable();
//
//		mqtt_init((char*)&uid[0], CONNECTED_LED_GPIO_NUM, &xmsg_mqtt_rx_queue);
//	}


	wifi_network_init(NULL, NULL);
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
    	xmsg_ble_tx_queue = xQueueCreate(100, sizeof( xdev_buffer) );
    	ble_init(&xmsg_ble_tx_queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM, pass, &ble_uid[0]);
    }

    xTaskCreate(can_rx_task, "can_rx_task", 2048, (void*)AF_INET, 5, NULL);
    xTaskCreate(can_tx_task, "can_tx_task", 2048, (void*)AF_INET, 5, NULL);

    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_app_desc_t running_app_info;
    if (esp_ota_get_partition_description(running, &running_app_info) == ESP_OK)
    {
        ESP_LOGI(TAG, "Running firmware version: %s", running_app_info.version);
        ESP_LOGI(TAG, "Project Name: %s", running_app_info.project_name);

        if(strstr(running_app_info.project_name, "usb") != 0)
        {
        	project_hardware_rev = WICAN_USB_V100;
        	ESP_LOGI(TAG, "project_hardware_rev: USB");
        	if(!config_server_mqtt_en_config())
        	{
        	    xmsg_uart_tx_queue = xQueueCreate(32, sizeof( xdev_buffer) );
        		wc_uart_init(&xmsg_uart_tx_queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM);
        	}

        }
        else
        {
        	ESP_LOGI(TAG, "project_hardware_rev: OBD");
            if(strstr(running_app_info.project_name, "hv210") != 0)
            {
            	project_hardware_rev = WICAN_V210;
            }
            else
            {
            	project_hardware_rev = WICAN_V300;
            }
        }
    }

    if(project_hardware_rev != WICAN_V210)
    {
		if(config_server_get_sleep_config())
		{
			float sleep_voltage = 0;

			if(config_server_get_sleep_volt(&sleep_voltage) != -1)
			{
				sleep_mode_init(1, sleep_voltage);
			}
			else
			{
				sleep_mode_init(0, 13.1f);
			}
		}
		else
		{
			sleep_mode_init(0, 13.1f);
		}
    }
    else
    {
    	sleep_mode_init(0, 13.1f);
    }

    gpio_set_level(PWR_LED_GPIO_NUM, 1);
    esp_ota_mark_app_valid_cancel_rollback();
//    while(1)
//    {
//		ESP_LOGI(TAG, "free heap : %d", xPortGetFreeHeapSize());
//		vTaskDelay(pdMS_TO_TICKS(2000));
//    }
    esp_log_level_set("*", ESP_LOG_NONE);
}

