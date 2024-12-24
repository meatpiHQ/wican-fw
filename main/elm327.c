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

#define TAG 		__func__

#define SERVICE_01			0x01
#define SERVICE_02			0x02
#define SERVICE_03			0x03
#define SERVICE_04			0x04
#define SERVICE_05			0x05
#define SERVICE_09			0x09
#define SERVICE_UNKNOWN		0xFF

uint8_t service_09_rsp_len[] = {1, 1, 4, 1, 255, 1, 255, 1, 1, 1, 4, 1}; //255 unknow

#define ELM327_READY_TO_RECEIVE_CAN			BIT0

static EventGroupHandle_t elm327_event_group = NULL;
static QueueHandle_t *can_rx_queue = NULL;

const char *ok_str = "OK";
const char *question_mark_str = "?";
const char *device_description = "ELM327 v1.3a";
const char *identify = "OBDLink MX";


void (*elm327_response)(char*, uint32_t, QueueHandle_t *q);
void (*elm327_can_log)(twai_message_t* frame, uint8_t type);
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
static SemaphoreHandle_t elm327_mutex = NULL;

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

uint32_t elm327_get_rx_address(void)
{
    if (elm327_config.rx_address_is_set)
	{
        return elm327_config.rx_address;
    }
	else
	{
        return 0;
    }
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
	char new_protocol;
	//Handle SPAx, and set it as x. 
	//TODO: add support for auto sp
	if(command_str[2] == 'a' || command_str[2] == 'A')
	{
		if(command_str[3] == '6' || command_str[3] == '7' || 
			command_str[3] == '8' || command_str[3] == '9')
		{
			new_protocol = command_str[3];
		}
		else
		{
			new_protocol = '4';
		}
	}
	else
	{
		new_protocol = command_str[2];
	}

	if(new_protocol == elm327_config.protocol)
	{
		return (char*)ok_str;
	}

	elm327_config.protocol = new_protocol;
	
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

char elm327_get_current_protocol(void)
{
	return elm327_config.protocol;
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

uint32_t elm327_get_identifier(void)
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
	while( xQueueReceive(*can_rx_queue, ( void * ) &rx_frame, pdMS_TO_TICKS(1)) == pdPASS );
	can_flush_rx();
	can_send(&txframe, 1);
	xEventGroupSetBits(elm327_event_group, ELM327_READY_TO_RECEIVE_CAN);

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
	xEventGroupClearBits(elm327_event_group, ELM327_READY_TO_RECEIVE_CAN);
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

void elm327_lock(void)
{
	xSemaphoreTake(elm327_mutex, pdMS_TO_TICKS(portMAX_DELAY));
}
void elm327_unlock(void)
{
	xSemaphoreGive(elm327_mutex);
}

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

uint8_t elm327_ready_to_receive(void)
{
    if (elm327_event_group == NULL)
	{
        return 0;
    }
    return (xEventGroupGetBits(elm327_event_group) & ELM327_READY_TO_RECEIVE_CAN) ? 1 : 0;
}


void elm327_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q), QueueHandle_t *rx_queue, void (*can_log)(twai_message_t* frame, uint8_t type))
{
	elm327_mutex = xSemaphoreCreateMutex();
	elm327_event_group = xEventGroupCreate();
    if (elm327_mutex == NULL || elm327_event_group == NULL)
	{
        ESP_LOGE(TAG, "Failed to create elm327 mutex");
        return;
    }
	
	elm327_set_default_config(true);
	elm327_response = send_to_host;
	can_rx_queue = rx_queue;
	elm327_can_log = can_log;
}
