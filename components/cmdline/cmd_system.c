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

#include "cmd_system.h"
#include "cmdline.h"
#include "esp_console.h"
#include "argtable3/argtable3.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "esp_chip_info.h"
#include "esp_private/esp_clk.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "sleep_mode.h"
#include "hw_config.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <inttypes.h>

static struct {
    struct arg_lit *voltage;
    struct arg_lit *reboot;
    struct arg_lit *info;
    struct arg_lit *memory;
    struct arg_end *end;
} system_args;

static esp_partition_t *running_partition = NULL;
static esp_app_desc_t running_app_info;

static int cmd_system(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&system_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, system_args.end, argv[0]);
        return 1;
    }

    if (system_args.voltage->count > 0) {
        float voltage;
        if (sleep_mode_get_voltage(&voltage) != ESP_OK) {
            cmdline_printf("Error: Failed to read voltage\n");
            return 1;
        }
        cmdline_printf("System Voltage: %.2f V\n", voltage);
        cmdline_printf("OK\n");
        return 0;
    }

    if (system_args.reboot->count > 0) {
        cmdline_printf("System will reboot now...\n");
        vTaskDelay(pdMS_TO_TICKS(2000));
        esp_restart();
        return 0;
    }

    if (system_args.info->count > 0) {
        char device_id[13];
        if (hw_config_get_device_id(device_id) != ESP_OK) {
            cmdline_printf("Error: Failed to read device ID\n");
            return 1;
        }
        cmdline_printf("Device ID: %s\n", device_id);

        if (running_partition != NULL) {
            cmdline_printf("Running Partition: %s\n", running_partition->label);
            cmdline_printf("App Version: %s\n", running_app_info.version);
            cmdline_printf("Project Name: %s\n", running_app_info.project_name);
            cmdline_printf("Build Time: %s %s\n", running_app_info.date, running_app_info.time);
            cmdline_printf("IDF Version: %s\n", running_app_info.idf_ver);
        }
        
        esp_chip_info_t chip_info;
        esp_chip_info(&chip_info);
        cmdline_printf("Chip Model: %s\n", 
            chip_info.model == CHIP_ESP32 ? "ESP32" : 
            chip_info.model == CHIP_ESP32S2 ? "ESP32-S2" : 
            chip_info.model == CHIP_ESP32S3 ? "ESP32-S3" : 
            chip_info.model == CHIP_ESP32C3 ? "ESP32-C3" : "Unknown");
        cmdline_printf("CPU Cores: %d\n", chip_info.cores);
        cmdline_printf("CPU Frequency: %d MHz\n", esp_clk_cpu_freq() / 1000000);
        
        unsigned major_rev = chip_info.revision / 100;
        unsigned minor_rev = chip_info.revision % 100;
        uint32_t flash_size;

        cmdline_printf("Chip Revision: v%d.%d\n", major_rev, minor_rev);
        if(esp_flash_get_size(NULL, &flash_size) != ESP_OK) {
            cmdline_printf("Get flash size failed\n");
            return 1;
        }
    
        cmdline_printf("Flash Size: %ld MB\n", flash_size / (uint32_t)(1024 * 1024));
        cmdline_printf("Minimum free heap size: %" PRIu32 " bytes\n", esp_get_minimum_free_heap_size());
        
        int64_t uptime_us = esp_timer_get_time();
        int uptime_s = uptime_us / 1000000;
        int uptime_m = uptime_s / 60;
        int uptime_h = uptime_m / 60;
        int uptime_d = uptime_h / 24;
        
        cmdline_printf("System Uptime: %dd %dh %dm %ds\n", 
            uptime_d, 
            uptime_h % 24, 
            uptime_m % 60, 
            uptime_s % 60);
        
        cmdline_printf("OK\n");
        return 0;
    }

    if (system_args.memory->count > 0) {
        uint32_t free_internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t free_psram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
        uint32_t largest_internal = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t largest_psram = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);
        uint32_t min_free_internal = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t min_free_psram = heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM);

        multi_heap_info_t info;
        heap_caps_get_info(&info, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t total_internal = info.total_free_bytes + info.total_allocated_bytes;

        cmdline_printf("RAM Free: %lu bytes\n", free_internal);
        cmdline_printf("RAM Largest block: %lu bytes\n", largest_internal);
        cmdline_printf("RAM Total: %lu bytes\n", total_internal);
        cmdline_printf("RAM Min free ever: %lu bytes\n", min_free_internal);
        cmdline_printf("PSRAM Free: %lu bytes\n", free_psram);
        cmdline_printf("PSRAM Largest block: %lu bytes\n", largest_psram);
        cmdline_printf("PSRAM Min free ever: %lu bytes\n", min_free_psram);
        cmdline_printf("OK\n");
        return 0;
    }

    cmdline_printf("Error: No valid subcommand\n");
    return 1;
}

esp_err_t cmd_system_register(void)
{
    running_partition = esp_ota_get_running_partition();
    esp_ota_get_partition_description(running_partition, &running_app_info);

    system_args.voltage = arg_lit0("v", "voltage", "Get system voltage");
    system_args.reboot = arg_lit0("r", "reboot", "Reboot system");
    system_args.info = arg_lit0("i", "info", "Get system information including device ID");
    system_args.memory = arg_lit0("m", "memory", "Get heap memory info");
    system_args.end = arg_end(5);

    const esp_console_cmd_t cmd = {
        .command = "system",
        .help = "System control and status",
        .hint = "Usage: system [-v|-r|-i|-m]",
        .func = &cmd_system,
        .argtable = &system_args
    };
    return esp_console_cmd_register(&cmd);
}