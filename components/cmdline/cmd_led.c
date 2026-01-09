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

#include "cmd_led.h"
#include "cmdline.h"
#include "esp_console.h"
#include "argtable3/argtable3.h"
#include "led.h"

static struct {
    struct arg_lit *id;
    struct arg_lit *color;
    struct arg_int *rgb;
    struct arg_lit *blink;
    struct arg_end *end;
} led_args;

static bool is_valid_u8_int(int value)
{
    return value >= 0 && value <= 255;
}

static int cmd_led(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&led_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, led_args.end, argv[0]);
        return 1;
    }

    if (led_args.id->count > 0) {
        uint8_t id;
        if (led_get_device_id(&id) != ESP_OK) {
            cmdline_printf("Error: Failed to read LED driver ID\n");
            return 1;
        }
        cmdline_printf("LED Driver ID: 0x%02X\n", id);
        cmdline_printf("OK\n");
        return 0;
    }

    if (led_args.color->count > 0) {
        if (led_args.rgb->count != 3) {
            cmdline_printf("Error: Missing RGB values. Usage: led -c <r> <g> <b> [-b]\n");
            return 1;
        }

        int red = led_args.rgb->ival[0];
        int green = led_args.rgb->ival[1];
        int blue = led_args.rgb->ival[2];

        if (!is_valid_u8_int(red) || !is_valid_u8_int(green) || !is_valid_u8_int(blue)) {
            cmdline_printf("Error: Color values must be in range 0-255\n");
            return 1;
        }

        if (led_args.blink->count > 0) {
            // Blink each enabled channel with the same timing.
            led_pattern_ms_t blink_pattern = {
                .rise_time_ms = 0,
                .hold_time_ms = 130,
                .fall_time_ms = 0,
                .off_time_ms = 130,
                .delay_time_ms = 0,
                .repeat_times = 0
            };

            if (red > 0) {
                if (led_set_pattern_ms(LED_RED, &blink_pattern) != ESP_OK) {
                    cmdline_printf("Error: Failed to enable red blink pattern\n");
                    return 1;
                }
            } else {
                (void)led_disable_pattern(LED_RED);
            }

            if (green > 0) {
                if (led_set_pattern_ms(LED_GREEN, &blink_pattern) != ESP_OK) {
                    cmdline_printf("Error: Failed to enable green blink pattern\n");
                    return 1;
                }
            } else {
                (void)led_disable_pattern(LED_GREEN);
            }

            if (blue > 0) {
                if (led_set_pattern_ms(LED_BLUE, &blink_pattern) != ESP_OK) {
                    cmdline_printf("Error: Failed to enable blue blink pattern\n");
                    return 1;
                }
            } else {
                (void)led_disable_pattern(LED_BLUE);
            }

        } else {
            // Solid color: disable any existing patterns.
            (void)led_disable_pattern(LED_RED);
            (void)led_disable_pattern(LED_GREEN);
            (void)led_disable_pattern(LED_BLUE);
        }

        if (led_set_level((uint8_t)red, (uint8_t)green, (uint8_t)blue) != ESP_OK) {
            cmdline_printf("Error: Failed to set LED color\n");
            return 1;
        }

        cmdline_printf("OK\n");
        return 0;
    }

    cmdline_printf("Error: No valid subcommand\n");
    return 1;
}

esp_err_t cmd_led_register(void)
{
    led_args.id = arg_lit0("i", "id", "Get LED driver device ID");
    led_args.color = arg_lit0("c", "color", "Set LED RGB color using the following <r> <g> <b>");
    led_args.rgb = arg_intn(NULL, NULL, "<r> <g> <b>", 0, 3, "RGB values (0-255 each)");
    led_args.blink = arg_lit0("b", "blink", "Blink the selected color channels");
    led_args.end = arg_end(5);

    const esp_console_cmd_t cmd = {
        .command = "led",
        .help = "LED driver control",
        .hint = "Usage: led [-i] [-c <r> <g> <b> [-b]]",
        .func = &cmd_led,
        .argtable = &led_args
    };
    return esp_console_cmd_register(&cmd);
}