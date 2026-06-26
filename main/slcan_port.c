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

/*
 * Dedicated, always-on SLCAN TCP listener for no-reboot coexistence (task #36).
 * See slcan_port.h for the architecture. This is a deliberate, simplified copy
 * of comm_server.c's TCP path (TCP only, no UDP/LED) so it shares NO module
 * state with the hardware-proven stock server -- nothing here can perturb the
 * datalogger/flash path that comm_server runs.
 */

#include <string.h>
#include <sys/param.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "lwip/err.h"
#include "lwip/sockets.h"
#include "lwip/sys.h"
#include <lwip/netdb.h>
#include "types.h"
#include "slcan_port.h"

#define TAG __func__

#define KEEPALIVE_IDLE      5
#define KEEPALIVE_INTERVAL  5
#define KEEPALIVE_COUNT     3

#define PORT_CLOSED_BIT     BIT0
#define PORT_OPEN_BIT       BIT1

static uint32_t s_port = 0;
static int s_sock = -1;
static int s_listen_sock = -1;
/* Bumped by the server task on every accept(). The rx task snapshots it before recv() and,
 * under the mutex, discards a recv result whose generation no longer matches -- otherwise a
 * stale recv on a just-closed fd could clear the FRESH PORT_OPEN bit of a new connection on a
 * fast host reconnect (the coexist flow reconnects on every ECU-reset boundary). */
static volatile uint32_t s_conn_gen = 0;
static EventGroupHandle_t s_event_group;
static StaticEventGroup_t s_event_group_buffer;
static QueueHandle_t *s_rx_queue;  /* shared with stock server (xMsg_Rx_Queue) */
static QueueHandle_t *s_tx_queue;  /* private to this port                     */
static SemaphoreHandle_t s_sock_mutex;

int8_t slcan_port_is_open(void)
{
	if (s_event_group != NULL)
	{
		return (xEventGroupGetBits(s_event_group) & PORT_OPEN_BIT) ? 1 : 0;
	}
	return 0;
}

uint32_t slcan_port_conn_gen(void)
{
	/* The current accept() generation while a client is connected, else 0 (no owner). The
	 * dead-man reaper uses 0 / a changed value as "the original host socket is gone". */
	if (s_event_group != NULL && (xEventGroupGetBits(s_event_group) & PORT_OPEN_BIT))
	{
		return s_conn_gen;
	}
	return 0;
}

/* Receive client frames, tag DEV_SLCAN_PORT, push onto the shared RX queue. */
static void slcan_port_rx_task(void *pvParameters)
{
	static xdev_buffer rx_buffer;

wait_skt_rx:
	xEventGroupWaitBits(s_event_group, PORT_OPEN_BIT, pdFALSE, pdFALSE, portMAX_DELAY);
	while (1)
	{
		uint32_t gen = s_conn_gen;  /* snapshot before blocking in recv */
		rx_buffer.usLen = recv(s_sock, rx_buffer.ucElement, sizeof(rx_buffer.ucElement) - 1, 0);
		if (xSemaphoreTake(s_sock_mutex, portMAX_DELAY) == pdTRUE)
		{
			/* The connection changed while we were blocked in recv(): this result is for a
			 * dead fd. Discard it WITHOUT touching the PORT bits (a new connection may have
			 * already armed PORT_OPEN) and re-block. */
			if (gen != s_conn_gen)
			{
				xSemaphoreGive(s_sock_mutex);
				goto wait_skt_rx;
			}
			if (rx_buffer.usLen < 0)
			{
				xEventGroupSetBits(s_event_group, PORT_CLOSED_BIT);
				xEventGroupClearBits(s_event_group, PORT_OPEN_BIT);
				ESP_LOGE(TAG, "Error during receiving: errno %d", errno);
				xSemaphoreGive(s_sock_mutex);
				goto wait_skt_rx;
			}
			else if (rx_buffer.usLen == 0)
			{
				xEventGroupSetBits(s_event_group, PORT_CLOSED_BIT);
				xEventGroupClearBits(s_event_group, PORT_OPEN_BIT);
				ESP_LOGW(TAG, "Connection closed");
				xSemaphoreGive(s_sock_mutex);
				goto wait_skt_rx;
			}
			else
			{
				rx_buffer.dev_channel = DEV_SLCAN_PORT;
				rx_buffer.ucElement[rx_buffer.usLen] = 0;
				xQueueSend(*s_rx_queue, (void *)&rx_buffer, portMAX_DELAY);
			}
			xSemaphoreGive(s_sock_mutex);
		}
	}
}

