#include "connection_manager.h"

#include <string.h>

#include "sdkconfig.h"

#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "dev_status.h"

#define CONNECTION_MANAGER_IFKEY_LEN 16

typedef struct connection_manager_runtime
{
    bool initialized;
    char wifi_sta_ifkey[CONNECTION_MANAGER_IFKEY_LEN];
    char usb_eth_ifkey[CONNECTION_MANAGER_IFKEY_LEN];
    SemaphoreHandle_t lock;
    StaticSemaphore_t lock_buffer;
    esp_event_handler_instance_t sta_got_ip_inst;
    esp_event_handler_instance_t sta_disconnected_inst;
    esp_event_handler_instance_t sta_stop_inst;
    esp_event_handler_instance_t eth_got_ip_inst;
    esp_event_handler_instance_t eth_lost_ip_inst;
    connection_manager_status_t status;
} connection_manager_runtime_t;

static const char *TAG = "conn_mgr";

static connection_manager_runtime_t s_connection_mgr = { 0 };

static const char *connection_manager_event_to_str(esp_event_base_t event_base, int32_t event_id)
{
    if (event_base == IP_EVENT)
    {
        switch (event_id)
        {
            case IP_EVENT_STA_GOT_IP:
                return "IP_EVENT_STA_GOT_IP";

            case IP_EVENT_ETH_GOT_IP:
                return "IP_EVENT_ETH_GOT_IP";

            case IP_EVENT_ETH_LOST_IP:
                return "IP_EVENT_ETH_LOST_IP";

            default:
                return "IP_EVENT_UNKNOWN";
        }
    }

    if (event_base == WIFI_EVENT)
    {
        switch (event_id)
        {
            case WIFI_EVENT_STA_DISCONNECTED:
                return "WIFI_EVENT_STA_DISCONNECTED";

            case WIFI_EVENT_STA_STOP:
                return "WIFI_EVENT_STA_STOP";

            default:
                return "WIFI_EVENT_UNKNOWN";
        }
    }

    return "EVENT_UNKNOWN";
}

static bool connection_manager_lock(TickType_t timeout)
{
    if (s_connection_mgr.lock == NULL)
    {
        return false;
    }

    return xSemaphoreTake(s_connection_mgr.lock, timeout) == pdTRUE;
}

static void connection_manager_unlock(void)
{
    if (s_connection_mgr.lock != NULL)
    {
        xSemaphoreGive(s_connection_mgr.lock);
    }
}

const char *connection_manager_uplink_to_str(connection_manager_uplink_t uplink)
{
    switch (uplink)
    {
        case CONNECTION_MANAGER_UPLINK_WIFI_STA:
            return "wifi_sta";

        case CONNECTION_MANAGER_UPLINK_USB_ETH:
            return "usb_eth";

        case CONNECTION_MANAGER_UPLINK_NONE:
        default:
            return "none";
    }
}

static void connection_manager_reconcile_locked(const char *reason)
{
    esp_netif_t *wifi_netif;
    esp_netif_t *usb_netif;
    esp_netif_t *target_netif;
    connection_manager_uplink_t next_uplink;

    s_connection_mgr.status.wifi_connected = dev_status_is_sta_connected();
    s_connection_mgr.status.usb_connected = dev_status_is_eth_connected();
    wifi_netif = esp_netif_get_handle_from_ifkey(s_connection_mgr.wifi_sta_ifkey);
    usb_netif = esp_netif_get_handle_from_ifkey(s_connection_mgr.usb_eth_ifkey);
    target_netif = NULL;
    next_uplink = CONNECTION_MANAGER_UPLINK_NONE;

    if (s_connection_mgr.status.wifi_connected)
    {
        target_netif = wifi_netif;
        if (target_netif != NULL)
        {
            next_uplink = CONNECTION_MANAGER_UPLINK_WIFI_STA;
        }
    }

    if (next_uplink == CONNECTION_MANAGER_UPLINK_NONE &&
        s_connection_mgr.status.usb_fallback_enabled &&
        s_connection_mgr.status.usb_connected)
    {
        target_netif = usb_netif;
        if (target_netif != NULL)
        {
            next_uplink = CONNECTION_MANAGER_UPLINK_USB_ETH;
        }
    }

    if (target_netif != NULL)
    {
        esp_netif_set_default_netif(target_netif);
    }

    ESP_LOGI(TAG,
             "reconcile[%s]: wifi_bit=%d usb_bit=%d usb_fallback=%d wifi_if=%p usb_if=%p target=%s",
             (reason != NULL) ? reason : "manual",
             s_connection_mgr.status.wifi_connected,
             s_connection_mgr.status.usb_connected,
             s_connection_mgr.status.usb_fallback_enabled,
             (void *)wifi_netif,
             (void *)usb_netif,
             connection_manager_uplink_to_str(next_uplink));

    if (next_uplink == CONNECTION_MANAGER_UPLINK_NONE &&
        s_connection_mgr.status.usb_fallback_enabled &&
        usb_netif != NULL &&
        !s_connection_mgr.status.usb_connected)
    {
        ESP_LOGI(TAG, "reconcile[%s]: USB netif exists but DEV_ETH_CONNECTED_BIT is clear; waiting for USB IP", (reason != NULL) ? reason : "manual");
    }

    if (s_connection_mgr.status.active_uplink != next_uplink)
    {
        ESP_LOGI(TAG,
                 "active uplink -> %s (wifi=%d usb=%d usb_fallback=%d)",
                 connection_manager_uplink_to_str(next_uplink),
                 s_connection_mgr.status.wifi_connected,
                 s_connection_mgr.status.usb_connected,
                 s_connection_mgr.status.usb_fallback_enabled);
    }

    s_connection_mgr.status.active_uplink = next_uplink;
}

