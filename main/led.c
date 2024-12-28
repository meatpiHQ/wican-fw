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

#include "driver/i2c.h"
#include "led.h"
#define LED_I2C_TIMEOUT_MS 1000

// Register addresses
#define AW2023_RSTR        0x00
#define AW2023_GCR1        0x01
#define AW2023_ISR         0x02
#define AW2023_PATST       0x03
#define AW2023_GCR2        0x04
#define AW2023_LCTR        0x30
#define AW2023_LCFG0       0x31
#define AW2023_LCFG1       0x32
#define AW2023_LCFG2       0x33
#define AW2023_PWM0        0x34
#define AW2023_PWM1        0x35
#define AW2023_PWM2        0x36
#define AW2023_LED0T0      0x37
#define AW2023_LED0T1      0x38
#define AW2023_LED0T2      0x39
#define AW2023_LED1T0      0x3A
#define AW2023_LED1T1      0x3B
#define AW2023_LED1T2      0x3C
#define AW2023_LED2T0      0x3D
#define AW2023_LED2T1      0x3E
#define AW2023_LED2T2      0x3F

// Internal pattern structure
typedef struct {
    uint8_t rise_time;    // T1: 0-15
    uint8_t hold_time;    // T2: 0-15
    uint8_t fall_time;    // T3: 0-15
    uint8_t off_time;     // T4: 0-15
    uint8_t delay_time;   // T0: 0-15
    uint8_t repeat_times; // 0-15
} led_pattern_t;

static i2c_port_t led_i2c = I2C_NUM_MAX;

// Time mapping table in milliseconds
static const uint16_t AW2023_TIME_MAP[] = {
    0,      // 0000: 0ms
    130,    // 0001: 130ms
    260,    // 0010: 260ms
    380,    // 0011: 380ms
    510,    // 0100: 510ms
    770,    // 0101: 770ms
    1040,   // 0110: 1.04s
    1600,   // 0111: 1.60s
    2100,   // 1000: 2.10s
    2600,   // 1001: 2.60s
    3100,   // 1010: 3.10s
    4200,   // 1011: 4.20s
    5200,   // 1100: 5.20s
    6200,   // 1101: 6.20s
    7300,   // 1110: 7.30s
    8300    // 1111: 8.30s
};

// I2C communication functions
static esp_err_t AW2023_register_read(uint8_t reg_addr, uint8_t *data, size_t len)
{
    if(led_i2c >= I2C_NUM_MAX)
    {
        return ESP_ERR_INVALID_ARG;
    }
    return i2c_master_write_read_device(led_i2c, 0x45, &reg_addr, 1, data, len, pdMS_TO_TICKS(LED_I2C_TIMEOUT_MS));
}

