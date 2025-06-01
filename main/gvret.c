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
#include "esp_timer.h"
#include "lwip/sockets.h"
#include "lwip/sys.h"
#include <lwip/netdb.h>
#include "can.h"
#include "config_server.h"
#include "gvret.h"
#include "comm_server.h"

#define TAG 		__func__

static SystemSettings SysSettings;
static EEPROMSettings settings;

static uint8_t transmitBuffer[WIFI_BUFF_SIZE];
static uint16_t transmitBufferLength = 0;

static SemaphoreHandle_t xgvert_tmr_semaphore;
static int64_t gvert_tmr_start_time = 0;
static esp_timer_handle_t periodic_timer;

static const uint32_t can_speed[] = {5000, 10000, 20000, 25000, 50000, 100000,
						125000, 250000, 500000, 800000, 1000000};

void (*gvret_response)(char*, uint32_t, QueueHandle_t *q, char* cmd_str);

void gvert_tmr_set_start(void)
{
	xSemaphoreTake(xgvert_tmr_semaphore, portMAX_DELAY);
	gvert_tmr_start_time = esp_timer_get_time();
	xSemaphoreGive(xgvert_tmr_semaphore);
}

int64_t gvert_tmr_get()
{
	int64_t ret = 0;
	xSemaphoreTake(xgvert_tmr_semaphore, portMAX_DELAY);
	ret = esp_timer_get_time() - gvert_tmr_start_time;
	xSemaphoreGive(xgvert_tmr_semaphore);

	return (int64_t)ret;
}

static void periodic_timer_callback(void* arg)
{
    int64_t time_since_boot = esp_timer_get_time();

    ESP_LOGW(__func__, "Periodic timer called, time since boot: %lld us, %lld", time_since_boot, gvert_tmr_get());
    gvert_tmr_set_start();
}

uint8_t checksumCalc(uint8_t *buffer, int length)
{
    uint8_t valu = 0;
    for (int c = 0; c < length; c++) {
        valu ^= buffer[c];
    }
    return valu;
}

void gvert_setup(EEPROMSettings *settings)
{
	can_disable();
	ESP_LOGI(__func__, "settings->CAN0Speed: %lu", settings->CAN0Speed);
	for(uint8_t i = 0; i < sizeof(can_speed)/sizeof(uint32_t); i++)
	{
		if(can_speed[i] == settings->CAN0Speed)
		{
			can_set_bitrate(i);
			ESP_LOGI(__func__, "CAN0 speed: %u", i);
			break;
		}
	}

	if(settings->CAN0ListenOnly)
	{
		can_set_silent(1);
	}
	else
	{
		can_set_silent(0);
	}

	if(settings->CAN0_Enabled)
	{
		can_enable();
		ESP_LOGI(__func__, "can_enabled");
	}
}

