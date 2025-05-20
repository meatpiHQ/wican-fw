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
// FreeRTOS includes
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"

// ESP system and core includes
#include "esp_system.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_mac.h"
#include "esp_ota_ops.h"

// Drivers
#include "driver/gpio.h"
#include "driver/twai.h"
#include "driver/i2c.h"
#include "esp_wifi.h"
#include "lwip/sockets.h"
#include "wifi_network.h"
#include "nvs.h"
#include "nvs_flash.h"
#include <inttypes.h>
#include <string.h>
#include "types.h"
#include "ver.h"
#include "hw_config.h"
#include "comm_server.h"
#include "config_server.h"
#include "realdash.h"
#include "slcan.h"
#include "can.h"
#include "ble.h"
#include "gvret.h"
#include "sleep_mode.h"
#include "wc_uart.h"
#include "elm327.h"
#include "mqtt.h"
#include "ftp.h"
#include "autopid.h"
#include "led.h"
#include "obd.h"
#include "wusb3801.h"
#include "usb_host.h"
#include "wc_mdns.h"
#include "sdcard.h"
#include "imu.h"
#include "rtcm.h"
#include "console.h"

#define TAG 		__func__
#define USB_ID_PIN					39
#define USB_OTG_PWR_EN				10
#define USB_ESP_MODE_EN				11
#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
#define GPIO_OUTPUT_PIN_SEL  		((1ULL<<CONNECTED_LED_GPIO_NUM) | (1ULL<<ACTIVE_LED_GPIO_NUM) | (1ULL<<PWR_LED_GPIO_NUM) | (1ULL<<CAN_STDBY_GPIO_NUM) | (1ULL<<USB_ESP_MODE_EN))
#elif HARDWARE_VER == WICAN_PRO
// #define GPIO_OUTPUT_PIN_SEL  		((1ULL<<CAN_STDBY_GPIO_NUM) | (1ULL<<USB_OTG_PWR_EN) | (1ULL<<USB_ESP_MODE_EN))
#define GPIO_OUTPUT_PIN_SEL  		(1ULL<<USB_ESP_MODE_EN)
#define I2C_MASTER_SCL_IO           6     
#define I2C_MASTER_SDA_IO           5      
#define I2C_MASTER_NUM              0
#define I2C_MASTER_FREQ_HZ          200000
#define I2C_MASTER_TX_BUF_DISABLE   0
#define I2C_MASTER_RX_BUF_DISABLE   0
#define I2C_MASTER_TIMEOUT_MS       1000
#endif

#define BLE_EN_PIN_SEL		(1ULL<<BLE_EN_PIN_NUM)
#define BLE_Enabled()		(!gpio_get_level(BLE_EN_PIN_NUM))

static QueueHandle_t xMsg_Tx_Queue, xMsg_Rx_Queue, xmsg_ws_tx_queue, xmsg_ble_tx_queue, xmsg_uart_tx_queue, xmsg_obd_rx_queue, xmsg_mqtt_rx_queue, xmsg_elm327_rx_queue;
static xdev_buffer ucTCP_RX_Buffer;
static xdev_buffer ucTCP_TX_Buffer;
static xdev_buffer ucBLE_TX_Buffer;

static uint8_t protocol = OBD_ELM327;

int FTP_TASK_FINISH_BIT = BIT2;
EventGroupHandle_t xEventTask;
static uint8_t mqtt_elm327_log_en = 0;
static uint8_t derived_mac_addr[6] = {0};
static uint8_t uid[16];
static uint8_t ble_uid[33];
static char hardware_version[16];
static char firmware_version[10];

