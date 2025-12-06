 /* This file is part of the WiCAN project.
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
#include <stdint.h>
#include <esp_err.h>
#include <esp_http_server.h>

#ifdef __cplusplus
extern "C"
{
#endif

/**
 * @brief Home Assistant webhook configuration structure
 *
 * Stores the webhook URL, state, and status information
 */
typedef struct ha_webhook_config
{
    char url[192];       /**< Webhook URL (HTTP/HTTPS) */
    bool enabled;        /**< Whether webhook is enabled */
    int interval;        /**< Poll/post interval in seconds (or minutes as defined by usage) */
    char last_post[32];  /**< Timestamp of last successful POST */
    char status[16];     /**< Current webhook status (e.g., "ok", "failed", "disabled") */
    int retries;         /**< Number of retry attempts */
} ha_webhook_config_t;

/**
 * @brief Load webhook configuration from filesystem
 *
 * Reads the webhook configuration from /ha_webhook.json file and populates
 * the provided configuration structure.
 *
 * @param[out] cfg Pointer to configuration structure to populate
 * @return
 *     - ESP_OK on success
 *     - ESP_ERR_INVALID_ARG if cfg is NULL or JSON is invalid
 *     - ESP_ERR_NOT_FOUND if config file doesn't exist
 *     - ESP_ERR_NO_MEM if memory allocation fails
 *     - ESP_ERR_INVALID_SIZE if file is empty
 *     - ESP_FAIL on read error
 */
esp_err_t ha_webhook_load_config(ha_webhook_config_t *cfg);

/**
 * @brief Save webhook configuration to filesystem
 *
 * Writes the webhook configuration to /ha_webhook.json file in JSON format.
 *
 * @param[in] cfg Pointer to configuration structure to save
 * @return
 *     - ESP_OK on success
 *     - ESP_ERR_INVALID_ARG if cfg is NULL
 *     - ESP_ERR_NO_MEM if memory allocation fails
 *     - ESP_ERR_NOT_FOUND if file cannot be opened
 *     - ESP_FAIL on write error
 */
esp_err_t ha_webhook_save_config(const ha_webhook_config_t *cfg);

/**
 * @brief Register HTTP handlers for webhook endpoints
 *
 * Registers GET, POST, and DELETE handlers for the /api/webhook endpoint.
 * These handlers allow configuration and management of Home Assistant webhooks.
 *
 * @param[in] server HTTP server handle
 * @return
 *     - ESP_OK on success
 *     - ESP_ERR_HTTPD_HANDLER_EXISTS if handlers already registered (non-fatal)
 *     - Error code from httpd_register_uri_handler on failure
 */
esp_err_t ha_webhooks_register_handlers(httpd_handle_t server);

/**
 * @brief Initialize PSRAM-backed webhook config cache
 *
 * Loads configuration from filesystem and stores a copy in PSRAM.
 * Creates internal mutex for thread-safe access. Call once at startup.
 *
 * @return ESP_OK on success, error from load if filesystem read fails
 */
esp_err_t ha_webhooks_init(void);

/**
 * @brief Get the current webhook configuration from PSRAM cache
 *
 * Copies the cached configuration into the provided destination.
 * Thread-safe.
 *
 * @param[out] out Destination pointer to receive a copy of the config
 * @return ESP_OK on success, ESP_ERR_INVALID_ARG if out is NULL
 */
esp_err_t ha_webhooks_get_config(ha_webhook_config_t *out);

/**
 * @brief Update configuration: persist to file and refresh PSRAM cache
 *
 * Writes the given configuration to the filesystem, then updates the
 * PSRAM-backed cache so all tasks can safely read the latest config.
 * Thread-safe.
 *
 * @param[in] cfg New configuration to persist and cache
 * @return ESP_OK on success, error from save operation otherwise
 */
esp_err_t ha_webhooks_set_config(const ha_webhook_config_t *cfg);

#ifdef __cplusplus
}
#endif
