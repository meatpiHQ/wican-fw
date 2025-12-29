#include <stdlib.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"

#include "lwip/opt.h"

#include "usb_eth_netif_glue_internal.h"

static const char *TAG = "usb_eth_netif";

static void usb_eth_netif_free(void *h, void *buffer)
{
    (void)h;
    free(buffer);
}

static void usb_eth_netif_action_got_ip(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    usb_eth_netif_glue_t *glue;
    ip_event_got_ip_t *event;
    const esp_netif_ip_info_t *ip_info;

    (void)base;
    (void)event_id;

    glue = (usb_eth_netif_glue_t *)handler_args;
    event = (ip_event_got_ip_t *)event_data;
    ip_info = &event->ip_info;

    ESP_LOGI(TAG, "NETIF %s Got IP", esp_netif_get_ifkey(glue->base.netif));
    ESP_LOGI(TAG, "IP:" IPSTR, IP2STR(&ip_info->ip));
    ESP_LOGI(TAG, "MASK:" IPSTR, IP2STR(&ip_info->netmask));
    ESP_LOGI(TAG, "GW:" IPSTR, IP2STR(&ip_info->gw));
}

static esp_err_t usb_eth_netif_post_attach(esp_netif_t *esp_netif, void *args)
{
    usb_eth_netif_glue_t *glue;
    esp_netif_driver_ifconfig_t driver_ifconfig;

    glue = (usb_eth_netif_glue_t *)args;

    glue->base.netif = esp_netif;

    memset(&driver_ifconfig, 0, sizeof(driver_ifconfig));
    driver_ifconfig.handle = glue;
    driver_ifconfig.transmit = glue->transmit;

#if !LWIP_TCPIP_CORE_LOCKING_INPUT
    driver_ifconfig.driver_free_rx_buffer = usb_eth_netif_free;
#endif

    return esp_netif_set_driver_config(esp_netif, &driver_ifconfig);
}

esp_err_t usb_eth_netif_glue_start(usb_eth_netif_glue_t *glue,
                                  const char *if_key,
                                  const char *if_desc,
                                  const uint8_t mac[6],
                                  const usb_eth_host_netif_config_t *netif_cfg,
                                  esp_err_t (*transmit)(void *h, void *buffer, size_t len))
{
    esp_netif_inherent_config_t base_cfg;
    esp_netif_config_t netif_config;
    esp_netif_t *esp_netif;

    if (glue == NULL || if_key == NULL || if_desc == NULL || mac == NULL || transmit == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (glue->started)
    {
        return ESP_OK;
    }

    memset(glue, 0, sizeof(*glue));

    glue->transmit = transmit;
    glue->base.post_attach = usb_eth_netif_post_attach;

    base_cfg = (esp_netif_inherent_config_t)ESP_NETIF_INHERENT_DEFAULT_ETH();
    base_cfg.if_key = if_key;
    base_cfg.if_desc = if_desc;

    netif_config = (esp_netif_config_t)ESP_NETIF_DEFAULT_ETH();
    netif_config.base = &base_cfg;

    esp_netif = esp_netif_new(&netif_config);
    if (esp_netif == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    esp_netif_attach(esp_netif, &glue->base);
    uint8_t mac_copy[6];
    memcpy(mac_copy, mac, sizeof(mac_copy));
    esp_netif_set_mac(esp_netif, mac_copy);

    // Only register DHCP "got IP" handler when we actually expect DHCP.
    if (netif_cfg == NULL || netif_cfg->mode == USB_ETH_HOST_IP_MODE_DHCP)
    {
        if (esp_event_handler_instance_register(IP_EVENT, IP_EVENT_ETH_GOT_IP, usb_eth_netif_action_got_ip, glue, &glue->ins_got_ip) == ESP_OK)
        {
            glue->got_ip_registered = true;
        }
    }

    esp_netif_action_start(esp_netif, NULL, 0, NULL);
    esp_netif_action_connected(esp_netif, NULL, 0, NULL);

    if (netif_cfg != NULL)
    {
        if (netif_cfg->mode == USB_ETH_HOST_IP_MODE_STATIC)
        {
            // action_connected() may auto-start DHCP; force stop + apply static after link-up.
            (void)esp_netif_dhcpc_stop(esp_netif);
            esp_err_t err = esp_netif_set_ip_info(esp_netif, &netif_cfg->static_ip);
            if (err == ESP_OK)
            {
                ESP_LOGI(TAG, "NETIF %s Static IP applied", if_key);
                ESP_LOGI(TAG, "IP:" IPSTR, IP2STR(&netif_cfg->static_ip.ip));
                ESP_LOGI(TAG, "MASK:" IPSTR, IP2STR(&netif_cfg->static_ip.netmask));
                ESP_LOGI(TAG, "GW:" IPSTR, IP2STR(&netif_cfg->static_ip.gw));
            }
            else
            {
                ESP_LOGW(TAG, "NETIF %s Static IP failed: %s", if_key, esp_err_to_name(err));
            }
        }
        else if (netif_cfg->mode == USB_ETH_HOST_IP_MODE_DHCP)
        {
            // Action_connected typically starts DHCP, but enforce it for determinism.
            (void)esp_netif_dhcpc_start(esp_netif);
        }
    }

    glue->started = true;
    return ESP_OK;
}

void usb_eth_netif_glue_stop(usb_eth_netif_glue_t *glue)
{
    if (glue == NULL)
    {
        return;
    }

    if (!glue->started)
    {
        return;
    }

    if (glue->got_ip_registered)
    {
        esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_ETH_GOT_IP, glue->ins_got_ip);
        glue->got_ip_registered = false;
    }
    esp_netif_action_stop(glue->base.netif, NULL, 0, NULL);
    esp_netif_destroy(glue->base.netif);

    memset(glue, 0, sizeof(*glue));
}

void usb_eth_netif_input(usb_eth_netif_glue_t *glue, uint8_t *buf, uint32_t len)
{
    uint8_t *input_buf;

    if (glue == NULL || !glue->started)
    {
        return;
    }

#if !LWIP_TCPIP_CORE_LOCKING_INPUT
    input_buf = (uint8_t *)malloc(len);
    if (input_buf == NULL)
    {
        return;
    }

    memcpy(input_buf, buf, len);
    esp_netif_receive(glue->base.netif, input_buf, len, NULL);
#else
    input_buf = buf;
    esp_netif_receive(glue->base.netif, input_buf, len, NULL);
#endif
}
