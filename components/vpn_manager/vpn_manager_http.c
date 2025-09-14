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

#include "vpn_manager_http.h"
#include "vpn_manager.h"
#include <string.h>
#include <stdint.h>
#include <stdlib.h>
#include <esp_log.h>
#include <esp_http_server.h>
#include <cJSON.h>

static const char *TAG = "VPN_HTTP";
// UI placeholder string used in read-only private key field
static const char *WG_PRIV_PLACEHOLDER = "Generated and stored on device";

// Simple in-place trim for C-strings
static void trim_str(char *s)
{
    if (!s) return;
    char *start = s;
    while (*start==' '||*start=='\t'||*start=='\r'||*start=='\n') start++;
    if (start != s) memmove(s, start, strlen(start)+1);
    size_t len = strlen(s);
    while (len>0)
    {
        char c = s[len-1];
        if (c==' '||c=='\t'||c=='\r'||c=='\n') { s[--len]='\0'; }
        else break;
    }
}

// Heuristic check for a WireGuard key (base64 of 32 bytes -> typically 44 chars)
static bool looks_like_wg_b64_key(const char *s)
{
    if (!s) return false;
    size_t n = strlen(s);
    if (n < 40 || n > 64) return false; // accept a reasonable range
    for (size_t i = 0; i < n; ++i)
    {
        char c = s[i];
        bool ok = (c >= 'A' && c <= 'Z') ||
                  (c >= 'a' && c <= 'z') ||
                  (c >= '0' && c <= '9') ||
                  (c == '+') || (c == '/') || (c == '=');
        if (!ok) return false;
    }
    return true;
}

// HTTP handler function declarations
static esp_err_t vpn_generate_keys_handler(httpd_req_t *req);
static esp_err_t vpn_load_config_handler(httpd_req_t *req);
static esp_err_t vpn_store_config_handler(httpd_req_t *req);
static esp_err_t vpn_test_connection_handler(httpd_req_t *req);
static esp_err_t vpn_status_handler(httpd_req_t *req);

// Unified router handler
static esp_err_t vpn_router_handler(httpd_req_t *req)
{
    const char *uri = req->uri; // prefix /vpn
    const char *p = uri + strlen("/vpn");
    if (*p == '/')
        p++;
    if (*p == '\0')
        return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "missing segment");

    // Route to appropriate handler based on path and method
    if (strcmp(p, "generate_keys") == 0)
    {
        if (req->method == HTTP_POST)
            return vpn_generate_keys_handler(req);
    }
    else if (strcmp(p, "load_config") == 0)
    {
        if (req->method == HTTP_GET)
            return vpn_load_config_handler(req);
    }
    else if (strcmp(p, "store_config") == 0)
    {
        if (req->method == HTTP_POST)
            return vpn_store_config_handler(req);
    }
    else if (strcmp(p, "test_connection") == 0)
    {
        if (req->method == HTTP_POST)
            return vpn_test_connection_handler(req);
    }
    else if (strcmp(p, "status") == 0)
    {
        if (req->method == HTTP_GET)
            return vpn_status_handler(req);
    }

    return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "route");
}

esp_err_t vpn_manager_register_handlers(httpd_handle_t server)
{
    static const httpd_uri_t get_u  = { .uri="/vpn*", .method=HTTP_GET,  .handler=vpn_router_handler };
    static const httpd_uri_t post_u = { .uri="/vpn*", .method=HTTP_POST, .handler=vpn_router_handler };
    const httpd_uri_t *arr[] = { &get_u, &post_u };

    for (size_t i = 0; i < 2; i++)
    {
        esp_err_t r = httpd_register_uri_handler(server, arr[i]);
        if (r != ESP_OK && r != ESP_ERR_HTTPD_HANDLER_EXISTS)
            return r;
    }

    ESP_LOGI(TAG, "VPN handlers registered at /vpn*");
    return ESP_OK;
}

