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

#ifndef VPN_MANAGER_H
#define VPN_MANAGER_H

#include <esp_err.h>
#include <esp_event.h>
#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include "vpn_manager_http.h"

#ifdef __cplusplus
extern "C" {
#endif

// VPN Event Group Bits
#define VPN_CONNECTED_BIT       BIT0
#define VPN_CONNECTING_BIT      BIT1
#define VPN_DISCONNECTED_BIT    BIT2
#define VPN_ERROR_BIT           BIT3

// VPN Types
typedef enum 
{
    VPN_TYPE_DISABLED = 0,
    VPN_TYPE_WIREGUARD = 1
} vpn_type_t;

// VPN Status
typedef enum 
{
    VPN_STATUS_DISABLED = 0,
    VPN_STATUS_DISCONNECTED,
    VPN_STATUS_CONNECTING,
    VPN_STATUS_CONNECTED,
    VPN_STATUS_ERROR
} vpn_status_t;

// VPN WireGuard Configuration (separate from esp_wireguard API)
typedef struct 
{
    char private_key[64];
    char public_key[64];
    char address[32];
    char allowed_ip[32];
    char allowed_ip_mask[32];
    char endpoint[64];
    int port;
    int persistent_keepalive;
} vpn_wireguard_config_t;

// VPN Configuration
typedef struct 
{
    vpn_type_t type;
    bool enabled;
    union 
    {
        vpn_wireguard_config_t wireguard;
    } config;
} vpn_config_t;

/**
 * @brief Initialize VPN manager
 * 
 * @return esp_err_t ESP_OK on success
 */
esp_err_t vpn_manager_init(void);

/**
 * @brief Deinitialize VPN manager
 * 
 * @return esp_err_t ESP_OK on success
 */
// Deinit no longer exposed (task lifecycle is app-owned)

/**
 * @brief Start VPN connection
 * 
 * @param config VPN configuration
 * @return esp_err_t ESP_OK on success
 */
esp_err_t vpn_manager_start(const vpn_config_t *config);

/**
 * @brief Stop VPN connection
 * 
 * @return esp_err_t ESP_OK on success
 */
esp_err_t vpn_manager_stop(void);

/**
 * @brief Get VPN status
 * 
 * @return vpn_status_t Current VPN status
 */
vpn_status_t vpn_manager_get_status(void);

/**
 * @brief Get VPN event group handle
 * 
 * @return EventGroupHandle_t Event group handle
 */
/**
 * @brief Enable/disable VPN from application logic (server does not control runtime)
 */
void vpn_manager_set_enabled(bool enabled);

/**
 * @brief Request the VPN task to reload configuration from storage and reconcile state
 */
void vpn_manager_request_reload(void);

/**
 * @brief Request a one-shot VPN connectivity test (uses current config and gating)
 */
void vpn_manager_request_test(void);

/**
 * @brief Request a one-shot VPN connectivity test using hardcoded WG values (dev only)
 */
void vpn_manager_request_test_hardcoded(void);

/**
 * @brief Generate WireGuard key pair
 * 
 * @param public_key Buffer to store public key (base64 encoded)
 * @param public_key_size Size of public key buffer
 * @return esp_err_t ESP_OK on success
 */
esp_err_t vpn_manager_generate_wireguard_keys(char *public_key, size_t public_key_size);

/**
 * @brief Save VPN configuration to filesystem
 * 
 * @param config VPN configuration
 * @return esp_err_t ESP_OK on success
 */
esp_err_t vpn_manager_save_config(const vpn_config_t *config);

/**
 * @brief Load VPN configuration from filesystem
 * 
 * @param config Buffer to store loaded configuration
 * @return esp_err_t ESP_OK on success
 */
esp_err_t vpn_manager_load_config(vpn_config_t *config);

/**
 * @brief Parse WireGuard configuration from text
 * 
 * @param config_text Configuration text
 * @param config Buffer to store parsed configuration
 * @return esp_err_t ESP_OK on success
 */
// Text parse API removed from manager; use vpn_config_parse_wg if needed internally

/**
 * @brief Test VPN connection
 * 
 * @return esp_err_t ESP_OK if connection test passes
 */
// Deprecated test removed; use vpn_manager_request_test[_hardcoded]()

/**
 * @brief Get VPN IP address if connected
 * 
 * @param ip_str Buffer to store IP address string
 * @param ip_str_size Size of IP address buffer
 * @return esp_err_t ESP_OK if connected and IP retrieved
 */
esp_err_t vpn_manager_get_ip_address(char *ip_str, size_t ip_str_size);

#ifdef __cplusplus
}
#endif

#endif // VPN_MANAGER_H