static void log_can_to_mqtt(twai_message_t *frame, uint8_t type)
{
	static mqtt_can_message_t mqtt_msg;

	mqtt_msg.frame.extd = frame->extd;
	mqtt_msg.frame.rtr = frame->rtr;
	mqtt_msg.frame.ss = frame->ss;
	mqtt_msg.frame.self = frame->self;
	mqtt_msg.frame.dlc_non_comp = frame->dlc_non_comp;
	mqtt_msg.frame.identifier = frame->identifier;
	mqtt_msg.frame.data_length_code = frame->data_length_code;

	mqtt_msg.frame.data[0] = frame->data[0];
	mqtt_msg.frame.data[1] = frame->data[1];
	mqtt_msg.frame.data[2] = frame->data[2];
	mqtt_msg.frame.data[3] = frame->data[3];
	mqtt_msg.frame.data[4] = frame->data[4];
	mqtt_msg.frame.data[5] = frame->data[5];
	mqtt_msg.frame.data[6] = frame->data[6];
	mqtt_msg.frame.data[7] = frame->data[7];

	mqtt_msg.type = type;
	xQueueSend( xmsg_mqtt_rx_queue, ( void * ) &mqtt_msg, pdMS_TO_TICKS(0) );
}
static void process_led(bool state)
{
	static bool current_state;
	static int64_t last_change;

	if(!can_is_enabled())
	{
		#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
		gpio_set_level(ACTIVE_LED_GPIO_NUM, 1);
		#endif
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
		#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
			gpio_set_level(ACTIVE_LED_GPIO_NUM, 0);
		#endif
	}
	else
	{
		#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
			gpio_set_level(ACTIVE_LED_GPIO_NUM, 1);
		#endif
	}
}

#if HARDWARE_VER == WICAN_PRO
static esp_err_t i2c_master_init(void)
{
    int i2c_master_port = I2C_MASTER_NUM;

    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_MASTER_SDA_IO,
        .scl_io_num = I2C_MASTER_SCL_IO,
        .sda_pullup_en = GPIO_PULLUP_DISABLE,
        .scl_pullup_en = GPIO_PULLUP_DISABLE,
        .master.clk_speed = I2C_MASTER_FREQ_HZ,
    };

    i2c_param_config(i2c_master_port, &conf);
    
    return i2c_driver_install(i2c_master_port, conf.mode, I2C_MASTER_RX_BUF_DISABLE, I2C_MASTER_TX_BUF_DISABLE, 0);
}
#endif
#if HARDWARE_VER == WICAN_PRO
void send_to_host(char* str, uint32_t len, QueueHandle_t *q, char* cmd_str)
#else
void send_to_host(char* str, uint32_t len, QueueHandle_t *q)
#endif
{
    if (q == NULL || *q == NULL)
    {
        ESP_LOGE(TAG, "Null queue");
        return;
    }

    static xdev_buffer xsend_buffer;  // Consider non-static for multithreading
    uint32_t bytes_sent = 0;
    uint32_t bytes_to_send;
	ESP_LOGW(TAG, "Herer");
    if (len == 0)
    {
		if(str == NULL || strlen(str) == 0)
		{
			ESP_LOGE(TAG, "Empty string");
			return;
		}
        len = strlen(str);  // Cache the string length
    }

    while (bytes_sent < len)
    {
        // Determine the number of bytes to send in this iteration
        bytes_to_send = len - bytes_sent;
        if (bytes_to_send > DEV_BUFFER_LENGTH)
        {
            bytes_to_send = DEV_BUFFER_LENGTH;
        }
		
        // Copy the data to the buffer
        memcpy(xsend_buffer.ucElement, str + bytes_sent, bytes_to_send);
        xsend_buffer.usLen = bytes_to_send;
		ESP_LOG_BUFFER_HEXDUMP(TAG, xsend_buffer.ucElement, bytes_to_send, ESP_LOG_INFO);
        // Send the buffer to the queue with a timeout
        if (xQueueSend(*q, (void*)&xsend_buffer, pdMS_TO_TICKS(100)) != pdPASS)
        {
            ESP_LOGE(TAG, "Failed to send data to queue");
            return;
        }

        // Update the number of bytes sent
        bytes_sent += bytes_to_send;
    }
	ESP_LOGI(TAG, "bytes_sent: %lu", bytes_sent);
}


