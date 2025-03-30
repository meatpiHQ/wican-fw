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
#ifndef __WUSB3801_H__
#define __WUSB3801_H__
#include "driver/i2c.h"

#define WUSB3801_ADDRESS            0x60

#define WUSB3801_DEV_ID_REG         0x01
#define WUSB3801_CTRL_REG           0x02
#define WUSB3801_INT_REG            0x03
#define WUSB3801_CC_STAT_REG        0x04

#define WUSB3801_I2C_TIMEOUT_MS          100

void wusb3801_init(i2c_port_t i2c_num);
uint8_t wusb3801_get_dev_id(void);
uint8_t wusb3801_get_cc_stat(void);

#endif
