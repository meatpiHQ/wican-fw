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

#include "cmd_status.h"
#include "cmdline.h"
#include "esp_console.h"

static int cmd_status(int argc, char **argv) 
{
    cmdline_printf("System Status: OK\n");
    return 0;
}

esp_err_t cmd_status_register(void)
{
    const esp_console_cmd_t cmd = {
        .command = "status",
        .help = "Get system status",
        .hint = NULL,
        .func = &cmd_status,
    };
    return esp_console_cmd_register(&cmd);
}