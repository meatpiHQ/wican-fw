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

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
#include <dirent.h>
#include <time.h>
#include <sys/time.h>
#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_attr.h"
#include "esp_http_server.h"
#include "cJSON.h"

#include "csv_logger.h"
#include "event_log.h"
#include "config_server.h"
#include "sdcard.h"
#include "can.h"   /* can_flash_active / can_park_lease_* / can_host_bus_claim_*: coexistence + dead-man's-switch (task #36) */
#include "vehicle.h"
#include "wc_timer.h"
#include "sleep_mode.h"      // sleep_mode_get_voltage() for the BATT_V system column (non-blocking queue peek)

static const char *TAG = "CSV_LOGGER";

// The wide column provider fills caller arrays at these exact strides; assert they match.
_Static_assert(CSV_LOGGER_NAME_MAX == 48, "wide column name dim must match provider");
_Static_assert(CSV_LOGGER_UNIT_MAX == 16, "wide column unit dim must match provider");
_Static_assert(CSV_LOGGER_SOURCE_MAX == 8, "wide column source dim must match provider");

#define CSV_LOGGER_QUEUE_LEN        256
#define CSV_LOGGER_TASK_STACK_SIZE  (1024 * 6)
// Small settle delay before auto-starting CSV at boot. NOTE: the historical "boot
// crash-loop" was NOT a timing/boot-window problem -- it was a task-publish race in
// csv_logger_init() (csv_queue was assigned AFTER xTaskCreateStatic, but the higher-prio
// writer task preempted and ran xQueueReceive(NULL) -> panic). Fixed by publishing the
// queue before creating the task. This delay is now just a modest margin.
#define CSV_LOGGER_DEFER_BOOT_MS    20000
// One file per engine on/off cycle: the session already opens on ignition-on and closes on
// ignition-off (3 s debounce), so the only thing that can split a single drive is this byte cap.
// It is now a SAFETY BACKSTOP, not a routine split -- a realistic drive is tens of MB (~6-7 MB/h
// at 10 Hz wide), far below this. Sized safely under the FAT32 single-file hard limit (4 GB) and
// the 32-bit csv_file_bytes counter; if ever hit, rotation still closes+reopens cleanly as before.
#define CSV_LOGGER_ROTATE_BYTES     (1024u * 1024u * 1024u)   /* 1 GiB backstop (~150 h of logging) */
#define CSV_LOGGER_FLUSH_PERIOD_MS  1000
#define CSV_LOGGER_IGNITION_POLL_MS 500
// Ignition-off must persist this long before the session closes, so a voltage
// dip during cranking doesn't split the log file.
#define CSV_LOGGER_IGN_OFF_DEBOUNCE_MS  3000
#define CSV_LOGGER_SD_RETRY_MS      5000

typedef struct
{
    int64_t t_ms;                          // ms since boot, captured at sample time
    float value;
    char name[CSV_LOGGER_NAME_MAX];
    char unit[CSV_LOGGER_UNIT_MAX];
    char source[CSV_LOGGER_SOURCE_MAX];
} csv_record_t;

static QueueHandle_t csv_queue = NULL;
static StaticQueue_t csv_queue_buffer;

static FILE *csv_file = NULL;
static char csv_file_path[128];
static size_t csv_file_bytes = 0;
static uint32_t csv_rows_written = 0;
static uint32_t csv_rows_dropped = 0;
static uint32_t csv_files_count = 0;
static bool csv_session_active = false;
static int64_t csv_sd_retry_after_ms = 0;

// Runtime manual logging mode (web Start/Stop button). Authoritative, like the CAN
// Monitor button: START forces logging ON, STOP forces it OFF -- both override the
// ignition gate (whose voltage heuristic is unreliable, e.g. reads ON at a 13.9V bench
// supply; see Task #6). AUTO (boot default) follows ignition, preserving the existing
// csv_log=enable behavior. Written by the httpd handler task, read by the writer task on
// a possibly-different core; a naturally-aligned int8 is an atomic load/store on the
// dual-core S3 and volatile forces a fresh read each writer-loop pass. .bss => INTERNAL RAM.
#define CSV_MANUAL_AUTO  0   // follow ignition_on (default)
#define CSV_MANUAL_ON    1   // force logging on
#define CSV_MANUAL_OFF   2   // force logging off
static volatile int8_t csv_manual_mode = CSV_MANUAL_AUTO;

// ---- Wide (Tactrix-style) CSV format state (Task #11) ----
// Approach B: write WIDE incrementally (one column per channel) with in-RAM last-observation-
// carried-forward (LOCF). Fast channels (RPM) change each row, slow ones (ECT/IAT) repeat
// their last value until refreshed. The on-SD file is a valid, complete-up-to-now CSV at
// every flush, so the operator can download the in-progress log while driving.
//
// Brick-safety: ALL wide buffers live in INTERNAL RAM. The writer dereferences them inside
// fprintf/fflush/fsync (flash-cache-disable windows where any PSRAM access faults -- brick
// invariants 2 & 4). The producer hot path (csv_logger_record / csv_record_t) is UNCHANGED;
// all wide/LOCF work is consumer-side. No new task is created (invariant 5): the fixed-rate
// grid is driven off the existing writer loop via a shortened queue timeout + a wc_timer.
#define CSV_WIDE_MAX_COLS       256
#define CSV_WIDE_LINE_BUF       8192    // bounded row builder; never overflowed, always \n-terminated.
                                        // 256 cols * ~31 chars covers any realistic value; pathological
                                        // huge values past this degrade to empty tail cells, never a
                                        // half-written number or a dropped column mid-row.
#define CSV_WIDE_FIELD_MAX      64      // holds ",%.3f" of any finite float (FLT_MAX ~= 45 chars)
#define CSV_GRID_HZ_DEFAULT     10

typedef enum { CSV_GRID_EVENT = 0, CSV_GRID_FIXED = 1 } csv_grid_mode_t;

// One wide column. (source,name) is the dedupe + match KEY (the producer emits the same
// name under different source tags, so name alone would collapse two distinct channels).
// name holds the RAW (unsanitized) name to string-match rec.name; the header writer
// sanitizes a local copy.
typedef struct {
    char  name[CSV_LOGGER_NAME_MAX];
    char  unit[CSV_LOGGER_UNIT_MAX];
    char  source[CSV_LOGGER_SOURCE_MAX];
    float last_value;
    bool  valid;        // false until first sample (LOCF presence: empty cell vs value)
    bool  dup_name;     // another column shares this name -> disambiguate in the header
} csv_wide_col_t;

// Latched ONCE per session by the writer task (single-writer thereafter -> no torn reads).
static csv_grid_mode_t csv_grid_mode  = CSV_GRID_EVENT;
static uint32_t        csv_grid_hz    = CSV_GRID_HZ_DEFAULT;
static csv_wide_col_t *csv_cols       = NULL;   // [csv_col_count], INTERNAL RAM, session-scoped
static int             csv_col_count  = 0;
static char           *csv_line_buf   = NULL;   // CSV_WIDE_LINE_BUF, INTERNAL RAM, alloc'd once

// Column provider (registered by autopid). Written once at boot before the writer runs.
static csv_column_provider_t csv_col_provider = NULL;

