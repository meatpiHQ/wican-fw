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

#include <stdio.h>
#include <string.h>
#include <strings.h>
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "wifi_mgr.h"
#include "filesystem.h"
#include "multipart_parser.h"
#include "multipart_upload.h"

// Define MIN macro if not already defined
#ifndef MIN
#define MIN(a,b) (((a)<(b))?(a):(b))
#endif

static const char *TAG = "SAFEMODE";

typedef struct
{
    esp_ota_handle_t ota_handle;
    const esp_partition_t *ota_partition;
    bool is_firmware_field;
    bool ota_started;
    httpd_req_t *req;
} ota_context_t;

// OTA upload state for multipart_upload component
typedef struct
{
    esp_ota_handle_t ota_handle;
    const esp_partition_t *ota_partition;
    bool ota_started;
    esp_err_t last_err;
} ota_upload_state_t;

static bool ota_on_part_begin(const multipart_part_info_t *info, void *user_ctx)
{
    ota_upload_state_t *st = (ota_upload_state_t*)user_ctx;
    // Only accept the form field named "firmware"
    if (info && strcasecmp(info->name, "firmware") == 0)
    {
        st->ota_partition = esp_ota_get_next_update_partition(NULL);
        if (!st->ota_partition)
        {
            ESP_LOGE(TAG, "No OTA partition found");
            st->last_err = ESP_ERR_NOT_FOUND;
            return false;
        }
        esp_err_t err = esp_ota_begin(st->ota_partition, OTA_WITH_SEQUENTIAL_WRITES, &st->ota_handle);
        if (err != ESP_OK)
        {
            ESP_LOGE(TAG, "esp_ota_begin failed: %s", esp_err_to_name(err));
            st->last_err = err;
            return false;
        }
        st->ota_started = true;
        ESP_LOGI(TAG, "OTA update started (field: %s, filename: %s)", info->name, info->filename);
        return true; // accept data for this part
    }
    // Skip any other fields
    return false;
}

static esp_err_t ota_on_part_data(const char *data, size_t len, void *user_ctx)
{
    ota_upload_state_t *st = (ota_upload_state_t*)user_ctx;
    if (st->ota_started && len)
    {
        esp_err_t err = esp_ota_write(st->ota_handle, data, len);
        if (err != ESP_OK)
        {
            ESP_LOGE(TAG, "esp_ota_write failed: %s", esp_err_to_name(err));
            st->last_err = err;
            return err;
        }
    }
    return ESP_OK;
}

static void ota_on_part_end(void *user_ctx)
{
    // Nothing extra per part; finalization happens on on_finished
}

static void ota_on_finished(void *user_ctx)
{
    ota_upload_state_t *st = (ota_upload_state_t*)user_ctx;
    if (st->ota_started)
    {
        esp_err_t err = esp_ota_end(st->ota_handle);
        if (err != ESP_OK)
        {
            ESP_LOGE(TAG, "esp_ota_end failed: %s", esp_err_to_name(err));
            st->last_err = err;
            return;
        }
        err = esp_ota_set_boot_partition(st->ota_partition);
        if (err != ESP_OK)
        {
            ESP_LOGE(TAG, "esp_ota_set_boot_partition failed: %s", esp_err_to_name(err));
            st->last_err = err;
            return;
        }
        ESP_LOGI(TAG, "Firmware update successful");
    }
}

// HTTP server handlers
static esp_err_t root_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    extern const uint8_t web_safemode_html_start[] asm("_binary_safemode_html_start");
    extern const uint8_t web_safemode_html_end[]   asm("_binary_safemode_html_end");
    size_t html_size = web_safemode_html_end - web_safemode_html_start;
    return httpd_resp_send(req, (const char *)web_safemode_html_start, html_size);
}

static esp_err_t upload_firmware_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "Starting firmware update, size: %d", req->content_len);

    multipart_upload_handlers_t handlers = {
        .on_part_begin = ota_on_part_begin,
        .on_part_data = ota_on_part_data,
        .on_part_end = ota_on_part_end,
        .on_finished = ota_on_finished,
    };

    ota_upload_state_t state = {0};

    multipart_upload_config_t cfg = multipart_upload_default_config();
    cfg.rx_buf_size = 1024*4;

    esp_err_t err = multipart_upload_handle(req, &handlers, &state, &cfg);

    if (err != ESP_OK || !state.ota_started || state.last_err != ESP_OK)
    {
        // Abort OTA if it had started but failed
        if (state.ota_started && state.last_err != ESP_OK)
        {
            esp_ota_abort(state.ota_handle);
        }
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Firmware upload failed");
        return ESP_FAIL;
    }

    httpd_resp_sendstr(req, "OK");

    // Reboot after a short delay
    vTaskDelay(3000 / portTICK_PERIOD_MS);
    esp_restart();
    return ESP_OK;
}

