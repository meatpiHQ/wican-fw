#include "usb_host_manager_http.h"

#include <stdlib.h>
#include <string.h>

#include "sdkconfig.h"

#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

#include "usb_host_manager.h"

static const char *TAG = "usb_host_http";

static esp_err_t usb_host_manager_get_config_handler(httpd_req_t *req);
static esp_err_t usb_host_manager_post_config_handler(httpd_req_t *req);
static esp_err_t usb_host_manager_status_handler(httpd_req_t *req);
static esp_err_t usb_host_manager_espnetlink_cached_handler(httpd_req_t *req, const char *kind);
static esp_err_t usb_host_manager_espnetlink_config_set_handler(httpd_req_t *req);

static esp_err_t usb_host_manager_send_json(httpd_req_t *req, cJSON *json)
{
    char *json_str;

    if (req == NULL || json == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    json_str = cJSON_PrintUnformatted(json);
    if (json_str == NULL)
    {
        cJSON_Delete(json);
        return ESP_ERR_NO_MEM;
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, json_str, HTTPD_RESP_USE_STRLEN);
    free(json_str);
    cJSON_Delete(json);
    return ESP_OK;
}

static void usb_host_manager_append_config_json(cJSON *root, const usb_host_manager_config_t *config)
{
    cJSON *espnetlink;
    cJSON *supported;
    cJSON *entry;

    if (root == NULL || config == NULL)
    {
        return;
    }

    cJSON_AddBoolToObject(root, "enabled", config->enabled);
    cJSON_AddStringToObject(root, "active_device_type", usb_host_manager_device_type_to_str(config->active_device_type));
    cJSON_AddNumberToObject(root, "monitor_interval_ms", config->monitor_interval_ms);
    cJSON_AddNumberToObject(root, "device_attach_delay_ms", config->device_attach_delay_ms);
    cJSON_AddNumberToObject(root, "device_detach_delay_ms", config->device_detach_delay_ms);

    espnetlink = cJSON_CreateObject();
    if (espnetlink != NULL)
    {
        cJSON_AddBoolToObject(espnetlink, "enable_cli", config->espnetlink.enable_cli);
        cJSON_AddBoolToObject(espnetlink, "prefer_default_route", config->espnetlink.prefer_default_route);
        cJSON_AddStringToObject(espnetlink,
                                "ip_mode",
                                (config->espnetlink.ip_mode == USB_HOST_MANAGER_IP_MODE_STATIC) ? "static" : "dhcp");
        cJSON_AddStringToObject(espnetlink, "static_ip", config->espnetlink.static_ip);
        cJSON_AddStringToObject(espnetlink, "static_netmask", config->espnetlink.static_netmask);
        cJSON_AddStringToObject(espnetlink, "static_gw", config->espnetlink.static_gw);
        cJSON_AddStringToObject(espnetlink, "management_ip", config->espnetlink.management_ip);
        cJSON_AddStringToObject(espnetlink, "cli_dev_path", config->espnetlink.cli_dev_path);
        cJSON_AddBoolToObject(espnetlink, "cli_assert_dtr", config->espnetlink.cli_assert_dtr);
        cJSON_AddBoolToObject(espnetlink, "cli_assert_rts", config->espnetlink.cli_assert_rts);
        cJSON_AddNumberToObject(espnetlink, "cli_reconnect_delay_ms", config->espnetlink.cli_reconnect_delay_ms);
        cJSON_AddNumberToObject(espnetlink, "cli_line_state_delay_ms", config->espnetlink.cli_line_state_delay_ms);
        cJSON_AddNumberToObject(espnetlink, "cli_rx_task_stack_size", config->espnetlink.cli_rx_task_stack_size);
        cJSON_AddNumberToObject(espnetlink, "cli_rx_task_priority", config->espnetlink.cli_rx_task_priority);
        cJSON_AddNumberToObject(espnetlink, "cli_rx_task_core_id", config->espnetlink.cli_rx_task_core_id);
        cJSON_AddNumberToObject(espnetlink, "cli_rx_buffer_size", config->espnetlink.cli_rx_buffer_size);
        cJSON_AddStringToObject(espnetlink, "expected_vid", "0x303A");
        cJSON_AddStringToObject(espnetlink, "expected_pid", "0x4007");
        cJSON_AddStringToObject(espnetlink, "expected_manufacturer", "ESPNetLink");
        cJSON_AddStringToObject(espnetlink, "expected_product", "ESPNetLink RNDIS");
        cJSON_AddItemToObject(root, "espnetlink", espnetlink);
    }

    supported = cJSON_CreateArray();
    if (supported != NULL)
    {
        entry = cJSON_CreateObject();
        if (entry != NULL)
        {
            cJSON_AddStringToObject(entry, "type", "espnetlink");
            cJSON_AddBoolToObject(entry, "implemented", true);
            cJSON_AddItemToArray(supported, entry);
        }

        entry = cJSON_CreateObject();
        if (entry != NULL)
        {
            cJSON_AddStringToObject(entry, "type", "gps");
            cJSON_AddBoolToObject(entry, "implemented", false);
            cJSON_AddItemToArray(supported, entry);
        }

        entry = cJSON_CreateObject();
        if (entry != NULL)
        {
            cJSON_AddStringToObject(entry, "type", "usb_ethernet");
            cJSON_AddBoolToObject(entry, "implemented", false);
            cJSON_AddItemToArray(supported, entry);
        }

        cJSON_AddItemToObject(root, "supported_device_types", supported);
    }
}

static esp_err_t usb_host_manager_read_body(httpd_req_t *req, char **out_body)
{
    int received;
    char *body;

    if (req == NULL || out_body == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    *out_body = NULL;
    body = (char *)heap_caps_malloc((size_t)req->content_len + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (body == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    received = httpd_req_recv(req, body, req->content_len);
    if (received <= 0)
    {
        heap_caps_free(body);
        return ESP_FAIL;
    }

    body[received] = '\0';
    *out_body = body;
    return ESP_OK;
}

static esp_err_t usb_host_manager_send_cached_json(httpd_req_t *req, const char *kind, char *json_str)
{
    cJSON *root;
    cJSON *parsed;

    if (json_str == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "cache");
    }

    root = cJSON_CreateObject();
    if (root == NULL)
    {
        free(json_str);
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json");
    }

    cJSON_AddBoolToObject(root, "success", true);
    cJSON_AddStringToObject(root, "kind", kind);

    parsed = cJSON_Parse(json_str);
    if (parsed != NULL)
    {
        cJSON_AddItemToObject(root, "data", parsed);
    }
    else
    {
        cJSON_AddStringToObject(root, "data", json_str);
    }

    free(json_str);
    return usb_host_manager_send_json(req, root);
}

static esp_err_t usb_host_manager_router_handler(httpd_req_t *req)
{
    const char *uri;
    const char *segment;
    char path[96];
    size_t path_len;

    uri = req->uri;
    segment = uri + strlen("/usb_host");
    if (*segment == '/')
    {
        segment++;
    }

    if (*segment == '\0')
    {
        return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "missing segment");
    }

    path_len = strcspn(segment, "?");
    if (path_len >= sizeof(path))
    {
        path_len = sizeof(path) - 1;
    }

    memcpy(path, segment, path_len);
    path[path_len] = '\0';

    if (strcmp(path, "config") == 0)
    {
        if (req->method == HTTP_GET)
        {
            return usb_host_manager_get_config_handler(req);
        }

        if (req->method == HTTP_POST)
        {
            return usb_host_manager_post_config_handler(req);
        }
    }
    else if (strcmp(path, "status") == 0)
    {
        if (req->method == HTTP_GET)
        {
            return usb_host_manager_status_handler(req);
        }
    }
    else if (strcmp(path, "espnetlink/config") == 0 && req->method == HTTP_GET)
    {
        return usb_host_manager_espnetlink_cached_handler(req, "config");
    }
    else if (strcmp(path, "espnetlink/gps") == 0 && req->method == HTTP_GET)
    {
        return usb_host_manager_espnetlink_cached_handler(req, "gps");
    }
    else if (strcmp(path, "espnetlink/lte") == 0 && req->method == HTTP_GET)
    {
        return usb_host_manager_espnetlink_cached_handler(req, "lte");
    }
    else if (strcmp(path, "espnetlink/config/set") == 0 && req->method == HTTP_POST)
    {
        return usb_host_manager_espnetlink_config_set_handler(req);
    }

    return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "route");
}

static esp_err_t usb_host_manager_espnetlink_cached_handler(httpd_req_t *req, const char *kind)
{
    char *json_str;
    esp_err_t ret;

    json_str = NULL;
    if (strcmp(kind, "config") == 0)
    {
        ret = usb_host_manager_espnetlink_get_cached_config_json(&json_str);
    }
    else if (strcmp(kind, "gps") == 0)
    {
        ret = usb_host_manager_espnetlink_get_cached_gps_json(&json_str);
    }
    else if (strcmp(kind, "lte") == 0)
    {
        ret = usb_host_manager_espnetlink_get_cached_lte_json(&json_str);
    }
    else
    {
        return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "kind");
    }

    if (ret != ESP_OK)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, esp_err_to_name(ret));
    }

    return usb_host_manager_send_cached_json(req, kind, json_str);
}

