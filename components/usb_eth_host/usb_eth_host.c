#include "usb_eth_host.h"

#include <string.h>

#include "esp_log.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_netif.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/gpio.h"

#include "usbh_core.h"
#include "usb_config.h"

static const char *TAG = "usb_eth_host";

static bool g_started;
static usb_eth_host_config_t g_cfg;

static esp_netif_t *g_last_usb_eth_netif;
static esp_event_handler_instance_t g_ip_eth_got_ip_inst;
static esp_event_handler_instance_t g_ip_sta_got_ip_inst;

static void usb_eth_host_try_set_default_netif(esp_netif_t *netif)
{
    if (netif == NULL)
    {
        return;
    }

    if (!g_cfg.netif.prefer_as_default_route)
    {
        return;
    }

    esp_netif_set_default_netif(netif);
}

static void usb_eth_host_on_eth_got_ip(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    ip_event_got_ip_t *event;

    (void)handler_args;
    (void)base;
    (void)event_id;

    event = (ip_event_got_ip_t *)event_data;
    if (event == NULL)
    {
        return;
    }

    g_last_usb_eth_netif = event->esp_netif;
    usb_eth_host_try_set_default_netif(g_last_usb_eth_netif);
}

static void usb_eth_host_on_sta_got_ip(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    (void)handler_args;
    (void)base;
    (void)event_id;
    (void)event_data;

    // If Wi-Fi becomes the default after getting IP, force USB-Ethernet back to default
    // when the option is enabled.
    if (g_last_usb_eth_netif != NULL)
    {
        usb_eth_host_try_set_default_netif(g_last_usb_eth_netif);
    }
}

