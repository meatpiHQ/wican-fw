#include "usb_host_manager.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "sdkconfig.h"

#include "connection_manager.h"
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/ip4_addr.h"

#include "dev_status.h"
#include "usb_acm_cli.h"
#include "usb_eth_host.h"
#include "usb_host_manager_internal.h"

#define USB_HOST_MANAGER_TASK_STACK_WORDS 4096
#define USB_HOST_MANAGER_TASK_PRIORITY 5
#define USB_HOST_MANAGER_CMD_QUEUE_LEN 4
#define USB_HOST_MANAGER_START_RETRY_MS 3000

typedef enum usb_host_manager_cmd_type
{
    USB_HOST_MANAGER_CMD_WAKE = 0,
    USB_HOST_MANAGER_CMD_RELOAD = 1,
} usb_host_manager_cmd_type_t;

typedef struct usb_host_manager_cmd
{
    usb_host_manager_cmd_type_t type;
} usb_host_manager_cmd_t;

typedef struct usb_host_manager_runtime
{
    bool initialized;
    bool device_present_raw;
    bool espnetlink_started;
    int64_t present_since_us;
    int64_t absent_since_us;
    int64_t next_start_retry_us;
    usb_host_manager_platform_config_t platform;
    usb_host_manager_config_t config;
    usb_host_manager_status_t status;
    SemaphoreHandle_t lock;
    StaticSemaphore_t lock_buffer;
    QueueHandle_t cmd_queue;
    StaticQueue_t cmd_queue_buffer;
    uint8_t *cmd_queue_storage;
    TaskHandle_t task;
    StackType_t *task_stack;
    StaticTask_t task_buffer;
} usb_host_manager_runtime_t;

static const char *TAG = "usb_host_mgr";

static usb_host_manager_runtime_t s_usb_host_mgr = { 0 };

static void usb_host_manager_task(void *arg);

static inline bool usb_host_manager_lock(TickType_t timeout)
{
    if (s_usb_host_mgr.lock == NULL)
    {
        return false;
    }

    return xSemaphoreTake(s_usb_host_mgr.lock, timeout) == pdTRUE;
}

static inline void usb_host_manager_unlock(void)
{
    if (s_usb_host_mgr.lock != NULL)
    {
        xSemaphoreGive(s_usb_host_mgr.lock);
    }
}

const char *usb_host_manager_device_type_to_str(usb_host_manager_device_type_t type)
{
    switch (type)
    {
        case USB_HOST_MANAGER_DEVICE_ESPNETLINK:
            return "espnetlink";

        case USB_HOST_MANAGER_DEVICE_GPS:
            return "gps";

        case USB_HOST_MANAGER_DEVICE_USB_ETHERNET:
            return "usb_ethernet";

        case USB_HOST_MANAGER_DEVICE_NONE:
        default:
            return "none";
    }
}

const char *usb_host_manager_state_to_str(usb_host_manager_state_t state)
{
    switch (state)
    {
        case USB_HOST_MANAGER_STATE_WAITING_FOR_DEVICE:
            return "waiting_for_device";

        case USB_HOST_MANAGER_STATE_STARTING:
            return "starting";

        case USB_HOST_MANAGER_STATE_RUNNING:
            return "running";

        case USB_HOST_MANAGER_STATE_STOPPING:
            return "stopping";

        case USB_HOST_MANAGER_STATE_ERROR:
            return "error";

        case USB_HOST_MANAGER_STATE_DISABLED:
        default:
            return "disabled";
    }
}

static void usb_host_manager_set_error_locked(const char *msg)
{
    if (msg == NULL)
    {
        s_usb_host_mgr.status.last_error[0] = '\0';
        return;
    }

    strlcpy(s_usb_host_mgr.status.last_error, msg, sizeof(s_usb_host_mgr.status.last_error));
}

static void usb_host_manager_update_state_locked(usb_host_manager_state_t state)
{
    s_usb_host_mgr.status.state = state;
}

