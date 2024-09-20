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

#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include  "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "esp_log.h"
#include <string.h>
#include <stdint.h>
#include "driver/twai.h"
#include "can.h"

#define TAG 		                __func__
#define HEADER_SIZE                 4
#define MAX_PAYLOAD_LENGTH          (4096*2)
#define MAX_CAN_DATA_LENGTH         8
#define FRAME_BUFFER_SIZE           (HEADER_SIZE + 6 + MAX_PAYLOAD_LENGTH)
#define FRAME_HEADER_0              0x99
#define FRAME_HEADER_1              0x33
#define FRAME_HEADER_2              0x22
#define FRAME_HEADER_3              0x11

static uint8_t *frame_buffer;
static size_t buffer_offset = 0;
static uint16_t expected_payload_length = 0;
static uint32_t current_identifier = 0;

static int is_extended_id(uint32_t identifier)
{
    return identifier > 0x7FF; 
}

static void send_can_message(uint32_t identifier, uint8_t *data, size_t len, bool is_extended)
{
    twai_message_t message;
    message.identifier = identifier;
    message.extd = is_extended ? 1 : 0;
    message.rtr = 0;
    message.data_length_code = len; 
    message.self = 0;
    memcpy(message.data, data, len);
    
    if (can_send(&message, pdMS_TO_TICKS(100)) != ESP_OK)
    {
        ESP_LOGE(TAG, "Error sending CAN message");
    }
}

static void reset_buffer()
{
    buffer_offset = 0;
    expected_payload_length = 0;
    current_identifier = 0;
}

static bool is_valid_header(const uint8_t *buffer)
{
    return (buffer[0] == FRAME_HEADER_0 &&
            buffer[1] == FRAME_HEADER_1 &&
            buffer[2] == FRAME_HEADER_2 &&
            buffer[3] == FRAME_HEADER_3);
}

static void shift_buffer()
{
    memmove(frame_buffer, frame_buffer + 1, buffer_offset - 1);
    buffer_offset--;
}

void frame_99_parse_data(const uint8_t *frame, size_t frame_len)
{
    if (frame == NULL || frame_len == 0)
    {
        ESP_LOGE(TAG, "Invalid frame data");
        return;
    }

    if (buffer_offset + frame_len > FRAME_BUFFER_SIZE)
    {
        ESP_LOGE(TAG, "Buffer overflow detected. Resetting buffer.");
        reset_buffer();
        return;
    }

    memcpy(&frame_buffer[buffer_offset], frame, frame_len);
    buffer_offset += frame_len;

    while (buffer_offset >= HEADER_SIZE)
    {
        if (!is_valid_header(frame_buffer))
        {
            shift_buffer();
            continue;
        }

        if (buffer_offset < HEADER_SIZE + 6)
        {
            return;
        }

        if (expected_payload_length == 0)
        {
            current_identifier = (frame_buffer[HEADER_SIZE] << 24) | 
                                 (frame_buffer[HEADER_SIZE + 1] << 16) |
                                 (frame_buffer[HEADER_SIZE + 2] << 8) |
                                 frame_buffer[HEADER_SIZE + 3];
            
            expected_payload_length = (frame_buffer[HEADER_SIZE + 4] << 8) | 
                                       frame_buffer[HEADER_SIZE + 5];

            if (expected_payload_length > MAX_PAYLOAD_LENGTH)
            {
                ESP_LOGE(TAG, "Invalid payload length: %u", expected_payload_length);
                reset_buffer();
                return;
            }
        }

        size_t total_frame_size = HEADER_SIZE + 6 + expected_payload_length;
        if (buffer_offset < total_frame_size)
        {
            return;  
        }

        bool is_extended = is_extended_id(current_identifier);

        const uint8_t *payload_ptr = &frame_buffer[HEADER_SIZE + 6];
        size_t bytes_remaining = expected_payload_length;

        while (bytes_remaining > 0)
        {
            size_t chunk_size = bytes_remaining > MAX_CAN_DATA_LENGTH ? MAX_CAN_DATA_LENGTH : bytes_remaining;

            send_can_message(current_identifier, (uint8_t *)payload_ptr, chunk_size, is_extended);

            payload_ptr += chunk_size;
            bytes_remaining -= chunk_size;
        }

        reset_buffer();
    }
}

int8_t frame_99_parse_can(uint8_t *buf, twai_message_t *message)
{
    if (buf == NULL || message == NULL)
    {
        return -1; 
    }

    size_t offset = 0;

    buf[offset++] = 0x99;
    buf[offset++] = 0x33;
    buf[offset++] = 0x22;
    buf[offset++] = 0x11;

    buf[offset++] = (message->identifier >> 24) & 0xFF;
    buf[offset++] = (message->identifier >> 16) & 0xFF;
    buf[offset++] = (message->identifier >> 8) & 0xFF;
    buf[offset++] = message->identifier & 0xFF;

    buf[offset++] = 0x00;
    buf[offset++] = message->data_length_code;

    memcpy(&buf[offset], message->data, message->data_length_code);
    offset += message->data_length_code;

    return (int8_t)offset;
}

void frame_99_init(void)
{
    frame_buffer = malloc(FRAME_BUFFER_SIZE);
    if(frame_buffer == NULL)
    {
        ESP_LOGE(TAG, "Failed to malloc memory");
    }
}
