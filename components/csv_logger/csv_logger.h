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
 * @brief Runtime manual Start/Stop for the CSV logger (web button, authoritative).
 *
 * Like the CAN Monitor button: START forces logging ON and STOP forces it OFF, both
 * overriding the ignition gate. Runtime-only, NOT persisted -- a reboot resets to AUTO
 * (follow ignition) under the csv_log config boot gate.
 *
 * enable=true:  force logging ON regardless of ignition; lazily creates the writer task
 *   (csv_logger_init()) if csv_log was disabled at boot. MUST be called from a task at
 *   priority > 4 (the httpd handler at prio 5 qualifies) so the on-demand init cannot hit
 *   the boot publish-race. Returns the csv_logger_init() error on a failed start (e.g. OOM),
 *   leaving the mode OFF. Note: a session only opens once a record arrives, so logging is
 *   effective only while AutoPID records are flowing.
 * enable=false: force logging OFF even if ignition reads on; the writer closes the session
 *   on its next pass and stays alive (never deleted). Stays off until the next START/reboot.
 *
 * @return ESP_OK, or the csv_logger_init() error on a failed on-demand start.
 */
esp_err_t csv_logger_set_manual_override(bool enable);

/**
 * @brief Column provider for the WIDE (Tactrix-style) CSV format (Task #11).
 *
 * Registered by the AutoPID module at init so the CSV logger can enumerate the set of
 * logged channels (one column each) WITHOUT a build-time dependency on autopid. autopid
 * already REQUIRES csv_logger; routing the enumeration through a function pointer keeps
 * that dependency one-way (no circular CMake REQUIRES).
 *
 * The provider fills caller-supplied fixed-stride arrays and returns the column count, or
 * -1 if the config/lock is not ready (the writer retries briefly, then falls back to LONG).
 * Counting mode: names==NULL returns an UPPER BOUND on the count (no writes) so the caller
 * can size buffers before the fill pass. The provider takes its own lock BRIEFLY and does
 * NO SD/flash/PSRAM-heavy work while holding it.
 *
 * Array dims MUST equal CSV_LOGGER_NAME_MAX / CSV_LOGGER_UNIT_MAX / CSV_LOGGER_SOURCE_MAX.
 * The (source,name) pair is the column key: the producer emits the same name under
 * different source tags (e.g. "PID" vs "CANFLT"), so source disambiguates two channels.
 */
typedef int (*csv_column_provider_t)(char (*names)[CSV_LOGGER_NAME_MAX],
                                     char (*units)[CSV_LOGGER_UNIT_MAX],
                                     char (*sources)[CSV_LOGGER_SOURCE_MAX],
                                     int max_cols);

/**
 * @brief Register the wide-CSV column provider. Call once at boot (before logging starts).
 */
void csv_logger_set_column_provider(csv_column_provider_t provider);

/**
 * @brief Engine-running predicate for the "Require engine running" CSV gate.
 *
 * Returns true while the engine is running. poll_log registers poll_log_engine_running here so
 * the logger can stop on engine-off without a circular component dependency (poll_log already
 * depends on csv_logger). Registered once at boot; when no provider is set the gate degrades to
 * the voltage ignition gate only. Must be cheap and lock-free (called from the writer task).
 */
typedef bool (*csv_engine_state_fn_t)(void);
void csv_logger_set_engine_state_fn(csv_engine_state_fn_t fn);

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

/**
 * @brief True if abspath is the CSV file the writer task currently holds open.
 *
 * Lets the SD file manager refuse to delete/rename the log being written.
 */
bool csv_logger_is_active_file(const char *abspath);

/**
 * @brief True while a CSV log session is open (file on SD, rows being accepted).
 *
 * Cross-task snapshot of the writer-task-owned session flag; drives the blue
 * LED activity indicator (issue #19).
 */
bool csv_logger_session_active(void);

/* HTTP endpoints (register in config_server):
 *   GET  /csv_status            -> logger status JSON (running, sd_mounted, session_active, rows_written...)
 *   GET  /csv_list              -> {"files":[{"name":..,"size":..}, ...]} of the SD-card CSV logs
 *   GET  /download_csv?file=NAME -> streams that CSV file as a download
 */
extern const httpd_uri_t csv_status_uri;
extern const httpd_uri_t csv_list_uri;
extern const httpd_uri_t csv_download_uri;
extern const httpd_uri_t csv_control_uri;     /* POST /csv_logger?op=start|stop|mark */
extern const httpd_uri_t datalog_control_uri; /* POST /datalog?op=pause|resume|bus_claim|bus_release|keepalive */
extern const httpd_uri_t datalog_status_uri;  /* GET  /datalog -> live coexistence state */

/**
 * @brief Restore the pre-pause manual mode after a park lease has been released/reaped
 *        (dead-man's-switch, WICAN_DEADMAN_AUTORESUME.md).
 *
 * Called by exactly one winner per park lease: the REST op=resume handler (after a successful
 * token-matched can_park_lease_release) OR the firmware dead-man reaper (after a successful
 * token+deadline-matched can_park_lease_reap). Because both lower the park flag atomically under
 * the can.c spinlock, only one reaches here for a given pause -- that mutual exclusion is what
 * makes this lock-free mode-restore safe. Touches ONLY the csv manual mode, never a CAN bit.
 */
void datalog_restore_mode(void);

#ifdef __cplusplus
}
#endif

#endif
