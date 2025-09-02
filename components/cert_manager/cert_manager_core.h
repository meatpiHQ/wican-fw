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

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"
#include "filesystem.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CERT_MANAGER_MAX_SETS 10
#define CERT_MANAGER_DIR FS_MOUNT_POINT"/certs"

typedef struct
{
    char name[25];
    bool has_ca;
    bool has_client_cert;
    bool has_client_key;
    // Cached contents (allocated in PSRAM if possible)
    char *ca_data;
    size_t ca_len;
    char *client_cert_data;
    size_t client_cert_len;
    char *client_key_data;
    size_t client_key_len;
} cert_set_entry_t;

// Core functionality
esp_err_t cert_manager_core_init(void);
bool cert_manager_core_valid_set_name(const char *name);
void cert_manager_core_build_set_file_path(const char *set, const char *fname, char *out, size_t out_sz);
esp_err_t cert_manager_core_ensure_base_dir(void);

// Set management
void cert_manager_core_scan_sets_unlocked(void);
cert_set_entry_t *cert_manager_core_find_set_unlocked(const char *name);
const cert_set_entry_t *cert_manager_core_find_set_locked(const char *name);
esp_err_t cert_manager_core_delete_set(const char *name);

// File operations
esp_err_t cert_manager_core_load_file_alloc_simple(const char *path, char **buf_out, size_t *len_out);
esp_err_t cert_manager_core_load_set_component(const char *set, const char *fname, char **buf_out, size_t *len_out);

// Cache operations
const char *cert_manager_core_get_cached_ptr(const char *set_name, size_t *len_out, char *which);
esp_err_t cert_manager_core_preload_sets(const char *const *set_names, size_t count);

// Reporting
esp_err_t cert_manager_core_log_report(void);
esp_err_t cert_manager_core_log_set(const char *set_name);

// Mutex operations
void cert_manager_core_lock(void);
void cert_manager_core_unlock(void);

// Getters
size_t cert_manager_core_get_set_count(void);
const cert_set_entry_t* cert_manager_core_get_sets(void);

#ifdef __cplusplus
}
#endif