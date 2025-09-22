/*
 * Enhanced WiFi Network Manager
 * Provides comprehensive control over WiFi operations
 */

#ifndef WIFI_MGR_H
#define WIFI_MGR_H

#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_event.h"
#include "esp_mac.h"
#include "freertos/event_groups.h"

// Event group bits for external status monitoring
#define WIFI_CONNECTED_BIT          BIT0
#define WIFI_FAIL_BIT               BIT1
#define WIFI_DISCONNECTED_BIT       BIT2
#define WIFI_INIT_BIT               BIT3
#define WIFI_CONNECT_IDLE_BIT       BIT4
#define WIFI_AP_STARTED_BIT         BIT5
#define WIFI_ENABLED_BIT            BIT6
#define WIFI_STA_GOT_IP_BIT         BIT7
#define WIFI_STA_AP_OVERLAP_BIT     BIT8

#define WIFI_MGR_MAX_FALLBACKS 5

#define WIFI_MGR_DEFAULT_CONFIG() { \
    .sta_ssid = "", \
    .sta_password = "", \
    .sta_auth_mode = WIFI_AUTH_WPA2_PSK, \
    .sta_auto_reconnect = true, \
    .sta_max_retry = -1, \
    .ap_ssid = "ESP32-AP", \
    .ap_password = "12345678", \
    .ap_channel = 6, \
    .ap_max_connections = 4, \
    .ap_auth_mode = WIFI_AUTH_WPA2_PSK, \
    .mode = WIFI_MGR_MODE_APSTA, \
    .ap_auto_disable = false, \
    .power_save_mode = WIFI_PS_NONE \
}

#define WIFI_MGR_AUTO_CONFIG() { \
    .sta_ssid = "", \
    .sta_password = "", \
    .sta_auth_mode = WIFI_AUTH_WPA2_PSK, \
    .sta_auto_reconnect = true, \
    .sta_max_retry = -1, \
    .ap_ssid = "ESP32-Setup", \
    .ap_password = "12345678", \
    .ap_channel = 6, \
    .ap_max_connections = 4, \
    .ap_auth_mode = WIFI_AUTH_WPA2_PSK, \
    .mode = WIFI_MGR_MODE_APSTA, \
    .ap_auto_disable = true, \
    .power_save_mode = WIFI_PS_NONE \
}

// WiFi Manager modes
typedef enum {
    WIFI_MGR_MODE_OFF = 0,
    WIFI_MGR_MODE_STA,
    WIFI_MGR_MODE_AP,
    WIFI_MGR_MODE_APSTA,
} wifi_mgr_mode_t;

// WiFi Manager configuration structure
typedef struct {
    // STA Configuration
    char sta_ssid[32];
    char sta_password[64];
    wifi_auth_mode_t sta_auth_mode;
    bool sta_auto_reconnect;
    int sta_max_retry;          // -1 for infinite retries
    // Fallback STA networks (priority by index: 0 = highest)
    struct {
        char ssid[32];
        char password[64];
        wifi_auth_mode_t auth_mode;
    } fallbacks[WIFI_MGR_MAX_FALLBACKS];
    uint8_t fallback_count;
    
    // AP Configuration
    char ap_ssid[32];
    char ap_password[64];
    uint8_t ap_channel;
    uint8_t ap_max_connections;
    wifi_auth_mode_t ap_auth_mode;
    
    // General Configuration
    wifi_mgr_mode_t mode;
    bool ap_auto_disable;       // Auto-disable AP when STA connects
    
    // Power saving
    wifi_ps_type_t power_save_mode;
} wifi_mgr_config_t;

// Callback function types
typedef void (*wifi_mgr_sta_connected_cb_t)(void);
typedef void (*wifi_mgr_sta_disconnected_cb_t)(void);
typedef void (*wifi_mgr_ap_station_connected_cb_t)(uint8_t* mac, uint8_t aid);
typedef void (*wifi_mgr_ap_station_disconnected_cb_t)(uint8_t* mac, uint8_t aid);

// Callback structure
typedef struct {
    wifi_mgr_sta_connected_cb_t sta_connected;
    wifi_mgr_sta_disconnected_cb_t sta_disconnected;
    wifi_mgr_ap_station_connected_cb_t ap_station_connected;
    wifi_mgr_ap_station_disconnected_cb_t ap_station_disconnected;
} wifi_mgr_callbacks_t;

// WiFi Manager API Functions
esp_err_t wifi_mgr_init(wifi_mgr_config_t* config);
esp_err_t wifi_mgr_deinit(void);
esp_err_t wifi_mgr_enable(void);
esp_err_t wifi_mgr_disable(void);

// Mode management
esp_err_t wifi_mgr_set_mode(wifi_mgr_mode_t mode);
wifi_mgr_mode_t wifi_mgr_get_mode(void);

// STA configuration
esp_err_t wifi_mgr_set_sta_config(const char* ssid, const char* password, wifi_auth_mode_t auth_mode);
esp_err_t wifi_mgr_set_sta_auto_reconnect(bool enable, int max_retry);
esp_err_t wifi_mgr_sta_connect(void);
esp_err_t wifi_mgr_sta_disconnect(void);

// AP configuration
esp_err_t wifi_mgr_set_ap_config(const char* ssid, const char* password, uint8_t channel, uint8_t max_connections);
esp_err_t wifi_mgr_set_ap_auto_disable(bool enable);

// Status queries (lightweight alternatives to full status structure)
bool wifi_mgr_is_sta_connected(void);
bool wifi_mgr_is_ap_started(void);
bool wifi_mgr_is_enabled(void);
char* wifi_mgr_get_sta_ip(void);
uint16_t wifi_mgr_get_ap_connected_stations(void);

// Event group access for external status monitoring
EventGroupHandle_t wifi_mgr_get_event_group(void);

// WiFi scanning
char* wifi_mgr_scan_networks(void);

// Callback management
esp_err_t wifi_mgr_set_callbacks(wifi_mgr_callbacks_t* callbacks);

// Power management
esp_err_t wifi_mgr_set_power_save_mode(wifi_ps_type_t mode);

#endif // WIFI_MGR_H