static void can_tx_task(void *pvParameters)
{
	while(1)
	{
		twai_message_t tx_msg;

		memset(ucTCP_RX_Buffer.ucElement,0, DEV_BUFFER_LENGTH);
		xQueueReceive(xMsg_Rx_Queue, &ucTCP_RX_Buffer, portMAX_DELAY);
		ESP_LOGI(TAG, "----------");
		ESP_LOG_BUFFER_HEXDUMP(TAG, ucTCP_RX_Buffer.ucElement, ucTCP_RX_Buffer.usLen, ESP_LOG_INFO);
		ESP_LOGI(TAG, "----------");
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
			#if HARDWARE_VER == WICAN_PRO
			static char elm327_cmd_buffer[2048];
			static uint32_t cmd_buffer_len = 0;
			static int64_t last_cmd_time = 0;
			// memset(elm327_cmd_buffer, 0, sizeof(elm327_cmd_buffer));
			if(ucTCP_RX_Buffer.dev_channel == DEV_WIFI)
			{
				elm327_process_cmd(msg_ptr, temp_len, &xMsg_Tx_Queue, elm327_cmd_buffer, &cmd_buffer_len, &last_cmd_time, &send_to_host);
			}
			else if(ucTCP_RX_Buffer.dev_channel == DEV_BLE)
			{
				elm327_send_cmd(msg_ptr, temp_len);
				// elm327_process_cmd(msg_ptr, temp_len, &xmsg_ble_tx_queue, elm327_cmd_buffer, &cmd_buffer_len, &last_cmd_time, &send_to_host);
				// elm327_run_command((char*)msg_ptr, temp_len, 1000, &xmsg_ble_tx_queue, &send_to_host);
			}
			#else
			if(ucTCP_RX_Buffer.dev_channel == DEV_WIFI)
			{
				elm327_process_cmd(msg_ptr, temp_len, &tx_msg, &xMsg_Tx_Queue);
			}
			else if(ucTCP_RX_Buffer.dev_channel == DEV_BLE)
			{
				elm327_process_cmd(msg_ptr, temp_len, &tx_msg, &xmsg_ble_tx_queue);
			}
			#endif
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
        static twai_message_t rx_msg;
//        esp_err_t ret = 0xFF;


//    	time_old = esp_timer_get_time();
//    	if((esp_timer_get_time() - time_old) > 1000000)
//    	{
//    		sleep_mode_get_voltage(&bvoltage);
//    		time_old = esp_timer_get_time();
//
//    		ESP_LOGI(TAG, "bvoltage: %f", bvoltage);
//    	}
        // process_led(0);
    	if(esp_timer_get_time() - time_old > 1000*1000)
    	{
    		// uint32_t free_heap = heap_caps_get_free_size(HEAP_CAPS);
    		// time_old = esp_timer_get_time();
    		// ESP_LOGI(TAG, "free_heap: %lu", free_heap);
//        		ESP_LOGI(TAG, "msg %u/sec", num_msg);
//        		num_msg = 0;
    	}

		// if(gpio_get_level(USB_ID_PIN) == 0)
		// {
		// 	// gpio_set_level(USB_OTG_PWR_EN, 1);
		// 	gpio_set_level(USB_ESP_MODE_EN, 1);
		// }
		// else
		// {
		// 	// gpio_set_level(USB_OTG_PWR_EN, 0);
		// 	gpio_set_level(USB_ESP_MODE_EN, 0);
		// }
        while(can_receive(&rx_msg, 0) ==  ESP_OK)
        {
//        	num_msg++;

        	process_led(1);

        	if(config_server_ws_connected())
        	{
        		ucTCP_TX_Buffer.usLen = slcan_parse_frame(ucTCP_TX_Buffer.ucElement, &rx_msg);
				if(config_server_ws_connected())
				{
					xQueueSend( xmsg_ws_tx_queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(0) );
				}
        	}
        	//TODO: optimize, useless ifs
			if(tcp_port_open() || ble_connected() || HARDWARE_VER == WICAN_USB_V100 || mqtt_connected() || protocol == AUTO_PID )
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
				#if HARDWARE_VER != WICAN_PRO
				else if(protocol == OBD_ELM327 || protocol == AUTO_PID)
				{
					// Let elm327.c decide which messages to process
					xQueueSend( xmsg_obd_rx_queue, ( void * ) &rx_msg, pdMS_TO_TICKS(0) );
				}
				#endif




				if(ucTCP_TX_Buffer.usLen != 0)
				{
					if(tcp_port_open())
					{
						xQueueSend( xMsg_Tx_Queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(0) );
					}
					if(ble_connected())
					{
						xQueueSend( xmsg_ble_tx_queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(0) );
					}
					else if(HARDWARE_VER == WICAN_USB_V100)
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
				static mqtt_can_message_t mqtt_rx_msg;
				if(mqtt_elm327_log_en == 0)
				{
					mqtt_rx_msg.frame.extd = rx_msg.extd;
					mqtt_rx_msg.frame.rtr = rx_msg.rtr;
					mqtt_rx_msg.frame.ss = rx_msg.ss;
					mqtt_rx_msg.frame.self = rx_msg.self;
					mqtt_rx_msg.frame.dlc_non_comp = rx_msg.dlc_non_comp;
					mqtt_rx_msg.frame.identifier = rx_msg.identifier;
					mqtt_rx_msg.frame.data_length_code = rx_msg.data_length_code;

					mqtt_rx_msg.frame.data[0] = rx_msg.data[0];
					mqtt_rx_msg.frame.data[1] = rx_msg.data[1];
					mqtt_rx_msg.frame.data[2] = rx_msg.data[2];
					mqtt_rx_msg.frame.data[3] = rx_msg.data[3];
					mqtt_rx_msg.frame.data[4] = rx_msg.data[4];
					mqtt_rx_msg.frame.data[5] = rx_msg.data[5];
					mqtt_rx_msg.frame.data[6] = rx_msg.data[6];
					mqtt_rx_msg.frame.data[7] = rx_msg.data[7];

					mqtt_rx_msg.type = MQTT_CAN;
					xQueueSend( xmsg_mqtt_rx_queue, ( void * ) &mqtt_rx_msg, pdMS_TO_TICKS(0) );
				}
			}
        }
        vTaskDelay(pdMS_TO_TICKS(1));
	}
}

