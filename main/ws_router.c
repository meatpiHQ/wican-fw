#include "ws_router.h"

#include <string.h>
#include <stdio.h>

#include "esp_log.h"
#include "cJSON.h"

#define TAG "WS_ROUTER"

typedef enum {
	WS_ROUTER_MODE_MONITOR = 0,
	WS_ROUTER_MODE_TERMINAL = 1,
} ws_router_mode_t;

static ws_router_mode_t s_mode = WS_ROUTER_MODE_MONITOR;

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

static esp_err_t ws_router_handle_terminal_cmd(httpd_req_t *req, const char *cmd)
{
	char reply[512];
	reply[0] = '\0';

	if (cmd == NULL || cmd[0] == '\0')
	{
		ws_router_append_prompt(reply, sizeof(reply));
		return ws_router_send_json(req, "{\"type\":\"term_out\",\"data\":\"\"}");
	}

	if (strcmp(cmd, "help") == 0)
	{
		snprintf(reply, sizeof(reply),
				"WiCAN Console (fake)\n"
				"Commands:\n"
				"  help\n"
				"  status\n"
				"  echo <text>\n"
				"\n"
				"Note: This is a stub backend; will be wired to cmdline next.\n");
		ws_router_append_prompt(reply, sizeof(reply));
	}
	else if (strcmp(cmd, "status") == 0)
	{
		snprintf(reply, sizeof(reply), "OK\n");
		ws_router_append_prompt(reply, sizeof(reply));
	}
	else if (strncmp(cmd, "echo ", 5) == 0)
	{
		snprintf(reply, sizeof(reply), "%s\n", cmd + 5);
		ws_router_append_prompt(reply, sizeof(reply));
	}
	else
	{
		snprintf(reply, sizeof(reply), "Unknown command: %s\n", cmd);
		ws_router_append_prompt(reply, sizeof(reply));
	}

	cJSON *root = cJSON_CreateObject();
	if (root == NULL)
	{
		return ESP_ERR_NO_MEM;
	}
	cJSON_AddStringToObject(root, "type", "term_out");
	cJSON_AddStringToObject(root, "data", reply);
	char *json = cJSON_PrintUnformatted(root);
	cJSON_Delete(root);
	if (json == NULL)
	{
		return ESP_ERR_NO_MEM;
	}

	esp_err_t err = ws_router_send_json(req, json);
	free(json);
	return err;
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