// Engine-running predicate (registered by poll_log). NULL = no provider -> gate degrades to the
// voltage ignition gate. Written once at boot before the writer runs; read lock-free in the gate.
static csv_engine_state_fn_t csv_engine_state_fn = NULL;

// Distinct diagnostics -- do NOT overload csv_rows_dropped (which means "queue full"):
static uint32_t csv_cols_unmatched = 0;   // WIDE record whose (source,name) isn't a column
static uint32_t csv_pending_drops  = 0;   // records lost while a WIDE session waited for enum

// One-shot auto-start crash guard (RTC_NOINIT: survives a panic/brownout/watchdog/SW
// reset; wiped by a cold power cycle). Armed when a boot attempt starts; cleared by the
// writer task after it proves stable for 15s. If a boot finds it still armed, the
// previous attempt crashed -> skip CSV this boot so a CSV-startup fault can never
// boot-loop the device; it self-recovers on the next boot.
#define CSV_ATTEMPT_MAGIC 0xA11C0DE5u
RTC_NOINIT_ATTR static uint32_t csv_attempt_inprogress;

static bool csv_time_is_valid(void)
{
    time_t now = time(NULL);
    struct tm tm_now;
    localtime_r(&now, &tm_now);
    return (tm_now.tm_year + 1900) >= 2020;
}

static void csv_sanitize_field(char *s)
{
    for (; *s; s++)
    {
        if (*s == ',' || *s == '"' || *s == '\r' || *s == '\n')
        {
            *s = '_';
        }
    }
}

static esp_err_t csv_open_new_file(void)
{
    struct stat st;
    if (stat(CSV_LOGGER_DIR, &st) != 0)
    {
        if (mkdir(CSV_LOGGER_DIR, 0775) != 0)
        {
            ESP_LOGE(TAG, "Failed to create %s", CSV_LOGGER_DIR);
            return ESP_FAIL;
        }
    }

    if (csv_time_is_valid())
    {
        time_t now = time(NULL);
        struct tm tm_now;
        localtime_r(&now, &tm_now);
        snprintf(csv_file_path, sizeof(csv_file_path), CSV_LOGGER_DIR "/%04d%02d%02d_%02dh%02dm%02ds.csv",
                 tm_now.tm_year + 1900, tm_now.tm_mon + 1, tm_now.tm_mday,
                 tm_now.tm_hour, tm_now.tm_min, tm_now.tm_sec);
    }
    else
    {
        snprintf(csv_file_path, sizeof(csv_file_path), CSV_LOGGER_DIR "/unknown_time_%lld.csv",
                 esp_timer_get_time() / 1000);
    }

    csv_file = fopen(csv_file_path, "w");
    if (csv_file == NULL)
    {
        ESP_LOGE(TAG, "Failed to open %s", csv_file_path);
        return ESP_FAIL;
    }

    // Wide header: timestamp_ms + two system columns (datetime, BATT_V) + one column per channel.
    // Long format was removed (Task #16) and a session only opens after columns are enumerated, so
    // csv_cols is always valid here (a 0-count loop just yields the system-columns-only header).
    // Built from csv_cols, which is fixed for the whole session -> rotation re-emits a byte-
    // identical header, so every rotated file is self-describing. Stream column-by-column (no
    // buffer bound here). The two system columns are emitted in the same fixed order by
    // csv_emit_wide_row(), so header and rows stay aligned.
    csv_file_bytes = 0;
    int n = fprintf(csv_file, "timestamp_ms,datetime,BATT_V");
    if (n > 0) csv_file_bytes += (size_t)n;
    for (int c = 0; c < csv_col_count; c++)
    {
        char hdr[CSV_LOGGER_NAME_MAX + CSV_LOGGER_SOURCE_MAX + 4];
        if (csv_cols[c].dup_name)
            snprintf(hdr, sizeof(hdr), "%s [%s]", csv_cols[c].name, csv_cols[c].source);
        else
            strlcpy(hdr, csv_cols[c].name, sizeof(hdr));
        csv_sanitize_field(hdr);   // sanitize a COPY; the column table keeps the raw name
        n = fprintf(csv_file, ",%s", hdr);
        if (n > 0) csv_file_bytes += (size_t)n;
    }
    n = fprintf(csv_file, "\n");
    if (n > 0) csv_file_bytes += (size_t)n;
    csv_files_count++;
    ESP_LOGI(TAG, "CSV log started: %s (wide, %d cols)", csv_file_path, csv_col_count);
    return ESP_OK;
}

static void csv_close_file(void)
{
    if (csv_file != NULL)
    {
        fflush(csv_file);
        fsync(fileno(csv_file));
        fclose(csv_file);
        ESP_LOGI(TAG, "CSV log closed: %s (%u rows, %u bytes)",
                 csv_file_path, (unsigned)csv_rows_written, (unsigned)csv_file_bytes);
        csv_file = NULL;
        csv_file_bytes = 0;
    }
}

// ---- Wide-format helpers (Task #11) ----
// HARD RULE: none of these may take any AutoPID lock or touch autopid_config. ONLY the
// session-open enumeration (csv_build_wide_columns) calls the provider, and only OUTSIDE
// any open SD write. Pulling a lock into the per-row path would stall the writer inside a
// flash-cache-disable window. Keep it out.

static void csv_free_wide_state(void)
{
    if (csv_cols != NULL)
    {
        heap_caps_free(csv_cols);
        csv_cols = NULL;
    }
    csv_col_count = 0;
}

// Match a record's (source,name) key to a column. -1 if none.
static int csv_find_col(const char *source, const char *name)
{
    for (int c = 0; c < csv_col_count; c++)
    {
        if (strcmp(csv_cols[c].name, name) == 0 &&
            strcmp(csv_cols[c].source, source) == 0)
        {
            return c;
        }
    }
    return -1;
}

// Apply one record to the in-RAM LOCF row. An unknown (source,name) is counted separately
// from a queue-full drop: in WIDE it means a whole channel is absent from every row (a
// config/gating divergence), which is a more serious integrity signal than SD-too-slow.
static void csv_locf_update(const csv_record_t *rec)
{
    int idx = csv_find_col(rec->source, rec->name);
    if (idx < 0)
    {
        csv_cols_unmatched++;
        return;
    }
    csv_cols[idx].last_value = rec->value;
    csv_cols[idx].valid = true;
}

