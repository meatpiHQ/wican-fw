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
#include <stdarg.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>
#include <sys/time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_attr.h"

#include "event_log.h"

static const char *TAG = "event_log";

// SD layout. EVENT_LOG_DIR ("/sdcard/events") is defined in event_log.h (shared with sd_filemgr, which
// marks it a protected dir). A fopen under it returns NULL (handled) when no card is mounted; event_log
// is a leaf and never reads main's SD_CARD_MOUNT_POINT macro.
#define EVENT_LOG_FILE         EVENT_LOG_DIR "/events.log"

// Tunables. Events are sparse (boot, a few engine/datalog transitions per drive, rare OTA), so the
// ring is small and the writer is a low-priority, infrequently-woken task.
#define EVENT_LOG_RING_N       64           // in-RAM ring depth (last N events, always available)
#define EVENT_LOG_LINE_MAX     192          // one fully-formatted line (no trailing newline)
#define EVENT_LOG_DETAIL_MAX   112          // printf detail portion
#define EVENT_LOG_ROTATE_BYTES (128 * 1024) // rotate the active file past this size
#define EVENT_LOG_KEEP_FILES   4            // events.1.log .. events.<KEEP>.log kept after rotation
#define EVENT_LOG_WRITER_PRIO  2            // below csv writer (4) and poll_log (5): never steals hot cycles
#define EVENT_LOG_WRITER_STACK (4096)
#define EVENT_LOG_WAKE_MS      1000         // writer wakes at least this often (also a flush tick)
#define EVENT_LOG_GUARD_STABLE_US (15 * 1000 * 1000)

// Distinct RTC_NOINIT crash-guard magic (NOT csv 0xA11C0DE5 / poll_log 0x9011106D / fast_log 0xFA571A6D).
#define EVENT_LOG_GUARD_MAGIC  0xE7106A11u
RTC_NOINIT_ATTR static uint32_t s_evl_guard;

typedef struct {
    uint32_t seq;                       // 1-based emit sequence (0 = never written)
    char     line[EVENT_LOG_LINE_MAX];  // preformatted, NUL-terminated, no '\n'
} evl_entry_t;

// In-RAM ring in INTERNAL RAM (.bss). The writer reads it inside fwrite/fsync windows, so it must
// never live in PSRAM. A static array also cannot fail to allocate -- the most brick-safe choice.
static evl_entry_t s_ring[EVENT_LOG_RING_N];
static portMUX_TYPE s_ring_lock = portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t s_head_seq = 0;     // next slot to write (monotonic)
static volatile uint32_t s_persist_seq = 0;  // next slot to flush to SD (monotonic)
static volatile uint32_t s_dropped = 0;      // events overwritten before they reached SD
static volatile uint32_t s_rotations = 0;
static volatile uint32_t s_file_bytes = 0;
static volatile bool     s_sd_ok = false;    // last SD write outcome (for status)

static SemaphoreHandle_t s_wake = NULL;      // emit() -> writer nudge
static SemaphoreHandle_t s_file_mtx = NULL;  // serializes SD file access (writer task + HTTP reader)
static bool s_inited = false;

static event_log_sd_ready_fn_t s_sd_ready_fn = NULL;

void event_log_set_sd_ready_fn(event_log_sd_ready_fn_t fn)
{
    s_sd_ready_fn = fn;   // plain store; written once at boot before the writer matters
}

static const char *evl_code_str(event_log_code_t code)
{
    switch (code)
    {
        case EVL_BOOT:          return "BOOT";
        case EVL_ENGINE_START:  return "ENGINE_START";
        case EVL_ENGINE_STOP:   return "ENGINE_STOP";
        case EVL_DATALOG_OPEN:  return "DATALOG_OPEN";
        case EVL_DATALOG_CLOSE: return "DATALOG_CLOSE";
        case EVL_OTA_START:     return "OTA_START";
        case EVL_OTA_OK:        return "OTA_OK";
        case EVL_OTA_FAIL:      return "OTA_FAIL";
        case EVL_INFO:          return "INFO";
        default:                return "EVENT";
    }
}

