#pragma once

#include "usb_eth_host.h"

#ifdef __cplusplus
extern "C" {
#endif

void usb_eth_host_notify_driver_started(usb_eth_host_driver_t driver, const char *ifkey);
void usb_eth_host_notify_driver_stopped(usb_eth_host_driver_t driver);

#ifdef __cplusplus
}
#endif