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
#include "freertos/queue.h"
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
#include "frame_99.h"

#define TAG                         "frame_99"

#define WAIT_FC_FRAME_BIT 			BIT0

static uint8_t *frame_buffer;
static size_t buffer_offset = 0;
static uint16_t expected_payload_length = 0;

// Configurable variables
static uint32_t ecu_rx_id = DEFAULT_ECU_RX_ID;                  // Configurable Rx ID
static uint32_t ecu_tx_id = DEFAULT_ECU_TX_ID;                  // Configurable Tx ID
static uint8_t block_size = DEFAULT_BLOCK_SIZE;                 // Configurable block size
static uint8_t separation_time = DEFAULT_SEPARATION_TIME;       // Configurable separation time (ms)
static uint8_t frame_99_padding_byte = DEFAULT_PADDING_BYTE;    // Configurable padding byte
static QueueHandle_t frame_99_can_rx_queue;

static void (*frame_99_send_response)(char*, uint32_t, QueueHandle_t *q);
static EventGroupHandle_t frame_99_group = NULL;


static int is_extended_id(uint32_t identifier)
{
    return identifier > 0x7FF; 
}

// Function to receive flow control frames from the ECU's Tx ID
static frame_99_err_t receive_flow_control_frame(uint32_t identifier, bool is_extended, flow_control_params_t *fc_params)
{
    twai_message_t rx_message;
    TickType_t start_time = xTaskGetTickCount();
    const TickType_t timeout = pdMS_TO_TICKS(1000); // 1 second timeout

    while ((xTaskGetTickCount() - start_time) < timeout)
    {
        // Check if a message is received
        // if (can_receive(&rx_message, pdMS_TO_TICKS(1000)) == ESP_OK)
        if(xQueueReceive(frame_99_can_rx_queue, &rx_message, 0) == pdPASS)
        {
            // Filter messages based on identifier (use Tx ID) and frame type
            if (rx_message.identifier == identifier &&
                rx_message.extd == (is_extended ? 1 : 0))
            {
                uint8_t pci_type = rx_message.data[0] & 0xF0;

                if (pci_type == 0x30)
                {
                    // Flow Control frame received
                    uint8_t flow_status = rx_message.data[0] & 0x0F;
                    uint8_t block_size = rx_message.data[1];
                    uint8_t st_min = rx_message.data[2];

                    if (flow_status == FLOW_STATUS_CTS)
                    {
                        // Continue to Send (CTS)
                        fc_params->block_size = block_size;
                        fc_params->st_min = st_min;
                        ESP_LOGW(TAG, "Continue to Send (CTS), block_size: %u, st_min: %u", fc_params->block_size, fc_params->st_min);
                        return FRAME_99_ACK;
                    }
                    else if (flow_status == FLOW_STATUS_WT)
                    {
                        // Wait (WT), handle it accordingly if needed
                        ESP_LOGE(TAG, "Flow Control: Wait requested by receiver");
                        vTaskDelay(pdMS_TO_TICKS(1));  // Wait 100 ms before retrying
                        continue;
                    }
                    else if (flow_status == FLOW_STATUS_OVFLW)
                    {
                        // Overflow (OVFLW)
                        ESP_LOGE(TAG, "Flow Control: Receiver overflowed");
                        return FRAME_99_OVFLW;
                    }
                    else
                    {
                        // Unsupported flow status
                        ESP_LOGE(TAG, "Flow Control: Unsupported flow status 0x%02X", flow_status);
                        return FRAME_99_UFC;
                    }
                }
                else
                {
                    // Not a Flow Control frame; ignore and continue waiting
                    continue;
                }
            }
        }
    }

    ESP_LOGE(TAG, "Flow Control frame not received within timeout");
    return FRAME_99_FC_TIMEOUT;
}

