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
#include "dev_status.h"

#define TAG "LED_IND"

#define LED_IND_TICK_MS         250
// Hold red across the FLASH_ACTIVE_BIT gaps between the ~128 KB 'X' chunks of
// one logical fast-read, so a long ROM read shows steady red-blink, not flicker.
#define LED_IND_RED_HOLD_US     (1500LL * 1000LL)
#define LED_IND_RED_BRIGHTNESS  255
#define LED_IND_BLUE_BRIGHTNESS 200

typedef enum {
    IND_UNKNOWN = 0,   // pre-first-paint; forces an initial repaint
    IND_IDLE,
    IND_FLASH_RED,
    IND_DATALOG_BLUE,
    IND_DEFERRED,      // a foreign owner (MIC update, sleep, config mode) holds the LED
} ind_state_t;

static SemaphoreHandle_t s_paint_mutex = NULL;
static volatile ind_state_t s_state = IND_UNKNOWN;
// Nested foreign-owner holds (MIC3624 update, sleep paths, config mode).
// Guarded by s_paint_mutex once it exists; boot-time calls before init are
// single-task.
static volatile int8_t s_suspend_count = 0;

// The allowed software-blink half-periods, ~19 Hz to ~2.4 Hz. The AW2023
// pattern engine bottoms out at 130 ms per phase, so the blink is timed in
// led_indicator_task (1 kHz RTOS tick) and the chip is driven as plain PWM —
// that is what allows rates the hardware time table cannot express. This
// table is mirrored by LED_BLINK_STEPS in main/web/src/main.js.
int32_t led_indicator_snap_rate_ms(int32_t ms)
{
    static const int32_t allowed[] = {26, 52, 76, 102, 154, 208};
    int32_t best = allowed[0];
    int32_t best_diff = (ms > best) ? (ms - best) : (best - ms);
    for (size_t i = 1; i < sizeof(allowed)/sizeof(allowed[0]); i++)
    {
        int32_t diff = (ms > allowed[i]) ? (ms - allowed[i]) : (allowed[i] - ms);
        if (diff < best_diff)
        {
            best_diff = diff;
            best = allowed[i];
        }
    }
    return best;
}

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

// Paint one phase of the software blink: the state's channel at its brightness
// or all channels dark. Blink amplitude and timing both live here — the AW2023
// runs in plain PWM mode, no hardware pattern.
static void ind_paint(ind_state_t state, bool phase_on)
{
    switch (state)
    {
        case IND_FLASH_RED:
            led_set_level(phase_on ? LED_IND_RED_BRIGHTNESS : 0, 0, 0);
            break;
        case IND_DATALOG_BLUE:
            led_set_level(0, 0, phase_on ? LED_IND_BLUE_BRIGHTNESS : 0);
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
    bool phase_on = false;

    for (;;)
    {
        // Painting is gated on the device being awake, same as config_mode_task;
        // the sleep paths additionally suspend() before writing the LED off.
        dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);

        // Already normalized to the allowed table by config_server_load_cfg.
        const uint32_t rate_ms = (uint32_t)config_server_get_led_blink_ms();

        // Decide and paint under the mutex: once led_indicator_suspend()
        // returns, this task must not touch the LED. The predicates are all
        // lock-free flag reads, so holding the mutex across them is cheap.
        xSemaphoreTake(s_paint_mutex, portMAX_DELAY);
        ind_state_t desired;
        if (s_suspend_count > 0)
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

        // dev_status_is_awake() is a backstop for any sleep path without a
        // suspend() hook — never paint over a LED the sleep code turned off.
        if (desired != IND_DEFERRED && dev_status_is_awake())
        {
            if (desired != s_state)
            {
                // The MD (pattern-mode) bit survives led_set_level: clear both
                // channels' patterns when (re)taking the LED so a leftover
                // hardware pattern can't fight the software blink.
                led_disable_pattern(LED_RED);
                led_disable_pattern(LED_BLUE);
                phase_on = true;   // enter blink states visibly on
                ind_paint(desired, phase_on);
            }
            else if (desired == IND_FLASH_RED || desired == IND_DATALOG_BLUE)
            {
                phase_on = !phase_on;
                ind_paint(desired, phase_on);
            }
        }
        const ind_state_t prev = s_state;
        s_state = desired;
        xSemaphoreGive(s_paint_mutex);

        if (desired != prev)
        {
            ESP_LOGI(TAG, "state %s -> %s (rate %lums)",
                     ind_state_str(prev), ind_state_str(desired), (unsigned long)rate_ms);
        }
        // While blinking, the tick IS the half-period; rate changes from the
        // config getter take effect on the next toggle without a reprogram.
        const bool fast_tick = (desired == IND_FLASH_RED || desired == IND_DATALOG_BLUE);
        vTaskDelay(pdMS_TO_TICKS(fast_tick ? rate_ms : LED_IND_TICK_MS));
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
    if (s_suspend_count > 0)
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
