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

#include "console.h"
#include "esp_log.h"
#include "esp_vfs_cdcacm.h"
#include "driver/uart.h"
#include "linenoise/linenoise.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "imu.h"
#include "rtcm.h"
#include "led.h"
#include "sleep_mode.h"
#include "hw_config.h"
#include "sdcard.h"
#include "esp_heap_caps.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_console.h"
#include "esp_vfs.h"
#include "esp_timer.h"
#include "lwip/sockets.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_chip_info.h"
#include "esp_private/esp_clk.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "soc/efuse_reg.h"
#include "esp_flash.h"
#include "wusb3801.h"

#define PROMPT "wican> "
#define MAX_CMDLINE_LENGTH 256
#define MAX_CMDLINE_ARGS 8
#define TCP_PORT 23
#define MAX_CLIENTS 1
#define RECV_BUF_SIZE (MAX_CMDLINE_LENGTH)
#define MAX_HISTORY_LINES 50

static TaskHandle_t tcp_console_task_handle = NULL;
static int server_socket = -1;
static int client_socket = -1;
static bool is_console_initialized = false;

static const char* TAG = "console";

typedef struct {
    const char *command;
    const char *help;
    const char *hint;
    esp_console_cmd_func_t func;
} console_cmd_t;

static struct {
    struct arg_lit *sync;
    struct arg_lit *read;
    struct arg_lit *id;
    struct arg_end *end;
} rtcm_args;

static struct {
    struct arg_lit *id;
    struct arg_end *end;
} imu_args;

static struct {
    struct arg_lit *id;
    struct arg_end *end;
} led_args;

struct {
    struct arg_lit *voltage;
    struct arg_lit *reboot;
    struct arg_lit *info;
    struct arg_lit *memory;
    struct arg_end *end;
} system_args;

static struct {
    struct arg_lit *info;
    struct arg_lit *test;
    struct arg_end *end;
} sdcard_args;

static struct {
    struct arg_lit *status;
    struct arg_lit *info;
    struct arg_end *end;
} wifi_args;

static struct {
    struct arg_lit *cc_status;
    struct arg_lit *id;
    struct arg_end *end;
} wusb_args;

static void tcp_console_write(const char* data, size_t len)
{
    if (client_socket < 0)
    {
        return;
    }
    send(client_socket, data, len, 0);
}

static void console_printf(const char *fmt, ...)
{
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    printf("%s", buf);
    tcp_console_write(buf, strlen(buf));
}

static int cmd_version(int argc, char **argv)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_app_desc_t running_app_info;

    if (esp_ota_get_partition_description(running, &running_app_info) != ESP_OK) {
        console_printf("Error: Failed to get partition info\n");
        return 1;
    }

    console_printf("Version: %s\n", running_app_info.version);
    console_printf("Project Name: %s\n", running_app_info.project_name);
    console_printf("Build Time: %s %s\n", running_app_info.date, running_app_info.time);
    console_printf("IDF Version: %s\n", running_app_info.idf_ver);
    console_printf("Running Partition: %s\n", running->label);
    console_printf("OK\n");
    return 0;
}

