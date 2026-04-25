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

#include "cmd_restart_tracker.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "argtable3/argtable3.h"
#include "cmdline.h"
#include "esp_console.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "restart_tracker.h"

static struct {
    struct arg_lit *latest;
    struct arg_lit *history;
    struct arg_lit *pending;
    struct arg_lit *panic;
    struct arg_int *count;
    struct arg_end *end;
} restart_tracker_args;

static void format_timestamp(int64_t timestamp, bool time_valid, char *buf, size_t buf_len)
{
    if (buf == NULL || buf_len == 0) {
        return;
    }

    if (!time_valid || timestamp <= 0) {
        snprintf(buf, buf_len, "unsynced");
        return;
    }

    time_t ts = (time_t)timestamp;
    struct tm utc_time;
    gmtime_r(&ts, &utc_time);

    if (strftime(buf, buf_len, "%Y-%m-%dT%H:%M:%SZ", &utc_time) == 0) {
        snprintf(buf, buf_len, "%" PRId64, timestamp);
    }
}

static void format_flags(uint32_t flags, char *buf, size_t buf_len)
{
    static const struct {
        uint32_t flag;
        const char *name;
    } flag_names[] = {
        { RESTART_TRACKER_FLAG_SETTINGS_SAVED, "settings_saved" },
        { RESTART_TRACKER_FLAG_FILESYSTEM_CHANGED, "filesystem_changed" },
        { RESTART_TRACKER_FLAG_NVS_ERASED, "nvs_erased" },
        { RESTART_TRACKER_FLAG_FIRMWARE_UPDATED, "firmware_updated" },
        { RESTART_TRACKER_FLAG_RECOVERY_ACTION, "recovery_action" },
    };

    if (buf == NULL || buf_len == 0) {
        return;
    }

    if (flags == RESTART_TRACKER_FLAG_NONE) {
        snprintf(buf, buf_len, "none");
        return;
    }

    buf[0] = '\0';
    size_t written = 0;
    bool first = true;

    for (size_t i = 0; i < sizeof(flag_names) / sizeof(flag_names[0]); i++) {
        if ((flags & flag_names[i].flag) == 0) {
            continue;
        }

        int ret = snprintf(buf + written,
                           written < buf_len ? buf_len - written : 0,
                           "%s%s",
                           first ? "" : ",",
                           flag_names[i].name);
        if (ret < 0) {
            break;
        }

        if ((size_t)ret >= (written < buf_len ? buf_len - written : 0)) {
            written = buf_len - 1;
            break;
        }

        written += (size_t)ret;
        first = false;
    }
}

static void print_record(const restart_tracker_record_t *record)
{
    char boot_ts[32];
    char req_ts[32];
    char flags[96];

    format_timestamp(record->boot_timestamp, record->time_valid != 0, boot_ts, sizeof(boot_ts));
    format_timestamp(record->request_timestamp, record->request_timestamp > 0, req_ts, sizeof(req_ts));
    format_flags(record->flags, flags, sizeof(flags));

    cmdline_printf("[%" PRIu32 "] reset=%s planned=%s source=%s flags=%s request=%s uptime_ms=%" PRIu64 " boot=%s\n",
                   record->sequence,
                   restart_tracker_reset_reason_to_str((esp_reset_reason_t)record->actual_reset_reason),
                   restart_tracker_planned_reason_to_str((restart_tracker_planned_reason_t)record->planned_reason),
                   restart_tracker_source_to_str((restart_tracker_source_t)record->source),
                   flags,
                   req_ts,
                   record->request_uptime_ms,
                   boot_ts);
}

static void print_pending(const restart_tracker_pending_restart_t *pending)
{
    char req_ts[32];
    char flags[96];

    if (pending->valid == 0) {
        cmdline_printf("Pending restart: none\n");
        return;
    }

    format_timestamp(pending->requested_timestamp,
                     pending->time_valid != 0,
                     req_ts,
                     sizeof(req_ts));
    format_flags(pending->flags, flags, sizeof(flags));

    cmdline_printf("Pending restart: reason=%s source=%s flags=%s request=%s uptime_ms=%" PRIu64 "\n",
                   restart_tracker_planned_reason_to_str((restart_tracker_planned_reason_t)pending->planned_reason),
                   restart_tracker_source_to_str((restart_tracker_source_t)pending->source),
                   flags,
                   req_ts,
                   pending->requested_uptime_ms);
}

