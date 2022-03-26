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
#include "config_server.h"
#include "realdash.h"
#include "slcan.h"
#include "can.h"
#include "ble.h"
#include "wifi_network.h"
#define TAG 		__func__
#define TX_GPIO_NUM             	0
#define RX_GPIO_NUM             	3
#define CONNECTED_LED_GPIO_NUM		8
#define ACTIVE_LED_GPIO_NUM			9
#define BLE_EN_PIN_NUM				5
#define GPIO_OUTPUT_PIN_SEL  ((1ULL<<CONNECTED_LED_GPIO_NUM) | (1ULL<<ACTIVE_LED_GPIO_NUM))
#define BLE_EN_PIN_SEL		(1ULL<<BLE_EN_PIN_NUM)
#define BLE_Enabled()		(!gpio_get_level(BLE_EN_PIN_NUM))


//static SemaphoreHandle_t xLed_Semaphore;
static QueueHandle_t xMsg_Tx_Queue, xMsg_Rx_Queue;
static xTCP_Buffer ucTCP_RX_Buffer;
static xTCP_Buffer ucTCP_TX_Buffer;

static uint8_t protocol = SLCAN;

static int s_retry_num = 0;

static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT 			BIT0
#define WIFI_FAIL_BIT     			BIT1
#define WIFI_DISCONNECTED_BIT      	BIT2
#define EXAMPLE_ESP_MAXIMUM_RETRY 	10
//char sta_ip[20] = {0};
TimerHandle_t xLedTimer;

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

static void can_tx_task(void *pvParameters)
{
	uint8_t processed_index;

	while(1)
	{
		twai_message_t tx_msg;
		xQueueReceive(xMsg_Rx_Queue, &ucTCP_RX_Buffer, portMAX_DELAY);
#ifdef MSG_PRINT
		uint32_t i;
//		ucTCP_RX_Buffer.ucElement[ucTCP_RX_Buffer.usLen] = 0;
//		ESP_LOGI(TAG, "\r\n      Received %d bytes: %s\r\n", ucTCP_RX_Buffer.usLen, ucTCP_RX_Buffer.ucElement);
		ESP_LOGI(TAG, "TCP Received %d", ucTCP_RX_Buffer.usLen);
		for(i = 0; i < ucTCP_RX_Buffer.usLen; i++)
		{
			printf("HEX value = %x, Character = %c\n", ucTCP_RX_Buffer.ucElement[i] , ucTCP_RX_Buffer.ucElement[i] );
		}
//		printf()
#endif

		if(protocol == SLCAN || config_server_ws_connected())
		{
			uint8_t* msg_ptr = ucTCP_RX_Buffer.ucElement;
			char* ret;

			processed_index = 0;
			while(processed_index != (ucTCP_RX_Buffer.usLen))
			{
				ucTCP_RX_Buffer.usLen -= processed_index;
				msg_ptr += processed_index;
				ret = slcan_parse_str(msg_ptr, ucTCP_RX_Buffer.usLen, &tx_msg, &processed_index);
				if(ret != 0)
				{
					ucTCP_TX_Buffer.usLen = strlen(ret);
					memcpy(ucTCP_TX_Buffer.ucElement, ret, ucTCP_TX_Buffer.usLen);
					xQueueSend( xMsg_Tx_Queue, ( void * ) &ucTCP_TX_Buffer, portMAX_DELAY );
				}
				processed_index++;

//				ESP_LOGI(TAG, "ucTCP_RX_Buffer.usLen: %d, processed_index: %d", ucTCP_RX_Buffer.usLen, processed_index);
			}
		}
		else if(protocol == REALDASH)
		{
			real_dash_parse_66(&tx_msg, ucTCP_RX_Buffer.ucElement);

			can_send(&tx_msg, portMAX_DELAY);
		}
	}
}
static void can_rx_task(void *pvParameters)
{
	static uint32_t num_msg = 0;
//	static int64_t time_old = 0;

//	vTaskDelay(pdMS_TO_TICKS(30000));
//	config_server_stop();
//	wifi_network_deinit();
//	vTaskDelay(pdMS_TO_TICKS(30000));
//	wifi_network_init();
//	config_server_restart();
//	while(1){
//		vTaskDelay(pdMS_TO_TICKS(30000));
//	}
//	time_old = esp_timer_get_time();
	while(1)
	{
        twai_message_t rx_msg;
        esp_err_t ret = 0xFF;
        process_led(0);
        ret = can_receive(&rx_msg, pdMS_TO_TICKS(1));
        if(ret == ESP_OK)
        {
//        	num_msg++;
//        	if(esp_timer_get_time() - time_old > 1000*1000)
//        	{
//        		time_old = esp_timer_get_time();
//
//        		ESP_LOGI(TAG, "msg %u/sec", num_msg);
//        		num_msg = 0;
//        	}
//        	active_led(1);
        	process_led(1);
#ifdef MSG_PRINT
			ESP_LOGI(TAG, "ID: 0x%04x Data: %02x %02x %02x %02x %02x %02x %02x %02x", rx_msg.identifier, rx_msg.data[0], rx_msg.data[1], rx_msg.data[2], rx_msg.data[3], rx_msg.data[4], rx_msg.data[5], rx_msg.data[6], rx_msg.data[7]);
#endif
			if(tcp_port_open() || ble_connected() || config_server_ws_connected())
			{
#ifdef MSG_PRINT
				ESP_LOGI(TAG, "sending to socket");
#endif

				memset(ucTCP_TX_Buffer.ucElement, 0, sizeof(ucTCP_TX_Buffer.ucElement));
				if(protocol == SLCAN || config_server_ws_connected())
				{
					ucTCP_TX_Buffer.usLen = slcan_parse_frame(ucTCP_TX_Buffer.ucElement, &rx_msg);
				}
				else if(protocol == REALDASH)
				{
					ucTCP_TX_Buffer.usLen = real_dash_set_66(&rx_msg, ucTCP_TX_Buffer.ucElement);
				}


//				ESP_LOGI(TAG, "Sending  %d", ucTCP_TX_Buffer.usLen);
//				for(uint8_t i = 0; i < ucTCP_TX_Buffer.usLen; i++)
//				{
//					printf("HEX value = %x, Character = %c\n", ucTCP_TX_Buffer.ucElement[i] , ucTCP_TX_Buffer.ucElement[i] );
//				}
//				if(!ble_flag)
				{
					xQueueSend( xMsg_Tx_Queue, ( void * ) &ucTCP_TX_Buffer, pdMS_TO_TICKS(2000) );
				}
//				else
//				{
//					ble_realdash_send(ucTCP_TX_Buffer.ucElement, ucTCP_TX_Buffer.usLen);
//				}

//		        twai_message_t tx_msg;
//				if(real_dash_parse_66(&tx_msg, ucTCP_TX_Buffer.ucElement))
//				{
//					tx_msg.identifier +=1;
//					twai_transmit(&tx_msg, portMAX_DELAY);
//				}
//				else
//				{
//					ESP_LOGE(TAG, "error real_dash_parse_66!");
//				}
			}
        }
	}
}

