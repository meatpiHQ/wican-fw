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

#include "ha_webhooks.h"
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <cJSON.h>
#include <esp_log.h>
#include <esp_http_server.h>

static const char *TAG = "HA_WEBHOOK_HTTP";

/**
 * @brief Check if a URL is valid HTTP or HTTPS
 *
 * @param[in] url URL string to validate
 * @return true if URL starts with "http://" or "https://", false otherwise
 */
static bool url_is_http(const char *url)
{
    if (!url)
        return false;
    return strncmp(url, "http://", 7) == 0 || strncmp(url, "https://", 8) == 0;
}

/**
 * @brief Send a JSON response to the client
 *
 * Serializes a cJSON object and sends it with appropriate headers and status code.
 * The cJSON object is deleted after sending.
 *
 * @param[in] req HTTP request handle
 * @param[in] obj cJSON object to send (will be deleted by this function)
 * @param[in] status HTTP status code (200, 201, 204, etc.)
 * @return ESP_OK on success, ESP_FAIL on serialization error
 */
static esp_err_t send_json(httpd_req_t *req, cJSON *obj, int status)
{
    char *js = cJSON_PrintUnformatted(obj);
    if (!js)
    {
        ESP_LOGE(TAG, "Failed to serialize JSON response");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "serialize error");
        cJSON_Delete(obj);
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_status(req, status == 201 ? "201 Created" : status == 200 ? "200 OK"
                                                           : status == 204   ? "204 No Content"
                                                                             : "200 OK");
    httpd_resp_send(req, js, strlen(js));

    ESP_LOGD(TAG, "Sent JSON response (status %d, %zu bytes)", status, strlen(js));

    cJSON_Delete(obj);
    free(js);
    return ESP_OK;
}

/**
 * @brief Handle POST requests to create/update webhook configuration
 *
 * Accepts JSON body with "url" (required) and "enabled" (optional) fields.
 * Returns 201 Created on first configuration, 200 OK on updates.
 *
 * @param[in] req HTTP request handle
 * @return ESP_OK on success, error otherwise
 */
static esp_err_t webhook_post_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "POST /api/webhook");

    size_t len = req->content_len;
    ESP_LOGD(TAG, "Request content length: %zu bytes", len);

    if (len == 0 || len > 2048)
    {
        ESP_LOGW(TAG, "Invalid content length: %zu", len);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "invalid content length");
    }

    char *buf = (char *)malloc(len + 1);
    if (!buf)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for request body");
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "no mem");
    }

    size_t total = 0;
    while (total < len)
    {
        int r = httpd_req_recv(req, buf + total, len - total);
        if (r <= 0)
        {
            ESP_LOGE(TAG, "Failed to receive request body");
            free(buf);
            return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "recv error");
        }
        total += (size_t)r;
    }
    buf[total] = '\0';

    ESP_LOGD(TAG, "Received request body: %s", buf);

    cJSON *root = cJSON_Parse(buf);
    free(buf);

    if (!root)
    {
        ESP_LOGW(TAG, "Invalid JSON in request body");
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "invalid json");
    }

    cJSON *url = cJSON_GetObjectItem(root, "url");
    cJSON *enabled = cJSON_GetObjectItem(root, "enabled");
    cJSON *interval = cJSON_GetObjectItem(root, "interval");

    if (!cJSON_IsString(url) || !url_is_http(url->valuestring))
    {
        ESP_LOGW(TAG, "Invalid or missing URL in request");
        cJSON_Delete(root);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "invalid url");
    }

    ESP_LOGI(TAG, "Setting webhook URL: %s", url->valuestring);

    // Load current cached config to determine if changes are needed
    ha_webhook_config_t old_cfg = {0};
    esp_err_t have = ha_webhooks_get_config(&old_cfg);
    bool first_set = (have != ESP_OK || old_cfg.url[0] == '\0');

    // Prepare new configuration, preserving fields not controlled here
    ha_webhook_config_t cfg = old_cfg;
    strlcpy(cfg.url, url->valuestring, sizeof(cfg.url));
    cfg.enabled = cJSON_IsBool(enabled) ? cJSON_IsTrue(enabled) : true;
    if (cJSON_IsNumber(interval))
        cfg.interval = interval->valueint;

    // Check if meaningful fields actually changed
    bool changed = (strcmp(old_cfg.url, cfg.url) != 0) || (old_cfg.enabled != cfg.enabled) || (old_cfg.interval != cfg.interval);

    ESP_LOGI(TAG, "Webhook %s, enabled: %s", first_set ? "created" : (changed ? "updated" : "unchanged"),
             cfg.enabled ? "yes" : "no");

    // Only save + update cache if configuration changed
    esp_err_t s = ESP_OK;
    if (changed || first_set)
    {
        s = ha_webhooks_set_config(&cfg);
    }
    cJSON_Delete(root);

    if (s != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to save webhook configuration");
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "save failed");
    }

    cJSON *resp = cJSON_CreateObject();
    cJSON_AddStringToObject(resp, "url", cfg.url);
    cJSON_AddBoolToObject(resp, "enabled", cfg.enabled);
    cJSON_AddNumberToObject(resp, "interval", cfg.interval);

    return send_json(req, resp, first_set ? 201 : 200);
}

/**
 * @brief Handle GET requests to retrieve webhook configuration
 *
 * Returns the current webhook configuration as JSON, including URL,
 * enabled state, last post timestamp, status, and retry count.
 *
 * @param[in] req HTTP request handle
 * @return ESP_OK on success
 */
