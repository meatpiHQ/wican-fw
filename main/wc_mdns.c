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

static char mdns_host_name[24]; 
static char wican_hostname[36];

char* wc_mdns_get_hostname(void)
{
    return wican_hostname;
}

void wc_mdns_init(char *id, char* hv, char* fv)
{
	sprintf(mdns_host_name, "wican_%s", id);
    sprintf(wican_hostname, "wican_%s.local", id);
    mdns_init();
    mdns_hostname_set(mdns_host_name);
    mdns_instance_name_set("wican web server");

    mdns_txt_item_t serviceTxtData[] = {
		{"fimrware", fv},
		{"hardware", hv},
        {"path", "/"}
    };

    ESP_ERROR_CHECK(mdns_service_add("WiCAN-WebServer", "_http", "_tcp", 80, serviceTxtData,
                                     sizeof(serviceTxtData) / sizeof(serviceTxtData[0])));
}
