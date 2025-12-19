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

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_event.h"
#include "esp_mac.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include <string.h>
#include "lwip/sockets.h"
#include "config_server.h"
#include "ble.h"
#include "dev_status.h"
#include <cJSON.h>
#include "wifi_mgr.h"
#include "smartconnect.h"

#define TAG "WiFi_NETWORK"

void wifi_network_init(char* ap_ssid_uid)
{
    wifi_mgr_config_t wifi_config;
    
    // Validate input parameter
    if (ap_ssid_uid == NULL || strlen(ap_ssid_uid) == 0) {
        ESP_LOGE(TAG, "Invalid AP SSID UID parameter");
        return;
    }
	
    // Get WiFi mode from config server
    int8_t wifi_mode = config_server_get_wifi_mode();
    
    if (wifi_mode == SMARTCONNECT_MODE) {
        // SmartConnect mode: Let SmartConnect handle all WiFi management
        ESP_LOGI(TAG, "SmartConnect mode detected - initializing SmartConnect module");
        
        esp_err_t ret = smartconnect_init(ap_ssid_uid);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to initialize SmartConnect: %s", esp_err_to_name(ret));
        } else {
            ESP_LOGI(TAG, "SmartConnect initialized successfully - it will manage WiFi");
        }
        return; // SmartConnect handles everything, exit here
    }
    
    // Non-SmartConnect modes: Use WiFi Manager with regular configuration
    ESP_LOGI(TAG, "Manual WiFi mode detected - configuring WiFi Manager");
    
    // Configure WiFi mode
    if (wifi_mode == AP_MODE) {
        wifi_config = (wifi_mgr_config_t)WIFI_MGR_DEFAULT_CONFIG();
        wifi_config.mode = WIFI_MGR_MODE_AP;
        ESP_LOGI(TAG, "Configuring AP mode");
    } else if (wifi_mode == APSTA_MODE) {
        wifi_config = (wifi_mgr_config_t)WIFI_MGR_DEFAULT_CONFIG();
        wifi_config.mode = WIFI_MGR_MODE_APSTA;
        ESP_LOGI(TAG, "Configuring AP+STA mode");
    } else if (wifi_mode == BLESTA_MODE) {
        wifi_config = (wifi_mgr_config_t)WIFI_MGR_DEFAULT_CONFIG();
        wifi_config.mode = WIFI_MGR_MODE_STA; // STA only, BLE handled elsewhere
        ESP_LOGI(TAG, "Configuring BLE+STA mode (STA-only WiFi)");
    } else if (wifi_mode == STA_MODE) {
        wifi_config = (wifi_mgr_config_t)WIFI_MGR_DEFAULT_CONFIG();
        wifi_config.mode = WIFI_MGR_MODE_STA; // STA only
        ESP_LOGI(TAG, "Configuring Station-only mode (STA-only WiFi)");
    } else {
        // Default to AUTO mode
        wifi_config = (wifi_mgr_config_t)WIFI_MGR_AUTO_CONFIG();
        ESP_LOGI(TAG, "Configuring AUTO mode");
    }
    
    // Configure STA settings from config server (for AP+STA and AUTO modes)
    char *sta_ssid = config_server_get_sta_ssid();
    char *sta_password = config_server_get_sta_pass();
    
    if (sta_ssid != NULL) {
        strncpy(wifi_config.sta_ssid, sta_ssid, sizeof(wifi_config.sta_ssid) - 1);
        wifi_config.sta_ssid[sizeof(wifi_config.sta_ssid) - 1] = '\0';
    } else {
        wifi_config.sta_ssid[0] = '\0';
    }
    
    if (sta_password != NULL) {
        strncpy(wifi_config.sta_password, sta_password, sizeof(wifi_config.sta_password) - 1);
        wifi_config.sta_password[sizeof(wifi_config.sta_password) - 1] = '\0';
    } else {
        wifi_config.sta_password[0] = '\0';
    }
    
    // Set STA security mode
    wifi_security_t sta_security = config_server_get_sta_security();
    switch(sta_security) {
        case WIFI_OPEN:
            wifi_config.sta_auth_mode = WIFI_AUTH_OPEN;
            break;
        case WIFI_WPA2_PSK:
            wifi_config.sta_auth_mode = WIFI_AUTH_WPA2_PSK;
            break;
        case WIFI_WPA3_PSK:
            wifi_config.sta_auth_mode = WIFI_AUTH_WPA2_WPA3_PSK;
            break;
        default:
            wifi_config.sta_auth_mode = WIFI_AUTH_WPA2_PSK;
            break;
    }
    
    // Configure AP settings from config server
    char *ap_pass = config_server_get_ap_pass();
    if (ap_pass != NULL && strlen(ap_pass) > 0) {
        strncpy(wifi_config.ap_password, ap_pass, sizeof(wifi_config.ap_password) - 1);
        wifi_config.ap_password[sizeof(wifi_config.ap_password) - 1] = '\0';
    } else {
        wifi_config.ap_password[0] = '\0';
    }
    
    // Set unique AP SSID using device UID
    snprintf(wifi_config.ap_ssid, sizeof(wifi_config.ap_ssid), "%s", ap_ssid_uid);
    
    // Set AP channel
    int8_t ap_channel = config_server_get_ap_ch();
    if (ap_channel > 0 && ap_channel <= 14) {
        wifi_config.ap_channel = ap_channel;
    }
    
    // Set AP auto-disable from config server
    int8_t ap_auto_disable = config_server_get_ap_auto_disable();
    wifi_config.ap_auto_disable = (ap_auto_disable != 0);
    
    // Set auto-reconnect parameters
    wifi_config.sta_auto_reconnect = true;
    wifi_config.sta_max_retry = -1; // Infinite retries

    // Populate fallback networks from config server (priority by index)
    wifi_config.fallback_count = 0;
    int fb_count = config_server_get_sta_fallbacks_count();
    if (fb_count > 0) {
        if (fb_count > WIFI_MGR_MAX_FALLBACKS) fb_count = WIFI_MGR_MAX_FALLBACKS;
        for (int i = 0; i < fb_count; ++i) {
            const char* f_ssid = config_server_get_sta_fallback_ssid(i);
            const char* f_pass = config_server_get_sta_fallback_pass(i);
            wifi_security_t f_sec = config_server_get_sta_fallback_security(i);
            if (f_ssid && f_ssid[0]) {
                strncpy(wifi_config.fallbacks[wifi_config.fallback_count].ssid, f_ssid, sizeof(wifi_config.fallbacks[0].ssid) - 1);
                wifi_config.fallbacks[wifi_config.fallback_count].ssid[sizeof(wifi_config.fallbacks[0].ssid) - 1] = '\0';
                if (f_pass) {
                    strncpy(wifi_config.fallbacks[wifi_config.fallback_count].password, f_pass, sizeof(wifi_config.fallbacks[0].password) - 1);
                    wifi_config.fallbacks[wifi_config.fallback_count].password[sizeof(wifi_config.fallbacks[0].password) - 1] = '\0';
                } else {
                    wifi_config.fallbacks[wifi_config.fallback_count].password[0] = '\0';
                }
                switch (f_sec) {
                    case WIFI_OPEN: wifi_config.fallbacks[wifi_config.fallback_count].auth_mode = WIFI_AUTH_OPEN; break;
                    case WIFI_WPA3_PSK: wifi_config.fallbacks[wifi_config.fallback_count].auth_mode = WIFI_AUTH_WPA2_WPA3_PSK; break;
                    case WIFI_WPA2_PSK: default: wifi_config.fallbacks[wifi_config.fallback_count].auth_mode = WIFI_AUTH_WPA2_PSK; break;
                }
                wifi_config.fallback_count++;
            }
        }
        ESP_LOGI(TAG, "   - Fallback networks loaded: %u", wifi_config.fallback_count);
    }
    
    ESP_LOGI(TAG, "  WiFi Configuration from Config Server:");
    ESP_LOGI(TAG, "   - WiFi Mode: %d (AP=0, APSTA=1, SMARTCONNECT=2, BLESTA=3, STA=4)", wifi_mode);
    ESP_LOGI(TAG, "   - Manager Mode: %d (OFF=0, STA=1, AP=2, APSTA=3, AUTO=4)", wifi_config.mode);
    ESP_LOGI(TAG, "   - STA SSID: %s", wifi_config.sta_ssid);
    ESP_LOGI(TAG, "   - STA Auth Mode: %d", wifi_config.sta_auth_mode);
    ESP_LOGI(TAG, "   - AP SSID: %s", wifi_config.ap_ssid);
    ESP_LOGI(TAG, "   - AP Channel: %d", wifi_config.ap_channel);
    ESP_LOGI(TAG, "   - AP Auto-disable: %s", wifi_config.ap_auto_disable ? "Yes" : "No");
    ESP_LOGI(TAG, "   - STA Auto-reconnect: %s", wifi_config.sta_auto_reconnect ? "Yes" : "No");
    
    // Initialize WiFi Manager
    esp_err_t ret = wifi_mgr_init(&wifi_config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "WiFi Manager initialization failed: %s", esp_err_to_name(ret));
        return;
    }
    ESP_LOGI(TAG, "WiFi Manager initialized");
    
    // Enable WiFi
    ret = wifi_mgr_enable();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable WiFi: %s", esp_err_to_name(ret));
        return;
    }
    ESP_LOGI(TAG, "WiFi enabled");
}

/**
 * Get the current WiFi mode from config server
 * Used by SmartConnect to determine if it should manage WiFi
 */
bool wifi_network_is_smartconnect_mode(void)
{
    int8_t wifi_mode = config_server_get_wifi_mode();
    return (wifi_mode == SMARTCONNECT_MODE);
}