static esp_err_t webhook_get_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "GET /api/webhook");

    ha_webhook_config_t cfg = {0};
    esp_err_t r = ha_webhooks_get_config(&cfg);

    cJSON *resp = cJSON_CreateObject();

    if (r == ESP_OK)
    {
        ESP_LOGI(TAG, "Returning webhook config: URL=%s, enabled=%s, status=%s",
                 cfg.url, cfg.enabled ? "yes" : "no",
                 cfg.status[0] ? cfg.status : "unknown");

        cJSON_AddStringToObject(resp, "url", cfg.url);
        cJSON_AddBoolToObject(resp, "enabled", cfg.enabled);
        cJSON_AddStringToObject(resp, "last_post", cfg.last_post[0] ? cfg.last_post : "");
        cJSON_AddStringToObject(resp, "status", cfg.status[0] ? cfg.status : "unknown");
        cJSON_AddNumberToObject(resp, "retries", cfg.retries);
        cJSON_AddNumberToObject(resp, "interval", cfg.interval);
    }
    else
    {
        ESP_LOGI(TAG, "No webhook configuration found, returning defaults");

        cJSON_AddStringToObject(resp, "url", "");
        cJSON_AddBoolToObject(resp, "enabled", false);
        cJSON_AddStringToObject(resp, "last_post", "");
        cJSON_AddStringToObject(resp, "status", "disabled");
        cJSON_AddNumberToObject(resp, "retries", 0);
        cJSON_AddNumberToObject(resp, "interval", 0);
    }

    return send_json(req, resp, 200);
}

/**
 * @brief Handle DELETE requests to remove webhook configuration
 *
 * Clears the webhook configuration by setting URL to empty string,
 * disabling the webhook, and resetting all fields.
 *
 * @param[in] req HTTP request handle
 * @return ESP_OK on success, error otherwise
 */
static esp_err_t webhook_delete_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "DELETE /api/webhook - removing webhook configuration");

    // Load existing cached config and only persist if a change is required
    ha_webhook_config_t old_cfg = {0};
    esp_err_t have = ha_webhooks_get_config(&old_cfg);

    ha_webhook_config_t cfg = old_cfg;
    strlcpy(cfg.url, "", sizeof(cfg.url));
    cfg.enabled = false;
    cfg.last_post[0] = '\0';
    strlcpy(cfg.status, "disabled", sizeof(cfg.status));
    cfg.retries = 0;

    bool changed = (old_cfg.url[0] != '\0') || (old_cfg.enabled != false) ||
                   (old_cfg.last_post[0] != '\0') || (strcmp(old_cfg.status, "disabled") != 0) ||
                   (old_cfg.retries != 0) || (old_cfg.interval != 0) || (have != ESP_OK);

    if (changed)
    {
        esp_err_t s = ha_webhooks_set_config(&cfg);
        if (s != ESP_OK)
        {
            ESP_LOGE(TAG, "Failed to save cleared webhook configuration");
            return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "save failed");
        }
        ESP_LOGI(TAG, "Webhook configuration deleted successfully");
    }
    else
    {
        ESP_LOGI(TAG, "Webhook configuration already cleared; no write performed");
    }

    httpd_resp_set_status(req, "204 No Content");
    httpd_resp_send(req, NULL, 0);
    return ESP_OK;
}

/**
 * @brief Route incoming requests to appropriate handler
 *
 * Routes requests to the correct HTTP method handler (GET, POST, DELETE)
 * for the /api/webhook endpoint.
 *
 * @param[in] req HTTP request handle
 * @return ESP_OK on success, error otherwise
 */
static esp_err_t router(httpd_req_t *req)
{
    const char *uri = req->uri; // expect /api/webhook

    if (strcmp(uri, "/api/webhook") != 0)
    {
        ESP_LOGW(TAG, "Invalid route requested: %s", uri);
        return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "route");
    }

    if (req->method == HTTP_POST)
        return webhook_post_handler(req);
    if (req->method == HTTP_GET)
        return webhook_get_handler(req);
    if (req->method == HTTP_DELETE)
        return webhook_delete_handler(req);

    ESP_LOGW(TAG, "Unsupported HTTP method for /api/webhook");
    return httpd_resp_send_err(req, HTTPD_405_METHOD_NOT_ALLOWED, "method");
}

esp_err_t ha_webhooks_register_handlers(httpd_handle_t server)
{
    ESP_LOGI(TAG, "Registering Home Assistant webhook HTTP handlers");

    static const httpd_uri_t get_u = {.uri = "/api/webhook", .method = HTTP_GET, .handler = router};
    static const httpd_uri_t post_u = {.uri = "/api/webhook", .method = HTTP_POST, .handler = router};
    static const httpd_uri_t del_u = {.uri = "/api/webhook", .method = HTTP_DELETE, .handler = router};

    const httpd_uri_t *arr[] = {&get_u, &post_u, &del_u};
    const char *method_names[] = {"GET", "POST", "DELETE"};

    for (size_t i = 0; i < 3; ++i)
    {
        esp_err_t r = httpd_register_uri_handler(server, arr[i]);
        if (r != ESP_OK && r != ESP_ERR_HTTPD_HANDLER_EXISTS)
        {
            ESP_LOGE(TAG, "Failed to register %s handler: %s", method_names[i], esp_err_to_name(r));
            return r;
        }
        ESP_LOGD(TAG, "Registered %s /api/webhook handler", method_names[i]);
    }

    ESP_LOGI(TAG, "HA webhook handlers registered successfully at /api/webhook");
    return ESP_OK;
}