/* Drain this port's private TX queue to the connected client socket. */
static void slcan_port_tx_task(void *pvParameters)
{
	static xdev_buffer tx_buffer;

wait_skt_tx:
	xEventGroupWaitBits(s_event_group, PORT_OPEN_BIT, pdFALSE, pdFALSE, portMAX_DELAY);
	ESP_LOGI(TAG, "Dedicated SLCAN port socket connected");
	while (1)
	{
		xQueuePeek(*s_tx_queue, (void *)&tx_buffer, portMAX_DELAY);

		while (xQueuePeek(*s_tx_queue, (void *)&tx_buffer, 0) == pdTRUE)
		{
			if (xSemaphoreTake(s_sock_mutex, portMAX_DELAY) == pdTRUE)
			{
				int to_write = tx_buffer.usLen;
				xQueueReceive(*s_tx_queue, (void *)&tx_buffer, 0);
				while (to_write > 0)
				{
					int written = send(s_sock, tx_buffer.ucElement + (tx_buffer.usLen - to_write), to_write, 0);
					if (written < 0)
					{
						ESP_LOGE(TAG, "Error during sending: errno %d", errno);
						xEventGroupSetBits(s_event_group, PORT_CLOSED_BIT);
						xEventGroupClearBits(s_event_group, PORT_OPEN_BIT);
						xSemaphoreGive(s_sock_mutex);
						goto wait_skt_tx;
					}
					to_write -= written;
				}
			}
			xSemaphoreGive(s_sock_mutex);
		}
		vTaskDelay(pdMS_TO_TICKS(1));
	}
}