// Function to send CAN messages to the ECU's Rx ID
static frame_99_err_t send_can_message(uint8_t *data, size_t len, bool is_extended)
{
    twai_message_t message;
    ESP_LOGI(TAG, "send_can_message: len: %u", len);
    message.identifier = ecu_rx_id;
    message.extd = is_extended ? 1 : 0;
    message.rtr = 0;
    message.self = 0;
    
    if (len <= 7)
    {
        // Single Frame (SF)
        message.data[0] = 0x00 | len;  // PCI with Single Frame type and length
        memcpy(&message.data[1], data, len); 

        message.data_length_code = len + 1;  // DLC = data + PCI
        ESP_LOGI(TAG, "Sending Single Frame (SF),       ID: %lX DLC: %u Data: %02X %02X %02X %02X %02X %02X %02X %02X", 
                                message.identifier, message.data_length_code, message.data[0],message.data[1],message.data[2],
                                message.data[3],message.data[4],message.data[5],message.data[6],message.data[7]);
        if (can_send(&message, pdMS_TO_TICKS(1000)) != ESP_OK)
        {
            ESP_LOGE(TAG, "Error sending Single Frame CAN message");
            return FRAME_99_SF_SEND_ERR;
        }
    }
    else
    {
        // Multi-frame message
        // First Frame (FF)
        message.data[0] = 0x10 | ((len >> 8) & 0x0F);  // First Frame PCI and upper nibble of length
        message.data[1] = len & 0xFF;  // Lower byte of length
        memcpy(&message.data[2], data, 6);  // First 6 bytes of data
        message.data_length_code = 8;

        // Send the first frame to the ECU's Rx ID
        message.identifier = ecu_rx_id;  // Use the Rx ID
        ESP_LOGI(TAG, "Sending First Frame (FF),       ID: %lX DLC: %u Data: %02X %02X %02X %02X %02X %02X %02X %02X", 
                                message.identifier, message.data_length_code, message.data[0],message.data[1],message.data[2],
                                message.data[3],message.data[4],message.data[5],message.data[6],message.data[7]);

        //expect FC frame after send
        xEventGroupSetBits(frame_99_group, WAIT_FC_FRAME_BIT);
        if (can_send(&message, pdMS_TO_TICKS(1000)) != ESP_OK)
        {
            ESP_LOGE(TAG, "Error sending First Frame CAN message");
            return FRAME_99_FF_SEND_ERR;
        }

        // Wait for Flow Control (FC) frame from the ECU's Tx ID
        flow_control_params_t fc_params;
        frame_99_err_t fc_result = receive_flow_control_frame(ecu_tx_id, is_extended, &fc_params);

        xEventGroupClearBits(frame_99_group, WAIT_FC_FRAME_BIT);
        if (fc_result != FRAME_99_ACK)
        {
            ESP_LOGE(TAG, "Failed to receive Flow Control frame");
            return fc_result;
        }

        // Update block size and separation time based on the FC frame received
        block_size = fc_params.block_size;
        separation_time = fc_params.st_min;

        size_t bytes_sent = 6; // Already sent in FF
        uint8_t sequence_number = 1;
        uint8_t frames_sent_in_block = 0;

        while (bytes_sent < len)
        {
            // Handle Block Size (BS)
            if (block_size != 0 && frames_sent_in_block >= block_size)
            {
                // Wait for next Flow Control frame
                fc_result = receive_flow_control_frame(ecu_tx_id, is_extended, &fc_params);

                xEventGroupClearBits(frame_99_group, WAIT_FC_FRAME_BIT);

                if (fc_result != FRAME_99_ACK)
                {
                    ESP_LOGE(TAG, "Failed to receive Flow Control frame during block transfer");
                    return fc_result;
                }

                frames_sent_in_block = 0;
                block_size = fc_params.block_size;
                separation_time = fc_params.st_min;
            }

            // Prepare Consecutive Frame (CF)
            size_t bytes_to_send = (len - bytes_sent > 7) ? 7 : (len - bytes_sent);  // Max 7 bytes in a CF
            message.data[0] = 0x20 | (sequence_number & 0x0F);  // CF PCI and sequence number
            memcpy(&message.data[1], data + bytes_sent, bytes_to_send);  // Copy data
            if (bytes_to_send < 7)
            {
                // Pad the remaining bytes with 0xAA if less than 7 bytes
                memset(&message.data[1 + bytes_to_send], frame_99_padding_byte, 7 - bytes_to_send);
            }
            message.data_length_code = 8;

            // Respect Separation Time (STmin)
            if (separation_time > 0)
            {
                vTaskDelay(pdMS_TO_TICKS(separation_time));
            }

            // Send CF to the ECU's Rx ID
            message.identifier = ecu_rx_id;  // Use the Rx ID for sending consecutive frames

            // ESP_LOGI(TAG, "Sending Consecutive Frame (CF), ID: %lX DLC: %u Data: %02X %02X %02X %02X %02X %02X %02X %02X", 
            //                         message.identifier, message.data_length_code, message.data[0],message.data[1],message.data[2],
            //                         message.data[3],message.data[4],message.data[5],message.data[6],message.data[7]);

            //expect FC frame after frames_sent_in_block+1
            if (block_size != 0 && frames_sent_in_block+1 >= block_size)
            {
                xEventGroupSetBits(frame_99_group, WAIT_FC_FRAME_BIT);
            }

            if (can_send(&message, pdMS_TO_TICKS(1000)) != ESP_OK)
            {
                ESP_LOGE(TAG, "Error sending Consecutive Frame CAN message");
                return FRAME_99_CF_SEND_ERR;
            }

            bytes_sent += bytes_to_send;
            sequence_number = (sequence_number + 1) & 0x0F;  // Keep sequence number within 4 bits (0-15)
            frames_sent_in_block++;
        }
    }

    return FRAME_99_ACK;
}

