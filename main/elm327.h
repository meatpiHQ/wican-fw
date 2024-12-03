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

#include "driver/twai.h"

#define OBD_FW_VER		"V2.3.18"

typedef void (*response_callback_t)(char*, uint32_t, QueueHandle_t *q, char* cmd_str);

#define ELM327_CAN_RX   0x01
#define ELM327_CAN_TX   0x02

typedef enum{
	ELM327_READY,
    ELM327_SLEEP
}elm327_chip_status_t;

void elm327_init(QueueHandle_t *rx_queue, void (*can_log)(twai_message_t* frame, uint8_t type));

#if HARDWARE_VER == WICAN_PRO
int8_t elm327_process_cmd(uint8_t *buf, uint8_t len, QueueHandle_t *q, char *cmd_buffer, uint32_t *cmd_buffer_len, int64_t *last_cmd_time, response_callback_t response_callback);
elm327_chip_status_t elm327_chip_get_status(void);
esp_err_t elm327_update_obd(bool force_update);
#else
int8_t elm327_process_cmd(uint8_t *buf, uint8_t len, twai_message_t *frame, QueueHandle_t *q);
#endif

void elm327_run_command(char* command, uint32_t command_len, uint32_t timeout, QueueHandle_t *response_q, response_callback_t response_callback);
esp_err_t elm327_sleep(void);
#endif
