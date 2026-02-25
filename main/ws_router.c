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

#include "ws_router.h"

#include <string.h>
#include <stdio.h>

#include "esp_log.h"
#include "cJSON.h"
#include "cmdline.h"
#include "elm327.h"
#include "dev_status.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#define TAG "WS_ROUTER"

typedef enum {
	WS_ROUTER_MODE_MONITOR = 0,
	WS_ROUTER_MODE_TERMINAL = 1,
} ws_router_mode_t;

typedef enum {
	WS_TERMINAL_TYPE_CONSOLE = 0,
	WS_TERMINAL_TYPE_ELM327 = 1,
} ws_terminal_type_t;

static ws_router_mode_t s_mode = WS_ROUTER_MODE_MONITOR;

static ws_terminal_type_t s_term_type = WS_TERMINAL_TYPE_CONSOLE;
static bool s_autopid_was_enabled_before_elm = false;

static SemaphoreHandle_t s_term_mutex;
static StaticSemaphore_t s_term_mutex_buf;
static httpd_req_t *s_term_req;

static esp_err_t ws_router_send_json(httpd_req_t *req, const char *json)
{
	httpd_ws_frame_t ws_pkt;
	memset(&ws_pkt, 0, sizeof(ws_pkt));
	ws_pkt.type = HTTPD_WS_TYPE_TEXT;
	ws_pkt.payload = (uint8_t *)json;
	ws_pkt.len = strlen(json);
	return httpd_ws_send_frame(req, &ws_pkt);
}

static void ws_router_append_prompt(char *out, size_t out_len)
{
	// For ELM327 we don't inject a prompt; the ELM/STN device outputs '>' itself.
	if (s_term_type == WS_TERMINAL_TYPE_CONSOLE)
	{
		strlcat(out, "wican> ", out_len);
	}
}

static void ws_router_send_term_out(httpd_req_t *req, const char *data)
{
	cJSON *root = cJSON_CreateObject();
	if (root == NULL)
	{
		return;
	}
	cJSON_AddStringToObject(root, "type", "term_out");
	cJSON_AddStringToObject(root, "data", (data != NULL) ? data : "");
	char *json = cJSON_PrintUnformatted(root);
	cJSON_Delete(root);
	if (json == NULL)
	{
		return;
	}
	(void)ws_router_send_json(req, json);
	free(json);
}

static void ws_terminal_output_cb(const char *data, size_t len)
{
	if (s_term_req == NULL || data == NULL || len == 0)
	{
		return;
	}

	char *tmp = (char *)malloc(len + 1);
	if (tmp == NULL)
	{
		return;
	}
	memcpy(tmp, data, len);
	tmp[len] = '\0';

	ws_router_send_term_out(s_term_req, tmp);
	free(tmp);
}

static void ws_elm327_output_cb(char *data, uint32_t len, QueueHandle_t *q, char *cmd_str)
{
	(void)q;
	(void)cmd_str;
	ws_terminal_output_cb(data, (size_t)len);
}

static void ws_router_set_terminal_type(ws_terminal_type_t t)
{
	if (t == s_term_type)
	{
		return;
	}

	// Leaving elm327: restore AutoPID state if we paused it.
	if (s_term_type == WS_TERMINAL_TYPE_ELM327)
	{
		if (s_autopid_was_enabled_before_elm)
		{
			dev_status_set_autopid_enabled();
		}
		s_autopid_was_enabled_before_elm = false;
	}

	s_term_type = t;

	// Entering elm327: pause AutoPID so it doesn't contend for the ELM chip.
	if (s_term_type == WS_TERMINAL_TYPE_ELM327)
	{
		s_autopid_was_enabled_before_elm = dev_status_is_autopid_enabled();
		dev_status_clear_autopid_enabled();
	}
}