esp_err_t usb_eth_host_platform_apply_netif_cfg(esp_netif_t *netif, const char *if_key)
{
    const usb_eth_host_netif_config_t *cfg;

    if (netif == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (!g_started)
    {
        return ESP_ERR_INVALID_STATE;
    }

    cfg = &g_cfg.netif;

    // Track the last-created USB ETH netif so we can keep it as default if requested.
    g_last_usb_eth_netif = netif;
    usb_eth_host_try_set_default_netif(netif);

    if (cfg->mode == USB_ETH_HOST_IP_MODE_STATIC)
    {
        // Best-effort: stop DHCP (even if it already started) and apply static.
        (void)esp_netif_dhcpc_stop(netif);
        esp_err_t err = esp_netif_set_ip_info(netif, &cfg->static_ip);
        if (err == ESP_OK)
        {
            ESP_LOGI(TAG, "NETIF %s Static IP applied", (if_key != NULL) ? if_key : "?");
            ESP_LOGI(TAG, "IP:" IPSTR, IP2STR(&cfg->static_ip.ip));
            ESP_LOGI(TAG, "MASK:" IPSTR, IP2STR(&cfg->static_ip.netmask));
            ESP_LOGI(TAG, "GW:" IPSTR, IP2STR(&cfg->static_ip.gw));
        }
        else
        {
            ESP_LOGW(TAG, "NETIF %s Static IP failed: %s", (if_key != NULL) ? if_key : "?", esp_err_to_name(err));
        }

        return err;
    }

    if (cfg->mode == USB_ETH_HOST_IP_MODE_DHCP)
    {
        // Ensure DHCP is allowed to run.
        (void)esp_netif_dhcpc_start(netif);

        // If DHCP provides connectivity, prefer this interface as default when enabled.
        usb_eth_host_try_set_default_netif(netif);
        return ESP_OK;
    }

    return ESP_ERR_INVALID_ARG;
}

static void usb_eth_host_apply_gpio(const usb_eth_host_gpio_t *gpio_cfg, bool enable)
{
    if (gpio_cfg == NULL)
    {
        return;
    }

    if (gpio_cfg->vbus_en_gpio < 0)
    {
        return;
    }

    gpio_reset_pin((gpio_num_t)gpio_cfg->vbus_en_gpio);
    gpio_set_direction((gpio_num_t)gpio_cfg->vbus_en_gpio, GPIO_MODE_OUTPUT);

    if (enable)
    {
        gpio_set_level((gpio_num_t)gpio_cfg->vbus_en_gpio, gpio_cfg->vbus_en_active_high ? 1 : 0);

        if (gpio_cfg->vbus_on_delay_ms > 0)
        {
            vTaskDelay(pdMS_TO_TICKS(gpio_cfg->vbus_on_delay_ms));
        }
    }
    else
    {
        gpio_set_level((gpio_num_t)gpio_cfg->vbus_en_gpio, gpio_cfg->vbus_en_active_high ? 0 : 1);
    }
}

bool usb_eth_host_driver_is_allowed(usb_eth_host_driver_t driver)
{
    uint32_t bit;

    if (driver >= USB_ETH_HOST_DRIVER_MAX)
    {
        return false;
    }

    bit = (1UL << (uint32_t)driver);
    return (g_cfg.allowed_driver_mask & bit) != 0;
}

bool usb_eth_host_is_started(void)
{
    return g_started;
}

const usb_eth_host_netif_config_t *usb_eth_host_get_netif_config(void)
{
    return &g_cfg.netif;
}

esp_err_t usb_eth_host_netif_apply(const char *ifkey, const usb_eth_host_netif_config_t *cfg)
{
    if (ifkey == NULL || cfg == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    esp_netif_t *netif = esp_netif_get_handle_from_ifkey(ifkey);
    if (netif == NULL)
    {
        return ESP_ERR_NOT_FOUND;
    }

    if (cfg->mode == USB_ETH_HOST_IP_MODE_DHCP)
    {
        // Best-effort reset then start DHCP.
        (void)esp_netif_dhcpc_stop(netif);
        esp_netif_ip_info_t zero_ip = { 0 };
        (void)esp_netif_set_ip_info(netif, &zero_ip);
        return esp_netif_dhcpc_start(netif);
    }

    if (cfg->mode == USB_ETH_HOST_IP_MODE_STATIC)
    {
        // Stop DHCP and apply static IP.
        (void)esp_netif_dhcpc_stop(netif);
        return esp_netif_set_ip_info(netif, &cfg->static_ip);
    }

    return ESP_ERR_INVALID_ARG;
}

esp_err_t usb_eth_host_start(const usb_eth_host_config_t *config)
{
    esp_err_t err;

    if (g_started)
    {
        return ESP_OK;
    }

    if (config == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    memset(&g_cfg, 0, sizeof(g_cfg));
    memcpy(&g_cfg, config, sizeof(g_cfg));

    g_last_usb_eth_netif = NULL;

    if (!g_cfg.enable)
    {
        ESP_LOGI(TAG, "USB ETH host disabled (runtime)");
        return ESP_OK;
    }

    if (g_cfg.allowed_driver_mask == 0)
    {
        g_cfg.allowed_driver_mask = USB_ETH_HOST_DRIVER_MASK_ALL;
    }

    if (g_cfg.bus_id < 0)
    {
        g_cfg.bus_id = 0;
    }

    if (g_cfg.reg_base == 0)
    {
        g_cfg.reg_base = (uintptr_t)ESP_USBH_BASE;
    }

    usb_eth_host_apply_gpio(&g_cfg.gpio, true);

    ESP_LOGI(TAG, "Starting CherryUSB host: bus=%d reg_base=0x%08x", g_cfg.bus_id, (unsigned)g_cfg.reg_base);

    // Register event handlers so we can prefer USB-Ethernet as the default route when requested.
    // These are best-effort; if registration fails, routing will still work, but may not be preferred.
    (void)esp_event_handler_instance_register(IP_EVENT, IP_EVENT_ETH_GOT_IP, usb_eth_host_on_eth_got_ip, NULL, &g_ip_eth_got_ip_inst);
    (void)esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, usb_eth_host_on_sta_got_ip, NULL, &g_ip_sta_got_ip_inst);

    err = usbh_initialize((uint8_t)g_cfg.bus_id, g_cfg.reg_base);
    ESP_RETURN_ON_ERROR(err, TAG, "usbh_initialize failed");

    g_started = true;
    return ESP_OK;
}

void usb_eth_host_stop(void)
{
    if (!g_started)
    {
        return;
    }

    usbh_deinitialize((uint8_t)g_cfg.bus_id);

    (void)esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_ETH_GOT_IP, g_ip_eth_got_ip_inst);
    (void)esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, g_ip_sta_got_ip_inst);
    g_last_usb_eth_netif = NULL;

    usb_eth_host_apply_gpio(&g_cfg.gpio, false);

    memset(&g_cfg, 0, sizeof(g_cfg));
    g_started = false;
}
