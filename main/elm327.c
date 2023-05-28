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

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include  "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include <string.h>
#include "driver/twai.h"
#include "slcan.h"
#include "can.h"
#include "std_pid.h"
#include "sleep_mode.h"
#define TAG 		__func__

#define SERVICE_01			0x01
#define SERVICE_02			0x02
#define SERVICE_03			0x03
#define SERVICE_04			0x04
#define SERVICE_05			0x05
#define SERVICE_09			0x09
#define SERVICE_UNKNOWN		0xFF

uint8_t service_09_rsp_len[] = {1, 1, 4, 1, 255, 1, 255, 1, 1, 1, 4, 1}; //255 unknow



static QueueHandle_t *can_rx_queue = NULL;

const char *ok_str = "OK";
const char *question_mark_str = "?";
const char *device_description = "ELM327 v1.3a";
const char *identify = "OBDLink MX";


void (*elm327_response)(char*, uint32_t, QueueHandle_t *q);



typedef struct __xelm327_config
{
	uint8_t priority_bits;
	uint32_t header;
	uint32_t rx_address;
	uint8_t protocol;
	uint32_t req_timeout;
	uint8_t linefeed:1;
	uint8_t echo:1;
	uint8_t space_print:1;
	uint8_t show_header:1;
	uint8_t header_is_set:1;
	uint8_t rx_address_is_set:1;
	uint8_t display_dlc:1;

}_xelm327_config_t;


static _xelm327_config_t elm327_config = {
	.req_timeout = 0x32,//in ms
	.protocol = '6',
	.linefeed = 1,
	.echo = 1,
	.priority_bits = 0x18,
	.header = 0,
	.rx_address = 0,
	.space_print = 1,
	.show_header = 0,
	.header_is_set = 0,
	.rx_address_is_set = 0,
	.display_dlc = 0,

};
typedef char* (*elm327_command_callback)(const char* command_str);
typedef struct _xelm327_cmd
{
	const char * const command;
	const elm327_command_callback command_interpreter;
}xelm327_cmd_t;


static unsigned int elm327_parse_hex_char(char chr)
{
    unsigned int result = 0;
    if (chr >= '0' && chr <= '9') result = chr - '0';
    else if (chr >= 'A' && chr <= 'F') result = 10 + chr - 'A';
    else if (chr >= 'a' && chr <= 'f') result = 10 + chr - 'a';

    return result;
}

static unsigned int elm327_parse_hex_str(const char *str, int len)
{
    unsigned int result = 0;
    for (int i = 0; i < len; i++) result += elm327_parse_hex_char(str[i]) << (4 * (len - i - 1));
    return result;
}

static uint8_t elm327_fill_data_from_hex_str(const char *str, uint8_t *data, int data_length)
{
	for (int i = 0; i < data_length; i++) {
		uint8_t byte = (elm327_parse_hex_char(str[i*2]) << 4) + (elm327_parse_hex_char(str[i*2 + 1]));
		data[i] = byte;
	}
	return 0;
}

static char* elm327_return_ok(const char* command_str)
{
	return (char*)ok_str;
}

static char* elm327_set_linefeed(const char* command_str)
{
	if(command_str[1] == '1')
	{
		elm327_config.linefeed = 1;
	}
	else if(command_str[1] == '0')
	{
		elm327_config.linefeed = 0;
	}
	else
	{
		return 0;
	}
	return (char*)ok_str;
}

static char* elm327_set_echo(const char* command_str)
{
	if(command_str[1] == '1')
	{
		elm327_config.echo = 1;
	}
	else if(command_str[1] == '0')
	{
		elm327_config.echo = 0;
	}
	else
	{
		return 0;
	}
	return (char*)ok_str;
}

static char* elm327_header_on_off(const char* command_str)
{
	if(command_str[1] == '1')
	{
		elm327_config.show_header = 1;
	}
	else if(command_str[1] == '0')
	{
		elm327_config.show_header = 0;
	}
	else
	{
		return 0;
	}
	return (char*)ok_str;
}


static char* elm327_device_description(const char* command_str)
{
	return (char*)device_description;
}

static char* elm327_identify(const char* command_str)
{
	return (char*)identify;
}

