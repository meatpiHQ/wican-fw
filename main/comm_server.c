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

#include <string.h>
#include <sys/param.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "driver/gpio.h"
#include "lwip/err.h"
#include "lwip/sockets.h"
#include "lwip/sys.h"
#include <lwip/netdb.h>
#include "types.h"
#include "comm_server.h"

#define TAG 		__func__

#define KEEPALIVE_IDLE              5
#define KEEPALIVE_INTERVAL          5
#define KEEPALIVE_COUNT             3

#define PORT_CLOSED_BIT			BIT0
#define PORT_OPEN_BIT			BIT1


static uint32_t server_port = 0;
static int sock = -1;
int listen_sock;
static EventGroupHandle_t xSocketEventGroup;
static StaticEventGroup_t xSocketEventGroupBuffer;
static QueueHandle_t *xTX_Queue, *xRX_Queue;
static SemaphoreHandle_t xTCP_Socket_Semaphore;
static uint8_t conn_led = 0;

uint8_t udp_enable = 0;

static void tcp_server_rx_task(void *pvParameters)
{
//	int addr_family = (int)pvParameters;
//    int len;
    static xdev_buffer rx_buffer;

wait_skt_rx:
	xEventGroupWaitBits(
					  xSocketEventGroup,   /* The event group being tested. */
					  PORT_OPEN_BIT, /* The bits within the event group to wait for. */
					  pdFALSE,        /* BIT_0 & BIT_4 should be cleared before returning. */
					  pdFALSE,       /* Don't wait for both bits, either bit will do. */
					  portMAX_DELAY );/* Wait a maximum of 100ms for either bit to be set. */
	while(1)
	{
		rx_buffer.usLen = recv(sock, rx_buffer.ucElement, sizeof(rx_buffer.ucElement) - 1, 0);
        if( xSemaphoreTake( xTCP_Socket_Semaphore, portMAX_DELAY ) == pdTRUE )
        {
        	//check if sock still connected?
			if (rx_buffer.usLen < 0)
			{
				xEventGroupSetBits( xSocketEventGroup, PORT_CLOSED_BIT );
				xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
				ESP_LOGE(TAG, "Error occurred during receiving: errno %d", errno);
				xSemaphoreGive( xTCP_Socket_Semaphore );
				goto wait_skt_rx;

			} else if (rx_buffer.usLen == 0)
			{
				xEventGroupSetBits( xSocketEventGroup, PORT_CLOSED_BIT );
				xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
				ESP_LOGW(TAG, "Connection closed");
				xSemaphoreGive( xTCP_Socket_Semaphore );
				goto wait_skt_rx;
			}
			else
			{
				rx_buffer.dev_channel = DEV_WIFI;
				rx_buffer.ucElement[rx_buffer.usLen] = 0; // Null-terminate whatever is received and treat it like a string
//				ESP_LOGI(TAG, "Received %d bytes: %s", rx_buffer.usLen, rx_buffer.ucElement);
		        //TODO: what happens if blocked for ever?
				xQueueSend( *xRX_Queue, ( void * ) &rx_buffer, portMAX_DELAY );
			}
			xSemaphoreGive( xTCP_Socket_Semaphore );
        }

	}
}

