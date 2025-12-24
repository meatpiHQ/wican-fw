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

#ifndef DEV_STATUS_H
#define DEV_STATUS_H

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include <stdbool.h>
#include <stddef.h>
#include "esp_partition.h"
#include "esp_app_desc.h"

#ifdef __cplusplus
extern "C" {
#endif

// Device status event bits
#define DEV_AWAKE_BIT                   BIT0
#define DEV_SLEEP_BIT                   BIT1
#define DEV_STA_CONNECTED_BIT           BIT2
#define DEV_MQTT_CONNECTED_BIT          BIT3
#define DEV_BLE_CONNECTED_BIT           BIT4
#define DEV_SDCARD_MOUNTED_BIT          BIT5 
#define DEV_RST_REASON_SW_BIT           BIT6
#define DEV_RST_REASON_DEEP_SLEEP_BIT   BIT7
#define DEV_BLE_ENABLED_BIT             BIT8
#define DEV_STA_ENABLED_BIT             BIT9
#define DEV_AP_ENABLED_BIT              BIT10
#define DEV_AUTOPID_ENABLED_BIT         BIT11
#define DEV_HOME_MODE_ENABLED_BIT       BIT12
#define DEV_DRIVE_MODE_ENABLED_BIT      BIT13
#define DEV_SMARTCONNECT_ENABLED_BIT    BIT14
#define DEV_STA_AP_OVERLAP_BIT          BIT15
#define DEV_TIME_SYNCED_BIT             BIT16
#define DEV_VPN_ENABLED_BIT             BIT17
#define DEV_AUTOPID_ELM327_APP_BIT      BIT18

// Mask for "all bits" in a FreeRTOS event group.
// Only the lower 24 bits are available for application use; the upper 8 are reserved by the kernel.
#define DEV_STATUS_ALL_BITS ((EventBits_t)0x00FFFFFFU)

// Initialize device status event group
void dev_status_init(void);

// Getters for running partition and app info
const esp_partition_t *dev_status_get_running_partition(void);
esp_app_desc_t *dev_status_get_running_app_info(void);

// Returns a static buffer with human-readable uptime since boot.
// Format: "HH:MM:SS" or "<d>d HH:MM:SS" when days > 0.
const char* dev_status_get_uptime_string(void);

// Returns number of characters written (excluding null), or 0 on error/insufficient buffer.
size_t dev_status_format_uptime(char* buf, size_t buf_len);

// Generic set/clear functions
void dev_status_set_bits(EventBits_t bits_to_set);
void dev_status_clear_bits(EventBits_t bits_to_clear);
void dev_status_clear_all_bits(void);

// Wait for specific bits to be set
EventBits_t dev_status_wait_for_bits(EventBits_t bits_to_wait_for, TickType_t timeout);
EventBits_t dev_status_wait_for_any_bits(EventBits_t bits_to_wait_for, TickType_t timeout);

// Get current status bits
EventBits_t dev_status_get_bits(void);

// Check if specific bits are set
bool dev_status_is_bit_set(EventBits_t bit);
bool dev_status_are_bits_set(EventBits_t bits);
bool dev_status_is_any_bit_set(EventBits_t bits);

// Macros for common operations
#define dev_status_set_awake()          dev_status_set_bits(DEV_AWAKE_BIT); dev_status_clear_bits(DEV_SLEEP_BIT)
#define dev_status_set_sleep()          dev_status_set_bits(DEV_SLEEP_BIT); dev_status_clear_bits(DEV_AWAKE_BIT)
#define dev_status_set_sta_connected()  dev_status_set_bits(DEV_STA_CONNECTED_BIT)
#define dev_status_set_sta_enabled()    dev_status_set_bits(DEV_STA_ENABLED_BIT)
#define dev_status_set_ap_enabled()     dev_status_set_bits(DEV_AP_ENABLED_BIT)
#define dev_status_set_autopid_enabled() dev_status_set_bits(DEV_AUTOPID_ENABLED_BIT)
#define dev_status_set_home_mode_enabled() dev_status_set_bits(DEV_HOME_MODE_ENABLED_BIT)
#define dev_status_set_drive_mode_enabled() dev_status_set_bits(DEV_DRIVE_MODE_ENABLED_BIT)
#define dev_status_set_smartconnect_enabled() dev_status_set_bits(DEV_SMARTCONNECT_ENABLED_BIT)
#define dev_status_set_mqtt_connected() dev_status_set_bits(DEV_MQTT_CONNECTED_BIT)
#define dev_status_set_ble_connected()  dev_status_set_bits(DEV_BLE_CONNECTED_BIT)
// New helpers
#define dev_status_set_time_synced()    dev_status_set_bits(DEV_TIME_SYNCED_BIT)
#define dev_status_clear_time_synced()  dev_status_clear_bits(DEV_TIME_SYNCED_BIT)
#define dev_status_set_vpn_enabled()    dev_status_set_bits(DEV_VPN_ENABLED_BIT)
#define dev_status_clear_vpn_enabled()  dev_status_clear_bits(DEV_VPN_ENABLED_BIT)

#define dev_status_clear_awake()          dev_status_clear_bits(DEV_AWAKE_BIT)
#define dev_status_clear_sleep()          dev_status_clear_bits(DEV_SLEEP_BIT)
#define dev_status_clear_sta_connected() dev_status_clear_bits(DEV_STA_CONNECTED_BIT)
#define dev_status_clear_sta_enabled()   dev_status_clear_bits(DEV_STA_ENABLED_BIT)
#define dev_status_clear_ap_enabled()    dev_status_clear_bits(DEV_AP_ENABLED_BIT)
#define dev_status_clear_autopid_enabled() dev_status_clear_bits(DEV_AUTOPID_ENABLED_BIT)
#define dev_status_clear_home_mode_enabled() dev_status_clear_bits(DEV_HOME_MODE_ENABLED_BIT)
#define dev_status_clear_drive_mode_enabled() dev_status_clear_bits(DEV_DRIVE_MODE_ENABLED_BIT)
#define dev_status_clear_smartconnect_enabled() dev_status_clear_bits(DEV_SMARTCONNECT_ENABLED_BIT)
#define dev_status_clear_mqtt_connected() dev_status_clear_bits(DEV_MQTT_CONNECTED_BIT)
#define dev_status_clear_ble_connected()  dev_status_clear_bits(DEV_BLE_CONNECTED_BIT)

#define dev_status_is_awake()        dev_status_is_bit_set(DEV_AWAKE_BIT)
#define dev_status_is_sleeping()     dev_status_is_bit_set(DEV_SLEEP_BIT)
#define dev_status_is_sta_connected()  dev_status_is_bit_set(DEV_STA_CONNECTED_BIT)
#define dev_status_is_sta_enabled()    dev_status_is_bit_set(DEV_STA_ENABLED_BIT)
#define dev_status_is_ap_enabled()     dev_status_is_bit_set(DEV_AP_ENABLED_BIT)
#define dev_status_is_autopid_enabled() dev_status_is_bit_set(DEV_AUTOPID_ENABLED_BIT)
#define dev_status_is_home_mode_enabled() dev_status_is_bit_set(DEV_HOME_MODE_ENABLED_BIT)
#define dev_status_is_drive_mode_enabled() dev_status_is_bit_set(DEV_DRIVE_MODE_ENABLED_BIT)
#define dev_status_is_smartconnect_enabled() dev_status_is_bit_set(DEV_SMARTCONNECT_ENABLED_BIT)
#define dev_status_is_mqtt_connected() dev_status_is_bit_set(DEV_MQTT_CONNECTED_BIT)
#define dev_status_is_ble_connected()  dev_status_is_bit_set(DEV_BLE_CONNECTED_BIT)
// New checkers
#define dev_status_is_time_synced()    dev_status_is_bit_set(DEV_TIME_SYNCED_BIT)
#define dev_status_is_vpn_enabled()    dev_status_is_bit_set(DEV_VPN_ENABLED_BIT)

#ifdef __cplusplus
}
#endif

#endif // DEV_STATUS_H
