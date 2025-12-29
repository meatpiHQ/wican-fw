#include "usb_eth_host.h"

#include "esp_log.h"

#include "usb_osal.h"

#include "usbh_core.h"

#include "usb_eth_netif_glue_internal.h"

#include "usbh_cdc_ecm.h"

static const char *TAG = "usb_eth_cdc_ecm";

static usb_eth_netif_glue_t g_glue;

static esp_err_t usb_eth_cdc_ecm_transmit(void *h, void *buffer, size_t len)
{
    int ret;

    (void)h;

    usb_memcpy(usbh_cdc_ecm_get_eth_txbuf(), buffer, len);

    ret = usbh_cdc_ecm_eth_output((uint32_t)len);
    if (ret < 0)
    {
        return ESP_FAIL;
    }

    return ESP_OK;
}

void usbh_cdc_ecm_eth_input(uint8_t *buf, uint32_t buflen)
{
    usb_eth_netif_input(&g_glue, buf, buflen);
}

void usbh_cdc_ecm_run(struct usbh_cdc_ecm *cdc_ecm_class)
{
    if (!usb_eth_host_is_started())
    {
        return;
    }

    if (!usb_eth_host_driver_is_allowed(USB_ETH_HOST_DRIVER_CDC_ECM))
    {
        ESP_LOGI(TAG, "CDC-ECM blocked by runtime mask");
        return;
    }

    if (cdc_ecm_class == NULL)
    {
        return;
    }

    ESP_LOGI(TAG, "Starting CDC-ECM netif");

    usb_eth_netif_glue_start(&g_glue,
                             "u0",
                             "usbh cdc ecm",
                             cdc_ecm_class->mac,
                             usb_eth_host_get_netif_config(),
                             usb_eth_cdc_ecm_transmit);

    usb_osal_thread_create("usbh_cdc_ecm_rx", 2048, CONFIG_USBHOST_PSC_PRIO + 1, usbh_cdc_ecm_rx_thread, NULL);
}

void usbh_cdc_ecm_stop(struct usbh_cdc_ecm *cdc_ecm_class)
{
    (void)cdc_ecm_class;

    usb_eth_netif_glue_stop(&g_glue);
}