void event_log_emit(event_log_code_t code, const char *fmt, ...)
{
    char detail[EVENT_LOG_DETAIL_MAX];
    if (fmt != NULL)
    {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(detail, sizeof(detail), fmt, ap);
        va_end(ap);
    }
    else
    {
        detail[0] = '\0';
    }

    // Wall-clock (when synced) + monotonic uptime, both computed OUTSIDE the critical section.
    int64_t up_ms = esp_timer_get_time() / 1000;
    char ts[24];
    struct timeval tv;
    struct tm tm_now;
    gettimeofday(&tv, NULL);
    localtime_r(&tv.tv_sec, &tm_now);
    if ((tm_now.tm_year + 1900) >= 2020)
        strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &tm_now);
    else
        strlcpy(ts, "unsynced", sizeof(ts));

    char line[EVENT_LOG_LINE_MAX];
    snprintf(line, sizeof(line), "%s up=%lldms %-12s %s",
             ts, (long long)up_ms, evl_code_str(code), detail);

    // Publish into the ring. Brief critical section: one strlcpy + a couple of counter updates.
    portENTER_CRITICAL(&s_ring_lock);
    uint32_t idx = s_head_seq % EVENT_LOG_RING_N;
    strlcpy(s_ring[idx].line, line, sizeof(s_ring[idx].line));
    s_head_seq++;
    s_ring[idx].seq = s_head_seq;   // 1-based
    portEXIT_CRITICAL(&s_ring_lock);

    if (s_wake != NULL)
    {
        xSemaphoreGive(s_wake);   // non-blocking nudge; safe from any task
    }

    ESP_LOGI(TAG, "%s", line);    // also visible on the console
}

// ---- SD writer ----

static bool evl_sd_ready(void)
{
    // If main injected the real predicate use it; otherwise assume "maybe" and let fopen decide.
    return (s_sd_ready_fn != NULL) ? s_sd_ready_fn() : true;
}

// Rename ring: drop the oldest, shift events.i.log -> events.(i+1).log, active -> events.1.log.
// Must be called holding s_file_mtx with the active file CLOSED. Errors (ENOENT) are ignored.
static void evl_rotate(void)
{
    char a[64], b[64];
    snprintf(a, sizeof(a), EVENT_LOG_DIR "/events.%d.log", EVENT_LOG_KEEP_FILES);
    remove(a);
    for (int i = EVENT_LOG_KEEP_FILES - 1; i >= 1; i--)
    {
        snprintf(a, sizeof(a), EVENT_LOG_DIR "/events.%d.log", i);
        snprintf(b, sizeof(b), EVENT_LOG_DIR "/events.%d.log", i + 1);
        rename(a, b);
    }
    rename(EVENT_LOG_FILE, EVENT_LOG_DIR "/events.1.log");
    s_rotations++;
    s_file_bytes = 0;
    ESP_LOGI(TAG, "event log rotated (%u total)", (unsigned)s_rotations);
}

