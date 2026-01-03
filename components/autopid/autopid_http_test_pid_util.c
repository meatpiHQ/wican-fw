#include "autopid_http_test_pid_util.h"

#include "sdkconfig.h"

// Some editor/IntelliSense environments don't load ESP-IDF's generated sdkconfig
// symbols, which can cause false "undefined CONFIG_*" diagnostics.
#ifndef CONFIG_FREERTOS_HZ
#define CONFIG_FREERTOS_HZ 100
#endif
#ifndef CONFIG_LOG_MAXIMUM_LEVEL
#define CONFIG_LOG_MAXIMUM_LEVEL 3
#endif

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "esp_heap_caps.h"

#include "elm327.h"

static StaticSemaphore_t test_pid_cmd_done_buf;
static SemaphoreHandle_t test_pid_cmd_done = NULL;
static char *test_pid_cmd_buffer = NULL;
static size_t test_pid_cmd_buffer_cap = 0;
static uint32_t test_pid_cmd_buffer_len = 0;
static int64_t test_pid_last_cmd_time = 0;

static char *test_pid_raw_buf = NULL;
static size_t test_pid_raw_cap = 0;
static size_t test_pid_raw_len = 0;
static bool test_pid_capture_active = false;

static bool test_pid_cmd_buffer_ensure(size_t cap)
{
    if (cap == 0)
        return false;

    if (test_pid_cmd_buffer && test_pid_cmd_buffer_cap >= cap)
        return true;

    if (test_pid_cmd_buffer)
    {
        heap_caps_free(test_pid_cmd_buffer);
        test_pid_cmd_buffer = NULL;
        test_pid_cmd_buffer_cap = 0;
    }

#ifdef CONFIG_SPIRAM
    test_pid_cmd_buffer = (char *)heap_caps_malloc(cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
    test_pid_cmd_buffer = (char *)heap_caps_malloc(cap, MALLOC_CAP_8BIT);
#endif
    if (!test_pid_cmd_buffer)
        return false;

    test_pid_cmd_buffer_cap = cap;
    memset(test_pid_cmd_buffer, 0, test_pid_cmd_buffer_cap);
    return true;
}

static void test_pid_capture_cb(char *str, uint32_t len, QueueHandle_t *q, char *cmd_str)
{
    (void)q;
    (void)cmd_str;

    if (!test_pid_capture_active || !test_pid_raw_buf || test_pid_raw_cap == 0)
        return;

    if (!str || len == 0)
        return;

    size_t avail = (test_pid_raw_cap > 0) ? (test_pid_raw_cap - 1 - test_pid_raw_len) : 0;
    if (avail == 0)
        return;

    size_t to_copy = len;
    if (to_copy > avail)
        to_copy = avail;

    memcpy(test_pid_raw_buf + test_pid_raw_len, str, to_copy);
    test_pid_raw_len += to_copy;
    test_pid_raw_buf[test_pid_raw_len] = '\0';
}

static void test_pid_elm_cb(char *str, uint32_t len, QueueHandle_t *q, char *cmd_str)
{
    // Capture raw bytes for the command (optional)
    test_pid_capture_cb(str, len, q, cmd_str);

    // Signal that the command has completed (uart1_event_task only calls the
    // response callback after it sees the terminator/prompt).
    if (test_pid_cmd_done)
        xSemaphoreGive(test_pid_cmd_done);
}

bool autopid_test_pid_raw_ensure(size_t cap)
{
    if (cap == 0)
        return false;

    if (!test_pid_raw_buf || test_pid_raw_cap < cap)
    {
#ifdef CONFIG_SPIRAM
        heap_caps_free(test_pid_raw_buf);
        test_pid_raw_buf = (char *)heap_caps_malloc(cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
        heap_caps_free(test_pid_raw_buf);
        test_pid_raw_buf = (char *)heap_caps_malloc(cap, MALLOC_CAP_8BIT);
#endif
        test_pid_raw_cap = test_pid_raw_buf ? cap : 0;
        test_pid_raw_len = 0;
        if (test_pid_raw_buf)
            test_pid_raw_buf[0] = '\0';
    }

    return (test_pid_raw_buf != NULL);
}

void autopid_test_pid_raw_reset(void)
{
    test_pid_raw_len = 0;
    if (test_pid_raw_buf && test_pid_raw_cap > 0)
        test_pid_raw_buf[0] = '\0';
}

const char *autopid_test_pid_raw_get(void)
{
    return test_pid_raw_buf ? test_pid_raw_buf : "";
}

void autopid_test_pid_raw_snippet(char *dst, size_t dstsz)
{
    if (!dst || dstsz == 0)
        return;
    dst[0] = '\0';

    const char *raw = autopid_test_pid_raw_get();
    if (!raw || raw[0] == '\0')
        return;

    size_t sn = strlen(raw);
    if (sn >= dstsz)
        sn = dstsz - 1;

    memcpy(dst, raw, sn);
    dst[sn] = '\0';
}

bool autopid_test_pid_send_cmd_sync(const char *cmd, uint32_t timeout_ms, bool capture)
{
    if (!cmd || cmd[0] == '\0')
        return true;

    if (test_pid_cmd_done == NULL)
    {
        test_pid_cmd_done = xSemaphoreCreateBinaryStatic(&test_pid_cmd_done_buf);
    }
    if (test_pid_cmd_done == NULL)
        return false;

    // Drain any previous completion signal
    while (xSemaphoreTake(test_pid_cmd_done, 0) == pdTRUE)
        ;

    test_pid_capture_active = capture;

    // Ensure command ends with CR
    char local[96];
    size_t cmd_len = strlen(cmd);
    const char *send = cmd;
    if (cmd_len == 0)
        return true;
    if (cmd[cmd_len - 1] != '\r')
    {
        if (cmd_len + 1 >= sizeof(local))
            return false;
        memcpy(local, cmd, cmd_len);
        local[cmd_len] = '\r';
        local[cmd_len + 1] = '\0';
        send = local;
        cmd_len++;
    }

    if (!test_pid_cmd_buffer_ensure(256))
        return false;

    // IMPORTANT: elm327_process_cmd() copies cmd_buffer_len+1 bytes into a newly
    // allocated command buffer (even though cmd_buffer is not explicitly NUL-terminated).
    // If a previous command was longer, the byte immediately after the current '\r'
    // can contain stale non-zero data, effectively appending garbage to the command.
    // Clearing the buffer ensures the extra byte is 0 and avoids subtle timeouts.
    memset(test_pid_cmd_buffer, 0, test_pid_cmd_buffer_cap);
    test_pid_cmd_buffer_len = 0;

    if (elm327_process_cmd((uint8_t *)send,
                           (uint32_t)cmd_len,
                           NULL,
                           test_pid_cmd_buffer,
                           &test_pid_cmd_buffer_len,
                           &test_pid_last_cmd_time,
                           test_pid_elm_cb) != 0)
    {
        test_pid_capture_active = false;
        return false;
    }

    // Wait for completion (best-effort)
    if (timeout_ms == 0)
        timeout_ms = 1200;
    bool done = (xSemaphoreTake(test_pid_cmd_done, pdMS_TO_TICKS(timeout_ms)) == pdTRUE);
    test_pid_capture_active = false;
    return done;
}

void autopid_test_pid_run_init_sequence(const char *commands, uint32_t per_cmd_timeout_ms)
{
    if (!commands || commands[0] == '\0')
        return;

    const char *p = commands;
    while (*p)
    {
        while (*p == '\r' || *p == '\n')
            p++;
        if (!*p)
            break;

        const char *end = strchr(p, '\r');
        size_t cmd_len = end ? (size_t)(end - p + 1) : strlen(p);
        if (cmd_len > 0)
        {
            bool has_cr = (p[cmd_len - 1] == '\r');
            if (has_cr)
            {
                char tmp[96];
                if (cmd_len >= sizeof(tmp))
                    return;
                memcpy(tmp, p, cmd_len);
                tmp[cmd_len] = '\0';
                (void)autopid_test_pid_send_cmd_sync(tmp, per_cmd_timeout_ms, false);
            }
            else
            {
                char tmp[96];
                if (cmd_len + 2 >= sizeof(tmp))
                    return;
                memcpy(tmp, p, cmd_len);
                tmp[cmd_len] = '\r';
                tmp[cmd_len + 1] = '\0';
                (void)autopid_test_pid_send_cmd_sync(tmp, per_cmd_timeout_ms, false);
            }
        }

        p = end ? (end + 1) : (p + cmd_len);
    }
}

void autopid_test_pid_restore_autopid_safe_elm_state(void)
{
    // IMPORTANT: Do NOT send ATWS/ATZ here.
    // The AutoPID task is running concurrently; resetting the ELM chip from the
    // test endpoint can flip echo/settings and/or disrupt the command runner.
    autopid_test_pid_run_init_sequence("ate0\rath1\ratl0\rats1\ratm0\ratst96\rATCRA\r", 1200);
}

bool autopid_test_pid_contains_case_insensitive(const char *haystack, const char *needle)
{
    if (!haystack || !needle || needle[0] == '\0')
        return false;

    size_t nlen = strlen(needle);
    for (const char *p = haystack; *p; p++)
    {
        size_t i = 0;
        while (i < nlen)
        {
            char a = (char)tolower((unsigned char)p[i]);
            char b = (char)tolower((unsigned char)needle[i]);
            if (p[i] == '\0' || a != b)
                break;
            i++;
        }
        if (i == nlen)
            return true;
    }
    return false;
}

bool autopid_test_pid_parse_hex_byte_stream(const char *s, uint8_t *out, size_t out_max, uint32_t *out_len)
{
    if (!s || !out || !out_len)
        return false;
    *out_len = 0;

    const unsigned char *p = (const unsigned char *)s;
    while (*p)
    {
        while (*p && !isxdigit(*p))
            p++;
        if (!*p)
            break;

        const unsigned char *start = p;
        size_t tok_len = 0;
        while (*p && isxdigit(*p) && tok_len < 8)
        {
            tok_len++;
            p++;
        }

        if (tok_len == 2)
        {
            if (*out_len >= out_max)
                break;

            char tmp[3];
            tmp[0] = (char)start[0];
            tmp[1] = (char)start[1];
            tmp[2] = '\0';
            out[*out_len] = (uint8_t)strtoul(tmp, NULL, 16);
            (*out_len)++;
        }

        while (*p && isxdigit(*p))
            p++;
    }

    return (*out_len > 0);
}

bool autopid_test_pid_find_response_window(const uint8_t *bytes,
                                           uint32_t bytes_len,
                                           uint8_t positive_service,
                                           uint8_t pid_byte,
                                           const uint8_t **out_ptr,
                                           uint32_t *out_len)
{
    if (!bytes || bytes_len == 0 || !out_ptr || !out_len)
        return false;

    for (uint32_t i = 0; i + 1 < bytes_len; i++)
    {
        if (bytes[i] == positive_service && (pid_byte == 0xFF || (i + 1 < bytes_len && bytes[i + 1] == pid_byte)))
        {
            uint32_t start = (i > 0) ? (i - 1) : i;
            *out_ptr = &bytes[start];
            *out_len = bytes_len - start;
            return true;
        }
    }

    *out_ptr = bytes;
    *out_len = bytes_len;
    return false;
}