static void usb_host_manager_refresh_ip_locked(void)
{
    esp_netif_t *netif;
    esp_netif_ip_info_t ip_info;

    s_usb_host_mgr.status.local_ip[0] = '\0';
    s_usb_host_mgr.status.netmask[0] = '\0';
    s_usb_host_mgr.status.gateway[0] = '\0';

    netif = esp_netif_get_handle_from_ifkey(USB_HOST_MANAGER_NETIF_IFKEY);
    if (netif == NULL)
    {
        return;
    }

    if (esp_netif_get_ip_info(netif, &ip_info) != ESP_OK)
    {
        return;
    }

    snprintf(s_usb_host_mgr.status.local_ip, sizeof(s_usb_host_mgr.status.local_ip), IPSTR, IP2STR(&ip_info.ip));
    snprintf(s_usb_host_mgr.status.netmask, sizeof(s_usb_host_mgr.status.netmask), IPSTR, IP2STR(&ip_info.netmask));
    snprintf(s_usb_host_mgr.status.gateway, sizeof(s_usb_host_mgr.status.gateway), IPSTR, IP2STR(&ip_info.gw));
}

static bool usb_host_manager_parse_ipv4(const char *str, esp_ip4_addr_t *out)
{
    ip4_addr_t tmp;

    if (str == NULL || out == NULL)
    {
        return false;
    }

    if (ip4addr_aton(str, &tmp) == 0)
    {
        return false;
    }

    out->addr = tmp.addr;
    return true;
}

static bool usb_host_manager_fill_static_ip(const usb_host_manager_espnetlink_config_t *cfg,
                                            esp_netif_ip_info_t *ip_info)
{
    if (cfg == NULL || ip_info == NULL)
    {
        return false;
    }

    memset(ip_info, 0, sizeof(*ip_info));

    if (!usb_host_manager_parse_ipv4(cfg->static_ip, &ip_info->ip))
    {
        return false;
    }

    if (!usb_host_manager_parse_ipv4(cfg->static_netmask, &ip_info->netmask))
    {
        return false;
    }

    if (!usb_host_manager_parse_ipv4(cfg->static_gw, &ip_info->gw))
    {
        return false;
    }

    return true;
}

static void usb_host_manager_on_eth_ip_up(void)
{
    if (!usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        return;
    }

    s_usb_host_mgr.status.ethernet_connected = true;
    usb_host_manager_refresh_ip_locked();
    usb_host_manager_unlock();
    dev_status_set_eth_connected();
}

static void usb_host_manager_on_eth_ip_lost(void)
{
    if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        s_usb_host_mgr.status.ethernet_connected = false;
        s_usb_host_mgr.status.local_ip[0] = '\0';
        s_usb_host_mgr.status.netmask[0] = '\0';
        s_usb_host_mgr.status.gateway[0] = '\0';
        usb_host_manager_unlock();
    }

    dev_status_clear_eth_connected();
}

static void usb_host_manager_update_presence_locked(void)
{
    int level;

    level = 1;
    if (s_usb_host_mgr.platform.usb_id_gpio >= 0)
    {
        level = gpio_get_level((gpio_num_t)s_usb_host_mgr.platform.usb_id_gpio);
    }

    s_usb_host_mgr.status.usb_id_level = level;
    s_usb_host_mgr.device_present_raw = (level == 0);
    s_usb_host_mgr.status.device_detected = s_usb_host_mgr.device_present_raw;
}