static void elm327_set_default_config()
{
	// TODO: This should set the active protocol to be
	// the stored protocol. Currently there is no distinction
	// between an active protocol and a stored protocol so
	// for now we just don't change the protocol
	elm327_config.req_timeout = 0x32;//50
	elm327_config.linefeed = 1;
	elm327_config.echo = 1;
	elm327_config.priority_bits = 0x18;
	elm327_config.header = 0;
	elm327_config.rx_address = 0;
	elm327_config.space_print = 1;
	elm327_config.show_header = 0;
	elm327_config.header_is_set = 0;
	elm327_config.rx_address_is_set = 0;
	elm327_config.display_dlc = 0;
}

static char* elm327_restore_defaults_or_display_dlc(const char* command_str)
{
	size_t arg_size = strlen(command_str+1);
	if(arg_size == 0)
	{
		// ATD: restore defaults
		elm327_set_default_config();
	}
	else
	{
		// TODO: currently we don't use this display_dlc property
		if(command_str[1] == '1')
		{
			// ATD1: display DLC
			elm327_config.display_dlc = 1;
		}
		else if(command_str[1] == '0')
		{
			// ATD0: don't display DLC
			elm327_config.display_dlc = 0;
		}
		else
		{
			return 0;
		}
	}

	return (char*)ok_str;
}

static char* elm327_reset_all(const char* command_str)
{
	elm327_set_default_config();

	return (char*)device_description;
}

/*
 * ELM327 supports 3 sizes of parameters to the set header (SH)
 * command.
 * - 3 hex digits (xyz) means to set the header field to 00 0x yz
 * - 6 hex digits (xx yy zz)
 * - 8 hex digits (ww xx yy zz)
 *
 * There is also the CP command for setting the priority bits of
 * a 29bit header. Basically that is an alternative way to set the
 * the ww in the 8 hex digit param.
 *
 * Because the CP command could be sent after set header
 * And because the header could be used for either a 11bit or 29bit
 * message. And because the TWAI spec doesn't indicate if the extra
 * bytes of the 11bit identifier would be ignored or not.
 * It seems easier and safer to store the header and priority bytes
 * separately.
 */
static char* elm327_set_header(const char* command_str)
{
	size_t id_size = strlen(command_str+2);

	if(id_size == 3 || id_size == 6)
	{
		// SH xyz or SH xx yy zz
		elm327_config.header = elm327_parse_hex_str(command_str+2, id_size);
		elm327_config.header_is_set = 1;
	}
	else if(id_size == 8)
	{
		// SH ww xx yy zz
		// The documentation isn't clear if priority bits should be stored separately.
		// The key question is whether setting the header again with the SH xx yy zz
		// form would keep the priority bits set here or not.
		// Since it is ambiguous, storing them separately is the easiest to implement
		elm327_config.priority_bits = elm327_parse_hex_str(command_str+2, 2) & 0x1F;
		elm327_config.header = elm327_parse_hex_str(command_str+4, 6);
		elm327_config.header_is_set = 1;
	}
	else
	{
		return 0;
	}

	return (char*)ok_str;
}

static char* elm327_set_priority_bits(const char* command_str)
{
	size_t arg_size = strlen(command_str+2);

	if(arg_size != 2) return 0;

	// Only save 5 bits
	elm327_config.priority_bits = elm327_parse_hex_str(command_str+2, arg_size) & 0x1F;

	return (char*)ok_str;
}

/*
 * Could be
 * - ATCRA
 * - ATCRA xyz
 * - ATCRA wwxxyyzz
 *
 * TODO: this should handle "X" characters. That would be best handled with a
 * mask property in the config. This mask property would be the the same one
 * that is set by the (not yet implemented) CM command.
 */
static char* elm327_set_receive_address(const char* command_str)
{
	size_t arg_size = strlen(command_str+3);

	if(arg_size == 0)
	{
		elm327_config.rx_address_is_set = 0;
	}
	else if(arg_size == 3 || arg_size == 8)
	{
		elm327_config.rx_address_is_set = 1;
		elm327_config.rx_address = elm327_parse_hex_str(command_str+3, arg_size);
	}
	else
	{
		return 0;
	}

	return (char*)ok_str;
}