static esp_err_t factory_reset_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "Factory reset requested");
    
    filesystem_delete_config_files();
    // Erase NVS partition
    nvs_flash_erase();
    

    httpd_resp_sendstr(req, "OK");

    // Reboot after a short delay
    vTaskDelay(2000 / portTICK_PERIOD_MS);
    esp_restart();

    return ESP_OK;
}

// HTTP server configuration
static httpd_handle_t start_webserver(void)
{
    httpd_handle_t server = NULL;
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.stack_size = (15*1024);
    config.max_uri_handlers = 8;
    config.max_resp_headers = 8;
    config.uri_match_fn = httpd_uri_match_wildcard;

    ESP_LOGI(TAG, "Starting HTTP Server");
    if (httpd_start(&server, &config) == ESP_OK)
    {
        // Root handler
        httpd_uri_t root_uri =
        {
            .uri = "/",
            .method = HTTP_GET,
            .handler = root_handler,
            .user_ctx = NULL
        };
        httpd_register_uri_handler(server, &root_uri);

        // Firmware upload handler
        httpd_uri_t upload_uri =
        {
            .uri = "/upload_firmware",
            .method = HTTP_POST,
            .handler = upload_firmware_handler,
            .user_ctx = NULL
        };
        httpd_register_uri_handler(server, &upload_uri);

        // Factory reset handler
        httpd_uri_t reset_uri =
        {
            .uri = "/factory_reset",
            .method = HTTP_POST,
            .handler = factory_reset_handler,
            .user_ctx = NULL
        };
        httpd_register_uri_handler(server, &reset_uri);

        return server;
    }

    ESP_LOGE(TAG, "Error starting server!");
    return NULL;
}

void safemode_start(void)
{
    ESP_LOGI(TAG, "Starting WiCAN Safe Mode");

    // Deinitialize existing WiFi to avoid conflicts
    ESP_LOGI(TAG, "Deinitializing existing WiFi...");
    esp_wifi_stop();
    esp_wifi_deinit();
    esp_netif_t *ap_netif = NULL;
    ap_netif = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");
    if (ap_netif)
    {
        esp_netif_destroy(ap_netif);
    }

    // Initialize NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Initialize TCP/IP and WiFi stack for AP mode
    // Tolerate already-initialized modules
    esp_err_t err = esp_netif_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE)
    {
        ESP_ERROR_CHECK(err);
    }
    err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE)
    {
        ESP_ERROR_CHECK(err);
    }
    esp_netif_create_default_wifi_ap();
    ap_netif = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    // Set up AP config
    wifi_config_t ap_config = { 0 };
    // Read MAC address and create safe mode SSID
    uint8_t mac_addr[6];
    ESP_ERROR_CHECK(esp_read_mac(mac_addr, ESP_MAC_WIFI_SOFTAP));
    sprintf((char *)ap_config.ap.ssid, "WiCAN_%02x%02x%02x%02x%02x%02x",
        mac_addr[0], mac_addr[1], mac_addr[2],
        mac_addr[3], mac_addr[4], mac_addr[5]);

    strcpy((char *)ap_config.ap.password, "@meatpi#");
    ap_config.ap.ssid_len = strlen((char *)ap_config.ap.ssid);
    ap_config.ap.channel = 6;
    ap_config.ap.authmode = WIFI_AUTH_WPA2_PSK;
    ap_config.ap.pmf_cfg = (wifi_pmf_config_t)
    {
        .required = false,
        .capable = true
    };
    ap_config.ap.pmf_cfg.required = false;
    ap_config.ap.max_connection = 4;
    esp_wifi_set_ps(WIFI_PS_NONE); // Disable power save mode for AP

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap_config));

    esp_netif_ip_info_t ip_info;
    IP4_ADDR(&ip_info.ip, 192, 168, 0, 10);
    IP4_ADDR(&ip_info.gw, 192, 168, 0, 10);
    IP4_ADDR(&ip_info.netmask, 255, 255, 255, 0);
    if (ap_netif)
    {
        esp_netif_dhcps_stop(ap_netif);
        esp_netif_set_ip_info(ap_netif, &ip_info);
        esp_netif_dhcps_start(ap_netif);
    }
    else
    {
        ESP_LOGE(TAG, "AP netif handle is NULL, cannot configure DHCP or IP info");
    }

    ESP_ERROR_CHECK(esp_wifi_start());

    // Start web server only once
    httpd_handle_t server = start_webserver();
    if (server == NULL)
    {
        ESP_LOGE(TAG, "Failed to start web server. Safe mode interface will not be available.");
        return;
    }

    ESP_LOGI(TAG, "Safe mode initialized successfully");
}
