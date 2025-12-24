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

#include "dev_status.h"
#include "esp_partition.h"
#include "esp_ota_ops.h"
#include "esp_timer.h"
#include <stdio.h>

static const char *DEV_STATUS_TAG = "DEV_STATUS";
static EventGroupHandle_t s_dev_status_event_group = NULL;
static StaticEventGroup_t s_dev_status_event_group_buffer;
static const esp_partition_t *dev_status_running_partition = NULL;
static esp_app_desc_t dev_status_running_app_info;


const esp_partition_t *dev_status_get_running_partition(void)
{
    return dev_status_running_partition;
}

esp_app_desc_t *dev_status_get_running_app_info(void)
{
    return &dev_status_running_app_info;
}

// Helper function to get bit name for logging
static const char* get_bit_name(EventBits_t bit)
{
    switch (bit) {
        case DEV_AWAKE_BIT:           return "DEV_AWAKE";
        case DEV_SLEEP_BIT:           return "DEV_SLEEP";
        case DEV_STA_CONNECTED_BIT:   return "STA_CONNECTED";
        case DEV_MQTT_CONNECTED_BIT:  return "MQTT_CONNECTED";
        case DEV_BLE_CONNECTED_BIT:   return "BLE_CONNECTED";
        case DEV_STA_ENABLED_BIT:     return "STA_ENABLED";
        case DEV_AP_ENABLED_BIT:      return "AP_ENABLED";
    case DEV_TIME_SYNCED_BIT:     return "TIME_SYNCED";
    case DEV_VPN_ENABLED_BIT:     return "VPN_ENABLED";
        default:                      return "UNKNOWN";
    }
}

// Helper function to log multiple bits
static void log_bits(const char* action, EventBits_t bits)
{
    if (bits & DEV_AWAKE_BIT)           ESP_LOGI(DEV_STATUS_TAG, "%s: DEV_AWAKE", action);
    if (bits & DEV_SLEEP_BIT)           ESP_LOGI(DEV_STATUS_TAG, "%s: DEV_SLEEP", action);
    if (bits & DEV_STA_CONNECTED_BIT)   ESP_LOGI(DEV_STATUS_TAG, "%s: STA_CONNECTED", action);
    if (bits & DEV_MQTT_CONNECTED_BIT)  ESP_LOGI(DEV_STATUS_TAG, "%s: MQTT_CONNECTED", action);
    if (bits & DEV_BLE_CONNECTED_BIT)   ESP_LOGI(DEV_STATUS_TAG, "%s: BLE_CONNECTED", action);
    if (bits & DEV_STA_ENABLED_BIT)     ESP_LOGI(DEV_STATUS_TAG, "%s: STA_ENABLED", action);
    if (bits & DEV_AP_ENABLED_BIT)      ESP_LOGI(DEV_STATUS_TAG, "%s: AP_ENABLED", action);
    if (bits & DEV_TIME_SYNCED_BIT)     ESP_LOGI(DEV_STATUS_TAG, "%s: TIME_SYNCED", action);
    if (bits & DEV_VPN_ENABLED_BIT)     ESP_LOGI(DEV_STATUS_TAG, "%s: VPN_ENABLED", action);
}

void dev_status_init(void)
{
    if (s_dev_status_event_group == NULL) {
        s_dev_status_event_group = xEventGroupCreateStatic(&s_dev_status_event_group_buffer);
        if (s_dev_status_event_group == NULL) {
            ESP_LOGE(DEV_STATUS_TAG, "Failed to create device status event group");
        } else {
            ESP_LOGI(DEV_STATUS_TAG, "Device status event group initialized");
            xEventGroupClearBits(s_dev_status_event_group, DEV_STATUS_ALL_BITS);
        }
    }
    dev_status_running_partition = esp_ota_get_running_partition();

    if(esp_ota_get_partition_description(dev_status_running_partition, &dev_status_running_app_info) != ESP_OK) {
        ESP_LOGE(DEV_STATUS_TAG, "Error getting partition_description");
        memset(&dev_status_running_app_info, 0, sizeof(dev_status_running_app_info));
        dev_status_running_app_info.version[0] = '1';
        dev_status_running_app_info.version[1] = '\0';
    } else {
        ESP_LOGI(DEV_STATUS_TAG, "Running Partition: %s", dev_status_running_partition->label);
        ESP_LOGI(DEV_STATUS_TAG, "App Version: %s", dev_status_running_app_info.version);
        ESP_LOGI(DEV_STATUS_TAG, "Project Name: %s", dev_status_running_app_info.project_name);
        ESP_LOGI(DEV_STATUS_TAG, "Build Time: %s %s", dev_status_running_app_info.date, dev_status_running_app_info.time);
        ESP_LOGI(DEV_STATUS_TAG, "IDF Version: %s", dev_status_running_app_info.idf_ver);
    }
}

