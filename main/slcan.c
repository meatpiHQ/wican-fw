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
#include "driver/twai.h"
#include "slcan.h"
#include "can.h"


#define TAG 				__func__
#define SLCAN_BUFFER_SIZE 	(2*1024)
#define SL_FRAME_ID			0x00
#define SL_FRAME_DLC		0x01
#define SL_FRAME_DATA		0x02
#define SL_HEADER			0x00
#define SL_BODY				0x01
#define SL_END				0x02

static const char serial[] = "N43010123\r";
static const char version[] = "V2011\r";
static const char ack[] = "\r";
static const char status[] = "F00\r";

static uint8_t timestamp_flag = 0;
static uint8_t sl_bitrate[] = {CAN_10K, CAN_20K, CAN_50K, CAN_100K,
								CAN_125K, CAN_250K, CAN_500K,
								CAN_800K, CAN_1000K};
void (*slcan_response)(char*, uint32_t, QueueHandle_t *q, char* cmd_str);

static uint16_t slcan_get_time(void)
{
	return pdTICKS_TO_MS(xTaskGetTickCount())%60000;
}

int8_t slcan_parse_frame(uint8_t *buf, twai_message_t *frame)
{
    uint8_t i = 0, j = 0;

    uint32_t tmp;
	uint8_t id_len = SLCAN_STD_ID_LEN;

    for (j=0; j < SLCAN_MTU; j++)
    {
        buf[j] = '\0';
    }

	if(frame->rtr == 0)
    {
        buf[i] = 't';
    }
	else
	{
        buf[i] = 'r';
    }

    id_len = SLCAN_STD_ID_LEN;
    tmp = frame->identifier;
    if(frame->extd == 1)
    {
        buf[i] -= 32;
        id_len = SLCAN_EXT_ID_LEN;
        tmp = frame->identifier;
    }
    i++;

    for(j = id_len; j > 0; j--)
    {
        buf[j] = (tmp & 0xF);
        tmp = tmp >> 4;
        i++;
    }

    buf[i++] = frame->data_length_code;

    for (j = 0; j < frame->data_length_code; j++)
    {
        buf[i++] = (frame->data[j] >> 4);
        buf[i++] = (frame->data[j] & 0x0F);
    }

    if(timestamp_flag)
    {
		uint32_t time_now = slcan_get_time();
		uint8_t ts1, ts0;
		ts1 = (time_now & 0xFF00) >> 8;
		ts0 = (time_now & 0xFF);

		buf[i++] = ts1 >> 4;
		buf[i++] = ts1 & 0x0F;
		buf[i++] = ts0 >> 4;
		buf[i++] = ts0 & 0x0F;
    }

    for (j = 1; j < i; j++)
    {
        if (buf[j] < 0xA) {
            buf[j] += 0x30;
        } else {
            buf[j] += 0x37;
        }
    }

    buf[i++] = '\r';

    return i;
}

static uint8_t ascii_to_num(uint8_t a)
{
	uint8_t x = a;
	if(x >= 'a')
		x = x - 'a' + 10;
	// Uppercase letters
	else if(x >= 'A')
		x = x - 'A' + 10;
	// Numbers
	else
		x = x - '0';

	return x;
}

static uint8_t slcan_frame_index = 0;
static uint8_t slcan_frame_id[8] = {0};
static uint8_t slcan_frame_data_nibble = 0;
static uint8_t slcan_frame_state = SL_FRAME_ID;

static void slcan_frame_parser_reset(void)
{
	slcan_frame_index = 0;
	slcan_frame_data_nibble = 0;
	slcan_frame_state = SL_FRAME_ID;
	memset(slcan_frame_id, 0, sizeof(slcan_frame_id));
}

