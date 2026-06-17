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

#ifndef __CSV_LOGGER_H__
#define __CSV_LOGGER_H__

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "esp_http_server.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CSV_LOGGER_DIR              "/sdcard/logs"
#define CSV_LOGGER_NAME_MAX         48
#define CSV_LOGGER_UNIT_MAX         16
#define CSV_LOGGER_SOURCE_MAX       8

/**
 * @brief Start the CSV datalogger.
 *
 * Creates the record queue and writer task. Safe to call once at startup;
 * if the logger is disabled in config the caller should simply not call this.
 * Records are written to /sdcard/logs/<name>.csv, one file per ignition cycle.
 *
 * @return ESP_OK on success
 */
esp_err_t csv_logger_init(void);

/**
 * @brief Start the CSV datalogger AFTER boot settles (deferred ~20s).
 *
 * Call this at boot instead of csv_logger_init(); it spawns a small task that waits,
 * then calls csv_logger_init() once. The wait is a modest settle margin (the historical
 * boot crash was a task-publish race in csv_logger_init(), now fixed). A one-shot RTC
 * guard skips CSV for a single boot if a startup attempt ever fails to stabilize, so a
 * CSV-startup fault can never boot-loop the device.
 */
void csv_logger_init_deferred(void);

/**
 * @brief Queue one decoded parameter sample for CSV logging.
 *
 * Non-blocking: called from the AutoPID hot path, so it never waits. If the
 * logger is not running or the queue is full the sample is counted as dropped
 * and the call returns immediately.
 *
 * @param name   Parameter name (e.g. "EngineRPM")
 * @param value  Decoded value
 * @param unit   Unit string, may be NULL
 * @param source Origin tag: "CANFLT" (broadcast), "PID" or "STD" (polled)
 */
void csv_logger_record(const char *name, float value, const char *unit, const char *source);

/**
 * @brief Logger status snapshot as a cJSON-printed string (caller frees), or NULL.
 */
char *csv_logger_get_status_json(void);

/* HTTP endpoints (register in config_server):
 *   GET  /csv_status            -> logger status JSON (running, sd_mounted, session_active, rows_written...)
 *   GET  /csv_list              -> {"files":[{"name":..,"size":..}, ...]} of the SD-card CSV logs
 *   GET  /download_csv?file=NAME -> streams that CSV file as a download
 */
extern const httpd_uri_t csv_status_uri;
extern const httpd_uri_t csv_list_uri;
extern const httpd_uri_t csv_download_uri;

#ifdef __cplusplus
}
#endif

#endif
