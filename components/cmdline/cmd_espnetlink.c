#include "cmd_espnetlink.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "cmdline.h"

#include "argtable3/argtable3.h"
#include "esp_console.h"
#include "esp_err.h"

#include "usb_acm_cli.h"

static struct
{
    struct arg_str *words;
    struct arg_int *timeout_ms;
    struct arg_end *end;
} s_args;

static int cmd_espnetlink(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&s_args);
    if (nerrors != 0)
    {
        arg_print_errors(stderr, s_args.end, argv[0]);
        return 1;
    }

    if (!usb_acm_cli_is_connected())
    {
        cmdline_printf("Error: ESPNetLink ACM is not connected (/dev/ttyACM0)\n");
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

    if (usb_acm_cli_send_line(line) != ESP_OK)
    {
        cmdline_printf("Error: failed to send to ESPNetLink\n");
        return 1;
    }

    // Drain output until we go quiet for a short period, or reach the timeout.
    const uint32_t max_total_ms = timeout_ms;
    const uint32_t per_read_timeout_ms = 100;
    const uint32_t quiet_stop_ms = 500;

    uint32_t elapsed = 0;
    uint32_t quiet = 0;
    bool got_any = false;

    uint8_t buf[128];

    while (elapsed < max_total_ms)
    {
        size_t out_len = 0;
        esp_err_t err = usb_acm_cli_read(buf, sizeof(buf), per_read_timeout_ms, &out_len);
        elapsed += per_read_timeout_ms;

        if (err == ESP_OK && out_len > 0)
        {
            got_any = true;
            quiet = 0;

            char out[129];
            size_t n = out_len;
            if (n > (sizeof(out) - 1))
            {
                n = sizeof(out) - 1;
            }

            memcpy(out, buf, n);
            out[n] = '\0';
            cmdline_printf("%s", out);

            continue;
        }

        quiet += per_read_timeout_ms;
        if (got_any && quiet >= quiet_stop_ms)
        {
            break;
        }
    }

    if (!got_any)
    {
        cmdline_printf("(no response)\n");
    }

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