// Emit one wide LOCF row into csv_line_buf with STRICTLY bounded writes, then one fprintf.
// Each field is formatted whole into a temp; if it would not fit, an EMPTY field is written
// instead (keeps exactly csv_col_count columns -> alignment is never broken, and a value is
// never half-written). The line is ALWAYS '\n'-terminated. %.3f uses the C locale decimal
// point (ESP-IDF newlib defaults to "C"; LC_NUMERIC is not switchable to comma here).
static bool csv_emit_wide_row(int64_t ts_ms)
{
    if (csv_line_buf == NULL || csv_cols == NULL)
    {
        return false;
    }
    char *buf = csv_line_buf;
    const size_t cap = CSV_WIDE_LINE_BUF;
    size_t len = 0;
    char field[CSV_WIDE_FIELD_MAX];

    int n = snprintf(field, sizeof(field), "%lld", (long long)ts_ms);
    if (n < 0) { field[0] = '0'; field[1] = '\0'; n = 1; }
    // Always room for the timestamp + reserve; copy it (clamped).
    {
        size_t fl = (size_t)n;
        if (fl > cap - 2) fl = cap - 2;
        memcpy(buf, field, fl);
        len = fl;
    }

    // System columns (must match the header order "timestamp_ms,datetime,BATT_V"):
    //   datetime: human-readable wall-clock at emit time; empty until system time is synced.
    //   BATT_V:   battery voltage from sleep_mode_get_voltage() (non-blocking queue peek).
    // Both use the same reserve-2-bytes bounded-append discipline as the channel loop below.
    {
        struct timeval tv;
        struct tm tm_now;
        gettimeofday(&tv, NULL);
        localtime_r(&tv.tv_sec, &tm_now);
        if ((tm_now.tm_year + 1900) >= 2020)
            n = snprintf(field, sizeof(field), ",%04d-%02d-%02d %02d:%02d:%02d.%03d",
                         tm_now.tm_year + 1900, tm_now.tm_mon + 1, tm_now.tm_mday,
                         tm_now.tm_hour, tm_now.tm_min, tm_now.tm_sec, (int)(tv.tv_usec / 1000));
        else
            n = snprintf(field, sizeof(field), ",");
        if (n < 0) { field[0] = ','; field[1] = '\0'; n = 1; }
        if (len + (size_t)n <= cap - 2) { memcpy(buf + len, field, (size_t)n); len += (size_t)n; }
        else if (len + 1 <= cap - 2) { buf[len++] = ','; }
    }
    {
        float bv = 0.0f;
        sleep_mode_get_voltage(&bv);
        if (isfinite(bv) && bv > 1.0f && bv < 60.0f)
            n = snprintf(field, sizeof(field), ",%.2f", bv);
        else
            n = snprintf(field, sizeof(field), ",");
        if (n < 0) { field[0] = ','; field[1] = '\0'; n = 1; }
        if (len + (size_t)n <= cap - 2) { memcpy(buf + len, field, (size_t)n); len += (size_t)n; }
        else if (len + 1 <= cap - 2) { buf[len++] = ','; }
    }

    for (int c = 0; c < csv_col_count; c++)
    {
        if (csv_cols[c].valid)
            n = snprintf(field, sizeof(field), ",%.3f", csv_cols[c].last_value);
        else
            n = snprintf(field, sizeof(field), ",");
        if (n < 0) { field[0] = ','; field[1] = '\0'; n = 1; }

        // Reserve the last 2 bytes for "\n\0". If the whole field won't fit, write just a
        // bare ',' to preserve column count (never a truncated number).
        if (len + (size_t)n > cap - 2)
        {
            if (len + 1 <= cap - 2) buf[len++] = ',';
            continue;
        }
        memcpy(buf + len, field, (size_t)n);
        len += (size_t)n;
    }

    buf[len++] = '\n';
    buf[len] = '\0';

    int w = fprintf(csv_file, "%s", buf);
    if (w < 0) return false;
    csv_file_bytes += (size_t)w;
    csv_rows_written++;
    return true;
}

