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

#include "restart_tracker_http.h"

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <cJSON.h>
#include <esp_err.h>

#include "restart_tracker.h"
#include "restart_tracker_coredump.h"

static size_t restart_tracker_history_count(const restart_tracker_state_t *state)
{
    if (state == NULL)
    {
        return 0;
    }

    if (state->boot_count < state->history_len)
    {
        return state->boot_count;
    }

    return state->history_len;
}

static void restart_tracker_format_timestamp(int64_t timestamp, bool time_valid, char *buf, size_t buf_len)
{
    if (buf == NULL || buf_len == 0U)
    {
        return;
    }

    if (!time_valid || timestamp <= 0)
    {
        strlcpy(buf, "unsynced", buf_len);
        return;
    }

    time_t raw = (time_t)timestamp;
    struct tm timeinfo;
    if (gmtime_r(&raw, &timeinfo) == NULL || strftime(buf, buf_len, "%Y-%m-%dT%H:%M:%SZ", &timeinfo) == 0U)
    {
        strlcpy(buf, "unsynced", buf_len);
    }
}

static void restart_tracker_format_flags(uint32_t flags, char *buf, size_t buf_len)
{
    if (buf == NULL || buf_len == 0U)
    {
        return;
    }

    if (flags == RESTART_TRACKER_FLAG_NONE)
    {
        strlcpy(buf, "none", buf_len);
        return;
    }

    buf[0] = '\0';

    if ((flags & RESTART_TRACKER_FLAG_SETTINGS_SAVED) != 0U)
    {
        strlcat(buf, "settings_saved", buf_len);
    }
    if ((flags & RESTART_TRACKER_FLAG_FILESYSTEM_CHANGED) != 0U)
    {
        if (buf[0] != '\0')
        {
            strlcat(buf, ",", buf_len);
        }
        strlcat(buf, "filesystem_changed", buf_len);
    }
    if ((flags & RESTART_TRACKER_FLAG_NVS_ERASED) != 0U)
    {
        if (buf[0] != '\0')
        {
            strlcat(buf, ",", buf_len);
        }
        strlcat(buf, "nvs_erased", buf_len);
    }
    if ((flags & RESTART_TRACKER_FLAG_FIRMWARE_UPDATED) != 0U)
    {
        if (buf[0] != '\0')
        {
            strlcat(buf, ",", buf_len);
        }
        strlcat(buf, "firmware_updated", buf_len);
    }
    if ((flags & RESTART_TRACKER_FLAG_RECOVERY_ACTION) != 0U)
    {
        if (buf[0] != '\0')
        {
            strlcat(buf, ",", buf_len);
        }
        strlcat(buf, "recovery_action", buf_len);
    }
}

static cJSON *restart_tracker_record_to_json(const restart_tracker_record_t *record)
{
    char boot_timestamp[32];
    char request_timestamp[32];
    char flags[96];
    cJSON *obj;

    if (record == NULL)
    {
        return NULL;
    }

    restart_tracker_format_timestamp(record->boot_timestamp, record->time_valid != 0U, boot_timestamp, sizeof(boot_timestamp));
    restart_tracker_format_timestamp(record->request_timestamp,
                                     record->was_planned != 0U && record->request_timestamp > 0,
                                     request_timestamp,
                                     sizeof(request_timestamp));
    restart_tracker_format_flags(record->flags, flags, sizeof(flags));

    obj = cJSON_CreateObject();
    if (obj == NULL)
    {
        return NULL;
    }

    cJSON_AddNumberToObject(obj, "sequence", record->sequence);
    cJSON_AddNumberToObject(obj, "actual_reset_reason_code", record->actual_reset_reason);
    cJSON_AddStringToObject(obj,
                            "actual_reset_reason",
                            restart_tracker_reset_reason_to_str((esp_reset_reason_t)record->actual_reset_reason));
    cJSON_AddBoolToObject(obj, "was_planned", record->was_planned != 0U);
    cJSON_AddStringToObject(obj,
                            "planned_reason",
                            record->was_planned != 0U
                                ? restart_tracker_planned_reason_to_str((restart_tracker_planned_reason_t)record->planned_reason)
                                : restart_tracker_planned_reason_to_str(RESTART_TRACKER_PLANNED_REASON_NONE));
    cJSON_AddStringToObject(obj,
                            "source",
                            record->was_planned != 0U
                                ? restart_tracker_source_to_str((restart_tracker_source_t)record->source)
                                : restart_tracker_source_to_str(RESTART_TRACKER_SOURCE_UNKNOWN));
    cJSON_AddNumberToObject(obj, "flags", record->flags);
    cJSON_AddStringToObject(obj, "flags_text", flags);
    cJSON_AddBoolToObject(obj, "time_valid", record->time_valid != 0U);
    cJSON_AddNumberToObject(obj, "boot_timestamp_unix", (double)record->boot_timestamp);
    cJSON_AddStringToObject(obj, "boot_timestamp", boot_timestamp);
    cJSON_AddNumberToObject(obj, "request_timestamp_unix", (double)record->request_timestamp);
    cJSON_AddStringToObject(obj, "request_timestamp", request_timestamp);
    cJSON_AddNumberToObject(obj, "request_uptime_ms", (double)record->request_uptime_ms);

    return obj;
}

