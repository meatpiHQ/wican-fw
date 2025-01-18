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
#include "esp_timer.h"
#include "esp_bt.h"

#include "esp_gap_ble_api.h"
#include "esp_gatts_api.h"
#include "esp_bt_defs.h"
#include "esp_bt_main.h"

#include "esp_system.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include <string.h>
#include "lwip/sockets.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_gatt_common_api.h"
#include "types.h"
#include "ble.h"
#include "comm_server.h"
#include "config_server.h"
#include "wifi_network.h"

#define ADV_CONFIG_FLAG                           (1 << 0)
#define SCAN_RSP_CONFIG_FLAG                      (1 << 1)
#define GATTS_TABLE_TAG "BLE"

#define SPP_PROFILE_NUM             1
#define SPP_PROFILE_APP_IDX         0
#define ESP_SPP_APP_ID              0x56
// #define HEART_RATE_SVC_INST_ID                    0
// #define EXT_ADV_HANDLE                            0
// #define NUM_EXT_ADV_SET                           1
// #define EXT_ADV_DURATION                          0
// #define EXT_ADV_MAX_EVENTS                        0

#define GATTS_DEMO_CHAR_VAL_LEN_MAX               0x40
#define BLE_SEND_BUF_SIZE                         490

#define BLE_CONNECTED_BIT 			BIT0
#define BLE_CONGEST_BIT				BIT1

#define CHAR_DECLARATION_SIZE       (sizeof(uint8_t))
#define SVC_INST_ID         

// Service Instance IDs
#define DEV_INFO_SVC_INST_ID    0
#define SSP_SVC_INST_ID         1
#define FFF0_SVC_INST_ID        2

enum {
    IDX_SVC_DEVICE_INFO,                     // 0

    IDX_CHAR_MANUFACTURER_DECL,              // 1
    IDX_CHAR_MANUFACTURER_VAL,               // 2

    IDX_CHAR_MODEL_DECL,                     // 3
    IDX_CHAR_MODEL_VAL,                      // 4

    IDX_CHAR_SERIAL_DECL,                    // 5
    IDX_CHAR_SERIAL_VAL,                     // 6

    IDX_CHAR_HW_REV_DECL,                    // 7
    IDX_CHAR_HW_REV_VAL,                     // 8

    IDX_CHAR_FW_REV_DECL,                    // 9
    IDX_CHAR_FW_REV_VAL,                     // 10

    IDX_CHAR_SW_REV_DECL,                    // 11
    IDX_CHAR_SW_REV_VAL,                     // 12

    IDX_CHAR_SYSTEM_ID_DECL,                 // 13
    IDX_CHAR_SYSTEM_ID_VAL,                  // 14

    IDX_CHAR_REG_CERT_DECL,                  // 15
    IDX_CHAR_REG_CERT_VAL,                   // 16

    DEVICE_INFO_IDX_NB                       // 17 total
};

enum {
    IDX_SVC_SPP,                     // 0

    IDX_CHAR_SPP_RW_DECL,            // 1
    IDX_CHAR_SPP_RW_VAL,             // 2

    IDX_CHAR_SPP_WN_DECL,            // 3
    IDX_CHAR_SPP_WN_VAL,             // 4
    IDX_CHAR_SPP_WN_CCCD,            // 5  (Client Characteristic Configuration Descriptor)

    SPP_IDX_NB
};

enum {
    IDX_SVC_FFF0,                // 0

    IDX_CHAR_FFF1_DECL,          // 1
    IDX_CHAR_FFF1_VAL,           // 2
    IDX_CHAR_FFF1_CCCD,          // 3

    IDX_CHAR_FFF2_DECL,          // 4
    IDX_CHAR_FFF2_VAL,           // 5

    FFF0_IDX_NB
};

/* --- 1) Device Information Service UUIDs (0x180A) --- */
static const uint16_t GATTS_SERVICE_UUID_DEVICE_INFO = 0x180A;
static const uint16_t GATTS_CHAR_UUID_MANUFACTURER   = 0x2A29;  
static const uint16_t GATTS_CHAR_UUID_MODEL_NUMBER   = 0x2A24; 
static const uint16_t GATTS_CHAR_UUID_SERIAL_NUMBER  = 0x2A25;
static const uint16_t GATTS_CHAR_UUID_HARDWARE_REV   = 0x2A27; 
static const uint16_t GATTS_CHAR_UUID_FIRMWARE_REV   = 0x2A26; 
static const uint16_t GATTS_CHAR_UUID_SOFTWARE_REV   = 0x2A28; 
static const uint16_t GATTS_CHAR_UUID_SYSTEM_ID      = 0x2A23;  
static const uint16_t GATTS_CHAR_UUID_REG_CERT_DATA  = 0x2A2A; 

static const uint8_t manufacturer_name[]   = "MEATPI.COM";
static const uint8_t model_number[]        = "WiCAN-PRO";
static uint8_t serial_number[32]       = "";
static uint8_t hardware_rev[]        = "1_53         "; // with trailing spaces
static uint8_t firmware_rev[]        = "400";
static uint8_t software_rev[]        = "0000";

// These two are typically 8-byte binary data (not human-readable strings)
static const uint8_t system_id[8]          = {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00};
static const uint8_t reg_cert_data[8]      = {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00};


/* --- 2) “SPP-like” Service UUIDs (49535343-fe7d-4ae5-8fa9-9fafd205e455) --- */
// Service UUID
static const uint8_t SPP_SERVICE_UUID[16] = {
    /* LSB <---------------------------------> MSB */
    0x55, 0xe4, 0x05, 0xd2, 0xaf, 0x9f, 0xa9, 0x8f,
    0xe5, 0x4a, 0xe5, 0x4a, 0x7d, 0xfe, 0x43, 0x53
};
// Characteristic #1 (49535343-6daa-4d02-abf6-19569aca69fe) - read/write
static const uint8_t SPP_READ_WRITE_CHAR_UUID[16] = {
    0xfe, 0x69, 0xca, 0x9a, 0x56, 0x19, 0xf6, 0xab,
    0x02, 0x4d, 0xaa, 0x6d, 0x43, 0x53, 0x53, 0x49
};
// Characteristic #2 (49535343-aca3-481c-91ec-d85e28a60318) - write + notify
static const uint8_t SPP_WRITE_NOTIFY_CHAR_UUID[16] = {
    0x18, 0x03, 0xa6, 0x28, 0x5e, 0xd8, 0xec, 0x91,
    0x1c, 0x48, 0xa3, 0xac, 0x43, 0x53, 0x53, 0x49
};

// Default value for the first characteristic (read+write)
// "00270027000000F401" -> 9 bytes if interpreted as hex
// You can store it as raw bytes or as a string. Example as raw bytes:
static const uint8_t spp_rw_char_value[9] = {
    0x00, 0x27, 0x00, 0x27, 0x00, 0x00, 0x00, 0xF4, 0x01
};

// Typically we store CCCD as 2 bytes: {0x00, 0x00}
static const uint8_t heart_measurement_ccc[2] = {0x00, 0x00};

/* --- 3) Custom FFF0 Service (0000FFF0-0000-1000-8000-00805f9b34fb) --- */
// 16-bit service: 0xFFF0
static const uint16_t GATTS_SERVICE_UUID_FFF0 =             0xFFF0;
static const uint16_t GATTS_CHAR_UUID_FFF1 =                0xFFF1; // notify + indicate
static const uint16_t GATTS_CHAR_UUID_FFF2 =                0xFFF2; // write + write-no-response

