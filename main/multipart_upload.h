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

#ifdef __cplusplus
extern "C" {
#endif

// Information about the current multipart part (form field)
typedef struct multipart_part_info_t {
    char name[64];         // form field name (e.g. "firmware")
    char filename[128];    // original filename if provided
    char content_type[64]; // part content-type if provided
} multipart_part_info_t;

// Callback set for handling parts and data
typedef struct multipart_upload_handlers_t {
    // Called once headers for a part are parsed.
    // Return true to accept and receive data for this part; false to skip its data.
    bool (*on_part_begin)(const multipart_part_info_t *info, void *user_ctx);

    // Called for each chunk of data in an accepted part.
    // Should return ESP_OK on success; any error aborts the parsing.
    esp_err_t (*on_part_data)(const char *data, size_t len, void *user_ctx);

    // Called when the current accepted part ends (after the last data chunk).
    void (*on_part_end)(void *user_ctx);

    // Called once when the entire body has been processed successfully.
    void (*on_finished)(void *user_ctx);
} multipart_upload_handlers_t;

// Optional configuration parameters
typedef struct multipart_upload_config_t {
    size_t rx_buf_size;    // HTTP receive buffer size
} multipart_upload_config_t;

// Default buffer size (can be overridden via config)
#ifndef MULTIPART_UPLOAD_DEFAULT_RX
#define MULTIPART_UPLOAD_DEFAULT_RX 1024
#endif

// Helper to get a default configuration
static inline multipart_upload_config_t multipart_upload_default_config(void) {
    multipart_upload_config_t cfg = {
        .rx_buf_size = MULTIPART_UPLOAD_DEFAULT_RX,
    };
    return cfg;
}

// Process a multipart/form-data HTTP POST request body, invoking the provided handlers.
// - req: the httpd request
// - handlers: callbacks invoked during parsing
// - user_ctx: user state passed back to callbacks
// - cfg: optional parameters (pass NULL to use defaults)
esp_err_t multipart_upload_handle(httpd_req_t *req,
                                  const multipart_upload_handlers_t *handlers,
                                  void *user_ctx,
                                  const multipart_upload_config_t *cfg);

#ifdef __cplusplus
}
#endif
