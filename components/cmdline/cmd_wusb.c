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

#include "cmd_wusb.h"
#include "cmdline.h"
#include "esp_console.h"
#include "argtable3/argtable3.h"
#include "wusb3801.h"

static struct {
    struct arg_lit *cc_status;
    struct arg_lit *id;
    struct arg_end *end;
} wusb_args;

static int cmd_wusb(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&wusb_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, wusb_args.end, argv[0]);
        return 1;
    }

    if (wusb_args.id->count > 0) {
        uint8_t id = wusb3801_get_dev_id();
        cmdline_printf("WUSB3801 Device ID: 0x%02X\n", id);
        cmdline_printf("OK\n");
        return 0;
    }

    if (wusb_args.cc_status->count > 0) {
        uint8_t cc_stat = wusb3801_get_cc_stat();
        cmdline_printf("WUSB3801 CC Status: 0x%02X\n", cc_stat);
        cmdline_printf("OK\n");
        return 0;
    }

    cmdline_printf("Error: No valid subcommand\n");
    return 1;
}

esp_err_t cmd_wusb_register(void)
{
    wusb_args.cc_status = arg_lit0("c", "cc", "Get CC status");
    wusb_args.id = arg_lit0("i", "id", "Get WUSB device ID");
    wusb_args.end = arg_end(3);

    const esp_console_cmd_t cmd = {
        .command = "wusb",
        .help = "WUSB3801 USB-C controller",
        .hint = "Usage: wusb [-i|-c]",
        .func = &cmd_wusb,
        .argtable = &wusb_args
    };
    return esp_console_cmd_register(&cmd);
}