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

#include "cmd_rtc.h"
#include "cmdline.h"
#include "esp_console.h"
#include "argtable3/argtable3.h"
#include "rtcm.h"

static struct {
    struct arg_lit *sync;
    struct arg_lit *read;
    struct arg_lit *id;
    struct arg_end *end;
} rtc_args;

static int cmd_rtc(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&rtc_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, rtc_args.end, argv[0]);
        return 1;
    }

    if (rtc_args.sync->count > 0) {
        if (rtcm_sync_internet_time() != ESP_OK) {
            cmdline_printf("Error: Failed to sync time\n");
            return 1;
        }
        cmdline_printf("Time synchronized successfully\n");
        cmdline_printf("OK\n");
        return 0;
    }
    else if (rtc_args.read->count > 0) {
        uint8_t hour, min, sec;
        uint8_t year, month, day, weekday;
        
        esp_err_t ret_time = rtcm_get_time(&hour, &min, &sec);
        esp_err_t ret_date = rtcm_get_date(&year, &month, &day, &weekday);

        if (ret_time == ESP_OK && ret_date == ESP_OK) {
            cmdline_printf("20%02X-%02X-%02X %02X:%02X:%02X (Day %d)\n", 
                   year, month, day, hour, min, sec, weekday);
            cmdline_printf("OK\n");
            return 0;
        } else {
            cmdline_printf("Error: Failed to read RTC time/date\n");
            return 1;
        }
    }
    else if (rtc_args.id->count > 0) {
        uint8_t id;
        if (rtcm_get_device_id(&id) != ESP_OK) { 
            cmdline_printf("Error: Failed to read RTC module ID\n");
            return 1;
        }
        cmdline_printf("RTC Module Device ID: 0x%02X\n", id);
        cmdline_printf("OK\n");
        return 0;
    }

    cmdline_printf("Error: No valid subcommand\n");
    return 1;
}

esp_err_t cmd_rtc_register(void)
{
    rtc_args.sync = arg_lit0("s", "sync", "Sync time from internet");
    rtc_args.read = arg_lit0("r", "read", "Read current time and date");
    rtc_args.id = arg_lit0("i", "id", "Get RTC module device ID");
    rtc_args.end = arg_end(3);

    const esp_console_cmd_t cmd = {
        .command = "rtc",
        .help = "RTC module control",
        .hint = "Usage: rtc [-s|-r|-i]",
        .func = &cmd_rtc,
        .argtable = &rtc_args
    };
    return esp_console_cmd_register(&cmd);
}