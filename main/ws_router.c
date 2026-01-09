#include "ws_router.h"

#include <string.h>
#include <stdio.h>

#include "esp_log.h"
#include "cJSON.h"
#include "cmdline.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#define TAG "WS_ROUTER"

typedef enum {
	WS_ROUTER_MODE_MONITOR = 0,
	WS_ROUTER_MODE_TERMINAL = 1,
} ws_router_mode_t;

static ws_router_mode_t s_mode = WS_ROUTER_MODE_MONITOR;

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
	strlcat(out, "wican> ", out_len);
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
		ws_router_send_term_out(req, "wican> ");
		return ESP_OK;
	}

	// Empty command => just reprint prompt
	if (cmd == NULL || cmd[0] == '\0')
	{
		ws_router_send_term_out(req, "wican> ");
		xSemaphoreGive(s_term_mutex);
		return ESP_OK;
	}

	s_term_req = req;
	(void)cmdline_run_with_output(cmd, ws_terminal_output_cb);
	s_term_req = NULL;

	ws_router_send_term_out(req, "wican> ");
	xSemaphoreGive(s_term_mutex);
	return ESP_OK;
}

void ws_router_on_open(void)
{
	s_mode = WS_ROUTER_MODE_MONITOR;
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
			(void)ws_router_send_json(req, "{\"type\":\"ws_mode\",\"ws_mode\":\"terminal\",\"ok\":true}");
			handled = true;
		}
		else if (strcmp(mode->valuestring, "monitor") == 0)
		{
			s_mode = WS_ROUTER_MODE_MONITOR;
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