static char* elm327_set_protocol(const char* command_str)
{

	elm327_config.protocol = command_str[2];
	ESP_LOGI(TAG, "elm327_config.protocol: %c", elm327_config.protocol);

	// The header should not be reset when the protocol is changed
	// unless protocol autoscanning is being used. And even in that case
	// the header bytes should revert to their set value after the
	// autoscanning is complete. See the 2nd and 3rd paragraphs of the
	// "SH xx yy zz" section of the ELM docs.
	//
	// In some cases Carscanner sends the header first and then changes
	// the protocol.
	if(elm327_config.protocol == '6' || elm327_config.protocol == '7')
	{
		can_disable();
		vTaskDelay(pdMS_TO_TICKS(15));
		can_set_bitrate(CAN_500K);
		can_enable();
		vTaskDelay(pdMS_TO_TICKS(15));
	}
	else if(elm327_config.protocol == '8' || elm327_config.protocol == '9')
	{
		can_disable();
		vTaskDelay(pdMS_TO_TICKS(15));
		can_set_bitrate(CAN_250K);
		can_enable();
		vTaskDelay(pdMS_TO_TICKS(15));
	}

	return (char*)ok_str;
}


static char* elm327_set_timeout(const char* command_str)
{
	elm327_config.req_timeout = (strtol((char *) &command_str[2], NULL, 16) & 0xFF);

	if(elm327_config.req_timeout == 0)
	{
		elm327_config.req_timeout = 0x32;
	}

	ESP_LOGI(TAG, "elm327_config.req_timeout: %d", elm327_config.req_timeout);

	return (char*)ok_str;
}

