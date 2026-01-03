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
#include "expression_parser.h"
#include "obd2_standard_pids.h"

static const char *TAG = "AUTOPID_HTTP";

static StaticSemaphore_t test_pid_lock_buf;
static SemaphoreHandle_t test_pid_lock = NULL;

static char *read_json_body(httpd_req_t *req, size_t *out_len)
{
    if (!req || !out_len)
        return NULL;
    *out_len = 0;

    size_t len = req->content_len;
    if (len == 0)
        return NULL;

    // Hard cap to avoid huge allocations
    if (len > 2048)
        len = 2048;

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

static char *normalize_init_string_heap(const char *src)
{
    if (!src || src[0] == '\0')
        return NULL;

    size_t n = strlen(src);
    char *out = (char *)malloc(n + 1);
    if (!out)
        return NULL;
    memcpy(out, src, n + 1);

    // Replace ';' with '\r' and normalize ATSP->ATTP (similar to autopid_config)
    for (size_t i = 0; out[i] != '\0'; i++)
    {
        if (out[i] == ';')
            out[i] = '\r';
    }

    // Replace occurrences of ATSP with ATTP (case-insensitive)
    for (size_t i = 0; out[i] != '\0'; i++)
    {
        if ((out[i] == 'A' || out[i] == 'a') && (out[i + 1] == 'T' || out[i + 1] == 't') &&
            (out[i + 2] == 'S' || out[i + 2] == 's') && (out[i + 3] == 'P' || out[i + 3] == 'p'))
        {
            out[i] = 'A';
            out[i + 1] = 'T';
            out[i + 2] = 'T';
            out[i + 3] = 'P';
        }
    }

    return out;
}

static esp_err_t test_pid_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "HTTP /autopid/test_pid hit (qlen=%u)", (unsigned)httpd_req_get_url_query_len(req));

    if (config_server_protocol() != AUTO_PID)
    {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"Set Protocol to AutoPID and Submit Changes\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    if (test_pid_lock == NULL)
    {
        test_pid_lock = xSemaphoreCreateMutexStatic(&test_pid_lock_buf);
    }

    if (xSemaphoreTake(test_pid_lock, pdMS_TO_TICKS(500)) != pdTRUE)
    {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"Busy\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    // Share the same mutex used by the AutoPID task to serialize access to ELM327.
    // Without this, the periodic AutoPID polling/ATMA can interleave with the test
    // request, producing echo-only buffers, "STOPPED", and truncated responses.
    if (!autopid_lock(6000))
    {
        xSemaphoreGive(test_pid_lock);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, "{\"ok\":false,\"error\":\"AutoPID busy\"}", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    // Accept either GET query params or POST JSON body.
    // If a JSON body is present, it takes precedence.
    char *query = NULL;
    size_t qlen = httpd_req_get_url_query_len(req);
    if (qlen > 0)
    {
        query = (char *)malloc(qlen + 1);
        if (!query)
        {
            autopid_unlock();
            xSemaphoreGive(test_pid_lock);
            httpd_resp_send_500(req);
            return ESP_OK;
        }
        if (httpd_req_get_url_query_str(req, query, qlen + 1) != ESP_OK)
        {
            free(query);
            query = NULL;
        }
    }

    size_t body_len = 0;
    char *body = read_json_body(req, &body_len);
    cJSON *body_json = NULL;
    if (body)
    {
        body_json = cJSON_Parse(body);
        if (!body_json)
        {
            free(body);
            if (query)
                free(query);
            autopid_unlock();
            xSemaphoreGive(test_pid_lock);
            httpd_resp_set_type(req, "application/json");
            httpd_resp_send(req, "{\"ok\":false,\"error\":\"Invalid JSON\"}", HTTPD_RESP_USE_STRLEN);
            return ESP_OK;
        }
    }

    char kind[16] = {0};
    char name[64] = {0};
    char protocol[8] = {0};
    char rxheader[16] = {0};
    char init[256] = {0};
    char pid_init[256] = {0};
    char pid_cmd[64] = {0};
    char expr[128] = {0};

    if (body_json)
    {
        const cJSON *jk = cJSON_GetObjectItemCaseSensitive(body_json, "kind");
        const cJSON *jn = cJSON_GetObjectItemCaseSensitive(body_json, "name");
        const cJSON *jp = cJSON_GetObjectItemCaseSensitive(body_json, "protocol");
        const cJSON *jrh = cJSON_GetObjectItemCaseSensitive(body_json, "rxheader");
        const cJSON *ji = cJSON_GetObjectItemCaseSensitive(body_json, "init");
        const cJSON *jpi = cJSON_GetObjectItemCaseSensitive(body_json, "pid_init");
        const cJSON *jpid = cJSON_GetObjectItemCaseSensitive(body_json, "pid");
        const cJSON *je = cJSON_GetObjectItemCaseSensitive(body_json, "expr");

        if (!cJSON_IsString(jk) || !jk->valuestring)
        {
            cJSON_Delete(body_json);
            free(body);
            if (query)
                free(query);
            autopid_unlock();
            xSemaphoreGive(test_pid_lock);
            httpd_resp_set_type(req, "application/json");
            httpd_resp_send(req, "{\"ok\":false,\"error\":\"Missing kind\"}", HTTPD_RESP_USE_STRLEN);
            return ESP_OK;
        }

        strlcpy(kind, jk->valuestring, sizeof(kind));
        if (cJSON_IsString(jn) && jn->valuestring)
            strlcpy(name, jn->valuestring, sizeof(name));
        if (cJSON_IsString(jp) && jp->valuestring)
            strlcpy(protocol, jp->valuestring, sizeof(protocol));
        else if (cJSON_IsNumber(jp))
            snprintf(protocol, sizeof(protocol), "%d", jp->valueint);
        if (cJSON_IsString(jrh) && jrh->valuestring)
            strlcpy(rxheader, jrh->valuestring, sizeof(rxheader));
        if (cJSON_IsString(ji) && ji->valuestring)
            strlcpy(init, ji->valuestring, sizeof(init));
        if (cJSON_IsString(jpi) && jpi->valuestring)
            strlcpy(pid_init, jpi->valuestring, sizeof(pid_init));
        if (cJSON_IsString(jpid) && jpid->valuestring)
            strlcpy(pid_cmd, jpid->valuestring, sizeof(pid_cmd));
        if (cJSON_IsString(je) && je->valuestring)
            strlcpy(expr, je->valuestring, sizeof(expr));

        cJSON_Delete(body_json);
        free(body);
        body_json = NULL;
        body = NULL;
        if (query)
        {
            free(query);
            query = NULL;
        }
    }
    else
    {
        if (!query || httpd_query_key_value(query, "kind", kind, sizeof(kind)) != ESP_OK)
        {
            if (query)
                free(query);
            autopid_unlock();
            xSemaphoreGive(test_pid_lock);
            httpd_resp_set_type(req, "application/json");
            httpd_resp_send(req, "{\"ok\":false,\"error\":\"Missing kind\"}", HTTPD_RESP_USE_STRLEN);
            return ESP_OK;
        }

        (void)httpd_query_key_value(query, "name", name, sizeof(name));
        (void)httpd_query_key_value(query, "protocol", protocol, sizeof(protocol));
        (void)httpd_query_key_value(query, "rxheader", rxheader, sizeof(rxheader));
        (void)httpd_query_key_value(query, "init", init, sizeof(init));
        (void)httpd_query_key_value(query, "pid_init", pid_init, sizeof(pid_init));
        (void)httpd_query_key_value(query, "pid", pid_cmd, sizeof(pid_cmd));
        (void)httpd_query_key_value(query, "expr", expr, sizeof(expr));

        if (query)
            free(query);
    }

    if (!autopid_test_pid_raw_ensure(4096))
    {
        autopid_unlock();
        xSemaphoreGive(test_pid_lock);
        httpd_resp_send_500(req);
        return ESP_OK;
    }
    autopid_test_pid_raw_reset();

    ESP_LOGI(TAG,
             "test_pid parsed: kind='%s' name='%s' proto='%s' rxheader='%s' pid='%s'",
             kind,
             name,
             protocol,
             rxheader,
             pid_cmd);

    double value = 0;
    bool ok = false;
    const char *unit = "";
    char err_msg[128] = {0};

    if (strcmp(kind, "std") == 0)
    {
        if (name[0] == '\0')
        {
            snprintf(err_msg, sizeof(err_msg), "Missing name");
            ok = false;
            goto respond;
        }

        char pid_hex[3] = {0};
        pid_hex[0] = name[0];
        pid_hex[1] = name[1];
        uint8_t pid_num = (uint8_t)strtoul(pid_hex, NULL, 16);
        const std_pid_t *pid_info = get_pid(pid_num);
        if (!pid_info)
        {
            snprintf(err_msg, sizeof(err_msg), "Unknown PID");
            ok = false;
            goto respond;
        }

        const char *param_name = strchr(name, '-');
        if (!param_name || param_name[1] == '\0')
        {
            snprintf(err_msg, sizeof(err_msg), "Bad name");
            ok = false;
            goto respond;
        }
        param_name++;

        const std_parameter_t *param = NULL;
        for (int i = 0; i < pid_info->num_params; i++)
        {
            if (pid_info->params[i].name && strcmp(pid_info->params[i].name, param_name) == 0)
            {
                param = &pid_info->params[i];
                break;
            }
        }
        if (!param)
        {
            snprintf(err_msg, sizeof(err_msg), "Param not found");
            ok = false;
            goto respond;
        }
        unit = param->unit ? param->unit : "";

        int proto = atoi(protocol);
        int32_t current_proto = -1;
        (void)autopid_get_protocol_number(&current_proto);
        if (proto <= 0 && current_proto > 0)
        {
            // UI may send protocol "0" (auto). While AutoPID is already running,
            // forcing ATTP0 can trigger re-autodetect and cause long delays/timeouts.
            // Reuse the currently-known protocol (e.g., 6) instead.
            proto = (int)current_proto;
        }

        // Base ELM config (keeps output consistent) - do NOT reset chip (ATWS).
        autopid_test_pid_run_init_sequence("atm0\rate0\rath1\ratl0\rats1\ratst96\r", 1200);

        // Keep Standard test as close as possible to AutoPID behavior.
        // - Do NOT force auto protocol (ATTP0) mid-run.
        // - Only set protocol if we have a concrete number.
        // - Optionally set receive filter (ATCRA) if provided.
        {
            char init_buf[64];
            if (proto > 0)
            {
                if (rxheader[0] != '\0')
                    snprintf(init_buf, sizeof(init_buf), "ATTP%d\rATCRA%s\r", proto, rxheader);
                else
                    snprintf(init_buf, sizeof(init_buf), "ATTP%d\rATCRA\r", proto);
            }
            else
            {
                // No protocol provided and none detected; avoid changing it.
                if (rxheader[0] != '\0')
                    snprintf(init_buf, sizeof(init_buf), "ATCRA%s\r", rxheader);
                else
                    snprintf(init_buf, sizeof(init_buf), "ATCRA\r");
            }

            autopid_test_pid_run_init_sequence(init_buf, 1200);
        }

        char cmd[8];
        snprintf(cmd, sizeof(cmd), "01%02X\r", pid_num);
        autopid_test_pid_raw_reset();
        if (!autopid_test_pid_send_cmd_sync(cmd, 6000, true))
        {
            ESP_LOGW(TAG, "STD test PID timed out waiting for response. cmd='%s'", cmd);
            snprintf(err_msg, sizeof(err_msg), "Timeout");
            ok = false;
            goto respond;
        }

        uint8_t bytes[128];
        uint32_t bytes_len = 0;
        const char *raw = autopid_test_pid_raw_get();
        if (!autopid_test_pid_parse_hex_byte_stream(raw, bytes, sizeof(bytes), &bytes_len))
        {
            snprintf(err_msg, sizeof(err_msg), "No response");
            ok = false;
            goto respond;
        }

        // Require a positive response (41 <pid>) and normalize the buffer layout.
        // AutoPID's std PID extraction expects data to be aligned such that the
        // first PID data byte is at index 3 (len/service/pid/data...).
        int resp_idx = -1;
        for (uint32_t i = 0; i + 1 < bytes_len; i++)
        {
            if (bytes[i] == 0x41 && bytes[i + 1] == pid_num)
            {
                resp_idx = (int)i;
                break;
            }
        }
        if (resp_idx < 0)
        {
            snprintf(err_msg, sizeof(err_msg), "No positive response");
            ok = false;
            goto respond;
        }

        uint8_t norm[128];
        uint32_t norm_len = 0;
        norm[norm_len++] = 0x00; // dummy length (keeps indexes consistent)
        norm[norm_len++] = 0x41;
        norm[norm_len++] = pid_num;
        for (uint32_t j = (uint32_t)resp_idx + 2; j < bytes_len && norm_len < sizeof(norm); j++)
        {
            norm[norm_len++] = bytes[j];
        }

        const uint8_t *win = norm;
        uint32_t win_len = norm_len;

        uint8_t start_byte = param->bit_start / 8;
        uint8_t bytes_needed = (param->bit_length + 7) / 8;
        if (!win || start_byte + bytes_needed > win_len)
        {
            snprintf(err_msg, sizeof(err_msg), "Response too short");
            ok = false;
            goto respond;
        }

        uint32_t raw_value = 0;
        for (uint8_t i = 0; i < bytes_needed; i++)
        {
            raw_value = (raw_value << 8) | win[start_byte + i];
        }

        uint32_t mask = (uint32_t)((1ULL << param->bit_length) - 1ULL);
        raw_value &= mask;
        float fval = (float)raw_value * param->scale + param->offset;
        if (fval < param->min)
            fval = param->min;
        if (fval > param->max)
            fval = param->max;
        value = (double)fval;
        ok = true;
    }
    else if (strcmp(kind, "custom") == 0 || strcmp(kind, "vehicle") == 0)
    {
        if (pid_cmd[0] == '\0' || expr[0] == '\0')
        {
            snprintf(err_msg, sizeof(err_msg), "Missing pid/expr");
            ok = false;
            goto respond;
        }

        // Base ELM config for consistent output - do NOT reset chip (ATWS).
        autopid_test_pid_run_init_sequence("atm0\rate0\rath1\ratl0\rats1\ratst96\r", 1200);

        char *n_init = normalize_init_string_heap(init);
        char *n_row_init = normalize_init_string_heap(pid_init);

        // Initialisation before per-PID init and PID (as requested)
        if (n_init)
            autopid_test_pid_run_init_sequence(n_init, 1200);
        if (n_row_init)
            autopid_test_pid_run_init_sequence(n_row_init, 1200);

        char pid_send[80];
        size_t pid_len = strlen(pid_cmd);
        bool has_cr = (pid_len > 0 && pid_cmd[pid_len - 1] == '\r');
        if (has_cr)
            snprintf(pid_send, sizeof(pid_send), "%s", pid_cmd);
        else
            snprintf(pid_send, sizeof(pid_send), "%s\r", pid_cmd);

        autopid_test_pid_raw_reset();
        if (!autopid_test_pid_send_cmd_sync(pid_send, 6000, true))
        {
            ESP_LOGW(TAG, "CUSTOM/VEHICLE test PID timed out waiting for response. pid='%s'", pid_send);
            snprintf(err_msg, sizeof(err_msg), "Timeout");
            if (n_init)
                free(n_init);
            if (n_row_init)
                free(n_row_init);
            ok = false;
            goto respond;
        }

        if (n_init)
            free(n_init);
        if (n_row_init)
            free(n_row_init);

        const char *raw = autopid_test_pid_raw_get();

        // If the adapter responded with a textual error, fail early.
        // NOTE: "NO DATA" contains hex letters ("DA"), which can trick our hex parser
        // into producing bytes; that must not be treated as a valid response.
        if (raw && (autopid_test_pid_contains_case_insensitive(raw, "NO DATA") ||
                    autopid_test_pid_contains_case_insensitive(raw, "ERROR") ||
                    autopid_test_pid_contains_case_insensitive(raw, "STOPPED")))
        {
            snprintf(err_msg, sizeof(err_msg), "NO DATA");
            if (autopid_test_pid_contains_case_insensitive(raw, "ERROR"))
                snprintf(err_msg, sizeof(err_msg), "ERROR");
            else if (autopid_test_pid_contains_case_insensitive(raw, "STOPPED"))
                snprintf(err_msg, sizeof(err_msg), "STOPPED");
            ok = false;
            goto respond;
        }

        uint8_t bytes[256];
        uint32_t bytes_len = 0;
        if (!autopid_test_pid_parse_hex_byte_stream(raw, bytes, sizeof(bytes), &bytes_len))
        {
            snprintf(err_msg, sizeof(err_msg), "No response");
            ok = false;
            goto respond;
        }

        uint8_t req_service = 0;
        if (isxdigit((unsigned char)pid_send[0]) && isxdigit((unsigned char)pid_send[1]))
        {
            char svc_hex[3] = {pid_send[0], pid_send[1], 0};
            req_service = (uint8_t)strtoul(svc_hex, NULL, 16);
        }
        uint8_t pos_service = (req_service <= 0x3F) ? (uint8_t)(req_service + 0x40) : 0;
        const uint8_t *win = bytes;
        uint32_t win_len = bytes_len;
        if (pos_service != 0)
        {
            bool found = autopid_test_pid_find_response_window(bytes, bytes_len, pos_service, 0xFF, &win, &win_len);
            if (!found)
            {
                snprintf(err_msg, sizeof(err_msg), "No positive response");
                ok = false;
                goto respond;
            }
        }

        // Evaluate against a zero-padded buffer to avoid reading garbage when
        // expressions reference bytes beyond the received response length.
        uint8_t padded[512];
        memset(padded, 0, sizeof(padded));
        if (win && win_len > 0)
        {
            uint32_t copy_len = win_len;
            if (copy_len > sizeof(padded))
                copy_len = sizeof(padded);
            memcpy(padded, win, copy_len);
        }

        double result = 0;
        if (evaluate_expression((uint8_t *)expr, (uint8_t *)padded, 0, &result))
        {
            result = round(result * 100.0) / 100.0;
            value = result;
            ok = true;
        }
        else
        {
            snprintf(err_msg, sizeof(err_msg), "Expression eval failed");
            ok = false;
        }
    }
    else
    {
        snprintf(err_msg, sizeof(err_msg), "Unknown kind");
        ok = false;
    }

respond:
    {
        cJSON *root = cJSON_CreateObject();
        cJSON_AddBoolToObject(root, "ok", ok);
        if (ok)
        {
            cJSON_AddNumberToObject(root, "value", value);
            cJSON_AddStringToObject(root, "unit", unit ? unit : "");
        }
        else
        {
            cJSON_AddStringToObject(root, "error", err_msg[0] ? err_msg : "Failed");
        }

        {
            char snippet[192];
            autopid_test_pid_raw_snippet(snippet, sizeof(snippet));
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
    xSemaphoreGive(test_pid_lock);
    return ESP_OK;
}

esp_err_t autopid_http_register_test_pid(httpd_handle_t server)
{
    static const httpd_uri_t test_pid_uri = {
        .uri = "/autopid/test_pid",
        .method = HTTP_GET,
        .handler = test_pid_handler,
        .user_ctx = NULL,
    };

    static const httpd_uri_t test_pid_uri_post = {
        .uri = "/autopid/test_pid",
        .method = HTTP_POST,
        .handler = test_pid_handler,
        .user_ctx = NULL,
    };

    if (test_pid_lock == NULL)
    {
        test_pid_lock = xSemaphoreCreateMutexStatic(&test_pid_lock_buf);
    }

    esp_err_t r = httpd_register_uri_handler(server, &test_pid_uri);
    if (r != ESP_OK && r != ESP_ERR_HTTPD_HANDLER_EXISTS)
        return r;

    r = httpd_register_uri_handler(server, &test_pid_uri_post);
    if (r != ESP_OK && r != ESP_ERR_HTTPD_HANDLER_EXISTS)
        return r;

    ESP_LOGI(TAG, "AutoPID handlers registered at /autopid/test_pid");
    return ESP_OK;
}