static const uint8_t char_value_dummy[1] = {0x00};  // Just a placeholder value

static uint8_t GATTS_CHAR_UUID_CUSTOM2[16] = {
    0x9F, 0x9F, 0x00, 0xC1, 0x58, 0xBD, 0x32, 0xB6,
    0x9E, 0x4C, 0x21, 0x9C, 0xC9, 0xD6, 0xF8, 0xBE
};

static const uint16_t primary_service_uuid         = ESP_GATT_UUID_PRI_SERVICE;
static const uint16_t character_declaration_uuid   = ESP_GATT_UUID_CHAR_DECLARE;
static const uint16_t client_char_config_uuid      = ESP_GATT_UUID_CHAR_CLIENT_CONFIG;

static const uint8_t char_prop_read                = ESP_GATT_CHAR_PROP_BIT_READ;
static const uint8_t char_prop_write               = ESP_GATT_CHAR_PROP_BIT_WRITE;
static const uint8_t char_prop_notify              = ESP_GATT_CHAR_PROP_BIT_NOTIFY;
static const uint8_t char_prop_read_write          = ESP_GATT_CHAR_PROP_BIT_READ  |
                                                     ESP_GATT_CHAR_PROP_BIT_WRITE;
static const uint8_t char_prop_notify_indicate     = ESP_GATT_CHAR_PROP_BIT_NOTIFY |
                                                     ESP_GATT_CHAR_PROP_BIT_INDICATE;


static uint8_t service_uuid[16] = {
    0xFB, 0x34, 0x9B, 0x5F, 0x80, 0x00, 0x00, 0x80, 0x00, 0x10, 0x00, 0x00, 0xF0, 0xFF, 0x00, 0x00,
};

static esp_ble_adv_data_t adv_config = {
    .set_scan_rsp = false,
    .include_name = true,
    .include_txpower = true,
    .min_interval = 0x0006,
    .max_interval = 0x0010,
    .appearance = 0x00,
    .manufacturer_len = 0,
    .p_manufacturer_data = NULL,
    .service_data_len = 0,
    .p_service_data = NULL,
    .service_uuid_len = sizeof(service_uuid),
    .p_service_uuid = service_uuid,
    .flag = (ESP_BLE_ADV_FLAG_GEN_DISC | ESP_BLE_ADV_FLAG_BREDR_NOT_SPT),
};


static uint8_t adv_config_done = 0;
static uint8_t dev_name[32] = {0};
static uint8_t manufacturer[]="MeatPi";
static esp_bd_addr_t remote_bd_addr;
TaskHandle_t xble_handle = NULL;
static uint8_t conn_led = 0;
static EventGroupHandle_t s_ble_event_group = NULL;


//static uint8_t *ext_adv_raw_data;
//static uint8_t ext_adv_raw_data[64] = {
//        0x02, 0x01, 0x06,
//        0x02, 0x0a, 0xeb, 0x03, 0x03, 0xab, 0xcd,
//        0x11, 0X09,0x00
//};

//static esp_ble_gap_ext_adv_t ext_adv[1] = {
//    [0] = {EXT_ADV_HANDLE, EXT_ADV_DURATION, EXT_ADV_MAX_EVENTS},
//};

static esp_ble_adv_params_t heart_rate_adv_params = {
    .adv_int_min        = 0x100,
    .adv_int_max        = 0x100,
    .adv_type           = ADV_TYPE_IND,
    .own_addr_type      = BLE_ADDR_TYPE_RPA_PUBLIC,
    .channel_map        = ADV_CHNL_ALL,
    .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
};

static uint8_t sec_service_uuid[16] = {
    /* LSB <--------------------------------------------------------------------------------> MSB */
    //first uuid, 16bit, [12],[13] is the value
//    0xfb, 0x34, 0x9b, 0x5f, 0x80, 0x00, 0x00, 0x80, 0x00, 0x10, 0x00, 0x00, 0x18, 0x0D, 0x00, 0x00,
	0xfb, 0x34, 0x9b, 0x5f, 0x80, 0x00, 0x00, 0x80, 0x00, 0x10, 0x00, 0x00, 0xe0, 0xfe, 0x00, 0x00,
};

// config scan response data
static esp_ble_adv_data_t heart_rate_scan_rsp_config = {
    .set_scan_rsp = true,
    .include_name = true,
    .manufacturer_len = sizeof(manufacturer),
    .p_manufacturer_data = manufacturer,
};

esp_ble_gap_ext_adv_params_t ext_adv_params_2M = {
    .type = ESP_BLE_GAP_SET_EXT_ADV_PROP_CONNECTABLE,
    .interval_min = 0x20,
    .interval_max = 0x20,
    .channel_map = ADV_CHNL_ALL,
    .filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
    .primary_phy = ESP_BLE_GAP_PHY_1M,
    .max_skip = 0,
    .secondary_phy = ESP_BLE_GAP_PHY_2M,
    .sid = 0,
    .scan_req_notif = false,
    .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
};

struct gatts_profile_inst {
    esp_gatts_cb_t gatts_cb;
    uint16_t gatts_if;
    uint16_t app_id;
    uint16_t conn_id;
    uint16_t service_handle;
    esp_gatt_srvc_id_t service_id;
    uint16_t char_handle;
    esp_bt_uuid_t char_uuid;
    esp_gatt_perm_t perm;
    esp_gatt_char_prop_t property;
    uint16_t descr_handle;
    esp_bt_uuid_t descr_uuid;
};

static void gatts_profile_event_handler(esp_gatts_cb_event_t event,
                                        esp_gatt_if_t gatts_if, esp_ble_gatts_cb_param_t *param);

/* One gatt-based profile one app_id and one gatts_if, this array will store the gatts_if returned by ESP_GATTS_REG_EVT */
static struct gatts_profile_inst spp_profile_tab[SPP_PROFILE_NUM] = {
    [SPP_PROFILE_APP_IDX] = {
        .gatts_cb = gatts_profile_event_handler,
        .gatts_if = ESP_GATT_IF_NONE,       /* Not get the gatt_if, so initial is ESP_GATT_IF_NONE */
    },
};

static QueueHandle_t *xBle_TX_Queue = NULL, *xBle_RX_Queue = NULL;
static uint16_t spp_mtu_size = 23;
static uint16_t spp_conn_id = 0xffff;
static esp_gatt_if_t spp_gatts_if = 0xff;
// If the client sends a larger MTU size, the ble_max_data_size will be set to
// the minium of BLE_SEND_BUF_SIZE and (spp_mtu_size - 3).
// Since the default MTU size is 23 this is initially set to 20
static uint16_t ble_max_data_size = 20;
static bool is_connected = false;
static uint8_t test1[] = {0x66 ,0x33 ,0x22 ,0x11 ,0xBB ,0x00 ,0x00 ,0x00 ,0x11 ,0x00 ,0x00 ,0x00 ,0x33 ,0x00 ,0x00 ,0x00 ,0xA4 ,0x3C ,0xD9 ,0x49};



// Handle tables for each service
static uint16_t dev_info_profile_handle_table[DEVICE_INFO_IDX_NB];
static uint16_t spp_handle_table[SPP_IDX_NB];
static uint16_t fff0_profile_handle_table[FFF0_IDX_NB];
// static const uint8_t reg_cert_data[] = { 0x00 };

