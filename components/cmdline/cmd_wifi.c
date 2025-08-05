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

#include "cmd_wifi.h"
#include "cmdline.h"
#include "esp_console.h"
#include "argtable3/argtable3.h"
#include "esp_wifi.h"
#include "esp_netif.h"

static struct {
    struct arg_lit *status;
    struct arg_lit *info;
    struct arg_end *end;
} wifi_args;

static int cmd_wifi(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&wifi_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, wifi_args.end, argv[0]);
        return 1;
    }

    if (wifi_args.status->count > 0) {
        wifi_mode_t mode;
        esp_err_t err = esp_wifi_get_mode(&mode);
        if (err != ESP_OK) {
            cmdline_printf("Error: Failed to get WiFi mode (error %d)\n", err);
            return 1;
        }
        
        if (mode == WIFI_MODE_NULL) {
            cmdline_printf("WiFi not initialized\n");
        } else if (mode == WIFI_MODE_STA || mode == WIFI_MODE_APSTA) {
            wifi_ap_record_t ap_info;
            err = esp_wifi_sta_get_ap_info(&ap_info);
            if (err == ESP_OK) {
                cmdline_printf("WiFi Status: Connected\n");
                cmdline_printf("SSID: %s\n", ap_info.ssid);
                cmdline_printf("RSSI: %d dBm\n", ap_info.rssi);
            } else if (err == ESP_ERR_WIFI_NOT_CONNECT) {
                cmdline_printf("WiFi Status: Disconnected\n");
            } else {
                cmdline_printf("WiFi Status: Error getting connection info (error %d)\n", err);
            }
        } else if (mode == WIFI_MODE_AP) {
            cmdline_printf("WiFi Status: Access Point mode\n");
        }
        
        cmdline_printf("OK\n");
        return 0;
    }

    if (wifi_args.info->count > 0) {
        wifi_mode_t mode;
        esp_err_t err = esp_wifi_get_mode(&mode);
        if (err != ESP_OK) {
            cmdline_printf("Error: Failed to get WiFi mode (error %d)\n", err);
            return 1;
        }
        
        cmdline_printf("WiFi Mode: ");
        switch (mode) {
            case WIFI_MODE_NULL: cmdline_printf("Not initialized\n"); break;
            case WIFI_MODE_STA: cmdline_printf("Station\n"); break;
            case WIFI_MODE_AP: cmdline_printf("Access Point\n"); break;
            case WIFI_MODE_APSTA: cmdline_printf("AP+Station\n"); break;
            default: cmdline_printf("Unknown\n");
        }
        
        if (mode == WIFI_MODE_STA || mode == WIFI_MODE_APSTA) {
            wifi_config_t wifi_cfg;
            err = esp_wifi_get_config(WIFI_IF_STA, &wifi_cfg);
            if (err == ESP_OK) {
                cmdline_printf("Station Configuration:\n");
                cmdline_printf("  SSID: %s\n", wifi_cfg.sta.ssid);
                
                wifi_ap_record_t ap_info;
                err = esp_wifi_sta_get_ap_info(&ap_info);
                if (err == ESP_OK) {
                    cmdline_printf("  Connected to AP:\n");
                    cmdline_printf("    BSSID: %02x:%02x:%02x:%02x:%02x:%02x\n",
                        ap_info.bssid[0], ap_info.bssid[1], ap_info.bssid[2],
                        ap_info.bssid[3], ap_info.bssid[4], ap_info.bssid[5]);
                    cmdline_printf("    Channel: %d\n", ap_info.primary);
                    cmdline_printf("    RSSI: %d dBm\n", ap_info.rssi);
                    cmdline_printf("    Authentication Mode: ");
                    switch (ap_info.authmode) {
                        case WIFI_AUTH_OPEN: cmdline_printf("Open\n"); break;
                        case WIFI_AUTH_WEP: cmdline_printf("WEP\n"); break;
                        case WIFI_AUTH_WPA_PSK: cmdline_printf("WPA PSK\n"); break;
                        case WIFI_AUTH_WPA2_PSK: cmdline_printf("WPA2 PSK\n"); break;
                        case WIFI_AUTH_WPA_WPA2_PSK: cmdline_printf("WPA/WPA2 PSK\n"); break;
                        case WIFI_AUTH_WPA2_ENTERPRISE: cmdline_printf("WPA2 Enterprise\n"); break;
                        default: cmdline_printf("Unknown\n");
                    }
                    
                    esp_netif_t *sta_netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
                    if (sta_netif) {
                        esp_netif_ip_info_t ip_info;
                        err = esp_netif_get_ip_info(sta_netif, &ip_info);
                        if (err == ESP_OK) {
                            cmdline_printf("    IP Address: " IPSTR "\n", IP2STR(&ip_info.ip));
                            cmdline_printf("    Subnet Mask: " IPSTR "\n", IP2STR(&ip_info.netmask));
                            cmdline_printf("    Gateway: " IPSTR "\n", IP2STR(&ip_info.gw));
                        } else {
                            cmdline_printf("    Error getting IP info (error %d)\n", err);
                        }
                    } else {
                        cmdline_printf("    Error getting netif handle\n");
                    }
                } else if (err == ESP_ERR_WIFI_NOT_CONNECT) {
                    cmdline_printf("  Not connected to any AP\n");
                } else {
                    cmdline_printf("  Error getting connection info (error %d)\n", err);
                }
            } else {
                cmdline_printf("Error getting station configuration (error %d)\n", err);
            }
        }
        
        if (mode == WIFI_MODE_AP || mode == WIFI_MODE_APSTA) {
            wifi_config_t wifi_cfg;
            err = esp_wifi_get_config(WIFI_IF_AP, &wifi_cfg);
            if (err == ESP_OK) {
                cmdline_printf("AP Configuration:\n");
                cmdline_printf("  SSID: %s\n", wifi_cfg.ap.ssid);
                cmdline_printf("  Channel: %d\n", wifi_cfg.ap.channel);
                cmdline_printf("  Max connections: %d\n", wifi_cfg.ap.max_connection);
            } else {
                cmdline_printf("Error getting AP configuration (error %d)\n", err);
            }
        }
        
        cmdline_printf("OK\n");
        return 0;
    }

    cmdline_printf("Error: No valid subcommand\n");
    return 1;
}

esp_err_t cmd_wifi_register(void)
{
    wifi_args.status = arg_lit0("s", "status", "Get WiFi connection status");
    wifi_args.info = arg_lit0("i", "info", "Get detailed WiFi connection information");
    wifi_args.end = arg_end(3);

    const esp_console_cmd_t cmd = {
        .command = "wifi",
        .help = "WiFi connection control and status",
        .hint = "Usage: wifi [-s|-i]",
        .func = &cmd_wifi,
        .argtable = &wifi_args
    };
    return esp_console_cmd_register(&cmd);
}