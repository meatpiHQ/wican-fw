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


#ifndef __CAN_H__
#define __CAN_H__
#include "driver/twai.h"
#include "hw_config.h"

typedef enum {
	CAN_BUS_0 = 0,   /* on-chip TWAI controller (OBD-II pins 6/14) */
	CAN_BUS_1 = 1,   /* MCP2515 over SPI, custom board only (OBD-II pins 12/13) */
} can_bus_t;

#define CAN_5K				0
#define CAN_10K				1
#define CAN_20K				2
#define CAN_25K				3
#define CAN_50K				4
#define CAN_100K			5
#define CAN_125K			6
#define CAN_250K			7
#define CAN_500K			8
#define CAN_800K			9
#define CAN_1000K			10
#define CAN_AUTO			11
typedef struct {
	uint8_t bus_state;
	uint8_t silent;
	uint8_t loopback;
	uint8_t auto_tx;
	uint8_t rate;
	uint32_t filter;
	uint32_t mask;
}can_cfg_t;

void can_init(void);
void can_enable(can_bus_t bus);
void can_disable(can_bus_t bus);
void can_set_silent(can_bus_t bus, uint8_t flag);
void can_set_loopback(can_bus_t bus, uint8_t flag);
void can_set_auto_retransmit(can_bus_t bus, uint8_t flag);
void can_set_filter(can_bus_t bus, uint32_t f);
void can_set_mask(can_bus_t bus, uint32_t m);
void can_set_bitrate(can_bus_t bus, uint8_t rate);
esp_err_t can_send(can_bus_t bus, twai_message_t *message, TickType_t ticks_to_wait);
esp_err_t can_receive(twai_message_t *message, can_bus_t *bus, TickType_t ticks_to_wait);
uint8_t can_is_silent(can_bus_t bus);
bool can_is_enabled(can_bus_t bus);
bool can_any_enabled(void);
uint8_t can_get_bitrate(can_bus_t bus);
void can_flush_rx(void);
#endif
