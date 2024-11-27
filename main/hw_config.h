 /* This file is part of the WiCAN project.
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
#ifndef GW_CONFIG_h
#define GW_CONFIG_h

#if HARDWARE_VER == WICAN_PRO

#define TX_GPIO_NUM             	2
#define RX_GPIO_NUM             	1
#define CAN_STDBY_GPIO_NUM			38

#define SDCARD_CLK                  21                 
#define SDCARD_CMD                  47
#define SDCARD_D0                   14
#define SDCARD_D1                   13
#define SDCARD_D2                   12
#define SDCARD_D3                   48

// #define CONNECTED_LED_GPIO_NUM		41  //NC pin
// #define ACTIVE_LED_GPIO_NUM			41  //NC pin
// #define BLE_EN_PIN_NUM				42  //NC pin
// #define PWR_LED_GPIO_NUM			41  //NC pin


#else

#define TX_GPIO_NUM             	0
#define RX_GPIO_NUM             	3
#define CONNECTED_LED_GPIO_NUM		8
#define ACTIVE_LED_GPIO_NUM			9
#define BLE_EN_PIN_NUM				5
#define PWR_LED_GPIO_NUM			7
#define CAN_STDBY_GPIO_NUM			6

#endif

#endif