static char hex_to_num(char a)
{
	char x = a;
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
static char* elm327_describe_protocol(const char* command_str)
{
	static char protocol[10][50] = {
								"AUTO",
								"SAE J1850 PWM",	//1
								"SAE J1850 VPW",	//2
								"ISO 9141-2",		//3
								"ISO 14230-4 KWP/5",	//4
								"ISO 14230-4 KWP",		//5
								"ISO 15765-4 CAN (11 bit ID, 500 kbaud)",	//6
								"ISO 15765-4 CAN (29 bit ID, 500 kbaud)",	//7
								"ISO 15765-4 CAN (11 bit ID, 250 kbaud)",	//8
								"ISO 15765-4 CAN (29 bit ID, 250 kbaud)"};	//9

	uint8_t protocool_number = (uint8_t)hex_to_num(elm327_config.protocol);
	if(protocool_number >= 10)
	{
		return 0;
	}
	return (char*)&protocol[protocool_number][0];
}

static char* elm327_describe_protocol_num(const char* command_str)
{
	static char protocol_number_str[3];

	sprintf( protocol_number_str, "%c", elm327_config.protocol);

	return protocol_number_str;
}

static char* elm327_input_voltage(const char* command_str)
{
	static char volt[10] = "";
	float voltage = 0;

	if(sleep_mode_get_voltage(&voltage) != -1)
	{
		sprintf(volt, "%.1fV",voltage);
	}
	else
	{
		sprintf(volt, "%.1f",0);
	}
	return (char*)volt;
}

static uint32_t elm327_get_identifier()
{
	if(!elm327_config.header_is_set) {
		switch(elm327_config.protocol) {
			case '6':
			case '8':
				// return 0x7E0
				return 0x7DF;
			case '7':
			case '9':
				// return 0x18DAF10A;
				return 0x18DB33F1;
			default:
				// In theory this line shouldn't be hit,
				// but just in case return something reasonable
				return 0x7DF;
		}
	} else {
		switch(elm327_config.protocol) {
			case '6':
			case '8':
				// The TWAI api isn't clear if it handles masking the header
				// So to be safe we mask it ourselves
				return elm327_config.header & TWAI_STD_ID_MASK;
			case '7':
			case '9':
				return (elm327_config.priority_bits << 24) | elm327_config.header;
			default:
				// In theory this line shouldn't be hit,
				// but just in case return something reasonable
				return elm327_config.header;
		}
	}
}

/*
 * TODO: this should support the CM and CF commands.
 *
 * It isn't clear if setting the priority bits with the CP should change the
 * default filter. Based on the commands sent by Carscanner it seems like the CP
 * priority bits do not change the default filter.
 */
static uint8_t elm327_should_receive(twai_message_t *rx_frame)
{
	uint32_t identifier = rx_frame->identifier;
	if(elm327_config.rx_address_is_set)
	{
		return identifier == elm327_config.rx_address;
	}
	else
	{
		if(rx_frame->extd)
		{
			return identifier >= 0x18DAF100 && identifier <= 0x18DAF1FF;
		}
		else
		{
			return identifier >= 0x7E8 && identifier <= 0x7EF;
		}
	}
}

static int8_t elm327_request(char *cmd, char *rsp, QueueHandle_t *queue)
{
	twai_message_t txframe;
	uint8_t cmd_data_length;

	ESP_LOGI(TAG, "PID req, cmd_buffer: %s", cmd);
	ESP_LOG_BUFFER_HEX(TAG, cmd, strlen(cmd));

	if((elm327_config.protocol != '6') && (elm327_config.protocol != '8') && (elm327_config.protocol != '7') && (elm327_config.protocol != '9'))
	{
		if(elm327_config.protocol == '1' || elm327_config.protocol == '2')
		{
			strcat(rsp, "NO DATA\r\r>");
			elm327_response((char*)rsp, 0, queue);
		}
		else
		{
			strcat(rsp, "BUS INIT: ...ERROR\r\r>");
			elm327_response((char*)rsp, 0, queue);
		}

		return 0;
	}

	twai_message_t rx_frame;

	txframe.identifier = elm327_get_identifier();
	txframe.extd = elm327_config.protocol == '7' || elm327_config.protocol == '9';

	txframe.rtr = 0;
	// Pad the data
	txframe.data[0] = 0xAA;
	txframe.data[1] = 0xAA;
	txframe.data[2] = 0xAA;
	txframe.data[3] = 0xAA;
	txframe.data[4] = 0xAA;
	txframe.data[5] = 0xAA;
	txframe.data[6] = 0xAA;
	txframe.data[7] = 0xAA;

	uint8_t req_expected_rsp = 0xFF;

	// If the command length is odd then the last digit is the number of frames
	// to expect in response. This is an optimization supported by the ELM327
	// protocol. It is so the OBD2 device doesn't have to wait to see if there
	// are more frames. Once it gets the expected number it can stop waiting and
	// return the result.
	if(strlen(cmd) % 2 == 1)
	{
		// FIXME: this should use hex conversion since the expected response
		// frames could be more than 9.
		req_expected_rsp = cmd[strlen(cmd)-1] - 0x30;
		cmd[strlen(cmd)-1] = 0;
		if(req_expected_rsp == 0 || req_expected_rsp > 9)
		{
			req_expected_rsp = 0xFF;
		}
		ESP_LOGW(TAG, "req_expected_rsp 1: %u", req_expected_rsp);
	}

	cmd_data_length = strlen(cmd)/2;
	if(cmd_data_length > 7)
	{
		// commands can't be longer than 7 bytes unless flow control is used
		// FIXME: this should use the linefeed setting and match the number of
		// `\r`s that are normally sent.
		strcat(rsp, "?\r>");
		elm327_response((char*)rsp, 0, queue);
		return 0;
	}

	txframe.data[0] = cmd_data_length;
	elm327_fill_data_from_hex_str(cmd, &txframe.data[1], cmd_data_length);

	// CAN frames always have a data length code of 8, this is different than the
	// PCI byte (txframe.data[0])
	txframe.data_length_code = 8;
	txframe.self = 0;

	// if(txframe.extd == 0)
	// {
	// 	ESP_LOGI(TAG, "sending %03X", txframe.identifier&0xFFF);
	// }
	// else
	// {
	// 	ESP_LOGI(TAG, "sending %08X", txframe.identifier&TWAI_EXTD_ID_MASK);
	// }
	// ESP_LOG_BUFFER_HEX(TAG, txframe.data, 8);

	can_send(&txframe, 1);
	TickType_t xtimeout = (elm327_config.req_timeout*4.096) / portTICK_PERIOD_MS;
	TickType_t xwait_time;
	int64_t txtime = esp_timer_get_time();
	uint8_t timeout_flag = 0;
	uint8_t rsp_found = 0;
	uint8_t number_of_rsp = 0;
	char tmp[10];
	xwait_time = xtimeout;
	memset(tmp, 0, sizeof(tmp));
	ESP_LOGW(TAG, "req_expected_rsp: %u", req_expected_rsp);
	while(timeout_flag == 0)
	{
		if( xQueueReceive(*can_rx_queue, ( void * ) &rx_frame, xwait_time) == pdPASS )
		{
			xwait_time = xtimeout;
			// if(rx_frame.extd == 0)
			// {
			// 	ESP_LOGI(TAG, "received %03X %02X", rx_frame.identifier&0xFFF,rx_frame.data[0]);
			// }
			// else
			// {
			// 	ESP_LOGI(TAG, "received %08X %02X", rx_frame.identifier&TWAI_EXTD_ID_MASK,rx_frame.data[0]);
			// }

			if(elm327_should_receive(&rx_frame))
			{
				//reset timeout after response is received
				rsp_found = 1;
				number_of_rsp++;
				// Based on the "CAF0 AND CAF1" section of the ELM doc, if headers are shown
				// the PCI byte(s) (usually just data[0]) should be printed.
				if(elm327_config.show_header)
				{
					if(rx_frame.extd == 0)
					{
						sprintf((char*)rsp, "%03X%02X", rx_frame.identifier&0xFFF,rx_frame.data[0]);
					}
					else
					{
						sprintf((char*)rsp, "%08X%02X", rx_frame.identifier&TWAI_EXTD_ID_MASK,rx_frame.data[0]);
					}

				}

//				ESP_LOGI(TAG, "ELM327 send 1: %s", rsp);

				int rx_frame_data_length = rx_frame.data[0];

				// If this is a first frame, consecutive frame, or flow control frame the PCI (rx_frame.data[0]) will
				// not be a valid length without some processing, so just print all 7 bytes
				if(rx_frame_data_length > 7) rx_frame_data_length = 7;

				for (int i = 0; i < rx_frame_data_length; i++)
				{
					sprintf((char*)tmp, "%02X", rx_frame.data[1+i]);
					strcat((char*)rsp, (char*)tmp);
				}

				strcat((char*)rsp, "\r");
				ESP_LOGW(TAG, "ELM327 send: %s", rsp);
//				ESP_LOG_BUFFER_HEX(TAG, rsp, strlen(rsp));
				elm327_response(rsp, 0, queue);
				memset(rsp, 0, strlen(rsp));
//				strcat((char*)rsp, "\r");
				if(req_expected_rsp != 0xFF)
				{
					if(req_expected_rsp == number_of_rsp)
					{
						timeout_flag = 1;
						break;
					}
				}
			}
			else
			{
				xwait_time -= (((esp_timer_get_time() - txtime)/1000)/portTICK_PERIOD_MS);
				ESP_LOGI(TAG, "xwait_time: %d", xwait_time);
				if(xwait_time > (elm327_config.req_timeout*4.096))
				{
					xwait_time = 0;
					timeout_flag = 1;
				}
			}
		}
		else
		{
			timeout_flag = 1;
		}
	}

	ESP_LOGW(TAG, "Response time: %u", (uint32_t)((esp_timer_get_time() - txtime)/1000));

	if(rsp_found == 0)
	{
		strcat((char*)rsp, "NO DATA\r\r>");
	}
	else
	{
		strcat((char*)rsp, "\r>");
	}
	elm327_response(rsp, 0, queue);

	return 0;
}


const xelm327_cmd_t elm327_commands[] = {
											{"dpn", elm327_describe_protocol_num},//describe protocol by number
											{"cra", elm327_set_receive_address},
											{"cp", elm327_set_priority_bits},// set five most significant bits of 29bit header
											{"dp", elm327_describe_protocol},//describe current protocol
											{"sh", elm327_set_header},// set header to xyz, xx yy zz, or ww xx yy zz
											{"at", elm327_return_ok},//adaptive timing control
											{"sp", elm327_set_protocol},//set protocol to h and save as new default, 6, 7, 8, 9
																	 // or ah	set protocol to auto, h
											{"rv", elm327_input_voltage},//read input voltage
											{"pc", elm327_return_ok},//close protocol
											{"st", elm327_set_timeout},//set timeout
											{"d", elm327_restore_defaults_or_display_dlc},//set all to defaults or change display DLC
											{"z", elm327_reset_all},// reset all/software reset
											{"s", elm327_return_ok},// printing of spaces off or on
											{"e", elm327_set_echo},// echo off or on
											{"h", elm327_header_on_off},//headers off or on
											{"l", elm327_set_linefeed},//linefeeds off or on
											{"@", elm327_device_description},//display device description
											{"i", elm327_identify},//identify yourself
											{"m", elm327_return_ok},//memory off or on

											{NULL, NULL},
									};


int8_t elm327_process_cmd(uint8_t *buf, uint8_t len, twai_message_t *frame, QueueHandle_t *q)
{
	// Because the cmd_buffer and cmd_len are static they keep their value
	// across multiple calls. So if a buf is an incomplete command the next
	// call will keep add to the cmd_buffer until the ending CR is found.
	static char cmd_buffer[128];
	static uint8_t cmd_len = 0;
	static char cmd_response[128];
	uint8_t cmd_found_flag = 0;

	for(int i = 0; i < len; i++)
	{
		if(buf[i] == '\r' || cmd_len > 126)
		{
	//		ESP_LOGI(TAG, "end of command i: %d, cmd_len: %u", i, cmd_len);
			cmd_buffer[cmd_len] = 0;
			cmd_response[0] = 0;
			cmd_found_flag = 0;
			memset(cmd_response, 0, sizeof(cmd_response));

			if(!strncmp(cmd_buffer, "at", 2))
			{
				for(int j = 0; elm327_commands[j].command != NULL; j++)
				{
					if(!strncmp(&cmd_buffer[2], elm327_commands[j].command, strlen(elm327_commands[j].command)))
					{
						cmd_response[0] = 0;
						char *ret_ptr = elm327_commands[j].command_interpreter(&cmd_buffer[2]);

						if(ret_ptr != 0)
						{
							strcat(cmd_response, ret_ptr);
							cmd_found_flag = 1;
							ESP_LOGI(TAG, "cmd: %s, rsp: %s", elm327_commands[j].command, cmd_response);
							break;
						}

					}
				}

				if(!cmd_found_flag)
				{
					strcat(cmd_response, (char*)question_mark_str);
				}

				if(elm327_config.linefeed)
				{
					strcat(cmd_response, "\r\n");
				}
				else
				{
					strcat(cmd_response, "\r");
				}

				elm327_response((char*)cmd_response, 0, q);
				memset(cmd_response, 0, sizeof(cmd_response));
				strcat(cmd_response, "\r>");

				elm327_response((char*)cmd_response, 0, q);

				if(cmd_buffer[2] == 'z')
				{
					// When the command is a reset ignore any other commands in the buffer.
					// This approach fixes an issue seen in the Carscanner Android app.
					// It might actually match a real ELM327 chip since the documentation
					// for the chip say an ATZ is like a full reset of the chip. So it
					// would make sense that fully reset would imply anything in the
					// incoming serial buffer would be cleared.
					//
					// In the Carscanner app a reset (ATZ) and a echo off (ATE0) are sent
					// in a single BLE message. Without the code below elm327_process_cmd
					// would respond to the ATZ and the ATE0. When it does this
					// Carscanner gets out of sync. The Carscanner log shows all following
					// commands with a response from the previous command.
					cmd_len = 0;
					memset(cmd_response, 0, sizeof(cmd_response));
					return 0;
				}
			}
			else if(!strncmp(cmd_buffer, "vti", 3) || !strncmp(cmd_buffer, "sti", 3))
			{
				strcat(cmd_response, (char*)question_mark_str);
				if(elm327_config.linefeed)
				{
					strcat(cmd_response, "\r\n");
				}
				else
				{
					strcat(cmd_response, "\r");
				}
				elm327_response((char*)cmd_response, 0, q);
				memset(cmd_response, 0, sizeof(cmd_response));
				strcat(cmd_response, "\r>");

				elm327_response((char*)cmd_response, 0, q);
			}
			else	//this is a request
			{
				memset(cmd_response, 0, sizeof(cmd_response));
				if(strlen(cmd_buffer) > 0)
				{
					elm327_request(cmd_buffer, cmd_response,q);
				}
//				ESP_LOGI(TAG, "PID req, cmd_buffer: %s", cmd_buffer);
//				if((!strncmp(cmd_buffer, "0100",4) || !strncmp(cmd_buffer, "0900",4)) && (elm327_config.protocol != '6'))
//				{
//					if(elm327_config.protocol == '1' || elm327_config.protocol == '2')
//					{
//						strcat(cmd_response, "NO DATA\r\r>");
//						elm327_response((char*)cmd_response, 0, q);
//					}
//					else
//					{
//						strcat(cmd_response, "BUS INIT: ...ERROR\r\r>");
//						elm327_response((char*)cmd_response, 0, q);
//					}
//
//				}
//				else
//				{
//					twai_message_t rx_frame;
//
//					frame->identifier = elm327_config.header;
//					frame->extd = false;
//					frame->rtr = 0;
//					frame->data[0] = 0xAA;
//					frame->data[1] = 0xAA;
//					frame->data[2] = 0xAA;
//					frame->data[3] = 0xAA;
//					frame->data[4] = 0xAA;
//					frame->data[5] = 0xAA;
//					frame->data[6] = 0xAA;
//					frame->data[7] = 0xAA;
//
//					uint16_t req_pid = 0;
//					uint8_t req_mode = 0;
//					uint8_t req_expected_rsp = 0xFF;
//
//					if(strlen(cmd_buffer) == 4 || strlen(cmd_buffer) == 5)
//					{
//						if(strlen(cmd_buffer) == 5)
//						{
//							req_expected_rsp = cmd_buffer[4] - 0x30;
//							cmd_buffer[4] = 0;
//							if(req_expected_rsp == 0 && req_expected_rsp > 9)
//							{
//								req_expected_rsp = 0xFF;
//							}
//						}
//
//						uint32_t value = strtol((char *) cmd_buffer, NULL, 16); //the pid format is always in hex
//						uint8_t pidnum = (uint8_t)(value & 0xFF);
//						uint8_t mode = (uint8_t)((value >> 8) & 0xFF);
//
//			            frame->data[0] = 2;
//			            frame->data[1] = mode;
//			            frame->data[2] = pidnum;
//						frame->data_length_code = 8;
//						req_pid = pidnum;
//						req_mode = mode;
//					}
//					else if(strlen(cmd_buffer) == 6)
//					{
//
//	//		        	req_pid = pidnum;
//					}
//
//					can_send(frame, 1);
//
//					TickType_t xwait_time = (elm327_config.req_timeout*4.096) / portTICK_PERIOD_MS;
//					int64_t txtime = esp_timer_get_time();
//					uint8_t timeout_flag = 0;
//					uint8_t rsp_found = 0;
//					uint8_t number_of_rsp = 0;
//					char tmp[10];
//
//
//
//					while(timeout_flag == 0)
//					{
//						if( xQueueReceive(*can_rx_queue, ( void * ) &rx_frame, xwait_time) == pdPASS )
//						{
//							if(rx_frame.identifier >= 0x7E8 && rx_frame.identifier <= 0x7EF)
//							{
//								if(rx_frame.data[2] == req_pid)
//								{
//									rsp_found = 1;
//									number_of_rsp++;
//									if(elm327_config.show_header)
//									{
//										sprintf((char*)cmd_response, "%03X%02X", rx_frame.identifier&0xFFF,rx_frame.data[0]);
//									}
//
////									ESP_LOGI(TAG, "ELM327 send 1: %s", cmd_response);
//									for (int i = 0; i < rx_frame.data[0]; i++)
//									{
//										sprintf((char*)tmp, "%02X", rx_frame.data[1+i]);
//										strcat((char*)cmd_response, (char*)tmp);
//									}
//
//									strcat((char*)cmd_response, "\r");
//									ESP_LOGI(TAG, "ELM327 send: %s", cmd_response);
//
//									elm327_response(cmd_response, 0, q);
//									memset(cmd_response, 0, sizeof(cmd_response));
//
//									if(req_expected_rsp != 0xFF)
//									{
//										if(req_expected_rsp == number_of_rsp)
//										{
//											timeout_flag = 1;
//											break;
//										}
//									}
//
//								}
//								else// check if 16bit
//								{
//
//								}
//							}
//
//							xwait_time -= (((esp_timer_get_time() - txtime)/1000)/portTICK_PERIOD_MS);
//							ESP_LOGI(TAG, "xwait_time: %d", xwait_time);
//							if(xwait_time > (elm327_config.req_timeout*4.096))
//							{
//								xwait_time = 0;
//								timeout_flag = 1;
//							}
//
//						}
//						else
//						{
//							timeout_flag = 1;
//						}
//					}
//					if(rsp_found == 0)
//					{
//						strcat((char*)cmd_response, "NO DATA\r\r>");
//					}
//					else
//					{
//						strcat((char*)cmd_response, "\r>");
//					}
//					elm327_response(cmd_response, 0, q);
//
//					ESP_LOGW(TAG, "Response time: %u", (uint32_t)((esp_timer_get_time() - txtime)/1000));
//				}
			}

			cmd_len = 0;
			memset(cmd_response, 0, sizeof(cmd_response));
		}
		else
		{
			//clear queu before sending command
			if(buf[i] != ' ' && buf[i] != '\n')
			{
				cmd_buffer[cmd_len++] = (char)tolower(buf[i]);
			}
		}
	}
	return 0;
}

void elm327_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q), QueueHandle_t *rx_queue)
{
	elm327_response = send_to_host;
	can_rx_queue = rx_queue;
}