void dev_status_set_bits(EventBits_t bits_to_set)
{
    if (s_dev_status_event_group != NULL) {
        xEventGroupSetBits(s_dev_status_event_group, bits_to_set);
        log_bits("SET", bits_to_set);
    }
}

void dev_status_clear_bits(EventBits_t bits_to_clear)
{
    if (s_dev_status_event_group != NULL) {
        xEventGroupClearBits(s_dev_status_event_group, bits_to_clear);
        log_bits("CLEAR", bits_to_clear);
    }
}

void dev_status_clear_all_bits(void)
{
    if (s_dev_status_event_group != NULL) {
        xEventGroupClearBits(s_dev_status_event_group, DEV_STATUS_ALL_BITS);
        ESP_LOGI(DEV_STATUS_TAG, "CLEAR: ALL_BITS");
    }
}

EventBits_t dev_status_wait_for_bits(EventBits_t bits_to_wait_for, TickType_t timeout)
{
    if (s_dev_status_event_group != NULL) {
        return xEventGroupWaitBits(s_dev_status_event_group, 
                                  bits_to_wait_for, 
                                  pdFALSE,  // Don't clear bits on exit
                                  pdTRUE,   // Wait for ALL bits
                                  timeout);
    }
    return 0;
}

EventBits_t dev_status_wait_for_any_bits(EventBits_t bits_to_wait_for, TickType_t timeout)
{
    if (s_dev_status_event_group != NULL) {
        return xEventGroupWaitBits(s_dev_status_event_group, 
                                  bits_to_wait_for, 
                                  pdFALSE,  // Don't clear bits on exit
                                  pdFALSE,  // Wait for ANY bits
                                  timeout);
    }
    return 0;
}

EventBits_t dev_status_get_bits(void)
{
    if (s_dev_status_event_group != NULL) {
        return xEventGroupGetBits(s_dev_status_event_group);
    }
    return 0;
}

bool dev_status_is_bit_set(EventBits_t bit)
{
    return (dev_status_get_bits() & bit) != 0;
}

bool dev_status_are_bits_set(EventBits_t bits)
{
    EventBits_t current_bits = dev_status_get_bits();
    return (current_bits & bits) == bits;  // ALL specified bits must be set
}

bool dev_status_is_any_bit_set(EventBits_t bits)
{
    return (dev_status_get_bits() & bits) != 0;  // ANY of the specified bits is set
}

size_t dev_status_format_uptime(char *buf, size_t buf_len)
{
    if (!buf || buf_len == 0)
    {
        return 0;
    }

    int64_t us = esp_timer_get_time(); // microseconds since boot
    uint64_t total_seconds = (uint64_t)(us / 1000000ULL);

    uint32_t days = (uint32_t)(total_seconds / 86400ULL);
    total_seconds %= 86400ULL;
    uint32_t hours = (uint32_t)(total_seconds / 3600ULL);
    total_seconds %= 3600ULL;
    uint32_t minutes = (uint32_t)(total_seconds / 60ULL);
    uint32_t seconds = (uint32_t)(total_seconds % 60ULL);

    int written;
    if (days > 0)
    {
        written = snprintf(buf, buf_len, "%lud %02lu:%02lu:%02lu", days, hours, minutes, seconds);
    }
    else
    {
        written = snprintf(buf, buf_len, "%02lu:%02lu:%02lu", hours, minutes, seconds);
    }

    if (written < 0)
    {
        // encoding/formatting error
        if (buf_len > 0)
            buf[0] = '\0';
        return 0;
    }

    // If truncated, ensure NUL-termination and report number actually stored
    if ((size_t)written >= buf_len)
    {
        buf[buf_len - 1] = '\0';
        return buf_len - 1;
    }
    return (size_t)written;
}

const char *dev_status_get_uptime_string(void)
{
    static char buf[32];
    dev_status_format_uptime(buf, sizeof(buf));
    return buf;
}