// Build csv_cols from the registered provider. Two-pass (count -> exact-size scratch ->
// fill) so we never allocate a fixed 256-wide scratch when the profile has a few dozen
// columns. Runs ONLY at session-open on the writer task, never inside an SD write.
// Returns 1 = built, 0 = provider not ready / no columns (caller retries or falls back),
// -1 = OOM (caller falls back).
static int csv_build_wide_columns(void)
{
    if (csv_col_provider == NULL || csv_line_buf == NULL)
    {
        return 0;   // no provider yet, or no row buffer -> caller falls back to LONG
    }
    csv_free_wide_state();   // defensive: never leak a prior table

    // Pass 1: upper-bound count (no writes).
    int raw = csv_col_provider(NULL, NULL, NULL, CSV_WIDE_MAX_COLS);
    if (raw <= 0)
    {
        return 0;   // -1 (lock busy) or 0 (none yet) -> caller retries / falls back
    }
    if (raw > CSV_WIDE_MAX_COLS) raw = CSV_WIDE_MAX_COLS;

    // Exactly-sized scratch in INTERNAL RAM.
    char (*names)[CSV_LOGGER_NAME_MAX]     = heap_caps_malloc((size_t)raw * CSV_LOGGER_NAME_MAX,   MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    char (*units)[CSV_LOGGER_UNIT_MAX]     = heap_caps_malloc((size_t)raw * CSV_LOGGER_UNIT_MAX,   MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    char (*sources)[CSV_LOGGER_SOURCE_MAX] = heap_caps_malloc((size_t)raw * CSV_LOGGER_SOURCE_MAX, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (names == NULL || units == NULL || sources == NULL)
    {
        heap_caps_free(names); heap_caps_free(units); heap_caps_free(sources);
        return -1;
    }

    // Pass 2: fill (deduped by the provider), clamped to raw.
    int m = csv_col_provider(names, units, sources, raw);
    if (m <= 0)
    {
        heap_caps_free(names); heap_caps_free(units); heap_caps_free(sources);
        return 0;
    }
    if (m > raw) m = raw;

    csv_cols = heap_caps_malloc((size_t)m * sizeof(csv_wide_col_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (csv_cols == NULL)
    {
        heap_caps_free(names); heap_caps_free(units); heap_caps_free(sources);
        return -1;
    }
    for (int c = 0; c < m; c++)
    {
        strlcpy(csv_cols[c].name,   names[c],   sizeof(csv_cols[c].name));
        strlcpy(csv_cols[c].unit,   units[c],   sizeof(csv_cols[c].unit));
        strlcpy(csv_cols[c].source, sources[c], sizeof(csv_cols[c].source));
        csv_cols[c].last_value = 0.0f;
        csv_cols[c].valid = false;
        csv_cols[c].dup_name = false;
    }
    csv_col_count = m;

    // Mark same-name columns (different source) so the header can disambiguate them.
    for (int a = 0; a < m; a++)
        for (int b = a + 1; b < m; b++)
            if (strcmp(csv_cols[a].name, csv_cols[b].name) == 0)
            {
                csv_cols[a].dup_name = true;
                csv_cols[b].dup_name = true;
            }

    heap_caps_free(names);
    heap_caps_free(units);
    heap_caps_free(sources);
    ESP_LOGI(TAG, "Wide CSV: %d columns enumerated", m);
    return 1;
}

void csv_logger_set_column_provider(csv_column_provider_t provider)
{
    csv_col_provider = provider;
}

void csv_logger_set_engine_state_fn(csv_engine_state_fn_t fn)
{
    csv_engine_state_fn = fn;
}

static void csv_logger_task(void *pvParameters)
{
    csv_record_t rec;
    wc_timer_t flush_timer = 0;
    wc_timer_t ignition_poll_timer = 0;
    wc_timer_t ignition_off_timer = 0;
    wc_timer_t grid_timer = 0;            // WIDE fixed-rate grid tick
    bool ignition_on = false;
    bool ignition_off_pending = false;
    // "Require engine running" gate (Stage 1), latched once at task start (config is constant per
    // boot -- store_config reboots). When on AND an engine-state provider is registered (poll_log),
    // AUTO logging also requires the engine to be running; otherwise it degrades to the voltage gate.
    bool require_engine = (config_server_get_csv_require_engine() == 1);
    bool flush_pending = false;

    wc_timer_set(&flush_timer, CSV_LOGGER_FLUSH_PERIOD_MS);
    int64_t csv_task_start_us = esp_timer_get_time();
    bool csv_guard_cleared = false;

    while (1)
    {
        if (!csv_guard_cleared && (esp_timer_get_time() - csv_task_start_us) > 15000000)
        {
            csv_attempt_inprogress = 0;   // survived the danger window; future boots may retry
            csv_guard_cleared = true;
            ESP_LOGI(TAG, "CSV auto-start proven stable (15s) - crash guard cleared");
        }
        // In WIDE fixed-rate mode shorten the wait to the grid period so a tick can fire on
        // idle passes (records may be sparse but the grid still emits at 1/hz). Default 250ms.
        TickType_t rx_to = pdMS_TO_TICKS(250);
        if (csv_session_active && csv_grid_mode == CSV_GRID_FIXED)
        {
            uint32_t gp = (csv_grid_hz > 0) ? (1000u / csv_grid_hz) : 100u;
            if (gp < 1) gp = 1;
            if (gp < 250u) rx_to = pdMS_TO_TICKS(gp);
        }
        bool got_record = (xQueueReceive(csv_queue, &rec, rx_to) == pdPASS);

        // Track ignition with debounce on the off transition.
        if (wc_timer_is_expired(&ignition_poll_timer))
        {
            wc_timer_set(&ignition_poll_timer, CSV_LOGGER_IGNITION_POLL_MS);
            vehicle_ignition_state_t ign = vehicle_ignition_state();
            if (ign == VEHICLE_STATE_IGNITION_ON)
            {
                ignition_on = true;
                ignition_off_pending = false;
            }
            else if (ign == VEHICLE_STATE_IGNITION_OFF)
            {
                if (ignition_on && !ignition_off_pending)
                {
                    ignition_off_pending = true;
                    wc_timer_set(&ignition_off_timer, CSV_LOGGER_IGN_OFF_DEBOUNCE_MS);
                }
                else if (ignition_off_pending && wc_timer_is_expired(&ignition_off_timer))
                {
                    ignition_on = false;
                    ignition_off_pending = false;
                }
            }
            // VEHICLE_STATE_IGNITION_INVALID: keep last known state
        }

        // Log while ignition is on (engine running). Ignition is derived from battery
        // voltage (sleep_mode_get_voltage > sleep_volt) with a 3s off-debounce. The manual
        // mode overrides authoritatively: FORCE_ON logs regardless of ignition (bench +
        // manual control), FORCE_OFF stops regardless of ignition, AUTO follows ignition.
        // NOTE: a session only OPENS when a record arrives, so a forced-on start logs only
        // while AutoPID records are actually flowing (csv_logger_record at autopid.c).
        // Engine-running gate (Stage 1): with the switch on, AUTO logging needs the engine actually
        // running (poll_log: ECU answering / not quiesced) IN ADDITION to the voltage ignition gate.
        // The provider returns true when poll_log isn't the active mode, so this auto-degrades to the
        // voltage gate when RPM isn't available. FORCE_ON/FORCE_OFF still win for bench use.
        bool engine_ok = !require_engine || csv_engine_state_fn == NULL || csv_engine_state_fn();
        bool logging_active = (csv_manual_mode == CSV_MANUAL_ON)  ? true
                            : (csv_manual_mode == CSV_MANUAL_OFF) ? false
                            : (ignition_on && engine_ok);

        // Session close: logging stopped (ignition off / disabled) or SD was pulled.
        if (csv_session_active && (!logging_active || !sdcard_is_mounted()))
        {
            // Accurate close cause for post-hoc forensics: distinguish a voltage ignition-off from the
            // "require engine running" gate going false (engine quiesced while the bench/voltage still
            // reads ON). ignition_on / engine_ok are already computed above for logging_active.
            const char *why = !sdcard_is_mounted()             ? "sd_removed"
                            : (csv_manual_mode == CSV_MANUAL_OFF) ? "manual_stop"
                            : (!ignition_on)                      ? "ignition_off"
                                                                  : "engine_stopped";
            size_t closed_bytes = csv_file_bytes;   // csv_close_file zeroes this; capture first
            csv_close_file();
            csv_session_active = false;
            csv_free_wide_state();
            // Operational event (Task #24): one emit per real session close. Non-blocking enqueue.
            event_log_emit(EVL_DATALOG_CLOSE, "%s (%s, %u bytes)",
                           csv_file_path, why, (unsigned)closed_bytes);
        }

        // WIDE fixed-rate grid: emit one LOCF snapshot row per 1/hz, even on idle passes.
        if (csv_session_active && csv_grid_mode == CSV_GRID_FIXED &&
            wc_timer_is_expired(&grid_timer))
        {
            uint32_t gp = (csv_grid_hz > 0) ? (1000u / csv_grid_hz) : 100u;
            if (gp < 1) gp = 1;
            wc_timer_set(&grid_timer, gp);
            if (!csv_emit_wide_row(esp_timer_get_time() / 1000))
            {
                ESP_LOGE(TAG, "Wide row write failed on %s, closing", csv_file_path);
                csv_close_file();
                csv_session_active = false;
                csv_free_wide_state();
                event_log_emit(EVL_DATALOG_CLOSE, "%s (write_error)", csv_file_path);
                csv_sd_retry_after_ms = (esp_timer_get_time() / 1000) + CSV_LOGGER_SD_RETRY_MS;
                continue;
            }
            flush_pending = true;
        }

        if (!got_record)
        {
            // Idle: opportunistic flush so a power cut loses at most ~1s of data.
            if (csv_session_active && csv_file != NULL && flush_pending)
            {
                fflush(csv_file);
                fsync(fileno(csv_file));
                flush_pending = false;
            }
            continue;
        }

        if (!logging_active)
        {
            continue;   // not logging: discard samples (ignition off or harness disabled)
        }

        // Session open, rate-limited if the SD card is missing or failing.
        if (!csv_session_active)
        {
            int64_t now_ms = esp_timer_get_time() / 1000;
            if (now_ms < csv_sd_retry_after_ms)
            {
                csv_pending_drops++;   // this record is lost while we wait to open
                continue;
            }

            // Wide is the only format (Task #16 removed LONG). It needs the line buffer that
            // csv_logger_init allocates; if that alloc failed there is nothing to fall back to.
            if (csv_line_buf == NULL)
            {
                csv_pending_drops++;
                csv_sd_retry_after_ms = now_ms + CSV_LOGGER_SD_RETRY_MS;
                continue;
            }

            // Latch the grid config ONCE for this session (a snapshot of device_config;
            // single-writer thereafter so no torn cross-core read). A reboot/new session re-reads.
            csv_grid_mode = (config_server_get_csv_grid_mode() == 0) ? CSV_GRID_EVENT : CSV_GRID_FIXED;
            uint32_t hz;
            csv_grid_hz = (config_server_get_csv_grid_hz(&hz) == 1) ? hz : CSV_GRID_HZ_DEFAULT;
            if (csv_grid_hz < 1) csv_grid_hz = 1;
            if (csv_grid_hz > 50) csv_grid_hz = 50;

            // Build the fixed column set from the AutoPID config. May briefly defer if the autopid
            // mutex is busy (held across an ELM read); we NEVER block the writer, and there is NO
            // LONG fallback -- drop + retry until columns are available (pending_drops, surfaced in
            // /csv_status, tracks the loss).
            int br = csv_build_wide_columns();
            if (br <= 0)
            {
                csv_pending_drops++;
                csv_sd_retry_after_ms = now_ms + 250;   // short retry (not the 5s SD backoff)
                continue;
            }

            if (!sdcard_is_mounted() || csv_open_new_file() != ESP_OK)
            {
                csv_free_wide_state();
                csv_sd_retry_after_ms = now_ms + CSV_LOGGER_SD_RETRY_MS;
                continue;
            }
            csv_session_active = true;
            // Operational event (Task #24): one emit per session open (covers auto + manual).
            // Not emitted on rotation (which calls csv_open_new_file directly, not this block).
            event_log_emit(EVL_DATALOG_OPEN, "%s (%d cols)", csv_file_path, csv_col_count);
            wc_timer_set(&flush_timer, CSV_LOGGER_FLUSH_PERIOD_MS);
            if (csv_grid_mode == CSV_GRID_FIXED)
            {
                uint32_t gp = (csv_grid_hz > 0) ? (1000u / csv_grid_hz) : 100u;
                if (gp < 1) gp = 1;
                wc_timer_set(&grid_timer, gp);
            }
        }

        // Update the LOCF row; in EVENT mode emit a wide row now (FIXED mode emits on the grid
        // tick above). The long per-record format was removed (Task #16).
        csv_locf_update(&rec);
        bool wrote_ok = (csv_grid_mode == CSV_GRID_EVENT) ? csv_emit_wide_row(rec.t_ms) : true;
        if (!wrote_ok)
        {
            ESP_LOGE(TAG, "Write failed on %s, closing", csv_file_path);
            csv_close_file();
            csv_session_active = false;
            csv_free_wide_state();
            event_log_emit(EVL_DATALOG_CLOSE, "%s (write_error)", csv_file_path);
            csv_sd_retry_after_ms = (esp_timer_get_time() / 1000) + CSV_LOGGER_SD_RETRY_MS;
            continue;
        }
        flush_pending = true;

        if (wc_timer_is_expired(&flush_timer))
        {
            wc_timer_set(&flush_timer, CSV_LOGGER_FLUSH_PERIOD_MS);
            fflush(csv_file);
            fsync(fileno(csv_file));
            flush_pending = false;
        }

        if (csv_file_bytes >= CSV_LOGGER_ROTATE_BYTES)
        {
            // Rotation: close + reopen. csv_close_file does NOT free the wide column table, so
            // the reopened file re-emits a byte-identical wide header and LOCF carries forward.
            // Capture the path being closed BEFORE csv_open_new_file() overwrites csv_file_path with
            // the new (failed-to-open) name, so the rotate_error event names the file we actually closed.
            char rot_closed[sizeof(csv_file_path)];
            strlcpy(rot_closed, csv_file_path, sizeof(rot_closed));
            csv_close_file();
            if (csv_open_new_file() != ESP_OK)
            {
                csv_session_active = false;
                csv_free_wide_state();
                event_log_emit(EVL_DATALOG_CLOSE, "%s (rotate_error)", rot_closed);
                csv_sd_retry_after_ms = (esp_timer_get_time() / 1000) + CSV_LOGGER_SD_RETRY_MS;
            }
        }
    }
}

void csv_logger_record(const char *name, float value, const char *unit, const char *source)
{
    if (csv_queue == NULL || name == NULL)
    {
        return;
    }

    csv_record_t rec;
    rec.t_ms = esp_timer_get_time() / 1000;
    rec.value = value;
    strlcpy(rec.name, name, sizeof(rec.name));
    strlcpy(rec.unit, (unit != NULL) ? unit : "", sizeof(rec.unit));
    strlcpy(rec.source, (source != NULL) ? source : "", sizeof(rec.source));

    if (xQueueSend(csv_queue, &rec, 0) != pdPASS)
    {
        csv_rows_dropped++;
    }
}

bool csv_logger_is_active_file(const char *abspath)
{
    // Plain read of the writer task's open-file state (same pattern as the status
    // JSON). Lets the SD file manager refuse to delete/rename the in-use log.
    return csv_session_active && csv_file != NULL && abspath != NULL &&
           strcmp(abspath, csv_file_path) == 0;
}

esp_err_t csv_logger_set_manual_override(bool enable)
{
    if (enable)
    {
        // Lazy on-demand start when csv_log=disable at boot (writer never created).
        // SAFE: this runs on the httpd handler task (prio 5 > writer prio 4), AND
        // csv_logger_init() publishes csv_queue BEFORE xTaskCreateStatic -- so the boot
        // publish-race cannot recur on-demand. csv_logger_init() is idempotent (early
        // return if csv_queue != NULL), so the csv_log=enable boot path is a no-op here.
        // Deliberately does NOT arm the RTC one-shot guard (csv_attempt_inprogress): a
        // manual start is operator-initiated, not a boot-loop risk, and arming it would
        // wrongly suppress the next AUTO boot. Only csv_logger_init_deferred() arms it.
        if (csv_queue == NULL)
        {
            esp_err_t e = csv_logger_init();
            if (e != ESP_OK)
            {
                csv_manual_mode = CSV_MANUAL_OFF;   // failed start: don't lie via status
                return e;
            }
        }
        csv_manual_mode = CSV_MANUAL_ON;
    }
    else
    {
        // STOP: authoritative force-off. The writer task stays alive (never deleted at
        // runtime) and closes the current session on its next pass (the close logic runs
        // every loop, even with no records). Logging stays off even if ignition reads on,
        // until the next START or a reboot (which resets to AUTO).
        csv_manual_mode = CSV_MANUAL_OFF;
    }
    return ESP_OK;
}

char *csv_logger_get_status_json(void)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL)
    {
        return NULL;
    }
    cJSON_AddBoolToObject(root, "running", csv_queue != NULL);
    cJSON_AddBoolToObject(root, "sd_mounted", sdcard_is_mounted());
    cJSON_AddBoolToObject(root, "session_active", csv_session_active);
    // Runtime manual mode for the web Start/Stop button: "auto" | "on" | "off". (The "file"
    // field below is read without a lock, but only when session_active is true; the writer
    // fully writes csv_file_path before session_active flips on, so the only unsynchronized
    // window is the close transition, where a stale-but-NUL-terminated name self-heals next poll.)
    cJSON_AddStringToObject(root, "manual_mode",
                            (csv_manual_mode == CSV_MANUAL_ON)  ? "on" :
                            (csv_manual_mode == CSV_MANUAL_OFF) ? "off" : "auto");
    cJSON_AddStringToObject(root, "file", csv_session_active ? csv_file_path : "");
    cJSON_AddNumberToObject(root, "rows_written", csv_rows_written);
    cJSON_AddNumberToObject(root, "rows_dropped", csv_rows_dropped);
    cJSON_AddNumberToObject(root, "files_count", csv_files_count);
    // Wide-format (Task #11) diagnostics. "format" is always "wide" now (long removed, Task #16);
    // "columns" reflects the ACTIVE session. cols_unmatched = records whose (source,name) had no
    // column (a config/gating divergence, distinct from the queue-full rows_dropped);
    // pending_drops = records lost while a session waited for column enumeration.
    cJSON_AddStringToObject(root, "format", "wide");
    cJSON_AddNumberToObject(root, "columns", csv_session_active ? csv_col_count : 0);
    cJSON_AddNumberToObject(root, "cols_unmatched", csv_cols_unmatched);
    cJSON_AddNumberToObject(root, "pending_drops", csv_pending_drops);
    char *out = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return out;
}

// ---- HTTP interface: status, file listing, and CSV download ----

static esp_err_t csv_status_handler(httpd_req_t *req)
{
    char *status = csv_logger_get_status_json();
    httpd_resp_set_type(req, "application/json");
    if (status == NULL)
    {
        httpd_resp_sendstr(req, "{\"running\":false}");
        return ESP_OK;
    }
    httpd_resp_sendstr(req, status);
    free(status);
    return ESP_OK;
}

static esp_err_t csv_list_handler(httpd_req_t *req)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL)
    {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_sendstr(req, "{\"files\":[]}");
        return ESP_OK;
    }
    cJSON *files = cJSON_AddArrayToObject(root, "files");

    // Missing dir (nothing logged yet) is not an error: report an empty list.
    DIR *dir = opendir(CSV_LOGGER_DIR);
    if (dir != NULL)
    {
        struct dirent *entry;
        char path[320];
        struct stat st;
        while ((entry = readdir(dir)) != NULL)
        {
            const char *ext = strrchr(entry->d_name, '.');
            if (ext == NULL || strcasecmp(ext, ".csv") != 0)
            {
                continue;
            }
            cJSON *item = cJSON_CreateObject();
            cJSON_AddStringToObject(item, "name", entry->d_name);
            snprintf(path, sizeof(path), "%s/%s", CSV_LOGGER_DIR, entry->d_name);
            if (stat(path, &st) == 0)
            {
                cJSON_AddNumberToObject(item, "size", (double)st.st_size);
            }
            cJSON_AddItemToArray(files, item);
        }
        closedir(dir);
    }

    char *out = cJSON_PrintUnformatted(root);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, (out != NULL) ? out : "{\"files\":[]}");
    if (out != NULL)
    {
        free(out);
    }
    cJSON_Delete(root);
    return ESP_OK;
}

