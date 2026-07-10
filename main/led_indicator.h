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

#ifndef LED_INDICATOR_H
#define LED_INDICATOR_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Activity indicators for the RGB status LED (issue #19): fast-blink RED while
 * a flashing operation holds FLASH_ACTIVE_BIT, fast-blink BLUE while a CSV
 * datalog session is open (and not parked), solid idle blue otherwise.
 *
 * All indicator LED I2C happens on this module's own low-priority task — never
 * on can_tx_task, where the flash codecs run inline and a single AW2023
 * transaction can block up to 1 s (led.c redefines LED_I2C_TIMEOUT_MS). */

// Idle baseline color (solid blue). Shared with app_main's end-of-boot paint
// so "what idle looks like" has a single owner.
#define LED_IND_IDLE_R 0
#define LED_IND_IDLE_G 0
#define LED_IND_IDLE_B 200

// Create the indicator task. Call once, after led_init() and after the boot
// LED baseline is set (end of app_main).
void led_indicator_init(void);

// Pause/resume indicator painting around a foreign LED owner (MIC3624 chip
// update's inline red blink, sleep paths writing the LED off, config mode's
// 1 Hz alternation). suspend() takes the paint mutex, so it returns only after
// any in-progress repaint finished — the caller then owns the LED. Calls nest;
// resume() triggers a repaint (which restores the idle color if nothing is
// active). Safe to call before init.
void led_indicator_suspend(void);
void led_indicator_resume(void);

// Snap a blink half-period (ms) to the nearest entry of the allowed table
// (26–208 ms, ~19 Hz to ~2.4 Hz — see led_indicator.c for the values and why).
// config_server_load_cfg() normalizes the stored led_blink_ms with this once;
// the table is mirrored by LED_BLINK_STEPS in main/web/src/main.js.
int32_t led_indicator_snap_rate_ms(int32_t ms);

// Current indicator state for /check_status:
// "flash_red" | "datalog_blue" | "idle" | "deferred"
const char *led_indicator_get_state_str(void);

#ifdef __cplusplus
}
#endif

#endif // LED_INDICATOR_H