static void udp_server_rx_task(void *pvParameters)
{
//	int addr_family = (int)pvParameters;
//    int len;
    static xdev_buffer rx_buffer;

wait_skt_rx:
	xEventGroupWaitBits(
					  xSocketEventGroup,   /* The event group being tested. */
					  PORT_OPEN_BIT, /* The bits within the event group to wait for. */
					  pdFALSE,        /* BIT_0 & BIT_4 should be cleared before returning. */
					  pdFALSE,       /* Don't wait for both bits, either bit will do. */
					  portMAX_DELAY );/* Wait a maximum of 100ms for either bit to be set. */
    struct sockaddr_storage source_addr; // Large enough for both IPv4 or IPv6
    socklen_t socklen = sizeof(source_addr);
//    int len = recvfrom(listen_sock, rx_buffer, sizeof(rx_buffer) - 1, 0, (struct sockaddr *)&source_addr, &socklen);
	while(1)
	{
//		 = recv(sock, rx_buffer.ucElement, sizeof(rx_buffer.ucElement) - 1, 0);
		rx_buffer.usLen = recvfrom(listen_sock, rx_buffer.ucElement, sizeof(rx_buffer.ucElement) - 1, 0, (struct sockaddr *)&source_addr, &socklen);
        if( xSemaphoreTake( xTCP_Socket_Semaphore, portMAX_DELAY ) == pdTRUE )
        {
        	//check if sock still connected?
			if (rx_buffer.usLen < 0)
			{
				xEventGroupSetBits( xSocketEventGroup, PORT_CLOSED_BIT );
				xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
				ESP_LOGE(TAG, "Error occurred during receiving: errno %d", errno);
				xSemaphoreGive( xTCP_Socket_Semaphore );
				goto wait_skt_rx;

			}
			else
			{
				rx_buffer.dev_channel = DEV_WIFI;
				rx_buffer.ucElement[rx_buffer.usLen] = 0; // Null-terminate whatever is received and treat it like a string
//				ESP_LOGI(TAG, "Received %d bytes: %s", rx_buffer.usLen, rx_buffer.ucElement);
		        //TODO: what happens if blocked for ever?
				xQueueSend( *xRX_Queue, ( void * ) &rx_buffer, portMAX_DELAY );
			}
			xSemaphoreGive( xTCP_Socket_Semaphore );
        }

	}
}

static void udp_server_tx_task(void *pvParameters)
{
//	int addr_family = (int)pvParameters;
	static xdev_buffer tx_buffer;
	struct sockaddr_in Recv_addr;

	Recv_addr.sin_family       = AF_INET;
	Recv_addr.sin_port         = htons(server_port);
	Recv_addr.sin_addr.s_addr  = INADDR_BROADCAST;


wait_skt_tx:
	xEventGroupWaitBits(
					  xSocketEventGroup,   /* The event group being tested. */
					  PORT_OPEN_BIT, /* The bits within the event group to wait for. */
					  pdFALSE,        /* BIT_0 & BIT_4 should be cleared before returning. */
					  pdFALSE,       /* Don't wait for both bits, either bit will do. */
					  portMAX_DELAY );/* Wait a maximum of 100ms for either bit to be set. */
	ESP_LOGI(TAG, "Socket connected...");
	while(1)
	{
		xQueueReceive(*xTX_Queue, ( void * ) &tx_buffer, portMAX_DELAY);
//		ESP_LOGI(TAG, "Sending %d bytes: %s", tx_buffer.usLen, tx_buffer.ucElement);
        if( xSemaphoreTake( xTCP_Socket_Semaphore, portMAX_DELAY ) == pdTRUE )
        {
			int err = sendto(listen_sock, tx_buffer.ucElement, tx_buffer.usLen, 0, (struct sockaddr *)&Recv_addr, sizeof(Recv_addr));
			if (err < 0)
			{
				ESP_LOGE(TAG, "Error occurred during sending: errno %d", errno);
				xEventGroupSetBits( xSocketEventGroup, PORT_CLOSED_BIT );
				xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
				xSemaphoreGive( xTCP_Socket_Semaphore );
				goto wait_skt_tx;
			}
        }
        xSemaphoreGive( xTCP_Socket_Semaphore );
	}
}