/* Full Database Description - Used to add attributes into the database */
static const esp_gatts_attr_db_t device_info_attr_db[DEVICE_INFO_IDX_NB] = {

    // 1) Service Declaration (0x2800)
    [IDX_SVC_DEVICE_INFO] = 
    {{ESP_GATT_AUTO_RSP}, 
     {ESP_UUID_LEN_16, (uint8_t *)&primary_service_uuid, ESP_GATT_PERM_READ,
      sizeof(uint16_t), sizeof(GATTS_SERVICE_UUID_DEVICE_INFO),
      (uint8_t *)&GATTS_SERVICE_UUID_DEVICE_INFO}},

    // 2) Manufacturer Name (2A29)
    [IDX_CHAR_MANUFACTURER_DECL] = 
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_read), (uint8_t *)&char_prop_read}},
    [IDX_CHAR_MANUFACTURER_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&GATTS_CHAR_UUID_MANUFACTURER, ESP_GATT_PERM_READ,
      sizeof(manufacturer_name), sizeof(manufacturer_name),
      (uint8_t *)manufacturer_name}},

    // 3) Model Number (2A24)
    [IDX_CHAR_MODEL_DECL] = 
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_read), (uint8_t *)&char_prop_read}},
    [IDX_CHAR_MODEL_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&GATTS_CHAR_UUID_MODEL_NUMBER, ESP_GATT_PERM_READ,
      sizeof(model_number), sizeof(model_number), (uint8_t *)model_number}},

    // 4) Serial Number (2A25)
    [IDX_CHAR_SERIAL_DECL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_read), (uint8_t *)&char_prop_read}},
    [IDX_CHAR_SERIAL_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&GATTS_CHAR_UUID_SERIAL_NUMBER, ESP_GATT_PERM_READ,
      sizeof(serial_number), sizeof(serial_number), (uint8_t *)serial_number}},

    // 5) Hardware Revision (2A27)
    [IDX_CHAR_HW_REV_DECL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_read), (uint8_t *)&char_prop_read}},
    [IDX_CHAR_HW_REV_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&GATTS_CHAR_UUID_HARDWARE_REV, ESP_GATT_PERM_READ,
      sizeof(hardware_rev), sizeof(hardware_rev), (uint8_t *)hardware_rev}},

    // 6) Firmware Revision (2A26)
    [IDX_CHAR_FW_REV_DECL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_read), (uint8_t *)&char_prop_read}},
    [IDX_CHAR_FW_REV_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&GATTS_CHAR_UUID_FIRMWARE_REV, ESP_GATT_PERM_READ,
      sizeof(firmware_rev), sizeof(firmware_rev), (uint8_t *)firmware_rev}},

    // 7) Software Revision (2A28)
    [IDX_CHAR_SW_REV_DECL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_read), (uint8_t *)&char_prop_read}},
    [IDX_CHAR_SW_REV_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&GATTS_CHAR_UUID_SOFTWARE_REV, ESP_GATT_PERM_READ,
      sizeof(software_rev), sizeof(software_rev), (uint8_t *)software_rev}},

    // 8) System ID (2A23)
    [IDX_CHAR_SYSTEM_ID_DECL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_read), (uint8_t *)&char_prop_read}},
    [IDX_CHAR_SYSTEM_ID_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&GATTS_CHAR_UUID_SYSTEM_ID, ESP_GATT_PERM_READ,
      sizeof(system_id), sizeof(system_id), (uint8_t *)system_id}},

    // 9) Regulatory Certification (2A2A)
    [IDX_CHAR_REG_CERT_DECL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_read), (uint8_t *)&char_prop_read}},
    [IDX_CHAR_REG_CERT_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&GATTS_CHAR_UUID_REG_CERT_DATA, ESP_GATT_PERM_READ,
      sizeof(reg_cert_data), sizeof(reg_cert_data), (uint8_t *)reg_cert_data}},
};

static const esp_gatts_attr_db_t spp_attr_db[SPP_IDX_NB] = {
    // Service Declaration
    [IDX_SVC_SPP] =
    {{ESP_GATT_AUTO_RSP}, 
     {ESP_UUID_LEN_16, (uint8_t *)&primary_service_uuid, ESP_GATT_PERM_READ,
      sizeof(SPP_SERVICE_UUID), sizeof(SPP_SERVICE_UUID), (uint8_t *)SPP_SERVICE_UUID}},

    // Characteristic #1 Declaration (read + write)
    [IDX_CHAR_SPP_RW_DECL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_read_write), (uint8_t *)&char_prop_read_write}},
    [IDX_CHAR_SPP_RW_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_128, (uint8_t *)SPP_READ_WRITE_CHAR_UUID,
      ESP_GATT_PERM_READ | ESP_GATT_PERM_WRITE,
      // Set a max length for the characteristic (e.g. 20 or more)
      20, sizeof(spp_rw_char_value), (uint8_t *)spp_rw_char_value}},

    // Characteristic #2 Declaration (write + notify)
    [IDX_CHAR_SPP_WN_DECL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_notify) + sizeof(char_prop_write), 
      // If you want both WRITE + NOTIFY, combine bits:
      (uint8_t *)&(uint8_t){ESP_GATT_CHAR_PROP_BIT_WRITE | ESP_GATT_CHAR_PROP_BIT_NOTIFY}
     }},
    [IDX_CHAR_SPP_WN_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_128, (uint8_t *)SPP_WRITE_NOTIFY_CHAR_UUID,
      ESP_GATT_PERM_WRITE,  // No read here, only write
      20, 0, NULL}},        // default length 0, no default value

    // CCCD descriptor for Characteristic #2
    [IDX_CHAR_SPP_WN_CCCD] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&client_char_config_uuid,
      ESP_GATT_PERM_READ | ESP_GATT_PERM_WRITE,
      sizeof(uint16_t), sizeof(heart_measurement_ccc),
      (uint8_t *)heart_measurement_ccc}},
};

static const esp_gatts_attr_db_t fff0_attr_db[FFF0_IDX_NB] = {
    // Service Declaration
    [IDX_SVC_FFF0] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&primary_service_uuid,
      ESP_GATT_PERM_READ,
      sizeof(uint16_t), sizeof(uint16_t),
      (uint8_t *)&GATTS_SERVICE_UUID_FFF0}},

    // FFF1 Declaration (notify + indicate)
    [IDX_CHAR_FFF1_DECL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid,
      ESP_GATT_PERM_READ,
      sizeof(uint8_t), sizeof(char_prop_notify_indicate),
      (uint8_t *)&char_prop_notify_indicate}},
    [IDX_CHAR_FFF1_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&(uint16_t){GATTS_CHAR_UUID_FFF1},
      /* No read or write perms, just notify/indicate (some apps also allow read) */
      ESP_GATT_PERM_READ,
      20, sizeof(char_value_dummy), (uint8_t *)char_value_dummy}},
    [IDX_CHAR_FFF1_CCCD] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&client_char_config_uuid,
      ESP_GATT_PERM_READ | ESP_GATT_PERM_WRITE,
      sizeof(uint16_t), sizeof(heart_measurement_ccc),
      (uint8_t *)heart_measurement_ccc}},

    // FFF2 Declaration (write + write-without-response)
    [IDX_CHAR_FFF2_DECL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid,
      ESP_GATT_PERM_READ,
      sizeof(uint8_t),
      sizeof(uint8_t),
      // Combine write + write-without-response bits
      (uint8_t *)&(uint8_t){ESP_GATT_CHAR_PROP_BIT_WRITE | ESP_GATT_CHAR_PROP_BIT_WRITE_NR}
     }},
    [IDX_CHAR_FFF2_VAL] =
    {{ESP_GATT_AUTO_RSP},
     {ESP_UUID_LEN_16, (uint8_t *)&(uint16_t){GATTS_CHAR_UUID_FFF2},
      // Allow client to write
      ESP_GATT_PERM_WRITE,
      20, 0, NULL}},
};

