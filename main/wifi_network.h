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

#ifndef __WIFI_NETWORK_H__
#define __WIFI_NETWORK_H__

#include "config_server.h"  // For wifi_security_t type

// bool wifi_network_is_connected(void);
// void wifi_network_init(char* sta_ssid, char* sta_pass);
void wifi_network_init(char* ap_ssid_uid);
// SmartConnect mode checking function
bool wifi_network_is_smartconnect_mode(void);
// Helper function to get SmartConnect credentials
// SmartConnect mode switching functions for future enhancement
// void wifi_network_switch_to_home_mode(void);
// void wifi_network_switch_to_drive_mode(void);
// void wifi_network_deinit(void);
// void wifi_network_restart(void);
// void wifi_network_stop(void);
// void wifi_network_start(void);
// char* wifi_network_scan(void);
#endif