static uint8_t slcan_set_frame(uint8_t byte, twai_message_t *frame, uint8_t msg_type)
{
	switch(slcan_frame_state)
	{
		case SL_FRAME_ID:
		{
			if(msg_type == SL_DATA_EXT || msg_type == SL_REMOTE_EXT)
			{
				slcan_frame_id[slcan_frame_index++] = ascii_to_num(byte);

				if(slcan_frame_index == 8)
				{
					frame->extd = 1;
					frame->identifier = (((slcan_frame_id[6]<<4)+slcan_frame_id[7]) & 0xFF) |
							((((slcan_frame_id[4]<<4)+slcan_frame_id[5]) << 8) & 0x0000FF00) |
							((((slcan_frame_id[2]<<4)+slcan_frame_id[3]) << 16) & 0x00FF0000) |
							((((slcan_frame_id[0]<<4)+slcan_frame_id[1]) << 24) & 0xFF000000);
					slcan_frame_state = SL_FRAME_DLC;
					slcan_frame_index = 0;
				}
			}
			else
			{
				slcan_frame_id[slcan_frame_index++] = ascii_to_num(byte);

				if(slcan_frame_index == 3)
				{
					frame->extd = 0;
					frame->identifier = (((slcan_frame_id[1]<<4)+slcan_frame_id[2]) & 0xFF) |
							((slcan_frame_id[0]<<8) & 0x00000F00) ;
					slcan_frame_state = SL_FRAME_DLC;
					slcan_frame_index = 0;
				}
			}
			return 0;
		}
		case SL_FRAME_DLC:
		{
			frame->data_length_code = ascii_to_num(byte);

			if(msg_type == SL_REMOTE_STD || msg_type == SL_REMOTE_EXT)
			{
				slcan_frame_parser_reset();
				return 1;
			}

			slcan_frame_state = SL_FRAME_DATA;
			return 0;
		}
		case SL_FRAME_DATA:
		{

			if((slcan_frame_index+1)%2)
			{
				slcan_frame_data_nibble = ascii_to_num(byte);
			}
			else
			{
				frame->data[slcan_frame_index/2] = (slcan_frame_data_nibble<<4)+ascii_to_num(byte);
			}

			slcan_frame_index++;

			if(slcan_frame_index == frame->data_length_code*2)
			{
				slcan_frame_parser_reset();
				return 1;
			}

			return 0;
		}
	}
	return 0;
}