static char *esp_key_type_to_str(esp_ble_key_type_t key_type)
{
   char *key_str = NULL;
   switch(key_type) {
	case ESP_LE_KEY_NONE:
		key_str = "ESP_LE_KEY_NONE";
		break;
	case ESP_LE_KEY_PENC:
		key_str = "ESP_LE_KEY_PENC";
		break;
	case ESP_LE_KEY_PID:
		key_str = "ESP_LE_KEY_PID";
		break;
	case ESP_LE_KEY_PCSRK:
		key_str = "ESP_LE_KEY_PCSRK";
		break;
	case ESP_LE_KEY_PLK:
		key_str = "ESP_LE_KEY_PLK";
		break;
	case ESP_LE_KEY_LLK:
		key_str = "ESP_LE_KEY_LLK";
		break;
	case ESP_LE_KEY_LENC:
		key_str = "ESP_LE_KEY_LENC";
		break;
	case ESP_LE_KEY_LID:
		key_str = "ESP_LE_KEY_LID";
		break;
	case ESP_LE_KEY_LCSRK:
		key_str = "ESP_LE_KEY_LCSRK";
		break;
	default:
		key_str = "INVALID BLE KEY TYPE";
		break;

   }

   return key_str;
}

static char *esp_auth_req_to_str(esp_ble_auth_req_t auth_req)
{
   char *auth_str = NULL;
   switch(auth_req) {
	case ESP_LE_AUTH_NO_BOND:
		auth_str = "ESP_LE_AUTH_NO_BOND";
		break;
	case ESP_LE_AUTH_BOND:
		auth_str = "ESP_LE_AUTH_BOND";
		break;
	case ESP_LE_AUTH_REQ_MITM:
		auth_str = "ESP_LE_AUTH_REQ_MITM";
		break;
	case ESP_LE_AUTH_REQ_BOND_MITM:
		auth_str = "ESP_LE_AUTH_REQ_BOND_MITM";
		break;
	case ESP_LE_AUTH_REQ_SC_ONLY:
		auth_str = "ESP_LE_AUTH_REQ_SC_ONLY";
		break;
	case ESP_LE_AUTH_REQ_SC_BOND:
		auth_str = "ESP_LE_AUTH_REQ_SC_BOND";
		break;
	case ESP_LE_AUTH_REQ_SC_MITM:
		auth_str = "ESP_LE_AUTH_REQ_SC_MITM";
		break;
	case ESP_LE_AUTH_REQ_SC_MITM_BOND:
		auth_str = "ESP_LE_AUTH_REQ_SC_MITM_BOND";
		break;
	default:
		auth_str = "INVALID BLE AUTH REQ";
		break;
   }

   return auth_str;
}

static void show_bonded_devices(void)
{
    int dev_num = esp_ble_get_bond_device_num();

    esp_ble_bond_dev_t *dev_list = (esp_ble_bond_dev_t *)malloc(sizeof(esp_ble_bond_dev_t) * dev_num);
    esp_ble_get_bond_device_list(&dev_num, dev_list);
    ESP_LOGI(GATTS_TABLE_TAG, "Bonded devices number : %d\n", dev_num);

    ESP_LOGI(GATTS_TABLE_TAG, "Bonded devices list : %d\n", dev_num);
    for (int i = 0; i < dev_num; i++) {
        esp_log_buffer_hex(GATTS_TABLE_TAG, (void *)dev_list[i].bd_addr, sizeof(esp_bd_addr_t));
    }

    free(dev_list);
}

static void __attribute__((unused)) remove_all_bonded_devices(void)
{
    int dev_num = esp_ble_get_bond_device_num();

    esp_ble_bond_dev_t *dev_list = (esp_ble_bond_dev_t *)malloc(sizeof(esp_ble_bond_dev_t) * dev_num);
    esp_ble_get_bond_device_list(&dev_num, dev_list);
    for (int i = 0; i < dev_num; i++) {
        esp_ble_remove_bond_device(dev_list[i].bd_addr);
    }

    free(dev_list);
}