static esp_err_t vpn_generate_keys_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "VPN generate keys request");

    char public_key[64] = {0};
    esp_err_t ret = vpn_manager_generate_wireguard_keys(public_key, sizeof(public_key));

    cJSON *response = cJSON_CreateObject();
    if (response == NULL)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to create response");
        return ESP_FAIL;
    }

    if (ret == ESP_OK)
    {
        cJSON_AddBoolToObject(response, "success", true);
        cJSON_AddStringToObject(response, "public_key", public_key);
        ESP_LOGI(TAG, "WireGuard keys generated successfully");
    }
    else
    {
        cJSON_AddBoolToObject(response, "success", false);
        cJSON_AddStringToObject(response, "error", esp_err_to_name(ret));
        ESP_LOGE(TAG, "Failed to generate WireGuard keys: %s", esp_err_to_name(ret));
    }

    char *json_string = cJSON_PrintUnformatted(response);
    cJSON_Delete(response);

    if (json_string == NULL)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to serialize response");
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, json_string, strlen(json_string));
    free(json_string);

    return ESP_OK;
}

static esp_err_t vpn_load_config_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "VPN load config request");

    vpn_config_t config = {0};
    esp_err_t ret = vpn_manager_load_config(&config);

    cJSON *response = cJSON_CreateObject();
    if (response == NULL)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to create response");
        return ESP_FAIL;
    }

    if (ret == ESP_OK)
    {
        cJSON_AddBoolToObject(response, "success", true);
        cJSON_AddNumberToObject(response, "vpn_type", config.type);
        cJSON_AddBoolToObject(response, "enabled", config.enabled);

        if (config.type == VPN_TYPE_WIREGUARD)
        {
            cJSON *wg = cJSON_CreateObject();
            if (wg != NULL)
            {
                // Don't send private key for security
                // Expose only the peer/server public key as peer_public_key
                cJSON_AddStringToObject(wg, "peer_public_key", config.config.wireguard.public_key);
                // UI expects CIDR here; if stored address lacks suffix, show /32 by default
                char address_cidr[64];
                if (config.config.wireguard.address[0] != '\0' && strchr(config.config.wireguard.address, '/') == NULL)
                {
                    snprintf(address_cidr, sizeof(address_cidr), "%s/32", config.config.wireguard.address);
                }
                else
                {
                    strlcpy(address_cidr, config.config.wireguard.address, sizeof(address_cidr));
                }
                cJSON_AddStringToObject(wg, "address", address_cidr);
                // Combine allowed_ip and mask into CIDR format for response (generic mapping)
                char allowed_ips_cidr[64];
                int a=0,b=0,c=0,d=0; unsigned int prefix = 32;
                if (sscanf(config.config.wireguard.allowed_ip_mask, "%d.%d.%d.%d", &a,&b,&c,&d) == 4)
                {
                    uint32_t mask = ((uint32_t)(a & 0xFF) << 24) | ((uint32_t)(b & 0xFF) << 16) |
                                    ((uint32_t)(c & 0xFF) << 8)  | ((uint32_t)(d & 0xFF));
                    if (mask == 0)
                    {
                        prefix = 0;
                    }
                    else
                    {
                        unsigned int count = 0; uint32_t bit = 0x80000000u;
                        while (bit && (mask & bit)) { count++; bit >>= 1; }
                        if ((mask << count) == 0) prefix = count; else prefix = 32;
                    }
                }
                snprintf(allowed_ips_cidr, sizeof(allowed_ips_cidr), "%s/%u", config.config.wireguard.allowed_ip, prefix);
                cJSON_AddStringToObject(wg, "allowed_ips", allowed_ips_cidr);

                // Combine endpoint and port for response
                char endpoint_with_port[96];
                snprintf(endpoint_with_port, sizeof(endpoint_with_port), "%s:%d",
                         config.config.wireguard.endpoint, config.config.wireguard.port);
                cJSON_AddStringToObject(wg, "endpoint", endpoint_with_port);
                cJSON_AddNumberToObject(wg, "persistent_keepalive", config.config.wireguard.persistent_keepalive);
                cJSON_AddItemToObject(response, "wireguard", wg);
            }
        }

        // Add current status
        vpn_status_t status = vpn_manager_get_status();
        const char *status_str = "unknown";
        switch (status)
        {
            case VPN_STATUS_DISABLED: status_str = "disabled"; break;
            case VPN_STATUS_DISCONNECTED: status_str = "disconnected"; break;
            case VPN_STATUS_CONNECTING: status_str = "connecting"; break;
            case VPN_STATUS_CONNECTED: status_str = "connected"; break;
            case VPN_STATUS_ERROR: status_str = "error"; break;
        }
        cJSON_AddStringToObject(response, "status", status_str);

        // Add IP address if connected
        if (status == VPN_STATUS_CONNECTED)
        {
            char ip_str[32] = {0};
            if (vpn_manager_get_ip_address(ip_str, sizeof(ip_str)) == ESP_OK)
            {
                cJSON_AddStringToObject(response, "ip_address", ip_str);
            }
        }
    }
    else
    {
        cJSON_AddBoolToObject(response, "success", false);
        cJSON_AddStringToObject(response, "error", esp_err_to_name(ret));

        // Return default values if no config found
        if (ret == ESP_ERR_NOT_FOUND)
        {
            cJSON_AddNumberToObject(response, "vpn_type", VPN_TYPE_DISABLED);
            cJSON_AddBoolToObject(response, "enabled", false);
            cJSON_AddStringToObject(response, "status", "disabled");
        }
    }

    char *json_string = cJSON_PrintUnformatted(response);
    cJSON_Delete(response);

    if (json_string == NULL)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to serialize response");
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, json_string, strlen(json_string));
    free(json_string);

    return ESP_OK;
}

