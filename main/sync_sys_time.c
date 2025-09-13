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
#include "esp_log.h"
#include "esp_err.h"
#include "esp_netif_sntp.h"
#include "esp_sntp.h"
#include "esp_heap_caps.h"
#include "dev_status.h"
#include <time.h>
#include <stdlib.h>
#include "sync_sys_time.h"
#include "rtcm.h"

#define TAG "SYNC_SYS_TIME"

/**
 * @brief Convert decimal to BCD (Binary Coded Decimal)
 */
static uint8_t dec_to_bcd(uint8_t decimal)
{
    return ((decimal / 10) << 4) | (decimal % 10);
}

/**
 * @brief Sync RTCM with current system time (UTC)
 */
static esp_err_t sync_rtcm_with_system_time(void)
{
    time_t now = time(NULL);
    struct tm timeinfo;
    gmtime_r(&now, &timeinfo); // Use gmtime_r to ensure UTC time
    
    // Convert to BCD format for RTCM
    uint8_t hour_bcd = dec_to_bcd(timeinfo.tm_hour);
    uint8_t min_bcd = dec_to_bcd(timeinfo.tm_min);
    uint8_t sec_bcd = dec_to_bcd(timeinfo.tm_sec);
    uint8_t year_bcd = dec_to_bcd(timeinfo.tm_year % 100); // Last two digits of year
    uint8_t month_bcd = dec_to_bcd(timeinfo.tm_mon + 1); // tm_mon is 0-based
    uint8_t day_bcd = dec_to_bcd(timeinfo.tm_mday);
    uint8_t weekday_bcd = dec_to_bcd(timeinfo.tm_wday); // 0=Sunday, 1=Monday, etc.
    
    // Set time first
    esp_err_t ret = rtcm_set_time(hour_bcd, min_bcd, sec_bcd);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set RTCM time: %s", esp_err_to_name(ret));
        return ret;
    }
    
    // Set date
    ret = rtcm_set_date(year_bcd, month_bcd, day_bcd, weekday_bcd);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set RTCM date: %s", esp_err_to_name(ret));
        return ret;
    }
    
    ESP_LOGI(TAG, "RTCM synchronized successfully with UTC: %04d-%02d-%02d %02d:%02d:%02d", 
             timeinfo.tm_year + 1900, timeinfo.tm_mon + 1, timeinfo.tm_mday,
             timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);
    
    return ESP_OK;
}

static void sync_sys_time(void *pvParameters)
{
    const time_t MIN_VALID_TIME = 1700000000; // ~2023-11-14
    uint32_t retry_count = 0;
    const uint32_t MAX_RETRIES = 10;

    dev_status_wait_for_any_bits(DEV_STA_CONNECTED_BIT, portMAX_DELAY);

    // Initialize SNTP with multiple servers for redundancy
    // Explicitly set timezone to UTC to prevent any local timezone conversions
    setenv("TZ", "UTC0", 1);
    tzset();

    // Build config explicitly; some IDF versions expect unused entries to be NULL
    esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("time.windows.com");
    config.server_from_dhcp = false;          // don't override manually set servers
    config.num_of_servers = 2;                // request two servers (will ignore NULL ones)
    config.servers[0] = "time.windows.com";
    config.servers[1] = "pool.ntp.org";      // second server

    esp_err_t ret = esp_netif_sntp_init(&config);
    if (ret == ESP_ERR_INVALID_ARG)
    {
        // Fallback: some builds may not accept num_of_servers > 1 with this macro
        ESP_LOGW(TAG, "SNTP multi-server init failed, falling back to single server");
        esp_netif_sntp_deinit();
        esp_sntp_config_t single_cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
        single_cfg.server_from_dhcp = false;
        ret = esp_netif_sntp_init(&single_cfg);
    }
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to initialize SNTP: %s", esp_err_to_name(ret));
        goto cleanup;
    }

    esp_netif_sntp_start();
    ESP_LOGI(TAG, "SNTP initialization completed with multiple servers");

    // Wait for initial time sync with retry mechanism
    while (retry_count < MAX_RETRIES)
    {
        if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(5000)) == ESP_OK)
        {
            time_t now = time(NULL);
            if (now >= MIN_VALID_TIME)
            {
                struct tm timeinfo;
                localtime_r(&now, &timeinfo);
                ESP_LOGI(TAG, "Time sync successful: %04d-%02d-%02d %02d:%02d:%02d UTC",
                         timeinfo.tm_year + 1900, timeinfo.tm_mon + 1, timeinfo.tm_mday,
                         timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);
                // Publish system time synced for other subsystems (e.g., VPN)
                dev_status_set_time_synced();
                
                // Sync RTCM with the newly acquired time
                esp_err_t rtcm_ret = sync_rtcm_with_system_time();
                if (rtcm_ret == ESP_OK) {
                    ESP_LOGI(TAG, "RTCM synchronized with system time");
                } else {
                    ESP_LOGW(TAG, "Failed to sync RTCM, but continuing with system time sync");
                }
                
                break;
            }
        }
        retry_count++;
        ESP_LOGW(TAG, "Time sync attempt %u/%u failed, retrying...", retry_count, MAX_RETRIES);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }

    if (retry_count >= MAX_RETRIES)
    {
        ESP_LOGE(TAG, "Failed to synchronize time after %u attempts", MAX_RETRIES);
    }

    // Periodic re-sync every hour to maintain accuracy
    for (;;)
    {
        vTaskDelay(pdMS_TO_TICKS(3600000)); // 1 hour

        // Check if we're still connected before attempting sync
        if (dev_status_is_bit_set(DEV_STA_CONNECTED_BIT))
        {
            if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(10000)) == ESP_OK)
            {
                ESP_LOGI(TAG, "Periodic time sync successful");
                dev_status_set_time_synced();
                
                // Also sync RTCM during periodic updates
                esp_err_t rtcm_ret = sync_rtcm_with_system_time();
                if (rtcm_ret == ESP_OK) {
                    ESP_LOGI(TAG, "RTCM periodic sync successful");
                } else {
                    ESP_LOGW(TAG, "RTCM periodic sync failed");
                }
            }
            else
            {
                ESP_LOGW(TAG, "Periodic time sync failed");
            }
        }
    }

cleanup:
    ESP_LOGE(TAG, "Time sync task terminating due to error");
    vTaskDelete(NULL);
}

void sync_sys_time_init(void)
{
    static StackType_t *sync_sys_time_stack;
    static StaticTask_t sync_sys_time_buffer;
    static const int sync_sys_time_stack_size = (1024 * 6);

    sync_sys_time_stack = heap_caps_malloc(sync_sys_time_stack_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);

    if (sync_sys_time_stack != NULL)
    {
        xTaskCreateStatic(sync_sys_time, "sync_sys_time", sync_sys_time_stack_size, NULL, 3,
                          sync_sys_time_stack, &sync_sys_time_buffer);
        ESP_LOGI(TAG, "Time sync task created successfully");
    }
    else
    {
        ESP_LOGE(TAG, "Failed to allocate sync_sys_time stack in PSRAM");
    }
}