static void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param)
{
    ESP_LOGV(GATTS_TABLE_TAG, "GAP_EVT, event %d\n", event);

    switch (event) {
    case ESP_GAP_BLE_SCAN_RSP_DATA_SET_COMPLETE_EVT:
        adv_config_done &= (~SCAN_RSP_CONFIG_FLAG);
        if (adv_config_done == 0){
            esp_ble_gap_start_advertising(&heart_rate_adv_params);
        }
        break;
    case ESP_GAP_BLE_ADV_DATA_SET_COMPLETE_EVT:
        adv_config_done &= (~ADV_CONFIG_FLAG);
        if (adv_config_done == 0){
            esp_ble_gap_start_advertising(&heart_rate_adv_params);
        }
        break;
    case ESP_GAP_BLE_ADV_START_COMPLETE_EVT:
        //advertising start complete event to indicate advertising start successfully or failed
        if (param->adv_start_cmpl.status != ESP_BT_STATUS_SUCCESS) {
            ESP_LOGE(GATTS_TABLE_TAG, "advertising start failed, error status = %x", param->adv_start_cmpl.status);
            break;
        }
        ESP_LOGI(GATTS_TABLE_TAG, "advertising start success");
        break;
    case ESP_GAP_BLE_PASSKEY_REQ_EVT:                           /* passkey request event */
        ESP_LOGI(GATTS_TABLE_TAG, "ESP_GAP_BLE_PASSKEY_REQ_EVT");
        /* Call the following function to input the passkey which is displayed on the remote device */
        //esp_ble_passkey_reply(spp_profile_tab[SPP_PROFILE_APP_IDX].remote_bda, true, 0x00);
        break;
    case ESP_GAP_BLE_OOB_REQ_EVT: {
        ESP_LOGI(GATTS_TABLE_TAG, "ESP_GAP_BLE_OOB_REQ_EVT");
        uint8_t tk[16] = {1}; //If you paired with OOB, both devices need to use the same tk
        esp_ble_oob_req_reply(param->ble_security.ble_req.bd_addr, tk, sizeof(tk));
        break;
    }
    case ESP_GAP_BLE_LOCAL_IR_EVT:                               /* BLE local IR event */
        ESP_LOGI(GATTS_TABLE_TAG, "ESP_GAP_BLE_LOCAL_IR_EVT");
        break;
    case ESP_GAP_BLE_LOCAL_ER_EVT:                               /* BLE local ER event */
        ESP_LOGI(GATTS_TABLE_TAG, "ESP_GAP_BLE_LOCAL_ER_EVT");
        break;
    case ESP_GAP_BLE_NC_REQ_EVT:
        /* The app will receive this evt when the IO has DisplayYesNO capability and the peer device IO also has DisplayYesNo capability.
        show the passkey number to the user to confirm it with the number displayed by peer device. */
        esp_ble_confirm_reply(param->ble_security.ble_req.bd_addr, true);
        ESP_LOGI(GATTS_TABLE_TAG, "ESP_GAP_BLE_NC_REQ_EVT, the passkey Notify number:%" PRIu32, param->ble_security.key_notif.passkey);
        break;
    case ESP_GAP_BLE_SEC_REQ_EVT:
        /* send the positive(true) security response to the peer device to accept the security request.
        If not accept the security request, should send the security response with negative(false) accept value*/
        esp_ble_gap_security_rsp(param->ble_security.ble_req.bd_addr, true);
        break;
    case ESP_GAP_BLE_PASSKEY_NOTIF_EVT:  ///the app will receive this evt when the IO  has Output capability and the peer device IO has Input capability.
        ///show the passkey number to the user to input it in the peer device.
        ESP_LOGI(GATTS_TABLE_TAG, "The passkey Notify number:%" PRIu32, param->ble_security.key_notif.passkey);
        break;
    case ESP_GAP_BLE_KEY_EVT:
        //shows the ble key info share with peer device to the user.
        ESP_LOGI(GATTS_TABLE_TAG, "key type = %s", esp_key_type_to_str(param->ble_security.ble_key.key_type));
        break;
    case ESP_GAP_BLE_AUTH_CMPL_EVT: {
        esp_bd_addr_t bd_addr;
        memcpy(bd_addr, param->ble_security.auth_cmpl.bd_addr, sizeof(esp_bd_addr_t));
        ESP_LOGI(GATTS_TABLE_TAG, "remote BD_ADDR: %08x%04x",\
                (bd_addr[0] << 24) + (bd_addr[1] << 16) + (bd_addr[2] << 8) + bd_addr[3],
                (bd_addr[4] << 8) + bd_addr[5]);
        ESP_LOGI(GATTS_TABLE_TAG, "address type = %d", param->ble_security.auth_cmpl.addr_type);
        ESP_LOGI(GATTS_TABLE_TAG, "pair status = %s",param->ble_security.auth_cmpl.success ? "success" : "fail");
        if(!param->ble_security.auth_cmpl.success) {
            ESP_LOGI(GATTS_TABLE_TAG, "fail reason = 0x%x",param->ble_security.auth_cmpl.fail_reason);
        } else {
            ESP_LOGI(GATTS_TABLE_TAG, "auth mode = %s",esp_auth_req_to_str(param->ble_security.auth_cmpl.auth_mode));
        }
        show_bonded_devices();
        break;
    }
    case ESP_GAP_BLE_REMOVE_BOND_DEV_COMPLETE_EVT: {
        ESP_LOGD(GATTS_TABLE_TAG, "ESP_GAP_BLE_REMOVE_BOND_DEV_COMPLETE_EVT status = %d", param->remove_bond_dev_cmpl.status);
        ESP_LOGI(GATTS_TABLE_TAG, "ESP_GAP_BLE_REMOVE_BOND_DEV");
        ESP_LOGI(GATTS_TABLE_TAG, "-----ESP_GAP_BLE_REMOVE_BOND_DEV----");
        esp_log_buffer_hex(GATTS_TABLE_TAG, (void *)param->remove_bond_dev_cmpl.bd_addr, sizeof(esp_bd_addr_t));
        ESP_LOGI(GATTS_TABLE_TAG, "------------------------------------");
        break;
    }
    case ESP_GAP_BLE_SET_LOCAL_PRIVACY_COMPLETE_EVT:
        if (param->local_privacy_cmpl.status != ESP_BT_STATUS_SUCCESS){
            ESP_LOGE(GATTS_TABLE_TAG, "config local privacy failed, error status = %x", param->local_privacy_cmpl.status);
            break;
        }

        esp_err_t ret = esp_ble_gap_config_adv_data(&adv_config);
        if (ret){
            ESP_LOGE(GATTS_TABLE_TAG, "config adv data failed, error code = %x", ret);
        }else{
            adv_config_done |= ADV_CONFIG_FLAG;
        }

        ret = esp_ble_gap_config_adv_data(&heart_rate_scan_rsp_config);
        if (ret){
            ESP_LOGE(GATTS_TABLE_TAG, "config adv data failed, error code = %x", ret);
        }else{
            adv_config_done |= SCAN_RSP_CONFIG_FLAG;
        }

        break;
    default:
        break;
    }
}