// Drain ring entries [persist..head) to the SD file. Bounded by mtx_wait_ms on the file mutex so a
// wedged card can never stall the caller (the reboot path passes a short timeout). Each line is
// copied out of the ring under the ring lock, then written outside it.
static void evl_drain(uint32_t mtx_wait_ms)
{
    // Snapshot the work window; if the ring wrapped past unpersisted entries, account the loss.
    portENTER_CRITICAL(&s_ring_lock);
    uint32_t head = s_head_seq;
    uint32_t persist = s_persist_seq;
    if ((head - persist) > EVENT_LOG_RING_N)
    {
        s_dropped += (head - persist) - EVENT_LOG_RING_N;
        persist = head - EVENT_LOG_RING_N;
        s_persist_seq = persist;
    }
    portEXIT_CRITICAL(&s_ring_lock);

    if (persist == head)
    {
        return;   // nothing pending
    }
    if (!evl_sd_ready())
    {
        return;   // no card: keep buffering in the ring
    }
    if (s_file_mtx == NULL ||
        xSemaphoreTake(s_file_mtx, pdMS_TO_TICKS(mtx_wait_ms)) != pdTRUE)
    {
        return;   // contended/unavailable: try again next tick
    }

    struct stat stx;
    if (stat(EVENT_LOG_DIR, &stx) != 0)
    {
        if (mkdir(EVENT_LOG_DIR, 0775) != 0)
        {
            s_sd_ok = false;
            xSemaphoreGive(s_file_mtx);
            return;
        }
    }

    FILE *f = fopen(EVENT_LOG_FILE, "a");
    if (f == NULL)
    {
        s_sd_ok = false;
        xSemaphoreGive(s_file_mtx);
        return;   // card vanished / FS error: leave persist where it is, retry later
    }

    bool wrote = true;
    for (uint32_t s = persist; s != head; s++)
    {
        char local[EVENT_LOG_LINE_MAX];
        bool valid;
        // Validate the slot still holds event s (seq == s+1). If a burst of emits overwrote it while
        // this (slow) SD write was in flight, the data is gone -- skip it (count the drop) instead of
        // writing a newer event's text into an older position, which would drop+duplicate lines.
        portENTER_CRITICAL(&s_ring_lock);
        valid = (s_ring[s % EVENT_LOG_RING_N].seq == s + 1);
        if (valid)
        {
            strlcpy(local, s_ring[s % EVENT_LOG_RING_N].line, sizeof(local));
        }
        portEXIT_CRITICAL(&s_ring_lock);

        if (!valid)
        {
            s_dropped++;
            continue;
        }
        if (fprintf(f, "%s\n", local) < 0)
        {
            wrote = false;
            break;
        }
    }
    fflush(f);
    fsync(fileno(f));
    long sz = ftell(f);
    fclose(f);

    if (wrote)
    {
        portENTER_CRITICAL(&s_ring_lock);
        s_persist_seq = head;
        portEXIT_CRITICAL(&s_ring_lock);
        s_sd_ok = true;
        if (sz >= 0) s_file_bytes = (uint32_t)sz;
        if (sz >= EVENT_LOG_ROTATE_BYTES)
        {
            evl_rotate();
        }
    }
    else
    {
        s_sd_ok = false;   // partial write: persist not advanced, lines re-sent next drain
    }

    xSemaphoreGive(s_file_mtx);
}

static void evl_writer_task(void *arg)
{
    (void)arg;
    int64_t start_us = esp_timer_get_time();
    bool guard_cleared = false;

    for (;;)
    {
        // Block until an event is signalled, or wake periodically as a flush heartbeat.
        if (s_wake != NULL)
        {
            xSemaphoreTake(s_wake, pdMS_TO_TICKS(EVENT_LOG_WAKE_MS));
        }
        else
        {
            vTaskDelay(pdMS_TO_TICKS(EVENT_LOG_WAKE_MS));
        }

        evl_drain(1000);

        if (!guard_cleared && (esp_timer_get_time() - start_us) > EVENT_LOG_GUARD_STABLE_US)
        {
            s_evl_guard = 0;   // survived the danger window; future boots may retry SD persistence
            guard_cleared = true;
            ESP_LOGI(TAG, "event_log stable (15s) - crash guard cleared");
        }
    }
}

