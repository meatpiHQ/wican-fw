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

#ifndef VPN_MANAGER_HTTP_H
#define VPN_MANAGER_HTTP_H

#include <esp_http_server.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Register HTTP handlers for VPN management
 * 
 * @param server HTTP server handle
 * @return esp_err_t ESP_OK on success
 */
esp_err_t vpn_manager_register_handlers(httpd_handle_t server);
#ifdef __cplusplus
}
#endif

#endif // VPN_MANAGER_HTTP_H