static esp_err_t usb_host_manager_start_espnetlink(void)
{
    usb_eth_host_config_t eth_cfg;
    usb_acm_cli_config_t cli_cfg;
    esp_netif_ip_info_t ip_info;
    esp_err_t ret;

    memset(&eth_cfg, 0, sizeof(eth_cfg));
    eth_cfg.enable = true;
    eth_cfg.allowed_driver_mask = USB_ETH_HOST_DRIVER_MASK_RNDIS;
    eth_cfg.bus_id = s_usb_host_mgr.platform.bus_id;
    eth_cfg.reg_base = s_usb_host_mgr.platform.reg_base;
    eth_cfg.gpio.vbus_en_gpio = s_usb_host_mgr.platform.usb_vbus_gpio;
    eth_cfg.gpio.vbus_en_active_high = s_usb_host_mgr.platform.usb_vbus_active_high;
    eth_cfg.gpio.vbus_on_delay_ms = s_usb_host_mgr.platform.usb_vbus_on_delay_ms;
    eth_cfg.netif.mode = (s_usb_host_mgr.config.espnetlink.ip_mode == USB_HOST_MANAGER_IP_MODE_STATIC) ?
        USB_ETH_HOST_IP_MODE_STATIC : USB_ETH_HOST_IP_MODE_DHCP;
    eth_cfg.netif.prefer_as_default_route = false;
    eth_cfg.on_eth_ip_up = usb_host_manager_on_eth_ip_up;
    eth_cfg.on_eth_ip_lost = usb_host_manager_on_eth_ip_lost;

    if (eth_cfg.netif.mode == USB_ETH_HOST_IP_MODE_STATIC)
    {
        if (!usb_host_manager_fill_static_ip(&s_usb_host_mgr.config.espnetlink, &ip_info))
        {
            return ESP_ERR_INVALID_ARG;
        }

        eth_cfg.netif.static_ip = ip_info;
    }

    if (s_usb_host_mgr.platform.usb_mode_gpio >= 0)
    {
        gpio_set_level((gpio_num_t)s_usb_host_mgr.platform.usb_mode_gpio, 1);
    }

    ret = usb_eth_host_start(&eth_cfg);
    if (ret != ESP_OK)
    {
        return ret;
    }

    if (s_usb_host_mgr.config.espnetlink.enable_cli)
    {
        memset(&cli_cfg, 0, sizeof(cli_cfg));
        cli_cfg.enable = true;
        cli_cfg.dev_path = s_usb_host_mgr.config.espnetlink.cli_dev_path;
        cli_cfg.reconnect_delay_ms = s_usb_host_mgr.config.espnetlink.cli_reconnect_delay_ms;
        cli_cfg.assert_dtr = s_usb_host_mgr.config.espnetlink.cli_assert_dtr;
        cli_cfg.assert_rts = s_usb_host_mgr.config.espnetlink.cli_assert_rts;
        cli_cfg.line_state_delay_ms = s_usb_host_mgr.config.espnetlink.cli_line_state_delay_ms;
        cli_cfg.rx_task_stack_size = s_usb_host_mgr.config.espnetlink.cli_rx_task_stack_size;
        cli_cfg.rx_task_priority = (UBaseType_t)s_usb_host_mgr.config.espnetlink.cli_rx_task_priority;
        cli_cfg.rx_task_core_id = s_usb_host_mgr.config.espnetlink.cli_rx_task_core_id;
        cli_cfg.rx_buffer_size = s_usb_host_mgr.config.espnetlink.cli_rx_buffer_size;

        ret = usb_acm_cli_start(&cli_cfg);
        if (ret != ESP_OK)
        {
            if (s_usb_host_mgr.platform.usb_mode_gpio >= 0)
            {
                gpio_set_level((gpio_num_t)s_usb_host_mgr.platform.usb_mode_gpio, 0);
            }
            return ret;
        }
    }

    return ESP_OK;
}

static void usb_host_manager_stop_espnetlink(void)
{
    (void)usb_acm_cli_stop();
    dev_status_clear_eth_connected();
    (void)connection_manager_request_reconcile();

    if (s_usb_host_mgr.platform.usb_mode_gpio >= 0)
    {
        gpio_set_level((gpio_num_t)s_usb_host_mgr.platform.usb_mode_gpio, 0);
    }
}

static void usb_host_manager_update_runtime_status(void)
{
    if (!usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        return;
    }

    usb_host_manager_update_presence_locked();
    s_usb_host_mgr.status.initialized = s_usb_host_mgr.initialized;
    s_usb_host_mgr.status.enabled = s_usb_host_mgr.config.enabled;
    s_usb_host_mgr.status.configured_device_type = s_usb_host_mgr.config.active_device_type;
    s_usb_host_mgr.status.active_device_type = s_usb_host_mgr.espnetlink_started ?
        USB_HOST_MANAGER_DEVICE_ESPNETLINK : USB_HOST_MANAGER_DEVICE_NONE;
    s_usb_host_mgr.status.device_started = s_usb_host_mgr.espnetlink_started;
    s_usb_host_mgr.status.cli_enabled = s_usb_host_mgr.config.espnetlink.enable_cli;
    s_usb_host_mgr.status.cli_connected = usb_acm_cli_is_connected();
    s_usb_host_mgr.status.ethernet_connected = dev_status_is_eth_connected();
    strlcpy(s_usb_host_mgr.status.management_ip,
            s_usb_host_mgr.config.espnetlink.management_ip,
            sizeof(s_usb_host_mgr.status.management_ip));

    if (s_usb_host_mgr.espnetlink_started)
    {
        usb_host_manager_refresh_ip_locked();
    }
    else
    {
        s_usb_host_mgr.status.local_ip[0] = '\0';
        s_usb_host_mgr.status.netmask[0] = '\0';
        s_usb_host_mgr.status.gateway[0] = '\0';
    }

    if (!s_usb_host_mgr.config.enabled || s_usb_host_mgr.config.active_device_type == USB_HOST_MANAGER_DEVICE_NONE)
    {
        usb_host_manager_update_state_locked(USB_HOST_MANAGER_STATE_DISABLED);
    }
    else if (s_usb_host_mgr.espnetlink_started)
    {
        usb_host_manager_update_state_locked(USB_HOST_MANAGER_STATE_RUNNING);
    }
    else if (s_usb_host_mgr.status.last_error[0] != '\0' && s_usb_host_mgr.device_present_raw)
    {
        usb_host_manager_update_state_locked(USB_HOST_MANAGER_STATE_ERROR);
    }
    else if (s_usb_host_mgr.device_present_raw)
    {
        usb_host_manager_update_state_locked(USB_HOST_MANAGER_STATE_STARTING);
    }
    else
    {
        usb_host_manager_update_state_locked(USB_HOST_MANAGER_STATE_WAITING_FOR_DEVICE);
    }

    usb_host_manager_unlock();
}

