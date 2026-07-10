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
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "led.h"
#include "led_indicator.h"
#include "can.h"
#include "csv_logger.h"
#include "config_server.h"
#include "config_mode.h"
#include "dev_status.h"

#define TAG "LED_IND"

#define LED_IND_TICK_MS         250
// Hold red across the FLASH_ACTIVE_BIT gaps between the ~128 KB 'X' chunks of
// one logical fast-read, so a long ROM read shows steady red-blink, not flicker.
#define LED_IND_RED_HOLD_US     (1500LL * 1000LL)
#define LED_IND_RED_BRIGHTNESS  255
#define LED_IND_BLUE_BRIGHTNESS 200
// Idle baseline: same solid blue app_main sets at end of boot.
#define LED_IND_IDLE_R 0
#define LED_IND_IDLE_G 0
#define LED_IND_IDLE_B 200

typedef enum {
    IND_UNKNOWN = 0,   // pre-first-paint; forces an initial repaint
    IND_IDLE,
    IND_FLASH_RED,
    IND_DATALOG_BLUE,
    IND_DEFERRED,      // a foreign owner (MIC update, sleep, config mode) holds the LED
} ind_state_t;

static SemaphoreHandle_t s_paint_mutex = NULL;
static volatile ind_state_t s_state = IND_UNKNOWN;
// Nested foreign-owner holds (MIC3624 update, sleep paths). Guarded by
// s_paint_mutex once it exists; boot-time calls before init are single-task.
static volatile int8_t s_suspend_count = 0;
static bool s_suspended_early = false;   // suspend() arrived before init

static const char *ind_state_str(ind_state_t st)
{
    switch (st)
    {
        case IND_FLASH_RED:     return "flash_red";
        case IND_DATALOG_BLUE:  return "datalog_blue";
        case IND_DEFERRED:      return "deferred";
        case IND_IDLE:
        default:                return "idle";
    }
}

const char *led_indicator_get_state_str(void)
{
    return ind_state_str(s_state);
}

void led_indicator_suspend(void)
{
    if (s_paint_mutex == NULL)
    {
        // Pre-init (boot-time MIC update): no task is painting yet.
        s_suspended_early = true;
        s_suspend_count++;
        return;
    }
    // Blocks until any in-progress repaint finished; after this returns the
    // indicator task will not touch the LED until led_indicator_resume().
    xSemaphoreTake(s_paint_mutex, portMAX_DELAY);
    s_suspend_count++;
    xSemaphoreGive(s_paint_mutex);
}

void led_indicator_resume(void)
{
    if (s_paint_mutex == NULL)
    {
        if (s_suspend_count > 0) s_suspend_count--;
        return;
    }
    xSemaphoreTake(s_paint_mutex, portMAX_DELAY);
    if (s_suspend_count > 0) s_suspend_count--;
    // Force a repaint on the next tick so the idle color comes back even if
    // the foreign owner left the LED dark (pre-existing MIC-update behavior).
    s_state = IND_UNKNOWN;
    xSemaphoreGive(s_paint_mutex);
}

// Program one indicator blink: symmetric hold/off at the user-configured rate,
// explicit nonzero off time (led_fast_blink programs off=0, which can render
// near-solid). Amplitude comes from the PWM level written after the pattern.
static esp_err_t ind_start_blink(led_color_t color, uint8_t brightness, uint32_t rate_ms)
{
    const led_pattern_ms_t blink = {
        .rise_time_ms = 0,
        .hold_time_ms = rate_ms,
        .fall_time_ms = 0,
        .off_time_ms  = rate_ms,
        .delay_time_ms = 0,
        .repeat_times = 0,   // forever
    };
    esp_err_t ret = led_set_pattern_ms(color, &blink);
    if (ret != ESP_OK) return ret;
    switch (color)
    {
        case LED_RED:   return led_set_level(brightness, 0, 0);
        case LED_GREEN: return led_set_level(0, brightness, 0);
        case LED_BLUE:
        default:        return led_set_level(0, 0, brightness);
    }
}