static esp_err_t usb_host_manager_espnetlink_config_set_handler(httpd_req_t *req)
{
    char *body;
    char *payload;
    cJSON *root;
    cJSON *response;
    cJSON *key_item;
    cJSON *value_item;
    bool success;
    esp_err_t ret;

    body = NULL;
    payload = NULL;
    success = false;

    ret = usb_host_manager_read_body(req, &body);
    if (ret != ESP_OK)
    {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "body");
    }

    root = cJSON_Parse(body);
    heap_caps_free(body);
    if (root == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "json");
    }

    key_item = cJSON_GetObjectItemCaseSensitive(root, "key");
    value_item = cJSON_GetObjectItemCaseSensitive(root, "value");
    if (!cJSON_IsString(key_item) || key_item->valuestring == NULL ||
        !cJSON_IsString(value_item) || value_item->valuestring == NULL)
    {
        cJSON_Delete(root);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "key/value");
    }

    ret = usb_host_manager_espnetlink_set_config_key(key_item->valuestring,
                                                     value_item->valuestring,
                                                     &payload,
                                                     &success);
    cJSON_Delete(root);

    response = cJSON_CreateObject();
    if (response == NULL)
    {
        free(payload);
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json");
    }

    cJSON_AddBoolToObject(response, "success", ret == ESP_OK && success);
    if (payload != NULL && payload[0] != '\0')
    {
        cJSON_AddStringToObject(response, "response", payload);
    }
    if (ret != ESP_OK)
    {
        cJSON_AddStringToObject(response, "error", esp_err_to_name(ret));
    }
    else if (!success)
    {
        cJSON_AddStringToObject(response, "error", "ERROR");
    }

    free(payload);
    return usb_host_manager_send_json(req, response);
}

