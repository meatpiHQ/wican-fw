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

#ifndef __BLE_H__
#define __BLE_H__

void ble_init(QueueHandle_t *xTXp_Queue, QueueHandle_t *xRXp_Queue, uint8_t connected_led, int passkey, uint8_t* uid);
bool ble_connected(void);
void ble_send(uint8_t* buf, uint8_t buf_len);
bool ble_tx_ready(void);
void ble_disable(void);
void ble_enable(void);
// Runtime control of pairing/bonding availability
void ble_pairing_enable(void);
void ble_pairing_disable(void);
bool ble_pairing_is_enabled(void);
#endif
