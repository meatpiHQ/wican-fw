#include "dev_status.h"

static const char *DEV_STATUS_TAG = "DEV_STATUS";
static EventGroupHandle_t s_dev_status_event_group = NULL;

// Helper function to get bit name for logging
static const char* get_bit_name(EventBits_t bit)
{
    switch (bit) {
        case DEV_AWAKE_BIT:       return "DEV_AWAKE";
        case DEV_SLEEP_BIT:       return "DEV_SLEEP";
        case DEV_WIFI_CONNECTED_BIT:  return "WIFI_CONNECTED";
        case DEV_MQTT_CONNECTED_BIT:  return "MQTT_CONNECTED";
        case DEV_BLE_CONNECTED_BIT:   return "BLE_CONNECTED";
        default:                  return "UNKNOWN";
    }
}

// Helper function to log multiple bits
static void log_bits(const char* action, EventBits_t bits)
{
    if (bits & DEV_AWAKE_BIT)       ESP_LOGI(DEV_STATUS_TAG, "%s: DEV_AWAKE", action);
    if (bits & DEV_SLEEP_BIT)       ESP_LOGI(DEV_STATUS_TAG, "%s: DEV_SLEEP", action);
    if (bits & DEV_WIFI_CONNECTED_BIT)  ESP_LOGI(DEV_STATUS_TAG, "%s: WIFI_CONNECTED", action);
    if (bits & DEV_MQTT_CONNECTED_BIT)  ESP_LOGI(DEV_STATUS_TAG, "%s: MQTT_CONNECTED", action);
    if (bits & DEV_BLE_CONNECTED_BIT)   ESP_LOGI(DEV_STATUS_TAG, "%s: BLE_CONNECTED", action);
}

void dev_status_init(void)
{
    if (s_dev_status_event_group == NULL) {
        s_dev_status_event_group = xEventGroupCreate();
        if (s_dev_status_event_group == NULL) {
            ESP_LOGE(DEV_STATUS_TAG, "Failed to create device status event group");
        } else {
            ESP_LOGI(DEV_STATUS_TAG, "Device status event group initialized");
        }
    }
}

void dev_status_set_bits(EventBits_t bits_to_set)
{
    if (s_dev_status_event_group != NULL) {
        xEventGroupSetBits(s_dev_status_event_group, bits_to_set);
        log_bits("SET", bits_to_set);
    }
}

void dev_status_clear_bits(EventBits_t bits_to_clear)
{
    if (s_dev_status_event_group != NULL) {
        xEventGroupClearBits(s_dev_status_event_group, bits_to_clear);
        log_bits("CLEAR", bits_to_clear);
    }
}

EventBits_t dev_status_wait_for_bits(EventBits_t bits_to_wait_for, TickType_t timeout)
{
    if (s_dev_status_event_group != NULL) {
        return xEventGroupWaitBits(s_dev_status_event_group, 
                                  bits_to_wait_for, 
                                  pdFALSE,  // Don't clear bits on exit
                                  pdTRUE,   // Wait for ALL bits
                                  timeout);
    }
    return 0;
}

EventBits_t dev_status_wait_for_any_bits(EventBits_t bits_to_wait_for, TickType_t timeout)
{
    if (s_dev_status_event_group != NULL) {
        return xEventGroupWaitBits(s_dev_status_event_group, 
                                  bits_to_wait_for, 
                                  pdFALSE,  // Don't clear bits on exit
                                  pdFALSE,  // Wait for ANY bits
                                  timeout);
    }
    return 0;
}

EventBits_t dev_status_get_bits(void)
{
    if (s_dev_status_event_group != NULL) {
        return xEventGroupGetBits(s_dev_status_event_group);
    }
    return 0;
}

bool dev_status_is_bit_set(EventBits_t bit)
{
    return (dev_status_get_bits() & bit) != 0;
}

bool dev_status_are_bits_set(EventBits_t bits)
{
    EventBits_t current_bits = dev_status_get_bits();
    return (current_bits & bits) == bits;  // ALL specified bits must be set
}

bool dev_status_is_any_bit_set(EventBits_t bits)
{
    return (dev_status_get_bits() & bits) != 0;  // ANY of the specified bits is set
}