static void tcp_server_tx_task(void *pvParameters)
{
//	int addr_family = (int)pvParameters;
	static xdev_buffer tx_buffer;

wait_skt_tx:
	xEventGroupWaitBits(
					  xSocketEventGroup,   /* The event group being tested. */
					  PORT_OPEN_BIT, /* The bits within the event group to wait for. */
					  pdFALSE,        /* BIT_0 & BIT_4 should be cleared before returning. */
					  pdFALSE,       /* Don't wait for both bits, either bit will do. */
					  portMAX_DELAY );/* Wait a maximum of 100ms for either bit to be set. */
	ESP_LOGI(TAG, "Socket connected...");
	while(1)
	{
		xQueuePeek(*xTX_Queue, ( void * ) &tx_buffer, portMAX_DELAY);

		while(xQueuePeek(*xTX_Queue, ( void * ) &tx_buffer, 0) == pdTRUE)
		{
			if( xSemaphoreTake( xTCP_Socket_Semaphore, portMAX_DELAY ) == pdTRUE )
			{
				int to_write = tx_buffer.usLen;
				xQueueReceive(*xTX_Queue, ( void * ) &tx_buffer, 0);
				while (to_write > 0)
				{
					int written = send(sock, tx_buffer.ucElement + (tx_buffer.usLen - to_write), to_write, 0);
					if (written < 0)
					{
						ESP_LOGE(TAG, "Error occurred during sending: errno %d", errno);
						xEventGroupSetBits( xSocketEventGroup, PORT_CLOSED_BIT );
						xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
						xSemaphoreGive( xTCP_Socket_Semaphore );
						goto wait_skt_tx;
					}
					to_write -= written;
				}
			}
			xSemaphoreGive( xTCP_Socket_Semaphore );
		}
		vTaskDelay(pdMS_TO_TICKS(1));
	}
}

