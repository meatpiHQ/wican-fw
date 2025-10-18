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
#include "esp_log.h"
#include <string.h>
#include <stdlib.h>
#include "sleep_mode.h"
#include "wifi_network.h"
#include "wifi_mgr.h"
#include "dev_status.h"
#include "vehicle.h"
#include "ble.h"
#include "wc_timer.h"
#include "smartconnect.h"
#include "config_server.h"

#define TAG                                 "SMARTCONNECT"
#define SMARTCONNECT_TASK_STACK_SIZE        8192

#define DRIVE_MODE_DELAY_MS                 2000    // 2 seconds
#define HOME_MODE_DELAY_MS                  5000    // 5 seconds

// Default auto connect configuration
#define SMARTCONNECT_DEFAULT_CONFIG() { \
    .home_mode_enable_ap_sta = true, \
    .home_ssid = "", \
    .home_password = "", \
    .drive_mode_enable_ble = true, \
    .drive_mode_enable_wifi = false, \
    .drive_ssid = "", \
    .drive_password = "" \
}

// Configuration storage
static smartconnect_config_t smartconnect_config = SMARTCONNECT_DEFAULT_CONFIG();

// Current state and timer
static smartconnect_state_t current_state = SMARTCONNECT_STATE_INIT;
static wc_timer_t state_timer;
static wc_timer_t drive_mode_timeout_timer;

// Status logging counters
static int wifi_status_log_counter = 0;
static int home_status_log_counter = 0;

// AP SSID UID for unique device identification
static char ap_ssid_uid[32] = "";

// Forward declarations for mode functions
static void enable_drive_mode(void);
static void enable_home_mode(void);
static void disable_drive_mode(void);
static void disable_home_mode(void);

/**
 * Auto Connect FreeRTOS Task
 * Main state machine loop
 */
