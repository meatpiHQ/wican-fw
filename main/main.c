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
#include "tcp_server.h"
#include "lwip/sockets.h"
#include "driver/twai.h"
#include "config_server.h"
#include "realdash.h"
#include "slcan.h"
#include "can.h"
#include "ble.h"

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

static uint8_t protocol = SOCKETCAN;

static int s_retry_num = 0;

static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT 			BIT0
#define WIFI_FAIL_BIT     			BIT1
#define WIFI_DISCONNECTED_BIT      	BIT2
#define EXAMPLE_ESP_MAXIMUM_RETRY 	10
char sta_ip[20] = {0};
TimerHandle_t xLedTimer;

static void event_handler(void* arg, esp_event_base_t event_base,
                                int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START)
    {
        esp_wifi_connect();
    }
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED)
    {
    	config_server_wifi_connected(0);
    	xEventGroupSetBits(s_wifi_event_group, WIFI_DISCONNECTED_BIT);

//        if (s_retry_num < EXAMPLE_ESP_MAXIMUM_RETRY)
        {
//            esp_wifi_connect();
//            s_retry_num++;
//            ESP_LOGI(TAG, "retry to connect to the AP");
        }
//        else
//        {
//            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
//        }
        ESP_LOGI(TAG,"connect to the AP fail");
    }
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP)
    {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "got ip:" IPSTR, IP2STR(&event->ip_info.ip));

//        ESP_LOGE(TAG, "ip: %d.%d.%d.%d", IP2STR(&event->ip_info.ip));
        sprintf(sta_ip, "%d.%d.%d.%d", IP2STR(&event->ip_info.ip));

        config_server_set_sta_ip(sta_ip);
        s_retry_num = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        xEventGroupClearBits(s_wifi_event_group, WIFI_DISCONNECTED_BIT);
        config_server_wifi_connected(1);
    }

    if (event_id == WIFI_EVENT_AP_STACONNECTED)
    {
        wifi_event_ap_staconnected_t* event = (wifi_event_ap_staconnected_t*) event_data;
        ESP_LOGI(TAG, "station "MACSTR" join, AID=%d",
                 MAC2STR(event->mac), event->aid);
    }
    else if (event_id == WIFI_EVENT_AP_STADISCONNECTED)
    {
        wifi_event_ap_stadisconnected_t* event = (wifi_event_ap_stadisconnected_t*) event_data;
        ESP_LOGI(TAG, "station "MACSTR" leave, AID=%d",
                 MAC2STR(event->mac), event->aid);
    }
    else if(event_id == WIFI_EVENT_AP_START)
    {

    }
}