static esp_err_t csv_download_handler(httpd_req_t *req)
{
    char query[160];
    char name[128];

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(query, "file", name, sizeof(name)) != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Missing 'file' query parameter");
        return ESP_FAIL;
    }

    // Only allow a bare *.csv basename from CSV_LOGGER_DIR -- no traversal/subdirs.
    const char *ext = strrchr(name, '.');
    if (strchr(name, '/') != NULL || strchr(name, '\\') != NULL ||
        strstr(name, "..") != NULL || ext == NULL || strcasecmp(ext, ".csv") != 0)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid file name");
        return ESP_FAIL;
    }

    char path[320];
    snprintf(path, sizeof(path), "%s/%s", CSV_LOGGER_DIR, name);

    FILE *f = fopen(path, "r");
    if (f == NULL)
    {
        httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "File not found");
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "text/csv");
    char disp[160];
    snprintf(disp, sizeof(disp), "attachment; filename=%s", name);
    httpd_resp_set_hdr(req, "Content-Disposition", disp);

    char *buf = malloc(2048);
    if (buf == NULL)
    {
        fclose(f);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
        return ESP_FAIL;
    }

    size_t n;
    esp_err_t ret = ESP_OK;
    while ((n = fread(buf, 1, 2048, f)) > 0)
    {
        if (httpd_resp_send_chunk(req, buf, n) != ESP_OK)
        {
            ret = ESP_FAIL;
            break;
        }
    }
    free(buf);
    fclose(f);
    httpd_resp_send_chunk(req, NULL, 0); // end of response
    return ret;
}