static esp_err_t vpn_store_config_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "=== VPN STORE CONFIG REQUEST START ===");
    ESP_LOGI(TAG, "Content length: %d bytes", req->content_len);

    char *buf = NULL;
    size_t buf_size = req->content_len;
    esp_err_t ret_val = ESP_OK;
    bool response_sent = false;

    if (buf_size <= 0 || buf_size > 8192)
    {
        ESP_LOGE(TAG, "Invalid content length: %d", (int)buf_size);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid content length");
        return ESP_FAIL;
    }

    buf = heap_caps_malloc(buf_size + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!buf)
    {
        ESP_LOGE(TAG, "Failed to allocate memory");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Memory allocation failed");
        return ESP_ERR_NO_MEM;
    }
    memset(buf, 0, buf_size + 1);

    // Read request body fully
    size_t total = 0;
    while (total < buf_size)
    {
        int r = httpd_req_recv(req, buf + total, buf_size - total);
        if (r <= 0)
        {
            ESP_LOGE(TAG, "Failed to receive data: %d (received: %u)", r, (unsigned)total);
            ret_val = ESP_FAIL;
            goto cleanup;
        }
        total += (size_t)r;
    }
    buf[total] = '\0';
    ESP_LOGI(TAG, "Received JSON payload (%u bytes): %.*s", (unsigned)total, (int)total, buf);

    cJSON *json = cJSON_Parse(buf);
    if (!json)
    {
        ESP_LOGE(TAG, "Invalid JSON format");
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid JSON format");
        response_sent = true;
        ret_val = ESP_FAIL;
        goto cleanup;
    }

    vpn_config_t config = (vpn_config_t){0};
    // Load existing config so we can preserve device private key if UI sends placeholder
    vpn_config_t existing = {0};
    esp_err_t have_existing = vpn_manager_load_config(&existing);

    cJSON *vpn_enabled = cJSON_GetObjectItem(json, "vpn_enabled");
    ESP_LOGI(TAG, "vpn_enabled field: %s", vpn_enabled && cJSON_IsString(vpn_enabled) ? vpn_enabled->valuestring : "(not present)");
    if (cJSON_IsString(vpn_enabled) && strcmp(vpn_enabled->valuestring, "wireguard") == 0)
    {
        config.type = VPN_TYPE_WIREGUARD;
        config.enabled = true;
        ESP_LOGI(TAG, "VPN type set to WIREGUARD, enabled");
    }
    else
    {
        config.type = VPN_TYPE_DISABLED;
        config.enabled = false;
        ESP_LOGI(TAG, "VPN type set to DISABLED");
    }

    if (config.type == VPN_TYPE_WIREGUARD)
    {
        ESP_LOGI(TAG, "Parsing WireGuard configuration fields...");

        cJSON *item = NULL;

        // Device private key
        item = cJSON_GetObjectItem(json, "wg_private_key");
        if (!cJSON_IsString(item)) item = cJSON_GetObjectItem(json, "private_key");
        ESP_LOGI(TAG, "WireGuard private key: %s", item && cJSON_IsString(item) ? item->valuestring : "(not present)");
        if (cJSON_IsString(item) && item->valuestring[0] != '\0')
        {
            char tmp[80]; strlcpy(tmp, item->valuestring, sizeof(tmp)); trim_str(tmp);
            if (strcmp(tmp, WG_PRIV_PLACEHOLDER) == 0)
            {
                ESP_LOGI(TAG, "Private key placeholder received; preserving existing key if available");
            }
            else if (looks_like_wg_b64_key(tmp))
            {
                strlcpy(config.config.wireguard.private_key, tmp, sizeof(config.config.wireguard.private_key));
                ESP_LOGI(TAG, "Accepted new private key (base64)");
            }
            else
            {
                ESP_LOGW(TAG, "Private key string doesn't look like a WireGuard key; ignoring");
            }
        }
        // If not provided or ignored, preserve existing stored key
        if (config.config.wireguard.private_key[0] == '\0' && have_existing == ESP_OK)
        {
            strlcpy(config.config.wireguard.private_key, existing.config.wireguard.private_key,
                    sizeof(config.config.wireguard.private_key));
        }

        // Peer/server public key (canonical: peer_public_key)
        item = cJSON_GetObjectItem(json, "peer_public_key");
        ESP_LOGI(TAG, "WireGuard peer_public_key: %s", item && cJSON_IsString(item) ? item->valuestring : "(not present)");
        if (cJSON_IsString(item))
        {
            char tmp[80]; strlcpy(tmp, item->valuestring, sizeof(tmp)); trim_str(tmp);
            if (looks_like_wg_b64_key(tmp))
            {
                strlcpy(config.config.wireguard.public_key, tmp, sizeof(config.config.wireguard.public_key));
            }
            else
            {
                ESP_LOGW(TAG, "Peer public key doesn't look like base64; keeping as-is");
                strlcpy(config.config.wireguard.public_key, tmp, sizeof(config.config.wireguard.public_key));
            }
        }

        // Interface address: store canonical IP without CIDR suffix
        item = cJSON_GetObjectItem(json, "wg_address");
        if (!cJSON_IsString(item)) item = cJSON_GetObjectItem(json, "address");
        ESP_LOGI(TAG, "WireGuard address: %s", item && cJSON_IsString(item) ? item->valuestring : "(not present)");
        if (cJSON_IsString(item))
        {
            char addr[64] = {0};
            strlcpy(addr, item->valuestring, sizeof(addr)); trim_str(addr);
            char *slash = strchr(addr, '/');
            if (slash)
            {
                *slash = '\0';
                ESP_LOGI(TAG, "Stripped CIDR from address, base IP: %s", addr);
            }
            strlcpy(config.config.wireguard.address, addr, sizeof(config.config.wireguard.address));
        }

        // Allowed IPs (CIDR -> ip + mask)
        item = cJSON_GetObjectItem(json, "wg_allowed_ips");
        if (!cJSON_IsString(item)) item = cJSON_GetObjectItem(json, "allowed_ips");
        ESP_LOGI(TAG, "WireGuard allowed_ips: %s", item && cJSON_IsString(item) ? item->valuestring : "(not present)");
        if (cJSON_IsString(item))
        {
            char cidr[64] = {0};
            strlcpy(cidr, item->valuestring, sizeof(cidr)); trim_str(cidr);
            char *slash = strchr(cidr, '/');
            if (slash)
            {
                *slash = '\0';
                strlcpy(config.config.wireguard.allowed_ip, cidr, sizeof(config.config.wireguard.allowed_ip));
                int prefix = atoi(slash + 1);
                ESP_LOGI(TAG, "Allowed IP: %s, CIDR prefix: %d", cidr, prefix);
                if (prefix < 0)
                {
                    prefix = 0;
                }
                if (prefix > 32)
                {
                    prefix = 32;
                }
                uint32_t mask = (prefix == 0) ? 0u : (0xFFFFFFFFu << (32 - prefix));
                char mask_str[16];
                snprintf(mask_str, sizeof(mask_str), "%u.%u.%u.%u",
                         (unsigned)((mask >> 24) & 0xFF),
                         (unsigned)((mask >> 16) & 0xFF),
                         (unsigned)((mask >> 8) & 0xFF),
                         (unsigned)(mask & 0xFF));
                strlcpy(config.config.wireguard.allowed_ip_mask, mask_str, sizeof(config.config.wireguard.allowed_ip_mask));
                ESP_LOGI(TAG, "Allowed IP mask: %s", mask_str);
            }
            else
            {
                // No prefix; default to /32
                strlcpy(config.config.wireguard.allowed_ip, cidr, sizeof(config.config.wireguard.allowed_ip));
                strlcpy(config.config.wireguard.allowed_ip_mask, "255.255.255.255", sizeof(config.config.wireguard.allowed_ip_mask));
                ESP_LOGI(TAG, "Allowed IP: %s, default mask: 255.255.255.255", cidr);
            }
        }

        // Endpoint host:port
        item = cJSON_GetObjectItem(json, "wg_endpoint");
        if (!cJSON_IsString(item)) item = cJSON_GetObjectItem(json, "endpoint");
        ESP_LOGI(TAG, "WireGuard endpoint: %s", item && cJSON_IsString(item) ? item->valuestring : "(not present)");
        if (cJSON_IsString(item))
        {
            char ep[128] = {0};
            strlcpy(ep, item->valuestring, sizeof(ep)); trim_str(ep);
            char *colon = strrchr(ep, ':');
            if (colon)
            {
                *colon = '\0';
                strlcpy(config.config.wireguard.endpoint, ep, sizeof(config.config.wireguard.endpoint));
                config.config.wireguard.port = atoi(colon + 1);
                ESP_LOGI(TAG, "Endpoint host: %s, port: %d", ep, config.config.wireguard.port);
            }
            else
            {
                strlcpy(config.config.wireguard.endpoint, ep, sizeof(config.config.wireguard.endpoint));
                config.config.wireguard.port = 51820;
                ESP_LOGI(TAG, "Endpoint host: %s, port not specified -> default 51820", ep);
            }
        }

        // Keepalive
        item = cJSON_GetObjectItem(json, "wg_persistent_keepalive");
        if (!cJSON_IsNumber(item)) item = cJSON_GetObjectItem(json, "persistent_keepalive");
        ESP_LOGI(TAG, "WireGuard persistent_keepalive: %d", item && cJSON_IsNumber(item) ? item->valueint : -1);
        if (cJSON_IsNumber(item))
        {
            config.config.wireguard.persistent_keepalive = item->valueint;
        }
    }

    cJSON_Delete(json);

    // Basic validation when attempting to enable
    if (config.enabled && config.type == VPN_TYPE_WIREGUARD)
    {
        bool valid = true;
        if (config.config.wireguard.private_key[0] == '\0' ||
            config.config.wireguard.public_key[0] == '\0' ||
            config.config.wireguard.address[0] == '\0' ||
            config.config.wireguard.allowed_ip[0] == '\0' ||
            config.config.wireguard.allowed_ip_mask[0] == '\0' ||
            config.config.wireguard.endpoint[0] == '\0' ||
            config.config.wireguard.port <= 0)
        {
            valid = false;
        }
        ESP_LOGI(TAG, "WireGuard config validation: %s", valid ? "valid" : "invalid");
        if (!valid)
        {
            httpd_resp_set_type(req, "application/json");
            cJSON *resp = cJSON_CreateObject();
            cJSON_AddBoolToObject(resp, "success", false);
            cJSON_AddStringToObject(resp, "error", "Invalid WireGuard configuration");
            char *js = cJSON_PrintUnformatted(resp);
            cJSON_Delete(resp);
            if (js) { httpd_resp_send(req, js, strlen(js)); free(js); }
            response_sent = true;
            ret_val = ESP_ERR_INVALID_ARG;
            goto cleanup;
        }
    }

    // Save configuration
    ESP_LOGI(TAG, "Calling vpn_manager_save_config...");
    esp_err_t save_ret = vpn_manager_save_config(&config);
    ESP_LOGI(TAG, "vpn_manager_save_config returned: %s", esp_err_to_name(save_ret));
    if (save_ret != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to save VPN config: %s", esp_err_to_name(save_ret));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to save configuration");
        response_sent = true;
        ret_val = ESP_FAIL;
        goto cleanup;
    }

    // Do not control runtime from server. Post a reload so VPN task reconciles state.
    vpn_manager_request_reload();
    bool started = false; // runtime control is handled by application logic

    // Respond JSON
    httpd_resp_set_type(req, "application/json");
    {
        cJSON *resp = cJSON_CreateObject();
        cJSON_AddBoolToObject(resp, "success", true);
        cJSON_AddBoolToObject(resp, "started", started);
        char *js = cJSON_PrintUnformatted(resp);
        cJSON_Delete(resp);
        if (js) { httpd_resp_send(req, js, strlen(js)); free(js); }
        response_sent = true;
        ESP_LOGI(TAG, "Response sent: success=%d, started=%d", 1, started ? 1 : 0);
    }

