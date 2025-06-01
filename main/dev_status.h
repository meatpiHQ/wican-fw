#ifndef DEV_STATUS_H
#define DEV_STATUS_H

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// Device status event bits
#define DEV_AWAKE_BIT       BIT0
#define DEV_SLEEP_BIT       BIT1
#define WIFI_CONNECTED_BIT  BIT2
#define MQTT_CONNECTED_BIT  BIT3
#define BLE_CONNECTED_BIT   BIT4
#define SDCARD_MOUNTED_BIT  BIT5 

// Initialize device status event group
void dev_status_init(void);

// Generic set/clear functions
void dev_status_set_bits(EventBits_t bits_to_set);
void dev_status_clear_bits(EventBits_t bits_to_clear);

// Wait for specific bits to be set
EventBits_t dev_status_wait_for_bits(EventBits_t bits_to_wait_for, TickType_t timeout);
EventBits_t dev_status_wait_for_any_bits(EventBits_t bits_to_wait_for, TickType_t timeout);

// Get current status bits
EventBits_t dev_status_get_bits(void);

// Check if specific bits are set
bool dev_status_is_bit_set(EventBits_t bit);
bool dev_status_are_bits_set(EventBits_t bits);
bool dev_status_is_any_bit_set(EventBits_t bits);

// Macros for common operations
#define dev_status_set_awake()          dev_status_set_bits(DEV_AWAKE_BIT); dev_status_clear_bits(DEV_SLEEP_BIT)
#define dev_status_set_sleep()          dev_status_set_bits(DEV_SLEEP_BIT); dev_status_clear_bits(DEV_AWAKE_BIT)
#define dev_status_set_wifi_connected() dev_status_set_bits(WIFI_CONNECTED_BIT)
#define dev_status_set_mqtt_connected() dev_status_set_bits(MQTT_CONNECTED_BIT)
#define dev_status_set_ble_connected()  dev_status_set_bits(BLE_CONNECTED_BIT)

#define dev_status_clear_awake()          dev_status_clear_bits(DEV_AWAKE_BIT)
#define dev_status_clear_sleep()          dev_status_clear_bits(DEV_SLEEP_BIT)
#define dev_status_clear_wifi_connected() dev_status_clear_bits(WIFI_CONNECTED_BIT)
#define dev_status_clear_mqtt_connected() dev_status_clear_bits(MQTT_CONNECTED_BIT)
#define dev_status_clear_ble_connected()  dev_status_clear_bits(BLE_CONNECTED_BIT)

#define dev_status_is_awake()        dev_status_is_bit_set(DEV_AWAKE_BIT)
#define dev_status_is_sleeping()     dev_status_is_bit_set(DEV_SLEEP_BIT)
#define dev_status_is_wifi_connected() dev_status_is_bit_set(WIFI_CONNECTED_BIT)
#define dev_status_is_mqtt_connected() dev_status_is_bit_set(MQTT_CONNECTED_BIT)
#define dev_status_is_ble_connected()  dev_status_is_bit_set(BLE_CONNECTED_BIT)

#ifdef __cplusplus
}
#endif

#endif // DEV_STATUS_H
