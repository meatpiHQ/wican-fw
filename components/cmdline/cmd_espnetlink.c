#include "cmd_espnetlink.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "cmdline.h"

#include "argtable3/argtable3.h"
#include "esp_console.h"
#include "esp_err.h"
#include "esp_log.h"

#include "usb_host_manager.h"

static const char *TAG = "espnetlink_cmd";

static struct
{
    struct arg_str *words;
    struct arg_int *timeout_ms;
    struct arg_end *end;
} s_args;

static int cmd_espnetlink(int argc, char **argv)
{
    char *payload;
    bool success;
    ESP_LOGI(TAG, "cmd_espnetlink called, argc=%d", argc);

    int nerrors = arg_parse(argc, argv, (void **)&s_args);
    if (nerrors != 0)
    {
        cmdline_printf("Error: bad arguments (%d errors)\n", nerrors);
        cmdline_printf("Usage: espnetlink <cmd...> [-t <timeout_ms>]\n");
        return 1;
    }

    if (s_args.words->count <= 0)
    {
        cmdline_printf("Usage: espnetlink <cmd...> [-t <timeout_ms>]\n");
        return 1;
    }

    char line[256];
    size_t pos = 0;
    for (int i = 0; i < s_args.words->count; i++)
    {
        const char *w = s_args.words->sval[i];
        if (w == NULL)
        {
            continue;
        }

        size_t wl = strlen(w);
        if (wl == 0)
        {
            continue;
        }

        if (pos != 0)
        {
            if (pos + 1 >= sizeof(line))
            {
                break;
            }
            line[pos++] = ' ';
        }

        if (pos + wl >= sizeof(line))
        {
            wl = sizeof(line) - 1 - pos;
        }

        memcpy(&line[pos], w, wl);
        pos += wl;
        if (pos >= sizeof(line) - 1)
        {
            break;
        }
    }
    line[pos] = '\0';

    uint32_t timeout_ms = 3000;
    if (s_args.timeout_ms->count > 0)
    {
        int v = s_args.timeout_ms->ival[0];
        if (v > 10)
        {
            timeout_ms = (uint32_t)v;
        }
    }

    ESP_LOGI(TAG, "Sending to ESPNetLink: '%s' (timeout=%"PRIu32"ms)", line, timeout_ms);

    payload = NULL;
    success = false;
    esp_err_t send_err = usb_host_manager_espnetlink_exec_command(line, timeout_ms, &payload, &success);
    if (send_err != ESP_OK)
    {
        cmdline_printf("Error: failed to send to ESPNetLink (%s)\n", esp_err_to_name(send_err));
        return 1;
    }

    if (payload != NULL && payload[0] != '\0')
    {
        cmdline_printf("%s\n", payload);
    }

    if (!success)
    {
        cmdline_printf("ERROR\n");
    }
    else if (payload == NULL || payload[0] == '\0')
    {
        cmdline_printf("OK\n");
    }

    free(payload);

    return 0;
}

esp_err_t cmd_espnetlink_register(void)
{
    s_args.words = arg_strn(NULL, NULL, "<cmd>", 1, 16, "Command words to send to ESPNetLink over USB-ACM");
    s_args.timeout_ms = arg_int0("t", "timeout", "<ms>", "Max time to wait for response (default: 1000)");
    s_args.end = arg_end(4);

    const esp_console_cmd_t cmd = {
        .command = "espnetlink",
        .help = "Send a command to ESPNetLink CLI over USB CDC-ACM",
        .hint = "Usage: espnetlink <cmd...> [-t ms]",
        .func = &cmd_espnetlink,
        .argtable = &s_args,
    };

    return esp_console_cmd_register(&cmd);
}