static int cmd_restart_tracker(int argc, char **argv)
{
    restart_tracker_state_t state;
    int nerrors = arg_parse(argc, argv, (void **)&restart_tracker_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, restart_tracker_args.end, argv[0]);
        return 1;
    }

    if (restart_tracker_args.panic->count > 0) {
        cmdline_printf("Forcing panic now. The next boot should show reset=panic and increment unexpected resets.\n");
        vTaskDelay(pdMS_TO_TICKS(100));
        abort();
    }

    if (restart_tracker_get_state(&state) != ESP_OK) {
        cmdline_printf("Error: Restart tracker state unavailable\n");
        return 1;
    }

    bool show_latest = restart_tracker_args.latest->count > 0;
    bool show_history = restart_tracker_args.history->count > 0;
    bool show_pending = restart_tracker_args.pending->count > 0;

    if (restart_tracker_args.count->count > 0 && !show_history) {
        cmdline_printf("Error: --count requires --history\n");
        return 1;
    }

    if (!show_latest && !show_history && !show_pending) {
        show_latest = true;
        show_pending = true;
    }

    uint32_t available_records = state.boot_count < RESTART_TRACKER_HISTORY_LEN
        ? state.boot_count
        : RESTART_TRACKER_HISTORY_LEN;

    cmdline_printf("Boot count: %" PRIu32 "\n", state.boot_count);
    cmdline_printf("Unexpected resets: %" PRIu32 "\n", state.unexpected_reset_count);
    cmdline_printf("History slots used: %" PRIu32 "/%u\n", available_records, RESTART_TRACKER_HISTORY_LEN);

    if (show_pending) {
        print_pending(&state.pending_restart);
    }

    if (show_latest) {
        restart_tracker_record_t latest_record;
        if (restart_tracker_get_latest_record(&latest_record) == ESP_OK) {
            cmdline_printf("Latest restart:\n");
            print_record(&latest_record);
        } else {
            cmdline_printf("Latest restart: none\n");
        }
    }

    if (show_history) {
        uint32_t limit = available_records;
        if (restart_tracker_args.count->count > 0) {
            int requested = restart_tracker_args.count->ival[0];
            if (requested <= 0) {
                cmdline_printf("Error: --count must be greater than zero\n");
                return 1;
            }
            if ((uint32_t)requested < limit) {
                limit = (uint32_t)requested;
            }
        }

        cmdline_printf("Restart history (newest first):\n");
        if (limit == 0) {
            cmdline_printf("No restart history available\n");
        }

        for (uint32_t i = 0; i < limit; i++) {
            uint32_t index = (state.latest_history_index + RESTART_TRACKER_HISTORY_LEN - i) % RESTART_TRACKER_HISTORY_LEN;
            print_record(&state.history[index]);
        }
    }

    cmdline_printf("OK\n");
    return 0;
}

esp_err_t cmd_restart_tracker_register(void)
{
    restart_tracker_args.latest = arg_lit0("l", "latest", "Show latest restart record");
    restart_tracker_args.history = arg_lit0("a", "history", "Show retained restart history");
    restart_tracker_args.pending = arg_lit0("p", "pending", "Show pending planned restart metadata");
    restart_tracker_args.panic = arg_lit0(NULL, "panic", "Force a test panic and reboot through the panic handler");
    restart_tracker_args.count = arg_int0("n", "count", "<n>", "Number of history entries to show with --history");
    restart_tracker_args.end = arg_end(6);

    const esp_console_cmd_t cmd = {
        .command = "restart_tracker",
        .help = "Inspect retained restart tracker state",
        .hint = "Options: -l/--latest, -a/--history, -p/--pending, -n/--count <n>, --panic",
        .func = &cmd_restart_tracker,
        .argtable = &restart_tracker_args,
    };
    return cmdline_cmd_register(&cmd);
}