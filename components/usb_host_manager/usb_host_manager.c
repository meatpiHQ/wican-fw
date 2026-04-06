#include "usb_host_manager.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sdkconfig.h"

#include "connection_manager.h"
#include "cJSON.h"
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
#define USB_HOST_MANAGER_CLI_TASK_STACK_WORDS 4096
#define USB_HOST_MANAGER_CLI_TASK_PRIORITY 4
#define USB_HOST_MANAGER_CLI_RESPONSE_MAX 2048
#define USB_HOST_MANAGER_CLI_RX_CHUNK_SIZE 128
#define USB_HOST_MANAGER_CLI_BOOTSTRAP_DELAY_MS 750
#define USB_HOST_MANAGER_CLI_SYNC_TIMEOUT_MS 1000
#define USB_HOST_MANAGER_CLI_COMMAND_TIMEOUT_MS 5000
#define USB_HOST_MANAGER_CLI_IDLE_MS 250
#define USB_HOST_MANAGER_CLI_CONFIG_POLL_MS 60000
#define USB_HOST_MANAGER_CLI_GPS_POLL_MS 5000
#define USB_HOST_MANAGER_CLI_LTE_POLL_MS 15000
#define USB_HOST_MANAGER_CLI_LOCK_TIMEOUT_MS 10
#define USB_HOST_MANAGER_ESPNETLINK_DEFAULT_TIMEOUT_MS 5000

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
    TaskHandle_t cli_task;
    StackType_t *cli_task_stack;
    StaticTask_t cli_task_buffer;
    bool cli_prompt_synced;
    bool cli_session_ready;
    int64_t cli_ready_after_us;
    int64_t cli_next_config_poll_us;
    int64_t cli_next_gps_poll_us;
    int64_t cli_next_lte_poll_us;
    char *cli_response_buf;
    char *cli_payload_buf;
    char *cli_config_json;
    size_t cli_config_json_len;
    char *cli_gps_json;
    size_t cli_gps_json_len;
    char *cli_lte_json;
    size_t cli_lte_json_len;
} usb_host_manager_runtime_t;

static const char *TAG = "usb_host_mgr";

static usb_host_manager_runtime_t s_usb_host_mgr = { 0 };

static void usb_host_manager_task(void *arg);
static void usb_host_manager_cli_task(void *arg);

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

static void usb_host_manager_cli_set_error_locked(const char *msg)
{
    if (msg == NULL)
    {
        s_usb_host_mgr.status.cli_last_error[0] = '\0';
        return;
    }

    strlcpy(s_usb_host_mgr.status.cli_last_error, msg, sizeof(s_usb_host_mgr.status.cli_last_error));
}

static void usb_host_manager_cli_set_last_command_locked(const char *cmd)
{
    if (cmd == NULL)
    {
        s_usb_host_mgr.status.cli_last_command[0] = '\0';
        return;
    }

    strlcpy(s_usb_host_mgr.status.cli_last_command, cmd, sizeof(s_usb_host_mgr.status.cli_last_command));
}

static void usb_host_manager_cli_set_last_response_locked(const char *response)
{
    if (response == NULL)
    {
        s_usb_host_mgr.status.cli_last_response[0] = '\0';
        return;
    }

    strlcpy(s_usb_host_mgr.status.cli_last_response, response, sizeof(s_usb_host_mgr.status.cli_last_response));
}

static void usb_host_manager_cli_cache_clear_locked(char **cache, size_t *cache_len)
{
    if (cache == NULL || cache_len == NULL)
    {
        return;
    }

    if (*cache != NULL)
    {
        heap_caps_free(*cache);
        *cache = NULL;
    }

    *cache_len = 0;
}