char* slcan_parse_str(uint8_t *buf, uint8_t len, twai_message_t *frame, QueueHandle_t *q)
{
//	twai_message_t frame;
	uint32_t i;
	static uint8_t state = SL_HEADER;
	static uint8_t cmd = SL_NONE;
//	static uint8_t ext_flag = 0;
	static uint8_t cmd_ret = SL_NONE;
	static int64_t time_now, time_old;
	static uint8_t msg_index = 0;
	static uint32_t filter[8];
	static uint32_t mask[8];
	static uint8_t loopback = 0;
	static char slcan_buffer[SLCAN_BUFFER_SIZE];
	static uint32_t buffer_index = 0;

	time_now = esp_timer_get_time();

	/* Idle gap: drop incomplete prior command before appending the new chunk. */
	if(time_old != 0 && (time_now - time_old > 100*1000))
	{
		state = SL_HEADER;
		cmd = SL_NONE;
		cmd_ret = SL_NONE;
		msg_index = 0;
		buffer_index = 0;
		slcan_frame_parser_reset();
	}

	if(buffer_index + len > SLCAN_BUFFER_SIZE)
	{
		ESP_LOGW(TAG, "SLCAN buffer overflow, resetting parser");
		state = SL_HEADER;
		cmd = SL_NONE;
		cmd_ret = SL_NONE;
		msg_index = 0;
		buffer_index = 0;
		slcan_frame_parser_reset();
	}

	memcpy(&slcan_buffer[buffer_index], buf, len);
	buffer_index += len;
	time_old = time_now;

	for(i = 0; i < buffer_index; i++)
	{
		switch(state)
		{
			case SL_HEADER:
			{
			    if (slcan_buffer[i] == 'O')
			    {
			    	ESP_LOGI(TAG, "open can!");
			        // Open channel command
			    	can_set_silent(0);
			    	can_set_loopback(0);
			    	loopback = 0;
					can_enable();
			    	state = SL_END;
			    	cmd = SL_OPEN;
			    	break;

			    }
			    else if (slcan_buffer[i] == 'Y')
			    {
			      // Open loop back mode
					can_set_silent(0);
					can_set_loopback(1);
					loopback = 1;
					can_enable();
			    	state = SL_END;
			    	cmd = SL_OPEN;
			    	break;

			    }
			    else if (slcan_buffer[i] == 'C')
			    {
			        // Close channel command
					ESP_LOGI(TAG, "open close!");
			    	can_disable();
			    	state = SL_END;
			    	cmd = SL_CLOSE;
			    	break;

			    }
			    else if (slcan_buffer[i] == 'L')
			    {
					can_set_silent(1);
					can_set_loopback(0);
					loopback = 0;
			    	can_enable();
			    	state = SL_END;
			    	cmd = SL_MUTE;
			    	break;

			    }
			    else if (slcan_buffer[i] == 'S')
			    {
			    	ESP_LOGI(TAG, "set datarate");
			      // Set bitrate command
			    	state = SL_BODY;
			    	cmd = SL_SPEED;
			    	break;

			    }
			    else if (slcan_buffer[i] == 's')
			    {
			      // Set bitrate command
			    	state = SL_BODY;
			    	cmd = SL_CUS_SPEED;
			    	break;

			    }
			    else if (slcan_buffer[i] == 'a' || slcan_buffer[i] == 'A')
			    {
			      // Set autoretry command
			    	state = SL_BODY;
			    	cmd = SL_AUTO;
			    	break;

			    }
				else if (slcan_buffer[i] == 'v' || slcan_buffer[i] == 'V')
				{
			      // Report firmware version and remote
			    	state = SL_END;
			    	cmd = SL_VER;
			    	break;

			    }
				else if (slcan_buffer[i] == 't' || slcan_buffer[i] == 'T')
				{
//						rtr_flag = 0;
					state = SL_BODY;
					if(slcan_buffer[i] == 't')
					{
						cmd = SL_DATA_STD;
					}
					else
					{
						cmd = SL_DATA_EXT;
					}
					break;

			    }
				else if (slcan_buffer[i] == 'r' || slcan_buffer[i] == 'R')
				{
//						rtr_flag = 1;
					state = SL_BODY;
					if(slcan_buffer[i] == 'r')
					{
						cmd = SL_REMOTE_STD;
					}
					else
					{
						cmd = SL_REMOTE_EXT;
					}
					break;
			    }
				else if(slcan_buffer[i] == 'N')
				{
					cmd = SL_SERIAL;
					state = SL_END;
					break;
				}
				else if(slcan_buffer[i] == 'Z')
				{
					cmd = SL_TSTAMP;
					state = SL_BODY;
					break;
				}
				else if(slcan_buffer[i] == 'D')
				{
					cmd = SL_AUTO;
					state = SL_BODY;
					break;
				}
				else if(slcan_buffer[i] == 'm')
				{
					cmd = SL_MASK_REG;
					state = SL_BODY;
					break;
				}
				else if(slcan_buffer[i] == 'M')
				{
					cmd = SL_ACP_CODE;
					state = SL_BODY;
					break;
				}
				else if(slcan_buffer[i] == 'F')
				{
			    	state = SL_END;
			    	cmd = SL_STATUS;
					break;
				}
				else
				{
			        // Error, unknown command
					break;
			    }

			}
			case SL_BODY:
			{
				if(cmd == SL_SPEED || cmd == SL_MUTE || cmd == SL_AUTO ||
					cmd == SL_TSTAMP || cmd == SL_ACP_CODE || cmd == SL_MASK_REG)
				{
					switch(cmd)
					{
						case SL_SPEED:
						{
							uint8_t b = ascii_to_num(slcan_buffer[i]);
//							//ESP_LOGI(TAG, "slcan_buffer[i]: PRIxx", slcan_buffer[i]);
					    	// Check for valid bitrate
							if(b > 8)
							{
//									can_set_bitrate(b);
								state = SL_END;
								break;
							}

							can_set_bitrate(sl_bitrate[b]);
							state = SL_END;
							break;
						}
						case SL_AUTO:
						{
							state = SL_END;
							break;
						}
						case SL_TSTAMP:
						{
							if(slcan_buffer[i] == 0x30)
							{
								timestamp_flag = 0;
//								set_time_stamp_flag(0);
							}
							else if(slcan_buffer[i] == 0x31)
							{
//								set_time_stamp_flag(1);
								timestamp_flag = 1;
							}
							state = SL_END;
							break;
						}
						case SL_ACP_CODE:
						{
							filter[msg_index++] = ascii_to_num(slcan_buffer[i]);

							if(msg_index == 8)
							{
								static uint32_t flt = 0;
								flt = (((filter[6]<<4)+filter[7]) & 0xFF) |
											((((filter[4]<<4)+filter[5]) << 8) & 0x0000FF00) |
											((((filter[2]<<4)+filter[3]) << 16) & 0x00FF0000) |
											((((filter[0]<<4)+filter[1]) << 24) & 0xFF000000);
								can_set_filter(flt);
								//ESP_LOGI(TAG, "flt: 0xPRIxx", flt);
								msg_index = 0;
								state = SL_END;
							}
							break;
						}
						case SL_MASK_REG:
						{
							mask[msg_index++] = ascii_to_num(slcan_buffer[i]);
							if(msg_index == 8)
							{
								static uint32_t msk = 0;
								msk = (((mask[6]<<4)+mask[7]) & 0xFF) |
											((((mask[4]<<4)+mask[5]) << 8) & 0x0000FF00) |
											((((mask[2]<<4)+mask[3]) << 16) & 0x00FF0000) |
											((((mask[0]<<4)+mask[1]) << 24) & 0xFF000000);
								can_set_mask(msk);
								//ESP_LOGI(TAG, "msk: 0xPRIxx", msk);
								msg_index = 0;
								state = SL_END;
							}
							break;
						}
						case SL_CUS_SPEED:
						{
							msg_index++;
							if(msg_index == 2)
							{
								msg_index = 0;
								state = SL_END;
							}
						}
					}
					break;
				}
				else
				{
					if(slcan_set_frame(slcan_buffer[i], frame, cmd) == 0)
					{
//						state = SL_END;
						break;
					}
					else
					{
						switch(cmd)
						{
							case SL_REMOTE_STD:
							{
								frame->rtr = 1;
								frame->extd = 0;
								state = SL_END;
								break;
							}
							case SL_DATA_STD:
							{
								frame->rtr = 0;
								frame->extd = 0;
								state = SL_END;
								break;
							}
							case SL_REMOTE_EXT:
							{
								frame->rtr = 1;
								frame->extd = 1;
								state = SL_END;
								break;
							}
							case SL_DATA_EXT:
							{
								frame->rtr = 0;
								frame->extd = 1;
								state = SL_END;
								break;
							}

//								break;
						}
//						state = SL_END;
//							break;
					}
					break;
				}
			}
			case SL_END:
			{
				if(slcan_buffer[i] == '\r')
				{
					if(cmd == SL_REMOTE_STD || cmd == SL_DATA_STD || cmd == SL_REMOTE_EXT || cmd == SL_DATA_EXT)
					{
						esp_err_t tx_err;

						if(loopback)
						{
							frame->self = 1;
						}
						else
						{
							frame->self = 0;
						}

						tx_err = can_send(frame, pdMS_TO_TICKS(50));
						if (tx_err != ESP_OK)
						{
							ESP_LOGE(TAG, "can_send failed: %s id=0x%lX",
									 esp_err_to_name(tx_err), (unsigned long)frame->identifier);
						}
						else
						{
							ESP_LOGI(TAG, "can_send ok id=0x%lX", (unsigned long)frame->identifier);
						}
					}
					cmd_ret = cmd;
					msg_index = 0;
//					//ESP_LOGI(TAG, "end of command");
					state = SL_HEADER;
					cmd = SL_NONE;
//					*processed_index = i;
//					//ESP_LOGI(TAG, "cmd_ret: %d", cmd_ret);
					switch(cmd_ret)
					{
						case SL_SERIAL:
						{
							slcan_response((char*)serial, 0, q, NULL);
							break;
//							return (char*)serial;
						}
						case SL_VER:
						{
							slcan_response((char*)version, 0, q, NULL);
							break;
//							return (char*)version;
						}
						case SL_STATUS:
						{
							slcan_response((char*)status, 0, q, NULL);
							break;
//							return (char*)status;
						}
						default:
						{
							slcan_response((char*)ack, 0, q, NULL);
							break;
//							return (char*)ack;
						}
					}
					break;
				}
				break;
			}
		}
	}
    // Keep only unconsumed bytes (incomplete trailing command).
    if (i < buffer_index)
    {
        memmove(slcan_buffer, &slcan_buffer[i], buffer_index - i);
        buffer_index -= i;
    }
    else if (state == SL_HEADER && cmd == SL_NONE)
    {
        buffer_index = 0;
    }
    else
    {
        /* Mid-command: bytes were absorbed into parser state; drop the raw buffer. */
        buffer_index = 0;
    }

	return 0;
}

#if HARDWARE_VER == WICAN_PRO
void slcan_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q, char* cmd_str))
#else
void slcan_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q))
#endif
{
	slcan_response = send_to_host;
}
