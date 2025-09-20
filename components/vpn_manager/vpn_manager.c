/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
 * Written by Ali Slim <ali@meatpi.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#include "vpn_manager.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <esp_log.h>
#include <esp_wifi.h>
#include <esp_netif.h>
#include <freertos/task.h>
#include <freertos/timers.h>
#include <freertos/queue.h>
#include <esp_wireguard.h>
#include <esp_heap_caps.h>
#include <cJSON.h>
#include "filesystem.h"
#include "vpn_wireguard.h"
#include "vpn_config.h"
#include <time.h>
#include "dev_status.h"
#include "esp_system.h"

static const char *TAG = "VPN_MANAGER";

// Global variables
static EventGroupHandle_t vpn_event_group = NULL;
static StaticEventGroup_t s_vpn_event_group_bss;
static vpn_config_t current_config = {0};
static vpn_status_t current_status = VPN_STATUS_DISABLED;
static esp_netif_t *vpn_netif = NULL;
// Legacy reconnect timer no longer used; reconnection handled by VPN task backoff
static TimerHandle_t reconnect_timer = NULL;

// VPN task/command infrastructure
typedef enum
{
    VPN_CMD_ENABLE,
    VPN_CMD_RELOAD,
    VPN_CMD_TEST
} vpn_cmd_type_t;

typedef struct
{
    vpn_cmd_type_t type;
    bool enabled; // for VPN_CMD_ENABLE
} vpn_cmd_msg_t;

static TaskHandle_t s_vpn_task = NULL;
static QueueHandle_t s_vpn_cmd_q = NULL;
static StaticQueue_t s_vpn_cmd_q_struct;
static uint8_t s_vpn_cmd_q_storage[8 * sizeof(vpn_cmd_msg_t)];
static uint32_t s_backoff_ms = 0;
static uint32_t s_backoff_cap_ms = 60000; // 60s cap
static uint32_t s_backoff_base_ms = 2000; // 2s start

// Use PSRAM for VPN task stack when available
#define VPN_TASK_STACK_WORDS  (4096)
#define VPN_TASK_PRIORITY     (5)
static StackType_t *s_vpn_task_stack = NULL;   // allocated from PSRAM
static StaticTask_t s_vpn_task_tcb;            // kept internal (BSS)

static void vpn_task_fn(void *arg);
static void vpn_backoff_reset(void)
{
    s_backoff_ms = 0;
}
static void vpn_backoff_bump(void)
{
    if (s_backoff_ms == 0)
    {
        s_backoff_ms = s_backoff_base_ms;
    }
    else
    {
        s_backoff_ms = s_backoff_ms * 2;
    }
    if (s_backoff_ms > s_backoff_cap_ms)
    {
        s_backoff_ms = s_backoff_cap_ms;
    }
    // add +-15% jitter
    uint32_t jitter = (s_backoff_ms * 15) / 100;
    s_backoff_ms = s_backoff_ms - (jitter / 2) + (esp_random() % (jitter + 1));
}

// Private function declarations
static void vpn_reconnect_timer_callback(TimerHandle_t xTimer);
static void vpn_manager_update_status(void);
static esp_err_t vpn_manager_validate_wireguard_config(const vpn_wireguard_config_t *cfg);