static esp_err_t usb_host_manager_cli_cache_set_locked(char **cache,
                                                       size_t *cache_len,
                                                       const char *data)
{
    char *buf;
    size_t len;

    if (cache == NULL || cache_len == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (data == NULL || data[0] == '\0')
    {
        usb_host_manager_cli_cache_clear_locked(cache, cache_len);
        return ESP_OK;
    }

    len = strlen(data);
    buf = (char *)heap_caps_malloc(len + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (buf == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    memcpy(buf, data, len + 1);
    usb_host_manager_cli_cache_clear_locked(cache, cache_len);
    *cache = buf;
    *cache_len = len;
    return ESP_OK;
}

static char *usb_host_manager_dup_string(const char *src)
{
    char *dst;
    size_t len;

    if (src == NULL)
    {
        return NULL;
    }

    len = strlen(src);
    dst = (char *)malloc(len + 1);
    if (dst == NULL)
    {
        return NULL;
    }

    memcpy(dst, src, len + 1);
    return dst;
}

static void usb_host_manager_cli_schedule_locked(int64_t now_us)
{
    s_usb_host_mgr.cli_next_config_poll_us = now_us;
    s_usb_host_mgr.cli_next_gps_poll_us = now_us;
    s_usb_host_mgr.cli_next_lte_poll_us = now_us;
}

static void usb_host_manager_cli_reset_locked(bool clear_cache)
{
    s_usb_host_mgr.cli_prompt_synced = false;
    s_usb_host_mgr.cli_session_ready = false;
    s_usb_host_mgr.cli_ready_after_us = 0;
    s_usb_host_mgr.cli_next_config_poll_us = 0;
    s_usb_host_mgr.cli_next_gps_poll_us = 0;
    s_usb_host_mgr.cli_next_lte_poll_us = 0;
    s_usb_host_mgr.status.cli_last_command[0] = '\0';
    s_usb_host_mgr.status.cli_last_response[0] = '\0';

    if (clear_cache)
    {
        usb_host_manager_cli_cache_clear_locked(&s_usb_host_mgr.cli_config_json, &s_usb_host_mgr.cli_config_json_len);
        usb_host_manager_cli_cache_clear_locked(&s_usb_host_mgr.cli_gps_json, &s_usb_host_mgr.cli_gps_json_len);
        usb_host_manager_cli_cache_clear_locked(&s_usb_host_mgr.cli_lte_json, &s_usb_host_mgr.cli_lte_json_len);
        usb_host_manager_cli_set_error_locked(NULL);
    }
}

static bool usb_host_manager_cli_append_normalized(char *dst,
                                                   size_t dst_size,
                                                   size_t *dst_len,
                                                   const uint8_t *src,
                                                   size_t src_len)
{
    size_t i;

    if (dst == NULL || dst_len == NULL || src == NULL || dst_size == 0)
    {
        return false;
    }

    for (i = 0; i < src_len; i++)
    {
        if (src[i] == '\r')
        {
            continue;
        }

        if ((*dst_len + 1) >= dst_size)
        {
            return false;
        }

        dst[*dst_len] = (char)src[i];
        (*dst_len)++;
    }

    dst[*dst_len] = '\0';
    return true;
}

static bool usb_host_manager_cli_buffer_has_prompt(const char *buffer)
{
    return (buffer != NULL) && (strstr(buffer, "esp>") != NULL);
}

static char *usb_host_manager_cli_trim(char *str)
{
    char *end;

    if (str == NULL)
    {
        return NULL;
    }

    while (*str == ' ' || *str == '\t')
    {
        str++;
    }

    end = str + strlen(str);
    while (end > str && (end[-1] == ' ' || end[-1] == '\t'))
    {
        end--;
    }

    *end = '\0';
    return str;
}

static const char *usb_host_manager_cli_match_terminal(const char *buffer, bool *success)
{
    static const char *ok_suffixes[] = { "OK\nesp> ", "OK\nesp>", "OK\nesp>\n" };
    static const char *error_suffixes[] = { "ERROR\nesp> ", "ERROR\nesp>", "ERROR\nesp>\n" };
    const char **suffixes;
    size_t suffix_count;
    size_t i;
    size_t len;
    size_t suffix_len;

    if (buffer == NULL || success == NULL)
    {
        return NULL;
    }

    len = strlen(buffer);

    suffixes = ok_suffixes;
    suffix_count = sizeof(ok_suffixes) / sizeof(ok_suffixes[0]);
    for (i = 0; i < suffix_count; i++)
    {
        suffix_len = strlen(suffixes[i]);
        if (len >= suffix_len && strcmp(buffer + (len - suffix_len), suffixes[i]) == 0)
        {
            *success = true;
            return suffixes[i];
        }
    }

    suffixes = error_suffixes;
    suffix_count = sizeof(error_suffixes) / sizeof(error_suffixes[0]);
    for (i = 0; i < suffix_count; i++)
    {
        suffix_len = strlen(suffixes[i]);
        if (len >= suffix_len && strcmp(buffer + (len - suffix_len), suffixes[i]) == 0)
        {
            *success = false;
            return suffixes[i];
        }
    }

    return NULL;
}

static void usb_host_manager_cli_extract_payload(char *buffer,
                                                 const char *cmd,
                                                 const char *terminal_suffix,
                                                 char *payload,
                                                 size_t payload_len)
{
    size_t body_len;
    char *line;
    char *saveptr;
    bool first_payload;

    if (buffer == NULL || cmd == NULL || terminal_suffix == NULL || payload == NULL || payload_len == 0)
    {
        return;
    }

    payload[0] = '\0';
    body_len = strlen(buffer);
    if (body_len < strlen(terminal_suffix))
    {
        return;
    }

    body_len -= strlen(terminal_suffix);
    buffer[body_len] = '\0';

    first_payload = true;
    saveptr = NULL;
    line = strtok_r(buffer, "\n", &saveptr);
    while (line != NULL)
    {
        char *trimmed;

        trimmed = usb_host_manager_cli_trim(line);
        if (trimmed[0] == '\0')
        {
            line = strtok_r(NULL, "\n", &saveptr);
            continue;
        }

        if (strcmp(trimmed, cmd) == 0)
        {
            line = strtok_r(NULL, "\n", &saveptr);
            continue;
        }

        if (strncmp(trimmed, "esp>", 4) == 0)
        {
            if (strncmp(trimmed, "esp> ", 5) == 0 && strcmp(trimmed + 5, cmd) == 0)
            {
                line = strtok_r(NULL, "\n", &saveptr);
                continue;
            }

            if (strcmp(trimmed, "esp>") == 0 || strcmp(trimmed, "esp> ") == 0)
            {
                line = strtok_r(NULL, "\n", &saveptr);
                continue;
            }
        }

        if (!first_payload)
        {
            strlcat(payload, "\n", payload_len);
        }

        strlcat(payload, trimmed, payload_len);
        first_payload = false;
        line = strtok_r(NULL, "\n", &saveptr);
    }
}

static void usb_host_manager_cli_drain_input_locked(uint32_t quiet_ms)
{
    uint8_t tmp[USB_HOST_MANAGER_CLI_RX_CHUNK_SIZE];
    size_t out_len;
    int64_t deadline_us;

    deadline_us = esp_timer_get_time() + ((int64_t)quiet_ms * 1000);
    while (esp_timer_get_time() < deadline_us)
    {
        if (usb_acm_cli_read(tmp, sizeof(tmp), 20, &out_len) != ESP_OK)
        {
            return;
        }

        if (out_len == 0)
        {
            return;
        }
    }
}

static esp_err_t usb_host_manager_cli_sync_prompt_locked(void)
{
    uint8_t tmp[USB_HOST_MANAGER_CLI_RX_CHUNK_SIZE];
    char *response;
    size_t response_len;
    size_t out_len;
    int64_t start_us;

    response = s_usb_host_mgr.cli_response_buf;
    if (response == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }

    response[0] = '\0';
    response_len = 0;

    usb_host_manager_cli_drain_input_locked(50);
    if (usb_acm_cli_send_line("") != ESP_OK)
    {
        return ESP_FAIL;
    }

    start_us = esp_timer_get_time();
    while ((esp_timer_get_time() - start_us) < ((int64_t)USB_HOST_MANAGER_CLI_SYNC_TIMEOUT_MS * 1000))
    {
        if (usb_acm_cli_read(tmp, sizeof(tmp), 100, &out_len) != ESP_OK)
        {
            return ESP_FAIL;
        }

        if (out_len == 0)
        {
            continue;
        }

        if (!usb_host_manager_cli_append_normalized(response,
                                                    USB_HOST_MANAGER_CLI_RESPONSE_MAX,
                                                    &response_len,
                                                    tmp,
                                                    out_len))
        {
            return ESP_ERR_NO_MEM;
        }

        if (usb_host_manager_lock(pdMS_TO_TICKS(10)))
        {
            usb_host_manager_cli_set_last_response_locked(response);
            usb_host_manager_unlock();
        }

        if (usb_host_manager_cli_buffer_has_prompt(response))
        {
            return ESP_OK;
        }
    }

    return ESP_ERR_TIMEOUT;
}

static esp_err_t usb_host_manager_cli_exec_locked(const char *cmd,
                                                  uint32_t timeout_ms,
                                                  bool *success,
                                                  char *payload,
                                                  size_t payload_len)
{
    uint8_t tmp[USB_HOST_MANAGER_CLI_RX_CHUNK_SIZE];
    char *response;
    size_t response_len;
    size_t out_len;
    int64_t start_us;
    const char *terminal_suffix;
    bool terminal_success;

    if (cmd == NULL || success == NULL || payload == NULL || payload_len == 0)
    {
        return ESP_ERR_INVALID_ARG;
    }

    response = s_usb_host_mgr.cli_response_buf;
    if (response == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }

    response[0] = '\0';
    payload[0] = '\0';
    response_len = 0;
    terminal_success = false;
    usb_host_manager_cli_drain_input_locked(50);

    if (usb_host_manager_lock(pdMS_TO_TICKS(20)))
    {
        usb_host_manager_cli_set_last_command_locked(cmd);
        usb_host_manager_cli_set_last_response_locked(NULL);
        usb_host_manager_unlock();
    }

    if (usb_acm_cli_send_line(cmd) != ESP_OK)
    {
        return ESP_FAIL;
    }

    start_us = esp_timer_get_time();
    while ((esp_timer_get_time() - start_us) < ((int64_t)timeout_ms * 1000))
    {
        if (usb_acm_cli_read(tmp, sizeof(tmp), 100, &out_len) != ESP_OK)
        {
            return ESP_FAIL;
        }

        if (out_len == 0)
        {
            continue;
        }

        if (!usb_host_manager_cli_append_normalized(response,
                                                    USB_HOST_MANAGER_CLI_RESPONSE_MAX,
                                                    &response_len,
                                                    tmp,
                                                    out_len))
        {
            return ESP_ERR_NO_MEM;
        }

        if (usb_host_manager_lock(pdMS_TO_TICKS(10)))
        {
            usb_host_manager_cli_set_last_response_locked(response);
            usb_host_manager_unlock();
        }

        terminal_suffix = usb_host_manager_cli_match_terminal(response, &terminal_success);
        if (terminal_suffix != NULL)
        {
            *success = terminal_success;
            usb_host_manager_cli_extract_payload(response, cmd, terminal_suffix, payload, payload_len);
            return ESP_OK;
        }
    }

    return ESP_ERR_TIMEOUT;
}

static esp_err_t usb_host_manager_cli_execute(const char *cmd,
                                              uint32_t lock_timeout_ms,
                                              uint32_t timeout_ms,
                                              bool update_error_on_busy,
                                              bool *out_success,
                                              char **out_payload)
{
    bool success;
    esp_err_t ret;
    char *payload;

    if (out_success != NULL)
    {
        *out_success = false;
    }

    if (out_payload != NULL)
    {
        *out_payload = NULL;
    }

    if (cmd == NULL || cmd[0] == '\0')
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (!s_usb_host_mgr.initialized || !s_usb_host_mgr.espnetlink_started || !s_usb_host_mgr.config.espnetlink.enable_cli)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (!usb_acm_cli_is_connected())
    {
        return ESP_ERR_INVALID_STATE;
    }

    payload = s_usb_host_mgr.cli_payload_buf;
    if (payload == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (timeout_ms == 0)
    {
        timeout_ms = USB_HOST_MANAGER_ESPNETLINK_DEFAULT_TIMEOUT_MS;
    }

    ret = usb_acm_cli_acquire(lock_timeout_ms);
    if (ret != ESP_OK)
    {
        if (update_error_on_busy && usb_host_manager_lock(pdMS_TO_TICKS(20)))
        {
            usb_host_manager_cli_set_error_locked("CLI busy");
            usb_host_manager_unlock();
        }
        return ret;
    }

    ret = usb_host_manager_cli_exec_locked(cmd, timeout_ms, &success, payload, USB_HOST_MANAGER_CLI_RESPONSE_MAX);
    usb_acm_cli_release();

    if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        if (ret == ESP_OK)
        {
            s_usb_host_mgr.cli_session_ready = true;
            s_usb_host_mgr.cli_prompt_synced = true;
            if (success)
            {
                usb_host_manager_cli_set_error_locked(NULL);
            }
            else
            {
                char err_msg[96];

                snprintf(err_msg, sizeof(err_msg), "%s returned ERROR", cmd);
                usb_host_manager_cli_set_error_locked(err_msg);
            }
        }
        else
        {
            char err_msg[96];

            s_usb_host_mgr.cli_prompt_synced = false;
            if (ret == ESP_ERR_TIMEOUT)
            {
                if (s_usb_host_mgr.status.cli_last_response[0] == '\0')
                {
                    snprintf(err_msg, sizeof(err_msg), "%s failed: no response", cmd);
                }
                else
                {
                    snprintf(err_msg, sizeof(err_msg), "%s failed: incomplete response", cmd);
                }
            }
            else
            {
                snprintf(err_msg, sizeof(err_msg), "%s failed: %s", cmd, esp_err_to_name(ret));
            }
            usb_host_manager_cli_set_error_locked(err_msg);
        }
        usb_host_manager_unlock();
    }

    if (out_success != NULL)
    {
        *out_success = success;
    }

    if (out_payload != NULL && ret == ESP_OK)
    {
        *out_payload = usb_host_manager_dup_string(payload);
        if (*out_payload == NULL)
        {
            return ESP_ERR_NO_MEM;
        }
    }

    return ret;
}

static esp_err_t usb_host_manager_espnetlink_refresh_cache(const char *cmd,
                                                           char **cache,
                                                           size_t *cache_len)
{
    bool success;
    esp_err_t ret;

    ret = usb_host_manager_cli_execute(cmd,
                                       USB_HOST_MANAGER_CLI_LOCK_TIMEOUT_MS,
                                       USB_HOST_MANAGER_CLI_COMMAND_TIMEOUT_MS,
                                       false,
                                       &success,
                                       NULL);
    if (ret != ESP_OK || !success)
    {
        return ret;
    }

    if (!usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        return ESP_ERR_TIMEOUT;
    }

    ret = usb_host_manager_cli_cache_set_locked(cache, cache_len, s_usb_host_mgr.cli_payload_buf);
    usb_host_manager_unlock();
    return ret;
}

static bool usb_host_manager_char_equals_ignore_case(char lhs, char rhs)
{
    if (lhs >= 'A' && lhs <= 'Z')
    {
        lhs = (char)(lhs - 'A' + 'a');
    }

    if (rhs >= 'A' && rhs <= 'Z')
    {
        rhs = (char)(rhs - 'A' + 'a');
    }

    return lhs == rhs;
}

static bool usb_host_manager_str_equals_ignore_case(const char *lhs, const char *rhs)
{
    size_t i;

    if (lhs == NULL || rhs == NULL)
    {
        return false;
    }

    for (i = 0; lhs[i] != '\0' && rhs[i] != '\0'; ++i)
    {
        if (!usb_host_manager_char_equals_ignore_case(lhs[i], rhs[i]))
        {
            return false;
        }
    }

    return lhs[i] == '\0' && rhs[i] == '\0';
}

static bool usb_host_manager_json_value_is_true(const cJSON *item)
{
    const char *value;

    if (item == NULL)
    {
        return false;
    }

    if (cJSON_IsBool(item))
    {
        return cJSON_IsTrue(item);
    }

    if (cJSON_IsNumber(item))
    {
        return item->valueint != 0;
    }

    if (!cJSON_IsString(item) || item->valuestring == NULL)
    {
        return false;
    }

    value = item->valuestring;
    return usb_host_manager_str_equals_ignore_case(value, "true") ||
           usb_host_manager_str_equals_ignore_case(value, "yes") ||
           usb_host_manager_str_equals_ignore_case(value, "enable") ||
           usb_host_manager_str_equals_ignore_case(value, "enabled") ||
           strcmp(value, "1") == 0;
}

static esp_err_t usb_host_manager_espnetlink_refresh_config_cache(void)
{
    char desired_apn[sizeof(s_usb_host_mgr.config.espnetlink.desired_apn)];
    char current_apn[sizeof(s_usb_host_mgr.config.espnetlink.desired_apn)];
    bool ncm_share_enabled;
    bool lte_enabled;
    bool gps_enabled;
    cJSON *root;
    cJSON *apn_item;
    char *config_json;
    bool success;
    esp_err_t ret;

    desired_apn[0] = '\0';
    current_apn[0] = '\0';
    config_json = NULL;
    ncm_share_enabled = false;
    lte_enabled = false;
    gps_enabled = false;

    ret = usb_host_manager_espnetlink_refresh_cache("config -l -j",
                                                    &s_usb_host_mgr.cli_config_json,
                                                    &s_usb_host_mgr.cli_config_json_len);
    if (ret != ESP_OK)
    {
        return ret;
    }

    if (!usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        return ESP_ERR_TIMEOUT;
    }

    strlcpy(desired_apn,
            s_usb_host_mgr.config.espnetlink.desired_apn,
            sizeof(desired_apn));
    config_json = usb_host_manager_dup_string(s_usb_host_mgr.cli_config_json);
    usb_host_manager_unlock();

    if (config_json == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    root = cJSON_Parse(config_json);
    free(config_json);
    if (root == NULL)
    {
        return ESP_ERR_INVALID_RESPONSE;
    }

    apn_item = cJSON_GetObjectItemCaseSensitive(root, "APN");
    if (!cJSON_IsString(apn_item) || apn_item->valuestring == NULL)
    {
        apn_item = cJSON_GetObjectItemCaseSensitive(root, "apn");
    }

    if (cJSON_IsString(apn_item) && apn_item->valuestring != NULL)
    {
        strlcpy(current_apn, apn_item->valuestring, sizeof(current_apn));
    }

    ncm_share_enabled = usb_host_manager_json_value_is_true(
        cJSON_GetObjectItemCaseSensitive(root, "NCM_SHARE"));
    lte_enabled = usb_host_manager_json_value_is_true(
        cJSON_GetObjectItemCaseSensitive(root, "LTE_ENABLED"));
    gps_enabled = usb_host_manager_json_value_is_true(
        cJSON_GetObjectItemCaseSensitive(root, "GPS_ENABLED"));

    cJSON_Delete(root);

    if (!ncm_share_enabled)
    {
        success = false;
        ret = usb_host_manager_espnetlink_set_config_key("NCM_SHARE", "true", NULL, &success);
        if (ret != ESP_OK)
        {
            return ret;
        }

        return success ? ESP_OK : ESP_FAIL;
    }

    if (!lte_enabled)
    {
        success = false;
        ret = usb_host_manager_espnetlink_set_config_key("LTE_ENABLED", "true", NULL, &success);
        if (ret != ESP_OK)
        {
            return ret;
        }

        return success ? ESP_OK : ESP_FAIL;
    }

    if (!gps_enabled)
    {
        success = false;
        ret = usb_host_manager_espnetlink_set_config_key("GPS_ENABLED", "true", NULL, &success);
        if (ret != ESP_OK)
        {
            return ret;
        }

        return success ? ESP_OK : ESP_FAIL;
    }

    if (desired_apn[0] == '\0' || strcmp(current_apn, desired_apn) == 0)
    {
        return ESP_OK;
    }

    success = false;
    ret = usb_host_manager_espnetlink_set_config_key("APN", desired_apn, NULL, &success);
    if (ret != ESP_OK)
    {
        return ret;
    }

    return success ? ESP_OK : ESP_FAIL;
}

static esp_err_t usb_host_manager_espnetlink_refresh_gps_cache(void)
{
    return usb_host_manager_espnetlink_refresh_cache("gps -p -j",
                                                     &s_usb_host_mgr.cli_gps_json,
                                                     &s_usb_host_mgr.cli_gps_json_len);
}

static esp_err_t usb_host_manager_espnetlink_refresh_lte_cache(void)
{
    return usb_host_manager_espnetlink_refresh_cache("lte -j",
                                                     &s_usb_host_mgr.cli_lte_json,
                                                     &s_usb_host_mgr.cli_lte_json_len);
}

static void usb_host_manager_cli_task(void *arg)
{
    (void)arg;

    while (true)
    {
        bool cli_available;
        bool session_ready;
        bool prompt_synced;
        int64_t now_us;

        cli_available = false;
        session_ready = false;
        prompt_synced = false;
        now_us = esp_timer_get_time();

        if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
        {
            cli_available = s_usb_host_mgr.initialized &&
                s_usb_host_mgr.espnetlink_started &&
                s_usb_host_mgr.config.enabled &&
                s_usb_host_mgr.config.active_device_type == USB_HOST_MANAGER_DEVICE_ESPNETLINK &&
                s_usb_host_mgr.config.espnetlink.enable_cli &&
                usb_acm_cli_is_connected();

            if (!cli_available)
            {
                usb_host_manager_cli_reset_locked(true);
                usb_host_manager_unlock();
                vTaskDelay(pdMS_TO_TICKS(USB_HOST_MANAGER_CLI_IDLE_MS));
                continue;
            }

            if (!s_usb_host_mgr.cli_session_ready && s_usb_host_mgr.cli_ready_after_us == 0)
            {
                s_usb_host_mgr.cli_ready_after_us = now_us + ((int64_t)USB_HOST_MANAGER_CLI_BOOTSTRAP_DELAY_MS * 1000);
                usb_host_manager_cli_set_error_locked(NULL);
            }

            session_ready = s_usb_host_mgr.cli_session_ready;
            prompt_synced = s_usb_host_mgr.cli_prompt_synced;
            usb_host_manager_unlock();
        }

        if (!session_ready)
        {
            if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
            {
                now_us = esp_timer_get_time();
                if (s_usb_host_mgr.cli_ready_after_us != 0 && now_us < s_usb_host_mgr.cli_ready_after_us)
                {
                    usb_host_manager_unlock();
                    vTaskDelay(pdMS_TO_TICKS(USB_HOST_MANAGER_CLI_IDLE_MS));
                    continue;
                }
                usb_host_manager_unlock();
            }

            if (usb_acm_cli_acquire(100) == ESP_OK)
            {
                esp_err_t sync_ret;

                sync_ret = usb_host_manager_cli_sync_prompt_locked();
                usb_acm_cli_release();

                if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
                {
                    s_usb_host_mgr.cli_session_ready = true;
                    s_usb_host_mgr.cli_prompt_synced = (sync_ret == ESP_OK);
                    usb_host_manager_cli_schedule_locked(esp_timer_get_time());
                    usb_host_manager_unlock();
                }
            }
            else if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
            {
                s_usb_host_mgr.cli_session_ready = true;
                s_usb_host_mgr.cli_prompt_synced = false;
                usb_host_manager_cli_schedule_locked(esp_timer_get_time());
                usb_host_manager_unlock();
            }

            vTaskDelay(pdMS_TO_TICKS(USB_HOST_MANAGER_CLI_IDLE_MS));
            continue;
        }

        if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
        {
            now_us = esp_timer_get_time();

            if (now_us >= s_usb_host_mgr.cli_next_config_poll_us)
            {
                s_usb_host_mgr.cli_next_config_poll_us = now_us + ((int64_t)USB_HOST_MANAGER_CLI_CONFIG_POLL_MS * 1000);
                usb_host_manager_unlock();
                (void)usb_host_manager_espnetlink_refresh_config_cache();
                continue;
            }

            if (now_us >= s_usb_host_mgr.cli_next_lte_poll_us)
            {
                s_usb_host_mgr.cli_next_lte_poll_us = now_us + ((int64_t)USB_HOST_MANAGER_CLI_LTE_POLL_MS * 1000);
                usb_host_manager_unlock();
                (void)usb_host_manager_espnetlink_refresh_lte_cache();
                continue;
            }

            if (now_us >= s_usb_host_mgr.cli_next_gps_poll_us)
            {
                s_usb_host_mgr.cli_next_gps_poll_us = now_us + ((int64_t)USB_HOST_MANAGER_CLI_GPS_POLL_MS * 1000);
                usb_host_manager_unlock();
                (void)usb_host_manager_espnetlink_refresh_gps_cache();
                continue;
            }

            usb_host_manager_unlock();
        }

        if (!prompt_synced)
        {
            vTaskDelay(pdMS_TO_TICKS(USB_HOST_MANAGER_CLI_IDLE_MS));
        }
        else
        {
            vTaskDelay(pdMS_TO_TICKS(USB_HOST_MANAGER_CLI_IDLE_MS));
        }
    }
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

    if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        usb_host_manager_cli_reset_locked(true);
        s_usb_host_mgr.cli_ready_after_us = esp_timer_get_time() + ((int64_t)USB_HOST_MANAGER_CLI_BOOTSTRAP_DELAY_MS * 1000);
        usb_host_manager_unlock();
    }

    return ESP_OK;
}

static void usb_host_manager_stop_espnetlink(void)
{
    (void)usb_acm_cli_stop();
    dev_status_clear_eth_connected();
    (void)connection_manager_request_reconcile();

    if (usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        usb_host_manager_cli_reset_locked(true);
        usb_host_manager_unlock();
    }

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
    s_usb_host_mgr.status.cli_prompt_synced = s_usb_host_mgr.cli_prompt_synced;
    s_usb_host_mgr.status.cli_session_ready = s_usb_host_mgr.cli_session_ready;
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

esp_err_t usb_host_manager_espnetlink_exec_command(const char *cmd,
                                                   uint32_t timeout_ms,
                                                   char **out_payload,
                                                   bool *out_success)
{
    return usb_host_manager_cli_execute(cmd,
                                        timeout_ms,
                                        timeout_ms,
                                        true,
                                        out_success,
                                        out_payload);
}

esp_err_t usb_host_manager_espnetlink_set_config_key(const char *key,
                                                     const char *value,
                                                     char **out_payload,
                                                     bool *out_success)
{
    char cmd[160];
    esp_err_t ret;

    if (key == NULL || key[0] == '\0' || value == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    snprintf(cmd, sizeof(cmd), "config set %s %s", key, value);
    ret = usb_host_manager_espnetlink_exec_command(cmd,
                                                   USB_HOST_MANAGER_ESPNETLINK_DEFAULT_TIMEOUT_MS,
                                                   out_payload,
                                                   out_success);
    if (ret == ESP_OK && out_success != NULL && *out_success)
    {
        (void)usb_host_manager_espnetlink_refresh_config_cache();
    }

    return ret;
}

esp_err_t usb_host_manager_espnetlink_get_cached_config_json(char **out_json)
{
    if (out_json == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    *out_json = NULL;
    if (!s_usb_host_mgr.initialized)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (!usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        return ESP_ERR_TIMEOUT;
    }

    *out_json = usb_host_manager_dup_string(s_usb_host_mgr.cli_config_json);
    usb_host_manager_unlock();
    return ESP_OK;
}

esp_err_t usb_host_manager_espnetlink_get_cached_gps_json(char **out_json)
{
    if (out_json == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    *out_json = NULL;
    if (!s_usb_host_mgr.initialized)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (!usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        return ESP_ERR_TIMEOUT;
    }

    *out_json = usb_host_manager_dup_string(s_usb_host_mgr.cli_gps_json);
    usb_host_manager_unlock();
    return ESP_OK;
}

esp_err_t usb_host_manager_espnetlink_get_cached_lte_json(char **out_json)
{
    if (out_json == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    *out_json = NULL;
    if (!s_usb_host_mgr.initialized)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (!usb_host_manager_lock(pdMS_TO_TICKS(50)))
    {
        return ESP_ERR_TIMEOUT;
    }

    *out_json = usb_host_manager_dup_string(s_usb_host_mgr.cli_lte_json);
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

    s_usb_host_mgr.cli_task_stack = (StackType_t *)heap_caps_malloc(
        USB_HOST_MANAGER_CLI_TASK_STACK_WORDS * sizeof(StackType_t),
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_RETURN_ON_FALSE(s_usb_host_mgr.cli_task_stack != NULL, ESP_ERR_NO_MEM, TAG, "failed to allocate cli task stack");

    s_usb_host_mgr.cli_response_buf = (char *)heap_caps_malloc(USB_HOST_MANAGER_CLI_RESPONSE_MAX,
                                                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_RETURN_ON_FALSE(s_usb_host_mgr.cli_response_buf != NULL, ESP_ERR_NO_MEM, TAG, "failed to allocate cli response buffer");

    s_usb_host_mgr.cli_payload_buf = (char *)heap_caps_malloc(USB_HOST_MANAGER_CLI_RESPONSE_MAX,
                                                              MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_RETURN_ON_FALSE(s_usb_host_mgr.cli_payload_buf != NULL, ESP_ERR_NO_MEM, TAG, "failed to allocate cli payload buffer");

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

    s_usb_host_mgr.cli_task = xTaskCreateStatic(usb_host_manager_cli_task,
                                                "usb_host_cli",
                                                USB_HOST_MANAGER_CLI_TASK_STACK_WORDS,
                                                NULL,
                                                USB_HOST_MANAGER_CLI_TASK_PRIORITY,
                                                s_usb_host_mgr.cli_task_stack,
                                                &s_usb_host_mgr.cli_task_buffer);
    ESP_RETURN_ON_FALSE(s_usb_host_mgr.cli_task != NULL, ESP_ERR_NO_MEM, TAG, "failed to create cli task");

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
