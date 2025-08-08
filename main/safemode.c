#include <stdio.h>
#include <string.h>
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

// Define MIN macro if not already defined
#ifndef MIN
#define MIN(a,b) (((a)<(b))?(a):(b))
#endif

static const char *TAG = "SAFEMODE";

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
    char buf[1024];
    esp_ota_handle_t update_handle = 0;
    const esp_partition_t *update_partition = NULL;
    esp_err_t err;
    int remaining = req->content_len;
    bool first_chunk = true;

    ESP_LOGI(TAG, "Starting firmware update, size: %d", remaining);

    // Get the next update partition
    update_partition = esp_ota_get_next_update_partition(NULL);
    if (update_partition == NULL)
    {
        ESP_LOGE(TAG, "No OTA partition found");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No OTA partition");
        return ESP_FAIL;
    }

    // Begin OTA update
    err = esp_ota_begin(update_partition, OTA_WITH_SEQUENTIAL_WRITES, &update_handle);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "esp_ota_begin failed: %s", esp_err_to_name(err));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OTA begin failed");
        return ESP_FAIL;
    }

    while (remaining > 0)
    {
        int recv_len = httpd_req_recv(req, buf, MIN(remaining, sizeof(buf)));
        if (recv_len <= 0)
        {
            if (recv_len == HTTPD_SOCK_ERR_TIMEOUT)
            {
                continue;
            }
            esp_ota_abort(update_handle);
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Receive timeout");
            return ESP_FAIL;
        }

        // Skip multipart headers on first chunk
        if (first_chunk)
        {
            char *data_start = strstr(buf, "\r\n\r\n");
            if (data_start != NULL)
            {
                data_start += 4; // Skip the \r\n\r\n
                // Find the end boundary and calculate actual data length
                char *boundary_start = strstr(data_start, "\r\n--");
                int data_len;
                if (boundary_start != NULL)
                {
                    data_len = boundary_start - data_start;
                }
                else
                {
                    data_len = recv_len - (data_start - buf);
                }

                if (data_len > 0)
                {
                    err = esp_ota_write(update_handle, data_start, data_len);
                    if (err != ESP_OK)
                    {
                        ESP_LOGE(TAG, "esp_ota_write failed: %s", esp_err_to_name(err));
                        esp_ota_abort(update_handle);
                        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OTA write failed");
                        return ESP_FAIL;
                    }
                }
            }
            first_chunk = false;
        }
        else
        {
            // For subsequent chunks, check for boundary
            char *boundary_start = strstr(buf, "\r\n--");
            int data_len = boundary_start ? (boundary_start - buf) : recv_len;

            if (data_len > 0)
            {
                err = esp_ota_write(update_handle, buf, data_len);
                if (err != ESP_OK)
                {
                    ESP_LOGE(TAG, "esp_ota_write failed: %s", esp_err_to_name(err));
                    esp_ota_abort(update_handle);
                    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OTA write failed");
                    return ESP_FAIL;
                }
            }

            if (boundary_start)
            {
                break; // End of data
            }
        }

        remaining -= recv_len;
    }

    // Finalize OTA update
    err = esp_ota_end(update_handle);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "esp_ota_end failed: %s", esp_err_to_name(err));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OTA end failed");
        return ESP_FAIL;
    }

    // Set boot partition
    err = esp_ota_set_boot_partition(update_partition);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "esp_ota_set_boot_partition failed: %s", esp_err_to_name(err));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Set boot partition failed");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Firmware update successful");
    httpd_resp_sendstr(req, "OK");

    // Reboot after a short delay
    vTaskDelay(1000 / portTICK_PERIOD_MS);
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
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
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
    ap_config.ap.authmode = WIFI_AUTH_WPA2_WPA3_PSK;
    ap_config.ap.pmf_cfg = (wifi_pmf_config_t)
    {
        .required = false,
        .capable = true
    };
    ap_config.ap.pmf_cfg.required = false;
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
    esp_netif_set_ip_info(ap_netif, &ip_info);
    esp_netif_dhcps_start(ap_netif);

    ESP_ERROR_CHECK(esp_wifi_start());

    // Start web server
    if (start_webserver() == NULL)
    {
        ESP_LOGE(TAG, "Failed to start web server. Safe mode interface will not be available.");
        return;
    }
    ESP_LOGI(TAG, "Safe mode initialized successfully");
    start_webserver();

    ESP_LOGI(TAG, "Safe mode initialized successfully");
}