static void gatts_profile_event_handler(esp_gatts_cb_event_t event,
                                        esp_gatt_if_t gatts_if, esp_ble_gatts_cb_param_t *param)
{
	esp_gatt_rsp_t rsp;
	static xdev_buffer rx_buffer;
    ESP_LOGV(GATTS_TABLE_TAG, "event = %x\n",event);
    switch (event) {
        case ESP_GATTS_REG_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_REG_EVT");
            esp_ble_gap_set_device_name((const char*)dev_name);
            esp_ble_gap_config_local_icon (ESP_BLE_APPEARANCE_GENERIC_COMPUTER);
            //generate a resolvable random address
            esp_ble_gap_config_local_privacy(true);
            esp_ble_gatts_create_attr_tab(device_info_attr_db, gatts_if,
                                    DEVICE_INFO_IDX_NB, DEV_INFO_SVC_INST_ID);
            break;
        case ESP_GATTS_READ_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_READ_EVT");
            // if(profile_handle_table[IDX_CHAR_VAL_C] == param->read.handle)
            // {
            // 	memset(&rsp, 0, sizeof(esp_gatt_rsp_t));
            // 	rsp.attr_value.handle = param->read.handle;
            // 	rsp.attr_value.len = 1;
            // 	rsp.attr_value.value[0] = config_server_get_ble_config();
			// 	esp_ble_gatts_send_response(gatts_if, param->read.conn_id, param->read.trans_id,
			// 			param->reg.status, &rsp);

            // }
            break;
        case ESP_GATTS_WRITE_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_WRITE_EVT, write value:");
            esp_log_buffer_hex(GATTS_TABLE_TAG, param->write.value, param->write.len);

            // if(custom2_profile_handle_table[IDX_CHAR_VAL_CUSTOM2] == param->write.handle)
            {
				memcpy(rx_buffer.ucElement, param->write.value, param->write.len);
				rx_buffer.dev_channel = DEV_BLE;
				rx_buffer.usLen = param->write.len;
				xQueueSend(*xBle_RX_Queue, ( void * ) &rx_buffer, portMAX_DELAY );
                ESP_LOGI(GATTS_TABLE_TAG, "writing value:");
                esp_log_buffer_hex(GATTS_TABLE_TAG, param->write.value, param->write.len);
            }
            // else if(profile_handle_table[IDX_CHAR_VAL_C] == param->write.handle)
            // {
            // 	if(param->write.len == 1 && (param->write.value[0] == 0 || param->write.value[0] == 1))
            // 	{
            // 		config_server_set_ble_config(param->write.value[0]);
            // 	}
            // }
            break;
        case ESP_GATTS_EXEC_WRITE_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_EXEC_WRITE_EVT");
            break;
        case ESP_GATTS_MTU_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_MTU_EVT");
            // NOTE: The ESP32 documentation doesn't explain how MTU negotiation works.
            // From the ESP SPP server demo, the characteristic is declared with a size of 512
            // and then this event determines the actual MTU size.
			// From experimentation the esp_ble_gatt_set_local_mtu function is related to this.
			// It seems the value set by esp_ble_gatt_set_local_mtu is sent to the client during
			// the connection. Then if the client supports it, it will send this ESP_GATTS_MTU_EVT
			// with the MTU value the client can support.
            //
            // As noted in above the call to esp_ble_gatt_set_local_mtu is not sent by
            // Car Scanner ELM OBD2 on Android. So it uses the default MTU of 23.
            spp_mtu_size = param->mtu.mtu;
            // Each BLE packet has 3 header bytes, so the actual amount of data that
            // can be sent is (MTU - 3).
            //
            // set the max data size to the minimum of (spp_mtu_size - 3) and BLE_SEND_BUF_SIZE
            ble_max_data_size = BLE_SEND_BUF_SIZE <= (spp_mtu_size - 3) ? BLE_SEND_BUF_SIZE : (spp_mtu_size -3);
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_MTU_EVT: %d", spp_mtu_size);
            break;
        case ESP_GATTS_CONF_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_CONF_EVT, status = %d, attr_handle %d", param->conf.status, param->conf.handle);
            ESP_LOG_BUFFER_HEX(GATTS_TABLE_TAG, param->conf.value, param->conf.len);
            break;
        case ESP_GATTS_UNREG_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_UNREG_EVT");
            break;
        case ESP_GATTS_DELETE_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_DELETE_EVT");
            break;
        case ESP_GATTS_START_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_START_EVT");
            break;
        case ESP_GATTS_STOP_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_STOP_EVT");
            break;
        case ESP_GATTS_CONNECT_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_CONNECT_EVT");
        	config_server_stop();
        	wifi_network_deinit();
            esp_ble_conn_update_params_t conn_params = {0};
            memcpy(conn_params.bda, param->connect.remote_bda, sizeof(esp_bd_addr_t));
            /* For the iOS system, please refer to Apple official documents about the BLE connection parameters restrictions. */
            conn_params.latency = 0;
            conn_params.max_int = 0x20;    // max_int = 0x20*1.25ms = 40ms
            conn_params.min_int = 0x10;    // min_int = 0x10*1.25ms = 20ms
            conn_params.timeout = 400;    // timeout = 400*10ms = 4000ms
            //start sent the update connection parameters to the peer device.
            esp_ble_gap_update_conn_params(&conn_params);
    	    spp_conn_id = param->connect.conn_id;
    	    spp_gatts_if = gatts_if;
    	    is_connected = true;
    	    xEventGroupSetBits(s_ble_event_group, BLE_CONNECTED_BIT);
			#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
    	    gpio_set_level(conn_led, 0);
			#endif
            /* start security connect with peer device when receive the connect event sent by the master */
            esp_ble_set_encryption(param->connect.remote_bda, ESP_BLE_SEC_ENCRYPT_MITM);

            // const char *str = "\r\rELM327 v2.3\r\r>";
            // esp_ble_gatts_send_indicate(spp_gatts_if, param->write.conn_id, custom2_profile_handle_table[IDX_CHAR_VAL_CUSTOM2],strlen(str), (uint8_t *)str, false);
            // esp_ble_gap_set_prefer_conn_params(remote_bd_addr, 
            //                                 0x0010,   // min_conn_int
            //                                 0x0020,   // max_conn_int
            //                                 0,        // conn_latency
            //                                 600);     // supervision_timeout
                break;
        case ESP_GATTS_DISCONNECT_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_DISCONNECT_EVT, disconnect reason 0x%x", param->disconnect.reason);
//            wifi_network_restart();
//        	config_server_restart();
            is_connected = false;
			#if HARDWARE_VER == WICAN_V300 || HARDWARE_VER == WICAN_USB_V100
            gpio_set_level(conn_led, 1);
			#endif
            /* start advertising again when missing the connect */
            esp_ble_gap_start_advertising(&heart_rate_adv_params);
            break;
        case ESP_GATTS_OPEN_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_OPEN_EVT");
            break;
        case ESP_GATTS_CANCEL_OPEN_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_CANCEL_OPEN_EVT");
            break;
        case ESP_GATTS_CLOSE_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_CLOSE_EVT");
            break;
        case ESP_GATTS_LISTEN_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_LISTEN_EVT");
            break;
        case ESP_GATTS_CONGEST_EVT:
            ESP_LOGI(GATTS_TABLE_TAG, "ESP_GATTS_CONGEST_EVT");
            if (param->congest.congested)
            {
//                can_send_notify = false;
            	xEventGroupSetBits(s_ble_event_group, BLE_CONGEST_BIT);
            }
            else
            {
            	xEventGroupClearBits(s_ble_event_group, BLE_CONGEST_BIT);
//                can_send_notify = true;
//                xSemaphoreGive(gatts_semaphore);
            }
            break;
        case ESP_GATTS_CREAT_ATTR_TAB_EVT: 
            ESP_LOGI(GATTS_TABLE_TAG, "The number handle, = %x, svc_inst_id = %u",param->add_attr_tab.num_handle, param->add_attr_tab.svc_inst_id);
            if (param->create.status == ESP_GATT_OK)
            {
                if (param->add_attr_tab.svc_inst_id == DEV_INFO_SVC_INST_ID)
                {
                    if(param->add_attr_tab.num_handle == DEVICE_INFO_IDX_NB)
                    {
                        memcpy(dev_info_profile_handle_table, param->add_attr_tab.handles,
                                    sizeof(dev_info_profile_handle_table));
                        esp_ble_gatts_start_service(dev_info_profile_handle_table[IDX_SVC_DEVICE_INFO]);
                        esp_ble_gatts_create_attr_tab(spp_attr_db, gatts_if,
                                    SPP_IDX_NB, SSP_SVC_INST_ID);
                    }
                    else
                    {
                        ESP_LOGE(GATTS_TABLE_TAG, "Create attribute table abnormally, num_handle (%d) doesn't equal to HRS_IDX_NB(%d)",
                         param->add_attr_tab.num_handle, DEVICE_INFO_IDX_NB);
                    }
                }
                else if (param->add_attr_tab.svc_inst_id == SSP_SVC_INST_ID)
                {
                    if(param->add_attr_tab.num_handle == SPP_IDX_NB)
                    {
                        // Handle second service creation
                        memcpy(spp_handle_table, param->add_attr_tab.handles,
                            sizeof(spp_handle_table));
                        esp_ble_gatts_start_service(spp_handle_table[IDX_SVC_SPP]);
                        esp_ble_gatts_create_attr_tab(fff0_attr_db, gatts_if,
                                                    FFF0_IDX_NB, FFF0_SVC_INST_ID);
                    }
                    else
                    {
                        ESP_LOGE(GATTS_TABLE_TAG, "Create attribute table abnormally, num_handle (%d) doesn't equal to HRS_IDX_NB(%d)",
                         param->add_attr_tab.num_handle, SPP_IDX_NB);
                    }
                }
                else if (param->add_attr_tab.svc_inst_id == FFF0_SVC_INST_ID) 
                {
                    if(param->add_attr_tab.num_handle == FFF0_IDX_NB)
                    {
                        // Handle third service creation
                        memcpy(fff0_profile_handle_table, param->add_attr_tab.handles,
                            sizeof(fff0_profile_handle_table));
                        esp_ble_gatts_start_service(fff0_profile_handle_table[IDX_SVC_FFF0]);
                    }
                    else
                    {
                        ESP_LOGE(GATTS_TABLE_TAG, "Create attribute table abnormally, num_handle (%d) doesn't equal to HRS_IDX_NB(%d)",
                         param->add_attr_tab.num_handle, FFF0_IDX_NB);
                    }
                }
            }
            else
            {
                ESP_LOGE(GATTS_TABLE_TAG, " Create attribute table failed, error code = %x", param->create.status);
            }
        break;
    

        default:
           break;
    }
}

