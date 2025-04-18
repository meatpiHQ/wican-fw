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
#include <ctype.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include  "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_timer.h"
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
#include "elm327.h"
#include "driver/uart.h"
#include "obd.h"
#include "sleep_mode.h"
#include "types.h"

#define TAG 		__func__

#define SERVICE_01			0x01
#define SERVICE_02			0x02
#define SERVICE_03			0x03
#define SERVICE_04			0x04
#define SERVICE_05			0x05
#define SERVICE_09			0x09
#define SERVICE_UNKNOWN		0xFF

static QueueHandle_t *xqueue_elm327_uart_rx = NULL;

void (*elm327_can_log)(twai_message_t* frame, uint8_t type);

#if HARDWARE_VER != WICAN_PRO
void (*elm327_response)(char*, uint32_t, QueueHandle_t *q);
uint8_t service_09_rsp_len[] = {1, 1, 4, 1, 255, 1, 255, 1, 1, 1, 4, 1}; //255 unknow
const char *ok_str = "OK";
const char *question_mark_str = "?";
const char *device_description = "ELM327 v1.3a";
const char *identify = "OBDLink MX";



// The fields are ordered this way so the data can be tightly packed.
// See elm327_set_default_config for a more readable ordering.
typedef struct __xelm327_config
{
	uint32_t header;
	uint32_t rx_address;
	uint32_t req_timeout;
	uint32_t fc_header;
	uint8_t fc_data[5];
	uint8_t protocol;
	uint8_t priority_bits;
	uint8_t fc_data_length:3;
	uint8_t fc_mode:2;
	uint8_t linefeed:1;
	uint8_t echo:1;
	uint8_t space_print:1;
	uint8_t show_header:1;
	uint8_t header_is_set:1;
	uint8_t fc_header_is_set:1;
	uint8_t rx_address_is_set:1;
	uint8_t display_dlc:1;

}_xelm327_config_t;


static _xelm327_config_t elm327_config;

static void elm327_set_default_config(bool reset_protocol)
{
	// Header or ID settings
	elm327_config.priority_bits = 0x18;
	elm327_config.header_is_set = 0;
	elm327_config.header = 0;

	// Response address filter settings
	elm327_config.rx_address_is_set = 0;
	elm327_config.rx_address = 0;

	// See reset_all for why this is optional
	if (reset_protocol)
	{
		elm327_config.protocol = '6';
	}

	elm327_config.req_timeout = 0x32; //50 ms

	// Flow Control Settings
	elm327_config.fc_mode = 0;
	elm327_config.fc_header_is_set = 0;
	elm327_config.fc_header = 0;
	elm327_config.fc_data_length = 0;
	memset(elm327_config.fc_data, 0, sizeof(elm327_config.fc_data));

	// Display settings
	elm327_config.show_header = 0;
	elm327_config.linefeed = 1;
	elm327_config.echo = 1;
	elm327_config.space_print = 1;
	elm327_config.display_dlc = 0;
}

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

