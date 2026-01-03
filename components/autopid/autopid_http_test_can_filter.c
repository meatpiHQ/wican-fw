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

#include "autopid_http_internal.h"

#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include <cJSON.h>
#include <esp_log.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "autopid.h"
#include "autopid_http_test_pid_util.h"
#include "config_server.h"
#include "elm327.h"
#include "expression_parser.h"

static const char *TAG = "AUTOPID_HTTP";

static StaticSemaphore_t test_canflt_lock_buf;
static SemaphoreHandle_t test_canflt_lock = NULL;

static char *read_json_body(httpd_req_t *req, size_t *out_len)
{
    if (!req || !out_len)
        return NULL;
    *out_len = 0;

    size_t len = req->content_len;
    if (len == 0)
        return NULL;

    // Hard cap to avoid huge allocations
    if (len > 1024)
        len = 1024;

    char *buf = (char *)malloc(len + 1);
    if (!buf)
        return NULL;

    size_t off = 0;
    while (off < len)
    {
        int r = httpd_req_recv(req, buf + off, (int)(len - off));
        if (r <= 0)
        {
            free(buf);
            return NULL;
        }
        off += (size_t)r;
    }

    buf[off] = '\0';
    *out_len = off;
    return buf;
}

static bool parse_frame_id_any(const cJSON *j, uint32_t *out)
{
    if (!out)
        return false;
    *out = 0;

    if (!j)
        return false;

    if (cJSON_IsNumber(j))
    {
        if (j->valuedouble < 0 || j->valuedouble > 0x1FFFFFFF)
            return false;
        *out = (uint32_t)j->valuedouble;
        return true;
    }

    if (!cJSON_IsString(j) || !j->valuestring)
        return false;

    const char *s = j->valuestring;
    while (*s && isspace((unsigned char)*s))
        s++;
    if (!*s)
        return false;

    int base = 10;
    if ((s[0] == '0') && (s[1] == 'x' || s[1] == 'X'))
    {
        base = 16;
        s += 2;
    }
    else
    {
        // If it contains hex letters, treat as hex
        for (const char *p = s; *p; p++)
        {
            if ((*p >= 'A' && *p <= 'F') || (*p >= 'a' && *p <= 'f'))
            {
                base = 16;
                break;
            }
        }
    }

    char *endptr = NULL;
    unsigned long v = strtoul(s, &endptr, base);
    if (endptr == s)
        return false;

    if (v > 0x1FFFFFFF)
        return false;

    *out = (uint32_t)v;
    return true;
}

static void canflt_raw_sanitize_snippet(char *s)
{
    if (!s)
        return;
    for (char *p = s; *p; p++)
    {
        unsigned char c = (unsigned char)*p;
        if (c == '\r' || c == '\n' || c == '\t')
            *p = ' ';
        else if (c < 0x20)
            *p = ' ';
    }
}

static char *test_can_raw_buf = NULL;
static size_t test_can_raw_cap = 0;
static size_t test_can_raw_len = 0;

static bool test_can_raw_ensure(size_t cap)
{
    if (cap == 0)
        return false;

    if (!test_can_raw_buf || test_can_raw_cap < cap)
    {
        free(test_can_raw_buf);
        test_can_raw_buf = (char *)malloc(cap);
        test_can_raw_cap = test_can_raw_buf ? cap : 0;
        test_can_raw_len = 0;
        if (test_can_raw_buf)
            test_can_raw_buf[0] = '\0';
    }

    return (test_can_raw_buf != NULL);
}

static void test_can_raw_reset(void)
{
    test_can_raw_len = 0;
    if (test_can_raw_buf && test_can_raw_cap > 0)
        test_can_raw_buf[0] = '\0';
}

static const char *test_can_raw_get(void)
{
    return test_can_raw_buf ? test_can_raw_buf : "";
}

static void test_can_raw_snippet(char *dst, size_t dstsz)
{
    if (!dst || dstsz == 0)
        return;
    dst[0] = '\0';

    const char *raw = test_can_raw_get();
    if (!raw || raw[0] == '\0')
        return;

    size_t n = strlen(raw);
    if (n >= dstsz)
        n = dstsz - 1;

    memcpy(dst, raw, n);
    dst[n] = '\0';
    canflt_raw_sanitize_snippet(dst);
}

