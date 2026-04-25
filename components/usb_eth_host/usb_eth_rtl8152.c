#include "usb_eth_host_internal.h"

#include "esp_log.h"

#include "usb_osal.h"

#include "usbh_core.h"

#include "usb_eth_netif_glue_internal.h"

#include "usbh_rtl8152.h"

static const char *TAG = "usb_eth_rtl8152";

static usb_eth_netif_glue_t g_glue;

static esp_err_t usb_eth_rtl8152_transmit(void *h, void *buffer, size_t len)
{
    int ret;

    (void)h;

    usb_memcpy(usbh_rtl8152_get_eth_txbuf(), buffer, len);

    ret = usbh_rtl8152_eth_output((uint32_t)len);
    if (ret < 0)
    {
        return ESP_FAIL;
    }

    return ESP_OK;
}

void usbh_rtl8152_eth_input(uint8_t *buf, uint32_t buflen)
{
    usb_eth_netif_input(&g_glue, buf, buflen);
}

void usbh_rtl8152_run(struct usbh_rtl8152 *rtl8152_class)
{
    if (!usb_eth_host_is_started())
    {
        return;
    }

    if (!usb_eth_host_driver_is_allowed(USB_ETH_HOST_DRIVER_RTL8152))
    {
        ESP_LOGI(TAG, "RTL8152 blocked by runtime mask");
        return;
    }

    if (rtl8152_class == NULL)
    {
        return;
    }

    ESP_LOGI(TAG, "Starting RTL8152 netif");

    usb_eth_netif_glue_start(&g_glue,
                             "u4",
                             "usbh rtl8152",
                             rtl8152_class->mac,
                             usb_eth_host_get_netif_config(),
                             usb_eth_rtl8152_transmit);

    usb_eth_host_notify_driver_started(USB_ETH_HOST_DRIVER_RTL8152, "u4");

    usb_osal_thread_create("usbh_rtl8152_rx", 2048, CONFIG_USBHOST_PSC_PRIO + 1, usbh_rtl8152_rx_thread, NULL);
}

void usbh_rtl8152_stop(struct usbh_rtl8152 *rtl8152_class)
{
    (void)rtl8152_class;

    usb_eth_host_notify_driver_stopped(USB_ETH_HOST_DRIVER_RTL8152);
    usb_eth_netif_glue_stop(&g_glue);
}
