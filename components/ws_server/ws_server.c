#include "ws_server.h"

#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"

#include "freertos/event_groups.h"
#include "freertos/task.h"

#include "types.h"

#define TAG "WS_SERVER"

#define WS_CONNECTED_BIT BIT0

typedef struct
{
	httpd_handle_t hd;
	int fd;
} ws_async_ctx_t;

static ws_async_ctx_t s_ctx;

static EventGroupHandle_t s_event_group;
static StaticEventGroup_t s_event_group_buffer;

static QueueHandle_t *s_tx_queue;
static QueueHandle_t *s_rx_queue;

static TaskHandle_t s_ws_task;
static StaticTask_t s_ws_task_buffer;
static StackType_t *s_ws_task_stack;

static ws_server_hooks_t s_hooks;

static esp_err_t ws_handler(httpd_req_t *req)
{
	if (req->method == HTTP_GET)
	{
		s_ctx.hd = req->handle;
		s_ctx.fd = httpd_req_to_sockfd(req);

		xEventGroupSetBits(s_event_group, WS_CONNECTED_BIT);
		if (s_hooks.on_open != NULL)
		{
			s_hooks.on_open(s_hooks.ctx);
		}
		return ESP_OK;
	}

	httpd_ws_frame_t ws_pkt;
	uint8_t *buf = NULL;
	memset(&ws_pkt, 0, sizeof(ws_pkt));
	ws_pkt.type = HTTPD_WS_TYPE_TEXT;

	esp_err_t ret = httpd_ws_recv_frame(req, &ws_pkt, 0);
	if (ret != ESP_OK)
	{
		ESP_LOGE(TAG, "httpd_ws_recv_frame failed to get frame len: %d", ret);
		return ret;
	}

	if (ws_pkt.len)
	{
		buf = calloc(1, ws_pkt.len + 1);
		if (buf == NULL)
		{
			ESP_LOGE(TAG, "Failed to calloc memory for ws buf");
			return ESP_ERR_NO_MEM;
		}

		ws_pkt.payload = buf;
		ret = httpd_ws_recv_frame(req, &ws_pkt, ws_pkt.len);
		if (ret != ESP_OK)
		{
			ESP_LOGE(TAG, "httpd_ws_recv_frame failed: %d", ret);
			free(buf);
			return ret;
		}
	}

	if (ws_pkt.payload && ws_pkt.len)
	{
		bool handled = false;
		if (s_hooks.handle_frame != NULL)
		{
			handled = s_hooks.handle_frame(req, ws_pkt.payload, ws_pkt.len, s_hooks.ctx);
		}

		if (!handled && s_rx_queue != NULL)
		{
			static xdev_buffer rx_buffer;
			memcpy(rx_buffer.ucElement, ws_pkt.payload, ws_pkt.len);
			rx_buffer.dev_channel = DEV_WIFI_WS;
			rx_buffer.usLen = ws_pkt.len;
			xQueueSend(*s_rx_queue, (void *)&rx_buffer, portMAX_DELAY);
		}
	}

	free(buf);
	return ret;
}

static void websocket_task(void *pvParameters)
{
	(void)pvParameters;

	static xdev_buffer tx_buffer;
	httpd_ws_frame_t ws_pkt;

	ESP_LOGI(TAG, "websocket_task started");

	while (1)
	{
		if (s_tx_queue == NULL)
		{
			vTaskDelay(pdMS_TO_TICKS(100));
			continue;
		}

		xQueueReceive(*s_tx_queue, &tx_buffer, portMAX_DELAY);

		memset(&ws_pkt, 0, sizeof(ws_pkt));
		ws_pkt.payload = (uint8_t *)tx_buffer.ucElement;
		ws_pkt.len = tx_buffer.usLen;
		ws_pkt.type = HTTPD_WS_TYPE_TEXT;

		esp_err_t ret = httpd_ws_send_frame_async(s_ctx.hd, s_ctx.fd, &ws_pkt);
		if (ret != ESP_OK)
		{
			xEventGroupClearBits(s_event_group, WS_CONNECTED_BIT);
			ESP_LOGE(TAG, "httpd_ws_send_frame_async failed: %d", ret);
		}
	}
}

esp_err_t ws_server_register_uri(httpd_handle_t http_server)
{
	static const httpd_uri_t ws_uri = {
		.uri = "/ws",
		.method = HTTP_GET,
		.handler = ws_handler,
		.user_ctx = NULL,
		.is_websocket = true,
	};

	if (http_server == NULL)
	{
		return ESP_ERR_INVALID_ARG;
	}

	return httpd_register_uri_handler(http_server, &ws_uri);
}

void ws_server_start(QueueHandle_t *tx_queue, QueueHandle_t *rx_queue, const ws_server_hooks_t *hooks)
{
	s_tx_queue = tx_queue;
	s_rx_queue = rx_queue;

	memset(&s_hooks, 0, sizeof(s_hooks));
	if (hooks != NULL)
	{
		s_hooks = *hooks;
	}

	if (s_event_group == NULL)
	{
		s_event_group = xEventGroupCreateStatic(&s_event_group_buffer);
	}

	if (s_ws_task != NULL)
	{
		return;
	}

	s_ws_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
	if (s_ws_task_stack == NULL)
	{
		ESP_LOGE(TAG, "Failed to allocate websocket task stack");
		return;
	}
	memset(s_ws_task_stack, 0, 4096);

	s_ws_task = xTaskCreateStatic(
		websocket_task,
		"ws_task",
		4096,
		NULL,
		5,
		s_ws_task_stack,
		&s_ws_task_buffer);

	if (s_ws_task == NULL)
	{
		ESP_LOGE(TAG, "Failed to create websocket task");
		heap_caps_free(s_ws_task_stack);
		s_ws_task_stack = NULL;
		return;
	}
}

void ws_server_stop(void)
{
	// Not currently used; safe placeholder for future cleanup/restart.
}

bool ws_server_is_connected(void)
{
	if (s_event_group == NULL)
	{
		return false;
	}

	EventBits_t bits = xEventGroupGetBits(s_event_group);
	return ((bits & WS_CONNECTED_BIT) != 0);
}