static void process_led(bool state)
{
	static bool current_state;
	static int64_t last_change;

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
//static void active_led(bool state)
//{
//	static uint8_t last_state;
//	//Semaphore might not be needed
//	if( xSemaphoreTake( xLed_Semaphore, 0 ) == pdTRUE )
//	{
//		if(state && last_state == 0)
//		{
//			gpio_set_level(ACTIVE_LED_GPIO_NUM, 0);
//			last_state = 1;
//			xTimerStart( xLedTimer, 0 );
//		}
//		else if(state == 0)
//		{
//			gpio_set_level(ACTIVE_LED_GPIO_NUM, 1);
//			last_state = 0;
//		}
//		xSemaphoreGive( xLed_Semaphore );
//	}
//}
//#define MSG_PRINT

//static void vLedTimerCallback( TimerHandle_t xTimer )
//{
//	active_led(0);
//}
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

		if(protocol == SOCKETCAN)
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
//			if(tcp_port_open() || ble_connected())
			{
#ifdef MSG_PRINT
				ESP_LOGI(TAG, "sending to socket");
#endif

				memset(ucTCP_TX_Buffer.ucElement, 0, sizeof(ucTCP_TX_Buffer.ucElement));
				if(protocol == SOCKETCAN)
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
					xQueueSend( xMsg_Tx_Queue, ( void * ) &ucTCP_TX_Buffer, portMAX_DELAY );
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

static void monitor_task(void *pvParameters)
{
	static const TickType_t connect_delay[] = {pdMS_TO_TICKS(2000), pdMS_TO_TICKS(4000),
												pdMS_TO_TICKS(6000),pdMS_TO_TICKS(8000),
												pdMS_TO_TICKS(10000),pdMS_TO_TICKS(10000)};
	uint8_t i = 0;

	while(1)
	{
		EventBits_t bits = xEventGroupGetBits(s_wifi_event_group);

		if(!(bits & WIFI_DISCONNECTED_BIT))
		{
			i = 0;
		}
		xEventGroupWaitBits(s_wifi_event_group,
							WIFI_DISCONNECTED_BIT,
							pdFALSE,
							pdFALSE,
							portMAX_DELAY);
		if(i >= 6)
		{
			i = 0;
		}
		vTaskDelay(connect_delay[i++]);
		ESP_LOGI(TAG, "Trying to reconnect...");
		esp_wifi_connect();

	}

}



void wifi_init(void)
{
	esp_netif_t* wifiAP = esp_netif_create_default_wifi_ap();

    int8_t channel = config_server_get_ap_ch();
	if(channel == -1)
	{
		channel = 6;
	}
	ESP_LOGE(TAG, "AP Channel:%d", channel);


//    ESP_ERROR_CHECK(esp_netif_init());

//    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t instance_any_id;
    esp_event_handler_instance_t instance_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT,
                                                        ESP_EVENT_ANY_ID,
                                                        &event_handler,
                                                        NULL,
                                                        &instance_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT,
                                                        IP_EVENT_STA_GOT_IP,
                                                        &event_handler,
                                                        NULL,
                                                        &instance_got_ip));

    static wifi_config_t wifi_config_sta = {
        .sta = {
            .ssid = "",
            .password = "",
            /* Setting a password implies station will connect to all security modes including WEP/WPA.
             * However these modes are deprecated and not advisable to be used. Incase your Access point
             * doesn't support WPA2, these mode can be enabled by commenting below line */
			.threshold.authmode = WIFI_AUTH_WPA2_PSK,

            .pmf_cfg = {
                .capable = true,
                .required = false
            },
        },
    };
    static wifi_config_t wifi_config_ap =
    {
        .ap = {
            .max_connection = 4,
            .authmode = WIFI_AUTH_WPA_WPA2_PSK
        },
    };
    wifi_config_ap.ap.channel = channel;
    if(config_server_get_wifi_mode() == APSTA_MODE)
    {
    	strcpy( (char*)wifi_config_sta.sta.ssid, (char*)config_server_get_sta_ssid());
    	strcpy( (char*)wifi_config_sta.sta.password, (char*)config_server_get_sta_pass());
    	ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    	ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config_sta) );
    }
    else
    {
    	ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    }



    uint8_t derived_mac_addr[6] = {0};
    ESP_ERROR_CHECK(esp_read_mac(derived_mac_addr, ESP_MAC_WIFI_SOFTAP));
    sprintf((char *)wifi_config_ap.ap.ssid,"WiCAN_%02x%02x%02x%02x%02x%02x",
            derived_mac_addr[0], derived_mac_addr[1], derived_mac_addr[2],
            derived_mac_addr[3], derived_mac_addr[4], derived_mac_addr[5]);
    strcpy( (char*)wifi_config_ap.ap.password, (char*)config_server_get_ap_pass());

    esp_netif_ip_info_t ipInfo;
    IP4_ADDR(&ipInfo.ip, 192,168,80,1);
	IP4_ADDR(&ipInfo.gw, 192,168,80,1);
	IP4_ADDR(&ipInfo.netmask, 255,255,255,0);
	esp_netif_dhcps_stop(wifiAP);
	esp_netif_set_ip_info(wifiAP, &ipInfo);
	esp_netif_dhcps_start(wifiAP);

	ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config_ap));

    ESP_ERROR_CHECK(esp_wifi_start() );

    ESP_LOGI(TAG, "wifi_init finished.");
//    if(config_server_get_wifi_mode() == APSTA_MODE)
//    {
//		/* Waiting until either the connection is established (WIFI_CONNECTED_BIT) or connection failed for the maximum
//		 * number of re-tries (WIFI_FAIL_BIT). The bits are set by event_handler() (see above) */
//		EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
//				WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
//				pdFALSE,
//				pdFALSE,
//				portMAX_DELAY);
//
//		/* xEventGroupWaitBits() returns the bits before the call returned, hence we can test which event actually
//		 * happened. */
//		if (bits & WIFI_CONNECTED_BIT) {
//			ESP_LOGI(TAG, "connected to ap SSID:%s password:%s",
//					wifi_config_sta.sta.ssid, "*");
//		} else if (bits & WIFI_FAIL_BIT) {
//			ESP_LOGI(TAG, "Failed to connect to SSID:%s, password:%s",
//					wifi_config_sta.sta.ssid, "*");
//		} else {
//			ESP_LOGE(TAG, "UNEXPECTED EVENT");
//		}
//
//		/* The event will not be processed after unregister */
//		ESP_ERROR_CHECK(esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, instance_got_ip));
//		ESP_ERROR_CHECK(esp_event_handler_instance_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID, instance_any_id));
//		vEventGroupDelete(s_wifi_event_group);
//    }
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


    s_wifi_event_group = xEventGroupCreate();

//    if(ble_flag == 0)
//    if(1)
//	if(!BLE_Enabled())
//    if(1)
    if(!config_server_get_ble_config())
    {
		wifi_init();
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
    }
    else
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
    xTaskCreate(monitor_task, "monitor_task", 4096, (void*)AF_INET, 5, NULL);

}