static void usb_host_manager_handle_reload(void)
{
    usb_host_manager_config_t loaded;

    if (usb_host_manager_load_config(&loaded) != ESP_OK)
    {
        return;
    }

    if (!usb_host_manager_lock(pdMS_TO_TICKS(100)))
    {
        return;
    }

    memcpy(&s_usb_host_mgr.config, &loaded, sizeof(loaded));
    usb_host_manager_set_error_locked(NULL);
    usb_host_manager_unlock();

    (void)connection_manager_set_usb_fallback_enabled(s_usb_host_mgr.config.espnetlink.prefer_default_route);
}

static void usb_host_manager_task(void *arg)
{
    usb_host_manager_cmd_t cmd;
    TickType_t wait_ticks;

    (void)arg;

    while (true)
    {
        wait_ticks = pdMS_TO_TICKS(s_usb_host_mgr.config.monitor_interval_ms);
        if (xQueueReceive(s_usb_host_mgr.cmd_queue, &cmd, wait_ticks) == pdPASS)
        {
            if (cmd.type == USB_HOST_MANAGER_CMD_RELOAD)
            {
                usb_host_manager_handle_reload();
            }
        }

        if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
        {
            int64_t now_us;
            bool should_enable;
            bool attach_ready;
            bool detach_ready;

            now_us = esp_timer_get_time();
            usb_host_manager_update_presence_locked();

            if (s_usb_host_mgr.device_present_raw)
            {
                if (s_usb_host_mgr.present_since_us == 0)
                {
                    s_usb_host_mgr.present_since_us = now_us;
                }
                s_usb_host_mgr.absent_since_us = 0;
            }
            else
            {
                if (s_usb_host_mgr.absent_since_us == 0)
                {
                    s_usb_host_mgr.absent_since_us = now_us;
                }
                s_usb_host_mgr.present_since_us = 0;
            }

            should_enable = s_usb_host_mgr.config.enabled &&
                s_usb_host_mgr.config.active_device_type == USB_HOST_MANAGER_DEVICE_ESPNETLINK;

            attach_ready = should_enable && s_usb_host_mgr.device_present_raw &&
                s_usb_host_mgr.present_since_us != 0 &&
                (now_us - s_usb_host_mgr.present_since_us) >=
                    ((int64_t)s_usb_host_mgr.config.device_attach_delay_ms * 1000);

            detach_ready = !s_usb_host_mgr.device_present_raw &&
                s_usb_host_mgr.absent_since_us != 0 &&
                (now_us - s_usb_host_mgr.absent_since_us) >=
                    ((int64_t)s_usb_host_mgr.config.device_detach_delay_ms * 1000);

            if (!should_enable && s_usb_host_mgr.espnetlink_started)
            {
                usb_host_manager_update_state_locked(USB_HOST_MANAGER_STATE_STOPPING);
                usb_host_manager_unlock();
                usb_host_manager_stop_espnetlink();
                if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
                {
                    s_usb_host_mgr.espnetlink_started = false;
                    usb_host_manager_set_error_locked(NULL);
                    usb_host_manager_unlock();
                }
                usb_host_manager_update_runtime_status();
                (void)connection_manager_request_reconcile();
                continue;
            }

            if (s_usb_host_mgr.espnetlink_started && detach_ready)
            {
                usb_host_manager_update_state_locked(USB_HOST_MANAGER_STATE_STOPPING);
                usb_host_manager_unlock();
                usb_host_manager_stop_espnetlink();
                if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
                {
                    s_usb_host_mgr.espnetlink_started = false;
                    usb_host_manager_unlock();
                }
                usb_host_manager_update_runtime_status();
                (void)connection_manager_request_reconcile();
                continue;
            }

            if (!s_usb_host_mgr.espnetlink_started && attach_ready && now_us >= s_usb_host_mgr.next_start_retry_us)
            {
                esp_err_t ret;

                usb_host_manager_update_state_locked(USB_HOST_MANAGER_STATE_STARTING);
                usb_host_manager_unlock();
                ret = usb_host_manager_start_espnetlink();

                if (usb_host_manager_lock(pdMS_TO_TICKS(100)))
                {
                    if (ret == ESP_OK)
                    {
                        s_usb_host_mgr.espnetlink_started = true;
                        s_usb_host_mgr.next_start_retry_us = 0;
                        usb_host_manager_set_error_locked(NULL);
                    }
                    else
                    {
                        char err_msg[96];

                        s_usb_host_mgr.espnetlink_started = false;
                        s_usb_host_mgr.next_start_retry_us = now_us + ((int64_t)USB_HOST_MANAGER_START_RETRY_MS * 1000);
                        snprintf(err_msg, sizeof(err_msg), "espnetlink start failed: %s", esp_err_to_name(ret));
                        usb_host_manager_set_error_locked(err_msg);
                    }
                    usb_host_manager_unlock();
                }

                usb_host_manager_update_runtime_status();
                (void)connection_manager_request_reconcile();
                continue;
            }

            usb_host_manager_unlock();
        }

        usb_host_manager_update_runtime_status();
    }
}