esp_err_t vpn_manager_init(void)
{
    esp_err_t ret = ESP_OK;

    ESP_LOGI(TAG, "Initializing VPN Manager");

    // Create event group
    if (vpn_event_group == NULL)
    {
        vpn_event_group = xEventGroupCreateStatic(&s_vpn_event_group_bss);
        if (vpn_event_group == NULL)
        {
            ESP_LOGE(TAG, "Failed to create VPN event group");
            return ESP_ERR_NO_MEM;
        }
    }

    // Create command queue and VPN task
    if (!s_vpn_cmd_q)
    {
        s_vpn_cmd_q = xQueueCreateStatic(8, sizeof(vpn_cmd_msg_t), s_vpn_cmd_q_storage, &s_vpn_cmd_q_struct);
        if (!s_vpn_cmd_q)
        {
            ESP_LOGE(TAG, "Failed to create VPN command queue");
            return ESP_ERR_NO_MEM;
        }
    }
    if (!s_vpn_task)
    {
        // Try to allocate the task stack from PSRAM
        size_t stack_bytes = VPN_TASK_STACK_WORDS * sizeof(StackType_t);
        s_vpn_task_stack = (StackType_t *)heap_caps_malloc(stack_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_vpn_task_stack)
        {
            ESP_LOGI(TAG, "Creating VPN task with PSRAM stack (%u bytes)", (unsigned)stack_bytes);
            s_vpn_task = xTaskCreateStatic(
                vpn_task_fn,
                "vpn_task",
                VPN_TASK_STACK_WORDS,
                NULL,
                VPN_TASK_PRIORITY,
                s_vpn_task_stack,
                &s_vpn_task_tcb);
            if (s_vpn_task == NULL)
            {
                ESP_LOGE(TAG, "xTaskCreateStatic failed even with PSRAM stack");
                // Fallback to normal dynamic create (internal heap)
                free(s_vpn_task_stack);
                s_vpn_task_stack = NULL;
            }
        }

        if (!s_vpn_task)
        {
            ESP_LOGE(TAG, "No PSRAM or failed to allocate PSRAM stack, creating VPN task with internal heap");
            return ESP_ERR_NO_MEM;
        }
    }

        // Load config at startup
    vpn_config_t loaded = {0};
    if (vpn_manager_load_config(&loaded) == ESP_OK)
    {
        memcpy(&current_config, &loaded, sizeof(current_config));
        ESP_LOGI(TAG, "VPN config loaded at task start");
    }
    // Initialize current status
    current_status = VPN_STATUS_DISABLED;
    xEventGroupSetBits(vpn_event_group, VPN_DISCONNECTED_BIT);

    // Preload JSON config into PSRAM once (avoids reopening file repeatedly)
    esp_err_t pre = vpn_config_preload();
    if (pre != ESP_OK)
    {
        ESP_LOGW(TAG, "vpn_config_preload failed: %s", esp_err_to_name(pre));
    }

    // Load configuration from (cached) JSON
    vpn_config_t loaded_config = {0};
    if (vpn_manager_load_config(&loaded_config) == ESP_OK)
    {
        memcpy(&current_config, &loaded_config, sizeof(vpn_config_t));
        ESP_LOGI(TAG, "Loaded VPN configuration");

        // Auto-start if enabled
        if (current_config.enabled && current_config.type == VPN_TYPE_WIREGUARD)
        {
            // ESP_LOGI(TAG, "Auto-starting VPN");
            // ret = vpn_manager_start(&current_config);
        }
    }

    ESP_LOGI(TAG, "VPN Manager initialized");
    return ret;
}

// Note: explicit deinit removed; app lifetime manages the task

