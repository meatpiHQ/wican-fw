#include "usb_eth_host.h"

#include "esp_log.h"

#include "usb_osal.h"

#include "usbh_core.h"

#include "usb_eth_netif_glue_internal.h"

#include "usbh_asix.h"

static const char *TAG = "usb_eth_asix";

static usb_eth_netif_glue_t g_glue;

static esp_err_t usb_eth_asix_transmit(void *h, void *buffer, size_t len)
{
    int ret;

    (void)h;

    usb_memcpy(usbh_asix_get_eth_txbuf(), buffer, len);

    ret = usbh_asix_eth_output((uint32_t)len);
    if (ret < 0)
    {
        return ESP_FAIL;
    }

    return ESP_OK;
}

void usbh_asix_eth_input(uint8_t *buf, uint32_t buflen)
{
    usb_eth_netif_input(&g_glue, buf, buflen);
}

void usbh_asix_run(struct usbh_asix *asix_class)
{
    if (!usb_eth_host_is_started())
    {
        return;
    }

    if (!usb_eth_host_driver_is_allowed(USB_ETH_HOST_DRIVER_ASIX))
    {
        ESP_LOGI(TAG, "ASIX blocked by runtime mask");
        return;
    }

    if (asix_class == NULL)
    {
        return;
    }

    ESP_LOGI(TAG, "Starting ASIX netif");

    usb_eth_netif_glue_start(&g_glue,
                             "u3",
                             "usbh asix",
                             asix_class->mac,
                             usb_eth_host_get_netif_config(),
                             usb_eth_asix_transmit);

    usb_osal_thread_create("usbh_asix_rx", 2048, CONFIG_USBHOST_PSC_PRIO + 1, usbh_asix_rx_thread, NULL);
}

void usbh_asix_stop(struct usbh_asix *asix_class)
{
    (void)asix_class;

    usb_eth_netif_glue_stop(&g_glue);
}