void smartconnect_task(void *pvParameters) {
    vehicle_motion_state_t motion_state;
    vehicle_motion_state_t prev_motion_state = VEHICLE_MOTION_INVALID;

    enable_home_mode();  // Start in home mode
    vTaskDelay(pdMS_TO_TICKS(3000));
    ESP_LOGI(TAG, "Auto Connect task started");

    while(1)
    {
        dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);

        motion_state = vehicle_motion_state();
        ESP_LOGI(TAG, "Current vehicle motion state: %d", motion_state);
        if(motion_state == VEHICLE_MOTION_INVALID)
        {
            ESP_LOGE(TAG, "Invalid vehicle motion state");
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        // State machine
        switch(current_state)
        {
            case SMARTCONNECT_STATE_INIT:
                ESP_LOGI(TAG, "State: INIT");
                if(motion_state == VEHICLE_MOTION_ACTIVE)
                {
                    ESP_LOGI(TAG, "Vehicle motion detected, waiting for stability...");
                    wc_timer_set(&state_timer, DRIVE_MODE_DELAY_MS);
                    current_state = SMARTCONNECT_STATE_WAITING_IGNITION_ON;
                }
                else if(motion_state == VEHICLE_MOTION_STATIONARY)
                {
                    ESP_LOGI(TAG, "Vehicle stationary detected, waiting for stability...");
                    wc_timer_set(&state_timer, HOME_MODE_DELAY_MS);
                    current_state = SMARTCONNECT_STATE_WAITING_IGNITION_OFF;
                }
                break;

            case SMARTCONNECT_STATE_WAITING_IGNITION_ON:
                if(motion_state == VEHICLE_MOTION_STATIONARY)
                {
                    ESP_LOGI(TAG, "Vehicle became stationary before timer expired");
                    wc_timer_set(&state_timer, HOME_MODE_DELAY_MS);
                    current_state = SMARTCONNECT_STATE_WAITING_IGNITION_OFF;
                }
                else if(wc_timer_is_expired(&state_timer))
                {
                    ESP_LOGI(TAG, "Switching to DRIVE MODE");
                    enable_drive_mode();
                    wifi_status_log_counter = 0;  // Reset counter for new state
                    current_state = SMARTCONNECT_STATE_DRIVE_MODE;
                }
                break;

            case SMARTCONNECT_STATE_WAITING_IGNITION_OFF:
                if(motion_state == VEHICLE_MOTION_ACTIVE)
                {
                    ESP_LOGI(TAG, "Vehicle motion detected before timer expired");
                    wc_timer_set(&state_timer, DRIVE_MODE_DELAY_MS);
                    current_state = SMARTCONNECT_STATE_WAITING_IGNITION_ON;
                }
                else if(wc_timer_is_expired(&state_timer))
                {
                    ESP_LOGI(TAG, "Switching to HOME MODE");
                    enable_home_mode();
                    home_status_log_counter = 0;  // Reset counter for new state
                    current_state = SMARTCONNECT_STATE_HOME_MODE;
                }
                break;

            case SMARTCONNECT_STATE_DRIVE_MODE:
                if(motion_state == VEHICLE_MOTION_STATIONARY)
                {
                    // Check if BLE is enabled and connected - if so, stay in drive mode
                    drive_connection_type_t drive_connection_type = config_server_get_drive_connection_type();
                    if (drive_connection_type == DRIVE_CONNECTION_BLE && dev_status_is_ble_connected()) {
                        ESP_LOGI(TAG, "Vehicle stopped but BLE is connected - staying in drive mode");
                        // Reset WiFi status log counter to avoid spam
                        wifi_status_log_counter = 0;
                    } else {
                        ESP_LOGI(TAG, "Vehicle stopped in drive mode, starting drive mode timeout");
                        
                        // Get drive mode timeout from config server (in seconds)
                        char *timeout_str = config_server_get_drive_mode_timeout();
                        uint32_t timeout_ms = HOME_MODE_DELAY_MS;  // Default fallback
                        
                        if (timeout_str != NULL && strlen(timeout_str) > 0) {
                            uint32_t timeout_seconds = atoi(timeout_str);
                            if (timeout_seconds > 0) {
                                timeout_ms = timeout_seconds * 1000;  // Convert to milliseconds
                                ESP_LOGI(TAG, "Using drive mode timeout: %lu seconds", timeout_seconds);
                            } else {
                                ESP_LOGW(TAG, "Invalid drive mode timeout value, using default");
                            }
                        } else {
                            ESP_LOGW(TAG, "No drive mode timeout configured, using default");
                        }
                        
                        wc_timer_set(&drive_mode_timeout_timer, timeout_ms);
                        current_state = SMARTCONNECT_STATE_DRIVE_MODE_TIMEOUT;
                    }
                }
                else
                {
                    // In drive mode, check WiFi connection status periodically
                    if (smartconnect_config.drive_mode_enable_wifi) {
                        bool wifi_connected = wifi_mgr_is_sta_connected();
                        wifi_status_log_counter++;
                        
                        // Log WiFi status every 30 seconds (30 iterations of 1 second delay)
                        if (wifi_status_log_counter >= 30) {
                            ESP_LOGI(TAG, "Drive mode WiFi status: %s (SSID: %s)", 
                                     wifi_connected ? "CONNECTED" : "DISCONNECTED", 
                                     smartconnect_config.drive_ssid);
                            wifi_status_log_counter = 0;
                        }
                    }
                }
                break;

            case SMARTCONNECT_STATE_DRIVE_MODE_TIMEOUT:
                if(motion_state == VEHICLE_MOTION_ACTIVE)
                {
                    ESP_LOGI(TAG, "Vehicle motion detected during drive mode timeout, returning to drive mode");
                    current_state = SMARTCONNECT_STATE_DRIVE_MODE;
                }
                else if(wc_timer_is_expired(&drive_mode_timeout_timer))
                {
                    // Check if BLE is connected before switching to home mode
                    drive_connection_type_t drive_connection_type = config_server_get_drive_connection_type();
                    if (drive_connection_type == DRIVE_CONNECTION_BLE && dev_status_is_ble_connected()) {
                        ESP_LOGI(TAG, "Drive mode timeout expired but BLE is connected - returning to drive mode");
                        current_state = SMARTCONNECT_STATE_DRIVE_MODE;
                    } else {
                        ESP_LOGI(TAG, "Drive mode timeout expired, switching to home mode");
                        disable_drive_mode();
                        enable_home_mode();
                        home_status_log_counter = 0;  // Reset counter for new state
                        current_state = SMARTCONNECT_STATE_HOME_MODE;
                    }
                }
                else
                {
                    // During timeout, check if BLE becomes connected
                    drive_connection_type_t drive_connection_type = config_server_get_drive_connection_type();
                    if (drive_connection_type == DRIVE_CONNECTION_BLE && dev_status_is_ble_connected()) {
                        ESP_LOGI(TAG, "BLE connected during drive mode timeout, returning to drive mode");
                        current_state = SMARTCONNECT_STATE_DRIVE_MODE;
                    }
                }
                break;

            case SMARTCONNECT_STATE_HOME_MODE:
                if(motion_state == VEHICLE_MOTION_ACTIVE)
                {
                    ESP_LOGI(TAG, "Vehicle motion detected in home mode, disabling home mode");
                    disable_home_mode();
                    wc_timer_set(&state_timer, DRIVE_MODE_DELAY_MS);
                    current_state = SMARTCONNECT_STATE_WAITING_IGNITION_ON;
                }
                else
                {
                    // In home mode, check WiFi connection status periodically
                    if (smartconnect_config.home_mode_enable_ap_sta && strlen(smartconnect_config.home_ssid) > 0) {
                        bool wifi_connected = wifi_mgr_is_sta_connected();
                        bool ap_started = wifi_mgr_is_ap_started();
                        home_status_log_counter++;
                        
                        // Log WiFi status every 30 seconds
                        if (home_status_log_counter >= 30) {
                            ESP_LOGI(TAG, "Home mode status - STA: %s (SSID: %s), AP: %s", 
                                     wifi_connected ? "CONNECTED" : "DISCONNECTED", 
                                     smartconnect_config.home_ssid,
                                     ap_started ? "ACTIVE" : "INACTIVE");
                            home_status_log_counter = 0;
                        }
                    }
                }
                break;

            default:
                ESP_LOGE(TAG, "Unknown state: %d", current_state);
                current_state = SMARTCONNECT_STATE_INIT;
                break;
        }

        prev_motion_state = motion_state;
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

/**
 * Enable drive mode - activate connectivity based on configuration
 */
static void enable_drive_mode(void)
{
    ESP_LOGI(TAG, "Enabling DRIVE MODE");
    
    // Set drive mode enabled bit and clear home mode bit
    dev_status_set_drive_mode_enabled();
    dev_status_clear_home_mode_enabled();
    
    // Check drive protocol and manage autopid bit
    int8_t drive_protocol = config_server_get_drive_protocol();
    if (drive_protocol == OBD_ELM327) {
        ESP_LOGI(TAG, "Drive mode: ELM327 protocol - disabling AutoPID");
        dev_status_clear_autopid_enabled();
    }
    
    // Ensure complete disconnection from home WiFi
    ESP_LOGI(TAG, "Disconnecting from home WiFi...");
    wifi_mgr_sta_disconnect();
    vTaskDelay(pdMS_TO_TICKS(100));
    
    // Disable all WiFi functionality to ensure clean state
    wifi_mgr_disable();
    vTaskDelay(pdMS_TO_TICKS(200));
    
    // Completely deinitialize WiFi manager to allow clean reconfiguration
    wifi_mgr_deinit();
    vTaskDelay(pdMS_TO_TICKS(300)); // Allow time for complete deinitialization
    
    // Get drive connection type from config server
    drive_connection_type_t drive_connection_type = config_server_get_drive_connection_type();
    
    if (drive_connection_type == DRIVE_CONNECTION_BLE) {
        // BLE mode: Enable BLE for configuration and WiFi in AP mode
        ESP_LOGI(TAG, "Drive mode: BLE connection - enabling BLE and WiFi AP");
        ble_enable();
        
        // Configure WiFi for AP-only mode in BLE drive mode
        wifi_mgr_config_t wifi_config = WIFI_MGR_DEFAULT_CONFIG();
        wifi_config.mode = WIFI_MGR_MODE_AP;
        
        // Configure AP settings using provided SSID UID
        strncpy(wifi_config.ap_ssid, ap_ssid_uid, sizeof(wifi_config.ap_ssid) - 1);
        wifi_config.ap_ssid[sizeof(wifi_config.ap_ssid) - 1] = '\0';
        
        // Use AP password from config server
        char *ap_pass = config_server_get_ap_pass();
        if (ap_pass != NULL && strlen(ap_pass) > 0) {
            strncpy(wifi_config.ap_password, ap_pass, sizeof(wifi_config.ap_password) - 1);
        } else {
            strcpy(wifi_config.ap_password, "12345678"); // Default fallback
        }
        
        wifi_config.ap_channel = config_server_get_ap_ch();
        wifi_config.ap_max_connections = 4;
        wifi_config.ap_auto_disable = config_server_get_ap_auto_disable();  // Keep AP always on in BLE drive mode
        
        wifi_mgr_init(&wifi_config);
        wifi_mgr_enable();
    } else if (drive_connection_type == DRIVE_CONNECTION_WIFI && 
               strlen(smartconnect_config.drive_ssid) > 0) {
        // WiFi mode: Connect to Drive WiFi
        ESP_LOGI(TAG, "Drive mode: WiFi connection - connecting to %s", smartconnect_config.drive_ssid);
        
        // Disable BLE when using WiFi in drive mode
        ble_disable();
        
        // Configure WiFi for drive mode (STA only)
        wifi_mgr_config_t wifi_config = WIFI_MGR_DEFAULT_CONFIG();
        wifi_config.mode = WIFI_MGR_MODE_STA;
        strncpy(wifi_config.sta_ssid, smartconnect_config.drive_ssid, sizeof(wifi_config.sta_ssid) - 1);
        strncpy(wifi_config.sta_password, smartconnect_config.drive_password, sizeof(wifi_config.sta_password) - 1);
        wifi_config.sta_auto_reconnect = true;
        wifi_config.sta_max_retry = -1;  // Limited retries in drive mode for faster fallback
        
        // Set Drive WiFi security mode from config server
        wifi_security_t drive_security = config_server_get_drive_security_type();
        switch(drive_security) {
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
        
        wifi_mgr_init(&wifi_config);
        wifi_mgr_enable();
        wifi_mgr_sta_connect();
        
        ESP_LOGI(TAG, "Drive WiFi configured - SSID: %s, Security: %d, Max Retries: %d", 
                 smartconnect_config.drive_ssid, drive_security, wifi_config.sta_max_retry);
    } else {
        // Unknown or disabled connection type
        ESP_LOGI(TAG, "Drive mode: No valid connection type configured - disabling all connectivity");
        ble_disable();
        wifi_mgr_disable();
    }
    
    ESP_LOGI(TAG, "DRIVE MODE enabled - Connection type: %s", 
             (drive_connection_type == DRIVE_CONNECTION_WIFI) ? "wifi" : 
             (drive_connection_type == DRIVE_CONNECTION_BLE) ? "ble+wifi_ap" : "unknown");
}

/**
 * Enable home mode - activate connectivity for home/garage use
 */
static void enable_home_mode(void)
{
    ESP_LOGI(TAG, "Enabling HOME MODE");
    
    // Set home mode enabled bit and clear drive mode bit
    dev_status_set_home_mode_enabled();
    dev_status_clear_drive_mode_enabled();
    
    // Check home protocol and manage autopid bit
    int8_t home_protocol = config_server_get_home_protocol();
    if (home_protocol == AUTO_PID) {
        ESP_LOGI(TAG, "Home mode: AutoPID protocol - enabling AutoPID");
        dev_status_set_autopid_enabled();
    }
    
    // In Home mode, only WiFi is allowed - disable BLE
    ESP_LOGI(TAG, "Disabling BLE for home mode...");
    ble_disable();
    
    // Ensure complete WiFi shutdown and cleanup
    wifi_mgr_sta_disconnect();
    vTaskDelay(pdMS_TO_TICKS(100));
    wifi_mgr_disable();
    vTaskDelay(pdMS_TO_TICKS(200));
    
    // Completely deinitialize WiFi manager to allow clean reconfiguration
    wifi_mgr_deinit();
    vTaskDelay(pdMS_TO_TICKS(300)); // Allow time for complete deinitialization
    
    if (smartconnect_config.home_mode_enable_ap_sta) {
        ESP_LOGI(TAG, "Enabling AP+STA mode for home mode");
        
        // Configure WiFi for home mode (AP+STA)
        wifi_mgr_config_t wifi_config = WIFI_MGR_DEFAULT_CONFIG();
        
        if (strlen(smartconnect_config.home_ssid) > 0) {
            // Configure STA to connect to home network
            wifi_config.mode = WIFI_MGR_MODE_APSTA;
            strncpy(wifi_config.sta_ssid, smartconnect_config.home_ssid, sizeof(wifi_config.sta_ssid) - 1);
            strncpy(wifi_config.sta_password, smartconnect_config.home_password, sizeof(wifi_config.sta_password) - 1);
            wifi_config.sta_auto_reconnect = true;
            wifi_config.sta_max_retry = -1;  // Infinite retries in home mode
            
            // Set Home WiFi security mode from config server
            wifi_security_t home_security = config_server_get_home_security_type();
            switch(home_security) {
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
            
            ESP_LOGI(TAG, "Connecting to home network: %s", smartconnect_config.home_ssid);
        } else {
            // Only AP mode if no home SSID configured
            wifi_config.mode = WIFI_MGR_MODE_AP;
            ESP_LOGI(TAG, "No home SSID configured, running AP only");
        }
        
        // Configure AP settings using provided SSID UID
        strncpy(wifi_config.ap_ssid, ap_ssid_uid, sizeof(wifi_config.ap_ssid) - 1);
        wifi_config.ap_ssid[sizeof(wifi_config.ap_ssid) - 1] = '\0';
        
        // Use AP password from config server
        char *ap_pass = config_server_get_ap_pass();
        if (ap_pass != NULL && strlen(ap_pass) > 0) {
            strncpy(wifi_config.ap_password, ap_pass, sizeof(wifi_config.ap_password) - 1);
        } else {
            strcpy(wifi_config.ap_password, "12345678"); // Default fallback
        }
        
        wifi_config.ap_channel = config_server_get_ap_ch();
        wifi_config.ap_max_connections = 4;
        wifi_config.ap_auto_disable = config_server_get_ap_auto_disable();  // Keep AP always on in home mode
        
        wifi_mgr_init(&wifi_config);
        wifi_mgr_enable();
        
        if (strlen(smartconnect_config.home_ssid) > 0) {
            wifi_mgr_sta_connect();
        }
    } else {
        ESP_LOGI(TAG, "WiFi disabled in home mode");
        wifi_mgr_disable();
    }
    
    ESP_LOGI(TAG, "HOME MODE enabled - BLE: OFF, WiFi: %s", 
             smartconnect_config.home_mode_enable_ap_sta ? "AP+STA" : "OFF");
}

/**
 * Disable drive mode - cleanup drive mode resources
 */
static void disable_drive_mode(void)
{
    ESP_LOGI(TAG, "Disabling DRIVE MODE");
    
    // Clear drive mode enabled bit
    dev_status_clear_drive_mode_enabled();
    
    // Get drive connection type to know what to disable
    drive_connection_type_t drive_connection_type = config_server_get_drive_connection_type();
    
    if (drive_connection_type == DRIVE_CONNECTION_WIFI) {
        ESP_LOGI(TAG, "Disconnecting drive mode WiFi");
        wifi_mgr_sta_disconnect();
        vTaskDelay(pdMS_TO_TICKS(100)); // Allow time for disconnection
        wifi_mgr_disable();
        vTaskDelay(pdMS_TO_TICKS(100));
        wifi_mgr_deinit(); // Complete cleanup
        vTaskDelay(pdMS_TO_TICKS(100));
    } else if (drive_connection_type == DRIVE_CONNECTION_BLE) {
        ESP_LOGI(TAG, "Disabling drive mode BLE and WiFi AP");
        ble_disable();
        vTaskDelay(pdMS_TO_TICKS(100)); // Allow time for BLE shutdown
        wifi_mgr_disable();
        vTaskDelay(pdMS_TO_TICKS(100));
        wifi_mgr_deinit(); // Complete cleanup
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    
    ESP_LOGI(TAG, "DRIVE MODE disabled");
}

/**
 * Disable home mode - cleanup home mode resources
 */
static void disable_home_mode(void)
{
    ESP_LOGI(TAG, "Disabling HOME MODE");
    
    // Clear home mode enabled bit
    dev_status_clear_home_mode_enabled();
    
    // Disconnect from home network and disable AP
    if (smartconnect_config.home_mode_enable_ap_sta) {
        ESP_LOGI(TAG, "Disconnecting home WiFi and stopping AP");
        wifi_mgr_sta_disconnect();
        vTaskDelay(pdMS_TO_TICKS(100)); // Allow time for disconnection
        wifi_mgr_disable();
        vTaskDelay(pdMS_TO_TICKS(100));
        wifi_mgr_deinit(); // Complete cleanup
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    
    // BLE is not used in home mode, so no need to manage it here
    
    ESP_LOGI(TAG, "HOME MODE disabled");
}

/**
 * Get current auto connect state (for debugging/status)
 */
smartconnect_state_t smartconnect_get_state(void)
{
    return current_state;
}

/**
 * Set auto connect configuration
 */
esp_err_t smartconnect_set_config(const smartconnect_config_t *config)
{
    if (config == NULL) {
        ESP_LOGE(TAG, "Invalid configuration pointer");
        return ESP_ERR_INVALID_ARG;
    }
    
    // Copy configuration
    memcpy(&smartconnect_config, config, sizeof(smartconnect_config_t));
    
    ESP_LOGI(TAG, "Auto connect configuration updated:");
    ESP_LOGI(TAG, "  Home mode AP+STA: %s", smartconnect_config.home_mode_enable_ap_sta ? "enabled" : "disabled");
    ESP_LOGI(TAG, "  Home SSID: %s", smartconnect_config.home_ssid);
    ESP_LOGI(TAG, "  Drive mode BLE: %s", smartconnect_config.drive_mode_enable_ble ? "enabled" : "disabled");
    ESP_LOGI(TAG, "  Drive mode WiFi: %s", smartconnect_config.drive_mode_enable_wifi ? "enabled" : "disabled");
    ESP_LOGI(TAG, "  Drive SSID: %s", smartconnect_config.drive_ssid);
    
    return ESP_OK;
}

/**
 * Get auto connect configuration
 */
esp_err_t smartconnect_get_config(smartconnect_config_t *config)
{
    if (config == NULL) {
        ESP_LOGE(TAG, "Invalid configuration pointer");
        return ESP_ERR_INVALID_ARG;
    }
    
    // Copy configuration
    memcpy(config, &smartconnect_config, sizeof(smartconnect_config_t));
    
    return ESP_OK;
}

/**
 * Load configuration from persistent storage
 * Integrates with config_server to get Home and Drive WiFi settings
 */
esp_err_t smartconnect_load_config(void)
{
    ESP_LOGI(TAG, "Loading SmartConnect configuration from config server...");
    
    // Start with default configuration
    smartconnect_config_t config = SMARTCONNECT_DEFAULT_CONFIG();
    
    // Load Home WiFi configuration from config server
    char *home_ssid = config_server_get_home_ssid();
    char *home_password = config_server_get_home_password();
    
    if (home_ssid != NULL && strlen(home_ssid) > 0) {
        strncpy(config.home_ssid, home_ssid, sizeof(config.home_ssid) - 1);
        config.home_ssid[sizeof(config.home_ssid) - 1] = '\0';
    }
    
    if (home_password != NULL && strlen(home_password) > 0) {
        strncpy(config.home_password, home_password, sizeof(config.home_password) - 1);
        config.home_password[sizeof(config.home_password) - 1] = '\0';
    }
    
    // Load Drive WiFi configuration from config server
    char *drive_ssid = config_server_get_drive_ssid();
    char *drive_password = config_server_get_drive_password();
    drive_connection_type_t drive_connection_type = config_server_get_drive_connection_type();
    
    if (drive_ssid != NULL && strlen(drive_ssid) > 0) {
        strncpy(config.drive_ssid, drive_ssid, sizeof(config.drive_ssid) - 1);
        config.drive_ssid[sizeof(config.drive_ssid) - 1] = '\0';
    }
    
    if (drive_password != NULL && strlen(drive_password) > 0) {
        strncpy(config.drive_password, drive_password, sizeof(config.drive_password) - 1);
        config.drive_password[sizeof(config.drive_password) - 1] = '\0';
    }
    
    // Home mode: Only WiFi (AP+STA)
    config.home_mode_enable_ap_sta = true;
    
    // Drive mode: Connection type will be determined dynamically from config server
    // No need to set drive_mode_enable_wifi or drive_mode_enable_ble here
    
    ESP_LOGI(TAG, "Loaded SmartConnect configuration:");
    ESP_LOGI(TAG, "  Home SSID: %s", config.home_ssid);
    ESP_LOGI(TAG, "  Drive SSID: %s", config.drive_ssid);
    ESP_LOGI(TAG, "  Drive connection type: %s", 
             (drive_connection_type == DRIVE_CONNECTION_WIFI) ? "wifi" : 
             (drive_connection_type == DRIVE_CONNECTION_BLE) ? "ble" : "unknown");
    
    return smartconnect_set_config(&config);
}

/**
 * Save configuration to persistent storage (to be implemented with actual storage)
 */
esp_err_t smartconnect_save_config(void)
{
    ESP_LOGI(TAG, "Saving auto connect configuration to storage...");
    
    // TODO: Implement actual saving to NVS or config_server
    // Example: Save to config_server when integrated
    // config_server_set_home_ssid(smartconnect_config.home_ssid);
    // config_server_set_home_password(smartconnect_config.home_password);
    
    return ESP_OK;
}

/**
 * Initialize and start the Auto Connect task
 */
esp_err_t smartconnect_init(const char* ap_ssid_uid_param) {
    static StackType_t *smartconnect_task_stack;
    static StaticTask_t smartconnect_task_buffer;
    
    // Initialize state
    current_state = SMARTCONNECT_STATE_INIT;
    
    // Store AP SSID UID
    if (ap_ssid_uid_param != NULL && strlen(ap_ssid_uid_param) > 0) {
        strncpy(ap_ssid_uid, ap_ssid_uid_param, sizeof(ap_ssid_uid) - 1);
        ap_ssid_uid[sizeof(ap_ssid_uid) - 1] = '\0';
        ESP_LOGI(TAG, "SmartConnect AP SSID UID set to: %s", ap_ssid_uid);
    } else {
        ESP_LOGW(TAG, "No AP SSID UID provided, using default device naming");
        strcpy(ap_ssid_uid, "WiCAN-Device");
    }
    
    // Load configuration
    esp_err_t ret = smartconnect_load_config();
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Failed to load configuration, using defaults");
    }
    
    // Allocate stack memory in PSRAM
    smartconnect_task_stack = heap_caps_malloc(SMARTCONNECT_TASK_STACK_SIZE, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(smartconnect_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate smartconnect_task stack in PSRAM");
        return ESP_FAIL;
    }
    memset(smartconnect_task_stack, 0, SMARTCONNECT_TASK_STACK_SIZE);
    
    // Create task with static allocation
    TaskHandle_t task_handle = xTaskCreateStatic(smartconnect_task, "smartconnect", SMARTCONNECT_TASK_STACK_SIZE, NULL, 5, 
                     smartconnect_task_stack, &smartconnect_task_buffer);
    if (task_handle == NULL) {
        ESP_LOGE(TAG, "Failed to create smartconnect task");
        heap_caps_free(smartconnect_task_stack);
        return ESP_FAIL;
    }
    
    ESP_LOGI(TAG, "Auto Connect task created successfully");

    return ESP_OK;
}