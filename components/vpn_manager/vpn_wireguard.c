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

#include "vpn_wireguard.h"
#include <string.h>
#include <esp_log.h>
#include <esp_netif.h>
#include <esp_wireguard.h>

static const char *TAG_WG = "VPN_WG";

static wireguard_ctx_t s_wg_ctx = {0};
static wireguard_config_t s_wg_cfg = {0};
static esp_netif_t *s_vpn_netif = NULL; 
static char s_addr_derived[32] = {0};

#define EMPTY_OR_NULL(s) ((s)==NULL || (s)[0]=='\0')

esp_err_t vpn_wg_init(const vpn_wireguard_config_t *cfg)
{
    if (s_wg_ctx.config != NULL)
    {
        ESP_LOGW(TAG_WG, "Already initialized");
        return ESP_OK;
    }

    memset(&s_wg_cfg, 0, sizeof(s_wg_cfg));
    if (cfg->private_key[0])
    {
        s_wg_cfg.private_key = (char*)cfg->private_key;
    }
    if (cfg->public_key[0])
    {
        s_wg_cfg.public_key = (char*)cfg->public_key;
    }
    // esp_wireguard uses allowed_ip/mask as LOCAL tunnel IP/mask. If user provided AllowedIPs=0.0.0.0/0,
    // derive local IP from Interface Address instead.
    bool have_allowed = (cfg->allowed_ip[0] && strcmp(cfg->allowed_ip, "0.0.0.0") != 0);
    if (have_allowed)
    {
        s_wg_cfg.allowed_ip = (char*)cfg->allowed_ip;
        if (cfg->allowed_ip_mask[0])
        {
            s_wg_cfg.allowed_ip_mask = (char*)cfg->allowed_ip_mask;
        }
    }
    else
    {
        if (cfg->address[0])
        {
            // Strip any trailing CIDR (e.g., "/32") from Address before using as local IP
            strlcpy(s_addr_derived, cfg->address, sizeof(s_addr_derived));
            char *slash = strchr(s_addr_derived, '/');
            if (slash)
            {
                *slash = '\0';
            }
            s_wg_cfg.allowed_ip = (char*)s_addr_derived;
            // default to /32 for point-to-point tunnel
            s_wg_cfg.allowed_ip_mask = "255.255.255.255";
            ESP_LOGI(TAG_WG, "Deriving local WG IP from address %s/32", s_addr_derived);
        }
    }
    if (cfg->endpoint[0])
    {
        s_wg_cfg.endpoint = (char*)cfg->endpoint;
    }
    s_wg_cfg.port = cfg->port;
    s_wg_cfg.persistent_keepalive = cfg->persistent_keepalive;
    s_wg_cfg.listen_port = 0;
    s_wg_cfg.fw_mark = 0;

    // Log all config parameters
    ESP_LOGD(TAG_WG, "WireGuard config:");
    ESP_LOGD(TAG_WG, "  private_key: %s", EMPTY_OR_NULL(s_wg_cfg.private_key) ? "<empty>" : s_wg_cfg.private_key);
    ESP_LOGD(TAG_WG, "  public_key: %s", EMPTY_OR_NULL(s_wg_cfg.public_key) ? "<empty>" : s_wg_cfg.public_key);
    ESP_LOGD(TAG_WG, "  allowed_ip: %s", EMPTY_OR_NULL(s_wg_cfg.allowed_ip) ? "<empty>" : s_wg_cfg.allowed_ip);
    ESP_LOGD(TAG_WG, "  allowed_ip_mask: %s", EMPTY_OR_NULL(s_wg_cfg.allowed_ip_mask) ? "<empty>" : s_wg_cfg.allowed_ip_mask);
    ESP_LOGD(TAG_WG, "  endpoint: %s", EMPTY_OR_NULL(s_wg_cfg.endpoint) ? "<empty>" : s_wg_cfg.endpoint);
    ESP_LOGD(TAG_WG, "  port: %d", s_wg_cfg.port);
    ESP_LOGD(TAG_WG, "  persistent_keepalive: %d", s_wg_cfg.persistent_keepalive);

    s_wg_ctx.config = &s_wg_cfg;
    s_wg_ctx.netif = NULL; // let library create
    s_wg_ctx.netif_default = NULL;

    esp_err_t ret = esp_wireguard_init(&s_wg_cfg, &s_wg_ctx);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG_WG, "esp_wireguard_init failed: %s", esp_err_to_name(ret));
        if (s_vpn_netif)
        {
            esp_netif_destroy(s_vpn_netif);
        }
        s_vpn_netif = NULL;
        memset(&s_wg_ctx, 0, sizeof(s_wg_ctx));
        memset(&s_wg_cfg, 0, sizeof(s_wg_cfg));
        return ret;
    }
    ESP_LOGI(TAG_WG, "Initialized");
    return ESP_OK;
}

esp_err_t vpn_wg_deinit(void)
{
    if (s_wg_ctx.config == NULL)
    {
        return ESP_OK;
    }
    if (s_wg_ctx.netif)
    {
        esp_err_t r = esp_wireguard_disconnect(&s_wg_ctx);
        if (r != ESP_OK)
        {
            ESP_LOGW(TAG_WG, "disconnect failed: %s", esp_err_to_name(r));
        }
    }
    s_vpn_netif = NULL; // library handles netif cleanup
    memset(&s_wg_ctx, 0, sizeof(s_wg_ctx));
    memset(&s_wg_cfg, 0, sizeof(s_wg_cfg));
    ESP_LOGI(TAG_WG, "Deinitialized");
    return ESP_OK;
}

esp_err_t vpn_wg_start(void)
{
    if (s_wg_ctx.config == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t ret = esp_wireguard_connect(&s_wg_ctx);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG_WG, "connect failed: %s", esp_err_to_name(ret));
        return ret;
    }
    ret = esp_wireguard_set_default(&s_wg_ctx);
    if (ret != ESP_OK)
    {
        ESP_LOGW(TAG_WG, "set_default failed: %s", esp_err_to_name(ret));
    }
    ESP_LOGI(TAG_WG, "Started");
    return ESP_OK;
}

esp_err_t vpn_wg_stop(void)
{
    if (s_wg_ctx.config == NULL)
    {
        return ESP_OK;
    }
    if (s_wg_ctx.netif)
    {
        esp_err_t ret = esp_wireguard_disconnect(&s_wg_ctx);
        if (ret != ESP_OK)
        {
            ESP_LOGW(TAG_WG, "disconnect failed: %s", esp_err_to_name(ret));
        }
    }
    ESP_LOGI(TAG_WG, "Stopped");
    return ESP_OK;
}

bool vpn_wg_is_peer_up(void)
{
    // Avoid calling into esp_wireguard when not initialized or not connected
    if (s_wg_ctx.config == NULL || s_wg_ctx.netif == NULL)
    {
        return false;
    }
    return esp_wireguardif_peer_is_up(&s_wg_ctx) == ESP_OK;
}

esp_err_t vpn_wg_set_default_route(void)
{
    if (s_wg_ctx.config == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }
    return esp_wireguard_set_default(&s_wg_ctx);
}
