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

#pragma once

#include <stdint.h>
#include "esp_err.h"
#include "esp_system.h"

#ifdef __cplusplus
extern "C" {
#endif

#define RESTART_TRACKER_HISTORY_LEN 8U
#define RESTART_TRACKER_COREDUMP_SUBDIR "coredumps"
#define RESTART_TRACKER_COREDUMP_MAX_FILES 5U

typedef enum {
    RESTART_TRACKER_PLANNED_REASON_NONE = 0,
    RESTART_TRACKER_PLANNED_REASON_USER_REQUEST,
    RESTART_TRACKER_PLANNED_REASON_CONFIG_APPLY,
    RESTART_TRACKER_PLANNED_REASON_CONFIG_RECOVERY,
    RESTART_TRACKER_PLANNED_REASON_OTA_APPLY,
    RESTART_TRACKER_PLANNED_REASON_FACTORY_RESET,
    RESTART_TRACKER_PLANNED_REASON_SAFE_MODE,
    RESTART_TRACKER_PLANNED_REASON_POWER_WAKE,
    RESTART_TRACKER_PLANNED_REASON_INTERNAL_RECOVERY,
} restart_tracker_planned_reason_t;

typedef enum {
    RESTART_TRACKER_SOURCE_UNKNOWN = 0,
    RESTART_TRACKER_SOURCE_WEB_UI,
    RESTART_TRACKER_SOURCE_CMDLINE,
    RESTART_TRACKER_SOURCE_CONSOLE,
    RESTART_TRACKER_SOURCE_MQTT,
    RESTART_TRACKER_SOURCE_OTA,
    RESTART_TRACKER_SOURCE_SAFE_MODE,
    RESTART_TRACKER_SOURCE_CONFIG_SERVER,
    RESTART_TRACKER_SOURCE_SLEEP_MODE,
} restart_tracker_source_t;

enum {
    RESTART_TRACKER_FLAG_NONE = 0,
    RESTART_TRACKER_FLAG_SETTINGS_SAVED = (1U << 0),
    RESTART_TRACKER_FLAG_FILESYSTEM_CHANGED = (1U << 1),
    RESTART_TRACKER_FLAG_NVS_ERASED = (1U << 2),
    RESTART_TRACKER_FLAG_FIRMWARE_UPDATED = (1U << 3),
    RESTART_TRACKER_FLAG_RECOVERY_ACTION = (1U << 4),
};

typedef struct {
    int64_t requested_timestamp;
    uint64_t requested_uptime_ms;
    uint32_t flags;
    uint16_t planned_reason;
    uint16_t source;
    uint8_t valid;
    uint8_t time_valid;
    uint8_t reserved[6];
} restart_tracker_pending_restart_t;

typedef struct {
    uint32_t sequence;
    int64_t boot_timestamp;
    int64_t request_timestamp;
    uint64_t request_uptime_ms;
    uint32_t actual_reset_reason;
    uint16_t planned_reason;
    uint16_t source;
    uint32_t flags;
    uint8_t was_planned;
    uint8_t time_valid;
    uint8_t reserved[2];
} restart_tracker_record_t;

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t history_len;
    uint32_t boot_count;
    uint32_t unexpected_reset_count;
    uint32_t record_sequence;
    uint32_t next_history_index;
    uint32_t latest_history_index;
    restart_tracker_pending_restart_t pending_restart;
    restart_tracker_record_t history[RESTART_TRACKER_HISTORY_LEN];
    uint32_t crc32;
} restart_tracker_state_t;

esp_err_t restart_tracker_init(void);
esp_err_t restart_tracker_mark_planned_restart(restart_tracker_planned_reason_t reason,
                                               restart_tracker_source_t source,
                                               uint32_t flags);
void restart_tracker_restart(restart_tracker_planned_reason_t reason,
                             restart_tracker_source_t source,
                             uint32_t flags);
esp_err_t restart_tracker_get_state(restart_tracker_state_t *out_state);
esp_err_t restart_tracker_get_latest_record(restart_tracker_record_t *out_record);
esp_err_t restart_tracker_coredump_on_storage_ready(const char *mount_path);
const char *restart_tracker_reset_reason_to_str(esp_reset_reason_t reason);
const char *restart_tracker_planned_reason_to_str(restart_tracker_planned_reason_t reason);
const char *restart_tracker_source_to_str(restart_tracker_source_t source);

#ifdef __cplusplus
}
#endif