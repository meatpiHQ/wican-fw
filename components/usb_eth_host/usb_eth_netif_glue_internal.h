#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#include "esp_event.h"
#include "esp_netif.h"

#include "usb_eth_host.h"

typedef struct usb_eth_netif_glue
{
    esp_netif_driver_base_t base;
    esp_event_handler_instance_t ins_got_ip;
    bool got_ip_registered;
    esp_err_t (*transmit)(void *h, void *buffer, size_t len);
    bool started;
} usb_eth_netif_glue_t;

esp_err_t usb_eth_netif_glue_start(usb_eth_netif_glue_t *glue,
                                  const char *if_key,
                                  const char *if_desc,
                                  const uint8_t mac[6],
                                  const usb_eth_host_netif_config_t *netif_cfg,
                                  esp_err_t (*transmit)(void *h, void *buffer, size_t len));

void usb_eth_netif_glue_stop(usb_eth_netif_glue_t *glue);
void usb_eth_netif_input(usb_eth_netif_glue_t *glue, uint8_t *buf, uint32_t len);