static void connection_manager_event_handler(void *arg,
                                             esp_event_base_t event_base,
                                             int32_t event_id,
                                             void *event_data)
{
    (void)arg;
    (void)event_data;

    if (!s_connection_mgr.initialized)
    {
        return;
    }

    ESP_LOGI(TAG, "event: %s", connection_manager_event_to_str(event_base, event_id));

    if (!connection_manager_lock(pdMS_TO_TICKS(50)))
    {
        ESP_LOGW(TAG, "event: failed to acquire lock for %s", connection_manager_event_to_str(event_base, event_id));
        return;
    }

    connection_manager_reconcile_locked(connection_manager_event_to_str(event_base, event_id));
    connection_manager_unlock();
}

esp_err_t connection_manager_request_reconcile(void)
{
    if (!s_connection_mgr.initialized)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (!connection_manager_lock(pdMS_TO_TICKS(50)))
    {
        return ESP_ERR_TIMEOUT;
    }

    connection_manager_reconcile_locked("manual");
    connection_manager_unlock();
    return ESP_OK;
}

esp_err_t connection_manager_set_usb_fallback_enabled(bool enabled)
{
    if (!s_connection_mgr.initialized)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (!connection_manager_lock(pdMS_TO_TICKS(50)))
    {
        return ESP_ERR_TIMEOUT;
    }

    s_connection_mgr.status.usb_fallback_enabled = enabled;
    connection_manager_reconcile_locked(enabled ? "usb_fallback_enabled" : "usb_fallback_disabled");
    connection_manager_unlock();
    return ESP_OK;
}

esp_err_t connection_manager_get_status(connection_manager_status_t *status)
{
    if (status == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (!s_connection_mgr.initialized)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (!connection_manager_lock(pdMS_TO_TICKS(50)))
    {
        return ESP_ERR_TIMEOUT;
    }

    memcpy(status, &s_connection_mgr.status, sizeof(*status));
    connection_manager_unlock();
    return ESP_OK;
}

esp_err_t connection_manager_init(const connection_manager_config_t *config)
{
    const char *wifi_sta_ifkey;
    const char *usb_eth_ifkey;

    if (s_connection_mgr.initialized)
    {
        return ESP_OK;
    }

    wifi_sta_ifkey = CONNECTION_MANAGER_DEFAULT_WIFI_STA_IFKEY;
    usb_eth_ifkey = CONNECTION_MANAGER_DEFAULT_USB_ETH_IFKEY;

    if (config != NULL)
    {
        if (config->wifi_sta_ifkey != NULL && config->wifi_sta_ifkey[0] != '\0')
        {
            wifi_sta_ifkey = config->wifi_sta_ifkey;
        }

        if (config->usb_eth_ifkey != NULL && config->usb_eth_ifkey[0] != '\0')
        {
            usb_eth_ifkey = config->usb_eth_ifkey;
        }
    }

    memset(&s_connection_mgr, 0, sizeof(s_connection_mgr));
    strlcpy(s_connection_mgr.wifi_sta_ifkey, wifi_sta_ifkey, sizeof(s_connection_mgr.wifi_sta_ifkey));
    strlcpy(s_connection_mgr.usb_eth_ifkey, usb_eth_ifkey, sizeof(s_connection_mgr.usb_eth_ifkey));
    s_connection_mgr.lock = xSemaphoreCreateMutexStatic(&s_connection_mgr.lock_buffer);
    ESP_RETURN_ON_FALSE(s_connection_mgr.lock != NULL, ESP_ERR_NO_MEM, TAG, "failed to create mutex");

    ESP_RETURN_ON_ERROR(
        esp_event_handler_instance_register(IP_EVENT,
                                            IP_EVENT_STA_GOT_IP,
                                            connection_manager_event_handler,
                                            NULL,
                                            &s_connection_mgr.sta_got_ip_inst),
        TAG,
        "failed to register sta got ip handler");

    ESP_RETURN_ON_ERROR(
        esp_event_handler_instance_register(WIFI_EVENT,
                                            WIFI_EVENT_STA_DISCONNECTED,
                                            connection_manager_event_handler,
                                            NULL,
                                            &s_connection_mgr.sta_disconnected_inst),
        TAG,
        "failed to register sta disconnected handler");

    ESP_RETURN_ON_ERROR(
        esp_event_handler_instance_register(WIFI_EVENT,
                                            WIFI_EVENT_STA_STOP,
                                            connection_manager_event_handler,
                                            NULL,
                                            &s_connection_mgr.sta_stop_inst),
        TAG,
        "failed to register sta stop handler");

    ESP_RETURN_ON_ERROR(
        esp_event_handler_instance_register(IP_EVENT,
                                            IP_EVENT_ETH_GOT_IP,
                                            connection_manager_event_handler,
                                            NULL,
                                            &s_connection_mgr.eth_got_ip_inst),
        TAG,
        "failed to register eth got ip handler");

    ESP_RETURN_ON_ERROR(
        esp_event_handler_instance_register(IP_EVENT,
                                            IP_EVENT_ETH_LOST_IP,
                                            connection_manager_event_handler,
                                            NULL,
                                            &s_connection_mgr.eth_lost_ip_inst),
        TAG,
        "failed to register eth lost ip handler");

    s_connection_mgr.initialized = true;
    s_connection_mgr.status.initialized = true;
    s_connection_mgr.status.usb_fallback_enabled = false;

    ESP_LOGI(TAG,
             "connection manager initialized (wifi=%s usb=%s)",
             s_connection_mgr.wifi_sta_ifkey,
             s_connection_mgr.usb_eth_ifkey);

    return connection_manager_request_reconcile();
}