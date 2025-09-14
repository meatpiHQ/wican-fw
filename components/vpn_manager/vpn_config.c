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

#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <esp_log.h>
#include <cJSON.h>
#include <ctype.h>
#include "include/vpn_manager.h"
#include "filesystem.h"
#include <esp_heap_caps.h>

static const char *TAG_CFG = "VPN_CFG";

// PSRAM-backed cache of /vpn_config.json
static char *s_vpn_cfg_json = NULL;
static size_t s_vpn_cfg_json_len = 0; // excludes NUL

static esp_err_t vpn_config_cache_set(const char *data, size_t len)
{
    // Allocate in PSRAM and copy; always NUL-terminate for safety
    char *buf = (char *)heap_caps_malloc(len + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!buf)
    {
        return ESP_ERR_NO_MEM;
    }
    memcpy(buf, data, len);
    buf[len] = '\0';
    if (s_vpn_cfg_json)
    {
        heap_caps_free(s_vpn_cfg_json);
    }
    s_vpn_cfg_json = buf;
    s_vpn_cfg_json_len = len;
    return ESP_OK;
}

esp_err_t vpn_config_preload(void)
{
    // If already cached, nothing to do
    if (s_vpn_cfg_json)
    {
        return ESP_OK;
    }
    FILE *f = fopen(FS_MOUNT_POINT "/vpn_config.json", "r");
    if (!f)
    {
        return ESP_ERR_NOT_FOUND;
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (size <= 0)
    {
        fclose(f);
        return ESP_ERR_INVALID_SIZE;
    }
    char *tmp = (char *)malloc((size_t)size);
    if (!tmp)
    {
        fclose(f);
        return ESP_ERR_NO_MEM;
    }
    size_t r = fread(tmp, 1, (size_t)size, f);
    fclose(f);
    if (r == 0)
    {
        free(tmp);
        return ESP_FAIL;
    }
    esp_err_t err = vpn_config_cache_set(tmp, r);
    free(tmp);
    if (err == ESP_OK)
    {
        ESP_LOGI(TAG_CFG, "vpn_config.json preloaded into PSRAM (%u bytes)", (unsigned)s_vpn_cfg_json_len);
    }
    return err;
}

const char *vpn_config_get_json_ptr(size_t *out_len)
{
    if (out_len)
    {
        *out_len = s_vpn_cfg_json_len;
    }
    return s_vpn_cfg_json;
}

static void trim_str(char *s)
{
    if (!s)
    {
        return;
    }
    char *start = s;
    while (*start==' '||*start=='\t'||*start=='\r'||*start=='\n')
    {
        start++;
    }
    if (start != s)
    {
        memmove(s, start, strlen(start)+1);
    }
    size_t len = strlen(s);
    while (len>0)
    {
        char c = s[len-1];
        if (c==' '||c=='\t'||c=='\r'||c=='\n')
        {
            s[--len]='\0';
        }
        else
        {
            break;
        }
    }
}

esp_err_t vpn_config_save(const vpn_config_t *config)
{
    if (!config)
    {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = cJSON_CreateObject();
    if (!root)
    {
        return ESP_ERR_NO_MEM;
    }
    cJSON_AddNumberToObject(root, "type", config->type);
    cJSON_AddBoolToObject(root, "enabled", config->enabled);
    if (config->type == VPN_TYPE_WIREGUARD)
    {
        cJSON *wg = cJSON_CreateObject();
        if (wg)
        {
            cJSON_AddStringToObject(wg, "private_key", config->config.wireguard.private_key);
            cJSON_AddStringToObject(wg, "peer_public_key", config->config.wireguard.public_key);
            cJSON_AddStringToObject(wg, "address", config->config.wireguard.address);
            // Convert dotted mask to CIDR prefix length
            char allowed_ips_cidr[64];
            int a=0,b=0,c=0,d=0;
            unsigned int pfx = 32; // default
            if (sscanf(config->config.wireguard.allowed_ip_mask, "%d.%d.%d.%d", &a,&b,&c,&d) == 4)
            {
                uint32_t mask = ((uint32_t)(a & 0xFF) << 24) | ((uint32_t)(b & 0xFF) << 16) |
                                ((uint32_t)(c & 0xFF) << 8)  | ((uint32_t)(d & 0xFF));
                if (mask == 0)
                {
                    pfx = 0;
                }
                else
                {
                    // count contiguous 1s from MSB
                    unsigned int count = 0;
                    uint32_t bit = 0x80000000u;
                    while (bit && (mask & bit))
                    {
                        count++;
                        bit >>= 1;
                    }
                    // remaining bits must be zero for a valid netmask
                    if ((mask << count) == 0)
                    {
                        pfx = count;
                    }
                    else
                    {
                        pfx = 32; // fallback on invalid mask
                    }
                }
            }
            snprintf(allowed_ips_cidr, sizeof(allowed_ips_cidr), "%s/%u", config->config.wireguard.allowed_ip, pfx);
            cJSON_AddStringToObject(wg, "allowed_ips", allowed_ips_cidr);
            char endpoint_with_port[96];
            snprintf(endpoint_with_port, sizeof(endpoint_with_port), "%s:%d", config->config.wireguard.endpoint, config->config.wireguard.port);
            cJSON_AddStringToObject(wg, "endpoint", endpoint_with_port);
            cJSON_AddNumberToObject(wg, "persistent_keepalive", config->config.wireguard.persistent_keepalive);
            cJSON_AddItemToObject(root, "wireguard", wg);
        }
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!json)
    {
        return ESP_ERR_NO_MEM;
    }
    FILE *f = fopen(FS_MOUNT_POINT "/vpn_config.json", "w");
    if (!f)
    {
        free(json);
        return ESP_ERR_NOT_FOUND;
    }
    size_t len = strlen(json);
    size_t w = fwrite(json, 1, len, f);
    fclose(f);
    // Update PSRAM cache on successful write
    if (w > 0)
    {
        vpn_config_cache_set(json, len);
    }
    free(json);
    return (w == 0) ? ESP_FAIL : ESP_OK;
}

esp_err_t vpn_config_load(vpn_config_t *config)
{
    if (!config)
    {
        return ESP_ERR_INVALID_ARG;
    }
    // Prefer cached JSON if available; otherwise try to preload (and then use cache)
    const char *json_ptr = s_vpn_cfg_json;
    if (!json_ptr)
    {
        esp_err_t pre = vpn_config_preload();
        if (pre == ESP_OK)
        {
            json_ptr = s_vpn_cfg_json;
        }
    }
    if (!json_ptr)
    {
        return ESP_ERR_NOT_FOUND;
    }
    cJSON *root = cJSON_Parse(json_ptr);
    if (!root)
    {
        return ESP_ERR_INVALID_ARG;
    }
    memset(config, 0, sizeof(*config));
    cJSON *item = cJSON_GetObjectItem(root, "type");
    if (cJSON_IsNumber(item))
    {
        config->type = (vpn_type_t)item->valueint;
    }
    item = cJSON_GetObjectItem(root, "enabled");
    if (cJSON_IsBool(item))
    {
        config->enabled = cJSON_IsTrue(item);
    }
    if (config->type == VPN_TYPE_WIREGUARD)
    {
        cJSON *wg = cJSON_GetObjectItem(root, "wireguard");
        if (cJSON_IsObject(wg))
        {
            cJSON *it;
            it = cJSON_GetObjectItem(wg, "private_key");
            if (cJSON_IsString(it))
            {
                strlcpy(config->config.wireguard.private_key, it->valuestring, sizeof(config->config.wireguard.private_key));
                trim_str(config->config.wireguard.private_key);
            }
            it = cJSON_GetObjectItem(wg, "peer_public_key");
            if (cJSON_IsString(it))
            {
                strlcpy(config->config.wireguard.public_key, it->valuestring, sizeof(config->config.wireguard.public_key));
                trim_str(config->config.wireguard.public_key);
            }
            it = cJSON_GetObjectItem(wg, "address");
            if (cJSON_IsString(it))
            {
                strlcpy(config->config.wireguard.address, it->valuestring, sizeof(config->config.wireguard.address));
                trim_str(config->config.wireguard.address);
                char *slash = strchr(config->config.wireguard.address, '/');
                if (slash)
                {
                    *slash='\0';
                }
            }
            it = cJSON_GetObjectItem(wg, "allowed_ips");
            if (cJSON_IsString(it))
            {
                char buf[64];
                strlcpy(buf, it->valuestring, sizeof(buf));
                trim_str(buf);
                const char *allowed = buf;
                const char *slash = strchr(allowed, '/');
                if (slash)
                {
                    size_t ip_len = slash - allowed;
                    if (ip_len < sizeof(config->config.wireguard.allowed_ip))
                    {
                        strncpy(config->config.wireguard.allowed_ip, allowed, ip_len);
                        config->config.wireguard.allowed_ip[ip_len] = '\0';
                        int prefix = atoi(slash + 1);
                        if (prefix == 0)
                        {
                            strlcpy(config->config.wireguard.allowed_ip_mask, "0.0.0.0", sizeof(config->config.wireguard.allowed_ip_mask));
                        }
                        else if (prefix == 8)
                        {
                            strlcpy(config->config.wireguard.allowed_ip_mask, "255.0.0.0", sizeof(config->config.wireguard.allowed_ip_mask));
                        }
                        else if (prefix == 16)
                        {
                            strlcpy(config->config.wireguard.allowed_ip_mask, "255.255.0.0", sizeof(config->config.wireguard.allowed_ip_mask));
                        }
                        else if (prefix == 24)
                        {
                            strlcpy(config->config.wireguard.allowed_ip_mask, "255.255.255.0", sizeof(config->config.wireguard.allowed_ip_mask));
                        }
                        else
                        {
                            strlcpy(config->config.wireguard.allowed_ip_mask, "255.255.255.255", sizeof(config->config.wireguard.allowed_ip_mask));
                        }
                    }
                }
                else
                {
                    strlcpy(config->config.wireguard.allowed_ip, allowed, sizeof(config->config.wireguard.allowed_ip));
                    strlcpy(config->config.wireguard.allowed_ip_mask, "255.255.255.255", sizeof(config->config.wireguard.allowed_ip_mask));
                }
            }
            it = cJSON_GetObjectItem(wg, "endpoint");
            if (cJSON_IsString(it))
            {
                char endpoint_buf[96];
                strlcpy(endpoint_buf, it->valuestring, sizeof(endpoint_buf));
                trim_str(endpoint_buf);
                const char *endpoint = endpoint_buf;
                const char *colon = strrchr(endpoint, ':');
                if (colon)
                {
                    size_t host_len = colon - endpoint;
                    if (host_len < sizeof(config->config.wireguard.endpoint))
                    {
                        strncpy(config->config.wireguard.endpoint, endpoint, host_len);
                        config->config.wireguard.endpoint[host_len] = '\0';
                        config->config.wireguard.port = atoi(colon + 1);
                    }
                }
                else
                {
                    strlcpy(config->config.wireguard.endpoint, endpoint, sizeof(config->config.wireguard.endpoint));
                    config->config.wireguard.port = 51820;
                }
            }
            it = cJSON_GetObjectItem(wg, "persistent_keepalive");
            if (cJSON_IsNumber(it))
            {
                config->config.wireguard.persistent_keepalive = it->valueint;
            }
        }
    }
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t vpn_config_parse_wg(const char *config_text, vpn_wireguard_config_t *config)
{
    if (!config_text || !config)
    {
        return ESP_ERR_INVALID_ARG;
    }
    memset(config, 0, sizeof(*config));
    char *copy = strdup(config_text);
    if (!copy)
    {
        return ESP_ERR_NO_MEM;
    }
    char *line = strtok(copy, "\r\n");
    char section[32] = {0};
    while (line)
    {
        while (*line==' '||*line=='\t')
        {
            line++;
        }
        size_t len = strlen(line);
        while (len>0 && (line[len-1]==' '||line[len-1]=='\t'))
        {
            line[--len] = '\0';
        }
        if (len==0 || line[0]=='#')
        {
            line=strtok(NULL, "\r\n");
            continue;
        }
        if (line[0]=='[' && line[len-1]==']')
        {
            line[len-1] = '\0';
            strlcpy(section, line+1, sizeof(section));
            for (int i=0; section[i]; ++i)
            {
                section[i] = (char)tolower((unsigned char)section[i]);
            }
        }
        else
        {
            char *eq = strchr(line, '=');
            if (eq)
            {
                *eq='\0';
                char *key = line;
                char *value = eq+1;
                while(*key==' '||*key=='\t') key++;
                while(*value==' '||*value=='\t') value++;
                for (int i=0; key[i]; ++i) key[i] = (char)tolower((unsigned char)key[i]);
                if (strcmp(section, "interface")==0)
                {
                    if (strcmp(key, "privatekey")==0)
                    {
                        strlcpy(config->private_key, value, sizeof(config->private_key));
                    }
                    else if (strcmp(key, "address")==0)
                    {
                        strlcpy(config->address, value, sizeof(config->address));
                    }
                }
                else if (strcmp(section, "peer")==0)
                {
                    if (strcmp(key, "publickey")==0)
                    {
                        strlcpy(config->public_key, value, sizeof(config->public_key));
                    }
                    else if (strcmp(key, "allowedips")==0)
                    {
                        char *slash = strchr(value,'/');
                        if (slash)
                        {
                            *slash='\0';
                            strlcpy(config->allowed_ip, value, sizeof(config->allowed_ip));
                            int prefix = atoi(slash+1);
                            if (prefix==0)
                            {
                                strlcpy(config->allowed_ip_mask,"0.0.0.0",sizeof(config->allowed_ip_mask));
                            }
                            else if (prefix==8)
                            {
                                strlcpy(config->allowed_ip_mask,"255.0.0.0",sizeof(config->allowed_ip_mask));
                            }
                            else if (prefix==16)
                            {
                                strlcpy(config->allowed_ip_mask,"255.255.0.0",sizeof(config->allowed_ip_mask));
                            }
                            else if (prefix==24)
                            {
                                strlcpy(config->allowed_ip_mask,"255.255.255.0",sizeof(config->allowed_ip_mask));
                            }
                            else
                            {
                                strlcpy(config->allowed_ip_mask,"255.255.255.255",sizeof(config->allowed_ip_mask));
                            }
                        }
                        else
                        {
                            strlcpy(config->allowed_ip, value, sizeof(config->allowed_ip));
                            strlcpy(config->allowed_ip_mask, "255.255.255.255", sizeof(config->allowed_ip_mask));
                        }
                    }
                    else if (strcmp(key, "endpoint")==0)
                    {
                        char *colon = strrchr(value,':');
                        if (colon)
                        {
                            *colon='\0';
                            strlcpy(config->endpoint, value, sizeof(config->endpoint));
                            config->port = atoi(colon+1);
                        }
                        else
                        {
                            strlcpy(config->endpoint, value, sizeof(config->endpoint));
                            config->port = 51820;
                        }
                    }
                    else if (strcmp(key, "persistentkeepalive")==0)
                    {
                        config->persistent_keepalive = atoi(value);
                    }
                }
            }
        }
        line = strtok(NULL, "\r\n");
    }
    free(copy);
    return ESP_OK;
}

#include "vpn_keygen.h"

esp_err_t vpn_config_generate_wg_keys(char *public_key, size_t public_key_size)
{
    if (public_key == NULL || public_key_size < 64)
    {
        return ESP_ERR_INVALID_ARG;
    }

    // Generate Base64 private/public keys
    char priv_b64[64] = {0};
    char pub_b64[64] = {0};
    esp_err_t err = vpn_keygen_generate_wireguard_keys(priv_b64, sizeof(priv_b64), pub_b64, sizeof(pub_b64));
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG_CFG, "WireGuard keygen failed: %s", esp_err_to_name(err));
        return err;
    }

    // Load existing config, update private key, save back
    vpn_config_t cfg = {0};
    if (vpn_config_load(&cfg) != ESP_OK)
    {
        // Initialize minimal WG config if none exists
        cfg.type = VPN_TYPE_WIREGUARD;
        cfg.enabled = false;
    }
    strlcpy(cfg.config.wireguard.private_key, priv_b64, sizeof(cfg.config.wireguard.private_key));
    // Do not overwrite peer_public_key here. Only return our public.

    esp_err_t save_r = vpn_config_save(&cfg);
    if (save_r != ESP_OK)
    {
        ESP_LOGW(TAG_CFG, "Failed to persist private key: %s", esp_err_to_name(save_r));
        // Still return the public key so UI can proceed; but surface error upstream
        // Fall through to return save_r so HTTP layer can show error
    }

    strlcpy(public_key, pub_b64, public_key_size);
    return save_r == ESP_OK ? ESP_OK : save_r;
}
