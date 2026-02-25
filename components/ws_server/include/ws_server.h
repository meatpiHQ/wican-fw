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
typedef void (*ws_server_on_close_fn)(void *ctx);
typedef bool (*ws_server_handle_frame_fn)(httpd_req_t *req, const uint8_t *data, size_t len, void *ctx);

typedef struct
{
	ws_server_on_open_fn on_open;
	ws_server_on_close_fn on_close;
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