static int cmd_status(int argc, char **argv) 
{
    console_printf("System Status: OK\n");
    return 0;
}

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
            console_printf("Error: Failed to read IMU ID\n");
            return 1;
        }
        console_printf("IMU Device ID: 0x%02X\n", id);
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static int cmd_rtcm(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&rtcm_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, rtcm_args.end, argv[0]);
        return 1;
    }

    if (rtcm_args.sync->count > 0)
    {
        // Sync time from internet
        if (rtcm_sync_internet_time() != ESP_OK)
        {
            console_printf("Error: Failed to sync time\n");
            return 1;
        }
        console_printf("Time synchronized successfully\n");
        console_printf("OK\n");
        return 0;
    }
    else if (rtcm_args.read->count > 0) 
    {
        uint8_t hour, min, sec;
        uint8_t year, month, day, weekday;
        
        esp_err_t ret_time = rtcm_get_time(&hour, &min, &sec);
        esp_err_t ret_date = rtcm_get_date(&year, &month, &day, &weekday);

        if (ret_time == ESP_OK && ret_date == ESP_OK) 
        {
            console_printf("20%02X-%02X-%02X %02X:%02X:%02X (Day %d)\n", 
                   year, month, day, hour, min, sec, weekday);
            console_printf("OK\n");
            return 0;
        } 
        else 
        {
            console_printf("Error: Failed to read RTC time/date\n");
            return 1;
        }
    }
    else if (rtcm_args.id->count > 0)
    {
        uint8_t id;
        if (rtcm_get_device_id(&id) != ESP_OK) 
        { 
            console_printf("Error: Failed to read RTC module ID\n");
            return 1;
        }
        console_printf("RTC Module Device ID: 0x%02X\n", id);
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
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
            console_printf("Error: Failed to read LED driver ID\n");
            return 1;
        }
        console_printf("LED Driver ID: 0x%02X\n", id);
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static int cmd_system(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&system_args);
    if (nerrors != 0) 
    {
        arg_print_errors(stderr, system_args.end, argv[0]);
        return 1;
    }

    if (system_args.voltage->count > 0) 
    {
        float voltage;
        if (sleep_mode_get_voltage(&voltage) != ESP_OK) 
        {
            console_printf("Error: Failed to read voltage\n");
            return 1;
        }
        console_printf("System Voltage: %.2f V\n", voltage);
        console_printf("OK\n");
        return 0;
    }

    if (system_args.reboot->count > 0)
    {
        console_printf("System will reboot now...\n");
        vTaskDelay(pdMS_TO_TICKS(2000));
        
        esp_restart();
        return 0;
    }

    if (system_args.info->count > 0)
    {
        // Get device ID
        char device_id[13];
        if (hw_config_get_device_id(device_id) != ESP_OK)
        {
            console_printf("Error: Failed to read device ID\n");
            return 1;
        }
        console_printf("Device ID: %s\n", device_id);
        
        // Get running partition info
        const esp_partition_t *running = esp_ota_get_running_partition();
        esp_app_desc_t running_app_info;
        
        if (esp_ota_get_partition_description(running, &running_app_info) == ESP_OK) {
            console_printf("Running Partition: %s\n", running->label);
            console_printf("App Version: %s\n", running_app_info.version);
            console_printf("Project Name: %s\n", running_app_info.project_name);
            console_printf("Build Time: %s %s\n", running_app_info.date, running_app_info.time);
            console_printf("IDF Version: %s\n", running_app_info.idf_ver);
        }
        
        // Get chip info
        esp_chip_info_t chip_info;
        esp_chip_info(&chip_info);
        console_printf("Chip Model: %s\n", 
            chip_info.model == CHIP_ESP32 ? "ESP32" : 
            chip_info.model == CHIP_ESP32S2 ? "ESP32-S2" : 
            chip_info.model == CHIP_ESP32S3 ? "ESP32-S3" : 
            chip_info.model == CHIP_ESP32C3 ? "ESP32-C3" : "Unknown");
        console_printf("CPU Cores: %d\n", chip_info.cores);
        console_printf("CPU Frequency: %d MHz\n", esp_clk_cpu_freq() / 1000000);
        
        // console_printf("Flash Size: %d MB\n", spi_flash_get_chip_size() / (1024 * 1024));
        unsigned major_rev = chip_info.revision / 100;
        unsigned minor_rev = chip_info.revision % 100;

        uint32_t flash_size;

        console_printf("Chip Revision: v%d.%d\n", major_rev, minor_rev);
        if(esp_flash_get_size(NULL, &flash_size) != ESP_OK)
        {
            console_printf("Get flash size failed\n");
            return 1;
        }
    
        console_printf("Flash Size: %ld MB\n", flash_size / (uint32_t)(1024 * 1024),
               (chip_info.features & CHIP_FEATURE_EMB_FLASH) ? "embedded" : "external");
    
        console_printf("Minimum free heap size: %" PRIu32 " bytes\n", esp_get_minimum_free_heap_size());
        
        // Get uptime
        int64_t uptime_us = esp_timer_get_time();
        int uptime_s = uptime_us / 1000000;
        int uptime_m = uptime_s / 60;
        int uptime_h = uptime_m / 60;
        int uptime_d = uptime_h / 24;
        
        console_printf("System Uptime: %dd %dh %dm %ds\n", 
            uptime_d, 
            uptime_h % 24, 
            uptime_m % 60, 
            uptime_s % 60);
        
        console_printf("OK\n");
        return 0;
    }

    if (system_args.memory->count > 0)
    {
        // Get memory stats
        uint32_t free_internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t free_psram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
        uint32_t largest_internal = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t largest_psram = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);
        uint32_t min_free_internal = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t min_free_psram = heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM);

        // Get total heap size
        multi_heap_info_t info;
        heap_caps_get_info(&info, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t total_internal = info.total_free_bytes + info.total_allocated_bytes;

        console_printf("RAM Free: %lu bytes\n", free_internal);
        console_printf("RAM Largest block: %lu bytes\n", largest_internal);
        console_printf("RAM Total: %lu bytes\n", total_internal);
        console_printf("RAM Min free ever: %lu bytes\n", min_free_internal);
        console_printf("PSRAM Free: %lu bytes\n", free_psram);
        console_printf("PSRAM Largest block: %lu bytes\n", largest_psram);
        console_printf("PSRAM Min free ever: %lu bytes\n", min_free_psram);
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static int cmd_sdcard(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&sdcard_args);
    if (nerrors != 0) 
    {
        arg_print_errors(stderr, sdcard_args.end, argv[0]);
        return 1;
    }

    if (sdcard_args.info->count > 0)
    {
        sdmmc_card_info_t card_info;
        if (sdcard_get_info(&card_info) != ESP_OK)
        {
            console_printf("Error: Failed to read SD card info\n");
            return 1;
        }
        console_printf("SD Card Info:\n");
        console_printf("Name: %s\n", card_info.name);
        console_printf("Type: %s\n", card_info.type == CARD_TYPE_SDHC ? "SDHC/SDXC" : 
                            card_info.type == CARD_TYPE_MMC ? "MMC" : 
                            card_info.type == CARD_TYPE_SDIO ? "SDIO" : "SDSC");
        console_printf("Capacity: %.2f GB\n", ((float)card_info.capacity/1024));
        console_printf("Sector Size: %d bytes\n", card_info.sector_size);
        console_printf("Speed: %lu KHz\n", card_info.speed);
        console_printf("OK\n");
        return 0;
    }

    if (sdcard_args.test->count > 0)
    {
        if (sdcard_test_rw() != ESP_OK)
        {
            console_printf("Error: SD card test failed\n");
            return 1;
        }
        console_printf("SD card test passed successfully\n");
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static int cmd_wifi(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&wifi_args);
    if (nerrors != 0) 
    {
        arg_print_errors(stderr, wifi_args.end, argv[0]);
        return 1;
    }

    if (wifi_args.status->count > 0)
    {
        // Get WiFi connection status
        wifi_mode_t mode;
        esp_err_t err = esp_wifi_get_mode(&mode);
        if (err != ESP_OK) {
            console_printf("Error: Failed to get WiFi mode (error %d)\n", err);
            return 1;
        }
        
        if (mode == WIFI_MODE_NULL) {
            console_printf("WiFi not initialized\n");
        } else if (mode == WIFI_MODE_STA || mode == WIFI_MODE_APSTA) {
            wifi_ap_record_t ap_info;
            err = esp_wifi_sta_get_ap_info(&ap_info);
            if (err == ESP_OK) {
                console_printf("WiFi Status: Connected\n");
                console_printf("SSID: %s\n", ap_info.ssid);
                console_printf("RSSI: %d dBm\n", ap_info.rssi);
            } else if (err == ESP_ERR_WIFI_NOT_CONNECT) {
                console_printf("WiFi Status: Disconnected\n");
            } else {
                console_printf("WiFi Status: Error getting connection info (error %d)\n", err);
            }
        } else if (mode == WIFI_MODE_AP) {
            console_printf("WiFi Status: Access Point mode\n");
        }
        
        console_printf("OK\n");
        return 0;
    }

    if (wifi_args.info->count > 0)
    {
        // Get detailed WiFi connection information
        wifi_mode_t mode;
        esp_err_t err = esp_wifi_get_mode(&mode);
        if (err != ESP_OK) {
            console_printf("Error: Failed to get WiFi mode (error %d)\n", err);
            return 1;
        }
        
        console_printf("WiFi Mode: ");
        switch (mode) {
            case WIFI_MODE_NULL: console_printf("Not initialized\n"); break;
            case WIFI_MODE_STA: console_printf("Station\n"); break;
            case WIFI_MODE_AP: console_printf("Access Point\n"); break;
            case WIFI_MODE_APSTA: console_printf("AP+Station\n"); break;
            default: console_printf("Unknown\n");
        }
        
        if (mode == WIFI_MODE_STA || mode == WIFI_MODE_APSTA) {
            wifi_config_t wifi_cfg;
            err = esp_wifi_get_config(WIFI_IF_STA, &wifi_cfg);
            if (err == ESP_OK) {
                console_printf("Station Configuration:\n");
                console_printf("  SSID: %s\n", wifi_cfg.sta.ssid);
                
                // Get connection info
                wifi_ap_record_t ap_info;
                err = esp_wifi_sta_get_ap_info(&ap_info);
                if (err == ESP_OK) {
                    console_printf("  Connected to AP:\n");
                    console_printf("    BSSID: %02x:%02x:%02x:%02x:%02x:%02x\n",
                        ap_info.bssid[0], ap_info.bssid[1], ap_info.bssid[2],
                        ap_info.bssid[3], ap_info.bssid[4], ap_info.bssid[5]);
                    console_printf("    Channel: %d\n", ap_info.primary);
                    console_printf("    RSSI: %d dBm\n", ap_info.rssi);
                    console_printf("    Authentication Mode: ");
                    switch (ap_info.authmode) {
                        case WIFI_AUTH_OPEN: console_printf("Open\n"); break;
                        case WIFI_AUTH_WEP: console_printf("WEP\n"); break;
                        case WIFI_AUTH_WPA_PSK: console_printf("WPA PSK\n"); break;
                        case WIFI_AUTH_WPA2_PSK: console_printf("WPA2 PSK\n"); break;
                        case WIFI_AUTH_WPA_WPA2_PSK: console_printf("WPA/WPA2 PSK\n"); break;
                        case WIFI_AUTH_WPA2_ENTERPRISE: console_printf("WPA2 Enterprise\n"); break;
                        default: console_printf("Unknown\n");
                    }
                    
                    // Get IP info using esp_netif APIs
                    esp_netif_t *sta_netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
                    if (sta_netif) {
                        esp_netif_ip_info_t ip_info;
                        err = esp_netif_get_ip_info(sta_netif, &ip_info);
                        if (err == ESP_OK) {
                            console_printf("    IP Address: " IPSTR "\n", IP2STR(&ip_info.ip));
                            console_printf("    Subnet Mask: " IPSTR "\n", IP2STR(&ip_info.netmask));
                            console_printf("    Gateway: " IPSTR "\n", IP2STR(&ip_info.gw));
                        } else {
                            console_printf("    Error getting IP info (error %d)\n", err);
                        }
                    } else {
                        console_printf("    Error getting netif handle\n");
                    }
                } else if (err == ESP_ERR_WIFI_NOT_CONNECT) {
                    console_printf("  Not connected to any AP\n");
                } else {
                    console_printf("  Error getting connection info (error %d)\n", err);
                }
            } else {
                console_printf("Error getting station configuration (error %d)\n", err);
            }
        }
        
        if (mode == WIFI_MODE_AP || mode == WIFI_MODE_APSTA) {
            wifi_config_t wifi_cfg;
            err = esp_wifi_get_config(WIFI_IF_AP, &wifi_cfg);
            if (err == ESP_OK) {
                console_printf("AP Configuration:\n");
                console_printf("  SSID: %s\n", wifi_cfg.ap.ssid);
                console_printf("  Channel: %d\n", wifi_cfg.ap.channel);
                console_printf("  Max connections: %d\n", wifi_cfg.ap.max_connection);
            } else {
                console_printf("Error getting AP configuration (error %d)\n", err);
            }
        }
        
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static int cmd_wusb(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&wusb_args);
    if (nerrors != 0) 
    {
        arg_print_errors(stderr, wusb_args.end, argv[0]);
        return 1;
    }

    if (wusb_args.id->count > 0)
    {
        uint8_t id = wusb3801_get_dev_id();
        console_printf("WUSB3801 Device ID: 0x%02X\n", id);
        console_printf("OK\n");
        return 0;
    }

    if (wusb_args.cc_status->count > 0)
    {
        uint8_t cc_stat = wusb3801_get_cc_stat();
        console_printf("WUSB3801 CC Status: 0x%02X\n", cc_stat);
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static void console_register_commands(void)
{
    esp_console_register_help_command();
    
    // Initialize IMU command arguments
    imu_args.id = arg_lit0("i", "id", "Get IMU device ID");
    imu_args.end = arg_end(2);

    // Initialize RTCM command arguments
    rtcm_args.sync = arg_lit0("s", "sync", "Sync time from internet");
    rtcm_args.read = arg_lit0("r", "read", "Read current time and date");
    rtcm_args.id = arg_lit0("i", "id", "Get RTC module device ID");  // Add this new argument
    rtcm_args.end = arg_end(3);

    // Initialize LED command arguments with other arg initializations
    led_args.id = arg_lit0("i", "id", "Get LED driver device ID");
    led_args.end = arg_end(2);
    
    // Initialize WUSB command arguments
    wusb_args.cc_status = arg_lit0("c", "cc", "Get CC status");
    wusb_args.id = arg_lit0("i", "id", "Get WUSB device ID");
    wusb_args.end = arg_end(3);
    
    system_args.voltage = arg_lit0("v", "voltage", "Get system voltage");
    system_args.reboot = arg_lit0("r", "reboot", "Reboot system");
    system_args.info = arg_lit0("i", "info", "Get system information including device ID");
    system_args.memory = arg_lit0("m", "memory", "Get heap memory info");
    system_args.end = arg_end(5);

    sdcard_args.info = arg_lit0("i", "info", "Get SD card information");
    sdcard_args.test = arg_lit0("t", "test", "Test SD card read/write");
    sdcard_args.end = arg_end(3);
    
    // Initialize WiFi command arguments
    wifi_args.status = arg_lit0("s", "status", "Get WiFi connection status");
    wifi_args.info = arg_lit0("i", "info", "Get detailed WiFi connection information");
    wifi_args.end = arg_end(3);

    
    const console_cmd_t cmd_table[] = {
        {"version", "Get firmware version", NULL, &cmd_version},
        {"status", "Get system status", NULL, &cmd_status},
        {"imu", "IMU control and status", "Usage: imu [-i]", &cmd_imu},
        {"rtc", "RTC module control", "Usage: rtc [-s|-r|-i]", &cmd_rtcm},
        {"led", "LED driver control", "Usage: led [-i]", &cmd_led},
        {"wusb", "WUSB3801 USB-C controller", "Usage: wusb [-i|-c]", &cmd_wusb},
        {"system", "System control and status", "Usage: system [-v|-r|-i|-m]", &cmd_system},
        {"sdcard", "SD card control and status", "Usage: sdcard [-i|-t]", &cmd_sdcard},
        {"wifi", "WiFi connection control and status", "Usage: wifi [-s|-i]", &cmd_wifi},
        {NULL, NULL, NULL, NULL}
    };
    
    const console_cmd_t *cmd = &cmd_table[0];
    while (cmd->command != NULL)
    {
        esp_console_cmd_t console_cmd = {
            .command = cmd->command,
            .help = cmd->help,
            .hint = cmd->hint,
            .func = cmd->func,
        };
        
        // Add argtable for each command
        if (strcmp(cmd->command, "imu") == 0) {
            console_cmd.argtable = &imu_args;
        } else if (strcmp(cmd->command, "rtc") == 0) {
            console_cmd.argtable = &rtcm_args;
        } else if (strcmp(cmd->command, "led") == 0) {
            console_cmd.argtable = &led_args;
        } else if (strcmp(cmd->command, "wusb") == 0) {
            console_cmd.argtable = &wusb_args;
        } else if (strcmp(cmd->command, "system") == 0) {
            console_cmd.argtable = &system_args;
        } else if (strcmp(cmd->command, "sdcard") == 0) {
            console_cmd.argtable = &sdcard_args;
        } else if (strcmp(cmd->command, "wifi") == 0) {
            console_cmd.argtable = &wifi_args;
        }
        
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_console_cmd_register(&console_cmd));
        cmd++;
    }
}

static void process_command(char* cmd)
{
    // Create local copy of command to preserve original
    char cmd_copy[MAX_CMDLINE_LENGTH];
    strncpy(cmd_copy, cmd, MAX_CMDLINE_LENGTH - 1);
    cmd_copy[MAX_CMDLINE_LENGTH - 1] = '\0';

    // Trim trailing whitespace and newlines
    size_t cmd_len = strlen(cmd_copy);
    while (cmd_len > 0 && (cmd_copy[cmd_len - 1] == ' ' || 
           cmd_copy[cmd_len - 1] == '\r' || 
           cmd_copy[cmd_len - 1] == '\n'))
    {
        cmd_copy[--cmd_len] = '\0';
    }

    // Skip empty commands
    if (cmd_len == 0)
    {
        // tcp_console_write(PROMPT, strlen(PROMPT));
        return;
    }
    int ret;
    esp_err_t err = esp_console_run(cmd_copy, &ret);
    // Handle command execution results
    if (err == ESP_ERR_NOT_FOUND)
    {
        tcp_console_write("Command not found\n", 20);
    }
    else if (err == ESP_ERR_INVALID_ARG)
    {
        // command was empty or invalid
        tcp_console_write("Invalid arguments\n", 20);
    }
    else if (err == ESP_OK && ret != ESP_OK)
    {
        tcp_console_write("Command returned non-zero error code\n", 39);
    }
    else if (err != ESP_OK)
    {
        tcp_console_write("Internal error\n", 17);
    }

    // Always print prompt after command execution
    // tcp_console_write(PROMPT, strlen(PROMPT));
}

// Main TCP console task
static void tcp_console_task(void* arg)
{
    struct sockaddr_in server_addr = {
        .sin_family = AF_INET,
        .sin_port = htons(TCP_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY)
    };

    // Create server socket
    server_socket = socket(AF_INET, SOCK_STREAM, 0);
    if (server_socket < 0)
    {
        ESP_LOGE(TAG, "Failed to create server socket");
        vTaskDelete(NULL);
        return;
    }

    // Socket options setup
    int opt = 1;
    setsockopt(server_socket, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    
    // TCP keepalive configuration
    int keepalive = 1;
    int keepidle = (60*5);
    int keepintvl = 10;
    int keepcnt = 3;
    setsockopt(server_socket, SOL_SOCKET, SO_KEEPALIVE, &keepalive, sizeof(keepalive));
    setsockopt(server_socket, IPPROTO_TCP, TCP_KEEPIDLE, &keepidle, sizeof(keepidle));
    setsockopt(server_socket, IPPROTO_TCP, TCP_KEEPINTVL, &keepintvl, sizeof(keepintvl));
    setsockopt(server_socket, IPPROTO_TCP, TCP_KEEPCNT, &keepcnt, sizeof(keepcnt));

    if (bind(server_socket, (struct sockaddr*)&server_addr, sizeof(server_addr)) != 0)
    {
        ESP_LOGE(TAG, "Socket bind failed");
        close(server_socket);
        vTaskDelete(NULL);
        return;
    }

    if (listen(server_socket, 1) != 0)
    {
        ESP_LOGE(TAG, "Socket listen failed");
        close(server_socket);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "TCP Console started on port %d", TCP_PORT);

    // Allocate buffers including command history
    char *recv_buf = heap_caps_malloc(RECV_BUF_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    char *cmd_buf = heap_caps_malloc(MAX_CMDLINE_LENGTH, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    char **cmd_history = heap_caps_malloc(MAX_HISTORY_LINES * sizeof(char*), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    for (int i = 0; i < MAX_HISTORY_LINES; i++)
    {
        cmd_history[i] = heap_caps_malloc(MAX_CMDLINE_LENGTH, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!cmd_history[i])
        {
            ESP_LOGE(TAG, "Failed to allocate history buffer %d", i);
            // Cleanup previously allocated buffers
            for (int j = 0; j < i; j++)
            {
                heap_caps_free(cmd_history[j]);
            }
            heap_caps_free(cmd_history);
            if (recv_buf) heap_caps_free(recv_buf);
            if (cmd_buf) heap_caps_free(cmd_buf);
            vTaskDelete(NULL);
            return;
        }
        memset(cmd_history[i], 0, MAX_CMDLINE_LENGTH);
    }
    
    if (!recv_buf || !cmd_buf || !cmd_history)
    {
        ESP_LOGE(TAG, "Failed to allocate console buffers in PSRAM");
        if (recv_buf) heap_caps_free(recv_buf);
        if (cmd_buf) heap_caps_free(cmd_buf);
        if (cmd_history)
        {
            for (int i = 0; i < MAX_HISTORY_LINES; i++)
            {
                if (cmd_history[i]) heap_caps_free(cmd_history[i]);
            }
            heap_caps_free(cmd_history);
        }
        vTaskDelete(NULL);
        return;
    }

    size_t cmd_pos = 0;
    size_t history_count = 0;
    int history_index = -1;
    bool insertion_mode = false;  // Toggle between insert/overwrite mode

    while (1)
    {
        struct sockaddr_in client_addr;
        socklen_t client_addr_len = sizeof(client_addr);

        client_socket = accept(server_socket, (struct sockaddr*)&client_addr, &client_addr_len);
        if (client_socket < 0)
        {
            ESP_LOGE(TAG, "Accept failed");
            continue;
        }
        
        ESP_LOGI(TAG, "Client connected from %s", inet_ntoa(client_addr.sin_addr));
        
        struct timeval timeout;
        timeout.tv_sec = (60*5);
        timeout.tv_usec = 0;
        setsockopt(client_socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

        // Send welcome message
        const char* welcome_msg = "\nWelcome to ESP32 Console\n"
                                "Type 'help' for available commands\n";
        tcp_console_write(welcome_msg, strlen(welcome_msg));

        memset(cmd_buf, 0, MAX_CMDLINE_LENGTH);
        cmd_pos = 0;
        history_index = -1;

        tcp_console_write(PROMPT, strlen(PROMPT));

        while (1)
        {
            int len = recv(client_socket, recv_buf, RECV_BUF_SIZE - 1, 0);
            if (len <= 0)
            {
                ESP_LOGI(TAG, "Client disconnected");
                break;
            }

            for (int i = 0; i < len; i++)
            {
                char c = recv_buf[i];
                
                // Handle escape sequences
                if (c == '\x1b')
                {
                    if (i + 2 < len && recv_buf[i + 1] == '[')
                    {
                        switch (recv_buf[i + 2])
                        {
                            case 'A': // Up arrow
                                if (history_count > 0 && history_index < (int)(history_count - 1))
                                {
                                    // Clear current line
                                    while (cmd_pos > 0)
                                    {
                                        tcp_console_write("\b \b", 3);
                                        cmd_pos--;
                                    }
                                    history_index++;
                                    strcpy(cmd_buf, cmd_history[history_index]);
                                    cmd_pos = strlen(cmd_buf);
                                    tcp_console_write(cmd_buf, cmd_pos);
                                }
                                i += 2;
                                continue;

                            case 'B': // Down arrow
                                if (history_index >= 0)
                                {
                                    // Clear current line
                                    while (cmd_pos > 0)
                                    {
                                        tcp_console_write("\b \b", 3);
                                        cmd_pos--;
                                    }
                                    history_index--;
                                    if (history_index >= 0)
                                    {
                                        strcpy(cmd_buf, cmd_history[history_index]);
                                        cmd_pos = strlen(cmd_buf);
                                        tcp_console_write(cmd_buf, cmd_pos);
                                    }
                                }
                                i += 2;
                                continue;

                            case 'C': // Right arrow
                                if (cmd_pos < strlen(cmd_buf))
                                {
                                    tcp_console_write("\x1b[C", 3);
                                    cmd_pos++;
                                }
                                i += 2;
                                continue;

                            case 'D': // Left arrow
                                if (cmd_pos > 0)
                                {
                                    tcp_console_write("\x1b[D", 3);
                                    cmd_pos--;
                                }
                                i += 2;
                                continue;

                            case '2': // Insert key
                                if (i + 3 < len && recv_buf[i + 3] == '~')
                                {
                                    insertion_mode = !insertion_mode;
                                    i += 3;
                                    continue;
                                }
                                break;

                            case '3': // Delete key
                                if (i + 3 < len && recv_buf[i + 3] == '~')
                                {
                                    if (cmd_pos < strlen(cmd_buf))
                                    {
                                        memmove(&cmd_buf[cmd_pos], &cmd_buf[cmd_pos + 1], strlen(cmd_buf) - cmd_pos);
                                        tcp_console_write("\x1b[K", 3); // Clear to end of line
                                        tcp_console_write(&cmd_buf[cmd_pos], strlen(&cmd_buf[cmd_pos]));
                                        // Move cursor back to original position
                                        char cursor_pos[16];
                                        snprintf(cursor_pos, sizeof(cursor_pos), "\x1b[%zuD", strlen(&cmd_buf[cmd_pos]));
                                        tcp_console_write(cursor_pos, strlen(cursor_pos));
                                    }
                                    i += 3;
                                    continue;
                                }
                                break;
                        }
                    }
                }
                
                // Handle backspace
                if (c == 0x7f || c == 0x08)
                {
                    if (cmd_pos > 0)
                    {
                        if (cmd_pos < strlen(cmd_buf))
                        {
                            // Remove character and shift remaining text
                            memmove(&cmd_buf[cmd_pos - 1], &cmd_buf[cmd_pos], strlen(cmd_buf) - cmd_pos + 1);
                            tcp_console_write("\b", 1);
                            tcp_console_write("\x1b[K", 3); // Clear to end of line
                            tcp_console_write(&cmd_buf[cmd_pos - 1], strlen(&cmd_buf[cmd_pos - 1]));
                            // Move cursor back
                            char cursor_pos[16];
                            snprintf(cursor_pos, sizeof(cursor_pos), "\x1b[%zuD", strlen(&cmd_buf[cmd_pos - 1]));
                            tcp_console_write(cursor_pos, strlen(cursor_pos));
                        }
                        else
                        {
                            cmd_buf[--cmd_pos] = '\0';
                            tcp_console_write("\b \b", 3);
                        }
                    }
                    continue;
                }
                
                // Handle newline
                if (c == '\n')
                {
                    // tcp_console_write("\n", 1);
                    if (cmd_pos > 0)
                    {
                        cmd_buf[cmd_pos] = '\0';
                        // Add to history if different from last command
                        if (history_count == 0 || strcmp(cmd_buf, cmd_history[0]) != 0)
                        {
                            // Shift history up
                            if (history_count < MAX_HISTORY_LINES)
                                history_count++;
                            for (int j = history_count - 1; j > 0; j--)
                            {
                                strcpy(cmd_history[j], cmd_history[j - 1]);
                            }
                            strcpy(cmd_history[0], cmd_buf);
                        }
                        process_command(cmd_buf);
                        memset(cmd_buf, 0, MAX_CMDLINE_LENGTH);
                        cmd_pos = 0;
                        history_index = -1;
                    }
                    tcp_console_write(PROMPT, strlen(PROMPT));
                    continue;
                }
                
                // Handle printable characters
                if (c >= 32 && c < 127)
                {
                    if (cmd_pos < MAX_CMDLINE_LENGTH - 1)
                    {
                        if (insertion_mode && cmd_pos < strlen(cmd_buf))
                        {
                            // Insert mode: shift characters right
                            memmove(&cmd_buf[cmd_pos + 1], &cmd_buf[cmd_pos], strlen(cmd_buf) - cmd_pos);
                            cmd_buf[cmd_pos] = c;
                            // tcp_console_write(&cmd_buf[cmd_pos], strlen(&cmd_buf[cmd_pos]));
                            cmd_pos++;
                            // Move cursor back to position after inserted char
                            char cursor_pos[16];
                            snprintf(cursor_pos, sizeof(cursor_pos), "\x1b[%zuD", strlen(&cmd_buf[cmd_pos]) - 1);
                            // tcp_console_write(cursor_pos, strlen(cursor_pos));
                        }
                        else
                        {
                            // Overwrite mode or at end of line
                            cmd_buf[cmd_pos++] = c;
                            // tcp_console_write(&c, 1);
                        }
                    }
                }
            }
        }

        close(client_socket);
        client_socket = -1;
    }
}

static esp_err_t tcp_console_init(void) 
{
    if (is_console_initialized)
    {
        ESP_LOGW(TAG, "TCP Console already initialized");
        return ESP_ERR_INVALID_STATE;
    }

    // Allocate stack memory in PSRAM for the TCP console task
    static StackType_t *tcp_console_task_stack;
    static StaticTask_t tcp_console_task_buffer;
    
    tcp_console_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    
    if (tcp_console_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate TCP console task stack memory");
        return ESP_ERR_NO_MEM;
    }
    
    // Create static task
    tcp_console_task_handle = xTaskCreateStatic(
        tcp_console_task,
        "tcp_console",
        4096,
        NULL,
        5,
        tcp_console_task_stack,
        &tcp_console_task_buffer
    );
    
    if (tcp_console_task_handle == NULL)
    {
        ESP_LOGE(TAG, "Failed to create TCP console task");
        heap_caps_free(tcp_console_task_stack);
        return ESP_ERR_NO_MEM;
    }

    is_console_initialized = true;
    return ESP_OK;
}

static void tcp_console_deinit(void)
{
    if (!is_console_initialized)
    {
        return;
    }

    if (client_socket >= 0)
    {
        close(client_socket);
        client_socket = -1;
    }
    
    if (server_socket >= 0)
    {
        close(server_socket);
        server_socket = -1;
    }

    if (tcp_console_task_handle)
    {
        // Get the task stack pointer before deleting the task
        StackType_t *stack_ptr = (StackType_t *)pxTaskGetStackStart(tcp_console_task_handle);
        
        vTaskDelete(tcp_console_task_handle);
        tcp_console_task_handle = NULL;
        
        // Free the stack memory
        if (stack_ptr) {
            heap_caps_free(stack_ptr);
        }
    }

    esp_console_deinit();
    is_console_initialized = false;
}

esp_err_t console_init(void)
{
    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t repl_config = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    repl_config.task_stack_size = (4*1024);
    repl_config.prompt = PROMPT;
    repl_config.max_cmdline_length = 256;
    
#if CONFIG_ESP_CONSOLE_USB_CDC
    esp_console_dev_usb_cdc_config_t cdc_config = ESP_CONSOLE_DEV_CDC_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_console_new_repl_usb_cdc(&cdc_config, &repl_config, &repl));
#elif CONFIG_ESP_CONSOLE_UART
    esp_console_dev_uart_config_t uart_config = ESP_CONSOLE_DEV_UART_CONFIG_DEFAULT();

    esp_err_t ret = esp_console_new_repl_uart(&uart_config, &repl_config, &repl);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to create UART REPL: %d", ret);
        return ret;
    }
#endif
    tcp_console_init();
    console_register_commands();
    
    return esp_console_start_repl(repl);
}


