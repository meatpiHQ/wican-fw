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
#ifndef GVRET_h
#define GVRET_h

#define CFG_BUILD_NUM   	618
#define WIFI_BUFF_SIZE      2048
enum parse_state
{
	GET_HEADER = 0,
	GET_CMD,
};

enum STATE {
    IDLE,
    GET_COMMAND,
    BUILD_CAN_FRAME,
    TIME_SYNC,
    GET_DIG_INPUTS,
    GET_ANALOG_INPUTS,
    SET_DIG_OUTPUTS,
    SETUP_CANBUS,
    GET_CANBUS_PARAMS,
    GET_DEVICE_INFO,
    SET_SINGLEWIRE_MODE,
    SET_SYSTYPE,
    ECHO_CAN_FRAME,
    SETUP_EXT_BUSES
};

enum GVRET_PROTOCOL
{
    PROTO_BUILD_CAN_FRAME = 0,
    PROTO_TIME_SYNC = 1,
    PROTO_DIG_INPUTS = 2,
    PROTO_ANA_INPUTS = 3,
    PROTO_SET_DIG_OUT = 4,
    PROTO_SETUP_CANBUS = 5,
    PROTO_GET_CANBUS_PARAMS = 6,
    PROTO_GET_DEV_INFO = 7,
    PROTO_SET_SW_MODE = 8,
    PROTO_KEEPALIVE = 9,
    PROTO_SET_SYSTYPE = 10,
    PROTO_ECHO_CAN_FRAME = 11,
    PROTO_GET_NUMBUSES = 12,
    PROTO_GET_EXT_BUSES = 13,
    PROTO_SET_EXT_BUSES = 14
};
typedef struct  {
    uint32_t CAN0Speed;
    bool CAN0_Enabled;
    bool CAN0ListenOnly; //if true we don't allow any messing with the bus but rather just passively monitor.

    uint32_t CAN1Speed;
    bool CAN1_Enabled;
    bool CAN1ListenOnly;

    bool useBinarySerialComm; //use a binary protocol on the serial link or human readable format?

    uint8_t logLevel; //Level of logging to output on serial line
    uint8_t systemType; //0 = A0RET, 1 = EVTV ESP32 Board, maybe others in the future

    bool enableBT; //are we enabling bluetooth too?
    char btName[32];

    bool enableLawicel;

    //if we're using WiFi then output to serial is disabled (it's far too slow to keep up)
    uint8_t wifiMode; //0 = don't use wifi, 1 = connect to an AP, 2 = Create an AP
    char SSID[32];     //null terminated string for the SSID
    char WPA2Key[64]; //Null terminated string for the key. Can be a passphase or the actual key
}EEPROMSettings;
#define NUM_BUSES 1
#define MAX_CLIENTS		2
typedef struct  {
    uint8_t LED_CANTX;
    uint8_t LED_CANRX;
    uint8_t LED_LOGGING;
    bool fancyLED;
    bool txToggle; //LED toggle values
    bool rxToggle;
    bool logToggle;
    bool lawicelMode;
    bool lawicellExtendedMode;
    bool lawicelAutoPoll;
    bool lawicelTimestamping;
    int lawicelPollCounter;
    bool lawicelBusReception[NUM_BUSES]; //does user want to see messages from this bus?
    int8_t numBuses; //number of buses this hardware currently supports.
//    WiFiClient clientNodes[MAX_CLIENTS];
//    WiFiClient wifiOBDClients[MAX_CLIENTS];
    bool isWifiConnected;
    bool isWifiActive;
}SystemSettings;


void gvret_parse(uint8_t *buf, uint8_t len, twai_message_t *frame, QueueHandle_t *q);
#if HARDWARE_VER == WICAN_PRO
void gvret_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q, char* cmd_str));
#else
void gvret_init(void (*send_to_host)(char*, uint32_t, QueueHandle_t *q));
#endif
int8_t gvret_parse_can_frame(uint8_t *buf, twai_message_t *frame);

#endif
