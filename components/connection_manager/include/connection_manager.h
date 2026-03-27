#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CONNECTION_MANAGER_DEFAULT_WIFI_STA_IFKEY "WIFI_STA_DEF"
#define CONNECTION_MANAGER_DEFAULT_USB_ETH_IFKEY  "u2"

typedef enum connection_manager_uplink
{
    CONNECTION_MANAGER_UPLINK_NONE = 0,
    CONNECTION_MANAGER_UPLINK_WIFI_STA,
    CONNECTION_MANAGER_UPLINK_USB_ETH,
} connection_manager_uplink_t;

typedef struct connection_manager_config
{
    const char *wifi_sta_ifkey;
    const char *usb_eth_ifkey;
} connection_manager_config_t;

typedef struct connection_manager_status
{
    bool initialized;
    bool usb_fallback_enabled;
    bool wifi_connected;
    bool usb_connected;
    connection_manager_uplink_t active_uplink;
} connection_manager_status_t;

esp_err_t connection_manager_init(const connection_manager_config_t *config);
esp_err_t connection_manager_set_usb_fallback_enabled(bool enabled);
esp_err_t connection_manager_request_reconcile(void);
esp_err_t connection_manager_get_status(connection_manager_status_t *status);

const char *connection_manager_uplink_to_str(connection_manager_uplink_t uplink);

#ifdef __cplusplus
}
#endif