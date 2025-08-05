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

#include "cmd_imu.h"
#include "cmdline.h"
#include "esp_console.h"
#include "argtable3/argtable3.h"
#include "imu.h"

static struct {
    struct arg_lit *id;
    struct arg_end *end;
} imu_args;

static int cmd_imu(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&imu_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, imu_args.end, argv[0]);
        return 1;
    }

    if (imu_args.id->count > 0) {
        uint8_t id;
        if (imu_get_device_id(&id) != ESP_OK) {
            cmdline_printf("Error: Failed to read IMU ID\n");
            return 1;
        }
        cmdline_printf("IMU Device ID: 0x%02X\n", id);
        cmdline_printf("OK\n");
        return 0;
    }

    cmdline_printf("Error: No valid subcommand\n");
    return 1;
}

esp_err_t cmd_imu_register(void)
{
    imu_args.id = arg_lit0("i", "id", "Get IMU device ID");
    imu_args.end = arg_end(2);

    const esp_console_cmd_t cmd = {
        .command = "imu",
        .help = "IMU control and status",
        .hint = "Usage: imu [-i]",
        .func = &cmd_imu,
        .argtable = &imu_args
    };
    return esp_console_cmd_register(&cmd);
}