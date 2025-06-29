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



#pragma once
#include "esp_tls_crypto.h"
#include <esp_http_server.h>

#define AP_MODE				0
#define APSTA_MODE			1

#define CAN_5K				0
#define CAN_10K				1
#define CAN_20K				2
#define CAN_25K				3
#define CAN_50K				4
#define CAN_100K			5
#define CAN_125K			6
#define CAN_250K			7
#define CAN_500K			8
#define CAN_800K			9
#define CAN_1000K			10

#define CAN_NORMAL			0
#define CAN_SILENT			1

#define UDP_PORT			0
#define TCP_PORT			1

#define SLCAN				0
#define REALDASH			1
#define SAVVYCAN			2
#define OBD_ELM327			3
#define AUTO_PID			4

typedef enum
{
	WIFI_OPEN,
	WIFI_WPA2_PSK,
	WIFI_WPA3_PSK,
	WIFI_MAX
}wifi_security_t;

typedef struct _device_config
{
	char wifi_mode[65];
	char ap_ch[65];
	char ap_auto_disable[12];
	char sta_ssid[65];
	char sta_pass[65];
	char sta_security[8];
	char can_datarate[65];
	char can_mode[65];
	char port_type[65];
	char port[65];
	char ap_pass[65];
	char protocol[65];
	char ble_pass[18];
	char ble_status[32];
	char sleep_status[32];
	char sleep_volt[10];
	char wakeup_volt[10];
	char sleep_time[32];
	char wakeup_time[32];
	char batt_alert[32];
	char batt_alert_ssid[65];
	char batt_alert_pass[65];
	char batt_alert_volt[32];
	char batt_alert_protocol[65];
	char batt_alert_url[256];
	char batt_alert_port[32];
	char batt_alert_topic[256];
	char batt_alert_time[16];
	char batt_mqtt_user[64];
	char batt_mqtt_pass[64];
	char keep_alive[16];
	char mqtt_en[10];
	char mqtt_tx_en[10];
	char mqtt_rx_en[10];
	char mqtt_url[256];
	char mqtt_port[32];
	char mqtt_user[64];
	char mqtt_pass[64];
	char mqtt_elm327_log[10];
	char mqtt_tx_topic[64];
	char mqtt_rx_topic[64];
	char mqtt_status_topic[64];
}device_config_t;


void config_server_start(QueueHandle_t *xTXp_Queue, QueueHandle_t *xRXp_Queue, uint8_t connected_led, char * did);
void config_server_stop(void);
int8_t config_server_get_wifi_mode(void);
int8_t config_server_get_ap_ch(void);
char *config_server_get_sta_ssid(void);
char *config_server_get_sta_pass(void);
int8_t config_server_get_can_rate(void);
int8_t config_server_get_can_mode(void);
int8_t config_server_get_port_type(void);
int32_t config_server_get_port(void);
//void config_server_wifi_connected(bool flag);
//bool config_server_get_wifi_connected(void);
void config_server_set_sta_ip(char* ip);
void config_server_get_sta_ip(char* ip);
char *config_server_get_ap_pass(void);
int8_t config_server_protocol(void);
int config_server_ble_pass(void);
int8_t config_server_get_sleep_config(void);
//void config_server_set_ble_tempfn(char b);
//char config_server_get_ble_tempfn(void);
int8_t config_server_get_ble_config(void);
void config_server_set_ble_config(uint8_t b);
void config_server_restart(void);
bool config_server_ws_connected(void);
int8_t config_server_get_sleep_volt(float *sleep_volt);
int8_t config_server_get_battery_alert_config(void);
int32_t config_server_get_alert_port(void);
char *config_server_get_alert_ssid(void);
char *config_server_get_alert_pass(void);
char *config_server_get_alert_protocol(void);
char *config_server_get_alert_url(void);
char *config_server_get_alert_topic(void);
int8_t config_server_get_alert_volt(float *alert_volt);
int config_server_get_alert_time(void);
char *config_server_get_alert_mqtt_user(void);
char *config_server_get_alert_mqtt_pass(void);
int8_t config_server_mqtt_en_config(void);
char *config_server_get_mqtt_url(void);
int32_t config_server_get_mqtt_port(void);
char *config_server_get_mqtt_user(void);
char *config_server_get_mmqtt_pass(void);
char *config_server_get_mqtt_canflt(void);
int8_t config_server_mqtt_elm327_log(void);
char *config_server_get_mqtt_tx_topic(void);
char *config_server_get_mqtt_rx_topic(void);
char *config_server_get_mqtt_status_topic(void);
int8_t config_server_mqtt_tx_en_config(void);
int8_t config_server_mqtt_rx_en_config(void);
int8_t config_server_get_wakeup_volt(float *wakeup_volt);
int8_t config_server_get_sleep_time(uint32_t *sleep_time);
int8_t config_server_get_wakeup_time(uint32_t *wakeup_time);
wifi_security_t config_server_get_sta_security(void);
int8_t config_server_get_ap_auto_disable(void);
int8_t config_server_get_keep_alive(uint32_t *keep_alive);