void gvret_parse(uint8_t *buf, uint8_t len, twai_message_t *frame, QueueHandle_t *q)
{
	static uint8_t state = IDLE;
	static uint16_t step = 0;
	static uint32_t build_int;

	static uint8_t buff[20];
	static uint32_t busSpeed = 0;
	static uint32_t now = 0;
	static uint8_t temp8, in_byte;
	static uint16_t temp16, i;

	now = gvert_tmr_get();


	for(i = 0; i < len; i++)
    {
    	in_byte = buf[i];
		switch (state) {
		case IDLE:
			if(in_byte == 0xF1)
			{
				state = GET_COMMAND;
			}
			else if(in_byte == 0xE7)
			{
				settings.useBinarySerialComm = true;
				SysSettings.lawicelMode = false;
				//setPromiscuousMode(); //going into binary comm will set promisc. mode too.
			}
			else
			{
//				console.rcvCharacter((uint8_t) in_byte);
			}
			break;
		case GET_COMMAND:
			switch(in_byte)
			{
			case PROTO_BUILD_CAN_FRAME:
				state = BUILD_CAN_FRAME;
				buff[0] = 0xF1;
				step = 0;
				break;
			case PROTO_TIME_SYNC:
				state = TIME_SYNC;
				step = 0;
				transmitBuffer[transmitBufferLength++] = 0xF1;
				transmitBuffer[transmitBufferLength++] = 1; //time sync
				transmitBuffer[transmitBufferLength++] = (uint8_t) (now & 0xFF);
				transmitBuffer[transmitBufferLength++] = (uint8_t) (now >> 8);
				transmitBuffer[transmitBufferLength++] = (uint8_t) (now >> 16);
				transmitBuffer[transmitBufferLength++] = (uint8_t) (now >> 24);
				break;
			case PROTO_DIG_INPUTS:
				//immediately return the data for digital inputs
				temp8 = 0; //getDigital(0) + (getDigital(1) << 1) + (getDigital(2) << 2) + (getDigital(3) << 3) + (getDigital(4) << 4) + (getDigital(5) << 5);
				transmitBuffer[transmitBufferLength++] = 0xF1;
				transmitBuffer[transmitBufferLength++] = 2; //digital inputs
				transmitBuffer[transmitBufferLength++] = temp8;
				temp8 = checksumCalc(buff, 2);
				transmitBuffer[transmitBufferLength++]  = temp8;
				gvret_response((char*)transmitBuffer, transmitBufferLength, q, NULL);
				transmitBufferLength = 0;
				state = IDLE;
				break;
			case PROTO_ANA_INPUTS:
				//immediately return data on analog inputs
				temp16 = 0;// getAnalog(0);  // Analogue input 1
				transmitBuffer[transmitBufferLength++] = 0xF1;
				transmitBuffer[transmitBufferLength++] = 3;
				transmitBuffer[transmitBufferLength++] = temp16 & 0xFF;
				transmitBuffer[transmitBufferLength++] = (uint8_t)(temp16 >> 8);
				temp16 = 0;//getAnalog(1);  // Analogue input 2
				transmitBuffer[transmitBufferLength++] = temp16 & 0xFF;
				transmitBuffer[transmitBufferLength++] = (uint8_t)(temp16 >> 8);
				temp16 = 0;//getAnalog(2);  // Analogue input 3
				transmitBuffer[transmitBufferLength++] = temp16 & 0xFF;
				transmitBuffer[transmitBufferLength++] = (uint8_t)(temp16 >> 8);
				temp16 = 0;//getAnalog(3);  // Analogue input 4
				transmitBuffer[transmitBufferLength++] = temp16 & 0xFF;
				transmitBuffer[transmitBufferLength++] = (uint8_t)(temp16 >> 8);
				temp16 = 0;//getAnalog(4);  // Analogue input 5
				transmitBuffer[transmitBufferLength++] = temp16 & 0xFF;
				transmitBuffer[transmitBufferLength++] = (uint8_t)(temp16 >> 8);
				temp16 = 0;//getAnalog(5);  // Analogue input 6
				transmitBuffer[transmitBufferLength++] = temp16 & 0xFF;
				transmitBuffer[transmitBufferLength++] = (uint8_t)(temp16 >> 8);
				temp16 = 0;//getAnalog(6);  // Vehicle Volts
				transmitBuffer[transmitBufferLength++] = temp16 & 0xFF;
				transmitBuffer[transmitBufferLength++] = (uint8_t)(temp16 >> 8);
				temp8 = checksumCalc(buff, 9);
				transmitBuffer[transmitBufferLength++] = temp8;
				gvret_response((char*)transmitBuffer, transmitBufferLength, q, NULL);
				transmitBufferLength = 0;
				state = IDLE;
				break;
			case PROTO_SET_DIG_OUT:
				state = SET_DIG_OUTPUTS;
				buff[0] = 0xF1;
				break;
			case PROTO_SETUP_CANBUS:
				state = SETUP_CANBUS;
				step = 0;
				buff[0] = 0xF1;
				break;
			case PROTO_GET_CANBUS_PARAMS:
//				ESP_LOG_BUFFER_HEXDUMP(__func__, buf, len, ESP_LOG_INFO);
				//immediately return data on canbus params
				transmitBuffer[transmitBufferLength++] = 0xF1;
				transmitBuffer[transmitBufferLength++] = 6;
				transmitBuffer[transmitBufferLength++] = settings.CAN0_Enabled + ((unsigned char) settings.CAN0ListenOnly << 4);
				transmitBuffer[transmitBufferLength++] = settings.CAN0Speed;
				transmitBuffer[transmitBufferLength++] = settings.CAN0Speed >> 8;
				transmitBuffer[transmitBufferLength++] = settings.CAN0Speed >> 16;
				transmitBuffer[transmitBufferLength++] = settings.CAN0Speed >> 24;
				transmitBuffer[transmitBufferLength++] = 0;
				transmitBuffer[transmitBufferLength++] = settings.CAN1Speed;
				transmitBuffer[transmitBufferLength++] = settings.CAN1Speed >> 8;
				transmitBuffer[transmitBufferLength++] = settings.CAN1Speed >> 16;
				transmitBuffer[transmitBufferLength++] = settings.CAN1Speed >> 24;
				gvret_response((char*)transmitBuffer, transmitBufferLength, q, NULL);
				transmitBufferLength = 0;
				state = IDLE;
				break;
			case PROTO_GET_DEV_INFO:
				//immediately return device information
				transmitBuffer[transmitBufferLength++] = 0xF1;
				transmitBuffer[transmitBufferLength++] = 7;
				transmitBuffer[transmitBufferLength++] = CFG_BUILD_NUM & 0xFF;
				transmitBuffer[transmitBufferLength++] = (CFG_BUILD_NUM >> 8);
				transmitBuffer[transmitBufferLength++] = 0x20;
				transmitBuffer[transmitBufferLength++] = 0;
				transmitBuffer[transmitBufferLength++] = 0;
				transmitBuffer[transmitBufferLength++] = 0; //was single wire mode. Should be rethought for this board.
				gvret_response((char*)transmitBuffer, transmitBufferLength, q, NULL);
				transmitBufferLength = 0;
				state = IDLE;
				break;
			case PROTO_SET_SW_MODE:
				buff[0] = 0xF1;
				state = SET_SINGLEWIRE_MODE;
				step = 0;
				break;
			case PROTO_KEEPALIVE:
				transmitBuffer[transmitBufferLength++] = 0xF1;
				transmitBuffer[transmitBufferLength++] = 0x09;
				transmitBuffer[transmitBufferLength++] = 0xDE;
				transmitBuffer[transmitBufferLength++] = 0xAD;
				gvret_response((char*)transmitBuffer, transmitBufferLength, q, NULL);
				transmitBufferLength = 0;
				state = IDLE;
				break;
			case PROTO_SET_SYSTYPE:
				buff[0] = 0xF1;
				state = SET_SYSTYPE;
				step = 0;
				break;
			case PROTO_ECHO_CAN_FRAME:
				state = ECHO_CAN_FRAME;
				buff[0] = 0xF1;
				step = 0;
				break;
			case PROTO_GET_NUMBUSES:
				transmitBuffer[transmitBufferLength++] = 0xF1;
				transmitBuffer[transmitBufferLength++] = 12;
				transmitBuffer[transmitBufferLength++] = 1;//SysSettings.numBuses;
				gvret_response((char*)transmitBuffer, transmitBufferLength, q, NULL);
				transmitBufferLength = 0;
				state = IDLE;
				break;
			case PROTO_GET_EXT_BUSES:
				transmitBuffer[transmitBufferLength++]  = 0xF1;
				transmitBuffer[transmitBufferLength++]  = 13;
				for (int u = 2; u < 17; u++) transmitBuffer[transmitBufferLength++] = 0;
				step = 0;
				gvret_response((char*)transmitBuffer, transmitBufferLength, q, NULL);
				transmitBufferLength = 0;
				state = IDLE;
				break;
			case PROTO_SET_EXT_BUSES:
				state = SETUP_EXT_BUSES;
				step = 0;
				buff[0] = 0xF1;
				break;
			}
			break;
		case BUILD_CAN_FRAME:
//			ESP_LOG_BUFFER_HEXDUMP(__func__, buf, len, ESP_LOG_INFO);
			buff[1 + step] = in_byte;
			switch(step)
			{
				case 0:
//					build_out_frame.id = in_byte;
					frame->identifier = in_byte;
					break;
				case 1:
//					build_out_frame.id |= in_byte << 8;
					frame->identifier |= in_byte << 8;
					break;
				case 2:
//					build_out_frame.id |= in_byte << 16;
					frame->identifier |= in_byte << 16;
					break;
				case 3:
//					build_out_frame.id |= in_byte << 24;
					frame->identifier |= in_byte << 24;
//					if(build_out_frame.id & 1 << 31)
					if(frame->identifier & 1 << 31)
					{
//						build_out_frame.id &= 0x7FFFFFFF;
//						build_out_frame.extended = true;
						frame->extd = 1;
					}
					else
					{
//						build_out_frame.extended = false;
						frame->extd = 0;
					}
					break;
				case 4:
//					out_bus = in_byte & 3;
					break;
				case 5:
//					build_out_frame.length = in_byte & 0xF;
					frame->data_length_code = in_byte & 0xF;
					if(frame->data_length_code > 8)
					{
						frame->data_length_code = 8;
					}
//					if(build_out_frame.length > 8)
//					{
//						build_out_frame.length = 8;
//					}
					break;
				default:
//					if(step < build_out_frame.length + 6)
//					{
//						build_out_frame.data.uint8[step - 6] = in_byte;
//					}
//					else
//					{
//						state = IDLE;
//						//this would be the checksum byte. Compute and compare.
//						//temp8 = checksumCalc(buff, step);
//						build_out_frame.rtr = 0;
//						if (out_bus == 0) canManager.sendFrame(&CAN0, build_out_frame);
//						if (out_bus == 1) canManager.sendFrame(&CAN1, build_out_frame);
//					}
					if(step < frame->data_length_code + 6)
					{
						frame->data[step - 6] = in_byte;
					}
					else
					{
						state = IDLE;
						//this would be the checksum byte. Compute and compare.
						//temp8 = checksumCalc(buff, step);
						frame->rtr = 0;
						frame->self = 0;
						if (ESP_ERR_INVALID_STATE == can_send(frame, 1))
						{
							ESP_LOGE(__func__, "can_send error");
						}
//						if (out_bus == 0) canManager.sendFrame(&CAN0, build_out_frame);
//						if (out_bus == 1) canManager.sendFrame(&CAN1, build_out_frame);
					}
					break;
			}
			step++;
			break;
			case TIME_SYNC:
				state = IDLE;
				gvret_response((char*)transmitBuffer, transmitBufferLength, q, NULL);
				transmitBufferLength = 0;
				break;
			case GET_DIG_INPUTS:
				// nothing to do
				break;
			case GET_ANALOG_INPUTS:
				// nothing to do
				break;
			case SET_DIG_OUTPUTS: //todo: validate the XOR byte
				buff[1] = in_byte;
				//temp8 = checksumCalc(buff, 2);
//				for(int c = 0; c < 8; c++){
//					if(in_byte & (1 << c)) setOutput(c, true);
//					else setOutput(c, false);
//				}
				state = IDLE;
				break;
			case SETUP_CANBUS: //todo: validate checksum
//				ESP_LOG_BUFFER_HEXDUMP(__func__, buf, len, ESP_LOG_INFO);
				switch(step)
				{
					case 0:
						build_int = in_byte;
						break;
					case 1:
						build_int |= in_byte << 8;
						break;
					case 2:
						build_int |= in_byte << 16;
						break;
					case 3:
						build_int |= in_byte << 24;
						busSpeed = build_int & 0xFFFFF;
						if(busSpeed > 1000000) busSpeed = 1000000;

						if(build_int > 0)
						{
							if(build_int & 0x80000000ul) //signals that enabled and listen only status are also being passed
							{
								if(build_int & 0x40000000ul)
								{
									settings.CAN0_Enabled = true;
								} else
								{
									settings.CAN0_Enabled = false;
								}
								if(build_int & 0x20000000ul)
								{
									settings.CAN0ListenOnly = true;
								} else
								{
									settings.CAN0ListenOnly = false;
								}
							} else
							{
								//if not using extended status mode then just default to enabling - this was old behavior
								settings.CAN0_Enabled = true;
							}
							//CAN0.set_baudrate(build_int);
							settings.CAN0Speed = busSpeed;
						} else { //disable first canbus
							settings.CAN0_Enabled = false;
						}
						gvert_setup(&settings);
//						if (settings.CAN0_Enabled)
//						{
//	//						CAN0.begin(settings.CAN0Speed, 255);
//	//						if (settings.CAN0ListenOnly) CAN0.setListenOnlyMode(true);
//	//						else CAN0.setListenOnlyMode(false);
//	//						CAN0.watchFor();
//							gvert_setup(&settings);
//						}
//						else
//						{
//	//						CAN0.disable();
//						}
						break;
					case 4:
						build_int = in_byte;
						break;
					case 5:
						build_int |= in_byte << 8;
						break;
					case 6:
						build_int |= in_byte << 16;
						break;
					case 7:
						build_int |= in_byte << 24;
						busSpeed = build_int & 0xFFFFF;
						if(busSpeed > 1000000) busSpeed = 1000000;

						if(build_int > 0 && SysSettings.numBuses > 1)
						{
							if(build_int & 0x80000000ul) //signals that enabled and listen only status are also being passed
							{
								if(build_int & 0x40000000ul)
								{
									settings.CAN1_Enabled = true;
								} else
								{
									settings.CAN1_Enabled = false;
								}
								if(build_int & 0x20000000ul)
								{
									settings.CAN1ListenOnly = true;
								} else
								{
									settings.CAN1ListenOnly = false;
								}
							} else
							{
								//if not using extended status mode then just default to enabling - this was old behavior
								settings.CAN1_Enabled = true;
							}
							//CAN1.set_baudrate(build_int);
							settings.CAN1Speed = busSpeed;
						} else { //disable first canbus
							settings.CAN1_Enabled = false;
						}

	//					if (settings.CAN1_Enabled)
	////					{
	////						CAN1.begin(settings.CAN0Speed, 255);
	////						if (settings.CAN1ListenOnly) CAN1.setListenOnlyMode(true);
	////						else CAN1.setListenOnlyMode(false);
	////						CAN1.watchFor();
	//					}
	//					else
	//					{
	////						CAN1.disable();
	//					}

						state = IDLE;
						//now, write out the new canbus settings to EEPROM
						//EEPROM.writeBytes(0, &settings, sizeof(settings));
						//EEPROM.commit();
						//setPromiscuousMode();
						break;
				}
				step++;
				break;
			case GET_CANBUS_PARAMS:
				// nothing to do
				break;
			case GET_DEVICE_INFO:
				// nothing to do
				break;
			case SET_SINGLEWIRE_MODE:
				if(in_byte == 0x10){
				} else {
				}
				//EEPROM.writeBytes(0, &settings, sizeof(settings));
				//EEPROM.commit();
				state = IDLE;
				break;
			case SET_SYSTYPE:
				settings.systemType = in_byte;
				//EEPROM.writeBytes(0, &settings, sizeof(settings));
				//EEPROM.commit();
				//loadSettings();
//				ESP_LOG_BUFFER_HEXDUMP(__func__, buf, len, ESP_LOG_INFO);
				state = IDLE;
				break;
			case ECHO_CAN_FRAME:

				buff[1 + step] = in_byte;
				switch(step)
				{
					static uint8_t tmp_len = 0;
					case 0:
//						build_out_frame.id = in_byte;
						break;
					case 1:
//						build_out_frame.id |= in_byte << 8;
						break;
					case 2:
//						build_out_frame.id |= in_byte << 16;
						break;
					case 3:
//						build_out_frame.id |= in_byte << 24;
//						if(build_out_frame.id & 1 << 31) {
//							build_out_frame.id &= 0x7FFFFFFF;
//							build_out_frame.extended = true;
//						} else build_out_frame.extended = false;
						break;
					case 4:
//						out_bus = in_byte & 1;
						break;
					case 5:
//						build_out_frame.length = in_byte & 0xF;
//						if(build_out_frame.length > 8) build_out_frame.length = 8;
						tmp_len = in_byte & 0xF;
						if(tmp_len > 8) tmp_len = 8;

						break;
					default:
						if(step < tmp_len + 6)
						{
//							build_out_frame.data.bytes[step - 6] = in_byte;
						}
						else
						{
							tmp_len = 0;
							state = IDLE;
							//this would be the checksum byte. Compute and compare.
							//temp8 = checksumCalc(buff, step);
							//if (temp8 == in_byte)
							//{
//							toggleRXLED();
							//if(isConnected) {
//							canManager.displayFrame(build_out_frame, 0);
							//}
							//}
						}
						break;
				}
				step++;
				break;
			case SETUP_EXT_BUSES: //setup enable/listenonly/speed for SWCAN, Enable/Speed for LIN1, LIN2
				switch(step)
				{
				case 0:
					build_int = in_byte;
					break;
				case 1:
					build_int |= in_byte << 8;
					break;
				case 2:
					build_int |= in_byte << 16;
					break;
				case 3:
					build_int |= in_byte << 24;
					break;
				case 4:
					build_int = in_byte;
					break;
				case 5:
					build_int |= in_byte << 8;
					break;
				case 6:
					build_int |= in_byte << 16;
					break;
				case 7:
					build_int |= in_byte << 24;
					break;
				case 8:
					build_int = in_byte;
					break;
				case 9:
					build_int |= in_byte << 8;
					break;
				case 10:
					build_int |= in_byte << 16;
					break;
				case 11:
					build_int |= in_byte << 24;
					state = IDLE;
					//now, write out the new canbus settings to EEPROM
					//EEPROM.writeBytes(0, &settings, sizeof(settings));
					//EEPROM.commit();
					//setPromiscuousMode();
					break;
				}
			step++;
			break;
		}
    }
}

