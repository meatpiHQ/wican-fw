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

#include "cmd_factoryreset.h"
#include "cmdline.h"
#include "esp_console.h"
#include "argtable3/argtable3.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "filesystem.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>

static const char *TAG = "cmd_factoryreset";

// Factory reset state
static bool factory_reset_pending = false;
static uint64_t factory_reset_timestamp = 0;
#define FACTORY_RESET_TIMEOUT_MS 60000  // 60 seconds timeout

static struct {
    struct arg_lit *confirm;
    struct arg_end *end;
} factoryreset_args;

static int cmd_factoryreset(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&factoryreset_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, factoryreset_args.end, argv[0]);
        return 1;
    }

    // Check if factory reset is pending and confirm was provided
    if (factoryreset_args.confirm->count > 0) {
        if (!factory_reset_pending) {
            cmdline_printf("Error: No factory reset operation pending.\n");
            cmdline_printf("Please run 'factoryreset' first to initiate the process.\n");
            return 1;
        }
        
        // Check if timeout has expired
        uint64_t current_time = esp_timer_get_time() / 1000;  // Convert to milliseconds
        if ((current_time - factory_reset_timestamp) > FACTORY_RESET_TIMEOUT_MS) {
            factory_reset_pending = false;
            cmdline_printf("Error: Factory reset confirmation timeout expired.\n");
            cmdline_printf("Please run 'factoryreset' again to restart the process.\n");
            return 1;
        }
        
        cmdline_printf("Starting factory reset...\n");
        
        // Delete all configuration files
        cmdline_printf("Deleting configuration files...\n");
        filesystem_delete_config_files();
        
        // Clear the pending flag
        factory_reset_pending = false;
        
        cmdline_printf("Factory reset completed successfully.\n");
        cmdline_printf("System will reboot in 3 seconds...\n");
        
        // Give some time for the message to be sent
        vTaskDelay(pdMS_TO_TICKS(3000));
        
        // Restart the system
        esp_restart();
        
        return 0;
    }
    
    // No confirm flag - initiate factory reset process
    if (factory_reset_pending) {
        uint64_t current_time = esp_timer_get_time() / 1000;
        uint32_t remaining_time = (FACTORY_RESET_TIMEOUT_MS - (current_time - factory_reset_timestamp)) / 1000;
        
        if ((current_time - factory_reset_timestamp) > FACTORY_RESET_TIMEOUT_MS) {
            factory_reset_pending = false;
            cmdline_printf("Previous factory reset confirmation expired.\n");
        } else {
            cmdline_printf("Factory reset already pending. You have %lu seconds to confirm.\n", remaining_time);
            cmdline_printf("Run 'factoryreset --confirm' to proceed or wait for timeout.\n");
            return 0;
        }
    }
    
    // Set factory reset pending
    factory_reset_pending = true;
    factory_reset_timestamp = esp_timer_get_time() / 1000;
    
    cmdline_printf("=== FACTORY RESET WARNING ===\n");
    cmdline_printf("This will permanently delete ALL configuration files including:\n");
    cmdline_printf("  - Device configuration\n");
    cmdline_printf("  - Vehicle profiles\n");
    cmdline_printf("  - Network settings\n");
    cmdline_printf("  - All user data\n");
    cmdline_printf("\n");
    cmdline_printf("This action CANNOT be undone!\n");
    cmdline_printf("\n");
    cmdline_printf("To proceed, run: factoryreset --confirm\n");
    cmdline_printf("You have 60 seconds to confirm.\n");
    cmdline_printf("============================\n");
    
    return 0;
}

esp_err_t cmd_factoryreset_register(void)
{
    factoryreset_args.confirm = arg_lit0("c", "confirm", "Confirm pending factory reset operation");
    factoryreset_args.end = arg_end(2);

    const esp_console_cmd_t cmd = {
        .command = "factoryreset",
        .help = "Perform factory reset - deletes all configuration files and reboots",
        .hint = "Usage: factoryreset [--confirm]",
        .func = &cmd_factoryreset,
        .argtable = &factoryreset_args
    };
    
    ESP_LOGI(TAG, "Registering factory reset command");
    return esp_console_cmd_register(&cmd);
}