static esp_err_t usb_host_manager_get_config_handler(httpd_req_t *req)
{
    usb_host_manager_config_t config;
    cJSON *root;

    if (usb_host_manager_get_config(&config) != ESP_OK)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "config");
    }

    root = cJSON_CreateObject();
    if (root == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json");
    }

    cJSON_AddBoolToObject(root, "success", true);
    usb_host_manager_append_config_json(root, &config);
    ESP_LOGI(TAG, "USB host config requested");
    return usb_host_manager_send_json(req, root);
}

static bool usb_host_manager_parse_bool(const cJSON *obj, const char *name, bool *out)
{
    const cJSON *item;

    item = cJSON_GetObjectItemCaseSensitive((cJSON *)obj, name);
    if (cJSON_IsBool(item))
    {
        *out = cJSON_IsTrue(item);
        return true;
    }

    return false;
}

static bool usb_host_manager_parse_u32(const cJSON *obj, const char *name, uint32_t *out)
{
    const cJSON *item;

    item = cJSON_GetObjectItemCaseSensitive((cJSON *)obj, name);
    if (cJSON_IsNumber(item) && item->valuedouble >= 0)
    {
        *out = (uint32_t)item->valuedouble;
        return true;
    }

    return false;
}

static bool usb_host_manager_parse_i32(const cJSON *obj, const char *name, int32_t *out)
{
    const cJSON *item;

    item = cJSON_GetObjectItemCaseSensitive((cJSON *)obj, name);
    if (cJSON_IsNumber(item))
    {
        *out = (int32_t)item->valueint;
        return true;
    }

    return false;
}

static void usb_host_manager_parse_string(const cJSON *obj, const char *name, char *dst, size_t dst_len)
{
    const cJSON *item;

    item = cJSON_GetObjectItemCaseSensitive((cJSON *)obj, name);
    if (cJSON_IsString(item) && item->valuestring != NULL)
    {
        strlcpy(dst, item->valuestring, dst_len);
    }
}