static uint8_t uid[33];
void app_main(void)
{
	int32_t port;
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

	io_conf.mode = GPIO_MODE_INPUT;
	io_conf.pull_up_en = 1;
	io_conf.pin_bit_mask = BLE_EN_PIN_SEL;
	gpio_config(&io_conf);

    xMsg_Rx_Queue = xQueueCreate(100, sizeof( xTCP_Buffer) );
    xMsg_Tx_Queue = xQueueCreate(100, sizeof( xTCP_Buffer) );

	config_server_start(&xMsg_Tx_Queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM);

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

	wifi_network_init();
	port = config_server_get_port();
	ESP_LOGI(TAG, "Port: %d",port);


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
        sprintf((char *)uid,"WiCAN_%02x%02x%02x%02x%02x%02x",
                derived_mac_addr[0], derived_mac_addr[1], derived_mac_addr[2],
                derived_mac_addr[3], derived_mac_addr[4], derived_mac_addr[5]);
    	ble_init(&xMsg_Tx_Queue, &xMsg_Rx_Queue, CONNECTED_LED_GPIO_NUM, pass, &uid[0]);
    }

    xTaskCreate(can_rx_task, "main_task", 4096, (void*)AF_INET, 5, NULL);
    xTaskCreate(can_tx_task, "can_tx_task", 4096, (void*)AF_INET, 5, NULL);
//    esp_log_level_set("*", ESP_LOG_INFO);
//    xTaskCreate(monitor_task, "monitor_task", 4096, (void*)AF_INET, 5, NULL);

}