static void test_can_capture_cb(char *str, uint32_t len, QueueHandle_t *q, char *cmd_str)
{
    (void)q;
    (void)cmd_str;

    if (!test_can_raw_buf || test_can_raw_cap == 0)
        return;
    if (!str || len == 0)
        return;

    size_t avail = (test_can_raw_cap > 0) ? (test_can_raw_cap - 1 - test_can_raw_len) : 0;
    if (avail == 0)
        return;

    size_t to_copy = (size_t)len;
    if (to_copy > avail)
        to_copy = avail;

    memcpy(test_can_raw_buf + test_can_raw_len, str, to_copy);
    test_can_raw_len += to_copy;
    test_can_raw_buf[test_can_raw_len] = '\0';
}

static bool is_hex_n(const char *s, size_t n)
{
    if (!s || n == 0)
        return false;
    for (size_t i = 0; i < n; i++)
    {
        if (!isxdigit((unsigned char)s[i]))
            return false;
    }
    return true;
}

static bool hex_byte(const char *s, uint8_t *out)
{
    if (!s || !out)
        return false;
    if (!is_hex_n(s, 2))
        return false;
    char tmp[3] = {s[0], s[1], 0};
    char *endptr = NULL;
    long v = strtol(tmp, &endptr, 16);
    if (endptr == tmp || v < 0 || v > 255)
        return false;
    *out = (uint8_t)v;
    return true;
}

static bool atma_parse_line_for_frame(const char *line,
                                      size_t line_len,
                                      uint32_t expected_frame_id,
                                      response_t *out_rsp)
{
    if (!line || line_len == 0 || !out_rsp)
        return false;

    // Trim
    while (line_len > 0 && (line[0] == ' ' || line[0] == '\t'))
    {
        line++;
        line_len--;
    }
    while (line_len > 0 && (line[line_len - 1] == ' ' || line[line_len - 1] == '\t'))
        line_len--;
    if (line_len == 0)
        return false;

    if (line_len == 1 && line[0] == '>')
        return false;
    if (line_len >= 7 && (strncasecmp(line, "STOPPED", 7) == 0))
        return false;
    if (line_len >= 5 && (strncasecmp(line, "ERROR", 5) == 0))
        return false;
    if (line_len >= 7 && (strncasecmp(line, "NO DATA", 7) == 0))
        return false;

    const char *p = line;
    const char *end = line + line_len;

    // token1
    while (p < end && (*p == ' ' || *p == '\t'))
        p++;
    const char *t1 = p;
    while (p < end && *p != ' ' && *p != '\t')
        p++;
    size_t t1_len = (size_t)(p - t1);
    if (t1_len == 0)
        return false;

    uint32_t header = 0;
    bool header_ok = false;

    if ((t1_len == 3 || t1_len == 8) && is_hex_n(t1, t1_len))
    {
        char tmp[9] = {0};
        memcpy(tmp, t1, t1_len);
        header = (uint32_t)strtoul(tmp, NULL, 16);
        header_ok = true;
    }
    else if (t1_len == 2 && is_hex_n(t1, 2))
    {
        // header split in bytes: 00 00 08 C0 ...
        const char *t2 = NULL, *t3 = NULL, *t4 = NULL;
        size_t t2_len = 0, t3_len = 0, t4_len = 0;

        while (p < end && (*p == ' ' || *p == '\t'))
            p++;
        t2 = p;
        while (p < end && *p != ' ' && *p != '\t')
            p++;
        t2_len = (size_t)(p - t2);

        while (p < end && (*p == ' ' || *p == '\t'))
            p++;
        t3 = p;
        while (p < end && *p != ' ' && *p != '\t')
            p++;
        t3_len = (size_t)(p - t3);

        while (p < end && (*p == ' ' || *p == '\t'))
            p++;
        t4 = p;
        while (p < end && *p != ' ' && *p != '\t')
            p++;
        t4_len = (size_t)(p - t4);

        uint8_t b1 = 0, b2 = 0, b3 = 0, b4 = 0;
        if (t2_len == 2 && t3_len == 2 && t4_len == 2 &&
            hex_byte(t1, &b1) && hex_byte(t2, &b2) && hex_byte(t3, &b3) && hex_byte(t4, &b4))
        {
            header = ((uint32_t)b1 << 24) | ((uint32_t)b2 << 16) | ((uint32_t)b3 << 8) | (uint32_t)b4;
            header_ok = true;
        }
        else if (t2_len == 2 && hex_byte(t1, &b1) && hex_byte(t2, &b2))
        {
            header = ((uint32_t)b1 << 8) | (uint32_t)b2;
            header_ok = true;
        }
    }

    if (!header_ok)
        return false;

    if (expected_frame_id != 0 && header != expected_frame_id)
        return false;

    // Parse remaining tokens as bytes
    memset(out_rsp, 0, sizeof(*out_rsp));
    out_rsp->priority_data = NULL;
    out_rsp->priority_data_len = 0;

    uint32_t out_idx = 0;
    while (p < end)
    {
        while (p < end && (*p == ' ' || *p == '\t'))
            p++;
        const char *bt = p;
        while (p < end && *p != ' ' && *p != '\t')
            p++;
        size_t bt_len = (size_t)(p - bt);
        if (bt_len == 0)
            continue;
        if (bt_len == 1 && bt[0] == '>')
            break;
        if (bt_len != 2 || !is_hex_n(bt, 2))
            continue;
        if (out_idx >= sizeof(out_rsp->data))
            break;
        uint8_t b = 0;
        if (!hex_byte(bt, &b))
            continue;
        out_rsp->data[out_idx++] = b;
    }

    out_rsp->length = out_idx;
    return (out_rsp->length > 0);
}