cleanup:
    if (buf) free(buf);
    if (ret_val != ESP_OK && !response_sent)
    {
        ESP_LOGE(TAG, "Failed to process request, sending error response");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to process request");
    }
    ESP_LOGI(TAG, "=== VPN STORE CONFIG REQUEST END ===");
    return ret_val;
}

static esp_err_t vpn_test_connection_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "VPN test connection request (queued to VPN task)");
    vpn_manager_request_test();

    cJSON *response = cJSON_CreateObject();
    if (response == NULL)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to create response");
        return ESP_FAIL;
    }

    // Asynchronous: acknowledge request
    cJSON_AddBoolToObject(response, "success", true);
    cJSON_AddStringToObject(response, "message", "VPN test scheduled");

    char *json_string = cJSON_PrintUnformatted(response);
    cJSON_Delete(response);

    if (json_string == NULL)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to serialize response");
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, json_string, strlen(json_string));
    free(json_string);

    return ESP_OK;
}

static esp_err_t vpn_status_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "VPN status request");

    cJSON *response = cJSON_CreateObject();
    if (response == NULL)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to create response");
        return ESP_FAIL;
    }

    vpn_status_t status = vpn_manager_get_status();
    const char *status_str = "unknown";

    switch (status)
    {
        case VPN_STATUS_DISABLED: status_str = "disabled"; break;
        case VPN_STATUS_DISCONNECTED: status_str = "disconnected"; break;
        case VPN_STATUS_CONNECTING: status_str = "connecting"; break;
        case VPN_STATUS_CONNECTED: status_str = "connected"; break;
        case VPN_STATUS_ERROR: status_str = "error"; break;
    }

    cJSON_AddStringToObject(response, "status", status_str);
    cJSON_AddNumberToObject(response, "status_code", status);

    // Add IP address if connected
    if (status == VPN_STATUS_CONNECTED)
    {
        char ip_str[32] = {0};
        if (vpn_manager_get_ip_address(ip_str, sizeof(ip_str)) == ESP_OK)
        {
            cJSON_AddStringToObject(response, "ip_address", ip_str);
        }
    }

    char *json_string = cJSON_PrintUnformatted(response);
    cJSON_Delete(response);

    if (json_string == NULL)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to serialize response");
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, json_string, strlen(json_string));
    free(json_string);

    return ESP_OK;
}