esp_err_t usb_host_manager_request_reload(void)
{
    usb_host_manager_cmd_t cmd;

    if (!s_usb_host_mgr.initialized || s_usb_host_mgr.cmd_queue == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }

    cmd.type = USB_HOST_MANAGER_CMD_RELOAD;
    if (xQueueSend(s_usb_host_mgr.cmd_queue, &cmd, pdMS_TO_TICKS(50)) != pdPASS)
    {
        return ESP_ERR_TIMEOUT;
    }

    return ESP_OK;
}

esp_err_t usb_host_manager_set_config(const usb_host_manager_config_t *config)
{
    usb_host_manager_config_t tmp;
    usb_host_manager_cmd_t cmd;
    esp_err_t ret;

    if (config == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    memcpy(&tmp, config, sizeof(tmp));
    usb_host_manager_sanitize_config(&tmp);

    ret = usb_host_manager_save_config(&tmp);
    if (ret != ESP_OK)
    {
        return ret;
    }

    if (!usb_host_manager_lock(pdMS_TO_TICKS(100)))
    {
        return ESP_ERR_TIMEOUT;
    }

    memcpy(&s_usb_host_mgr.config, &tmp, sizeof(tmp));
    usb_host_manager_unlock();

    (void)connection_manager_set_usb_fallback_enabled(tmp.espnetlink.prefer_default_route);

    if (s_usb_host_mgr.cmd_queue != NULL)
    {
        cmd.type = USB_HOST_MANAGER_CMD_WAKE;
        (void)xQueueSend(s_usb_host_mgr.cmd_queue, &cmd, 0);
    }

    return ESP_OK;
}

esp_err_t usb_host_manager_get_config(usb_host_manager_config_t *config)
{
    if (config == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (!s_usb_host_mgr.initialized)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (!usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        return ESP_ERR_TIMEOUT;
    }

    memcpy(config, &s_usb_host_mgr.config, sizeof(*config));
    usb_host_manager_unlock();
    return ESP_OK;
}

esp_err_t usb_host_manager_get_status(usb_host_manager_status_t *status)
{
    if (status == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (!s_usb_host_mgr.initialized)
    {
        return ESP_ERR_INVALID_STATE;
    }

    usb_host_manager_update_runtime_status();

    if (!usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        return ESP_ERR_TIMEOUT;
    }

    memcpy(status, &s_usb_host_mgr.status, sizeof(*status));
    usb_host_manager_unlock();
    return ESP_OK;
}

esp_err_t usb_host_manager_init(const usb_host_manager_platform_config_t *platform_config)
{
    usb_host_manager_config_t loaded;
    size_t queue_storage_size;

    ESP_RETURN_ON_FALSE(platform_config != NULL, ESP_ERR_INVALID_ARG, TAG, "platform config is required");

    if (s_usb_host_mgr.initialized)
    {
        return ESP_OK;
    }

    memset(&s_usb_host_mgr, 0, sizeof(s_usb_host_mgr));
    memcpy(&s_usb_host_mgr.platform, platform_config, sizeof(s_usb_host_mgr.platform));

    usb_host_manager_set_default_config(&s_usb_host_mgr.config);
    if (usb_host_manager_load_config(&loaded) == ESP_OK)
    {
        memcpy(&s_usb_host_mgr.config, &loaded, sizeof(loaded));
    }
    else
    {
        (void)usb_host_manager_save_config(&s_usb_host_mgr.config);
    }

    s_usb_host_mgr.lock = xSemaphoreCreateMutexStatic(&s_usb_host_mgr.lock_buffer);
    ESP_RETURN_ON_FALSE(s_usb_host_mgr.lock != NULL, ESP_ERR_NO_MEM, TAG, "failed to create mutex");

    queue_storage_size = USB_HOST_MANAGER_CMD_QUEUE_LEN * sizeof(usb_host_manager_cmd_t);
    s_usb_host_mgr.cmd_queue_storage = (uint8_t *)heap_caps_malloc(queue_storage_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_RETURN_ON_FALSE(s_usb_host_mgr.cmd_queue_storage != NULL, ESP_ERR_NO_MEM, TAG, "failed to allocate queue storage");

    s_usb_host_mgr.cmd_queue = xQueueCreateStatic(USB_HOST_MANAGER_CMD_QUEUE_LEN,
                                                   sizeof(usb_host_manager_cmd_t),
                                                   s_usb_host_mgr.cmd_queue_storage,
                                                   &s_usb_host_mgr.cmd_queue_buffer);
    ESP_RETURN_ON_FALSE(s_usb_host_mgr.cmd_queue != NULL, ESP_ERR_NO_MEM, TAG, "failed to create queue");

    s_usb_host_mgr.task_stack = (StackType_t *)heap_caps_malloc(
        USB_HOST_MANAGER_TASK_STACK_WORDS * sizeof(StackType_t),
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_RETURN_ON_FALSE(s_usb_host_mgr.task_stack != NULL, ESP_ERR_NO_MEM, TAG, "failed to allocate task stack");

    if (s_usb_host_mgr.platform.usb_id_gpio >= 0)
    {
        gpio_reset_pin((gpio_num_t)s_usb_host_mgr.platform.usb_id_gpio);
        gpio_set_direction((gpio_num_t)s_usb_host_mgr.platform.usb_id_gpio, GPIO_MODE_INPUT);
    }

    if (s_usb_host_mgr.platform.usb_mode_gpio >= 0)
    {
        gpio_reset_pin((gpio_num_t)s_usb_host_mgr.platform.usb_mode_gpio);
        gpio_set_direction((gpio_num_t)s_usb_host_mgr.platform.usb_mode_gpio, GPIO_MODE_OUTPUT);
        gpio_set_level((gpio_num_t)s_usb_host_mgr.platform.usb_mode_gpio, 0);
    }

    s_usb_host_mgr.task = xTaskCreateStatic(usb_host_manager_task,
                                            "usb_host_mgr",
                                            USB_HOST_MANAGER_TASK_STACK_WORDS,
                                            NULL,
                                            USB_HOST_MANAGER_TASK_PRIORITY,
                                            s_usb_host_mgr.task_stack,
                                            &s_usb_host_mgr.task_buffer);
    ESP_RETURN_ON_FALSE(s_usb_host_mgr.task != NULL, ESP_ERR_NO_MEM, TAG, "failed to create task");

    s_usb_host_mgr.initialized = true;
    s_usb_host_mgr.status.initialized = true;
    s_usb_host_mgr.status.configured_device_type = s_usb_host_mgr.config.active_device_type;
    strlcpy(s_usb_host_mgr.status.management_ip,
            s_usb_host_mgr.config.espnetlink.management_ip,
            sizeof(s_usb_host_mgr.status.management_ip));
    usb_host_manager_update_runtime_status();
    (void)connection_manager_set_usb_fallback_enabled(s_usb_host_mgr.config.espnetlink.prefer_default_route);

    ESP_LOGI(TAG, "USB host manager initialized for %s", usb_host_manager_device_type_to_str(s_usb_host_mgr.config.active_device_type));
    return ESP_OK;
}