/* Bind/listen/accept loop for the dedicated port. */
static void slcan_port_server_task(void *pvParameters)
{
	char addr_str[128];
	int keepAlive = 1;
	int keepIdle = KEEPALIVE_IDLE;
	int keepInterval = KEEPALIVE_INTERVAL;
	int keepCount = KEEPALIVE_COUNT;
	struct sockaddr_storage dest_addr;
	struct sockaddr_storage source_addr;
	socklen_t addr_len = sizeof(source_addr);

	struct sockaddr_in *dest_addr_ip4 = (struct sockaddr_in *)&dest_addr;
	dest_addr_ip4->sin_addr.s_addr = htonl(INADDR_ANY);
	dest_addr_ip4->sin_family = AF_INET;
	dest_addr_ip4->sin_port = htons(s_port);

	/* Bring up the listening socket with retry: a transient netif-not-ready or a brief
	 * SO_REUSEADDR/bind race at boot self-heals instead of permanently killing the listener
	 * (which would silently disable coexistence and force the host onto the reboot path). Each
	 * failed attempt closes its fd before retrying so we never leak a descriptor. */
	for (;;)
	{
		s_listen_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
		if (s_listen_sock < 0)
		{
			ESP_LOGE(TAG, "Unable to create socket: errno %d (retry)", errno);
			vTaskDelay(pdMS_TO_TICKS(1000));
			continue;
		}
		int opt = 1;
		setsockopt(s_listen_sock, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

		if (bind(s_listen_sock, (struct sockaddr *)&dest_addr, sizeof(dest_addr)) != 0)
		{
			ESP_LOGE(TAG, "Socket unable to bind port %lu: errno %d (retry)", s_port, errno);
			close(s_listen_sock);
			s_listen_sock = -1;
			vTaskDelay(pdMS_TO_TICKS(1000));
			continue;
		}

		if (listen(s_listen_sock, 1) != 0)
		{
			ESP_LOGE(TAG, "Error during listen: errno %d (retry)", errno);
			close(s_listen_sock);
			s_listen_sock = -1;
			vTaskDelay(pdMS_TO_TICKS(1000));
			continue;
		}
		break;
	}
	ESP_LOGI(TAG, "Dedicated SLCAN port bound + listening, port %lu", s_port);

	while (1)
	{
		ESP_LOGI(TAG, "Dedicated SLCAN port listening");
	accept_socket:
		s_sock = accept(s_listen_sock, (struct sockaddr *)&source_addr, &addr_len);
		if (s_sock < 0)
		{
			ESP_LOGE(TAG, "Unable to accept connection: errno %d", errno);
			goto accept_socket;
		}
		s_conn_gen++;  /* mark a new connection generation (see rx-task stale-recv guard) */
		xEventGroupClearBits(s_event_group, PORT_CLOSED_BIT);
		setsockopt(s_sock, SOL_SOCKET, SO_KEEPALIVE, &keepAlive, sizeof(int));
		setsockopt(s_sock, IPPROTO_TCP, TCP_KEEPIDLE, &keepIdle, sizeof(int));
		setsockopt(s_sock, IPPROTO_TCP, TCP_KEEPINTVL, &keepInterval, sizeof(int));
		setsockopt(s_sock, IPPROTO_TCP, TCP_KEEPCNT, &keepCount, sizeof(int));
		if (source_addr.ss_family == PF_INET)
		{
			inet_ntoa_r(((struct sockaddr_in *)&source_addr)->sin_addr, addr_str, sizeof(addr_str) - 1);
			ESP_LOGI(TAG, "Dedicated SLCAN port accepted ip: %s", addr_str);
		}
		xEventGroupSetBits(s_event_group, PORT_OPEN_BIT);
		xEventGroupWaitBits(s_event_group, PORT_CLOSED_BIT, pdFALSE, pdFALSE, portMAX_DELAY);
		xEventGroupClearBits(s_event_group, PORT_OPEN_BIT);
		ESP_LOGI(TAG, "Dedicated SLCAN port disconnected");
		shutdown(s_sock, 0);
		close(s_sock);
		s_sock = -1;
	}
}

int8_t slcan_port_init(uint32_t port, QueueHandle_t *rx_queue, QueueHandle_t *tx_queue)
{
	s_port = port;
	s_rx_queue = rx_queue;
	s_tx_queue = tx_queue;
	s_sock_mutex = xSemaphoreCreateMutex();
	s_event_group = xEventGroupCreateStatic(&s_event_group_buffer);
	xEventGroupSetBits(s_event_group, PORT_CLOSED_BIT);
	xEventGroupClearBits(s_event_group, PORT_OPEN_BIT);

	static StackType_t *server_task_stack, *rx_task_stack, *tx_task_stack;
	static StaticTask_t server_task_buffer, rx_task_buffer, tx_task_buffer;

	server_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
	rx_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
	tx_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);

	if (server_task_stack == NULL || rx_task_stack == NULL || tx_task_stack == NULL)
	{
		ESP_LOGE(TAG, "Failed to allocate task stack memory");
		if (server_task_stack) heap_caps_free(server_task_stack);
		if (rx_task_stack) heap_caps_free(rx_task_stack);
		if (tx_task_stack) heap_caps_free(tx_task_stack);
		return -1;
	}

	TaskHandle_t server_handle = xTaskCreateStatic(
		slcan_port_server_task, "slcan_port_srv", 4096, NULL, 5, server_task_stack, &server_task_buffer);
	TaskHandle_t rx_handle = xTaskCreateStatic(
		slcan_port_rx_task, "slcan_port_rx", 4096, NULL, 5, rx_task_stack, &rx_task_buffer);
	TaskHandle_t tx_handle = xTaskCreateStatic(
		slcan_port_tx_task, "slcan_port_tx", 4096, NULL, 5, tx_task_stack, &tx_task_buffer);

	if (server_handle == NULL || rx_handle == NULL || tx_handle == NULL)
	{
		ESP_LOGE(TAG, "Failed to create dedicated SLCAN port tasks");
		return -1;
	}
	ESP_LOGI(TAG, "Dedicated SLCAN port initialised on %lu", port);
	return 0;
}
