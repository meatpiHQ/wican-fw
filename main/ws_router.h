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
#include <stdint.h>
#include <esp_http_server.h>

#ifdef __cplusplus
extern "C" {
#endif

void ws_router_on_open(void);
void ws_router_on_close(void);
bool ws_router_handle_frame(httpd_req_t *req, const uint8_t *payload, size_t len);

// True when the single WS connection is being used for CAN monitor streaming.
// When false (terminal mode), firmware should avoid pushing raw CAN frames to the WS.
bool ws_router_is_in_monitor_mode(void);

#ifdef __cplusplus
}
#endif
