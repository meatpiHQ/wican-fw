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


#include "cmd_autopid.h"
#include "cmdline.h"
#include "esp_console.h"
#include "argtable3/argtable3.h"
#include "autopid.h"
#include "cJSON.h"
#include <string.h>
#include <stdlib.h>

static struct {
    struct arg_lit *data;        // -d, --data
    struct arg_lit *pretty;      // -p, --pretty
    struct arg_str *name;        // -n, --name <NAME>
    struct arg_lit *value_only;  // -v, --value-only
    struct arg_end *end;
} ap_args;

static int cmd_autopid(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&ap_args);
    if (nerrors != 0)
    {
        arg_print_errors(stderr, ap_args.end, argv[0]);
        return 1;
    }

    // Get full JSON
    if (ap_args.data->count > 0)
    {
        char *json = autopid_data_read();
        if (!json)
        {
            cmdline_printf("Not available\n");
            return 1;
        }
        if (ap_args.pretty->count > 0)
        {
            // Pretty print: reparse and print formatted
            cJSON *root = cJSON_Parse(json);
            if (root)
            {
                char *pretty = cJSON_Print(root);
                if (pretty)
                {
                    cmdline_printf("%s\n", pretty);
                    free(pretty);
                }
                else
                {
                    cmdline_printf("%s\n", json);
                }
                cJSON_Delete(root);
            }
            else
            {
                cmdline_printf("%s\n", json);
            }
        }
        else
        {
            cmdline_printf("%s\n", json);
        }
        free(json);
        cmdline_printf("OK\n");
        return 0;
    }

    // Get single value by name
    if (ap_args.name->count > 0)
    {
        const char *name = ap_args.name->sval[0];
        if (!name || strlen(name) == 0)
        {
            cmdline_printf("Invalid name\n");
            return 1;
        }
        char *res = autopid_get_value_by_name((char*)name);
        if (!res)
        {
            cmdline_printf("Not available\n");
            return 1;
        }
        if (ap_args.value_only->count > 0)
        {
            // Expect res to be {"NAME":VALUE}, extract VALUE quickly
            const char *colon = strchr(res, ':');
            if (colon)
            {
                // Skip colon and any space
                colon++;
                while (*colon == ' ') colon++;
                // Print up to closing brace
                const char *end = strrchr(colon, '}');
                if (end && end > colon)
                {
                    cmdline_printf("%.*s\n", (int)(end - colon), colon);
                }
                else
                {
                    cmdline_printf("%s\n", res);
                }
            }
            else
            {
                cmdline_printf("%s\n", res);
            }
            free(res);
        }
        else
        {
            cmdline_printf("%s\n", res);
            free(res);
        }
        cmdline_printf("OK\n");
        return 0;
    }

    cmdline_printf("Usage: autopid [-d|--data] [--pretty] | [--name <NAME>] [--value-only]\n");
    return 1;
}

esp_err_t cmd_autopid_register(void)
{
    ap_args.data = arg_lit0("d", "data", "Get full AutoPID JSON");
    ap_args.pretty = arg_lit0("p", "pretty", "Pretty print JSON (with --data)");
    ap_args.name = arg_str0("n", "name", "NAME", "Get single parameter by name");
    ap_args.value_only = arg_lit0("v", "value-only", "Print only the value (with --name)");
    ap_args.end = arg_end(6);

    const esp_console_cmd_t cmd = {
        .command = "autopid",
        .help = "AutoPID data access",
        .hint = "autopid --data [--pretty] | --name <NAME> [--value-only]",
        .func = &cmd_autopid,
        .argtable = &ap_args
    };
    return esp_console_cmd_register(&cmd);
}
