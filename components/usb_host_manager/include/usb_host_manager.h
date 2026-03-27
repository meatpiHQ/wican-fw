#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum usb_host_manager_device_type
{
    USB_HOST_MANAGER_DEVICE_NONE = 0,
    USB_HOST_MANAGER_DEVICE_ESPNETLINK = 1,
    USB_HOST_MANAGER_DEVICE_GPS = 2,
    USB_HOST_MANAGER_DEVICE_USB_ETHERNET = 3,
} usb_host_manager_device_type_t;

typedef enum usb_host_manager_ip_mode
{
    USB_HOST_MANAGER_IP_MODE_DHCP = 0,
    USB_HOST_MANAGER_IP_MODE_STATIC = 1,
} usb_host_manager_ip_mode_t;

typedef enum usb_host_manager_state
{
    USB_HOST_MANAGER_STATE_DISABLED = 0,
    USB_HOST_MANAGER_STATE_WAITING_FOR_DEVICE,
    USB_HOST_MANAGER_STATE_STARTING,
    USB_HOST_MANAGER_STATE_RUNNING,
    USB_HOST_MANAGER_STATE_STOPPING,
    USB_HOST_MANAGER_STATE_ERROR,
} usb_host_manager_state_t;

typedef struct usb_host_manager_platform_config
{
    int usb_id_gpio;
    int usb_mode_gpio;
    int usb_vbus_gpio;
    bool usb_vbus_active_high;
    uint32_t usb_vbus_on_delay_ms;
    int bus_id;
    uintptr_t reg_base;
} usb_host_manager_platform_config_t;

typedef struct usb_host_manager_espnetlink_config
{
    bool enable_cli;
    bool prefer_default_route;
    usb_host_manager_ip_mode_t ip_mode;
    char static_ip[16];
    char static_netmask[16];
    char static_gw[16];
    char management_ip[16];
    char cli_dev_path[32];
    bool cli_assert_dtr;
    bool cli_assert_rts;
    uint32_t cli_reconnect_delay_ms;
    uint32_t cli_line_state_delay_ms;
    uint32_t cli_rx_task_stack_size;
    uint32_t cli_rx_task_priority;
    int32_t cli_rx_task_core_id;
    uint32_t cli_rx_buffer_size;
} usb_host_manager_espnetlink_config_t;

typedef struct usb_host_manager_config
{
    bool enabled;
    usb_host_manager_device_type_t active_device_type;
    uint32_t monitor_interval_ms;
    uint32_t device_attach_delay_ms;
    uint32_t device_detach_delay_ms;
    usb_host_manager_espnetlink_config_t espnetlink;
} usb_host_manager_config_t;

typedef struct usb_host_manager_status
{
    bool initialized;
    bool enabled;
    int usb_id_level;
    bool device_detected;
    bool device_started;
    bool ethernet_connected;
    bool cli_enabled;
    bool cli_connected;
    usb_host_manager_device_type_t configured_device_type;
    usb_host_manager_device_type_t active_device_type;
    usb_host_manager_state_t state;
    char local_ip[16];
    char netmask[16];
    char gateway[16];
    char management_ip[16];
    char last_error[96];
} usb_host_manager_status_t;

esp_err_t usb_host_manager_init(const usb_host_manager_platform_config_t *platform_config);
esp_err_t usb_host_manager_request_reload(void);
esp_err_t usb_host_manager_load_config(usb_host_manager_config_t *config);
esp_err_t usb_host_manager_save_config(const usb_host_manager_config_t *config);
esp_err_t usb_host_manager_set_config(const usb_host_manager_config_t *config);
esp_err_t usb_host_manager_get_config(usb_host_manager_config_t *config);
esp_err_t usb_host_manager_get_status(usb_host_manager_status_t *status);

const char *usb_host_manager_device_type_to_str(usb_host_manager_device_type_t type);
const char *usb_host_manager_state_to_str(usb_host_manager_state_t state);

#ifdef __cplusplus
}
#endif