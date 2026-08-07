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

#include "esp_mac.h"
#include "esp_ota_ops.h"
#include "esp_mac.h"
#include "mdns.h"
#include "esp_log.h"

#define TAG         __func__
static char mdns_host_name[24]; 
static char wican_hostname[36];

char* wc_mdns_get_hostname(void)
{
    return wican_hostname;
}

void wc_mdns_init(char *id, char* hv, char* fv)
{
    // Get MAC address
    uint8_t mac[6];
    char mac_str[18];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    sprintf(mac_str, "%02X:%02X:%02X:%02X:%02X:%02X", 
            mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    
    sprintf(mdns_host_name, "wican_%s", id);
    sprintf(wican_hostname, "wican_%s.local", id);
    ESP_LOGI(TAG, "Host Name: %s", wican_hostname);
    ESP_LOGI(TAG, "MAC Address: %s", mac_str);
    
    mdns_init();
    mdns_hostname_set(mdns_host_name);
    mdns_instance_name_set("wican web server");

    mdns_txt_item_t serviceTxtData[] = {
        {"mac", mac_str},           // for stable unique ID
        {"device_id", id},          // device serial/ID
        {"firmware", fv},           // "fimrware" -> "firmware"
        {"hardware", hv},
        {"version", fv},            // lias for consistency
        {"path", "/"}
    };

    // Change service type to _wican for better discovery
    ESP_ERROR_CHECK_WITHOUT_ABORT(mdns_service_add("WiCAN-WebServer", "_wican", "_tcp", 80, 
                                     serviceTxtData, sizeof(serviceTxtData) / sizeof(serviceTxtData[0])));
}
