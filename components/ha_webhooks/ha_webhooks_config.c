 /* This file is part of the WiCAN project.
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

#include "ha_webhooks.h"
#include <string.h>
#include <stdio.h>
#include <cJSON.h>
#include <esp_err.h>
#include <esp_log.h>
#include "filesystem.h"

static const char *TAG = "HA_WEBHOOK_CFG";

/**
 * @brief Trim leading and trailing whitespace from a string
 *
 * Modifies the string in-place to remove spaces, tabs, carriage returns,
 * and newlines from both ends.
 *
 * @param[in,out] s String to trim (modified in-place)
 */
static void trim_str(char *s)
{
    if (!s)
        return;
    char *start = s;
    while (*start == ' ' || *start == '\t' || *start == '\r' || *start == '\n')
        start++;
    if (start != s)
        memmove(s, start, strlen(start) + 1);
    size_t len = strlen(s);
    while (len > 0)
    {
        char c = s[len - 1];
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n')
        {
            s[--len] = '\0';
        }
        else
            break;
    }
}

esp_err_t ha_webhook_load_config(ha_webhook_config_t *cfg)
{
    ESP_LOGI(TAG, "Loading webhook configuration from filesystem");

    if (!cfg)
    {
        ESP_LOGE(TAG, "Invalid argument: cfg is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    memset(cfg, 0, sizeof(*cfg));
    FILE *f = fopen(FS_MOUNT_POINT "/ha_webhook.json", "r");
    if (!f)
    {
        ESP_LOGW(TAG, "Config file not found: %s/ha_webhook.json", FS_MOUNT_POINT);
        return ESP_ERR_NOT_FOUND;
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);

    if (size <= 0)
    {
        ESP_LOGE(TAG, "Config file is empty or invalid size: %ld", size);
        fclose(f);
        return ESP_ERR_INVALID_SIZE;
    }

    ESP_LOGD(TAG, "Config file size: %ld bytes", size);

    char *buf = (char *)malloc((size_t)size + 1);
    if (!buf)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for config file (%ld bytes)", size);
        fclose(f);
        return ESP_ERR_NO_MEM;
    }

    size_t r = fread(buf, 1, (size_t)size, f);
    fclose(f);

    if (r == 0)
    {
        ESP_LOGE(TAG, "Failed to read config file");
        free(buf);
        return ESP_FAIL;
    }

    buf[r] = '\0';
    cJSON *root = cJSON_Parse(buf);
    free(buf);

    if (!root)
    {
        ESP_LOGE(TAG, "Failed to parse JSON config");
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *it;

    // Parse URL
    it = cJSON_GetObjectItem(root, "url");
    if (cJSON_IsString(it))
    {
        strlcpy(cfg->url, it->valuestring, sizeof(cfg->url));
        trim_str(cfg->url);
        ESP_LOGD(TAG, "Loaded webhook URL: %s", cfg->url);
    }

    // Parse enabled flag
    it = cJSON_GetObjectItem(root, "enabled");
    if (cJSON_IsBool(it))
    {
        cfg->enabled = cJSON_IsTrue(it);
        ESP_LOGD(TAG, "Webhook enabled: %s", cfg->enabled ? "yes" : "no");
    }

    // Parse interval
    it = cJSON_GetObjectItem(root, "interval");
    if (cJSON_IsNumber(it))
    {
        cfg->interval = it->valueint;
        ESP_LOGD(TAG, "Interval: %d", cfg->interval);
    }

    // Parse last_post timestamp
    it = cJSON_GetObjectItem(root, "last_post");
    if (cJSON_IsString(it))
    {
        strlcpy(cfg->last_post, it->valuestring, sizeof(cfg->last_post));
        ESP_LOGD(TAG, "Last POST: %s", cfg->last_post);
    }

    // Parse status
    it = cJSON_GetObjectItem(root, "status");
    if (cJSON_IsString(it))
    {
        strlcpy(cfg->status, it->valuestring, sizeof(cfg->status));
        ESP_LOGD(TAG, "Webhook status: %s", cfg->status);
    }

    // Parse retries count
    it = cJSON_GetObjectItem(root, "retries");
    if (cJSON_IsNumber(it))
    {
        cfg->retries = it->valueint;
        ESP_LOGD(TAG, "Retry count: %d", cfg->retries);
    }

    cJSON_Delete(root);
    ESP_LOGI(TAG, "Webhook configuration loaded successfully");
    return ESP_OK;
}

esp_err_t ha_webhook_save_config(const ha_webhook_config_t *cfg)
{
    ESP_LOGI(TAG, "Saving webhook configuration to filesystem");

    if (!cfg)
    {
        ESP_LOGE(TAG, "Invalid argument: cfg is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGD(TAG, "Config - URL: %s, Enabled: %s, Status: %s, Retries: %d",
             cfg->url, cfg->enabled ? "yes" : "no",
             cfg->status[0] ? cfg->status : "unknown", cfg->retries);

    cJSON *root = cJSON_CreateObject();
    if (!root)
    {
        ESP_LOGE(TAG, "Failed to create JSON object");
        return ESP_ERR_NO_MEM;
    }

    cJSON_AddStringToObject(root, "url", cfg->url);
    cJSON_AddBoolToObject(root, "enabled", cfg->enabled);
    cJSON_AddStringToObject(root, "last_post", cfg->last_post);
    cJSON_AddStringToObject(root, "status", cfg->status[0] ? cfg->status : "unknown");
    cJSON_AddNumberToObject(root, "retries", cfg->retries);
    cJSON_AddNumberToObject(root, "interval", cfg->interval);

    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);

    if (!json)
    {
        ESP_LOGE(TAG, "Failed to serialize JSON");
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGD(TAG, "Writing config to file: %s/ha_webhook.json", FS_MOUNT_POINT);

    FILE *f = fopen(FS_MOUNT_POINT "/ha_webhook.json", "w");
    if (!f)
    {
        ESP_LOGE(TAG, "Failed to open config file for writing");
        free(json);
        return ESP_ERR_NOT_FOUND;
    }

    size_t len = strlen(json);
    size_t w = fwrite(json, 1, len, f);
    fclose(f);
    free(json);

    if (w == 0)
    {
        ESP_LOGE(TAG, "Failed to write config to file");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Webhook configuration saved successfully (%zu bytes)", w);
    return ESP_OK;
}