static esp_err_t usb_host_manager_post_config_handler(httpd_req_t *req)
{
    char *body;
    cJSON *root;
    cJSON *espnetlink;
    cJSON *type_item;
    cJSON *mode_item;
    usb_host_manager_config_t config;
    esp_err_t ret;
    cJSON *response;

    if (usb_host_manager_get_config(&config) != ESP_OK)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "config");
    }

    if (usb_host_manager_read_body(req, &body) != ESP_OK)
    {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "body");
    }

    root = cJSON_Parse(body);
    heap_caps_free(body);
    if (root == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "json");
    }

    (void)usb_host_manager_parse_bool(root, "enabled", &config.enabled);
    (void)usb_host_manager_parse_u32(root, "monitor_interval_ms", &config.monitor_interval_ms);
    (void)usb_host_manager_parse_u32(root, "device_attach_delay_ms", &config.device_attach_delay_ms);
    (void)usb_host_manager_parse_u32(root, "device_detach_delay_ms", &config.device_detach_delay_ms);

    type_item = cJSON_GetObjectItemCaseSensitive(root, "active_device_type");
    if (cJSON_IsString(type_item) && type_item->valuestring != NULL)
    {
        if (strcmp(type_item->valuestring, "espnetlink") == 0)
        {
            config.active_device_type = USB_HOST_MANAGER_DEVICE_ESPNETLINK;
        }
        else if (strcmp(type_item->valuestring, "none") == 0)
        {
            config.active_device_type = USB_HOST_MANAGER_DEVICE_NONE;
        }
    }

    espnetlink = cJSON_GetObjectItemCaseSensitive(root, "espnetlink");
    if (cJSON_IsObject(espnetlink))
    {
        (void)usb_host_manager_parse_bool(espnetlink, "enable_cli", &config.espnetlink.enable_cli);
        (void)usb_host_manager_parse_bool(espnetlink, "prefer_default_route", &config.espnetlink.prefer_default_route);
        usb_host_manager_parse_string(espnetlink, "static_ip", config.espnetlink.static_ip, sizeof(config.espnetlink.static_ip));
        usb_host_manager_parse_string(espnetlink, "static_netmask", config.espnetlink.static_netmask, sizeof(config.espnetlink.static_netmask));
        usb_host_manager_parse_string(espnetlink, "static_gw", config.espnetlink.static_gw, sizeof(config.espnetlink.static_gw));
        usb_host_manager_parse_string(espnetlink, "management_ip", config.espnetlink.management_ip, sizeof(config.espnetlink.management_ip));
        usb_host_manager_parse_string(espnetlink, "cli_dev_path", config.espnetlink.cli_dev_path, sizeof(config.espnetlink.cli_dev_path));
        (void)usb_host_manager_parse_bool(espnetlink, "cli_assert_dtr", &config.espnetlink.cli_assert_dtr);
        (void)usb_host_manager_parse_bool(espnetlink, "cli_assert_rts", &config.espnetlink.cli_assert_rts);
        (void)usb_host_manager_parse_u32(espnetlink, "cli_reconnect_delay_ms", &config.espnetlink.cli_reconnect_delay_ms);
        (void)usb_host_manager_parse_u32(espnetlink, "cli_line_state_delay_ms", &config.espnetlink.cli_line_state_delay_ms);
        (void)usb_host_manager_parse_u32(espnetlink, "cli_rx_task_stack_size", &config.espnetlink.cli_rx_task_stack_size);
        (void)usb_host_manager_parse_u32(espnetlink, "cli_rx_task_priority", &config.espnetlink.cli_rx_task_priority);
        (void)usb_host_manager_parse_i32(espnetlink, "cli_rx_task_core_id", &config.espnetlink.cli_rx_task_core_id);
        (void)usb_host_manager_parse_u32(espnetlink, "cli_rx_buffer_size", &config.espnetlink.cli_rx_buffer_size);

        mode_item = cJSON_GetObjectItemCaseSensitive(espnetlink, "ip_mode");
        if (cJSON_IsString(mode_item) && mode_item->valuestring != NULL)
        {
            config.espnetlink.ip_mode = (strcmp(mode_item->valuestring, "static") == 0) ?
                USB_HOST_MANAGER_IP_MODE_STATIC : USB_HOST_MANAGER_IP_MODE_DHCP;
        }
    }

    cJSON_Delete(root);

    ret = usb_host_manager_set_config(&config);
    response = cJSON_CreateObject();
    if (response == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json");
    }

    cJSON_AddBoolToObject(response, "success", ret == ESP_OK);
    if (ret != ESP_OK)
    {
        cJSON_AddStringToObject(response, "error", esp_err_to_name(ret));
    }
    else
    {
        usb_host_manager_append_config_json(response, &config);
    }

    ESP_LOGI(TAG, "USB host config updated: %s", (ret == ESP_OK) ? "ok" : esp_err_to_name(ret));
    return usb_host_manager_send_json(req, response);
}