static bool parse_first_atma_frame(const char *raw, uint32_t expected_frame_id, response_t *out_rsp)
{
    if (!raw || !out_rsp)
        return false;

    const char *p = raw;
    while (*p)
    {
        // find end of line
        const char *line = p;
        size_t line_len = 0;
        while (p[line_len] && p[line_len] != '\r' && p[line_len] != '\n')
            line_len++;

        if (line_len > 0)
        {
            if (atma_parse_line_for_frame(line, line_len, expected_frame_id, out_rsp))
                return true;
        }

        // consume line breaks
        p += line_len;
        while (*p == '\r' || *p == '\n')
            p++;
    }

    return false;
}

static void send_can_filter_cmd_http(uint32_t frame_id)
{
    // Keep CAN protocol consistent with ID width, like the AutoPID task.
    int32_t current_protocol_number = -1;
    (void)autopid_get_protocol_number(&current_protocol_number);
    bool is_extended = (frame_id > 0x7FF);

    int32_t desired_protocol_number = -1;
    if (current_protocol_number == 6 || current_protocol_number == 7)
    {
        desired_protocol_number = is_extended ? 7 : 6;
    }
    else if (current_protocol_number == 8 || current_protocol_number == 9)
    {
        desired_protocol_number = is_extended ? 9 : 8;
    }

    if (desired_protocol_number != -1 && desired_protocol_number != current_protocol_number)
    {
        char proto_cmd[10];
        snprintf(proto_cmd, sizeof(proto_cmd), "ATTP%01lX\r", (unsigned long)desired_protocol_number);
        (void)autopid_test_pid_send_cmd_sync(proto_cmd, 1200, false);
    }

    char cmd[20];
    if (frame_id <= 0x7FF)
        snprintf(cmd, sizeof(cmd), "ATCRA%03lX\r", (unsigned long)frame_id);
    else
        snprintf(cmd, sizeof(cmd), "ATCRA%08lX\r", (unsigned long)frame_id);

    (void)autopid_test_pid_send_cmd_sync(cmd, 1200, false);
}