int8_t gvret_parse_can_frame(uint8_t *buf, twai_message_t *frame)
{
	uint8_t length = 0;
    if (frame->extd)
    {
    	frame->identifier |= 1 << 31;
    }
    buf[length++] = 0xF1;
    buf[length++] = 0; //0 = canbus frame sending
    uint32_t now = gvert_tmr_get();
    buf[length++] = (uint8_t)(now & 0xFF);
    buf[length++] = (uint8_t)(now >> 8);
    buf[length++] = (uint8_t)(now >> 16);
    buf[length++] = (uint8_t)(now >> 24);
    buf[length++] = (uint8_t)(frame->identifier & 0xFF);
    buf[length++] = (uint8_t)(frame->identifier >> 8);
    buf[length++] = (uint8_t)(frame->identifier >> 16);
    buf[length++] = (uint8_t)(frame->identifier >> 24);
    buf[length++] = frame->data_length_code;
    for (int c = 0; c < frame->data_length_code; c++)
    {
        buf[length++] = frame->data[c];
    }
    //temp = checksumCalc(buff, 11 + frame.length);
    uint8_t temp = checksumCalc(transmitBuffer, 11 + frame->data_length_code);
    buf[length++] = temp;

    return (int8_t)length;
}


static void gvret_broadcast_task(void *pvParameters)
{
	uint8_t buff[4] = {0x1C,0xEF,0xAC,0xED};
    int addr_family = (int)pvParameters;
    int ip_protocol = 0;
    struct sockaddr_in6 dest_addr;
    char *broadcastIP = "255.255.255.255";
    struct sockaddr_in broadcastAddr; /* Broadcast address */
    struct sockaddr_in *dest_addr_ip4 = (struct sockaddr_in *)&dest_addr;

    dest_addr_ip4->sin_addr.s_addr = htonl(INADDR_ANY);
    dest_addr_ip4->sin_family = AF_INET;
    dest_addr_ip4->sin_port = htons(17222);
    ip_protocol = IPPROTO_IP;

    int sock = socket(addr_family, SOCK_DGRAM, ip_protocol);
    if (sock < 0) {
        ESP_LOGE(TAG, "Unable to create socket: errno %d", errno);
//        break;
    }
    ESP_LOGI(TAG, "Socket created");

    int err = bind(sock, (struct sockaddr *)&dest_addr, sizeof(dest_addr));
    if (err < 0) {
        ESP_LOGE(TAG, "Socket unable to bind: errno %d", errno);
    }
    ESP_LOGI(TAG, "Socket bound, port %d", 17222);

    memset(&broadcastAddr, 0, sizeof(broadcastAddr));   	/* Zero out structure */
    broadcastAddr.sin_family = AF_INET;               		/* Internet address family */
    broadcastAddr.sin_addr.s_addr = inet_addr(broadcastIP);	/* Broadcast IP address */
    broadcastAddr.sin_port = htons(17222);         			/* Broadcast port */
    vTaskDelay(pdMS_TO_TICKS(2000));
	while(1)
	{
		vTaskDelay(pdMS_TO_TICKS(1000));
		if(!tcp_port_open())
		{
			int err = sendto(sock, buff, 4, 0, (struct sockaddr *)&broadcastAddr, sizeof(broadcastAddr));
			if (err < 0)
			{
				ESP_LOGE(TAG, "Error occurred during sending: errno %d", errno);
//				break;
			}
		}
	}

}