// Apply `next`. The MD (pattern-mode) bit survives led_set_level, so every
// transition explicitly disables the patterns it is leaving before writing the
// next state — otherwise the old channel keeps blinking the new color.
static void ind_apply(ind_state_t next, uint32_t rate_ms)
{
    led_disable_pattern(LED_RED);
    led_disable_pattern(LED_BLUE);

    switch (next)
    {
        case IND_FLASH_RED:
            ind_start_blink(LED_RED, LED_IND_RED_BRIGHTNESS, rate_ms);
            break;
        case IND_DATALOG_BLUE:
            ind_start_blink(LED_BLUE, LED_IND_BLUE_BRIGHTNESS, rate_ms);
            break;
        case IND_IDLE:
        default:
            led_set_level(LED_IND_IDLE_R, LED_IND_IDLE_G, LED_IND_IDLE_B);
            break;
    }
}

static void led_indicator_task(void *pvParameters)
{
    int64_t red_hold_until_us = 0;
    uint32_t applied_rate_ms = 0;

    for (;;)
    {
        // Painting is gated on the device being awake, same as config_mode_task;
        // the sleep paths additionally suspend() before writing the LED off.
        dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);

        const uint32_t rate_ms = (uint32_t)config_server_get_led_blink_ms();

        ind_state_t desired;
        if (s_suspend_count > 0 || config_mode_active())
        {
            desired = IND_DEFERRED;
        }
        else if (can_flash_active())
        {
            desired = IND_FLASH_RED;
            red_hold_until_us = esp_timer_get_time() + LED_IND_RED_HOLD_US;
        }
        else if (esp_timer_get_time() < red_hold_until_us)
        {
            desired = IND_FLASH_RED;   // chunk-gap hysteresis
        }
        else if (csv_logger_session_active() && !can_should_park())
        {
            desired = IND_DATALOG_BLUE;
        }
        else
        {
            desired = IND_IDLE;
        }

        const bool blinking = (desired == IND_FLASH_RED || desired == IND_DATALOG_BLUE);
        if (desired != s_state || (blinking && rate_ms != applied_rate_ms))
        {
            xSemaphoreTake(s_paint_mutex, portMAX_DELAY);
            // Re-check under the mutex: a suspend() may have raced in, and the
            // foreign owner (e.g. sleep writing the LED off) must not be
            // repainted over.
            if (s_suspend_count > 0)
            {
                desired = IND_DEFERRED;
            }
            if (desired != IND_DEFERRED && dev_status_is_awake())
            {
                ind_apply(desired, rate_ms);
                applied_rate_ms = rate_ms;
            }
            if (desired != s_state)
            {
                ESP_LOGI(TAG, "state %s -> %s (rate %lums)",
                         ind_state_str(s_state), ind_state_str(desired), (unsigned long)rate_ms);
                s_state = desired;
            }
            xSemaphoreGive(s_paint_mutex);
        }

        vTaskDelay(pdMS_TO_TICKS(LED_IND_TICK_MS));
    }
}

void led_indicator_init(void)
{
    if (s_paint_mutex != NULL)
    {
        return;
    }
    SemaphoreHandle_t mutex = xSemaphoreCreateMutex();
    if (mutex == NULL)
    {
        ESP_LOGE(TAG, "Failed to create paint mutex");
        return;
    }
    // Publish the mutex before the task exists (csv_logger boot-race lesson:
    // publish state before creating the task that reads it).
    s_paint_mutex = mutex;
    if (s_suspended_early)
    {
        ESP_LOGI(TAG, "starting with %d pre-init suspend hold(s)", s_suspend_count);
    }
    if (xTaskCreateWithCaps(led_indicator_task, "led_ind_task", 3072, NULL, 2, NULL,
                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS)
    {
        ESP_LOGE(TAG, "Failed to create led_ind_task");
        vSemaphoreDelete(mutex);
        s_paint_mutex = NULL;
    }
}