static esp_err_t test_can_filter_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "HTTP /autopid/test_can_filter hit");

    if (config_server_protocol() != AUTO_PID)
    {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"Set Protocol to AutoPID and Submit Changes\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    if (test_canflt_lock == NULL)
    {
        test_canflt_lock = xSemaphoreCreateMutexStatic(&test_canflt_lock_buf);
    }

    if (xSemaphoreTake(test_canflt_lock, pdMS_TO_TICKS(500)) != pdTRUE)
    {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"Busy\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    if (!autopid_lock(6000))
    {
        xSemaphoreGive(test_canflt_lock);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"AutoPID busy\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    size_t body_len = 0;
    char *body = read_json_body(req, &body_len);
    if (!body)
    {
        autopid_unlock();
        xSemaphoreGive(test_canflt_lock);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"Missing JSON\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    cJSON *json = cJSON_Parse(body);
    free(body);
    body = NULL;

    if (!json)
    {
        autopid_unlock();
        xSemaphoreGive(test_canflt_lock);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"Invalid JSON\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    const cJSON *jkind = cJSON_GetObjectItemCaseSensitive(json, "kind");
    const cJSON *jfid = cJSON_GetObjectItemCaseSensitive(json, "frame_id");
    const cJSON *jexpr = cJSON_GetObjectItemCaseSensitive(json, "expr");

    char kind[16] = {0};
    char expr[192] = {0};
    uint32_t frame_id = 0;

    if (!cJSON_IsString(jkind) || !jkind->valuestring)
    {
        cJSON_Delete(json);
        autopid_unlock();
        xSemaphoreGive(test_canflt_lock);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"Missing kind\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }
    strlcpy(kind, jkind->valuestring, sizeof(kind));

    if (!parse_frame_id_any(jfid, &frame_id) || frame_id == 0)
    {
        cJSON_Delete(json);
        autopid_unlock();
        xSemaphoreGive(test_canflt_lock);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"Invalid frame_id\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    if (!cJSON_IsString(jexpr) || !jexpr->valuestring || jexpr->valuestring[0] == '\0')
    {
        cJSON_Delete(json);
        autopid_unlock();
        xSemaphoreGive(test_canflt_lock);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"Missing expr\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }
    strlcpy(expr, jexpr->valuestring, sizeof(expr));

    cJSON_Delete(json);

    if (!test_can_raw_ensure(4096))
    {
        autopid_unlock();
        xSemaphoreGive(test_canflt_lock);
        httpd_resp_send_500(req);
        return ESP_OK;
    }

    test_can_raw_reset();

    // Configure ELM for consistent output; do NOT reset chip.
    autopid_test_pid_run_init_sequence("atm0\rate0\rath1\ratl0\rats1\ratst96\r", 1200);

    // ATMA monitoring for this frame
    (void)autopid_test_pid_send_cmd_sync("ATCAF0\r", 1200, false);
    send_can_filter_cmd_http(frame_id);

    // ATMA runs until a key is sent; elm327_run_command will stop after first matching frame.
    elm327_run_command("ATMA\r", 0, 1100, NULL, test_can_capture_cb, true, frame_id);

    (void)autopid_test_pid_send_cmd_sync("ATCAF1\r", 1200, false);

    bool ok = false;
    double value = 0;
    char err_msg[96] = {0};

    response_t rsp;
    const char *raw = test_can_raw_get();
    if (!parse_first_atma_frame(raw, frame_id, &rsp))
    {
        snprintf(err_msg, sizeof(err_msg), "No frame");
        ok = false;
        goto respond;
    }

    double result = 0;
    if (!evaluate_expression((uint8_t *)expr, (uint8_t *)rsp.data, 0, &result))
    {
        snprintf(err_msg, sizeof(err_msg), "Expression eval failed");
        ok = false;
        goto respond;
    }

    result = round(result * 100.0) / 100.0;
    value = result;
    ok = true;

respond:
    {
        cJSON *root = cJSON_CreateObject();
        cJSON_AddBoolToObject(root, "ok", ok);
        if (ok)
        {
            cJSON_AddNumberToObject(root, "value", value);
        }
        else
        {
            cJSON_AddStringToObject(root, "error", err_msg[0] ? err_msg : "Failed");
        }

        {
            char snippet[192];
            test_can_raw_snippet(snippet, sizeof(snippet));
            if (snippet[0] != '\0')
                cJSON_AddStringToObject(root, "raw", snippet);
        }

        char *out = cJSON_PrintUnformatted(root);
        cJSON_Delete(root);
        httpd_resp_set_type(req, "application/json");
        if (out)
        {
            httpd_resp_send(req, out, HTTPD_RESP_USE_STRLEN);
            cJSON_free(out);
        }
        else
        {
            httpd_resp_send(req, "{\"ok\":false,\"error\":\"OOM\"}", HTTPD_RESP_USE_STRLEN);
        }
    }

    // Restore settings that can break AutoPID if left altered (echo/spacing/ATCRA).
    autopid_test_pid_restore_autopid_safe_elm_state();
    autopid_unlock();
    xSemaphoreGive(test_canflt_lock);
    return ESP_OK;
}

esp_err_t autopid_http_register_test_can_filter(httpd_handle_t server)
{
    static const httpd_uri_t test_can_uri_post = {
        .uri = "/autopid/test_can_filter",
        .method = HTTP_POST,
        .handler = test_can_filter_handler,
        .user_ctx = NULL,
    };

    if (test_canflt_lock == NULL)
    {
        test_canflt_lock = xSemaphoreCreateMutexStatic(&test_canflt_lock_buf);
    }

    esp_err_t r = httpd_register_uri_handler(server, &test_can_uri_post);
    if (r != ESP_OK && r != ESP_ERR_HTTPD_HANDLER_EXISTS)
        return r;

    ESP_LOGI(TAG, "AutoPID handlers registered at /autopid/test_can_filter");
    return ESP_OK;
}
