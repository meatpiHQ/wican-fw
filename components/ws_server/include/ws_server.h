#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*ws_server_on_open_fn)(void *ctx);
typedef bool (*ws_server_handle_frame_fn)(httpd_req_t *req, const uint8_t *data, size_t len, void *ctx);

typedef struct
{
	ws_server_on_open_fn on_open;
	ws_server_handle_frame_fn handle_frame;
	void *ctx;
} ws_server_hooks_t;

esp_err_t ws_server_register_uri(httpd_handle_t http_server);

void ws_server_start(QueueHandle_t *tx_queue, QueueHandle_t *rx_queue, const ws_server_hooks_t *hooks);
void ws_server_stop(void);

bool ws_server_is_connected(void);

#ifdef __cplusplus
}
#endif