static cJSON *restart_tracker_pending_to_json(const restart_tracker_pending_restart_t *pending)
{
    char request_timestamp[32];
    char flags[96];
    cJSON *obj;

    if (pending == NULL)
    {
        return NULL;
    }

    restart_tracker_format_timestamp(pending->requested_timestamp,
                                     pending->time_valid != 0U && pending->valid != 0U,
                                     request_timestamp,
                                     sizeof(request_timestamp));
    restart_tracker_format_flags(pending->flags, flags, sizeof(flags));

    obj = cJSON_CreateObject();
    if (obj == NULL)
    {
        return NULL;
    }

    cJSON_AddBoolToObject(obj, "valid", pending->valid != 0U);
    cJSON_AddBoolToObject(obj, "time_valid", pending->time_valid != 0U);
    cJSON_AddNumberToObject(obj, "requested_timestamp_unix", (double)pending->requested_timestamp);
    cJSON_AddStringToObject(obj, "requested_timestamp", request_timestamp);
    cJSON_AddNumberToObject(obj, "requested_uptime_ms", (double)pending->requested_uptime_ms);
    cJSON_AddNumberToObject(obj, "flags", pending->flags);
    cJSON_AddStringToObject(obj, "flags_text", flags);
    cJSON_AddStringToObject(obj,
                            "planned_reason",
                            pending->valid != 0U
                                ? restart_tracker_planned_reason_to_str((restart_tracker_planned_reason_t)pending->planned_reason)
                                : restart_tracker_planned_reason_to_str(RESTART_TRACKER_PLANNED_REASON_NONE));
    cJSON_AddStringToObject(obj,
                            "source",
                            pending->valid != 0U
                                ? restart_tracker_source_to_str((restart_tracker_source_t)pending->source)
                                : restart_tracker_source_to_str(RESTART_TRACKER_SOURCE_UNKNOWN));

    return obj;
}

static esp_err_t restart_tracker_send_json(httpd_req_t *req, cJSON *obj)
{
    char *payload;

    if (obj == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "no response");
    }

    payload = cJSON_PrintUnformatted(obj);
    cJSON_Delete(obj);
    if (payload == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "serialize error");
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, payload, HTTPD_RESP_USE_STRLEN);
    free(payload);
    return ESP_OK;
}

static esp_err_t restart_tracker_status_handler(httpd_req_t *req)
{
    restart_tracker_state_t state = {0};
    restart_tracker_record_t latest = {0};
    esp_err_t ret = restart_tracker_get_state(&state);
    cJSON *response = cJSON_CreateObject();

    if (response == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json alloc");
    }

    cJSON_AddBoolToObject(response, "available", ret == ESP_OK);
    if (ret != ESP_OK)
    {
        cJSON_AddStringToObject(response, "error", esp_err_to_name(ret));
        cJSON_AddNullToObject(response, "latest");
        cJSON_AddNullToObject(response, "pending");
        cJSON_AddNumberToObject(response, "boot_count", 0);
        cJSON_AddNumberToObject(response, "unexpected_reset_count", 0);
        cJSON_AddNumberToObject(response, "history_count", 0);
        cJSON_AddNumberToObject(response, "history_capacity", RESTART_TRACKER_HISTORY_LEN);
        return restart_tracker_send_json(req, response);
    }

    cJSON_AddNumberToObject(response, "boot_count", state.boot_count);
    cJSON_AddNumberToObject(response, "unexpected_reset_count", state.unexpected_reset_count);
    cJSON_AddNumberToObject(response, "history_count", restart_tracker_history_count(&state));
    cJSON_AddNumberToObject(response, "history_capacity", state.history_len);

    if (restart_tracker_get_latest_record(&latest) == ESP_OK)
    {
        cJSON_AddItemToObject(response, "latest", restart_tracker_record_to_json(&latest));
    }
    else
    {
        cJSON_AddNullToObject(response, "latest");
    }

    cJSON_AddItemToObject(response, "pending", restart_tracker_pending_to_json(&state.pending_restart));

    return restart_tracker_send_json(req, response);
}