static esp_err_t ws_router_handle_terminal_cmd(httpd_req_t *req, const char *cmd)
{
	if (req == NULL)
	{
		return ESP_ERR_INVALID_ARG;
	}

	if (s_term_mutex == NULL)
	{
		s_term_mutex = xSemaphoreCreateMutexStatic(&s_term_mutex_buf);
	}
	if (s_term_mutex == NULL)
	{
		return ESP_ERR_NO_MEM;
	}

	if (xSemaphoreTake(s_term_mutex, 0) != pdTRUE)
	{
		ws_router_send_term_out(req, "Terminal busy\n");
		{
			char p[32] = {0};
			ws_router_append_prompt(p, sizeof(p));
			if (p[0] != '\0')
			{
				ws_router_send_term_out(req, p);
			}
		}
		return ESP_OK;
	}

	// Empty command => just reprint prompt
	if (cmd == NULL || cmd[0] == '\0')
	{
		char p[32] = {0};
		ws_router_append_prompt(p, sizeof(p));
		if (p[0] != '\0')
		{
			ws_router_send_term_out(req, p);
		}
		xSemaphoreGive(s_term_mutex);
		return ESP_OK;
	}

	s_term_req = req;
	if (s_term_type == WS_TERMINAL_TYPE_ELM327)
	{
		// Ensure commands are CR-terminated for the ELM/STN parser.
		char tmp[256];
		size_t n = strnlen(cmd, sizeof(tmp) - 2);
		memcpy(tmp, cmd, n);
		// If user didn't include CR, add it.
		if (n == 0 || tmp[n - 1] != '\r')
		{
			tmp[n++] = '\r';
		}
		tmp[n] = '\0';
		elm327_run_command(tmp, (uint32_t)n, 2000, NULL, ws_elm327_output_cb, false, 0);
	}
	else
	{
		(void)cmdline_run_with_output(cmd, ws_terminal_output_cb);
	}
	s_term_req = NULL;

	// Only print a firmware prompt for the console terminal.
	if (s_term_type == WS_TERMINAL_TYPE_CONSOLE)
	{
		char p[32] = {0};
		ws_router_append_prompt(p, sizeof(p));
		if (p[0] != '\0')
		{
			ws_router_send_term_out(req, p);
		}
	}
	xSemaphoreGive(s_term_mutex);
	return ESP_OK;
}

void ws_router_on_open(void)
{
	s_mode = WS_ROUTER_MODE_MONITOR;
	ws_router_set_terminal_type(WS_TERMINAL_TYPE_CONSOLE);
}

void ws_router_on_close(void)
{
	// Ensure we don't leave the device with AutoPID paused due to an unclean client disconnect.
	ws_router_set_terminal_type(WS_TERMINAL_TYPE_CONSOLE);
	s_mode = WS_ROUTER_MODE_MONITOR;
}

bool ws_router_is_in_monitor_mode(void)
{
	return (s_mode == WS_ROUTER_MODE_MONITOR);
}

bool ws_router_handle_frame(httpd_req_t *req, const uint8_t *payload, size_t len)
{
	if (req == NULL || payload == NULL || len == 0)
	{
		return false;
	}

	if (payload[0] != '{')
	{
		return false;
	}

	cJSON *root = cJSON_ParseWithLength((const char *)payload, len);
	if (root == NULL)
	{
		return false;
	}

	bool handled = false;

	cJSON *mode = cJSON_GetObjectItemCaseSensitive(root, "ws_mode");
	if (cJSON_IsString(mode) && (mode->valuestring != NULL))
	{
		if (strcmp(mode->valuestring, "terminal") == 0)
		{
			s_mode = WS_ROUTER_MODE_TERMINAL;
			cJSON *tt = cJSON_GetObjectItemCaseSensitive(root, "terminal_type");
			if (cJSON_IsString(tt) && tt->valuestring)
			{
				if (strcmp(tt->valuestring, "elm327") == 0)
				{
					ws_router_set_terminal_type(WS_TERMINAL_TYPE_ELM327);
				}
				else
				{
					ws_router_set_terminal_type(WS_TERMINAL_TYPE_CONSOLE);
				}
			}
			(void)ws_router_send_json(req, "{\"type\":\"ws_mode\",\"ws_mode\":\"terminal\",\"ok\":true}");
			// Print prompt on entry only for the console terminal.
			if (s_term_type == WS_TERMINAL_TYPE_CONSOLE)
			{
				char p[32] = {0};
				ws_router_append_prompt(p, sizeof(p));
				if (p[0] != '\0')
				{
					ws_router_send_term_out(req, p);
				}
			}
			handled = true;
		}
		else if (strcmp(mode->valuestring, "monitor") == 0)
		{
			s_mode = WS_ROUTER_MODE_MONITOR;
			ws_router_set_terminal_type(WS_TERMINAL_TYPE_CONSOLE);
			(void)ws_router_send_json(req, "{\"type\":\"ws_mode\",\"ws_mode\":\"monitor\",\"ok\":true}");
			handled = true;
		}
		else
		{
			ESP_LOGW(TAG, "Unknown ws_mode '%s'", mode->valuestring);
			(void)ws_router_send_json(req, "{\"type\":\"ws_mode\",\"ok\":false}");
			handled = true;
		}
	}

	if (!handled && s_mode == WS_ROUTER_MODE_TERMINAL)
	{
		cJSON *cmd = cJSON_GetObjectItemCaseSensitive(root, "cmd");
		if (cJSON_IsString(cmd) && (cmd->valuestring != NULL))
		{
			(void)ws_router_handle_terminal_cmd(req, cmd->valuestring);
			handled = true;
		}
	}

	cJSON_Delete(root);
	return handled;
}
