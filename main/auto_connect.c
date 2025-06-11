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
#include "sleep_mode.h"
#include "wifi_network.h"
#include "dev_status.h"
#include "vehicle.h"
#include "ble.h"
#include "wc_timer.h"
#include "auto_connect.h"
#include "vehicle.h"

#define TAG                                 "AUTO_CONNECT"
#define AUTO_CONNECT_TASK_STACK_SIZE        4096

#define DRIVE_MODE_DELAY_MS                 2000    // 2 seconds
#define HOME_MODE_DELAY_MS                  5000    // 5 seconds

// Current state and timer
static auto_connect_state_t current_state = AUTO_CONNECT_STATE_INIT;
static wc_timer_t state_timer;

// Forward declarations for mode functions
static void enable_drive_mode(void);
static void enable_home_mode(void);
static void disable_drive_mode(void);
static void disable_home_mode(void);

/**
 * Auto Connect FreeRTOS Task
 * Main state machine loop
 */
void auto_connect_task(void *pvParameters) {
    float batt_volt = 0;
    vehicle_ignition_state_t ignition_state;
    vehicle_ignition_state_t prev_ignition_state = VEHICLE_STATE_IGNITION_INVALID;

    ESP_LOGI(TAG, "Auto Connect task started");

    while(1)
    {
        dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);

        ignition_state = vehicle_ignition_state();

        if(ignition_state == VEHICLE_STATE_IGNITION_INVALID)
        {
            ESP_LOGE(TAG, "Invalid ignition state");
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        // State machine
        switch(current_state)
        {
            case AUTO_CONNECT_STATE_INIT:
                ESP_LOGI(TAG, "State: INIT");
                if(ignition_state == VEHICLE_STATE_IGNITION_ON)
                {
                    ESP_LOGI(TAG, "Ignition ON detected, waiting for stability...");
                    wc_timer_set(&state_timer, DRIVE_MODE_DELAY_MS);
                    current_state = AUTO_CONNECT_STATE_WAITING_IGNITION_ON;
                }
                else if(ignition_state == VEHICLE_STATE_IGNITION_OFF)
                {
                    ESP_LOGI(TAG, "Ignition OFF detected, waiting for stability...");
                    wc_timer_set(&state_timer, HOME_MODE_DELAY_MS);
                    current_state = AUTO_CONNECT_STATE_WAITING_IGNITION_OFF;
                }
                break;

            case AUTO_CONNECT_STATE_WAITING_IGNITION_ON:
                if(ignition_state == VEHICLE_STATE_IGNITION_OFF)
                {
                    ESP_LOGI(TAG, "Ignition turned OFF before timer expired");
                    wc_timer_set(&state_timer, HOME_MODE_DELAY_MS);
                    current_state = AUTO_CONNECT_STATE_WAITING_IGNITION_OFF;
                }
                else if(wc_timer_is_expired(&state_timer))
                {
                    ESP_LOGI(TAG, "Switching to DRIVE MODE");
                    enable_drive_mode();
                    current_state = AUTO_CONNECT_STATE_DRIVE_MODE;
                }
                break;

            case AUTO_CONNECT_STATE_WAITING_IGNITION_OFF:
                if(ignition_state == VEHICLE_STATE_IGNITION_ON)
                {
                    ESP_LOGI(TAG, "Ignition turned ON before timer expired");
                    wc_timer_set(&state_timer, DRIVE_MODE_DELAY_MS);
                    current_state = AUTO_CONNECT_STATE_WAITING_IGNITION_ON;
                }
                else if(wc_timer_is_expired(&state_timer))
                {
                    ESP_LOGI(TAG, "Switching to HOME MODE");
                    enable_home_mode();
                    current_state = AUTO_CONNECT_STATE_HOME_MODE;
                }
                break;

            case AUTO_CONNECT_STATE_DRIVE_MODE:
                if(ignition_state == VEHICLE_STATE_IGNITION_OFF)
                {
                    ESP_LOGI(TAG, "Ignition OFF in drive mode, disabling drive mode");
                    disable_drive_mode();
                    wc_timer_set(&state_timer, HOME_MODE_DELAY_MS);
                    current_state = AUTO_CONNECT_STATE_WAITING_IGNITION_OFF;
                }
                // In drive mode, continue normal operation
                break;

            case AUTO_CONNECT_STATE_HOME_MODE:
                if(ignition_state == VEHICLE_STATE_IGNITION_ON)
                {
                    ESP_LOGI(TAG, "Ignition ON in home mode, disabling home mode");
                    disable_home_mode();
                    wc_timer_set(&state_timer, DRIVE_MODE_DELAY_MS);
                    current_state = AUTO_CONNECT_STATE_WAITING_IGNITION_ON;
                }
                // In home mode, continue normal operation
                break;

            default:
                ESP_LOGE(TAG, "Unknown state: %d", current_state);
                current_state = AUTO_CONNECT_STATE_INIT;
                break;
        }

        prev_ignition_state = ignition_state;
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

/**
 * Enable drive mode - activate BLE and WiFi for vehicle connectivity
 */
static void enable_drive_mode(void)
{
    ESP_LOGI(TAG, "Enabling DRIVE MODE");
    
    // TODO: Implement drive mode specific functionality
    // Enable BLE for OBD communication
    // ble_enable_drive_mode();
    
    // Enable WiFi for data transmission
    // wifi_enable_drive_mode();
    
    // Set device status bits
    ble_enable();
    
    ESP_LOGI(TAG, "DRIVE MODE enabled - BLE and WiFi active");
}

/**
 * Enable home mode - activate connectivity for home/garage use
 */
static void enable_home_mode(void)
{
    ESP_LOGI(TAG, "Enabling HOME MODE");
    
    // TODO: Implement home mode specific functionality
    // Enable BLE for configuration/maintenance
    // ble_enable_home_mode();
    
    // Configure WiFi for home network
    // wifi_enable_home_mode();
    
    // Set appropriate device status bits
    wifi_network_start();
    
    ESP_LOGI(TAG, "HOME MODE enabled - BLE active for configuration");
}

/**
 * Disable drive mode - cleanup drive mode resources
 */
static void disable_drive_mode(void)
{
    ESP_LOGI(TAG, "Disabling DRIVE MODE");
    
    // TODO: Implement drive mode cleanup
    // ble_disable_drive_mode();
    // wifi_disable_drive_mode();
    
    ble_disable();
    
    ESP_LOGI(TAG, "DRIVE MODE disabled");
}

/**
 * Disable home mode - cleanup home mode resources
 */
static void disable_home_mode(void)
{
    ESP_LOGI(TAG, "Disabling HOME MODE");
    
    // TODO: Implement home mode cleanup
    // ble_disable_home_mode();
    // wifi_disable_home_mode();
    
    wifi_network_stop();
    
    ESP_LOGI(TAG, "HOME MODE disabled");
}

/**
 * Get current auto connect state (for debugging/status)
 */
auto_connect_state_t auto_connect_get_state(void)
{
    return current_state;
}

/**
 * Initialize and start the Auto Connect task
 */
esp_err_t auto_connect_init(void) {
    static StackType_t *auto_connect_task_stack;
    static StaticTask_t auto_connect_task_buffer;
    
    // Initialize state
    current_state = AUTO_CONNECT_STATE_INIT;
    
    // Allocate stack memory in PSRAM
    auto_connect_task_stack = heap_caps_malloc(AUTO_CONNECT_TASK_STACK_SIZE, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(auto_connect_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate auto_connect_task stack in PSRAM");
        return ESP_FAIL;
    }
    memset(auto_connect_task_stack, 0, AUTO_CONNECT_TASK_STACK_SIZE);
    
    // Check if memory allocation was successful
    if (auto_connect_task_stack != NULL){
        // Create task with static allocation
        xTaskCreateStatic(auto_connect_task, "auto_connect", AUTO_CONNECT_TASK_STACK_SIZE, NULL, 5, 
                         auto_connect_task_stack, &auto_connect_task_buffer);
        ESP_LOGI(TAG, "Auto Connect task created successfully");
    }
    else 
    {
        // Handle memory allocation failure
        ESP_LOGE(TAG, "Failed to allocate auto_connect_task stack in PSRAM");
        return ESP_FAIL;
    }

    return ESP_OK;
}