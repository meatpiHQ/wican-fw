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

#include "cmd_sdcard.h"
#include "cmdline.h"
#include "esp_console.h"
#include "argtable3/argtable3.h"
#include "sdcard.h"

static struct {
    struct arg_lit *info;
    struct arg_lit *test;
    struct arg_end *end;
} sdcard_args;

static int cmd_sdcard(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&sdcard_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, sdcard_args.end, argv[0]);
        return 1;
    }

    if (sdcard_args.info->count > 0) {
        sdmmc_card_info_t card_info;
        if (sdcard_get_info(&card_info) != ESP_OK) {
            cmdline_printf("Error: Failed to read SD card info\n");
            return 1;
        }
        cmdline_printf("SD Card Info:\n");
        cmdline_printf("Name: %s\n", card_info.name);
        cmdline_printf("Type: %s\n", card_info.type == CARD_TYPE_SDHC ? "SDHC/SDXC" : 
                            card_info.type == CARD_TYPE_MMC ? "MMC" : 
                            card_info.type == CARD_TYPE_SDIO ? "SDIO" : "SDSC");
        cmdline_printf("Capacity: %.2f GB\n", ((float)card_info.capacity/1024));
        cmdline_printf("Sector Size: %d bytes\n", card_info.sector_size);
        cmdline_printf("Speed: %lu KHz\n", card_info.speed);
        cmdline_printf("OK\n");
        return 0;
    }

    if (sdcard_args.test->count > 0) {
        if (sdcard_test_rw() != ESP_OK) {
            cmdline_printf("Error: SD card test failed\n");
            return 1;
        }
        cmdline_printf("SD card test passed successfully\n");
        cmdline_printf("OK\n");
        return 0;
    }

    cmdline_printf("Error: No valid subcommand\n");
    return 1;
}

esp_err_t cmd_sdcard_register(void)
{
    sdcard_args.info = arg_lit0("i", "info", "Get SD card information");
    sdcard_args.test = arg_lit0("t", "test", "Test SD card read/write");
    sdcard_args.end = arg_end(3);

    const esp_console_cmd_t cmd = {
        .command = "sdcard",
        .help = "SD card control and status",
        .hint = "Usage: sdcard [-i|-t]",
        .func = &cmd_sdcard,
        .argtable = &sdcard_args
    };
    return esp_console_cmd_register(&cmd);
}