#ifdef PRINT_HEAP
static void print_heap_task(void *pvParameters)
{
    while(1) 
	{
        uint32_t free_internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t free_psram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
        
        // Largest free blocks
        uint32_t largest_internal = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t largest_psram = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);
        
        // Minimum free heap size seen
        uint32_t min_free_internal = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t min_free_psram = heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM);

        // Total heap size
        multi_heap_info_t info;
        heap_caps_get_info(&info, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t total_internal = info.total_free_bytes + info.total_allocated_bytes;
		ESP_LOGI("HEAP", "********************************");
        ESP_LOGI("HEAP", "Internal RAM - Free: %lu, Largest: %lu, Total: %lu, Min Free Ever: %lu", 
                 free_internal, largest_internal, total_internal, min_free_internal);
        ESP_LOGI("HEAP", "PSRAM      - Free: %lu, Largest: %lu, Min Free Ever: %lu", 
                 free_psram, largest_psram, min_free_psram);
		ESP_LOGI("HEAP", "********************************\r\n");
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
#endif

#if HARDWARE_VER == WICAN_PRO
//This is a hack to get ble working 
static void obd_rx_task(void *pvParameters)
{
	while (1)
	{
		memset(ucBLE_TX_Buffer.ucElement,0, DEV_BUFFER_LENGTH);
		xQueueReceive(xmsg_obd_rx_queue, &ucBLE_TX_Buffer, portMAX_DELAY);
		if(ble_connected())
		{
			xQueueSend( xmsg_ble_tx_queue, ( void * ) &ucBLE_TX_Buffer, pdMS_TO_TICKS(0) );
		}
	}
}
#endif 

void app_main(void)
{
	void* internal_buf = NULL;
	// internal_buf = heap_caps_malloc(64 * 1024, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);

	gpio_reset_pin(BUTTON_GPIO_NUM);
	gpio_set_direction(BUTTON_GPIO_NUM, GPIO_MODE_INPUT);
	gpio_set_pull_mode(BUTTON_GPIO_NUM, GPIO_PULLUP_ONLY);
	sd_card_init();
	
	if(gpio_get_level(BUTTON_GPIO_NUM) == 0)
	{
		sdcard_perform_ota_update("/wican.bin");
	}

	#ifdef PRINT_HEAP
	static StackType_t *heap_task_stack;
	static StaticTask_t heap_task_buffer;

	heap_task_stack = heap_caps_malloc(1024*3 * sizeof(StackType_t), MALLOC_CAP_SPIRAM);

	if (heap_task_stack != NULL) {
		xTaskCreateStatic(print_heap_task, 
						"heap_task", 
						(1024*3),
						NULL,
						1, 
						heap_task_stack,
						&heap_task_buffer);
	}
	#endif

	sleep_mode_print_wakeup_reason();

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
	printf("Project Version: %d\n", HARDWARE_VER);
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

	#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
	gpio_set_level(CONNECTED_LED_GPIO_NUM, 1);
	gpio_set_level(ACTIVE_LED_GPIO_NUM, 1);
	#endif

	gpio_reset_pin(USB_ID_PIN);
	gpio_set_direction(USB_ID_PIN, GPIO_MODE_INPUT);
	gpio_reset_pin(4);
	gpio_set_direction(4, GPIO_MODE_INPUT);
	gpio_pullup_dis(4);
	gpio_pulldown_dis(4);
	
	#if HARDWARE_VER == WICAN_PRO
	i2c_master_init();
	led_init(I2C_MASTER_NUM);
	imu_init(I2C_MASTER_NUM, I2C_MASTER_SDA_IO, I2C_MASTER_SCL_IO, IMU_INT_GPIO_NUM);
	rtcm_init(I2C_MASTER_NUM);
	// rtcm_set_time(0x23, 0x32, 0x00);  // 12:30:00 in BCD
	// rtcm_set_date(0x24, 0x12, 0x27, 0x06);  // 2024-01-20 Saturday(6) in BCD	
	#endif

	gpio_reset_pin(0);
	gpio_set_direction(0, GPIO_MODE_INPUT);
	gpio_reset_pin(USB_OTG_PWR_EN);
	gpio_set_direction(USB_OTG_PWR_EN, GPIO_MODE_INPUT);

	
	gpio_reset_pin(CAN_STDBY_GPIO_NUM);
	gpio_set_direction(CAN_STDBY_GPIO_NUM, GPIO_MODE_OUTPUT);
	gpio_set_level(CAN_STDBY_GPIO_NUM, 1);

    gpio_reset_pin(OBD_LED_EN_PIN);
    gpio_set_direction(OBD_LED_EN_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(OBD_LED_EN_PIN, 1);

    gpio_reset_pin(OBD_RESET_PIN);
    gpio_set_direction(OBD_RESET_PIN, GPIO_MODE_OUTPUT_OD);
    gpio_set_level(OBD_RESET_PIN, 1);

	gpio_reset_pin(OBD_READY_PIN);
	gpio_set_direction(OBD_READY_PIN, GPIO_MODE_INPUT);
	// gpio_reset_pin(USB_ESP_MODE_EN);
	// gpio_set_direction(USB_ESP_MODE_EN, GPIO_MODE_OUTPUT);
	// gpio_set_level(USB_ESP_MODE_EN, 1);

	// while(1)
	// {
	// 	printf("Project Version: %d\n", HARDWARE_VER);
	// 	vTaskDelay(pdMS_TO_TICKS(1000));
	// 	if(gpio_get_level(USB_ID_PIN) == 0)
	// 	{
	// 		gpio_set_level(USB_OTG_PWR_EN, 1);
	// 		gpio_set_level(USB_ESP_MODE_EN, 1);
	// 	}
	// 	else
	// 	{
	// 		gpio_set_level(USB_OTG_PWR_EN, 0);
	// 		gpio_set_level(USB_ESP_MODE_EN, 0);
	// 	}
	// }
	#if HARDWARE_VER == WICAN_PRO
	wc_uart_init();
	#endif
	#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
	xMsg_Rx_Queue = xQueueCreate(32, sizeof( xdev_buffer) );
    xMsg_Tx_Queue = xQueueCreate(32, sizeof( xdev_buffer) );
    xmsg_ws_tx_queue = xQueueCreate(32, sizeof( xdev_buffer) );
	#elif HARDWARE_VER == WICAN_PRO
	static xdev_buffer* xMsg_Rx_Queue_Storage;
	static xdev_buffer* xMsg_Tx_Queue_Storage;
	static xdev_buffer* xmsg_ws_tx_queue_Storage;
	static StaticQueue_t xMsg_Rx_Queue_Buffer;
	static StaticQueue_t xMsg_Tx_Queue_Buffer;
	static StaticQueue_t xmsg_ws_tx_queue_Buffer;

	size_t xdev_buffer_size = sizeof( xdev_buffer);
	
    xMsg_Rx_Queue_Storage = (xdev_buffer *)heap_caps_malloc(32 * xdev_buffer_size, MALLOC_CAP_SPIRAM);
    xMsg_Tx_Queue_Storage = (xdev_buffer *)heap_caps_malloc(32 * xdev_buffer_size, MALLOC_CAP_SPIRAM);
    xmsg_ws_tx_queue_Storage = (xdev_buffer *)heap_caps_malloc(32 * xdev_buffer_size, MALLOC_CAP_SPIRAM);

    // Check if memory allocation was successful
    if (xMsg_Rx_Queue_Storage == NULL || xMsg_Tx_Queue_Storage == NULL || xmsg_ws_tx_queue_Storage == NULL) {
        // Handle memory allocation failure
        ESP_LOGE(TAG, "Failed to allocate memory for queues in external RAM");
        return;
    }

    // Create the static queues
    xMsg_Rx_Queue = xQueueCreateStatic(32, xdev_buffer_size, (uint8_t *)xMsg_Rx_Queue_Storage, &xMsg_Rx_Queue_Buffer);
    xMsg_Tx_Queue = xQueueCreateStatic(32, xdev_buffer_size, (uint8_t *)xMsg_Tx_Queue_Storage, &xMsg_Tx_Queue_Buffer);
    xmsg_ws_tx_queue = xQueueCreateStatic(32, xdev_buffer_size, (uint8_t *)xmsg_ws_tx_queue_Storage, &xmsg_ws_tx_queue_Buffer);

    // Check if queues were created successfully
    if (xMsg_Rx_Queue == NULL || xMsg_Tx_Queue == NULL || xmsg_ws_tx_queue == NULL) {
        // Handle queue creation failure
        ESP_LOGE(TAG, "Failed to create queues");
        return;
    }
	#endif


	esp_ota_mark_app_valid_cancel_rollback();
//    xmsg_obd_rx_queue = xQueueCreate(100, sizeof( twai_message_t) );

    ESP_ERROR_CHECK(esp_read_mac(derived_mac_addr, ESP_MAC_WIFI_SOFTAP));
    sprintf((char *)ble_uid,"WiC_%02x%02x%02x%02x%02x%02x",
            derived_mac_addr[0], derived_mac_addr[1], derived_mac_addr[2],
            derived_mac_addr[3], derived_mac_addr[4], derived_mac_addr[5]);

    sprintf((char *)uid,"%02x%02x%02x%02x%02x%02x",
            derived_mac_addr[0], derived_mac_addr[1], derived_mac_addr[2],
            derived_mac_addr[3], derived_mac_addr[4], derived_mac_addr[5]);
	#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
		config_server_start(&xmsg_ws_tx_queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM, (char*)&uid[0]);
	#else
		config_server_start(&xmsg_ws_tx_queue, &xMsg_Rx_Queue, 0, (char*)&uid[0]);
	#endif

	#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
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
	#elif HARDWARE_VER == WICAN_PRO
	sleep_mode_init();
	#endif


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
	// protocol = OBD_ELM327;

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
	#if HARDWARE_VER == WICAN_PRO
	// xmsg_obd_rx_queue = xQueueCreate(32, sizeof( twai_message_t) );
	// static uint8_t* elm327_uart_rx_queue_storage;
	// static StaticQueue_t xMsg_Rx_Queue_Buffer;


	// size_t buffer_size = (1024*10);
	
    // elm327_uart_rx_queue_storage = (uint8_t *)heap_caps_malloc(32 * buffer_size, MALLOC_CAP_SPIRAM);
	// xmsg_obd_rx_queue = xQueueCreateStatic(32, buffer_size, (uint8_t *)elm327_uart_rx_queue_storage, &xMsg_Rx_Queue_Buffer);
	static xdev_buffer* elm327_uart_rx_queue_storage;
	static StaticQueue_t elm327_uart_rx_queue_buffer;
	
    elm327_uart_rx_queue_storage = (xdev_buffer *)heap_caps_malloc(32 * xdev_buffer_size, MALLOC_CAP_SPIRAM);
	xmsg_obd_rx_queue = xQueueCreateStatic(32, xdev_buffer_size, (uint8_t *)elm327_uart_rx_queue_storage, &elm327_uart_rx_queue_buffer);

	elm327_init( &send_to_host, &xmsg_obd_rx_queue, NULL);
	if(protocol == AUTO_PID)
	{
		// can_set_bitrate(can_datarate);
		// #if HARDWARE_VER != WICAN_PRO
		// can_enable();
		// #endif
		uint32_t log_period = 0;
		if(config_server_get_log_period(&log_period) == -1)
		{
			ESP_LOGE(TAG, "error getting log period");
			log_period = 60;
		}
		autopid_init((char*)&uid[0], config_server_get_logger_config(), log_period);
	}

	#else
	else if(protocol == OBD_ELM327)
	{
//		can_init(CAN_500K);
		#if HARDWARE_VER != WICAN_PRO
		can_set_bitrate(can_datarate);
		can_set_silent(1);
		can_enable();
		#endif
		xmsg_obd_rx_queue = xQueueCreate(32, sizeof( twai_message_t) );
		if(config_server_mqtt_en_config() && config_server_mqtt_elm327_log())
		{
			mqtt_elm327_log_en = config_server_mqtt_elm327_log();
			elm327_init(&xmsg_obd_rx_queue, log_can_to_mqtt);
		}
		else
		{
			elm327_init(&xmsg_obd_rx_queue, NULL);
		}
	}
	else if(protocol == AUTO_PID)
	{
		can_set_bitrate(can_datarate);
		#if HARDWARE_VER != WICAN_PRO
		can_enable();
		#endif
		xmsg_obd_rx_queue = xQueueCreate(32, sizeof( twai_message_t) );
		
		elm327_init(&xmsg_obd_rx_queue, NULL);
		autopid_init((char*)&uid[0], config_server_get_auto_pid());
	}
	#endif
	

	if(config_server_mqtt_en_config())
	{
		xmsg_mqtt_rx_queue = xQueueCreate(32, sizeof(mqtt_can_message_t) );
		#if HARDWARE_VER == WICAN_PRO
		// if(protocol != AUTO_PID && protocol != OBD_ELM327)
		{
			can_set_bitrate(can_datarate);
			can_enable();
		}
		// else
		// {
		// 	can_disable();
		// }
		#else
		can_set_bitrate(can_datarate);
		can_enable();
		#endif
		
		#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
			mqtt_init((char*)&uid[0], CONNECTED_LED_GPIO_NUM, &xmsg_mqtt_rx_queue);
		#else
			mqtt_init((char*)&uid[0], 0, &xmsg_mqtt_rx_queue);
		#endif
		
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
		port = 35000;
	}

	if(protocol != AUTO_PID)
	{
		if(config_server_get_port_type() == UDP_PORT)
		{	
			#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
			tcp_server_init(port, &xMsg_Tx_Queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM, 1);
			#elif HARDWARE_VER == WICAN_PRO
			tcp_server_init(port, &xMsg_Tx_Queue, &xMsg_Rx_Queue, 0, 1);
			#endif
		}
		else
		{
			#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
			tcp_server_init(port, &xMsg_Tx_Queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM, 0);
			#elif HARDWARE_VER == WICAN_PRO
			tcp_server_init(port, &xMsg_Tx_Queue, &xMsg_Rx_Queue, 0, 0);
			#endif
		}
		
		if(config_server_get_ble_config())
		{
			int pass = config_server_ble_pass();
			static xdev_buffer* xmsg_ble_tx_queue_Storage;
			static StaticQueue_t xmsg_ble_tx_queue_Buffer;
			// xMsg_Rx_Queue_Storage = (xdev_buffer *)heap_caps_malloc(32 * xdev_buffer_size, MALLOC_CAP_SPIRAM);
			// xmsg_ble_tx_queue = xQueueCreate(20, sizeof( xdev_buffer) );
			xmsg_ble_tx_queue_Storage = (xdev_buffer *)heap_caps_malloc(100 * xdev_buffer_size, MALLOC_CAP_SPIRAM);
			xmsg_ble_tx_queue = xQueueCreateStatic(100, xdev_buffer_size, (uint8_t *)xmsg_ble_tx_queue_Storage, &xmsg_ble_tx_queue_Buffer);
			#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
			ble_init(&xmsg_ble_tx_queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM, pass, &ble_uid[0]);
			#elif HARDWARE_VER == WICAN_PRO
			if(internal_buf != NULL)
			{
				free(internal_buf);
			}
			ble_init(&xmsg_ble_tx_queue, &xMsg_Rx_Queue, 0, pass, &ble_uid[0]);
			#endif
		}
	}

    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_app_desc_t running_app_info;
	uint32_t firmware_ver_minor, firmware_ver_major;

    if (esp_ota_get_partition_description(running, &running_app_info) == ESP_OK)
    {
        ESP_LOGI(TAG, "Running firmware version: %s", running_app_info.version);
        ESP_LOGI(TAG, "Project Name: %s", running_app_info.project_name);

        if(HARDWARE_VER == WICAN_USB_V100)
        {
        	// project_hardware_rev = WICAN_USB_V100;
        	// ESP_LOGI(TAG, "project_hardware_rev: USB");
        	if(!config_server_mqtt_en_config())
        	{
				#if HARDWARE_VER == WICAN_USB_V100
				xmsg_uart_tx_queue = xQueueCreate(32, sizeof( xdev_buffer) );
				wc_uart_init(&xmsg_uart_tx_queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM);
				#endif
        	}

        }
        // else
        // {
        // 	ESP_LOGI(TAG, "project_hardware_rev: OBD");
        //     if(strstr(running_app_info.project_name, "hv210") != 0)
        //     {
        //     	project_hardware_rev = WICAN_V210;
        //     }
        //     else
        //     {
        //     	project_hardware_rev = WICAN_V300;
        //     }
        // }
    }
	wc_mdns_init((char*)uid, hardware_version, firmware_version);
// Declare static stack and task buffers
	static StackType_t *can_rx_task_stack, *can_tx_task_stack, *obd_rx_task_stack;
	static StaticTask_t can_rx_task_buffer, can_tx_task_buffer, obd_rx_task_buffer;

	// Allocate stack memory in PSRAM
	can_rx_task_stack = heap_caps_malloc(1024*3, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
	can_tx_task_stack = heap_caps_malloc(1024*3, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
	obd_rx_task_stack = heap_caps_malloc(1024*3, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);

	if (can_rx_task_stack != NULL && can_tx_task_stack != NULL && obd_rx_task_stack != NULL) 
	{
		// xTaskCreateStatic(can_rx_task, "can_rx_task", 1024*3, (void*)AF_INET, 5, 
		// 				can_rx_task_stack, &can_rx_task_buffer);
		xTaskCreateStatic(can_tx_task, "can_tx_task", 1024*3, (void*)AF_INET, 5, 
						can_tx_task_stack, &can_tx_task_buffer);
		xTaskCreateStatic(obd_rx_task, "obd_rx_task", 1024*3, (void*)AF_INET, 5, 
						obd_rx_task_stack, &obd_rx_task_buffer);
	} 
	else 
	{
		ESP_LOGE(TAG, "Failed to allocate task stacks in PSRAM");
		return;
	}



	
	wusb3801_init(I2C_MASTER_NUM);
	// xEventTask = xEventGroupCreate();
	// xTaskCreate(ftp_task, "FTP", 1024*6, NULL, 2, NULL);
	// xEventGroupWaitBits( xEventTask,
	// FTP_TASK_FINISH_BIT, /* The bits within the event group to wait for. */
	// pdTRUE, /* BIT_0 should be cleared before returning. */
	// pdFALSE, /* Don't wait for both bits, either bit will do. */
	// portMAX_DELAY);/* Wait forever. */ 
	esp_log_level_set("*", ESP_LOG_NONE);
	// esp_log_level_set("*", ESP_LOG_ERROR);
	// esp_log_level_set("HEAP", ESP_LOG_INFO);
	// esp_log_level_set("imu", ESP_LOG_INFO);
	// esp_log_level_set("rtcm", ESP_LOG_INFO);
	// esp_log_level_set("console", ESP_LOG_INFO);
	// esp_log_level_set("usb", ESP_LOG_INFO);
	esp_log_level_set("read_ss_adc_voltage", ESP_LOG_NONE);	
	// esp_log_level_set("HEAP", ESP_LOG_NONE);
	// esp_log_level_set("autopid_find_standard_pid", ESP_LOG_INFO);
	// esp_log_level_set("SLEEP_MODE", ESP_LOG_INFO);
	// esp_log_level_set("OBD_LOGGER", ESP_LOG_INFO);
	// esp_log_level_set("OBD_LOGGER_WS_IFACE", ESP_LOG_INFO);
	// esp_log_level_set("CONFIG_SERVER", ESP_LOG_INFO);
	// esp_log_level_set("EX_TIME", ESP_LOG_INFO);
	// esp_log_level_set("AUTO_PID", ESP_LOG_INFO);
	// esp_log_level_set("QUERY_EXAMPLE", ESP_LOG_INFO);
	// esp_log_level_set("sqlite_log", ESP_LOG_INFO);
	// esp_log_level_set("AUTO_PID", ESP_LOG_INFO);
	
	#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
    gpio_set_level(PWR_LED_GPIO_NUM, 1);
	#elif HARDWARE_VER == WICAN_PRO
	led_set_level(0,0,200);
	if(gpio_get_level(USB_ID_PIN) == 0)
	{
		usb_host_init();
	}
    #endif

	console_init();
	if(internal_buf != NULL)
	{
		free(internal_buf);
	}
}

