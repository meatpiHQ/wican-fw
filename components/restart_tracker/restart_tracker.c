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

#include "restart_tracker.h"

#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <time.h>

#include "esp_attr.h"
#include "esp_cache.h"
#include "esp_crc.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

#define RESTART_TRACKER_MAGIC 0x5254524BU
#define RESTART_TRACKER_VERSION 1U
#define RESTART_TRACKER_MIN_VALID_UNIX_TIME 1704067200LL

static EXT_RAM_NOINIT_ATTR restart_tracker_state_t s_restart_state;
static portMUX_TYPE s_restart_lock = portMUX_INITIALIZER_UNLOCKED;

static bool restart_tracker_is_time_valid(time_t timestamp)
{
    return timestamp >= (time_t)RESTART_TRACKER_MIN_VALID_UNIX_TIME;
}

static uint32_t restart_tracker_calculate_crc(const restart_tracker_state_t *state)
{
    return esp_crc32_le(UINT32_MAX,
                        (const uint8_t *)state,
                        (uint32_t)offsetof(restart_tracker_state_t, crc32));
}

static bool restart_tracker_state_is_valid(const restart_tracker_state_t *state)
{
    if (state->magic != RESTART_TRACKER_MAGIC)
    {
        return false;
    }

    if (state->version != RESTART_TRACKER_VERSION)
    {
        return false;
    }

    if (state->history_len != RESTART_TRACKER_HISTORY_LEN)
    {
        return false;
    }

    return restart_tracker_calculate_crc(state) == state->crc32;
}

static void restart_tracker_reset_state(restart_tracker_state_t *state)
{
    memset(state, 0, sizeof(*state));
    state->magic = RESTART_TRACKER_MAGIC;
    state->version = RESTART_TRACKER_VERSION;
    state->history_len = RESTART_TRACKER_HISTORY_LEN;
}

static void restart_tracker_update_crc(restart_tracker_state_t *state)
{
    state->crc32 = restart_tracker_calculate_crc(state);
}