static esp_err_t usb_host_manager_status_handler(httpd_req_t *req)
{
    usb_host_manager_status_t status;
    cJSON *root;

    if (usb_host_manager_get_status(&status) != ESP_OK)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "status");
    }

    root = cJSON_CreateObject();
    if (root == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json");
    }

    cJSON_AddBoolToObject(root, "success", true);
    cJSON_AddBoolToObject(root, "initialized", status.initialized);
    cJSON_AddBoolToObject(root, "enabled", status.enabled);
    cJSON_AddNumberToObject(root, "usb_id_level", status.usb_id_level);
    cJSON_AddBoolToObject(root, "device_detected", status.device_detected);
    cJSON_AddBoolToObject(root, "device_started", status.device_started);
    cJSON_AddBoolToObject(root, "ethernet_connected", status.ethernet_connected);
    cJSON_AddBoolToObject(root, "cli_enabled", status.cli_enabled);
    cJSON_AddBoolToObject(root, "cli_connected", status.cli_connected);
    cJSON_AddBoolToObject(root, "cli_prompt_synced", status.cli_prompt_synced);
    cJSON_AddBoolToObject(root, "cli_session_ready", status.cli_session_ready);
    cJSON_AddStringToObject(root, "state", usb_host_manager_state_to_str(status.state));
    cJSON_AddStringToObject(root, "configured_device_type", usb_host_manager_device_type_to_str(status.configured_device_type));
    cJSON_AddStringToObject(root, "active_device_type", usb_host_manager_device_type_to_str(status.active_device_type));
    cJSON_AddStringToObject(root, "local_ip", status.local_ip);
    cJSON_AddStringToObject(root, "netmask", status.netmask);
    cJSON_AddStringToObject(root, "gateway", status.gateway);
    cJSON_AddStringToObject(root, "management_ip", status.management_ip);
    cJSON_AddStringToObject(root, "cli_last_command", status.cli_last_command);
    cJSON_AddStringToObject(root, "cli_last_error", status.cli_last_error);
    cJSON_AddStringToObject(root, "cli_last_response", status.cli_last_response);
    cJSON_AddStringToObject(root, "last_error", status.last_error);
    cJSON_AddStringToObject(root, "expected_vid", "0x303A");
    cJSON_AddStringToObject(root, "expected_pid", "0x4007");
    cJSON_AddStringToObject(root, "expected_network_if", "RNDIS");
    cJSON_AddStringToObject(root, "expected_cli_if", "CDC-ACM");

    ESP_LOGI(TAG, "USB host status requested");
    return usb_host_manager_send_json(req, root);
}

esp_err_t usb_host_manager_register_handlers(httpd_handle_t server)
{
    static const httpd_uri_t get_uri = {
        .uri = "/usb_host*",
        .method = HTTP_GET,
        .handler = usb_host_manager_router_handler,
    };
    static const httpd_uri_t post_uri = {
        .uri = "/usb_host*",
        .method = HTTP_POST,
        .handler = usb_host_manager_router_handler,
    };
    esp_err_t ret;

    ret = httpd_register_uri_handler(server, &get_uri);
    if (ret != ESP_OK && ret != ESP_ERR_HTTPD_HANDLER_EXISTS)
    {
        return ret;
    }

    ret = httpd_register_uri_handler(server, &post_uri);
    if (ret != ESP_OK && ret != ESP_ERR_HTTPD_HANDLER_EXISTS)
    {
        return ret;
    }

    ESP_LOGI(TAG, "USB host handlers registered at /usb_host*");
    return ESP_OK;
}