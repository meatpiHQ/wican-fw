#pragma once

#include "filesystem.h"
#include "usb_host_manager.h"

#define USB_HOST_MANAGER_CONFIG_PATH FS_MOUNT_POINT "/usb_host_config.json"
#define USB_HOST_MANAGER_NETIF_IFKEY "u2"

void usb_host_manager_set_default_config(usb_host_manager_config_t *config);
void usb_host_manager_sanitize_config(usb_host_manager_config_t *config);
esp_err_t usb_host_manager_config_preload(void);