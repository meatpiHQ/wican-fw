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

#ifndef __SMARTCONNECT_H__
#define __SMARTCONNECT_H__

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

// Auto Connect Configuration Structure
typedef struct {
    // Home mode configuration
    bool home_mode_enable_ap_sta;       // Enable AP+STA mode in home mode
    char home_ssid[32];                 // Home WiFi SSID
    char home_password[64];             // Home WiFi password
    
    // Drive mode configuration
    bool drive_mode_enable_ble;         // Enable BLE in drive mode
    bool drive_mode_enable_wifi;        // Enable WiFi in drive mode  
    char drive_ssid[32];                // Drive mode WiFi SSID (optional)
    char drive_password[64];            // Drive mode WiFi password (optional)
} smartconnect_config_t;

// Auto Connect States
typedef enum {
    SMARTCONNECT_STATE_INIT,
    SMARTCONNECT_STATE_WAITING_IGNITION_ON,
    SMARTCONNECT_STATE_WAITING_IGNITION_OFF,
    SMARTCONNECT_STATE_DRIVE_MODE,
    SMARTCONNECT_STATE_DRIVE_MODE_TIMEOUT,
    SMARTCONNECT_STATE_HOME_MODE
} smartconnect_state_t;

/**
 * Initialize and start the Auto Connect task
 */
esp_err_t smartconnect_init(const char* ap_ssid_uid);

/**
 * Get current auto connect state (for debugging/status)
 */
smartconnect_state_t smartconnect_get_state(void);

/**
 * Set auto connect configuration
 */
esp_err_t smartconnect_set_config(const smartconnect_config_t *config);

/**
 * Get auto connect configuration
 */
esp_err_t smartconnect_get_config(smartconnect_config_t *config);

/**
 * Load configuration from persistent storage
 */
esp_err_t smartconnect_load_config(void);

/**
 * Save configuration to persistent storage
 */
esp_err_t smartconnect_save_config(void);

#ifdef __cplusplus
}
#endif

#endif // __SMARTCONNECT_H__