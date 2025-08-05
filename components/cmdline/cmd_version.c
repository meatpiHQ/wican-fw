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

#include "cmd_version.h"
#include "cmdline.h"
#include "esp_console.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"

static int cmd_version(int argc, char **argv)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_app_desc_t running_app_info;

    if (esp_ota_get_partition_description(running, &running_app_info) != ESP_OK) {
        cmdline_printf("Error: Failed to get partition info\n");
        return 1;
    }

    cmdline_printf("Version: %s\n", running_app_info.version);
    cmdline_printf("Project Name: %s\n", running_app_info.project_name);
    cmdline_printf("Build Time: %s %s\n", running_app_info.date, running_app_info.time);
    cmdline_printf("IDF Version: %s\n", running_app_info.idf_ver);
    cmdline_printf("Running Partition: %s\n", running->label);
    cmdline_printf("OK\n");
    return 0;
}

esp_err_t cmd_version_register(void)
{
    const esp_console_cmd_t cmd = {
        .command = "version",
        .help = "Get firmware version",
        .hint = NULL,
        .func = &cmd_version,
    };
    return esp_console_cmd_register(&cmd);
}