static void reset_buffer()
{
    buffer_offset = 0;
    expected_payload_length = 0;
}

static bool is_valid_header(const uint8_t *buffer)
{
    return (buffer[0] == FRAME_HEADER_0 &&
            buffer[1] == FRAME_HEADER_1 &&
            buffer[2] == FRAME_HEADER_2 );
}

static void shift_buffer()
{
    memmove(frame_buffer, frame_buffer + 1, buffer_offset - 1);
    buffer_offset--;
}

static void send_cmd_response(uint8_t cmd, frame_99_err_t err, QueueHandle_t *frame_99_rsp_q)
{
    uint8_t rsp[] = {FRAME_HEADER_0, FRAME_HEADER_1, FRAME_HEADER_2, CMD_ACK_NAK, cmd, err};

    frame_99_send_response((char*)rsp, 6, frame_99_rsp_q);
}

void frame_99_parse_data(const uint8_t *frame, size_t frame_len, QueueHandle_t *frame_99_rsp_q)
{
    ESP_LOGI(TAG, "Parsing frame, len: %u", frame_len);
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

    // Ensure there's enough data for the header and command
    while (buffer_offset >= HEADER_SIZE + 1)
    {
        // Check if the header is valid
        if (!is_valid_header(frame_buffer))
        {
            shift_buffer();
            continue;
        }

        // Ensure we have enough data for header + command + at least 1 byte of data
        if (buffer_offset < HEADER_SIZE + 2)
        {
            return;  // Wait for more data
        }

        uint8_t command = frame_buffer[HEADER_SIZE]; // Extract the command byte

        switch (command)
        {
            case CMD_ISO_TP_FRAME:  // ISO-TP Frame (Command 0x11)
                if (buffer_offset < HEADER_SIZE + 3 + 2) // Ensure we have header + command + length
                {
                    return;  // Wait for more data
                }

                if (expected_payload_length == 0)
                {
                    // Extract ISO-TP length (2 bytes)
                    expected_payload_length = (frame_buffer[HEADER_SIZE + 1] << 8) |
                                              frame_buffer[HEADER_SIZE + 2];

                    if (expected_payload_length > MAX_PAYLOAD_LENGTH)
                    {
                        ESP_LOGE(TAG, "Invalid payload length: %u", expected_payload_length);
                        reset_buffer();
                        send_cmd_response(command, FRAME_99_NAK, frame_99_rsp_q);
                        return;
                    }
                }

                // Ensure we have the complete frame
                size_t total_frame_size = HEADER_SIZE + 3 + expected_payload_length;
                if (buffer_offset < total_frame_size)
                {
                    return;  // Wait for the rest of the data
                }

                // Extract the payload and process it
                const uint8_t *payload_ptr = &frame_buffer[HEADER_SIZE + 3];
                bool is_extended = is_extended_id(ecu_rx_id);

                frame_99_err_t fc_result = send_can_message((uint8_t *)payload_ptr, expected_payload_length, is_extended);

                if (fc_result != FRAME_99_ACK)
                {
                    ESP_LOGW(TAG, "send_can_message returned error: 0x%02X", fc_result);
                }
                else
                {
                    ESP_LOGI(TAG, "send_can_message returned ACK");
                }

                send_cmd_response(command, fc_result, frame_99_rsp_q);
                reset_buffer();  // Reset for the next frame
                break;

            case CMD_SET_ECU_TX_RX_ID:  // Set ECU TX/RX ID (Command 0x04)
                if (buffer_offset < HEADER_SIZE + 9) // Ensure we have header + command + ECU TX ID (4 bytes) + ECU RX ID (4 bytes)
                {
                    return;  // Wait for more data
                }

                // Extract the ECU RX ID (4 bytes)
                ecu_rx_id = (frame_buffer[HEADER_SIZE + 1] << 24) |
                            (frame_buffer[HEADER_SIZE + 2] << 16) |
                            (frame_buffer[HEADER_SIZE + 3] << 8) |
                            frame_buffer[HEADER_SIZE + 4];

                // Extract the ECU TX ID (4 bytes)
                ecu_tx_id = (frame_buffer[HEADER_SIZE + 5] << 24) |
                            (frame_buffer[HEADER_SIZE + 6] << 16) |
                            (frame_buffer[HEADER_SIZE + 7] << 8) |
                            frame_buffer[HEADER_SIZE + 8];

                ESP_LOGI(TAG, "Setting ECU RX ID: 0x%08lX, ECU TX ID: 0x%08lX", ecu_rx_id, ecu_tx_id);

                send_cmd_response(command, FRAME_99_ACK, frame_99_rsp_q); // Acknowledge success
                reset_buffer();  // Reset for the next frame
                break;

            case CMD_CAN_ENABLE_DISABLE:  // CAN Enable/Disable (Command 0x02)
                if (buffer_offset < HEADER_SIZE + 2) // Ensure we have header + command + enable byte
                {
                    return;  // Wait for more data
                }

                uint8_t enable = frame_buffer[HEADER_SIZE + 1]; // Extract the enable/disable flag
                if (enable == 0x01)
                {
                    ESP_LOGI(TAG, "Enabling CAN communication");
                    can_enable(); // Placeholder for enabling CAN
                }
                else if (enable == 0x00)
                {
                    ESP_LOGI(TAG, "Disabling CAN communication");
                    can_disable(); // Placeholder for disabling CAN
                }
                else
                {
                    ESP_LOGE(TAG, "Invalid enable/disable value: 0x%02X", enable);
                    send_cmd_response(command, FRAME_99_NAK, frame_99_rsp_q);
                    reset_buffer();
                    return;
                }

                send_cmd_response(command, FRAME_99_ACK, frame_99_rsp_q); // Acknowledge success
                reset_buffer();  // Reset for the next frame
                break;

            case CMD_SET_CAN_BITRATE:  // Set CAN Bitrate (Command 0x03)
                if (buffer_offset < HEADER_SIZE + 2) // Ensure we have header + command + bitrate byte
                {
                    return;  // Wait for more data
                }

                uint8_t bitrate = frame_buffer[HEADER_SIZE + 1]; // Extract the bitrate value

                if (bitrate <= 0x0A) // Assuming valid bitrate values are 0x00 to 0x0A
                {
                    ESP_LOGI(TAG, "Setting CAN bitrate: 0x%02X", bitrate);
                    can_set_bitrate(bitrate); // Placeholder for setting CAN bitrate
                }
                else
                {
                    ESP_LOGE(TAG, "Invalid CAN bitrate: 0x%02X", bitrate);
                    send_cmd_response(command, FRAME_99_NAK, frame_99_rsp_q);
                    reset_buffer();
                    return;
                }

                send_cmd_response(command, FRAME_99_ACK, frame_99_rsp_q); // Acknowledge success
                reset_buffer();  // Reset for the next frame
                break;

            case CMD_SET_PADDING_BYTE:  // Set Padding Byte (Command 0x05)
                if (buffer_offset < HEADER_SIZE + 2) // Ensure we have header + command + padding byte
                {
                    return;  // Wait for more data
                }

                uint8_t padding_byte = frame_buffer[HEADER_SIZE + 1]; // Extract the padding byte value

                ESP_LOGI(TAG, "Setting padding byte: 0x%02X", padding_byte);
                frame_99_padding_byte = padding_byte; // Placeholder for setting the padding byte value

                send_cmd_response(command, FRAME_99_ACK, frame_99_rsp_q); // Acknowledge success
                reset_buffer();  // Reset for the next frame
                break;

            case CMD_SEND_RAW_CAN_FRAME:  // Send RAW CAN Frame (Command 0x21)
                if (buffer_offset < HEADER_SIZE + 6)  // Ensure we have header + command + CAN ID + RTR + DLC
                {
                    return;  // Wait for more data
                }

                // Extract CAN ID (4 bytes)
                uint32_t can_id = (frame_buffer[HEADER_SIZE + 1] << 24) |
                                (frame_buffer[HEADER_SIZE + 2] << 16) |
                                (frame_buffer[HEADER_SIZE + 3] << 8) |
                                frame_buffer[HEADER_SIZE + 4];

                // Extract RTR (1 byte)
                uint8_t rtr = frame_buffer[HEADER_SIZE + 5];

                // Extract DLC (1 byte)
                uint8_t dlc = frame_buffer[HEADER_SIZE + 6];

                // Ensure DLC is valid and the buffer has enough data for the payload
                if (dlc > MAX_CAN_DATA_LENGTH || buffer_offset < HEADER_SIZE + 7 + dlc)
                {
                    ESP_LOGE(TAG, "Invalid DLC or incomplete frame: DLC=%u", dlc);
                    send_cmd_response(command, FRAME_99_NAK, frame_99_rsp_q);
                    reset_buffer();
                    return;
                }

                // Extract payload (0-8 bytes)
                uint8_t payload[MAX_CAN_DATA_LENGTH] = {0};
                memcpy(payload, &frame_buffer[HEADER_SIZE + 7], dlc);

                ESP_LOGI(TAG, "Sending RAW CAN frame: CAN ID=0x%08lX, RTR=%u, DLC=%u", can_id, rtr, dlc);

                // Create and send the CAN message (use placeholder function)
                twai_message_t tx_message;
                tx_message.identifier = can_id;
                tx_message.rtr = (rtr == 0x01);  // Set RTR flag
                tx_message.data_length_code = dlc;
                memcpy(tx_message.data, payload, dlc);

                // Send the message (assuming a placeholder function `send_raw_can_frame`)
                frame_99_err_t send_result = FRAME_99_ACK;

                if (can_send(&tx_message, pdMS_TO_TICKS(10)) != ESP_OK)
                {
                    ESP_LOGE(TAG, "Error sending First Frame CAN message");
                    send_result = FRAME_99_NAK;
                }
                
                send_cmd_response(command, send_result, frame_99_rsp_q);
                reset_buffer();  // Reset for the next frame
                break;
                
            default:
                ESP_LOGW(TAG, "Unknown command: 0x%02X", command);
                send_cmd_response(command, FRAME_99_NAK, frame_99_rsp_q);
                reset_buffer();  // Reset the buffer for unknown command
                break;
        }
    }
}