// POST /csv_logger?op=start|stop -- runtime manual start/stop (web Start/Stop button).
static esp_err_t csv_control_handler(httpd_req_t *req)
{
    char query[64];
    char op[16] = {0};
    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(query, "op", op, sizeof(op)) != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Missing op=start|stop");
        return ESP_FAIL;
    }

    esp_err_t e;
    if (strcmp(op, "start") == 0)     { e = csv_logger_set_manual_override(true); }
    else if (strcmp(op, "stop") == 0) { e = csv_logger_set_manual_override(false); }
    else
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "op must be start or stop");
        return ESP_FAIL;
    }

    if (e != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "CSV start failed (out of memory)");
        return ESP_FAIL;
    }

    // Reply with the live status JSON so the UI updates without a second GET.
    // Mirror csv_status_handler: malloc'd, freed after sendstr.
    char *status = csv_logger_get_status_json();
    httpd_resp_set_type(req, "application/json");
    if (status == NULL)
    {
        httpd_resp_sendstr(req, "{\"ok\":true}");
        return ESP_OK;
    }
    httpd_resp_sendstr(req, status);
    free(status);
    return ESP_OK;
}

// ---- /datalog coordination endpoint (no-reboot coexistence, task #36.C) ----
// Mode to restore on resume (snapshotted on the FIRST pause so a re-pause can't clobber it).
static volatile int8_t s_datalog_prepause_mode = CSV_MANUAL_AUTO;

// Compact live state for the host to verify quiesce/resume + run the dead-man's-switch
// (host reconcile reads flash_active/host_bus_claimed; pause/bus_claim read the issued
// tokens). Tokens are emitted as JSON numbers when armed, null when disarmed, matching
// WiCANDatalogClient. Caller frees.
static char *datalog_state_json(void)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddBoolToObject(root, "ok", true);
    cJSON_AddBoolToObject(root, "flash_active", can_flash_active());
    cJSON_AddBoolToObject(root, "datalog_parked", can_datalog_park_active());
    cJSON_AddBoolToObject(root, "host_bus_claimed", can_host_bus_claim_active());
    cJSON_AddStringToObject(root, "manual_mode",
                            (csv_manual_mode == CSV_MANUAL_ON)  ? "on" :
                            (csv_manual_mode == CSV_MANUAL_OFF) ? "off" : "auto");
    uint32_t park_tok = can_park_token();
    uint32_t claim_tok = can_host_bus_claim_token();
    if (park_tok) { cJSON_AddNumberToObject(root, "park_token", park_tok); }
    else          { cJSON_AddNullToObject(root, "park_token"); }
    if (claim_tok) { cJSON_AddNumberToObject(root, "claim_token", claim_tok); }
    else           { cJSON_AddNullToObject(root, "claim_token"); }
    cJSON_AddNumberToObject(root, "lease_ttl_ms", COEXIST_PARK_LEASE_TTL_MS);
    cJSON_AddNumberToObject(root, "claim_ttl_ms", COEXIST_HOST_CLAIM_LEASE_TTL_MS);
    cJSON_AddNumberToObject(root, "bus_idle_ms", can_bus_idle_ms());
    cJSON_AddBoolToObject(root, "stuck_flash_alarm", can_stuck_flash_alarm());
    char *out = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return out;
}

