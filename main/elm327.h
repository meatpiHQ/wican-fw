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


#ifndef __ELM327__
#define __ELM327__

#define ELM327_CAN_RX   0x01
#define ELM327_CAN_TX   0x02

void elm327_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q), QueueHandle_t *rx_queue, void (*can_log)(twai_message_t* frame, uint8_t type));
int8_t elm327_process_cmd(uint8_t *buf, uint8_t len, twai_message_t *frame, QueueHandle_t *q);
char elm327_get_current_protocol(void);
void elm327_lock(void);
void elm327_unlock(void);
uint32_t elm327_get_identifier(void);
uint32_t elm327_get_rx_address(void);
uint8_t elm327_ready_to_receive(void);
#endif
