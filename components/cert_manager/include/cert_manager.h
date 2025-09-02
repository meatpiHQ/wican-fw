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

#include "esp_err.h"
#include "esp_http_server.h"
#include <stddef.h>

#ifdef __cplusplus
extern "C"
{
#endif

// Maximum number of stored named certificate sets
#define CERT_MANAGER_MAX_SETS 10

// Initialization for certificate manager (ensures directories etc.)
esp_err_t cert_manager_init(void);

// Register certificate manager endpoints (/cert_manager/sets*)
esp_err_t cert_manager_register_handlers(httpd_handle_t server);

// Preload (warm) one or more certificate sets into PSRAM cache so later calls avoid filesystem access.
// Pass NULL or count == 0 to preload all currently known sets. Returns ESP_OK if calls were attempted
// (individual missing files are logged but not fatal). Intended to be called early at boot.
esp_err_t cert_manager_preload_sets(const char *const *set_names, size_t count);

// Cached (preloaded) accessors: return pointer & length to in-memory copy (PSRAM if available).
// Pointer remains valid until the set is deleted or re-uploaded. Do NOT free the returned pointer.
// Returns NULL if set or component not available.
const char *cert_manager_get_set_ca_ptr(const char *set_name, size_t *len_out);
const char *cert_manager_get_set_client_cert_ptr(const char *set_name, size_t *len_out);
const char *cert_manager_get_set_client_key_ptr(const char *set_name, size_t *len_out);

// Loading functions: allocate and return certificate data (caller must free)
esp_err_t cert_manager_load_set_ca(const char *set_name, char **buf_out, size_t *len_out);
esp_err_t cert_manager_load_set_client_cert(const char *set_name, char **buf_out, size_t *len_out);
esp_err_t cert_manager_load_set_client_key(const char *set_name, char **buf_out, size_t *len_out);

// Log (info level) the cached status & sizes of all certificate components for a set.
// Uses the cached pointer accessors; does NOT read filesystem.
esp_err_t cert_manager_log_set(const char *set_name);

#ifdef __cplusplus
}
#endif