void event_log_init(void)
{
    if (s_inited)
    {
        return;
    }

    // One-shot crash guard: if a prior boot armed an attempt and didn't survive long enough to clear
    // it, that attempt crashed during event_log SD work -> skip the writer/SD this boot so a fault can
    // never boot-loop the device. The in-RAM ring still records everything; self-recovers next boot.
    if (s_evl_guard == EVENT_LOG_GUARD_MAGIC)
    {
        ESP_LOGW(TAG, "prior event_log attempt did not complete - SD persistence skipped this boot");
        s_inited = true;   // ring + emit still work; just no writer/SD this boot
        return;
    }
    s_evl_guard = EVENT_LOG_GUARD_MAGIC;   // arm

    s_wake = xSemaphoreCreateBinary();
    s_file_mtx = xSemaphoreCreateMutex();
    if (s_wake == NULL || s_file_mtx == NULL)
    {
        ESP_LOGE(TAG, "sync primitive alloc failed - SD persistence disabled (ring still active)");
        s_evl_guard = 0;
        s_inited = true;
        return;
    }

    // Writer task stack in INTERNAL RAM: it dereferences its stack locals inside fwrite/fsync
    // flash-cache-disable windows, where a PSRAM stack would fault (the csv_logger brick invariant).
    static StackType_t *evl_stack;
    static StaticTask_t evl_tcb;
    evl_stack = heap_caps_malloc(EVENT_LOG_WRITER_STACK, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (evl_stack == NULL)
    {
        ESP_LOGE(TAG, "writer stack alloc failed - SD persistence disabled (ring still active)");
        s_evl_guard = 0;
        s_inited = true;
        return;
    }

    if (xTaskCreateStatic(evl_writer_task, "event_log", EVENT_LOG_WRITER_STACK,
                          NULL, EVENT_LOG_WRITER_PRIO, evl_stack, &evl_tcb) == NULL)
    {
        ESP_LOGE(TAG, "writer task create failed - SD persistence disabled (ring still active)");
        heap_caps_free(evl_stack);
        s_evl_guard = 0;
        s_inited = true;
        return;
    }

    s_inited = true;
    ESP_LOGI(TAG, "event log started (ring=%d, file=%s)", EVENT_LOG_RING_N, EVENT_LOG_FILE);
}

// ---- HTTP retrieval (GET /event_log*) ----

// Stream the in-RAM ring oldest->newest as text/plain. Always works (no SD needed). Copies each line
// out under the ring lock, then sends it as a chunk outside the lock.
static esp_err_t evl_send_ram(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/plain");

    uint32_t head, count;
    portENTER_CRITICAL(&s_ring_lock);
    head = s_head_seq;
    portEXIT_CRITICAL(&s_ring_lock);
    count = (head < EVENT_LOG_RING_N) ? head : EVENT_LOG_RING_N;

    for (uint32_t i = 0; i < count; i++)
    {
        uint32_t s = head - count + i;
        char local[EVENT_LOG_LINE_MAX + 2];
        bool valid;
        portENTER_CRITICAL(&s_ring_lock);
        valid = (s_ring[s % EVENT_LOG_RING_N].seq == s + 1);   // skip slots overwritten mid-dump
        if (valid)
        {
            strlcpy(local, s_ring[s % EVENT_LOG_RING_N].line, EVENT_LOG_LINE_MAX);
        }
        portEXIT_CRITICAL(&s_ring_lock);
        if (!valid)
        {
            continue;
        }
        strlcat(local, "\n", sizeof(local));
        if (httpd_resp_send_chunk(req, local, strlen(local)) != ESP_OK)
        {
            return ESP_FAIL;
        }
    }
    httpd_resp_send_chunk(req, NULL, 0);
    return ESP_OK;
}

// Stream the active SD log file chunked, using an OOM-safe laddered INTERNAL-RAM buffer (8K->1K),
// the same discipline sd_filemgr uses to survive WiFi+httpd memory pressure. Falls back to the RAM
// ring if the file can't be opened (no card / not yet written).
static esp_err_t evl_send_file(httpd_req_t *req)
{
    // Serialize against the writer task's append/rotate on the same file. A read handle held open
    // across the writer's rotation rename() is undefined on FatFs and can corrupt the directory
    // entry -- so take s_file_mtx for the whole fopen..fclose. If the writer is busy (e.g. mid-
    // rotation) and we can't acquire in time, fall back to the always-safe in-RAM ring.
    if (s_file_mtx == NULL || xSemaphoreTake(s_file_mtx, pdMS_TO_TICKS(2000)) != pdTRUE)
    {
        return evl_send_ram(req);
    }

    FILE *f = fopen(EVENT_LOG_FILE, "r");
    if (f == NULL)
    {
        xSemaphoreGive(s_file_mtx);
        return evl_send_ram(req);
    }

    httpd_resp_set_type(req, "text/plain");
    httpd_resp_set_hdr(req, "Content-Disposition", "attachment; filename=events.log");

    size_t bufsz = 8 * 1024;
    char *buf = NULL;
    while (bufsz >= 1024)
    {
        buf = heap_caps_malloc(bufsz, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (buf != NULL) break;
        bufsz /= 2;
    }
    if (buf == NULL)
    {
        fclose(f);
        xSemaphoreGive(s_file_mtx);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
        return ESP_FAIL;
    }

    size_t n;
    esp_err_t ret = ESP_OK;
    while ((n = fread(buf, 1, bufsz, f)) > 0)
    {
        if (httpd_resp_send_chunk(req, buf, n) != ESP_OK)
        {
            ret = ESP_FAIL;
            break;
        }
    }
    heap_caps_free(buf);
    fclose(f);
    xSemaphoreGive(s_file_mtx);
    httpd_resp_send_chunk(req, NULL, 0);
    return ret;
}

static esp_err_t evl_send_status(httpd_req_t *req)
{
    uint32_t head;
    portENTER_CRITICAL(&s_ring_lock);
    head = s_head_seq;
    portEXIT_CRITICAL(&s_ring_lock);
    uint32_t ring_count = (head < EVENT_LOG_RING_N) ? head : EVENT_LOG_RING_N;

    // Hand-rolled JSON (no cJSON dep -> keeps event_log a minimal leaf), mirroring poll_log.
    char body[256];
    snprintf(body, sizeof(body),
             "{\"sd_ready\":%s,\"sd_ok\":%s,\"file_bytes\":%u,\"rotations\":%u,"
             "\"events_total\":%u,\"ring_count\":%u,\"dropped\":%u}",
             evl_sd_ready() ? "true" : "false",
             s_sd_ok ? "true" : "false",
             (unsigned)s_file_bytes, (unsigned)s_rotations,
             (unsigned)head, (unsigned)ring_count, (unsigned)s_dropped);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, body);
    return ESP_OK;
}

static esp_err_t evl_router_handler(httpd_req_t *req)
{
    const char *seg = req->uri + strlen("/event_log");
    if (*seg == '/') seg++;

    char route[16] = {0};
    size_t rl = strcspn(seg, "?");
    if (rl >= sizeof(route)) rl = sizeof(route) - 1;
    memcpy(route, seg, rl);
    route[rl] = '\0';

    if (strcmp(route, "status") == 0)
    {
        return evl_send_status(req);
    }
    if (strcmp(route, "ram") == 0)
    {
        return evl_send_ram(req);
    }
    // "" (the bare /event_log) or "tail" -> the persisted file, RAM-ring fallback.
    return evl_send_file(req);
}

esp_err_t event_log_register_handlers(httpd_handle_t server)
{
    static const httpd_uri_t evl_uri = {
        .uri = "/event_log*",
        .method = HTTP_GET,
        .handler = evl_router_handler,
        .user_ctx = NULL,
    };
    esp_err_t ret = httpd_register_uri_handler(server, &evl_uri);
    if (ret == ESP_OK || ret == ESP_ERR_HTTPD_HANDLER_EXISTS)
    {
        return ESP_OK;
    }
    return ret;
}