static esp_err_t AW2023_register_write_byte(uint8_t reg_addr, uint8_t data)
{
    if(led_i2c >= I2C_NUM_MAX)
    {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t write_buf[2] = {reg_addr, data};
    return i2c_master_write_to_device(led_i2c, 0x45, write_buf, sizeof(write_buf), pdMS_TO_TICKS(LED_I2C_TIMEOUT_MS));
}

// Helper function to convert milliseconds to chip time values
static uint8_t ms_to_time_value(uint32_t ms)
{
    uint8_t best_idx = 0;
    uint32_t min_diff = UINT32_MAX;
    
    for (uint8_t i = 0; i < sizeof(AW2023_TIME_MAP)/sizeof(AW2023_TIME_MAP[0]); i++)
    {
        uint32_t diff = abs((int32_t)(ms - AW2023_TIME_MAP[i]));
        if (diff < min_diff)
        {
            min_diff = diff;
            best_idx = i;
        }
    }
    return best_idx;
}

esp_err_t led_get_device_id(uint8_t *id)
{
    if (id == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    
    return AW2023_register_read(AW2023_RSTR, id, 1);
}

void led_init(i2c_port_t i2c_num)
{
    led_i2c = i2c_num;
    AW2023_register_write_byte(0x00, 0x55);    // Software reset
    AW2023_register_write_byte(0x01, 0x01);    // Enable chip
    AW2023_register_write_byte(0x30, 0x07);    // Enable all LEDs
    AW2023_register_write_byte(0x31, 0x03);    // Set LED0 current to max
    AW2023_register_write_byte(0x32, 0x03);    // Set LED1 current to max
    AW2023_register_write_byte(0x33, 0x03);    // Set LED2 current to max
}

esp_err_t led_set_level(uint8_t red, uint8_t green, uint8_t blue)
{
    esp_err_t ret0, ret1, ret2;
    
    ret0 = AW2023_register_write_byte(AW2023_PWM0, red);
    ret1 = AW2023_register_write_byte(AW2023_PWM1, green);
    ret2 = AW2023_register_write_byte(AW2023_PWM2, blue);

    if(ret0 != ESP_OK || ret1 != ESP_OK || ret2 != ESP_OK)
    {
        return ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t led_set_pattern_ms(led_color_t color, const led_pattern_ms_t *pattern_ms)
{
    if ((uint8_t)color > LED_BLUE || pattern_ms == NULL) return ESP_ERR_INVALID_ARG;
    
    // Convert millisecond pattern to chip time values
    led_pattern_t pattern = {
        .rise_time = ms_to_time_value(pattern_ms->rise_time_ms),
        .hold_time = ms_to_time_value(pattern_ms->hold_time_ms),
        .fall_time = ms_to_time_value(pattern_ms->fall_time_ms),
        .off_time = ms_to_time_value(pattern_ms->off_time_ms),
        .delay_time = ms_to_time_value(pattern_ms->delay_time_ms),
        .repeat_times = pattern_ms->repeat_times
    };
    
    esp_err_t ret;
    const uint8_t base_reg = (color == LED_RED) ? AW2023_LED0T0 : 
                            (color == LED_GREEN) ? AW2023_LED1T0 : AW2023_LED2T0;
    
    // Set T1 (rise) and T2 (hold) times
    ret = AW2023_register_write_byte(base_reg, 
        ((pattern.rise_time & 0x0F) << 4) | (pattern.hold_time & 0x0F));
    if (ret != ESP_OK) return ret;
    
    // Set T3 (fall) and T4 (off) times
    ret = AW2023_register_write_byte(base_reg + 1,
        ((pattern.fall_time & 0x0F) << 4) | (pattern.off_time & 0x0F));
    if (ret != ESP_OK) return ret;
    
    // Set T0 (delay) and repeat times
    ret = AW2023_register_write_byte(base_reg + 2,
        ((pattern.delay_time & 0x0F) << 4) | (pattern.repeat_times & 0x0F));
    if (ret != ESP_OK) return ret;

    // Enable pattern mode for this LED
    uint8_t lcfg_reg = AW2023_LCFG0 + (uint8_t)color;
    uint8_t lcfg_val;
    ret = AW2023_register_read(lcfg_reg, &lcfg_val, 1);
    if (ret != ESP_OK) return ret;
    
    lcfg_val |= (1 << 4); // Set MD bit for pattern mode
    return AW2023_register_write_byte(lcfg_reg, lcfg_val);
}

esp_err_t led_disable_pattern(led_color_t color)
{
    if ((uint8_t)color > LED_BLUE) return ESP_ERR_INVALID_ARG;
    
    uint8_t lcfg_reg = AW2023_LCFG0 + (uint8_t)color;
    uint8_t lcfg_val;
    
    esp_err_t ret = AW2023_register_read(lcfg_reg, &lcfg_val, 1);
    if (ret != ESP_OK) return ret;
    
    lcfg_val &= ~(1 << 4);  // Clear MD bit to disable pattern mode
    return AW2023_register_write_byte(lcfg_reg, lcfg_val);
}

esp_err_t led_fast_blink(led_color_t color, uint8_t brightness, bool enable)
{
    if ((uint8_t)color > LED_BLUE) return ESP_ERR_INVALID_ARG;

    if (enable)
    {
        // Fast blinking pattern (130ms on/off)
        led_pattern_ms_t fast_blink = {
            .rise_time_ms = 0,        // Instant on
            .hold_time_ms = 130,      // On for 130ms
            .fall_time_ms = 0,        // Instant off
            .off_time_ms = 0,       // Off for 130ms
            .delay_time_ms = 0,       // No initial delay
            .repeat_times = 0         // Repeat forever
        };

        // Set pattern and brightness
        esp_err_t ret = led_set_pattern_ms(color, &fast_blink);
        if (ret != ESP_OK) return ret;

        // Set LED level
        switch (color)
        {
            case LED_RED:
                return led_set_level(brightness, 0, 0);
            case LED_GREEN:
                return led_set_level(0, brightness, 0);
            case LED_BLUE:
                return led_set_level(0, 0, brightness);
            default:
                return ESP_ERR_INVALID_ARG;
        }
    }
    else
    {
        // Disable pattern
        esp_err_t ret = led_disable_pattern(color);
        if (ret != ESP_OK) return ret;

        // Turn off all LEDs
        return led_set_level(0, 0, 0);
    }
}

esp_err_t led_enable_fade(led_color_t color, bool fade_in, bool fade_out)
{
    if ((uint8_t)color > LED_BLUE) return ESP_ERR_INVALID_ARG;
    
    uint8_t lcfg_reg = AW2023_LCFG0 + (uint8_t)color;
    uint8_t lcfg_val;
    esp_err_t ret = AW2023_register_read(lcfg_reg, &lcfg_val, 1);
    if (ret != ESP_OK) return ret;
    
    lcfg_val &= ~((1 << 6) | (1 << 5));
    if (fade_out) lcfg_val |= (1 << 6);
    if (fade_in) lcfg_val |= (1 << 5);
    
    return AW2023_register_write_byte(lcfg_reg, lcfg_val);
}

esp_err_t led_sync_mode(bool enable)
{
    uint8_t lcfg0_val;
    esp_err_t ret = AW2023_register_read(AW2023_LCFG0, &lcfg0_val, 1);
    if (ret != ESP_OK) return ret;
    
    if (enable)
        lcfg0_val |= (1 << 7);
    else
        lcfg0_val &= ~(1 << 7);
        
    return AW2023_register_write_byte(AW2023_LCFG0, lcfg0_val);
}

esp_err_t led_set_pwm_freq(bool use_125hz)
{
    uint8_t lctr_val;
    esp_err_t ret = AW2023_register_read(AW2023_LCTR, &lctr_val, 1);
    if (ret != ESP_OK) return ret;
    
    if (use_125hz)
        lctr_val |= (1 << 5);
    else
        lctr_val &= ~(1 << 5);
        
    return AW2023_register_write_byte(AW2023_LCTR, lctr_val);
}

esp_err_t led_set_max_current(led_current_t current)
{
    uint8_t gcr2_val;
    esp_err_t ret = AW2023_register_read(AW2023_GCR2, &gcr2_val, 1);
    if (ret != ESP_OK) return ret;
    
    gcr2_val &= ~0x03;
    gcr2_val |= current & 0x03;
    
    return AW2023_register_write_byte(AW2023_GCR2, gcr2_val);
}

uint32_t led_get_actual_time_ms(uint32_t requested_ms)
{
    return AW2023_TIME_MAP[ms_to_time_value(requested_ms)];
}
