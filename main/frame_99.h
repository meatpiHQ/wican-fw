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


#ifndef __frame_99__
#define __frame_99__

#include "driver/twai.h"

#define HEADER_SIZE                 3
#define FRAME_BUFFER_SIZE           (HEADER_SIZE + 6 + MAX_PAYLOAD_LENGTH)
#define FRAME_HEADER_0              0x99
#define FRAME_HEADER_1              0x33
#define FRAME_HEADER_2              0x22

#define MAX_PAYLOAD_LENGTH          (4096*2)
#define MAX_CAN_DATA_LENGTH         8

#define FLOW_STATUS_CTS             0x00    // Clear to Send
#define FLOW_STATUS_WT              0x01    // Wait
#define FLOW_STATUS_OVFLW           0x02    // Overflow

// Define CAN Rx and Tx IDs
#define DEFAULT_ECU_RX_ID           0x7E8   // Default CAN ID for ECU to receive messages
#define DEFAULT_ECU_TX_ID           0x7DF   // Default CAN ID for ECU to send responses

// Define block size and separation time defaults
#define DEFAULT_BLOCK_SIZE          255     // Default Block Size (BS) - number of CFs before an FC
#define DEFAULT_SEPARATION_TIME     0       // Default Separation Time (STmin) in ms
#define DEFAULT_PADDING_BYTE        0xAA

#define CMD_ISO_TP_FRAME            0x11
#define CMD_RECEIVE_RAW_CAN_FRAME   0x20
#define CMD_SEND_RAW_CAN_FRAME      0x21
#define CMD_ACK_NAK                 0x01
#define CMD_CAN_ENABLE_DISABLE      0x02
#define CMD_SET_CAN_BITRATE         0x03
#define CMD_SET_ECU_TX_RX_ID        0x04
#define CMD_SET_PADDING_BYTE        0x05

typedef struct {
    uint8_t block_size;
    uint8_t st_min; // Separation Time Minimum
} flow_control_params_t;

typedef enum
{
    FRAME_99_NAK        = 0,
    FRAME_99_ACK        = 1,
    FRAME_99_TIMEOUT    = 2,
    FRAME_99_OVFLW      = 3,
    FRAME_99_FC_TIMEOUT = 4,
    FRAME_99_UFC        = 5,
    FRAME_99_FF_SEND_ERR = 6,
    FRAME_99_SF_SEND_ERR = 7,
    FRAME_99_CF_SEND_ERR = 8,
} frame_99_err_t;

void frame_99_parse_data(const uint8_t *frame, size_t frame_len, QueueHandle_t *frame_99_rsp_q);
int8_t frame_99_parse_can(uint8_t *buf, twai_message_t *frame);
void frame_99_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q));

#endif