esp_err_t vpn_manager_start(const vpn_config_t *config)
{
    if (config == NULL)
    {
        ESP_LOGE(TAG, "VPN config is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGI(TAG, "Starting VPN connection, type: %d", config->type);

    // Stop any existing connection
    vpn_manager_stop();

    // Copy configuration
    memcpy(&current_config, config, sizeof(vpn_config_t));

    current_status = VPN_STATUS_CONNECTING;
    xEventGroupClearBits(vpn_event_group, VPN_CONNECTED_BIT | VPN_DISCONNECTED_BIT | VPN_ERROR_BIT);
    xEventGroupSetBits(vpn_event_group, VPN_CONNECTING_BIT);

    esp_err_t ret = ESP_OK;
    switch (config->type)
    {
        case VPN_TYPE_WIREGUARD:
        {
            // Validate config before attempting to init/connect to avoid NULL strings
            esp_err_t vret = vpn_manager_validate_wireguard_config(&config->config.wireguard);
            if (vret != ESP_OK)
            {
                ESP_LOGE(TAG, "WireGuard config invalid; aborting start");
                ret = vret;
                break;
            }
            ret = vpn_wg_init(&config->config.wireguard);
            if (ret == ESP_OK)
            {
                ret = vpn_wg_start();
            }
            break;
        }
        case VPN_TYPE_DISABLED:
        default:
            ESP_LOGW(TAG, "Invalid VPN type: %d", config->type);
            ret = ESP_ERR_NOT_SUPPORTED;
            break;
    }

    if (ret != ESP_OK)
    {
        current_status = VPN_STATUS_ERROR;
        xEventGroupClearBits(vpn_event_group, VPN_CONNECTING_BIT);
        xEventGroupSetBits(vpn_event_group, VPN_ERROR_BIT);
        ESP_LOGE(TAG, "Failed to start VPN: %s", esp_err_to_name(ret));
    }

    return ret;
}

// Ensure all mandatory fields are present/non-empty before starting WireGuard
static esp_err_t vpn_manager_validate_wireguard_config(const vpn_wireguard_config_t *cfg)
{
    if (cfg == NULL)
    {
        ESP_LOGE(TAG, "WG config is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    // Helper to check empty strings safely
    #define EMPTY_OR_NULL(s) ((s)==NULL || (s)[0]=='\0')

    bool ok = true;
    if (EMPTY_OR_NULL(cfg->private_key))
    {
        ESP_LOGE(TAG, "WG private_key is empty");
        ok = false;
    }
    if (EMPTY_OR_NULL(cfg->public_key))
    {
        ESP_LOGE(TAG, "WG public_key is empty");
        ok = false;
    }
    if (EMPTY_OR_NULL(cfg->address))
    {
        ESP_LOGE(TAG, "WG address is empty");
        ok = false;
    }
    // allowed_ip/mask are used as local tunnel IP/mask by esp_wireguard.
    // Permit empty or 0.0.0.0 if address is provided (we'll derive /32 from address).
    bool allowed_empty = EMPTY_OR_NULL(cfg->allowed_ip) || strcmp(cfg->allowed_ip, "0.0.0.0") == 0;
    if (allowed_empty && EMPTY_OR_NULL(cfg->address))
    {
        ESP_LOGE(TAG, "WG allowed_ip is empty and no address to derive from");
        ok = false;
    }
    if (!allowed_empty && EMPTY_OR_NULL(cfg->allowed_ip_mask))
    {
        ESP_LOGE(TAG, "WG allowed_ip_mask is empty");
        ok = false;
    }
    if (EMPTY_OR_NULL(cfg->endpoint))
    {
        ESP_LOGE(TAG, "WG endpoint is empty");
        ok = false;
    }
    if (cfg->port <= 0)
    {
        ESP_LOGE(TAG, "WG port is invalid: %d", cfg->port);
        ok = false;
    }

    if (!ok)
    {
        ESP_LOGE(TAG, "WireGuard configuration is incomplete. Will not start.");
        return ESP_ERR_INVALID_ARG;
    }

    return ESP_OK;
}

esp_err_t vpn_manager_stop(void)
{
    ESP_LOGI(TAG, "Stopping VPN connection");

    esp_err_t ret = ESP_OK;
    switch (current_config.type)
    {
        case VPN_TYPE_WIREGUARD:
            ret = vpn_wg_stop();
            vpn_wg_deinit();
            break;

        case VPN_TYPE_DISABLED:
        default:
            break;
    }

    current_status = VPN_STATUS_DISCONNECTED;
    xEventGroupClearBits(vpn_event_group, VPN_CONNECTED_BIT | VPN_CONNECTING_BIT | VPN_ERROR_BIT);
    xEventGroupSetBits(vpn_event_group, VPN_DISCONNECTED_BIT);

    return ret;
}

vpn_status_t vpn_manager_get_status(void)
{
    // Update status by checking WireGuard connection
    vpn_manager_update_status();
    return current_status;
}

// Event group handle is now private to manager

esp_err_t vpn_manager_generate_wireguard_keys(char *public_key, size_t public_key_size)
{
    if (public_key == NULL || public_key_size < 64)
    {
        ESP_LOGE(TAG, "Invalid parameters for key generation");
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGI(TAG, "Generating WireGuard key pair (delegated)");
    return vpn_config_generate_wg_keys(public_key, public_key_size);
}

esp_err_t vpn_manager_save_config(const vpn_config_t *config)
{
    if (config == NULL)
    {
        ESP_LOGE(TAG, "Config is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    return vpn_config_save(config);
}

esp_err_t vpn_manager_load_config(vpn_config_t *config)
{
    if (config == NULL)
    {
        ESP_LOGE(TAG, "Config buffer is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    return vpn_config_load(config);
}

// Text parse helper removed from manager (use vpn_config_parse_wg where needed)

// Old blocking test removed; replaced with async command below

esp_err_t vpn_manager_get_ip_address(char *ip_str, size_t ip_str_size)
{
    if (ip_str == NULL || ip_str_size == 0)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (vpn_netif == NULL || current_status != VPN_STATUS_CONNECTED)
    {
        return ESP_ERR_INVALID_STATE;
    }

    esp_netif_ip_info_t ip_info;
    esp_err_t ret = esp_netif_get_ip_info(vpn_netif, &ip_info);
    if (ret == ESP_OK)
    {
        snprintf(ip_str, ip_str_size, IPSTR, IP2STR(&ip_info.ip));
    }

    return ret;
}

// Private function implementations

// Helper function to update VPN status
static void vpn_manager_update_status(void)
{
    if (vpn_wg_is_peer_up())
    {
        if (current_status != VPN_STATUS_CONNECTED)
        {
            current_status = VPN_STATUS_CONNECTED;
            xEventGroupClearBits(vpn_event_group, VPN_CONNECTING_BIT | VPN_DISCONNECTED_BIT | VPN_ERROR_BIT);
            xEventGroupSetBits(vpn_event_group, VPN_CONNECTED_BIT);
            ESP_LOGI(TAG, "VPN connected successfully");
        }
    }
    else
    {
        if (current_status == VPN_STATUS_CONNECTED || current_status == VPN_STATUS_CONNECTING)
        {
            current_status = VPN_STATUS_DISCONNECTED;
            xEventGroupClearBits(vpn_event_group, VPN_CONNECTED_BIT | VPN_CONNECTING_BIT);
            xEventGroupSetBits(vpn_event_group, VPN_DISCONNECTED_BIT);
            ESP_LOGI(TAG, "VPN disconnected");
        }
    }
}

static void vpn_reconnect_timer_callback(TimerHandle_t xTimer)
{
    // Unused: reconnection handled in vpn_task_fn()
    (void)xTimer;
}

// Public control API implemented via command queue and dev_status bits
void vpn_manager_set_enabled(bool enabled)
{
    if (enabled)
    {
        dev_status_set_vpn_enabled();
    }
    else
    {
        dev_status_clear_vpn_enabled();
    }
    if (s_vpn_cmd_q)
    {
        vpn_cmd_msg_t msg = { .type = VPN_CMD_ENABLE, .enabled = enabled };
        xQueueSend(s_vpn_cmd_q, &msg, 0);
    }
}

void vpn_manager_request_reload(void)
{
    if (s_vpn_cmd_q)
    {
        vpn_cmd_msg_t msg = { .type = VPN_CMD_RELOAD };
        xQueueSend(s_vpn_cmd_q, &msg, 0);
    }
}

void vpn_manager_request_test(void)
{
    if (s_vpn_cmd_q)
    {
        vpn_cmd_msg_t msg = { .type = VPN_CMD_TEST };
        xQueueSend(s_vpn_cmd_q, &msg, 0);
    }
}

void vpn_manager_request_test_hardcoded(void)
{
    // Push a RELOAD with temporary hardcoded config then TEST
    // For safety, only if type is WG
    vpn_config_t cfg = {0};
    if (vpn_manager_load_config(&cfg) != ESP_OK)
    {
        cfg.type = VPN_TYPE_WIREGUARD;
        cfg.enabled = true;
    }
    if (cfg.type != VPN_TYPE_WIREGUARD)
    {
        cfg.type = VPN_TYPE_WIREGUARD;
    }
    cfg.enabled = true;
    // Hardcoded values (developer bench). Replace as needed.
    strlcpy(cfg.config.wireguard.private_key, "KEY_HERE", sizeof(cfg.config.wireguard.private_key));
    strlcpy(cfg.config.wireguard.public_key,  "KEY_HERE", sizeof(cfg.config.wireguard.public_key));
    strlcpy(cfg.config.wireguard.address,      "0.0.0.0", sizeof(cfg.config.wireguard.address));
    strlcpy(cfg.config.wireguard.allowed_ip,   "", sizeof(cfg.config.wireguard.allowed_ip)); // derive from address
    strlcpy(cfg.config.wireguard.allowed_ip_mask, "", sizeof(cfg.config.wireguard.allowed_ip_mask));
    strlcpy(cfg.config.wireguard.endpoint,     "0.0.0.0", sizeof(cfg.config.wireguard.endpoint));
    cfg.config.wireguard.port = 51820;
    cfg.config.wireguard.persistent_keepalive = 25;

    vpn_manager_save_config(&cfg);
    vpn_manager_request_reload();
    vpn_manager_set_enabled(true);
    vpn_manager_request_test();
}

// Core VPN task: owns WG ctx; gates on dev_status; backoff on failures
static void vpn_task_fn(void *arg)
{
    (void)arg;
    bool test_once = false;

    const TickType_t tick = pdMS_TO_TICKS(200);
    for (;;)
    {
        // Drain commands
        vpn_cmd_msg_t msg;
        while (s_vpn_cmd_q && xQueueReceive(s_vpn_cmd_q, &msg, 0) == pdTRUE)
        {
            switch (msg.type)
            {
                case VPN_CMD_ENABLE:
                    ESP_LOGI(TAG, "CMD ENABLE: %s", msg.enabled ? "on" : "off");
                    // Nothing else; bit already set/cleared by caller
                    // If disabling, stop immediately
                    if (!msg.enabled)
                    {
                        vpn_manager_stop();
                        vpn_backoff_reset();
                    }
                    break;
                case VPN_CMD_RELOAD:
                {
                    vpn_config_t cfg = {0};
                    if (vpn_manager_load_config(&cfg) == ESP_OK)
                    {
                        current_config = cfg;
                        ESP_LOGI(TAG, "Config reloaded (type=%d, enabled=%d)", cfg.type, (int)cfg.enabled);
                        // If connected and type/params changed, restart
                        if (current_status == VPN_STATUS_CONNECTED || current_status == VPN_STATUS_CONNECTING)
                        {
                            vpn_manager_stop();
                            vpn_backoff_reset();
                        }
                    }
                    else
                    {
                        ESP_LOGW(TAG, "Config reload failed");
                    }
                }
                break;
                case VPN_CMD_TEST:
                    ESP_LOGI(TAG, "CMD TEST: will attempt one-shot connect when gated");
                    test_once = true;
                    vpn_backoff_reset();
                    if (current_status == VPN_STATUS_CONNECTED)
                    {
                        // Force re-handshake
                        vpn_manager_stop();
                    }
                    break;
            }
        }

        // Gating conditions
        bool prereqs = dev_status_are_bits_set(DEV_VPN_ENABLED_BIT | DEV_STA_CONNECTED_BIT | DEV_TIME_SYNCED_BIT);
        bool blockers = dev_status_is_any_bit_set(DEV_AP_ENABLED_BIT | DEV_SLEEP_BIT);

        if (!prereqs || blockers)
        {
            if (current_status == VPN_STATUS_CONNECTED || current_status == VPN_STATUS_CONNECTING)
            {
                ESP_LOGI(TAG, "Gating lost or blocker set; stopping VPN");
                vpn_manager_stop();
            }
            // Idle state while waiting
            vTaskDelay(tick);
            continue;
        }

        // Attempt connect if needed
        if (current_status != VPN_STATUS_CONNECTED && current_status != VPN_STATUS_CONNECTING)
        {
            if (s_backoff_ms == 0 || test_once)
            {
                if (current_config.type == VPN_TYPE_WIREGUARD)
                {
                    esp_err_t vret = vpn_manager_validate_wireguard_config(&current_config.config.wireguard);
                    if (vret != ESP_OK)
                    {
                        // Invalid config: do not retry until reload
                        ESP_LOGE(TAG, "Invalid WG config; waiting for reload");
                        // Sleep a bit to avoid tight loop
                        vTaskDelay(pdMS_TO_TICKS(2000));
                    }
                    else
                    {
                        ESP_LOGI(TAG, "Connecting VPN...");
                        esp_err_t sret = vpn_manager_start(&current_config);
                        if (sret != ESP_OK)
                        {
                            ESP_LOGW(TAG, "Connect failed: %s", esp_err_to_name(sret));
                            if (!test_once)
                            {
                                vpn_backoff_bump();
                            }
                            else
                            {
                                test_once = false; // single attempt
                            }
                        }
                        else
                        {
                            vpn_backoff_reset();
                            if (test_once)
                            {
                                test_once = false;
                            }
                        }
                    }
                }
            }
        }
        else if (current_status == VPN_STATUS_CONNECTED)
        {
            // Monitor link; if peer down, trigger reconnect with backoff
            if (!vpn_wg_is_peer_up())
            {
                ESP_LOGW(TAG, "Peer down; restarting VPN with backoff");
                vpn_manager_stop();
                vpn_backoff_bump();
            }
        }

        // Handle backoff countdown while still responsive to commands
        if (s_backoff_ms > 0)
        {
            uint32_t step = 200;
            if (s_backoff_ms < step)
            {
                step = s_backoff_ms;
            }
            vTaskDelay(pdMS_TO_TICKS(step));
            s_backoff_ms -= step;
        }
        else
        {
            vTaskDelay(tick);
        }
    }
}