int8_t frame_99_parse_can(uint8_t *buf, twai_message_t *message)
{
    if (buf == NULL || message == NULL)
    {
        return -1; 
    }

    if(xEventGroupGetBits(frame_99_group) & WAIT_FC_FRAME_BIT)
    {
        // Check if the queue is full
        if (uxQueueSpacesAvailable(frame_99_can_rx_queue) == 0)
        {
            // Queue is full, remove the oldest item to make space
            twai_message_t dummy_message;
            xQueueReceive(frame_99_can_rx_queue, &dummy_message, 0);  // Discard the oldest message
            ESP_LOGW(TAG, "Queue full, overwriting oldest message");
        }

        // Send the new message to the queue
        if (xQueueSend(frame_99_can_rx_queue, message, 0) != pdPASS)
        {
            ESP_LOGE(TAG, "Failed to send message to TWAI RX queue");
        }

        return 0;
    }

    size_t offset = 0;

    // Add the FRAME 99 Protocol header
    buf[offset++] = FRAME_HEADER_0;
    buf[offset++] = FRAME_HEADER_1;
    buf[offset++] = FRAME_HEADER_2;

    // Set the command byte for RAW CAN frame
    buf[offset++] = CMD_RECEIVE_RAW_CAN_FRAME;

    // Add the CAN identifier (supports extended IDs)
    buf[offset++] = (message->identifier >> 24) & 0xFF;
    buf[offset++] = (message->identifier >> 16) & 0xFF;
    buf[offset++] = (message->identifier >> 8) & 0xFF;
    buf[offset++] = message->identifier & 0xFF;

    // Add the RTR (Remote Transmission Request) field
    // `0x01` for RTR frame, `0x00` for normal data frame
    buf[offset++] = message->rtr ? 0x01 : 0x00;

    // Add the DLC (Data Length Code)
    buf[offset++] = message->data_length_code;

    // Copy the CAN payload (0 to 8 bytes)
    if (message->data_length_code > 0)
    {
        memcpy(&buf[offset], message->data, message->data_length_code);
        offset += message->data_length_code;
    }

    return (int8_t)offset;
}

void frame_99_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q))
{
    frame_99_send_response = send_to_host;
    frame_buffer = malloc(FRAME_BUFFER_SIZE);
    if (frame_buffer == NULL)
    {
        ESP_LOGE(TAG, "Failed to malloc memory");
    }
    
    frame_99_can_rx_queue = xQueueCreate(CAN_RX_QUEUE_LEN, sizeof(twai_message_t));
    if (frame_99_can_rx_queue == NULL)
    {
        ESP_LOGE(TAG, "Failed to create queue");
    } 

    frame_99_group =  xEventGroupCreate();
    if (frame_99_group == NULL)
    {
        ESP_LOGE(TAG, "Failed to create group");
    } 
    else
    {
        xEventGroupClearBits(frame_99_group, WAIT_FC_FRAME_BIT);
    }
}
