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

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "esp_http_server.h"

#ifdef __cplusplus
extern "C" {
#endif

// SD directory holding the rotating event-log files. Hardcoded mount root MUST match
// SD_CARD_MOUNT_POINT in main/sdcard.h (event_log is a leaf and can't read that macro). Exposed here
// so sd_filemgr can treat it as a protected dir.
#define EVENT_LOG_DIR  "/sdcard/events"

// On-device operational event log (Task #24).
//
// Records meaningful operating events (boot, engine start/stop, datalog session open/close,
// software update / reboot, and NC-Flash flash/read + host-coexistence sessions) so the device
// keeps a factual, retrievable history of what it did -- without a serial cable. This matters most
// on the wireless brick-risk path: a flash leaves no other post-hoc trace. Two sinks: an
// always-present in-RAM ring (survives a missing/failed SD) and a rotating text file on the SD card.
//
// BRICK-SAFE CONTRACT (mirrors csv_logger's invariants):
//  - This is a LEAF component: it REQUIRES only base IDF, so csv_logger / poll_log / config_server /
//    main can all call event_log_emit() without a dependency cycle.
//  - event_log_emit() is NON-BLOCKING and safe from any task (incl. the poll_log poll loop at prio 5
//    and the csv_logger writer at prio 4): it formats into a stack buffer, copies one line into the
//    in-RAM ring under a brief critical section, and signals a low-priority writer task. It NEVER
//    touches the SD card on the caller's thread and NEVER allocates.
//  - The in-RAM ring + the writer task stack live in INTERNAL RAM (the writer dereferences buffers
//    inside fwrite/fsync flash-cache-disable windows where any PSRAM access would fault).
//  - event_log_emit() is safe to call before event_log_init() (it just fills the ring; nothing
//    drains to SD until the writer is up).

// Event categories. Keep the set small and debugging-focused.
typedef enum {
    EVL_BOOT = 0,        // power-on / reset (reason + firmware version)
    EVL_ENGINE_START,    // poll_log: ECU answering again (bus resume)
    EVL_ENGINE_STOP,     // poll_log: ECU silent -> LISTEN_ONLY quiesce
    EVL_DATALOG_OPEN,    // csv_logger: a logging session/file opened
    EVL_DATALOG_CLOSE,   // csv_logger: a logging session/file closed
    EVL_OTA_START,       // firmware OTA upload began
    EVL_OTA_OK,          // firmware OTA written + boot partition switched
    EVL_OTA_FAIL,        // firmware OTA aborted/failed
    // --- NC-Flash / coexistence lifecycle (Task #12). Milestone-only: emitted at start/done/abort
    //     of a flash, fast-read, or host bus session -- NEVER per-block on the TransferData hot path
    //     and NEVER inside an fwrite/fsync flash-cache-disable window (NCFWPROG covers live progress).
    EVL_FLASH_START,     // ncflash_fastwrite: SD-staged fast-write begins (mode, ROM, total blocks)
    EVL_FLASH_OK,        // ncflash_fastwrite: NCFWDONE reached (blocks written, elapsed)
    EVL_FLASH_FAIL,      // ncflash_fastwrite: aborted/failed (where it died: stage + FWSUB/NRC + block)
    EVL_READ_START,      // ncflash_fastread: fast ROM read begins (addr, length)
    EVL_READ_OK,         // ncflash_fastread: fast ROM read completed (bytes, elapsed)
    EVL_HOST_CLAIM,      // host opened the bus-claim window (NC-Flash "cable plugged in")
    EVL_HOST_RELEASE,    // host closed the bus-claim window
    EVL_DATALOG_PARK,    // datalogger parked for a host session (POST /datalog?op=pause)
    EVL_DATALOG_RESUME,  // datalogger resumed after a host session (POST /datalog?op=resume)
    EVL_REAPER_RESUME,   // dead-man reaper auto-resumed datalog (host vanished) -- highest-value line
    EVL_INFO,            // generic informational note
    EVL_CODE_MAX
} event_log_code_t;

// SD-mounted predicate injection (optional). event_log is a leaf and cannot call the main-owned
// sdcard_is_mounted(); main registers it here so the writer can skip pointless fopen attempts when
// no card is present. NULL (never set) is fine -- the writer then relies on fopen() failing cleanly.
typedef bool (*event_log_sd_ready_fn_t)(void);
void event_log_set_sd_ready_fn(event_log_sd_ready_fn_t fn);

// Bring up the in-RAM ring + the low-priority SD writer task. Idempotent. Call once early (right
// after the SD mount + restart_tracker_init in app_main). Carries its own RTC_NOINIT crash-guard:
// if a prior boot crashed during event_log SD work, SD persistence is skipped THIS boot (the RAM
// ring still records everything) and self-recovers next boot.
void event_log_init(void);

// Record one event. Non-blocking, variadic detail (printf-style). Safe from any task and before init.
// Events are fsync'd to SD by the writer task within ~1s of emission, so a reboot a couple of seconds
// later (the planned-restart timer) keeps them; no synchronous flush is needed on the reset path.
void event_log_emit(event_log_code_t code, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

// Register the GET /event_log* retrieval endpoint on the running httpd server. Idempotent. Must be
// called before the catch-all wildcard handler. Routes:
//   /event_log            -> the SD log file (chunked), or the RAM ring if no card
//   /event_log/ram        -> the in-RAM ring (always works)
//   /event_log/status     -> JSON {sd, file_bytes, rotations, ring_count, dropped, ...}
esp_err_t event_log_register_handlers(httpd_handle_t server);

#ifdef __cplusplus
}
#endif
