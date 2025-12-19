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
#include "wifi_mgr.h"
#include "hw_config.h"
#include "dev_status.h"
#include "ble.h"
#include "led.h"
#include <stdbool.h>
#include "wifi_mgr.h"

#define TAG "CONFIG_MODE"

// Timing and UI constants
#define CONFIG_MODE_TICK_MS        1000   // Task tick period in milliseconds
#define CONFIG_MODE_HOLD_SECONDS   5      // Seconds to hold the button to enter config mode
#define LED_MAX_LEVEL              255

void config_mode_task(void *pvParameters)
{
    // Track how many seconds the button has been held and LED/UI state
    uint8_t hold_seconds = 0;
    bool in_config_mode = false;
    bool led_on = false;

    const TickType_t tick_delay = pdMS_TO_TICKS(CONFIG_MODE_TICK_MS);

    for(;;)
    {
        dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);
        if (in_config_mode)
        {
            led_set_level(led_on ? LED_MAX_LEVEL : 0,
                          led_on ? LED_MAX_LEVEL : 0,
                          led_on ? 0 : LED_MAX_LEVEL);
            led_on = !led_on;
        }

        const bool button_pressed = (gpio_get_level(BUTTON_GPIO_NUM) == 0);

        if (button_pressed)
        {
            if (++hold_seconds >= CONFIG_MODE_HOLD_SECONDS)
            {
                ESP_LOGI(TAG, "Disabling BLE and entering config mode");
                ble_disable();
                ESP_LOGI(TAG, "Switching WiFi to AP+STA mode for configuration");
                wifi_mgr_set_ap_auto_disable(false);
                wifi_mgr_set_mode(WIFI_MGR_MODE_APSTA);
                wifi_mgr_enable();
                in_config_mode = true;

                hold_seconds = 0; // Avoid repeated triggers while holding
            }
        }
        else
        {
            hold_seconds = 0;
        }

        vTaskDelay(tick_delay);
    }
}

void config_mode_init(void)
{
    xTaskCreateWithCaps(config_mode_task, "config_mode_task", 4096, NULL, 5, NULL, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
}