static esp_err_t restart_tracker_commit_state(restart_tracker_state_t *state)
{
    return esp_cache_msync(state,
                           sizeof(*state),
                           ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
}

static bool restart_tracker_is_unexpected_reset(esp_reset_reason_t reason)
{
    switch (reason)
    {
    case ESP_RST_SW:
    case ESP_RST_POWERON:
    case ESP_RST_DEEPSLEEP:
        return false;
    default:
        return true;
    }
}

esp_err_t restart_tracker_init(void)
{
    time_t now = time(NULL);
    bool time_valid = restart_tracker_is_time_valid(now);
    esp_reset_reason_t reset_reason = esp_reset_reason();
    esp_err_t ret;

    portENTER_CRITICAL(&s_restart_lock);

    if (!restart_tracker_state_is_valid(&s_restart_state))
    {
        restart_tracker_reset_state(&s_restart_state);
    }

    uint32_t slot = s_restart_state.next_history_index % RESTART_TRACKER_HISTORY_LEN;
    restart_tracker_record_t *record = &s_restart_state.history[slot];

    memset(record, 0, sizeof(*record));
    record->sequence = ++s_restart_state.record_sequence;
    record->boot_timestamp = time_valid ? (int64_t)now : 0;
    record->actual_reset_reason = (uint32_t)reset_reason;
    record->time_valid = time_valid ? 1U : 0U;

    if (s_restart_state.pending_restart.valid)
    {
        record->request_timestamp = s_restart_state.pending_restart.requested_timestamp;
        record->request_uptime_ms = s_restart_state.pending_restart.requested_uptime_ms;
        record->planned_reason = s_restart_state.pending_restart.planned_reason;
        record->source = s_restart_state.pending_restart.source;
        record->flags = s_restart_state.pending_restart.flags;
        record->was_planned = 1U;
    }

    s_restart_state.boot_count++;
    if (!record->was_planned && restart_tracker_is_unexpected_reset(reset_reason))
    {
        s_restart_state.unexpected_reset_count++;
    }

    s_restart_state.latest_history_index = slot;
    s_restart_state.next_history_index = (slot + 1U) % RESTART_TRACKER_HISTORY_LEN;
    memset(&s_restart_state.pending_restart, 0, sizeof(s_restart_state.pending_restart));
    restart_tracker_update_crc(&s_restart_state);
    ret = restart_tracker_commit_state(&s_restart_state);

    portEXIT_CRITICAL(&s_restart_lock);

    return ret;
}

esp_err_t restart_tracker_mark_planned_restart(restart_tracker_planned_reason_t reason,
                                               restart_tracker_source_t source,
                                               uint32_t flags)
{
    time_t now = time(NULL);
    bool time_valid = restart_tracker_is_time_valid(now);
    uint64_t uptime_ms = (uint64_t)(esp_timer_get_time() / 1000ULL);
    esp_err_t ret;

    portENTER_CRITICAL(&s_restart_lock);

    if (!restart_tracker_state_is_valid(&s_restart_state))
    {
        restart_tracker_reset_state(&s_restart_state);
    }

    s_restart_state.pending_restart.requested_timestamp = time_valid ? (int64_t)now : 0;
    s_restart_state.pending_restart.requested_uptime_ms = uptime_ms;
    s_restart_state.pending_restart.flags = flags;
    s_restart_state.pending_restart.planned_reason = (uint16_t)reason;
    s_restart_state.pending_restart.source = (uint16_t)source;
    s_restart_state.pending_restart.valid = 1U;
    s_restart_state.pending_restart.time_valid = time_valid ? 1U : 0U;
    restart_tracker_update_crc(&s_restart_state);
    ret = restart_tracker_commit_state(&s_restart_state);

    portEXIT_CRITICAL(&s_restart_lock);

    return ret;
}

void restart_tracker_restart(restart_tracker_planned_reason_t reason,
                             restart_tracker_source_t source,
                             uint32_t flags)
{
    restart_tracker_mark_planned_restart(reason, source, flags);
    esp_restart();
}

esp_err_t restart_tracker_get_state(restart_tracker_state_t *out_state)
{
    if (out_state == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    portENTER_CRITICAL(&s_restart_lock);
    if (!restart_tracker_state_is_valid(&s_restart_state))
    {
        portEXIT_CRITICAL(&s_restart_lock);
        return ESP_ERR_INVALID_STATE;
    }

    *out_state = s_restart_state;
    portEXIT_CRITICAL(&s_restart_lock);
    return ESP_OK;
}

esp_err_t restart_tracker_get_latest_record(restart_tracker_record_t *out_record)
{
    if (out_record == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    portENTER_CRITICAL(&s_restart_lock);
    if (!restart_tracker_state_is_valid(&s_restart_state))
    {
        portEXIT_CRITICAL(&s_restart_lock);
        return ESP_ERR_INVALID_STATE;
    }

    if (s_restart_state.boot_count == 0U)
    {
        portEXIT_CRITICAL(&s_restart_lock);
        return ESP_ERR_NOT_FOUND;
    }

    *out_record = s_restart_state.history[s_restart_state.latest_history_index % RESTART_TRACKER_HISTORY_LEN];
    portEXIT_CRITICAL(&s_restart_lock);
    return ESP_OK;
}

const char *restart_tracker_reset_reason_to_str(esp_reset_reason_t reason)
{
    switch (reason)
    {
        case ESP_RST_UNKNOWN:
            return "unknown";
        case ESP_RST_POWERON:
            return "poweron";
        case ESP_RST_EXT:
            return "external";
        case ESP_RST_SW:
            return "software";
        case ESP_RST_PANIC:
            return "panic";
        case ESP_RST_INT_WDT:
            return "interrupt_wdt";
        case ESP_RST_TASK_WDT:
            return "task_wdt";
        case ESP_RST_WDT:
            return "wdt";
        case ESP_RST_DEEPSLEEP:
            return "deepsleep";
        case ESP_RST_BROWNOUT:
            return "brownout";
        case ESP_RST_SDIO:
            return "sdio";
        default:
            return "invalid";
    }
}

const char *restart_tracker_planned_reason_to_str(restart_tracker_planned_reason_t reason)
{
    switch (reason)
    {
        case RESTART_TRACKER_PLANNED_REASON_NONE:
            return "none";
        case RESTART_TRACKER_PLANNED_REASON_USER_REQUEST:
            return "user_request";
        case RESTART_TRACKER_PLANNED_REASON_CONFIG_APPLY:
            return "config_apply";
        case RESTART_TRACKER_PLANNED_REASON_CONFIG_RECOVERY:
            return "config_recovery";
        case RESTART_TRACKER_PLANNED_REASON_OTA_APPLY:
            return "ota_apply";
        case RESTART_TRACKER_PLANNED_REASON_FACTORY_RESET:
            return "factory_reset";
        case RESTART_TRACKER_PLANNED_REASON_SAFE_MODE:
            return "safe_mode";
        case RESTART_TRACKER_PLANNED_REASON_POWER_WAKE:
            return "power_wake";
        case RESTART_TRACKER_PLANNED_REASON_INTERNAL_RECOVERY:
            return "internal_recovery";
        default:
            return "invalid";
    }
}

const char *restart_tracker_source_to_str(restart_tracker_source_t source)
{
    switch (source)
    {
        case RESTART_TRACKER_SOURCE_UNKNOWN:
            return "unknown";
        case RESTART_TRACKER_SOURCE_WEB_UI:
            return "web_ui";
        case RESTART_TRACKER_SOURCE_CMDLINE:
            return "cmdline";
        case RESTART_TRACKER_SOURCE_CONSOLE:
            return "console";
        case RESTART_TRACKER_SOURCE_MQTT:
            return "mqtt";
        case RESTART_TRACKER_SOURCE_OTA:
            return "ota";
        case RESTART_TRACKER_SOURCE_SAFE_MODE:
            return "safe_mode";
        case RESTART_TRACKER_SOURCE_CONFIG_SERVER:
            return "config_server";
        case RESTART_TRACKER_SOURCE_SLEEP_MODE:
            return "sleep_mode";
        default:
            return "invalid";
    }
}