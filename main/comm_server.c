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
#include "esp_timer.h"
#include "lwip/err.h"
#include "lwip/sockets.h"
#include "lwip/sys.h"
#include <lwip/netdb.h>
#include "types.h"
#include "comm_server.h"
#include "hw_config.h"

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
		// Holding the semaphore is not necessary; recv and send on the same
		// socket don't need to be synchronized.
		rx_buffer.usLen = recv(sock, rx_buffer.ucElement, sizeof(rx_buffer.ucElement) - 1, 0);
		if (rx_buffer.usLen <= 0)
		{
			if (rx_buffer.usLen < 0)
			{
				ESP_LOGE(TAG, "Error occurred during receiving: errno %d", errno);
			}
			else
			{
				ESP_LOGW(TAG, "Connection closed");
			}
			// Stop the tx task streaming into the dead session, and shut the fd
			// down so the peer isn't left hanging until the server task reaps it.
			xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
			gpio_set_level(conn_led, LED_OFF);
			shutdown(sock, SHUT_RDWR);
			// PORT_CLOSED must be raised last, after this task is done touching
			// the socket: the server task takes it as the ack that nobody is
			// blocked in recv() and the fd can be closed/replaced safely.
			xEventGroupSetBits( xSocketEventGroup, PORT_CLOSED_BIT );
			goto wait_skt_rx;
		}
		else
		{
			rx_buffer.dev_channel = DEV_WIFI;
			rx_buffer.ucElement[rx_buffer.usLen] = 0; // Null-terminate whatever is received and treat it like a string
//			ESP_LOGI(TAG, "Received %d bytes: %s", rx_buffer.usLen, rx_buffer.ucElement);
			// Blocking here is the backpressure path:
			// TCP's receive window closes and throttles the peer.
			xQueueSend( *xRX_Queue, ( void * ) &rx_buffer, portMAX_DELAY );
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
		if (rx_buffer.usLen < 0)
		{
			xEventGroupSetBits( xSocketEventGroup, PORT_CLOSED_BIT );
			xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
			ESP_LOGE(TAG, "Error occurred during receiving: errno %d", errno);
			goto wait_skt_rx;

		}
		else
		{
			rx_buffer.dev_channel = DEV_WIFI;
			rx_buffer.ucElement[rx_buffer.usLen] = 0; // Null-terminate whatever is received and treat it like a string
			xQueueSend( *xRX_Queue, ( void * ) &rx_buffer, portMAX_DELAY );
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
	// One queue item per send() couldn't keep up with a busy CAN bus: every
	// ~20-byte GVRET frame paid a full trip through the lwip tcpip thread.
	// Coalesce the queue backlog into one MSS-sized segment per send() instead.
	// TCP only--all the stream protocols (gvret/slcan/elm327) tolerate
	// arbitrary segmentation.
	static uint8_t coalesce_buf[1460];

	while(1)
	{
		xQueueReceive(*xTX_Queue, ( void * ) &tx_buffer, portMAX_DELAY);

		// No client connected: discard the frame. Letting the queue fill would
		// stall send_to_host()/can_tx_task long after the disconnect, and the
		// next client would receive the dead session's leftover frames.
		if (!(xEventGroupGetBits(xSocketEventGroup) & PORT_OPEN_BIT))
		{
			continue;
		}

		int bytes_used = tx_buffer.usLen;
		memcpy(coalesce_buf, tx_buffer.ucElement, bytes_used);
		// Drain whatever is already queued while a worst-case item still fits.
		while (bytes_used + DEV_BUFFER_LENGTH <= (int)sizeof(coalesce_buf) &&
		       xQueueReceive(*xTX_Queue, ( void * ) &tx_buffer, 0) == pdTRUE)
		{
			memcpy(coalesce_buf + bytes_used, tx_buffer.ucElement, tx_buffer.usLen);
			bytes_used += tx_buffer.usLen;
		}

		if( xSemaphoreTake( xTCP_Socket_Semaphore, portMAX_DELAY ) == pdTRUE )
		{
			// While send() is stalled here (wifi retries, full lwip sndbuf), the
			// producer's queue absorbs bus traffic before frames drop at the
			// producer. Log stalls to correlate with drops.
			int64_t send_start = esp_timer_get_time();
			int to_write = bytes_used;
			while (to_write > 0)
			{
				int written = send(sock, coalesce_buf + (bytes_used - to_write), to_write, 0);
				if (written < 0)
				{
					ESP_LOGE(TAG, "Error occurred during sending: errno %d", errno);
					// Flag the session dead and kick the rx task out of recv() via
					// shutdown; only the rx task raises PORT_CLOSED, so the server
					// task can use that bit as proof nobody is blocked on this fd.
					xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
					gpio_set_level(conn_led, LED_OFF);
					shutdown(sock, SHUT_RDWR);
					break;
				}
				to_write -= written;
			}
			int64_t send_us = esp_timer_get_time() - send_start;
			if (send_us > 20000)
			{
				ESP_LOGW(TAG, "send() stalled %lld us, %u msgs queued behind it",
				         send_us, (unsigned)uxQueueMessagesWaiting(*xTX_Queue));
			}
			xSemaphoreGive( xTCP_Socket_Semaphore );
		}
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
			int new_sock = accept(listen_sock, (struct sockaddr *)&source_addr, &addr_len);
			if (new_sock < 0)
			{
				ESP_LOGE(TAG, "Unable to accept connection: errno %d", errno);
				continue;
			}
			// Set tcp keepalive option
			setsockopt(new_sock, SOL_SOCKET, SO_KEEPALIVE, &keepAlive, sizeof(int));
			setsockopt(new_sock, IPPROTO_TCP, TCP_KEEPIDLE, &keepIdle, sizeof(int));
			setsockopt(new_sock, IPPROTO_TCP, TCP_KEEPINTVL, &keepInterval, sizeof(int));
			setsockopt(new_sock, IPPROTO_TCP, TCP_KEEPCNT, &keepCount, sizeof(int));
			// Bound how many GVRET commands can queue on-device, so keepalive replies stay
			// within SavvyCAN's ~10 s watchdog even when a CAN bus is saturated.
			// Once this fills, TCP flow control makes further data wait in the
			// sender's own send buffer instead of piling up here; nothing is dropped.
			int rcvBuf = 2048;
			setsockopt(new_sock, SOL_SOCKET, SO_RCVBUF, &rcvBuf, sizeof(int));
			// Convert ip address to string; keep the source port too, so a
			// connection can be matched to a process on the client machine.
			if (source_addr.ss_family == PF_INET)
			{
				inet_ntoa_r(((struct sockaddr_in *)&source_addr)->sin_addr, addr_str, sizeof(addr_str) - 1);
				size_t ip_len = strlen(addr_str);
				snprintf(addr_str + ip_len, sizeof(addr_str) - ip_len, ":%u",
				         ntohs(((struct sockaddr_in *)&source_addr)->sin_port));
			}
			ESP_LOGI(TAG, "Socket accepted ip address: %s", addr_str);

			// Prevent a TCP connect with no data from evicting a live session, as an
			// extra measure to prevent non-GVRET clients from taking over.
			if (sock >= 0 && (xEventGroupGetBits(xSocketEventGroup) & PORT_OPEN_BIT))
			{
				fd_set rfds;
				struct timeval tv = { .tv_sec = 2, .tv_usec = 0 };
				FD_ZERO(&rfds);
				FD_SET(new_sock, &rfds);
				uint8_t peek[16];
				int peeked;
				// MSG_PEEK leaves the hello bytes queued for the rx task.
				if (select(new_sock + 1, &rfds, NULL, NULL, &tv) <= 0 ||
				    (peeked = recv(new_sock, peek, sizeof(peek), MSG_PEEK)) <= 0)
				{
					ESP_LOGW(TAG, "Rejecting %s: session active and newcomer sent no data", addr_str);
					close(new_sock);
					continue;
				}
				ESP_LOGW(TAG, "New client %s, closing previous session; hello bytes:", addr_str);
				ESP_LOG_BUFFER_HEXDUMP(TAG, peek, peeked, ESP_LOG_WARN);
			}

			// Take over from any previous session. If the old client vanished
			// without a trace (wifi drop), its socket can sit ESTABLISHED for
			// a while until keepalive/retransmit timeouts fire; a new client
			// must not have to wait that out.
			if (sock >= 0)
			{
				// Unblocks the rx task's recv() (and any stalled tx send()) no
				// matter what state the old session is in.
				shutdown(sock, SHUT_RDWR);
				// Wait for the rx task to park (it raises PORT_CLOSED only after
				// it is done with the socket) before invalidating the fd under it.
				// The timeout only hits if the rx task is wedged somewhere other
				// than recv(); reject the client rather than risk the fd.
				if (!(xEventGroupWaitBits(xSocketEventGroup, PORT_CLOSED_BIT,
				                          pdFALSE, pdFALSE, pdMS_TO_TICKS(5000)) & PORT_CLOSED_BIT))
				{
					ESP_LOGE(TAG, "Previous session did not release the socket, rejecting new client");
					close(new_sock);
					continue;
				}
				// The tx task sends while holding the socket semaphore; taking it
				// here means it is not mid-send() on the fd we are about to close.
				xSemaphoreTake(xTCP_Socket_Semaphore, portMAX_DELAY);
				close(sock);
				sock = new_sock;
				xSemaphoreGive(xTCP_Socket_Semaphore);
			}
			else
			{
				sock = new_sock;
			}
			xEventGroupClearBits(xSocketEventGroup, PORT_CLOSED_BIT);
			xEventGroupSetBits( xSocketEventGroup, PORT_OPEN_BIT );
			gpio_set_level(conn_led, LED_ON);
		}
		else
		{
			ESP_LOGI(TAG, "UDP socket ready");
			xEventGroupClearBits(xSocketEventGroup, PORT_CLOSED_BIT);
			xEventGroupSetBits( xSocketEventGroup, PORT_OPEN_BIT );
			gpio_set_level(conn_led, LED_ON);
            ESP_LOGI(TAG, "Waiting for data");

			xEventGroupWaitBits(
					  xSocketEventGroup,   /* The event group being tested. */
					  PORT_CLOSED_BIT, /* The bits within the event group to wait for. */
					  pdFALSE,        /* BIT_0 & BIT_4 should be cleared before returning. */
					  pdFALSE,       /* Don't wait for both bits, either bit will do. */
					  portMAX_DELAY );/* Wait a maximum of 100ms for either bit to be set. */

			xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
			ESP_LOGI(TAG, "UDP socket error");

			gpio_set_level(conn_led, LED_OFF);
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
	xSocketEventGroup = xEventGroupCreate();
	xEventGroupSetBits( xSocketEventGroup, PORT_CLOSED_BIT );
	xEventGroupClearBits( xSocketEventGroup, PORT_OPEN_BIT );
	udp_enable = udp_en;
	xTaskCreate(tcp_server_task, "tcp_server", 4096, (void*)AF_INET, 5, &xserver_handle);
	if(!udp_enable)
	{
		xTaskCreate(tcp_server_rx_task, "tcp_rx_server", 4096, (void*)AF_INET, 5, &xrx_handle);
		xTaskCreate(tcp_server_tx_task, "tcp_tx_server", 4096, (void*)AF_INET, 5, &xtx_handle);
	}
	else
	{
		xTaskCreate(udp_server_rx_task, "udp_rx_server", 4096, (void*)AF_INET, 5, &xrx_handle);
		xTaskCreate(udp_server_tx_task, "udp_tx_server", 4096, (void*)AF_INET, 5, &xtx_handle);
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