// Restore the pre-pause manual mode after the park flag has ALREADY been lowered (by the
// atomic can_park_lease_release / can_park_lease_reap in can.c). Called by exactly ONE winner
// per park lease -- the REST op=resume handler (after a successful token-matched release) OR
// the dead-man reaper (after a successful token+deadline-matched reap) -- because both clear
// the lease atomically under s_park_mux, so only one of them succeeds and reaches here for a
// given pause. That mutual exclusion is what makes this lock-free mode-restore safe (it does
// not itself re-derive park state). Touches ONLY the csv manual mode, never any CAN bit, so it
// is brick-safe. In the realistic flow the logger was already running before the pause, so
// csv_logger_set_manual_override(true)'s lazy csv_logger_init() early-returns (csv_queue != NULL).
void datalog_restore_mode(void)
{
    if (s_datalog_prepause_mode == CSV_MANUAL_ON)
    {
        csv_logger_set_manual_override(true);   // ON needs the lazy writer-init path
    }
    else
    {
        csv_manual_mode = s_datalog_prepause_mode;  // AUTO/OFF: no init, just restore the gate
    }
}

// Parse an unsigned-int query param; true + *out on a present, parseable value.
static bool datalog_query_u32(const char *query, const char *key, uint32_t *out)
{
    char buf[16];
    if (httpd_query_key_value(query, key, buf, sizeof(buf)) != ESP_OK) { return false; }
    *out = (uint32_t)strtoul(buf, NULL, 10);
    return true;
}

static void datalog_send_state(httpd_req_t *req)
{
    char *s = datalog_state_json();
    httpd_resp_set_type(req, "application/json");
    if (s == NULL) { httpd_resp_sendstr(req, "{\"ok\":false}"); return; }
    httpd_resp_sendstr(req, s);
    free(s);
}

// POST /datalog?op=pause|resume|bus_claim|bus_release|keepalive -- the host's no-reboot
// coexistence + dead-man's-switch coordination layer (WICAN_DEADMAN_AUTORESUME.md).
//   pause     : stop the CSV/AutoPID producer, then arm the park lease (raises the park flag).
//               -> returns park_token.
//   resume    : token-matched release of the park lease + restore pre-pause mode. A stale token
//               (the reaper already auto-resumed after the host vanished) -> 409, which the host
//               treats as success.
//   bus_claim : arm the host bus-claim lease (raises the claim flag) -- the brick fence over the
//               UDS auth window FLASH_ACTIVE_BIT does not cover. -> returns claim_token.
//   bus_release: token-matched clear of the claim (stale token -> 409).
//   keepalive : token-matched renew of EITHER/BOTH leases; never touches a flag.
// All park/claim state here is SEPARATE from the codec-owned FLASH_ACTIVE_BIT: this REST path
// must NEVER write BIT1, or a stray/duplicate resume could un-park a LIVE flash -> soft-brick.
// BIT1 remains the brick-safety guarantee; these are advisory pre-park + auth-window fence.
// Returns the live state JSON. (Reuses csv_logger_set_manual_override; ~public funcs.)
static esp_err_t datalog_control_handler(httpd_req_t *req)
{
    char query[96];
    char op[16] = {0};
    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(query, "op", op, sizeof(op)) != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Missing op");
        return ESP_FAIL;
    }

    bool conflict = false;  // -> HTTP 409 (stale token; host reads it as already-reaped success)

    if (strcmp(op, "pause") == 0)
    {
        // Snapshot the restore target only on the FIRST pause (re-pause must not overwrite it
        // with the already-forced-off mode). Stop the producer BEFORE parking the bus. Arming
        // the park lease raises the park flag atomically (issues park_token, stamps the owning
        // 35001 connection generation).
        // Snapshot the restore target AND decide whether to log only on the FIRST pause: a re-pause
        // (already parked) must neither overwrite the saved mode nor emit a second PARK with no
        // intervening RESUME -- the resume side is edge-gated, so the park side must be too.
        bool was_parked = can_datalog_park_active();
        if (!was_parked) { s_datalog_prepause_mode = csv_manual_mode; }
        csv_logger_set_manual_override(false);
        can_park_lease_arm(COEXIST_PARK_LEASE_TTL_US);
        // Operational milestone (Task #12): datalogger parked for a host session. Only the rising
        // edge is logged; keepalive renews + re-pause are deliberately NOT logged (too chatty).
        if (!was_parked) { event_log_emit(EVL_DATALOG_PARK, "host paused datalog"); }
    }
    else if (strcmp(op, "resume") == 0)
    {
        // Token-matched atomic release of the park flag, THEN restore the mode. If the reaper
        // already reaped (host had vanished) the token no longer matches -> release fails -> 409.
        uint32_t token = 0;
        bool has_token = datalog_query_u32(query, "token", &token);
        if (!can_park_lease_release(has_token ? token : 0))
        {
            conflict = true;  // reaper already reset this lease -> 409, leave state as-is
        }
        else
        {
            datalog_restore_mode();   // park flag already lowered; restore exact pre-pause mode
            // Only the real host-driven resume is logged here. A 409 (stale token) means the reaper
            // already auto-resumed and emitted EVL_REAPER_RESUME, so emitting again would double-log.
            event_log_emit(EVL_DATALOG_RESUME, "host resumed datalog");
        }
    }
    else if (strcmp(op, "bus_claim") == 0)
    {
        // Raise the auth-window brick fence for the WHOLE host-driven session; arming the lease
        // raises the claim flag atomically and issues claim_token.
        bool was_claimed = can_host_bus_claim_active();
        can_host_bus_claim_arm(COEXIST_HOST_CLAIM_LEASE_TTL_US);
        // Operational milestone (Task #12): NC-Flash "cable plugged in" -- the host bus-claim window
        // opened. Log only the rising edge (the release side is edge-gated) so a re-claim renew
        // doesn't double-log. Brackets the flash sequence in events.log.
        if (!was_claimed) { event_log_emit(EVL_HOST_CLAIM, "host claimed bus"); }
    }
    else if (strcmp(op, "bus_release") == 0)
    {
        uint32_t token = 0;
        bool has_token = datalog_query_u32(query, "token", &token);
        if (!can_host_bus_claim_release(has_token ? token : 0))
        {
            conflict = true;  // stale claim token (already reaped) -> 409
        }
        else
        {
            // Host bus-claim window closed cleanly (stale-token 409 already reaped -> no log).
            event_log_emit(EVL_HOST_RELEASE, "host released bus");
        }
    }
    else if (strcmp(op, "keepalive") == 0)
    {
        // Renew whichever leases the host still holds. A token mismatch is silently ignored
        // (the lease simply isn't renewed and the reaper will handle a truly-gone host).
        uint32_t park_tok = 0, claim_tok = 0;
        if (datalog_query_u32(query, "park_token", &park_tok))
        {
            can_park_lease_renew(park_tok, COEXIST_PARK_LEASE_TTL_US);
        }
        if (datalog_query_u32(query, "claim_token", &claim_tok))
        {
            can_host_bus_claim_renew(claim_tok, COEXIST_HOST_CLAIM_LEASE_TTL_US);
        }
    }
    else
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "unknown op");
        return ESP_FAIL;
    }

    if (conflict) { httpd_resp_set_status(req, "409 Conflict"); }
    datalog_send_state(req);
    return ESP_OK;
}