static void gatts_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if,
                                esp_ble_gatts_cb_param_t *param)
{
    /* If event is register event, store the gatts_if for each profile */
    if (event == ESP_GATTS_REG_EVT) {
        if (param->reg.status == ESP_GATT_OK) {
            spp_profile_tab[SPP_PROFILE_APP_IDX].gatts_if = gatts_if;
        } else {
            ESP_LOGI(GATTS_TABLE_TAG, "Reg app failed, app_id %04x, status %d\n",
                    param->reg.app_id,
                    param->reg.status);
            return;
        }
    }

    do {
        int idx;
        for (idx = 0; idx < SPP_PROFILE_NUM; idx++) {
            if (gatts_if == ESP_GATT_IF_NONE || /* ESP_GATT_IF_NONE, not specify a certain gatt_if, need to call every profile cb function */
                    gatts_if == spp_profile_tab[idx].gatts_if) {
                if (spp_profile_tab[idx].gatts_cb) {
                    spp_profile_tab[idx].gatts_cb(event, gatts_if, param);
                }
            }
        }
    } while (0);
}

static void ble_task(void *pvParameters)
{
	static xdev_buffer tx_buffer;
	static uint8_t ble_send_buf[BLE_SEND_BUF_SIZE];
	static uint32_t ble_send_buf_len = 0;
	static uint32_t num_msg = 0;
	static int64_t time_old = 0;
//	static int64_t send_time = 0;
	while(1)
	{
		//		ESP_LOGI(GATTS_TABLE_TAG, "wait BLE_CONNECTED_BIT");
				xEventGroupWaitBits(s_ble_event_group,
									BLE_CONNECTED_BIT,
									pdFALSE,
									pdFALSE,
									portMAX_DELAY);
		//		ESP_LOGI(GATTS_TABLE_TAG, "BLE_CONNECTED_BIT");

				xQueuePeek(*xBle_TX_Queue, ( void * ) &tx_buffer, portMAX_DELAY);
		//		memcpy(ble_send_buf, tx_buffer.ucElement, tx_buffer.usLen);
		//		ble_send_buf_len = tx_buffer.usLen;
				xEventGroupWaitBits(s_ble_event_group,
									BLE_CONNECTED_BIT,
									pdFALSE,
									pdFALSE,
									portMAX_DELAY);



				while(!ble_tx_ready())
				{
					vTaskDelay(pdMS_TO_TICKS(1));
				}
				int free_packet = esp_ble_get_cur_sendable_packets_num(spp_conn_id);
//				int free_packet = esp_ble_get_sendable_packets_num();

				if(free_packet && !(BLE_CONGEST_BIT & xEventGroupGetBits(s_ble_event_group)))
				{
					while((xQueuePeek(*xBle_TX_Queue, ( void * ) &tx_buffer, 0) == pdTRUE))
					{
						// figure out how many packets are needed to send this tx_buffer
						int num_req_packets = ((ble_send_buf_len + tx_buffer.usLen) / ble_max_data_size);
						// Round up. Only part of a packet might be needed and integer math rounds down.
						if((ble_send_buf_len + tx_buffer.usLen) % ble_max_data_size) {
							num_req_packets++;
						}

						if(free_packet < num_req_packets)
						{
							// We don't have enough free_packets to send this item
							break;
						}

						xQueueReceive(*xBle_TX_Queue, ( void * ) &tx_buffer, 0);
						num_msg++;
						if(esp_timer_get_time() - time_old > 1000*1000)
						{
							time_old = esp_timer_get_time();

		//					ESP_LOGI(GATTS_TABLE_TAG, "msg %u/sec", num_msg);
							num_msg = 0;
						}
						int tx_buffer_copied = 0;
						while(tx_buffer_copied < tx_buffer.usLen)
						{
							int ble_send_buf_remaining = ble_max_data_size - ble_send_buf_len;
							int tx_buffer_remaining = tx_buffer.usLen - tx_buffer_copied;
							// only copy bytes that will fit in the ble_send_buf
							int copy_len = tx_buffer_remaining >= ble_send_buf_remaining ? ble_send_buf_remaining : tx_buffer_remaining;
							memcpy(ble_send_buf+ble_send_buf_len, tx_buffer.ucElement+tx_buffer_copied, copy_len);
							ble_send_buf_len += copy_len;
							tx_buffer_copied += copy_len;

							// If we have a full ble_send_buf, send it. Otherwise wait to fill it with more bytes,
							// If there are no more bytes to fill it with, then it will be sent at the end.
							if(ble_send_buf_len == ble_max_data_size)
							{
			//					xEventGroupWaitBits(s_ble_event_group,
			//										BLE_CONNECTED_BIT,
			//										pdFALSE,
			//										pdFALSE,
			//										portMAX_DELAY);
			//					ESP_LOG_BUFFER_HEXDUMP(GATTS_TABLE_TAG, ble_send_buf, ble_send_buf_len, ESP_LOG_INFO);
			//					vTaskDelay(pdMS_TO_TICKS(30000));
								ble_send(ble_send_buf, ble_send_buf_len);
								ble_send_buf_len = 0;
								if(--free_packet == 0 && tx_buffer_remaining > 0)
								{
									// We did a computation above to make sure we had a enough
									// free_packets. If we are down to zero and there are still
									// bytes to send, then something went wrong
									ESP_LOGE(GATTS_TABLE_TAG, "Ran out of free_packets too soon");
									break;
								}
							}
						}
					}
					if(free_packet != 0 && ble_send_buf_len != 0)
					{
			//			ESP_LOG_BUFFER_HEXDUMP(GATTS_TABLE_TAG, ble_send_buf, ble_send_buf_len, ESP_LOG_INFO);
						ble_send(ble_send_buf, ble_send_buf_len);
						ble_send_buf_len = 0;
					}
				}

		//		ESP_LOGI(GATTS_TABLE_TAG, "esp_ble_get_cur_sendable_packets_num 1: %d", esp_ble_get_cur_sendable_packets_num(spp_conn_id));
		//		while(!ble_tx_ready())
		//		{
		//			vTaskDelay(pdMS_TO_TICKS(1));
		//		}
		//		xEventGroupWaitBits(s_ble_event_group,
		//							BLE_CONNECTED_BIT,
		//							pdFALSE,
		//							pdFALSE,
		//							portMAX_DELAY);
		//		ble_send(ble_send_buf, ble_send_buf_len);
		//		ESP_LOGI(GATTS_TABLE_TAG, "esp_ble_get_cur_sendable_packets_num 2: %d", esp_ble_get_cur_sendable_packets_num(spp_conn_id));
	}
}

bool ble_connected(void)
{
	EventBits_t uxBits;
	if(s_ble_event_group != NULL)
	{
		uxBits = xEventGroupGetBits(s_ble_event_group);

		return (uxBits & BLE_CONNECTED_BIT)?1:0;
	}
	else return 0;
}

