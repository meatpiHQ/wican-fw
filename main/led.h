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

#ifndef LED_h
#define LED_h
#include "driver/i2c.h"

#define LED_I2C_TIMEOUT_MS          100

// LED colors
typedef enum {
    LED_RED = 0,
    LED_GREEN = 1,
    LED_BLUE = 2
} led_color_t;

// LED current options
typedef enum {
    LED_CURRENT_5MA = 0x02,   // 10
    LED_CURRENT_10MA = 0x03,  // 11
    LED_CURRENT_15MA = 0x00,  // 00 (default)
    LED_CURRENT_30MA = 0x01   // 01
} led_current_t;

// LED pattern timing structure with milliseconds
typedef struct {
    uint32_t rise_time_ms;    // Time to fade in
    uint32_t hold_time_ms;    // Time to stay on
    uint32_t fall_time_ms;    // Time to fade out
    uint32_t off_time_ms;     // Time to stay off
    uint32_t delay_time_ms;   // Initial delay before starting
    uint8_t repeat_times;     // 0 = infinite, 1-15 = number of repeats
} led_pattern_ms_t;

// Core functions
void led_init(i2c_port_t i2c_num);
esp_err_t led_set_level(uint8_t red, uint8_t green, uint8_t blue);
esp_err_t led_set_max_current(led_current_t current);

// Pattern control functions
esp_err_t led_set_pattern_ms(led_color_t color, const led_pattern_ms_t *pattern_ms);
esp_err_t led_disable_pattern(led_color_t color);
esp_err_t led_fast_blink(led_color_t color, uint8_t brightness, bool enable);

// Effect control functions
esp_err_t led_enable_fade(led_color_t color, bool fade_in, bool fade_out);
esp_err_t led_sync_mode(bool enable);
esp_err_t led_set_pwm_freq(bool use_125hz);

// Utility function
uint32_t led_get_actual_time_ms(uint32_t requested_ms);

esp_err_t led_get_device_id(uint8_t *id);

#endif