static char* elm327_space_on_off(const char* command_str)
{
	if(command_str[1] == '1')
	{
		elm327_config.space_print = 1;
	}
	else if(command_str[1] == '0')
	{
		elm327_config.space_print = 0;
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

static char* elm327_restore_defaults_or_display_dlc(const char* command_str)
{
	size_t arg_size = strlen(command_str+1);
	if(arg_size == 0)
	{
		// ATD: restore defaults
		elm327_set_default_config(false);
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
	// TODO: this should set the active protocol to be
	// the stored protocol. The SP command should store the
	// protocol, and TP could be used to try a new protocol without
	// storing it. Also if a SP 0 is sent then protocol scanning will
	// happen and and only if a valid protocol is found will it be stored.
	// Currently we don't have a explict stored protocol, so for now
	// when ATZ or ATD is called we don't change the protocol.
	// This approach is required because at least some clients do
	// not set the protocol again after calling ATZ.
	elm327_set_default_config(false);

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

static char* elm327_set_fc_mode(const char* command_str)
{
	size_t arg_size = strlen(command_str+4);

	if (arg_size != 1)
	{
		return 0;
	}

	int mode = elm327_parse_hex_str(command_str+4, 1);
	if (mode < 0 || mode > 2)
	{
		return 0;
	}

	if ((mode == 1 || mode == 2) && elm327_config.fc_data_length == 0)
	{
		// The flow control data needs to be set before using modes 1 or 2
		return 0;
	}

	if (mode == 1 && elm327_config.fc_header_is_set == 0)
	{
		// The flow control header has to be set before using mode 1
		return 0;
	}

	elm327_config.fc_mode = mode;
	return (char*)ok_str;
}

static char* elm327_set_fc_header(const char* command_str)
{
	size_t id_size = strlen(command_str+4);

	if(!(id_size == 3 || id_size == 8))
	{
		// Only "FC SH xyz" or "FC SH ww xx yy zz" is allowed
		return 0;
	}

	elm327_config.fc_header = elm327_parse_hex_str(command_str+4, id_size);
	elm327_config.fc_header_is_set = 1;
	return (char*)ok_str;
}

static char* elm327_set_fc_data(const char* command_str)
{
	size_t data_size = strlen(command_str+4)/2;

	if(data_size < 1 || data_size > 5)
	{
		return 0;

	}

	elm327_config.fc_data_length = data_size;
	elm327_fill_data_from_hex_str(command_str+4, elm327_config.fc_data, data_size);
	return (char*)ok_str;
}

static char* elm327_set_protocol(const char* command_str)
{
	//Handle SPAx, and set it as x. 
	//TODO: add support for auto sp
	if(command_str[2] == 'a' || command_str[2] == 'A')
	{
		if(command_str[3] == '6' || command_str[3] == '7' || 
			command_str[3] == '8' || command_str[3] == '9')
		{
			elm327_config.protocol = command_str[3];
		}
		else
		{
			elm327_config.protocol = '4';
		}
	}
	else
	{
		elm327_config.protocol = command_str[2];
	}
	
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

	ESP_LOGI(TAG, "elm327_config.req_timeout: %lu", elm327_config.req_timeout);

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
		sprintf(volt, "%.1f",0.0f);
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

static void elm327_send_flow_control_frame(twai_message_t *first_frame)
{
	twai_message_t txframe;
	uint8_t source_ecu;

	// Use the same size id as the first frame
	txframe.extd = first_frame->extd;

	if (first_frame->extd)
	{
		if (elm327_config.fc_mode == 0 || elm327_config.fc_mode == 2)
		{
			// Automatically compute the identifier from the first frame
			//
			// We find the source ECU and then construct an identifier with
			// - the default priority bits (18)
			// - a physical type (DA) as opposed to a functional type
			// - the source_ecu as the destination
			// - ourselves (F1) as the source
			source_ecu = 0xFF & first_frame->identifier;
			txframe.identifier = 0x18DA00F1 | (source_ecu << 8);
		}
		else
		{
			// fc_mode 1: use the configured header
			txframe.identifier = elm327_config.fc_header;
		}
	}
	else
	{
		if (elm327_config.fc_mode == 0 || elm327_config.fc_mode == 2)
		{
			// Automatically compute the identifier from the first frame
			//
			// We set the 4th bit to 0. Apparently when the first two nibbles are
			// 7E, this 4th bit indicates wether a message is being sent to or
			// received from an ECU identified by the last 3 bits.
			// For example 0x7E8 becomes 0x7E0 and 0x7EF becomes 0x7E7
			txframe.identifier = first_frame->identifier & 0xFF7;
		}
		else
		{
			// fc_mode 1: use the configured header
			txframe.identifier = elm327_config.fc_header & TWAI_STD_ID_MASK;;
		}
	}

	txframe.rtr = 0;

	// Initialize the data
	memset(txframe.data, 0xAA, 8);

	if (elm327_config.fc_mode == 0)
	{
		// data[0] & 0xF0 == 0x30 identifies it as a flow control frame,
		// data[0] & 0X0F is the flow status:
		// - 0 tells the ECU we are ready to receive more frames
		// - 1 tells the ECU to wait
		// - 2 tells the ECU we are overloaded and it should abort
		txframe.data[0] = 0x30;
		// Block Size:
		// - 0 means send all of the frames
		// - greater than 0 means send this number of frames then wait for a flow control ACK
		txframe.data[1] = 0x00;
		// Separation time in ms. There are also special values for large separation times.
		// The spec indicates this is supposed to be the time from when the last bit of the
		// last frame was sent to when the first bit of the next frame. However the spec
		// notes that some ECUs will implement this as the time from first bit to first bit
		//
		// Note: this value of 10ms is just a guess. Some docs have 20ms in their examples.
		// When carscanner sets the flow control data it uses a value 0ms.
		txframe.data[2] = 10;
	}
	else
	{
		// mode 1 or 2: use the data set by the client
		memcpy(txframe.data, elm327_config.fc_data, elm327_config.fc_data_length);
	}

	// CAN frames always have a data length code of 8
	txframe.data_length_code = 8;
	txframe.self = 0;

	if( elm327_can_log != NULL)
	{
		elm327_can_log(&txframe, ELM327_CAN_TX);
	}
	
	can_send(&txframe, 1);
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
	if( elm327_can_log != NULL)
	{
		elm327_can_log(&txframe, ELM327_CAN_TX);
	}
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
				if( elm327_can_log != NULL)
				{
					elm327_can_log(&rx_frame, ELM327_CAN_RX);
				}
				//reset timeout after response is received
				rsp_found = 1;
				number_of_rsp++;

				// Identify what kind of frame this is.
				int rx_frame_data_length = 0;
				uint8_t frame_type = rx_frame.data[0] & 0xF0;
				if (frame_type == 0x10)
				{
					// This is a first frame
					// Send a flow control response so we can get the remaining frames
					elm327_send_flow_control_frame(&rx_frame);
					// Length of the full data is:
					//   ((0x0F & data[0]) << 8 | data[1])
					//
					// For now, we say the data length is 7 so the second part
					// of the data length (data[1]) is sent plus the 6 bytes of
					// actual data.
					//
					// TODO: if elm327_config.show_header is disabled then we
					// should send the length of the full data on its own line
					// and then send `0: [6 bytes of data]` on the next line for
					// the first frame
					rx_frame_data_length = 7;
				}
				else if (frame_type == 0x20)
				{
					// This is a consecutive frame
					// Sequence index of the frame is 0x0F & data[0]
					// From the examples in the ELM327 docs the final consecutive frame includes
					// any padding bytes. In theory we could be smarter and figure out how many
					// of the total bytes we've received and then not print the padding bytes.
					// However this is complex since the frames might come in out of order and
					// there might be more than 15 of them.
					// TODO: if elm327_config.show_header is disabled then we should add a prefix to the
					// printed line: `[sequence index]: [7 bytes of data]`.
					rx_frame_data_length = 7;
				}
				else if (frame_type == 0x30)
				{
					// This is a flow control frame from an ECU
					//
					// TODO: if we start supporting sending more than 7 bytes of
					// data. We'll have to send first frames ourselves, and
					// we'll need to handle receiving flow control frames from
					// ECUs. In the meantime if we get one just send all the
					// bytes to the client
					rx_frame_data_length = 7;
				}
				else
				{
					// This is a single frame
					rx_frame_data_length = rx_frame.data[0];
				}

				// Based on the "CAF0 AND CAF1" section of the ELM doc, if headers are shown
				// the PCI byte(s) (usually just data[0]) should be printed.
				if(elm327_config.show_header)
				{
					if(rx_frame.extd == 0)
					{
						sprintf((char*)rsp, "%03lX", rx_frame.identifier&0xFFF);
					}
					else
					{
						sprintf((char*)rsp, "%08lX", rx_frame.identifier&TWAI_EXTD_ID_MASK);
					}
					if(elm327_config.space_print)
					{
						strcat((char*)rsp, (char*)" ");
					}
					sprintf((char*)tmp, "%02X", rx_frame.data[0]);
					strcat((char*)rsp, (char*)tmp);
				}

//				ESP_LOGI(TAG, "ELM327 send 1: %s", rsp);

				// If this is a first frame, consecutive frame, or flow control frame the PCI (rx_frame.data[0]) will
				// not be a valid length without some processing, so just print all 7 bytes
				if(rx_frame_data_length > 7) rx_frame_data_length = 7;

				for (int i = 0; i < rx_frame_data_length; i++)
				{
					if(elm327_config.space_print)
					{
						sprintf((char*)tmp, " %02X", rx_frame.data[1+i]);
					}
					else
					{
						sprintf((char*)tmp, "%02X", rx_frame.data[1+i]);
					}
					
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
				ESP_LOGI(TAG, "xwait_time: %lu" , xwait_time);
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

	ESP_LOGW(TAG, "Response time: %" PRIu32, (uint32_t)((esp_timer_get_time() - txtime)/1000));

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
											{"fcsd", elm327_set_fc_data},// set the flow control data
											{"fcsh", elm327_set_fc_header},// set the flow control header
											{"fcsm", elm327_set_fc_mode}, // determine if the fc_data and/or fc_header is uses
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
											{"s", elm327_space_on_off},// printing of spaces off or on
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
					// would make sense that a full reset would imply anything in the
					// incoming serial buffer would be cleared.
					//
					// In the Carscanner app a reset (ATZ) and an echo off (ATE0) are sent
					// in a single BLE message. Without the code below, elm327_process_cmd
					// would respond to the ATZ and the ATE0. When it does this,
					// Carscanner gets out of sync: the Carscanner log shows the next
					// command with a response from the previous command.
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
			//clear queue before sending command
			if(buf[i] != ' ' && buf[i] != '\n')
			{
				cmd_buffer[cmd_len++] = (char)tolower(buf[i]);
			}
		}
	}
	return 0;
}

void elm327_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q), QueueHandle_t *rx_queue, void (*can_log)(twai_message_t* frame, uint8_t type))
{
	elm327_set_default_config(true);
	elm327_response = send_to_host;
	can_rx_queue = rx_queue;
	elm327_can_log = can_log;
}
#else
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "led.h"
#include "hw_config.h"

/* Defines and Constants */
#define BUF_SIZE (1024)
#define ELM327_CMD_QUEUE_SIZE 100
#define ELM327_MAX_CMD_LEN (UART_BUF_SIZE)
#define ELM327_CMD_TIMEOUT_MS   10000
#define ELM327_CMD_TIMEOUT_US   (ELM327_CMD_TIMEOUT_MS*1000)  // 10 seconds in microseconds
#define UART_TIMEOUT_MS 1200
#define DESIRED_BAUD_RATE 2000000
#define DEFAULT_BAUD_RATE 115200
// #define DESIRED_BAUD_RATE 115200
// #define DEFAULT_BAUD_RATE 2000000
#define BUFFER_SIZE 128

#define ELM327_UPDATE_BUF_SIZE 512
#define ELM327_UPDATE_MAX_LINE_LENGTH 256
#define ELM327_UPDATE_TIMEOUT_MS 5000

typedef struct {
    bool in_normal_state;
    uint16_t device_type;
	bool need_update;
} device_status_t;

typedef struct {
    uint8_t* file_data;
    int32_t file_size;
    uint16_t device_type;
    uint32_t file_lines;
    double file_rawfactor;
    char* device_name;  // Changed to pointer
} firmware_info_t;

static device_status_t device_status = {0};
static firmware_info_t fw_info = {0};
response_callback_t elm327_response;

/* Type Definitions */
typedef struct 
{
    char* command;
    uint32_t command_len;
    QueueHandle_t *response_queue;
    response_callback_t response_callback;
	void *temp;
} elm327_commands_t;

/* Global Variables */
static QueueHandle_t elm327_cmd_queue;
QueueHandle_t uart1_queue = NULL;
SemaphoreHandle_t xuart1_semaphore = NULL;

extern const unsigned char obd_fw_start[] asm("_binary_V2_3_18_txt_start");
extern const unsigned char obd_fw_end[]   asm("_binary_V2_3_18_txt_end");

static int uart_read_until_pattern(uart_port_t uart_num, char* buffer, size_t buffer_size, 
                                 const char* end_pattern, int total_timeout_ms) ;

/* Function Implementations */
int8_t elm327_process_cmd(uint8_t *cmd, uint32_t len, QueueHandle_t *q, 
                         char *cmd_buffer, uint32_t *cmd_buffer_len, 
                         int64_t *last_cmd_time, response_callback_t response_callback)
{
    int64_t current_time = esp_timer_get_time();

    if(len == 0)
    {
        len = strlen((char*)cmd);
    }

    if (current_time - *last_cmd_time > ELM327_CMD_TIMEOUT_US)
    {
        ESP_LOGW(TAG, "Timeout occurred, resetting command buffer.");
        *cmd_buffer_len = 0;
    }

    *last_cmd_time = current_time;

    for (int i = 0; i < len; i++)
    {
        if (*cmd_buffer_len < ELM327_MAX_CMD_LEN - 1)
        {
            cmd_buffer[(*cmd_buffer_len)++] = cmd[i];
        }

        if (cmd[i] == '\r')
        {
            // cmd_buffer[*cmd_buffer_len] = '\0';

            elm327_commands_t *command_data;
			// command_data = (elm327_commands_t*) malloc(sizeof(elm327_commands_t));
			command_data = (elm327_commands_t*) heap_caps_aligned_alloc(16,sizeof(elm327_commands_t), MALLOC_CAP_SPIRAM);
            // command_data->command = (char*) malloc(*cmd_buffer_len + 1);
			command_data->command = (char*) heap_caps_aligned_alloc(16,*cmd_buffer_len + 1, MALLOC_CAP_SPIRAM);
            if (command_data->command == NULL)
            {
                ESP_LOGE(TAG, "Failed to allocate memory for command");
                *cmd_buffer_len = 0;
                return -1;
            }
			memset(command_data->command,0,*cmd_buffer_len + 1);
            memcpy(command_data->command, cmd_buffer, *cmd_buffer_len + 1);
            command_data->command_len = *cmd_buffer_len;
            command_data->response_queue = q;
            command_data->response_callback = response_callback;
			command_data->temp = command_data;
            if (xQueueSend(elm327_cmd_queue, (void*)command_data, portMAX_DELAY) != pdPASS)
            {
                ESP_LOGE(TAG, "Failed to send command to the queue");
                free(command_data->command);
				free(command_data);
                *cmd_buffer_len = 0;
                return -1;
            }

            *cmd_buffer_len = 0;
        }
    }

    return 0;
}

void elm327_send_cmd(uint8_t* cmd, uint32_t cmd_len)
{
	xSemaphoreTake(xuart1_semaphore, portMAX_DELAY);
	ESP_LOG_BUFFER_HEXDUMP(TAG, (char*)cmd, strlen((char*)cmd), ESP_LOG_INFO);
	uart_write_bytes(UART_NUM_1, cmd, cmd_len);
	xSemaphoreGive(xuart1_semaphore);
}

static void uart1_event_task(void *pvParameters)
{
    static uint8_t *uart_read_buf __attribute__((aligned(4))) DRAM_ATTR;
    static DRAM_ATTR elm327_commands_t elm327_command;
    size_t response_len = 0;
    uart_event_t event;

	uart_read_buf = (uint8_t *)heap_caps_aligned_alloc(4, ELM327_MAX_CMD_LEN, MALLOC_CAP_SPIRAM);

    while (1) 
    {
        if (xQueueReceive(elm327_cmd_queue, (void*)&elm327_command, portMAX_DELAY) == pdTRUE)
        {
			sleep_state_info_t sleep_state;
			sleep_mode_get_state(&sleep_state);
			memset(uart_read_buf, 0, sizeof(uart_read_buf));
            if ((elm327_chip_get_status() == ELM327_READY) && (sleep_state.state != STATE_SLEEPING) && xSemaphoreTake(xuart1_semaphore, portMAX_DELAY) == pdTRUE)
            {
                // uart_flush(UART_NUM_1);
                uart_event_t event;
                static const char atze_fake_rsp[] = "ATZ\r\r\rELM327 v2.3\r\r>";
				static const char atz_fake_rsp[] = "\r\rELM327 v2.3\r\r>";
				uint8_t atz_flag = 0;
				esp_err_t tx_wait_ret = ESP_FAIL;

                // while (xQueueReceive(uart1_queue, &event, 0) == pdTRUE) 
                // {
                //     ESP_LOGW(TAG, "Discarding UART event: %d", event.type);
				// 	uart_read_bytes(UART_NUM_1, uart_read_buf, sizeof(uart_read_buf), 0);
                // }
                
                // uart_read_bytes(UART_NUM_1, uart_read_buf, sizeof(uart_read_buf), 0);
				uart_flush_input(UART_NUM_1);
				xQueueReset(uart1_queue);

                if(strstr(elm327_command.command, "ATZ\r") != NULL || 
                   strstr(elm327_command.command, "atz\r") != NULL ||
                   strstr(elm327_command.command, "AT Z\r") != NULL ||
                   strstr(elm327_command.command, "at z\r") != NULL)
                {
					// if(strlen(elm327_command.command) < sizeof(last_command))
					// {
					// 	strcpy(last_command, elm327_command.command);
					// }
                    uart_write_bytes(UART_NUM_1, "ATWS\r", strlen("ATWS\r"));
					tx_wait_ret = uart_wait_tx_done(UART_NUM_1, pdMS_TO_TICKS(100));
					if(tx_wait_ret != ESP_OK )
					{
						ESP_LOGE(TAG, "uart_wait_tx_done returned error");
					}
					atz_flag = 1;
                    ESP_LOGI(TAG, "Replaced ATZ with ATWS command");
                }
				// else if(elm327_command.command[0] == '\r' && elm327_command.command[1] == 0)
				// {
				// 	uart_write_bytes(UART_NUM_1, last_command, strlen(last_command));
				// 	ESP_LOGI(TAG, "Repeat last command");
				// 	printf("Repeat last command: \r\n%s\r\n", last_command);
				// 	printf("-----\r\n");
				// 	ESP_LOGW(TAG, "-------------Sent");
				// 	ESP_LOG_BUFFER_HEXDUMP(TAG, last_command, strlen(last_command), ESP_LOG_INFO);
				// 	ESP_LOGW(TAG, "-------------Sent");
				// }
                else 
                {
					// if(strlen(elm327_command.command) < sizeof(last_command))
					// {
					// 	strcpy(last_command, elm327_command.command);
					// }
                    uart_write_bytes(UART_NUM_1, elm327_command.command, elm327_command.command_len);
					tx_wait_ret = uart_wait_tx_done(UART_NUM_1, pdMS_TO_TICKS(100));
					if(tx_wait_ret != ESP_OK )
					{
						ESP_LOGE(TAG, "uart_wait_tx_done returned error");
					}
					ESP_LOGW(TAG, "-------------Sent");
					ESP_LOG_BUFFER_HEXDUMP(TAG, elm327_command.command, elm327_command.command_len, ESP_LOG_INFO);
					ESP_LOGW(TAG, "-------------Sent");
                }

                bool terminator_received = false;
                response_len = 0;

                while (!terminator_received)
                {
                    if (xQueueReceive(uart1_queue, (void*)&event, pdMS_TO_TICKS(ELM327_CMD_TIMEOUT_MS)) == pdTRUE)
                    {
                        if (event.type == UART_DATA)
                        {
                            int read_bytes = uart_read_bytes(UART_NUM_1, 
                                                           uart_read_buf+response_len, 
                                                           event.size, 
                                                           pdMS_TO_TICKS(1));
                            if (read_bytes > 0)
                            {
								ESP_LOGW(TAG, "-------------Received");
                                ESP_LOG_BUFFER_HEXDUMP(TAG, uart_read_buf + response_len, 
                                                     read_bytes, ESP_LOG_INFO);
								ESP_LOGW(TAG, "-------------Received");
                                response_len += read_bytes;
								uart_read_buf[response_len] = 0;
								// elm327_command.response_callback((char*)uart_read_buf, 
								// 									read_bytes, 
								// 									elm327_command.response_queue, 
								// 									elm327_command.command);
								for (int i = 0; i < read_bytes; i++)
                                {
									if(uart_read_buf[i] == 0xA5)
									{
										printf("A5 detected\r\n");
									}
								}
                                for (int i = 0; i < response_len+1; i++)
                                {
                                    // if ((i > 1 && uart_read_buf[i-1] == '\r' && uart_read_buf[i] == '>') || 
                                    //     (i > 2 && uart_read_buf[i-2] == 'O' && uart_read_buf[i-1] == 'K' && 
                                    //      uart_read_buf[i] == '\r'))
									
									if ((i >= 2 && uart_read_buf[i-2] == '\r' && uart_read_buf[i-1] == '>' && uart_read_buf[i] == '\0'))
                                    {
                                        // if(strstr((char*)uart_read_buf, "STSBR2000000\rOK"))
                                        // {
                                        //     uart_write_bytes(UART_NUM_1, "\r\r", 2);
                                        //     uart_write_bytes(UART_NUM_1, "STWBR\r", strlen("STWBR\r"));
                                        // }
										ESP_LOGI(TAG, "Terminator Received");
                                        terminator_received = true;
                                        break;
                                    }
									// else if(uart_read_buf[response_len-1] == '\r')
									// {
									// 	elm327_command.response_callback((char*)uart_read_buf, 
									// 										response_len, 
									// 										elm327_command.response_queue, 
									// 										elm327_command.command);
									// 	response_len = 0;
									// }
                                }
                            }
                        }
                    }
                    else
                    {
                        ESP_LOGE(TAG, "UART read timeout");
                        break;
                    }
                }

                if (terminator_received && response_len > 0)
                {
                    uart_read_buf[response_len] = '\0';

                    if (elm327_command.response_callback != NULL)
                    {
						if(atz_flag == 1)
						{
							atz_flag = 0;
							if(strstr((char*)uart_read_buf, "WS") != NULL || strstr((char*)uart_read_buf, "ws") != NULL)
							{
								elm327_command.response_callback(atze_fake_rsp, strlen(atze_fake_rsp), 
															elm327_command.response_queue, 
															elm327_command.command);
							}
							else
							{
								elm327_command.response_callback(atz_fake_rsp, strlen(atz_fake_rsp), 
															elm327_command.response_queue, 
															elm327_command.command);
							}
						}
                        else
						{
							elm327_command.response_callback((char*)uart_read_buf, 
                                                       response_len, 
                                                       elm327_command.response_queue, 
                                                       elm327_command.command);
						}
                    }
                }

                xSemaphoreGive(xuart1_semaphore);
            }

            free(elm327_command.command);
			free(elm327_command.temp);
        }
    }
}

#define READ_TIMEOUT_MS 10
#define MAX_TOTAL_TIMEOUT_MS 1000

static int uart_read_until_pattern(uart_port_t uart_num, char* buffer, size_t buffer_size, 
                                 const char* end_pattern, int total_timeout_ms) 
{
    int total_len = 0;
    int64_t start_time = esp_timer_get_time() / 1000;  // Convert to milliseconds
    
    while (total_len < buffer_size - 1) 
	{  // Leave space for null terminator
        // Read a small chunk
        int len = uart_read_bytes(uart_num, buffer + total_len, 
                                buffer_size - total_len - 1, READ_TIMEOUT_MS);
        
        if (len > 0)
		{
            total_len += len;
            buffer[total_len] = '\0';  // Null terminate for string operations
            // ESP_LOG_BUFFER_HEXDUMP(TAG, buffer, total_len, ESP_LOG_INFO);
            // Check if we found our pattern
            if (strstr(buffer, end_pattern))
			{
                return total_len;
            }
        }
        
        // Check if we've exceeded our total timeout
        if ((esp_timer_get_time() / 1000 - start_time) >= total_timeout_ms)
		{
			ESP_LOGE(TAG, "Timeout!");
            break;
        }
    }
    
    return total_len;
}

bool elm327_set_baudrate(void)
{
    char rx_buffer[BUFFER_SIZE];
    int len;
    bool success = false;
    char command[20];
    
    if (xSemaphoreTake(xuart1_semaphore, portMAX_DELAY) == pdTRUE)
    {
        uart_flush(UART_NUM_1);
        if (uart1_queue)
        {
            xQueueReset(uart1_queue);
        }
        
        // uart_set_baudrate(UART_NUM_1, DESIRED_BAUD_RATE);
        ESP_LOGI(TAG, "Trying %d baud", DESIRED_BAUD_RATE);

        uart_write_bytes(UART_NUM_1, "VTVERS\r", 7);
        
        len = uart_read_until_pattern(UART_NUM_1, rx_buffer, BUFFER_SIZE - 1, "\r>", UART_TIMEOUT_MS);
        
        if (len > 2 && rx_buffer[len-2] == '\r' && rx_buffer[len-1] == '>')
        {
            ESP_LOGI(TAG, "Device already at %d baud", DESIRED_BAUD_RATE);
            success = true;
        }
        else
        {
            uart_set_baudrate(UART_NUM_1, DEFAULT_BAUD_RATE);
            ESP_LOGI(TAG, "Trying %d baud", DEFAULT_BAUD_RATE);
            
            uart_write_bytes(UART_NUM_1, "VTVERS\r", 7);
            len = uart_read_until_pattern(UART_NUM_1, rx_buffer, BUFFER_SIZE - 1, "\r>", UART_TIMEOUT_MS);
            
            if (len > 2 && rx_buffer[len-2] == '\r' && rx_buffer[len-1] == '>')
            {
                ESP_LOGI(TAG, "Connected at %d baud, switching to %d", 
                        DEFAULT_BAUD_RATE, DESIRED_BAUD_RATE);
                
                snprintf(command, sizeof(command), "STSBR %d\r", DESIRED_BAUD_RATE);
                uart_write_bytes(UART_NUM_1, command, strlen(command));
                len = uart_read_until_pattern(UART_NUM_1, rx_buffer, BUFFER_SIZE - 1, "OK", UART_TIMEOUT_MS);
                
                if (len > 0 && strstr(rx_buffer, "OK"))
                {
                    vTaskDelay(pdMS_TO_TICKS(100));
                    uart_set_baudrate(UART_NUM_1, DESIRED_BAUD_RATE);
                    
                    uart_write_bytes(UART_NUM_1, "VTVERS\r", 7);
                    len = uart_read_until_pattern(UART_NUM_1, rx_buffer, BUFFER_SIZE - 1, "\r>", UART_TIMEOUT_MS);
                    
                    if (len > 2 && rx_buffer[len-2] == '\r' && rx_buffer[len-1] == '>')
                    {
                        ESP_LOGI(TAG, "Successfully switched to %d baud", DESIRED_BAUD_RATE);
                        success = true;
                        
                        uart_write_bytes(UART_NUM_1, "STWBR\r", 6);
                        uart_read_until_pattern(UART_NUM_1, rx_buffer, BUFFER_SIZE - 1, "\r>", UART_TIMEOUT_MS);
                    }
                    else
                    {
                        ESP_LOGE(TAG, "Failed to verify new baud rate");
                    }
                }
                else
                {
                    ESP_LOGE(TAG, "Failed to change baud rate");
                }
            }
            else
            {
                ESP_LOGE(TAG, "No response at %d baud", DEFAULT_BAUD_RATE);
            }
        }
    }
    xSemaphoreGive(xuart1_semaphore);
    return success;
}

void elm327_lock(void)
{
	xSemaphoreTake(xuart1_semaphore, portMAX_DELAY);
}

void elm327_hardreset_chip(void)
{
    static char rsp_buffer[100];
    uint32_t rsp_len;
	if (xSemaphoreTake(xuart1_semaphore, portMAX_DELAY) == pdTRUE)
	{
		vTaskDelay(pdMS_TO_TICKS(300));
		uart_flush_input(UART_NUM_1);
		xQueueReset(uart1_queue);
		if(gpio_get_level(OBD_READY_PIN) == 1)
		{
			gpio_set_level(OBD_RESET_PIN, 0);
			vTaskDelay(pdMS_TO_TICKS(5));
			gpio_set_level(OBD_RESET_PIN, 1);
		}
		else
		{
			uart_write_bytes(UART_NUM_1, "ATZ\r", strlen("ATZ\r"));
		}
		memset(rsp_buffer, 0, sizeof(rsp_buffer));
        int len = uart_read_until_pattern(UART_NUM_1, rsp_buffer, BUFFER_SIZE - 1, "\r>", UART_TIMEOUT_MS+300);
		if(len > 0)
		{
			// ESP_LOG_BUFFER_CHAR(TAG, rsp_buffer, len);
			ESP_LOGW(TAG, "Hardreset OK");
		}
		else
		{
			ESP_LOGE(TAG, "Hardreset failed");
		}
		xSemaphoreGive(xuart1_semaphore);
	}
	
    if (elm327_set_baudrate())
    {
        ESP_LOGI(TAG, "UART configuration completed successfully");
    }
    else
    {
        ESP_LOGE(TAG, "UART configuration failed");
    }
}

esp_err_t elm327_sleep(void)
{
    static char rsp_buffer[100];
    uint32_t rsp_len;
	esp_err_t ret = ESP_FAIL;

	if (xSemaphoreTake(xuart1_semaphore, portMAX_DELAY) == pdTRUE)
	{
		uart_flush_input(UART_NUM_1);
		xQueueReset(uart1_queue);
        uart_write_bytes(UART_NUM_1, "STSLEEP0\r", strlen("STSLEEP0\r"));
        int len = uart_read_until_pattern(UART_NUM_1, rsp_buffer, sizeof(rsp_buffer), "\r>", UART_TIMEOUT_MS+300);
		if(len > 0 && strstr(rsp_buffer, "OK\r\r>"))
		{
			printf("Sleep OK\r\n");
			ESP_LOGW(TAG, "Sleep OK");
			ret = ESP_OK;
		}
		else
		{
			ret = ESP_FAIL;
		}
		// uart_flush_input(UART_NUM_1);
		// xQueueReset(uart1_queue);
		// xSemaphoreGive(xuart1_semaphore);
	}
	return ret;
}

void elm327_run_command(char* command, uint32_t command_len, uint32_t timeout, QueueHandle_t *response_q, response_callback_t response_callback)
{
	if (xSemaphoreTake(xuart1_semaphore, portMAX_DELAY) == pdTRUE)
    {
		uint32_t len;
		bool terminator_received = false;
		uart_event_t event;
		static uint8_t uart_read_buf[2048];
		
		if(command_len == 0)
		{
			len = strlen(command);
		}
		else
		{
			len = command_len;
		}
		uart_flush_input(UART_NUM_1);
		xQueueReset(uart1_queue);


		if(strstr(command, "ATZ\r") != NULL || 
		strstr(command, "atz\r") != NULL ||
		strstr(command, "AT Z\r") != NULL ||
		strstr(command, "at z\r") != NULL)
		{
			// if(strlen(elm327_command.command) < sizeof(last_command))
			// {
			// 	strcpy(last_command, elm327_command.command);
			// }
			uart_write_bytes(UART_NUM_1, "ATWS\r", strlen("ATWS\r"));
			uart_wait_tx_done(UART_NUM_1, pdMS_TO_TICKS(100));
		}
		else
		{
			uart_write_bytes(UART_NUM_1, command, len);
		}
		
		while (!terminator_received)
		{
			if (xQueueReceive(uart1_queue, (void*)&event, pdMS_TO_TICKS(ELM327_CMD_TIMEOUT_MS)) == pdTRUE)
			{
				if (event.type == UART_DATA)
				{
					int read_bytes = uart_read_bytes(UART_NUM_1, 
													uart_read_buf, 
													event.size, 
													pdMS_TO_TICKS(1));
					if (read_bytes > 0)
					{
						if(response_callback != NULL)
						{
							response_callback((char*)uart_read_buf, 
														read_bytes, 
														response_q,
														command);
						}
						
						if(strstr((char*) uart_read_buf, "\r>"))
						{
							terminator_received = true;
							break;
						}
					}

				}

			}
		}
		xSemaphoreGive(xuart1_semaphore);
	}
}

elm327_chip_status_t elm327_chip_get_status(void)
{
	elm327_chip_status_t status = gpio_get_level(OBD_READY_PIN);

	return status;
}

static int get_one_record(const uint8_t* data, int size, int index, char* record)
{
    bool found_line_end = false;
    int count = 0;
    
    while (index < size)
	{
        uint8_t byte = data[index++];
        
        if (byte == '\r' || byte == '\n')
		{
            if (!found_line_end) 
			{
                found_line_end = true;
                continue;
            }
            if (found_line_end && count > 0) break;
            continue;
        }
        
        if (byte >= 'a' && byte <= 'z')
		{
            byte -= ('a' - 'A');
        }
        
        if ((byte >= '0' && byte <= '9') || (byte >= 'A' && byte <= 'F'))
		{
            record[count++] = byte;
        }
    }
    
    record[count] = '\0';
    return index;
}

esp_err_t elm327_send_update_command(const char* cmd, char** response, size_t* response_size, int timeout_ms) 
{
    esp_err_t ret = ESP_FAIL;
    size_t alloc_size = ELM327_UPDATE_BUF_SIZE;
    *response = malloc(alloc_size);
    if (!*response)
	{
        ESP_LOGE(TAG, "Failed to allocate response buffer");
        return ESP_ERR_NO_MEM;
    }

    uart_flush(UART_NUM_1);
    
    ESP_LOGD(TAG, "Sending: %s", cmd);
    int written = uart_write_bytes(UART_NUM_1, cmd, strlen(cmd));
    if (written < 0)
	{
        ESP_LOGE(TAG, "Failed to write command");
        goto cleanup;
    }
    
    int len = 0;
    uint8_t byte_data;
    int64_t start_time = esp_timer_get_time() / 1000;
    
    while (true)
	{
        if ((esp_timer_get_time() / 1000 - start_time) >= timeout_ms)
		{
            ESP_LOGE(TAG, "Command timeout");
            ret = ESP_ERR_TIMEOUT;
            goto cleanup;
        }

        if (uart_read_bytes(UART_NUM_1, &byte_data, 1, pdMS_TO_TICKS(100)) != 1)
		{
            continue;
        }

        // Check if buffer needs to grow
        if (len >= alloc_size - 1)
		{
            alloc_size *= 2;
            char* new_buf = realloc(*response, alloc_size);
            if (!new_buf)
			{
                ESP_LOGE(TAG, "Failed to reallocate buffer");
                ret = ESP_ERR_NO_MEM;
                goto cleanup;
            }
            *response = new_buf;
        }

        (*response)[len] = (char)byte_data;
        
        // Skip command echo
        if (len == strlen(cmd)-1 && strncmp(*response, cmd, len+1) == 0)
		{
            len = 0;
            continue;
        }
        
        if (byte_data == '>') {
            (*response)[++len] = '\0';
            break;
        }
        
        if (byte_data != 0) {
            len++;
        }
    }

    *response_size = len;
    ESP_LOGD(TAG, "Response: %s", *response);
    
    if (strstr(*response, "OK") || strstr(*response, "MIC3624"))
	{
        ret = ESP_OK;
    }
	else if (strstr(*response, "?"))
	{
        ret = ESP_ERR_NOT_FOUND;
    }
	else
	{
        ESP_LOGW(TAG, "Unexpected response");
        ret = ESP_FAIL;
    }
    
    return ret;

cleanup:
    free(*response);
    *response = NULL;
    return ret;
}

esp_err_t elm327_check_obd_device() 
{
    char* response = NULL;
    size_t response_size = 0;
    device_status.in_normal_state = false;
	device_status.need_update = false;
	
    esp_err_t ret = elm327_send_update_command("VTVERS\r", &response, &response_size, ELM327_UPDATE_TIMEOUT_MS);
	
    if (response)
	{
		ESP_LOG_BUFFER_HEXDUMP(TAG, response, strlen(response), ESP_LOG_INFO);
        if (ret != ESP_OK)
		{
            if (ret == ESP_ERR_NOT_FOUND)
			{
                device_status.device_type = 0xFF;
                ret = ESP_OK;
            }
        }
		else if (strncmp(response, "MIC3624", 7) == 0)
		{
            device_status.in_normal_state = true;
            device_status.device_type = 0x3624;
			if(strstr(response, OBD_FW_VER) != NULL)
			{
				ESP_LOGI(TAG, "ELM327 OBD Firmware is already up to date.");
			}
			else
			{
				device_status.need_update = true;
			}
			
            ret = ESP_OK;
        }
		else
		{
            ESP_LOGE(TAG, "Unknown device");
            ret = ESP_FAIL;
        }
        free(response);
    }
    
    return ret;
}

static void elm327_disable_wake_commands(void)
{
	char* rx_buffer = malloc(256);
	xSemaphoreTake(xuart1_semaphore, portMAX_DELAY);
	//make sure chip goes to sleep
	uart_write_bytes(UART_NUM_1, "ATPP 0F SV 95\r", strlen("ATPP 0F SV 95\r"));
    uart_read_until_pattern(UART_NUM_1, rx_buffer, BUFFER_SIZE - 1, "\r>", UART_TIMEOUT_MS);
	bzero(rx_buffer, 256);
	vTaskDelay(pdMS_TO_TICKS(100));
	uart_write_bytes(UART_NUM_1, "ATPP 0F ON\r", strlen("ATPP 0F ON\r"));
    uart_read_until_pattern(UART_NUM_1, rx_buffer, BUFFER_SIZE - 1, "\r>", UART_TIMEOUT_MS);
	vTaskDelay(pdMS_TO_TICKS(100));
	xSemaphoreGive(xuart1_semaphore);
	elm327_hardreset_chip();
}

esp_err_t elm327_update_obd(bool force_update)
{
    const char* current_ptr = (const char*)obd_fw_start;
    const char* end_ptr = (const char*)obd_fw_end;
	xSemaphoreTake(xuart1_semaphore, portMAX_DELAY);
    esp_err_t ret = elm327_check_obd_device();
    if (ret != ESP_OK)
	{
		xSemaphoreGive(xuart1_semaphore);
        return ret;
    }

	if(device_status.need_update == false && force_update == false)
	{
		xSemaphoreGive(xuart1_semaphore);
		return ret;
	}

    ESP_LOGW(TAG, "MIC3624 Start update");
	
    char* response;
    size_t response_size;
    
    // Enter update mode
    ret = elm327_send_update_command("VTDLMIC3422\r", &response, &response_size, ELM327_UPDATE_TIMEOUT_MS);
    if (ret != ESP_OK)
	{
		xSemaphoreGive(xuart1_semaphore);
        return ret;
    }
    free(response);

	led_fast_blink(LED_RED, 255, true);

    uint32_t line_count = 0;
    bool update_complete = false;
    char line[ELM327_UPDATE_MAX_LINE_LENGTH];
    
    // Process embedded firmware data
    while (current_ptr < end_ptr)
	{
        // Copy line until newline or end of data
        size_t i = 0;
        while (current_ptr < end_ptr && i < (ELM327_UPDATE_MAX_LINE_LENGTH - 1) && *current_ptr != '\n')
		{
            line[i++] = *current_ptr++;
        }
        line[i] = '\0';
        
        // Skip remaining newline character if present
        if (current_ptr < end_ptr && *current_ptr == '\n')
		{
            current_ptr++;
        }

        line_count++;
        size_t len = strlen(line);

        // Remove carriage return if present
        if (len > 0 && line[len-1] == '\r')
		{
            line[--len] = '\0';
        }

        // Skip empty lines
        if (len == 0)
		{
            continue;
        }

        // Check for end marker
        if (strncmp(line, "FFF1", 4) == 0)
		{
            update_complete = true;
            break;
        }

        // Prepare and send command
        char* cmd = malloc(len + 8);  // "VTDLDT" + line + \r + \0
        if (!cmd)
		{
            ret = ESP_ERR_NO_MEM;
            break;
        }
        
        snprintf(cmd, len + 8, "VTDLDT%s\r", line);
        ret = elm327_send_update_command(cmd, &response, &response_size, ELM327_UPDATE_TIMEOUT_MS);
        free(cmd);
        if (response) free(response);
        
        if (ret != ESP_OK)
		{
            ESP_LOGE(TAG, "Failed at line %lu", line_count);
            break;
        }

        vTaskDelay(pdMS_TO_TICKS(1));
    }

    // End update if everything was successful
    if (ret == ESP_OK && update_complete)
	{
        ret = elm327_send_update_command("VTDLED\r", &response, &response_size, ELM327_UPDATE_TIMEOUT_MS);
        if (response) free(response);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
	
	ESP_LOGW(TAG, "ELM327 chip update DONE!");
	xSemaphoreGive(xuart1_semaphore);
    elm327_hardreset_chip();

	elm327_disable_wake_commands();
	led_fast_blink(LED_RED, 0, false);
	
	return ret;
}

esp_err_t elm327_update_obd_from_file(const char* filename)
{
    FILE* file = fopen(filename, "r");
    if (!file)
	{
        ESP_LOGE(TAG, "Failed to open file: %s", filename);
        return ESP_FAIL;
    }

    // Allocate line buffer
    char* line = malloc(ELM327_UPDATE_MAX_LINE_LENGTH);
    if (!line)
	{
        fclose(file);
        return ESP_ERR_NO_MEM;
    }

    esp_err_t ret = elm327_check_obd_device();
    if (ret != ESP_OK)
	{
        free(line);
        fclose(file);
        return ret;
    }
	ESP_LOGW(TAG, "MIC3624 Start update");
    char* response;
    size_t response_size;
    
    // Enter update mode
    ret = elm327_send_update_command("VTDLMIC3422\r", &response, &response_size, ELM327_UPDATE_TIMEOUT_MS);
    if (ret != ESP_OK)
	{
        free(line);
        fclose(file);
        return ret;
    }
    free(response);

	led_fast_blink(LED_RED, 255, true);

    uint32_t line_count = 0;
    bool update_complete = false;

    // Process file
    while (fgets(line, ELM327_UPDATE_MAX_LINE_LENGTH, file))
	{
        size_t len = strlen(line);
        line_count++;

        // Remove line endings
        while (len > 0 && (line[len-1] == '\n' || line[len-1] == '\r'))
		{
            line[--len] = '\0';
        }

        // Skip empty lines
        if (len == 0)
		{
            continue;
        }

        // Check for end marker
        if (strncmp(line, "FFF1", 4) == 0)
		{
            update_complete = true;
            break;
        }

        // Prepare and send command
        char* cmd = malloc(len + 8);  // "VTDLDT" + line + \r + \0
        if (!cmd)
		{
            ret = ESP_ERR_NO_MEM;
            break;
        }
        snprintf(cmd, len + 8, "VTDLDT%s\r", line);
        ret = elm327_send_update_command(cmd, &response, &response_size, ELM327_UPDATE_TIMEOUT_MS);
        free(cmd);
        if (response) free(response);
        
        if (ret != ESP_OK)
		{
            ESP_LOGE(TAG, "Failed at line %lu", line_count);
            break;
        }

        vTaskDelay(pdMS_TO_TICKS(1));
    }

    // Cleanup
    free(line);
    fclose(file);

    // End update if everything was successful
    if (ret == ESP_OK && update_complete)
	{
        ret = elm327_send_update_command("VTDLED\r", &response, &response_size, ELM327_UPDATE_TIMEOUT_MS);
        if (response) free(response);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }

	ESP_LOGW(TAG, "ELM327 chip update DONE!");
	elm327_hardreset_chip();
	elm327_disable_wake_commands();
	led_fast_blink(LED_RED, 0, false);
	
    return ret;
}

void elm327_read_task(void *pvParameters)
{
    uart_event_t event;
	static xdev_buffer dtmp;
    // uint8_t* dtmp = (uint8_t*) malloc(UART_BUF_SIZE);
    
    while(1) 
    {
        if(xQueuePeek(uart1_queue, (void *)&event, portMAX_DELAY)) 
        {
			xSemaphoreTake(xuart1_semaphore, portMAX_DELAY);
            bzero(dtmp.ucElement, sizeof(dtmp.ucElement));
			// TODO: fix this. Here it's checking if queu is not empty, other task might have processed the queue while waiting for empty queue
			if((xQueuePeek(uart1_queue, (void *)&event, 0)) == pdTRUE && xQueueReceive(uart1_queue, (void *)&event, portMAX_DELAY) == pdTRUE)
			{
				switch(event.type) 
				{
					case UART_DATA:
						uart_read_bytes(UART_NUM_1, dtmp.ucElement, event.size, portMAX_DELAY);
						
						if(elm327_response != NULL)
						{
							ESP_LOG_BUFFER_HEXDUMP(TAG, (char*)dtmp.ucElement, event.size, ESP_LOG_INFO);
							dtmp.usLen = event.size;
							// ESP_LOG_BUFFER_CHAR(TAG, (char*)dtmp, event.size);
							elm327_response((char*)dtmp.ucElement, 
													event.size, 
													xqueue_elm327_uart_rx, 
													NULL);
						}
						break;
					case UART_FIFO_OVF:
						uart_flush_input(UART_NUM_1);
						xQueueReset(uart1_queue);
						break;
					case UART_BUFFER_FULL:
						uart_flush_input(UART_NUM_1);
						xQueueReset(uart1_queue);
						break;
					default:
						break;
				}
			}
			xSemaphoreGive(xuart1_semaphore);
        }
    }
    
    vTaskDelete(NULL);
}


void elm327_init(response_callback_t rsp_callback, QueueHandle_t *rx_queue, void (*can_log)(twai_message_t* frame, uint8_t type))
{
    xqueue_elm327_uart_rx = rx_queue;
    elm327_can_log = can_log;
	elm327_response = rsp_callback;

    elm327_cmd_queue = xQueueCreate(ELM327_CMD_QUEUE_SIZE, sizeof(elm327_commands_t));
	if (elm327_cmd_queue == NULL){
		ESP_LOGE(TAG, "Failed to create queue");
	}

	xuart1_semaphore = xSemaphoreCreateMutex();
	if (xuart1_semaphore == NULL) {
		ESP_LOGE(TAG, "Failed to create semaphore");
	}
	
    uart_config_t uart1_config = 
    {
        .baud_rate = DESIRED_BAUD_RATE,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    esp_err_t ret = uart_driver_install(UART_NUM_1, UART_BUF_SIZE, UART_BUF_SIZE, 
                                      100, &uart1_queue, 0);
    if (ret != ESP_OK) 
    {
        ESP_LOGE(TAG, "Failed to install UART driver: %s", esp_err_to_name(ret));
    }

    uart_param_config(UART_NUM_1, &uart1_config);
    uart_set_pin(UART_NUM_1, GPIO_NUM_16, GPIO_NUM_15, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);

	vTaskDelay(pdMS_TO_TICKS(50));
	elm327_hardreset_chip();

	// elm327_update_obd_from_file("/sdcard/MIC3624_v2.3.07.beta4.txt");
	// elm327_update_obd_from_file("/sdcard/MIC3624_v2.3.10.txt");
	// elm327_update_obd_from_file("/sdcard/MIC3624_v2.3.18.txt");
	elm327_update_obd(false);

	while(elm327_chip_get_status() != ELM327_READY)
	{
		ESP_LOGW(TAG, "ELM327 not ready...");
		vTaskDelay(pdMS_TO_TICKS(200));
	}
	
    uart_flush(UART_NUM_1);

    obd_init();

    xTaskCreate(uart1_event_task, "uart1_event_task", 2048*2, NULL, 12, NULL);
	xTaskCreate(elm327_read_task, "elm327_read_task", 2048*2, NULL, 12, NULL);
 
}

#endif