char rx_buffer[128];
static void tcp_server_task(void *pvParameters)
{
    char addr_str[128];
    int addr_family = (int)pvParameters;
    int ip_protocol = 0;
    int keepAlive = 1;
    int keepIdle = KEEPALIVE_IDLE;
    int keepInterval = KEEPALIVE_INTERVAL;
    int keepCount = KEEPALIVE_COUNT;
    struct sockaddr_storage dest_addr;


    struct sockaddr_storage source_addr; // Large enough for both IPv4 or IPv6
    socklen_t addr_len = sizeof(source_addr);
//    EventBits_t uxBits;

    if (addr_family == AF_INET)
    {
        struct sockaddr_in *dest_addr_ip4 = (struct sockaddr_in *)&dest_addr;
        dest_addr_ip4->sin_addr.s_addr = htonl(INADDR_ANY);
        dest_addr_ip4->sin_family = AF_INET;
        dest_addr_ip4->sin_port = htons(server_port);
        ip_protocol = IPPROTO_IP;
    }

    if(!udp_enable)
    {
    	listen_sock = socket(addr_family, SOCK_STREAM, ip_protocol);
    }
    else
    {
    	listen_sock = socket(addr_family, SOCK_DGRAM, ip_protocol);
    }

    if (listen_sock < 0)
    {
        ESP_LOGE(TAG, "Unable to create socket: errno %d", errno);
        vTaskDelete(NULL);
        return;
    }
    int opt = 1;
    if(!udp_enable)
    {
    	setsockopt(listen_sock, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    }
    ESP_LOGI(TAG, "Socket created");

    int err = bind(listen_sock, (struct sockaddr *)&dest_addr, sizeof(dest_addr));
    if (err != 0)
    {
        ESP_LOGE(TAG, "Socket unable to bind: errno %d", errno);
        ESP_LOGE(TAG, "IPPROTO: %d", addr_family);
        goto CLEAN_UP;
    }
    ESP_LOGI(TAG, "Socket bound, port %lu", server_port);

    if(!udp_enable)
    {
		err = listen(listen_sock, 1);
		if (err != 0)
		{
			ESP_LOGE(TAG, "Error occurred during listen: errno %d", errno);
			goto CLEAN_UP;
		}
    }


    while (1)
    {

		if(!udp_enable)
		{
			ESP_LOGI(TAG, "Socket listening");
accept_socket:
			sock = accept(listen_sock, (struct sockaddr *)&source_addr, &addr_len);
			if (sock < 0)
			{
				ESP_LOGE(TAG, "Unable to accept connection: errno %d", errno);
				goto accept_socket;
			}
			xEventGroupClearBits(xSocketEventGroup, PORT_CLOSED_BIT);// Set tcp keepalive option
			setsockopt(sock, SOL_SOCKET, SO_KEEPALIVE, &keepAlive, sizeof(int));
			setsockopt(sock, IPPROTO_TCP, TCP_KEEPIDLE, &keepIdle, sizeof(int));
			setsockopt(sock, IPPROTO_TCP, TCP_KEEPINTVL, &keepInterval, sizeof(int));
			setsockopt(sock, IPPROTO_TCP, TCP_KEEPCNT, &keepCount, sizeof(int));
			// Convert ip address to string
			if (source_addr.ss_family == PF_INET)
			{
				inet_ntoa_r(((struct sockaddr_in *)&source_addr)->sin_addr, addr_str, sizeof(addr_str) - 1);
			}
			ESP_LOGI(TAG, "Socket accepted ip address: %s", addr_str);
			xEventGroupSetBits( xSocketEventGroup, PORT_OPEN_BIT );
			#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
			gpio_set_level(conn_led, 0);
			#endif
			xEventGroupWaitBits(
					  xSocketEventGroup,   /* The event group being tested. */
					  PORT_CLOSED_BIT, /* The bits within the event group to wait for. */
					  pdFALSE,        /* BIT_0 & BIT_4 should be cleared before returning. */
					  pdFALSE,       /* Don't wait for both bits, either bit will do. */
					  portMAX_DELAY );/* Wait a maximum of 100ms for either bit to be set. */
			xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
			ESP_LOGI(TAG, "Socket disconnected...");
			#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
			gpio_set_level(conn_led, 1);
			shutdown(sock, 0);
			#endif
			close(sock);
		}
		else
		{
			ESP_LOGI(TAG, "UDP socket ready");
			xEventGroupClearBits(xSocketEventGroup, PORT_CLOSED_BIT);
			xEventGroupSetBits( xSocketEventGroup, PORT_OPEN_BIT );
			#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
			gpio_set_level(conn_led, 0);
			#endif
            ESP_LOGI(TAG, "Waiting for data");

			xEventGroupWaitBits(
					  xSocketEventGroup,   /* The event group being tested. */
					  PORT_CLOSED_BIT, /* The bits within the event group to wait for. */
					  pdFALSE,        /* BIT_0 & BIT_4 should be cleared before returning. */
					  pdFALSE,       /* Don't wait for both bits, either bit will do. */
					  portMAX_DELAY );/* Wait a maximum of 100ms for either bit to be set. */

			xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
			ESP_LOGI(TAG, "UDP socket error");

			#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
			gpio_set_level(conn_led, 1);
			#endif
			shutdown(sock, 0);
			close(sock);
			goto CLEAN_UP;

			//////
            struct sockaddr_storage source_addr; // Large enough for both IPv4 or IPv6
            socklen_t socklen = sizeof(source_addr);
            int len = recvfrom(listen_sock, rx_buffer, sizeof(rx_buffer) - 1, 0, (struct sockaddr *)&source_addr, &socklen);

            // Error occurred during receiving
            if (len < 0)
			{
                ESP_LOGE(TAG, "recvfrom failed: errno %d", errno);
                break;
            }
            // Data received
            else
			{
                // Get the sender's ip address as string
                if (source_addr.ss_family == PF_INET)
				{
                    inet_ntoa_r(((struct sockaddr_in *)&source_addr)->sin_addr, addr_str, sizeof(addr_str) - 1);
                }
				else if (source_addr.ss_family == PF_INET6)
				{
                    inet6_ntoa_r(((struct sockaddr_in6 *)&source_addr)->sin6_addr, addr_str, sizeof(addr_str) - 1);
                }

                rx_buffer[len] = 0; // Null-terminate whatever we received and treat like a string...
                ESP_LOGI(TAG, "Received %d bytes from %s:", len, addr_str);
                ESP_LOGI(TAG, "%s", rx_buffer);

                int err = sendto(listen_sock, rx_buffer, len, 0, (struct sockaddr *)&source_addr, sizeof(source_addr));
                if (err < 0)
				{
                    ESP_LOGE(TAG, "Error occurred during sending: errno %d", errno);
                    break;
                }
            }

            //////
		}
    }

CLEAN_UP:
    close(listen_sock);
    vTaskDelete(NULL);
}

int8_t tcp_port_open(void)
{
	EventBits_t uxBits;
	if(xSocketEventGroup != NULL)
	{
		uxBits = xEventGroupGetBits(xSocketEventGroup);

		return (uxBits & PORT_OPEN_BIT);
	}
	else return 0;
}
TaskHandle_t xserver_handle = NULL;
TaskHandle_t xtx_handle = NULL;
TaskHandle_t xrx_handle = NULL;
int8_t tcp_server_init(uint32_t port, QueueHandle_t *xTXp_Queue, QueueHandle_t *xRXp_Queue, uint8_t connected_led, uint8_t udp_en)
{
	server_port = port;
	xTX_Queue = xTXp_Queue;
	xRX_Queue = xRXp_Queue;
	conn_led = connected_led;
	xTCP_Socket_Semaphore = xSemaphoreCreateMutex();
	xSocketEventGroup = xEventGroupCreateStatic(&xSocketEventGroupBuffer);
	xEventGroupSetBits( xSocketEventGroup, PORT_CLOSED_BIT );
	xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
	udp_enable = udp_en;
	static StackType_t *server_task_stack, *rx_task_stack, *tx_task_stack;
	static StaticTask_t server_task_buffer, rx_task_buffer, tx_task_buffer;
	
	server_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
	rx_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
	tx_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
	
	if (server_task_stack == NULL || rx_task_stack == NULL || tx_task_stack == NULL)
	{
		ESP_LOGE(TAG, "Failed to allocate task stack memory");
		if (server_task_stack) heap_caps_free(server_task_stack);
		if (rx_task_stack) heap_caps_free(rx_task_stack);
		if (tx_task_stack) heap_caps_free(tx_task_stack);
		return -1;
	}
	
	// Create static tasks
	xserver_handle = xTaskCreateStatic(
		tcp_server_task,
		"tcp_server",
		4096,
		(void*)AF_INET,
		5,
		server_task_stack,
		&server_task_buffer
	);
	
	if (xserver_handle == NULL)
	{
		ESP_LOGE(TAG, "Failed to create server task");
		heap_caps_free(server_task_stack);
		heap_caps_free(rx_task_stack);
		heap_caps_free(tx_task_stack);
		return -1;
	}
	
	if(!udp_enable)
	{
		xrx_handle = xTaskCreateStatic(
			tcp_server_rx_task,
			"tcp_rx_server",
			4096,
			(void*)AF_INET,
			5,
			rx_task_stack,
			&rx_task_buffer
		);
		
		xtx_handle = xTaskCreateStatic(
			tcp_server_tx_task,
			"tcp_tx_server",
			4096,
			(void*)AF_INET,
			5,
			tx_task_stack,
			&tx_task_buffer
		);
	}
	else
	{
		xrx_handle = xTaskCreateStatic(
			udp_server_rx_task,
			"udp_rx_server",
			4096,
			(void*)AF_INET,
			5,
			rx_task_stack,
			&rx_task_buffer
		);
		
		xtx_handle = xTaskCreateStatic(
			udp_server_tx_task,
			"udp_tx_server",
			4096,
			(void*)AF_INET,
			5,
			tx_task_stack,
			&tx_task_buffer
		);
	}
	
	if (xrx_handle == NULL || xtx_handle == NULL)
	{
		ESP_LOGE(TAG, "Failed to create rx/tx tasks");
		if (xrx_handle == NULL) heap_caps_free(rx_task_stack);
		if (xtx_handle == NULL) heap_caps_free(tx_task_stack);
		return -1;
	}
	return 0;
}
void tcp_server_suspend(void)
{
	vTaskSuspend(xserver_handle);
	vTaskSuspend(xtx_handle);
	vTaskSuspend(xrx_handle);
}

void tcp_server_resume(void)
{
	vTaskResume(xserver_handle);
	vTaskResume(xtx_handle);
	vTaskResume(xrx_handle);
}