#if HARDWARE_VER == WICAN_PRO
void gvret_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q, char* cmd_str))
#else
void gvret_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q))
#endif
{
	gvret_response = send_to_host;

    const esp_timer_create_args_t periodic_timer_args =
    {
            .callback = &periodic_timer_callback,
            /* name is optional, but may help identify the timer when debugging */
            .name = "periodic"
    };

    settings.CAN0_Enabled = can_is_enabled();
    if(config_server_get_can_mode() == CAN_SILENT)
    {
    	settings.CAN0ListenOnly = true;
    }
    else settings.CAN0ListenOnly = false;

    settings.CAN0Speed = can_speed[config_server_get_can_rate()];

    xgvert_tmr_semaphore = xSemaphoreCreateMutex();
    if(esp_timer_create(&periodic_timer_args, &periodic_timer))
	{
		ESP_LOGE(TAG, "Failed to create timer");
		return;
	}

    gvert_tmr_start_time = esp_timer_get_time();
    if(esp_timer_start_periodic(periodic_timer, 4294967296-100) != ESP_OK)
	{
		ESP_LOGE(TAG, "Failed to start timer");
		return;
	}

    static StackType_t *gvret_bcast_task_stack;
    static StaticTask_t gvret_bcast_task_buffer;
    
    gvret_bcast_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    
    if (gvret_bcast_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate GVRET broadcast task stack memory");
        return;
    }
    
    // Create static task
    TaskHandle_t gvret_bcast_handle = xTaskCreateStatic(
        gvret_broadcast_task,
        "gvret_bcast_task",
        4096,
        (void*)AF_INET,
        5,
        gvret_bcast_task_stack,
        &gvret_bcast_task_buffer
    );
    
    if (gvret_bcast_handle == NULL)
    {
        ESP_LOGE(TAG, "Failed to create GVRET broadcast task");
        heap_caps_free(gvret_bcast_task_stack);
        return;
    }
}
