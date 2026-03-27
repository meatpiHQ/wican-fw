#include "usb_host_manager_internal.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sdkconfig.h"

#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

static const char *TAG = "usb_host_cfg";

static char *s_usb_host_cfg_json = NULL;
static size_t s_usb_host_cfg_json_len = 0;

static esp_err_t usb_host_manager_cache_set(const char *data, size_t len)
{
    char *buf;

    buf = (char *)heap_caps_malloc(len + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (buf == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    memcpy(buf, data, len);
    buf[len] = '\0';

    if (s_usb_host_cfg_json != NULL)
    {
        heap_caps_free(s_usb_host_cfg_json);
    }

    s_usb_host_cfg_json = buf;
    s_usb_host_cfg_json_len = len;
    return ESP_OK;
}

static void usb_host_manager_json_add_string(cJSON *obj, const char *name, const char *value)
{
    if (obj == NULL || name == NULL || value == NULL)
    {
        return;
    }

    cJSON_AddStringToObject(obj, name, value);
}

static void usb_host_manager_parse_string(const cJSON *obj, const char *name, char *dst, size_t dst_len)
{
    const cJSON *item;

    if (obj == NULL || name == NULL || dst == NULL || dst_len == 0)
    {
        return;
    }

    item = cJSON_GetObjectItemCaseSensitive((cJSON *)obj, name);
    if (cJSON_IsString(item) && item->valuestring != NULL)
    {
        strlcpy(dst, item->valuestring, dst_len);
    }
}

void usb_host_manager_set_default_config(usb_host_manager_config_t *config)
{
    if (config == NULL)
    {
        return;
    }

    memset(config, 0, sizeof(*config));
    config->enabled = true;
    config->active_device_type = USB_HOST_MANAGER_DEVICE_ESPNETLINK;
    config->monitor_interval_ms = 500;
    config->device_attach_delay_ms = 1500;
    config->device_detach_delay_ms = 250;

    config->espnetlink.enable_cli = true;
    config->espnetlink.prefer_default_route = true;
    config->espnetlink.ip_mode = USB_HOST_MANAGER_IP_MODE_DHCP;
    strlcpy(config->espnetlink.static_ip, "192.168.7.2", sizeof(config->espnetlink.static_ip));
    strlcpy(config->espnetlink.static_netmask, "255.255.255.0", sizeof(config->espnetlink.static_netmask));
    strlcpy(config->espnetlink.static_gw, "192.168.7.1", sizeof(config->espnetlink.static_gw));
    strlcpy(config->espnetlink.management_ip, "192.168.7.1", sizeof(config->espnetlink.management_ip));
    strlcpy(config->espnetlink.cli_dev_path, "/dev/ttyACM0", sizeof(config->espnetlink.cli_dev_path));
    config->espnetlink.cli_assert_dtr = true;
    config->espnetlink.cli_assert_rts = false;
    config->espnetlink.cli_reconnect_delay_ms = 1000;
    config->espnetlink.cli_line_state_delay_ms = 200;
    config->espnetlink.cli_rx_task_stack_size = 4096;
    config->espnetlink.cli_rx_task_priority = 5;
    config->espnetlink.cli_rx_task_core_id = -1;
    config->espnetlink.cli_rx_buffer_size = 2048;
}

void usb_host_manager_sanitize_config(usb_host_manager_config_t *config)
{
    if (config == NULL)
    {
        return;
    }

    if (config->monitor_interval_ms == 0)
    {
        config->monitor_interval_ms = 500;
    }

    if (config->device_attach_delay_ms < 100)
    {
        config->device_attach_delay_ms = 100;
    }

    if (config->espnetlink.management_ip[0] == '\0')
    {
        strlcpy(config->espnetlink.management_ip, "192.168.7.1", sizeof(config->espnetlink.management_ip));
    }

    if (config->espnetlink.cli_dev_path[0] == '\0')
    {
        strlcpy(config->espnetlink.cli_dev_path, "/dev/ttyACM0", sizeof(config->espnetlink.cli_dev_path));
    }

    if (config->espnetlink.cli_reconnect_delay_ms == 0)
    {
        config->espnetlink.cli_reconnect_delay_ms = 1000;
    }

    if (config->espnetlink.cli_line_state_delay_ms == 0)
    {
        config->espnetlink.cli_line_state_delay_ms = 200;
    }

    if (config->espnetlink.cli_rx_task_stack_size == 0)
    {
        config->espnetlink.cli_rx_task_stack_size = 4096;
    }

    if (config->espnetlink.cli_rx_task_priority == 0)
    {
        config->espnetlink.cli_rx_task_priority = 5;
    }

    if (config->espnetlink.cli_rx_buffer_size == 0)
    {
        config->espnetlink.cli_rx_buffer_size = 2048;
    }

    if (config->active_device_type != USB_HOST_MANAGER_DEVICE_NONE &&
        config->active_device_type != USB_HOST_MANAGER_DEVICE_ESPNETLINK &&
        config->active_device_type != USB_HOST_MANAGER_DEVICE_GPS &&
        config->active_device_type != USB_HOST_MANAGER_DEVICE_USB_ETHERNET)
    {
        config->active_device_type = USB_HOST_MANAGER_DEVICE_ESPNETLINK;
    }
}

esp_err_t usb_host_manager_config_preload(void)
{
    FILE *f;
    char *tmp;
    long size;
    size_t read_size;
    esp_err_t err;

    if (s_usb_host_cfg_json != NULL)
    {
        return ESP_OK;
    }

    f = fopen(USB_HOST_MANAGER_CONFIG_PATH, "r");
    if (f == NULL)
    {
        return ESP_ERR_NOT_FOUND;
    }

    fseek(f, 0, SEEK_END);
    size = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (size <= 0)
    {
        fclose(f);
        return ESP_ERR_INVALID_SIZE;
    }

    tmp = (char *)malloc((size_t)size);
    if (tmp == NULL)
    {
        fclose(f);
        return ESP_ERR_NO_MEM;
    }

    read_size = fread(tmp, 1, (size_t)size, f);
    fclose(f);
    if (read_size == 0)
    {
        free(tmp);
        return ESP_FAIL;
    }

    err = usb_host_manager_cache_set(tmp, read_size);
    free(tmp);

    if (err == ESP_OK)
    {
        ESP_LOGI(TAG, "usb_host_config.json preloaded into PSRAM (%u bytes)", (unsigned)s_usb_host_cfg_json_len);
    }

    return err;
}

esp_err_t usb_host_manager_save_config(const usb_host_manager_config_t *config)
{
    cJSON *root;
    cJSON *devices;
    cJSON *espnetlink;
    char *json;
    FILE *f;
    size_t len;
    size_t written;

    if (config == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    root = cJSON_CreateObject();
    if (root == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    cJSON_AddBoolToObject(root, "enabled", config->enabled);
    cJSON_AddStringToObject(root, "active_device_type", usb_host_manager_device_type_to_str(config->active_device_type));
    cJSON_AddNumberToObject(root, "monitor_interval_ms", config->monitor_interval_ms);
    cJSON_AddNumberToObject(root, "device_attach_delay_ms", config->device_attach_delay_ms);
    cJSON_AddNumberToObject(root, "device_detach_delay_ms", config->device_detach_delay_ms);

    devices = cJSON_CreateObject();
    espnetlink = cJSON_CreateObject();
    if (devices == NULL || espnetlink == NULL)
    {
        cJSON_Delete(root);
        if (devices != NULL)
        {
            cJSON_Delete(devices);
        }
        if (espnetlink != NULL)
        {
            cJSON_Delete(espnetlink);
        }
        return ESP_ERR_NO_MEM;
    }

    cJSON_AddBoolToObject(espnetlink, "enable_cli", config->espnetlink.enable_cli);
    cJSON_AddBoolToObject(espnetlink, "prefer_default_route", config->espnetlink.prefer_default_route);
    cJSON_AddStringToObject(espnetlink, "ip_mode",
                            (config->espnetlink.ip_mode == USB_HOST_MANAGER_IP_MODE_STATIC) ? "static" : "dhcp");
    usb_host_manager_json_add_string(espnetlink, "static_ip", config->espnetlink.static_ip);
    usb_host_manager_json_add_string(espnetlink, "static_netmask", config->espnetlink.static_netmask);
    usb_host_manager_json_add_string(espnetlink, "static_gw", config->espnetlink.static_gw);
    usb_host_manager_json_add_string(espnetlink, "management_ip", config->espnetlink.management_ip);
    usb_host_manager_json_add_string(espnetlink, "cli_dev_path", config->espnetlink.cli_dev_path);
    cJSON_AddBoolToObject(espnetlink, "cli_assert_dtr", config->espnetlink.cli_assert_dtr);
    cJSON_AddBoolToObject(espnetlink, "cli_assert_rts", config->espnetlink.cli_assert_rts);
    cJSON_AddNumberToObject(espnetlink, "cli_reconnect_delay_ms", config->espnetlink.cli_reconnect_delay_ms);
    cJSON_AddNumberToObject(espnetlink, "cli_line_state_delay_ms", config->espnetlink.cli_line_state_delay_ms);
    cJSON_AddNumberToObject(espnetlink, "cli_rx_task_stack_size", config->espnetlink.cli_rx_task_stack_size);
    cJSON_AddNumberToObject(espnetlink, "cli_rx_task_priority", config->espnetlink.cli_rx_task_priority);
    cJSON_AddNumberToObject(espnetlink, "cli_rx_task_core_id", config->espnetlink.cli_rx_task_core_id);
    cJSON_AddNumberToObject(espnetlink, "cli_rx_buffer_size", config->espnetlink.cli_rx_buffer_size);

    cJSON_AddItemToObject(devices, "espnetlink", espnetlink);
    cJSON_AddItemToObject(root, "devices", devices);

    json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    f = fopen(USB_HOST_MANAGER_CONFIG_PATH, "w");
    if (f == NULL)
    {
        free(json);
        return ESP_ERR_NOT_FOUND;
    }

    len = strlen(json);
    written = fwrite(json, 1, len, f);
    fclose(f);
    if (written > 0)
    {
        (void)usb_host_manager_cache_set(json, len);
    }

    free(json);
    return (written == 0) ? ESP_FAIL : ESP_OK;
}

esp_err_t usb_host_manager_load_config(usb_host_manager_config_t *config)
{
    cJSON *root;
    cJSON *devices;
    cJSON *espnetlink;
    cJSON *item;
    const char *json_ptr;
    usb_host_manager_config_t tmp;

    if (config == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    json_ptr = s_usb_host_cfg_json;
    if (json_ptr == NULL)
    {
        if (usb_host_manager_config_preload() == ESP_OK)
        {
            json_ptr = s_usb_host_cfg_json;
        }
    }

    if (json_ptr == NULL)
    {
        return ESP_ERR_NOT_FOUND;
    }

    usb_host_manager_set_default_config(&tmp);

    root = cJSON_Parse(json_ptr);
    if (root == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    item = cJSON_GetObjectItemCaseSensitive(root, "enabled");
    if (cJSON_IsBool(item))
    {
        tmp.enabled = cJSON_IsTrue(item);
    }

    item = cJSON_GetObjectItemCaseSensitive(root, "active_device_type");
    if (cJSON_IsString(item) && item->valuestring != NULL)
    {
        if (strcmp(item->valuestring, "espnetlink") == 0)
        {
            tmp.active_device_type = USB_HOST_MANAGER_DEVICE_ESPNETLINK;
        }
        else if (strcmp(item->valuestring, "gps") == 0)
        {
            tmp.active_device_type = USB_HOST_MANAGER_DEVICE_GPS;
        }
        else if (strcmp(item->valuestring, "usb_ethernet") == 0)
        {
            tmp.active_device_type = USB_HOST_MANAGER_DEVICE_USB_ETHERNET;
        }
        else
        {
            tmp.active_device_type = USB_HOST_MANAGER_DEVICE_NONE;
        }
    }

    item = cJSON_GetObjectItemCaseSensitive(root, "monitor_interval_ms");
    if (cJSON_IsNumber(item) && item->valuedouble > 0)
    {
        tmp.monitor_interval_ms = (uint32_t)item->valuedouble;
    }

    item = cJSON_GetObjectItemCaseSensitive(root, "device_attach_delay_ms");
    if (cJSON_IsNumber(item) && item->valuedouble >= 0)
    {
        tmp.device_attach_delay_ms = (uint32_t)item->valuedouble;
    }

    item = cJSON_GetObjectItemCaseSensitive(root, "device_detach_delay_ms");
    if (cJSON_IsNumber(item) && item->valuedouble >= 0)
    {
        tmp.device_detach_delay_ms = (uint32_t)item->valuedouble;
    }

    devices = cJSON_GetObjectItemCaseSensitive(root, "devices");
    espnetlink = cJSON_GetObjectItemCaseSensitive(devices, "espnetlink");
    if (cJSON_IsObject(espnetlink))
    {
        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "enable_cli");
        if (cJSON_IsBool(item))
        {
            tmp.espnetlink.enable_cli = cJSON_IsTrue(item);
        }

        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "prefer_default_route");
        if (cJSON_IsBool(item))
        {
            tmp.espnetlink.prefer_default_route = cJSON_IsTrue(item);
        }

        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "ip_mode");
        if (cJSON_IsString(item) && item->valuestring != NULL)
        {
            tmp.espnetlink.ip_mode = (strcmp(item->valuestring, "static") == 0) ?
                USB_HOST_MANAGER_IP_MODE_STATIC : USB_HOST_MANAGER_IP_MODE_DHCP;
        }

        usb_host_manager_parse_string(espnetlink, "static_ip", tmp.espnetlink.static_ip, sizeof(tmp.espnetlink.static_ip));
        usb_host_manager_parse_string(espnetlink, "static_netmask", tmp.espnetlink.static_netmask, sizeof(tmp.espnetlink.static_netmask));
        usb_host_manager_parse_string(espnetlink, "static_gw", tmp.espnetlink.static_gw, sizeof(tmp.espnetlink.static_gw));
        usb_host_manager_parse_string(espnetlink, "management_ip", tmp.espnetlink.management_ip, sizeof(tmp.espnetlink.management_ip));
        usb_host_manager_parse_string(espnetlink, "cli_dev_path", tmp.espnetlink.cli_dev_path, sizeof(tmp.espnetlink.cli_dev_path));

        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "cli_assert_dtr");
        if (cJSON_IsBool(item))
        {
            tmp.espnetlink.cli_assert_dtr = cJSON_IsTrue(item);
        }

        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "cli_assert_rts");
        if (cJSON_IsBool(item))
        {
            tmp.espnetlink.cli_assert_rts = cJSON_IsTrue(item);
        }

        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "cli_reconnect_delay_ms");
        if (cJSON_IsNumber(item) && item->valuedouble >= 0)
        {
            tmp.espnetlink.cli_reconnect_delay_ms = (uint32_t)item->valuedouble;
        }

        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "cli_line_state_delay_ms");
        if (cJSON_IsNumber(item) && item->valuedouble >= 0)
        {
            tmp.espnetlink.cli_line_state_delay_ms = (uint32_t)item->valuedouble;
        }

        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "cli_rx_task_stack_size");
        if (cJSON_IsNumber(item) && item->valuedouble > 0)
        {
            tmp.espnetlink.cli_rx_task_stack_size = (uint32_t)item->valuedouble;
        }

        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "cli_rx_task_priority");
        if (cJSON_IsNumber(item) && item->valuedouble > 0)
        {
            tmp.espnetlink.cli_rx_task_priority = (uint32_t)item->valuedouble;
        }

        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "cli_rx_task_core_id");
        if (cJSON_IsNumber(item))
        {
            tmp.espnetlink.cli_rx_task_core_id = (int32_t)item->valueint;
        }

        item = cJSON_GetObjectItemCaseSensitive(espnetlink, "cli_rx_buffer_size");
        if (cJSON_IsNumber(item) && item->valuedouble > 0)
        {
            tmp.espnetlink.cli_rx_buffer_size = (uint32_t)item->valuedouble;
        }
    }

    cJSON_Delete(root);
    usb_host_manager_sanitize_config(&tmp);
    memcpy(config, &tmp, sizeof(*config));
    return ESP_OK;
}