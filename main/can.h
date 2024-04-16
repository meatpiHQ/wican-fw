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
	uint16_t brp;
	uint8_t phase_seg1;
	uint8_t phase_seg2;
	uint8_t sjw;
	uint32_t filter;
	uint32_t mask;
	uint8_t auto_bitrate;
}can_cfg_t;


void can_enable(void);
void can_disable(void);
void can_set_silent(uint8_t flag);
void can_set_loopback(uint8_t flag);
void can_set_auto_retransmit(uint8_t flag);
void can_set_filter(uint32_t f);
void can_set_mask(uint32_t m);
void can_set_bitrate(uint8_t rate);
esp_err_t can_receive(twai_message_t *message, TickType_t ticks_to_wait);
esp_err_t can_send(twai_message_t *message, TickType_t ticks_to_wait);
void can_init(uint8_t bitrate);
uint8_t can_is_silent(void);
bool can_is_enabled(void);
uint8_t can_get_bitrate(void);
uint32_t can_msgs_to_rx(void);

#endif