static esp_err_t restart_tracker_history_handler(httpd_req_t *req)
{
    restart_tracker_state_t state = {0};
    esp_err_t ret = restart_tracker_get_state(&state);
    cJSON *response = cJSON_CreateObject();
    cJSON *records;
    size_t history_count;

    if (response == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json alloc");
    }

    cJSON_AddBoolToObject(response, "available", ret == ESP_OK);
    cJSON_AddNumberToObject(response, "history_capacity", RESTART_TRACKER_HISTORY_LEN);

    if (ret != ESP_OK)
    {
        cJSON_AddStringToObject(response, "error", esp_err_to_name(ret));
        cJSON_AddNumberToObject(response, "boot_count", 0);
        cJSON_AddNumberToObject(response, "unexpected_reset_count", 0);
        cJSON_AddNumberToObject(response, "history_count", 0);
        cJSON_AddItemToObject(response, "records", cJSON_CreateArray());
        return restart_tracker_send_json(req, response);
    }

    history_count = restart_tracker_history_count(&state);
    cJSON_AddNumberToObject(response, "boot_count", state.boot_count);
    cJSON_AddNumberToObject(response, "unexpected_reset_count", state.unexpected_reset_count);
    cJSON_AddNumberToObject(response, "history_count", history_count);
    records = cJSON_AddArrayToObject(response, "records");

    if (records != NULL)
    {
        for (size_t offset = 0, added = 0; offset < RESTART_TRACKER_HISTORY_LEN && added < history_count; ++offset)
        {
            uint32_t index = (state.latest_history_index + RESTART_TRACKER_HISTORY_LEN - offset) % RESTART_TRACKER_HISTORY_LEN;
            const restart_tracker_record_t *record = &state.history[index];
            cJSON *record_obj;

            if (record->sequence == 0U)
            {
                continue;
            }

            record_obj = restart_tracker_record_to_json(record);
            if (record_obj != NULL)
            {
                cJSON_AddItemToArray(records, record_obj);
                ++added;
            }
        }
    }

    return restart_tracker_send_json(req, response);
}

static esp_err_t restart_tracker_pending_handler(httpd_req_t *req)
{
    restart_tracker_state_t state = {0};
    esp_err_t ret = restart_tracker_get_state(&state);
    cJSON *response = cJSON_CreateObject();

    if (response == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json alloc");
    }

    cJSON_AddBoolToObject(response, "available", ret == ESP_OK);
    if (ret != ESP_OK)
    {
        cJSON_AddStringToObject(response, "error", esp_err_to_name(ret));
        cJSON_AddNullToObject(response, "pending");
        return restart_tracker_send_json(req, response);
    }

    cJSON_AddItemToObject(response, "pending", restart_tracker_pending_to_json(&state.pending_restart));
    return restart_tracker_send_json(req, response);
}

static esp_err_t restart_tracker_coredumps_handler(httpd_req_t *req)
{
    return restart_tracker_send_json(req,
                                     restart_tracker_coredump_list_to_json("/restart_tracker/coredumps/download"));
}

static esp_err_t restart_tracker_coredumps_alias_handler(httpd_req_t *req)
{
    return restart_tracker_send_json(req,
                                     restart_tracker_coredump_list_to_json("/coredumps/download"));
}

static esp_err_t restart_tracker_coredumps_download_handler(httpd_req_t *req)
{
    char query[128];
    char name[128];

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(query, "name", name, sizeof(name)) != ESP_OK)
    {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "missing name");
    }

    return restart_tracker_coredump_send_file(req, name);
}

static esp_err_t restart_tracker_router_handler(httpd_req_t *req)
{
    const char *uri = req->uri;
    const char *segment = uri + strlen("/restart_tracker");
    char route[40] = {0};
    size_t route_len;

    if (*segment == '/')
    {
        ++segment;
    }

    route_len = strcspn(segment, "?");
    if (route_len >= sizeof(route))
    {
        route_len = sizeof(route) - 1U;
    }
    memcpy(route, segment, route_len);
    route[route_len] = '\0';

    if (req->method != HTTP_GET)
    {
        return httpd_resp_send_err(req, HTTPD_405_METHOD_NOT_ALLOWED, "method");
    }

    if (route[0] == '\0' || strcmp(route, "status") == 0)
    {
        return restart_tracker_status_handler(req);
    }

    if (strcmp(route, "history") == 0)
    {
        return restart_tracker_history_handler(req);
    }

    if (strcmp(route, "pending") == 0)
    {
        return restart_tracker_pending_handler(req);
    }

    if (strcmp(route, "coredumps") == 0)
    {
        return restart_tracker_coredumps_handler(req);
    }

    if (strcmp(route, "coredumps/download") == 0)
    {
        return restart_tracker_coredumps_download_handler(req);
    }

    return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "route");
}

esp_err_t restart_tracker_register_handlers(httpd_handle_t server)
{
    static const httpd_uri_t restart_tracker_uri = {
        .uri = "/restart_tracker*",
        .method = HTTP_GET,
        .handler = restart_tracker_router_handler,
    };
    static const httpd_uri_t coredumps_uri = {
        .uri = "/coredumps",
        .method = HTTP_GET,
        .handler = restart_tracker_coredumps_alias_handler,
    };
    static const httpd_uri_t coredumps_download_uri = {
        .uri = "/coredumps/download",
        .method = HTTP_GET,
        .handler = restart_tracker_coredumps_download_handler,
    };
    const httpd_uri_t *handlers[] = {
        &restart_tracker_uri,
        &coredumps_uri,
        &coredumps_download_uri,
    };

    for (size_t i = 0; i < sizeof(handlers) / sizeof(handlers[0]); ++i)
    {
        esp_err_t ret = httpd_register_uri_handler(server, handlers[i]);
        if (ret != ESP_OK && ret != ESP_ERR_HTTPD_HANDLER_EXISTS)
        {
            return ret;
        }
    }

    return ESP_OK;
}