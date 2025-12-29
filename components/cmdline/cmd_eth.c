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

#include "cmd_eth.h"

#include "cmdline.h"

#include "argtable3/argtable3.h"
#include "esp_console.h"
#include "esp_netif.h"

static struct {
    struct arg_lit *info;
    struct arg_str *ifkey;
    struct arg_end *end;
} eth_args;

static int cmd_eth(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&eth_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, eth_args.end, argv[0]);
        return 1;
    }

    const char *ifkey = "u4";
    if (eth_args.ifkey->count > 0 && eth_args.ifkey->sval[0] != NULL) {
        ifkey = eth_args.ifkey->sval[0];
    }

    if (eth_args.info->count == 0) {
        cmdline_printf("Error: No valid subcommand\n");
        return 1;
    }

    esp_netif_t *netif = esp_netif_get_handle_from_ifkey(ifkey);
    if (netif == NULL) {
        cmdline_printf("No netif found for if_key=%s\n", ifkey);
        return 1;
    }

    uint8_t mac[6] = {0};
    if (esp_netif_get_mac(netif, mac) == ESP_OK) {
        cmdline_printf("IFKEY: %s\n", ifkey);
        cmdline_printf("MAC: %02x:%02x:%02x:%02x:%02x:%02x\n",
                      mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    } else {
        cmdline_printf("IFKEY: %s\n", ifkey);
        cmdline_printf("MAC: (unavailable)\n");
    }

    esp_netif_ip_info_t ip_info;
    if (esp_netif_get_ip_info(netif, &ip_info) == ESP_OK) {
        cmdline_printf("IP: " IPSTR "\n", IP2STR(&ip_info.ip));
        cmdline_printf("MASK: " IPSTR "\n", IP2STR(&ip_info.netmask));
        cmdline_printf("GW: " IPSTR "\n", IP2STR(&ip_info.gw));
        if (ip_info.ip.addr == 0) {
            cmdline_printf("Note: IP is 0.0.0.0 (DHCP likely not completed)\n");
        }
    } else {
        cmdline_printf("IP: (unavailable)\n");
    }

    cmdline_printf("OK\n");
    return 0;
}

esp_err_t cmd_eth_register(void)
{
    eth_args.info = arg_lit0("i", "info", "Show Ethernet (esp-netif) info");
    eth_args.ifkey = arg_str0("k", "ifkey", "<ifkey>", "esp-netif if_key (default: u4)");
    eth_args.end = arg_end(3);

    const esp_console_cmd_t cmd = {
        .command = "eth",
        .help = "USB Ethernet status (e.g. RTL8152)",
        .hint = "Usage: eth -i [--ifkey u4]",
        .func = &cmd_eth,
        .argtable = &eth_args,
    };

    return esp_console_cmd_register(&cmd);
}