// GET /datalog -- live coordination state so the host can VERIFY (not assume) quiesce/resume.
static esp_err_t datalog_status_handler(httpd_req_t *req)
{
    datalog_send_state(req);
    return ESP_OK;
}

const httpd_uri_t datalog_control_uri = {
    .uri = "/datalog",
    .method = HTTP_POST,
    .handler = datalog_control_handler,
    .user_ctx = NULL
};

const httpd_uri_t datalog_status_uri = {
    .uri = "/datalog",
    .method = HTTP_GET,
    .handler = datalog_status_handler,
    .user_ctx = NULL
};

const httpd_uri_t csv_status_uri = {
    .uri = "/csv_status",
    .method = HTTP_GET,
    .handler = csv_status_handler,
    .user_ctx = NULL
};

const httpd_uri_t csv_list_uri = {
    .uri = "/csv_list",
    .method = HTTP_GET,
    .handler = csv_list_handler,
    .user_ctx = NULL
};

const httpd_uri_t csv_download_uri = {
    .uri = "/download_csv",
    .method = HTTP_GET,
    .handler = csv_download_handler,
    .user_ctx = NULL
};

const httpd_uri_t csv_control_uri = {
    .uri = "/csv_logger",
    .method = HTTP_POST,
    .handler = csv_control_handler,
    .user_ctx = NULL
};

esp_err_t csv_logger_init(void)
{
    if (csv_queue != NULL)
    {
        return ESP_OK;
    }

    // Wide-format row buffer (Task #11): one fixed INTERNAL-RAM buffer reused for every wide
    // row across all sessions (no per-session churn -> no fragmentation as sessions cycle).
    // INTERNAL because the writer dereferences it inside fprintf (a flash-cache-disable
    // window). NULL is non-fatal: a WIDE session simply falls back to LONG.
    if (csv_line_buf == NULL)
    {
        csv_line_buf = heap_caps_malloc(CSV_WIDE_LINE_BUF, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (csv_line_buf == NULL)
        {
            ESP_LOGW(TAG, "Wide row buffer alloc failed - wide CSV will fall back to long");
        }
    }

    // Hybrid design: the queue STORAGE lives in INTERNAL RAM, never PSRAM. auto_pid's
    // decode hot path (autopid.c:170 -> csv_logger_record -> xQueueSend) copies each
    // record into this storage from auto_pid's own task context. A flash-cache-disable
    // window (NVS commit, OTA, SD/SPI op) makes any PSRAM access from that context
    // fault; internal RAM is always reachable. ~22 KB at 256 deep -- the burst buffer
    // that lets us keep full decode-rate logging without dropping samples.
    static uint8_t *csv_queue_storage;
    csv_queue_storage = heap_caps_malloc(CSV_LOGGER_QUEUE_LEN * sizeof(csv_record_t),
                                         MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (csv_queue_storage == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate queue storage");
        return ESP_FAIL;
    }

    QueueHandle_t q = xQueueCreateStatic(CSV_LOGGER_QUEUE_LEN, sizeof(csv_record_t),
                                         csv_queue_storage, &csv_queue_buffer);
    if (q == NULL)
    {
        ESP_LOGE(TAG, "Failed to create queue");
        heap_caps_free(csv_queue_storage);
        return ESP_FAIL;
    }

    // Consumer task stack also in INTERNAL RAM. It runs a tight drain loop (250 ms
    // queue wait), far likelier to coincide with a flash-cache-disable window than the
    // stock logger's 10 s loop -- so a PSRAM stack here is a genuine fault risk. With
    // both queue and stack internal, no CSV code path ever depends on the PSRAM cache.
    static StackType_t *csv_task_stack;
    static StaticTask_t csv_task_buffer;
    csv_task_stack = heap_caps_malloc(CSV_LOGGER_TASK_STACK_SIZE, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (csv_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate task stack");
        vQueueDelete(q);
        heap_caps_free(csv_queue_storage);
        return ESP_FAIL;
    }

    // Publish the queue BEFORE creating the task. csv_logger_task runs at a HIGHER
    // priority (4) than the task that calls this (the deferred-init task is 3; app_main
    // is 1), so xTaskCreateStatic preempts to it IMMEDIATELY -- before any assignment
    // placed AFTER the create could run. If csv_queue were still NULL then, the task's
    // first xQueueReceive(NULL) panics. That race WAS the entire "auto-start boot crash";
    // the producer (csv_logger_record) also saw the NULL queue and silently no-op'd.
    // On-demand never hit it only because the httpd caller is higher prio than 4, so no
    // preempt. Publishing first makes the queue valid the instant the task runs; auto_pid
    // (the producer) simply buffers into it meanwhile.
    csv_queue = q;

    if (xTaskCreateStatic(csv_logger_task, "csv_logger", CSV_LOGGER_TASK_STACK_SIZE,
                          NULL, 4, csv_task_stack, &csv_task_buffer) == NULL)
    {
        ESP_LOGE(TAG, "Failed to create task");
        csv_queue = NULL;   // un-publish so producers stop and a later retry can re-init
        vQueueDelete(q);
        heap_caps_free(csv_queue_storage);
        heap_caps_free(csv_task_stack);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "CSV datalogger started");
    return ESP_OK;
}

static void csv_deferred_init_task(void *arg)
{
    // Modest settle margin before init. The boot crash was a publish race in
    // csv_logger_init() (now fixed); this delay is just headroom past the boot storm.
    vTaskDelay(pdMS_TO_TICKS(CSV_LOGGER_DEFER_BOOT_MS));
    if (csv_logger_init() != ESP_OK)
    {
        ESP_LOGE(TAG, "deferred CSV init failed");
    }
    vTaskDelete(NULL);
}

void csv_logger_init_deferred(void)
{
    // One-shot crash guard: if a prior boot armed an attempt and didn't survive long
    // enough to clear it, that attempt crashed -> skip CSV this boot so we can't
    // boot-loop. Cleared after 15s of stable logging (writer task) or by a cold power
    // cycle (RTC wiped).
    if (csv_attempt_inprogress == CSV_ATTEMPT_MAGIC)
    {
        ESP_LOGW(TAG, "Prior CSV auto-start attempt did not complete - skipping this boot to avoid a boot-loop");
        return;
    }
    csv_attempt_inprogress = CSV_ATTEMPT_MAGIC;   // arm the one-shot guard
    // Tiny internal-RAM stack: it only sleeps then calls csv_logger_init().
    if (xTaskCreate(csv_deferred_init_task, "csv_defer", 4096, NULL, 3, NULL) != pdPASS)
    {
        ESP_LOGE(TAG, "Failed to create deferred-init task");
    }
}
