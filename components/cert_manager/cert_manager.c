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

// Certificate Manager - Main Coordinator Module
// This module provides the public API and coordinates between core and HTTP modules

#include "cert_manager.h"
#include "cert_manager_core.h"
#include "cert_manager_http.h"
#include "esp_log.h"

#define TAG "cert_manager"

// Public API Implementation - delegates to appropriate modules

esp_err_t cert_manager_init(void)
{
    ESP_LOGI(TAG, "Initializing Certificate Manager");
    return cert_manager_core_init();
}

esp_err_t cert_manager_register_handlers(httpd_handle_t server)
{
    ESP_LOGI(TAG, "Registering HTTP handlers");
    return cert_manager_http_register_handlers(server);
}

esp_err_t cert_manager_preload_sets(const char *const *set_names, size_t count)
{
    return cert_manager_core_preload_sets(set_names, count);
}

const char *cert_manager_get_set_ca_ptr(const char *set_name, size_t *len_out)
{
    return cert_manager_core_get_cached_ptr(set_name, len_out, "ca");
}

const char *cert_manager_get_set_client_cert_ptr(const char *set_name, size_t *len_out)
{
    return cert_manager_core_get_cached_ptr(set_name, len_out, "ce");
}

const char *cert_manager_get_set_client_key_ptr(const char *set_name, size_t *len_out)
{
    return cert_manager_core_get_cached_ptr(set_name, len_out, "ck");
}

esp_err_t cert_manager_load_set_ca(const char *set_name, char **buf_out, size_t *len_out)
{
    return cert_manager_core_load_set_component(set_name, "ca.pem", buf_out, len_out);
}

esp_err_t cert_manager_load_set_client_cert(const char *set_name, char **buf_out, size_t *len_out)
{
    return cert_manager_core_load_set_component(set_name, "client.crt", buf_out, len_out);
}

esp_err_t cert_manager_load_set_client_key(const char *set_name, char **buf_out, size_t *len_out)
{
    return cert_manager_core_load_set_component(set_name, "client.key", buf_out, len_out);
}

esp_err_t cert_manager_log_set(const char *set_name)
{
    return cert_manager_core_log_set(set_name);
}