bool ble_tx_ready(void)
{
	if(ble_connected())
	{
		if(esp_ble_get_cur_sendable_packets_num(spp_conn_id) > 0)
		{
			return true;
		}
		else return false;
	}
	return false;
}
void ble_send(uint8_t* buf, uint8_t buf_len)
{
	if(ble_tx_ready())
	{
		esp_ble_gatts_send_indicate(spp_gatts_if, spp_conn_id, fff0_profile_handle_table[IDX_CHAR_FFF1_VAL],buf_len, buf, false);
		// The ESP SPP server demo adds a 20ms delay after each send.
		// It doesn't seem like it is needed in the WiCAN case.
		// vTaskDelay(20 / portTICK_PERIOD_MS);
	}
}
static uint32_t ble_pass_key = 0;
void ble_init(QueueHandle_t *xTXp_Queue, QueueHandle_t *xRXp_Queue, uint8_t connected_led, int passkey, uint8_t* uid)
{
	esp_err_t ret;

	if(conn_led == 0 && dev_name[0] == 0)
	{
		strcpy((char*)dev_name, (char*)uid);
		conn_led = connected_led;
		ble_pass_key = passkey;
		ESP_LOGW(GATTS_TABLE_TAG, "ble passkey: %lu", ble_pass_key);
	}
    memset(serial_number, 0, sizeof(serial_number));
    memcpy(serial_number, dev_name+7, strlen((char*)dev_name)-7);

	if(xBle_TX_Queue == NULL)
	{
		xBle_TX_Queue = xTXp_Queue;
	}
	if(xBle_RX_Queue == NULL)
	{
		xBle_RX_Queue = xRXp_Queue;
	}

	if(s_ble_event_group == NULL)
	{
		s_ble_event_group = xEventGroupCreate();
	}

	xEventGroupClearBits(s_ble_event_group, BLE_CONNECTED_BIT);
	xEventGroupClearBits(s_ble_event_group, BLE_CONGEST_BIT);
//	ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_BLE));

	esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    bt_cfg.controller_task_stack_size = (1024*8);
	ret = esp_bt_controller_init(&bt_cfg);
	if (ret) {
		ESP_LOGE(GATTS_TABLE_TAG, "%s init controller failed: %s", __func__, esp_err_to_name(ret));
		return;
	}
	ret = esp_bt_controller_enable(ESP_BT_MODE_BLE);
	if (ret) {
		ESP_LOGE(GATTS_TABLE_TAG, "%s enable controller failed: %s", __func__, esp_err_to_name(ret));
		return;
	}

	ESP_LOGI(GATTS_TABLE_TAG, "%s init bluetooth", __func__);
	ret = esp_bluedroid_init();
	if (ret) {
		ESP_LOGE(GATTS_TABLE_TAG, "%s init bluetooth failed: %s", __func__, esp_err_to_name(ret));
		return;
	}
	ret = esp_bluedroid_enable();
	if (ret) {
		ESP_LOGE(GATTS_TABLE_TAG, "%s enable bluetooth failed: %s", __func__, esp_err_to_name(ret));
		return;
	}

	ret = esp_ble_gatts_register_callback(gatts_event_handler);
	if (ret){
		ESP_LOGE(GATTS_TABLE_TAG, "gatts register error, error code = %x", ret);
		return;
	}
	ret = esp_ble_gap_register_callback(gap_event_handler);
	if (ret){
		ESP_LOGE(GATTS_TABLE_TAG, "gap register error, error code = %x", ret);
		return;
	}
	ret = esp_ble_gatts_app_register(ESP_SPP_APP_ID);
	if (ret){
		ESP_LOGE(GATTS_TABLE_TAG, "gatts app register error, error code = %x", ret);
		return;
	}

    // The documentation on how this works is not clear. It is used in the ESP
	// SPP server demo. From experimentation, the value set here is sometimes
	// sent back to us via the ESP_GATTS_MTU_EVT. Only when it is sent back,
	// does it mean the MTU has been increased from the default of 23. From
	// looking at the ESP code its max value is 517.
	//
	// Tested configurations:
	// - Realdash on Android: this value is sent back
	// - ble-serial on MacOS: this value is sent back
	// - Carscanner on Android: this value is not sent back. And based on
	//   experimentation the message length is capped at 20 bytes which is
	//   consistent with the default MTU setting of 23.
    esp_err_t local_mtu_ret = esp_ble_gatt_set_local_mtu(517);
    if (local_mtu_ret){
        ESP_LOGE(GATTS_TABLE_TAG, "set local  MTU failed, error code = %x", local_mtu_ret);
    }
	/* set the security iocap & auth_req & key size & init key response key parameters to the stack*/
	esp_ble_auth_req_t auth_req = ESP_LE_AUTH_REQ_SC_MITM_BOND;     //bonding with peer device after authentication
	esp_ble_io_cap_t iocap = ESP_IO_CAP_OUT;           //set the IO capability to No output No input
	uint8_t key_size = 16;      //the key size should be 7~16 bytes
	uint8_t init_key = ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK;
	uint8_t rsp_key = ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK;
	//set static passkey
//	uint32_t passkey = 123456;
	uint8_t auth_option = ESP_BLE_ONLY_ACCEPT_SPECIFIED_AUTH_DISABLE;
	uint8_t oob_support = ESP_BLE_OOB_DISABLE;

	esp_ble_gap_set_security_param(ESP_BLE_SM_CLEAR_STATIC_PASSKEY, &ble_pass_key, sizeof(uint32_t));
	esp_ble_gap_set_security_param(ESP_BLE_SM_SET_STATIC_PASSKEY, &ble_pass_key, sizeof(uint32_t));
	esp_ble_gap_set_security_param(ESP_BLE_SM_AUTHEN_REQ_MODE, &auth_req, sizeof(uint8_t));
	esp_ble_gap_set_security_param(ESP_BLE_SM_IOCAP_MODE, &iocap, sizeof(uint8_t));
	esp_ble_gap_set_security_param(ESP_BLE_SM_MAX_KEY_SIZE, &key_size, sizeof(uint8_t));
	esp_ble_gap_set_security_param(ESP_BLE_SM_ONLY_ACCEPT_SPECIFIED_SEC_AUTH, &auth_option, sizeof(uint8_t));
	esp_ble_gap_set_security_param(ESP_BLE_SM_OOB_SUPPORT, &oob_support, sizeof(uint8_t));
	/* If your BLE device acts as a Slave, the init_key means you hope which types of key of the master should distribute to you,
	and the response key means which key you can distribute to the master;
	If your BLE device acts as a master, the response key means you hope which types of key of the slave should distribute to you,
	and the init key means which key you can distribute to the slave. */
	esp_ble_gap_set_security_param(ESP_BLE_SM_SET_INIT_KEY, &init_key, sizeof(uint8_t));
	esp_ble_gap_set_security_param(ESP_BLE_SM_SET_RSP_KEY, &rsp_key, sizeof(uint8_t));

	if(xble_handle == NULL)
	{
		xTaskCreate(ble_task, "ble_task", 4096, (void*)AF_INET, 5, &xble_handle);
	}

//    esp_log_level_set(GATTS_TABLE_TAG, ESP_LOG_NONE);
}

void ble_disable(void)
{
	esp_bluedroid_disable();
	esp_bluedroid_deinit();
	esp_bt_controller_disable();
	esp_bt_controller_deinit();
}
void ble_enable(void)
{
	ble_init(0,0,0,0,0);
//	esp_bluedroid_enable();
}
