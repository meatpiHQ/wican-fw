/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
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
 */

/*
 * Native-TWAI request/response datalogger -- Phase B, "measure-first".
 *
 * Why this exists: the AutoPID poll loop is ~0.6 Hz/channel because it is one serial
 * ELM327-emulation loop with a HARDCODED vTaskDelay(100ms) between every PID request
 * (autopid.c:4190) plus a ~1.7 s ATMA broadcast window per cycle. That 100 ms floor is a
 * firmware artifact, NOT an ECU limit: a Denso PCM answers a single-frame mode-01/22 PID
 * in ~5-8 ms (request airtime + ECU turnaround). This module polls those PIDs straight over
 * the native TWAI controller (can_send -> twai_transmit for the request, can_receive for the
 * 0x7E8 reply) with NO artificial delay, single-PID round-robin, and logs the real per-request
 * turnaround so we can size batching (mode-01 multi-PID / UDS multi-DID) against measured data
 * instead of an estimate.
 *
 * Difference from fast_log (Phase A): fast_log is LISTEN_ONLY (passive broadcast). poll_log is
 * NORMAL / on-bus -- it TRANSMITS diagnostic read requests (the same non-intrusive reads every
 * scan tool issues). It is a sibling mode; broadcast sniffing is unchanged and still available.
 *
 * Pure poll-only: this mode does NOT decode broadcast frames (it discards them while waiting for
 * the response). The broadcast channels (RPM/VSS/ECT...) therefore stay empty in the CSV here --
 * that is intentional, so the experiment isolates the polled-channel rate. The eventual production
 * mode is the hybrid (passive broadcast + polling the rest).
 *
 * Single-consumer discipline: in POLL_LOG mode this task is the SOLE twai_receive()/twai_transmit()
 * caller. can_rx_task() is gated off for POLL_LOG in main.c so it never steals response frames
 * (TWAI delivers each frame to exactly one waiter). All bus access goes through can.c so the
 * CAN_ENABLE_BIT / can_block() teardown fence quiesces us during an OTA-upload or sleep can_disable().
 *
 * Teardown safety: every can_receive() uses timeout 0 (non-blocking) and every can_send() uses
 * timeout 0, exactly like fast_log. A blocking receive/transmit could be parked inside the driver
 * when an OTA/sleep can_disable() runs can_block() (clears CAN_ENABLE_BIT, ~1 ms, then
 * twai_driver_uninstall()) -> PANIC. Timeout 0 returns at once so the next can.c call parks on
 * CAN_ENABLE_BIT instead of inside the driver.
 */

#include <string.h>
#include <math.h>
#include <float.h>
#include <stdbool.h>
#include <stdint.h>
#include <ctype.h>
#include <stdlib.h>
#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "esp_attr.h"
#include "esp_err.h"
#include "driver/twai.h"

#include "can.h"
#include "autopid.h"
#include "csv_logger.h"
#include "config_server.h"
#include "expression_parser.h"

#include "poll_log.h"

static const char *TAG = "poll_log";

/* ---- Tunables ----------------------------------------------------------- */
#define POLLLOG_TX_ID            0x7E0u  /* physical request to the PCM (works for mode-01 & mode-22) */
#define POLLLOG_RX_ID            0x7E8u  /* PCM positive-response ID */
#define POLLLOG_PAD              0x55u   /* ISO-TP single-frame padding (ECU ignores unused bytes) */
#define POLLLOG_RESP_TIMEOUT_MS  30      /* per-PID wait budget for the response */
#define POLLLOG_RX_TASK_PRIO     5       /* == can_rx_task; sole TWAI consumer in POLL_LOG */
#define POLLLOG_RX_STACK_BYTES   (1024 * 8)   /* INTERNAL RAM (brick invariant) */
#define POLLLOG_STATS_PERIOD_US  (3LL * 1000 * 1000)    /* emit turnaround stats every 3 s */
#define POLLLOG_GUARD_STABLE_US  (15LL * 1000 * 1000)   /* clear crash-guard after 15 s stable */

/* ---- Hybrid broadcast capture (STAGED -- OFF by default) ----------------- */
/* Flip POLLLOG_HYBRID to 1, then rebuild + OTA, to fold the passive broadcast decode back into
 * poll_log: every non-response frame poll_log already drains while waiting (and currently throws
 * away) is matched against the configured can_filters and logged with source "CANFLT". Result:
 * RPM/VSS/ECT/IAT/TPS/APP fill at bus rate ALONGSIDE their polled copies, so "RPM [CANFLT]" and
 * "RPM [PID]" sit side by side for direct validation -- and it's ~free, the frames are in hand.
 * Kept at 0 for now so the poll-only validation run stays pure (broadcast columns empty). When 0,
 * neither the decoder nor its call sites are compiled, so behaviour is bit-identical to today. */
#define POLLLOG_HYBRID           0
#define POLLLOG_BCAST_PERIOD_MS  20      /* per-broadcast-channel record throttle (~50 Hz/ch) */

/* ---- Independent one-shot RTC crash-guard ------------------------------- */
/* Distinct magic from fast_log (0xFA571A6D) and the CSV logger (0xA11C0DE5). If a prior boot
 * armed this and never cleared it (a crash during POLL_LOG bring-up), skip POLL_LOG for one boot
 * so a startup fault self-recovers instead of boot-looping. Recovery (safe-mode/AP/OTA) runs
 * upstream of poll_log_init() and is unaffected either way. */
#define POLLLOG_GUARD_ARMED      0x9011106Du
static RTC_NOINIT_ATTR uint32_t s_polllog_guard;

static autopid_config_t *s_cfg = NULL;

/* Static task storage -> .bss -> INTERNAL RAM (no PSRAM on the hot poll path). */
static StaticTask_t s_rx_task_buf;
static StackType_t  s_rx_task_stack[POLLLOG_RX_STACK_BYTES];

/* Rolling turnaround stats, reset each time they are printed. */
static struct {
    int64_t sum_us;
    int64_t min_us;
    int64_t max_us;
    int      ok;
    int      timeout;
    int      txfail;
} s_st;

static void polllog_stats_reset(void)
{
    s_st.sum_us = 0;
    s_st.min_us = INT64_MAX;
    s_st.max_us = 0;
    s_st.ok = 0;
    s_st.timeout = 0;
    s_st.txfail = 0;
}

/* WiFi-visible snapshot, read by GET /poll_status via poll_log_get_status_json(). The cumulative
 * counters update LIVE on every poll (immediate answer/timeout feedback); the rtt/req-s metrics
 * refresh once per stats window. Plain aligned 32-bit fields -> atomic enough for a status
 * readout across the poll task and the httpd handler, so no mutex is needed. */
static volatile bool     s_active = false;
static volatile uint32_t s_cum_ok = 0, s_cum_timeout = 0, s_cum_txfail = 0;
static volatile uint32_t s_win_ok = 0, s_win_timeout = 0, s_win_txfail = 0;
static volatile float    s_win_rtt_avg_ms = 0, s_win_rtt_min_ms = 0, s_win_rtt_max_ms = 0, s_win_req_s = 0;

/*
 * Parse a polled-PID command string into request bytes.
 * The config stores cmd as the raw PID string plus a trailing CR, e.g. "010B1\r" (mode-01 PID 0B)
 * or "2217461\r" (mode-22 PID 1746). The LAST hex nibble is the ELM "expected response frames"
 * hint (1 = single frame here), NOT part of the request, so an odd nibble count means we drop the
 * final nibble. Returns request length in bytes (1..3), or 0 if the string isn't a PID request.
 */
static size_t polllog_req_bytes(const char *cmd, uint8_t out[3])
{
    if (!cmd)
        return 0;

    char hex[8];
    size_t n = 0;
    for (const char *p = cmd; *p && n < sizeof(hex); p++)
    {
        if (isxdigit((unsigned char)*p))
            hex[n++] = *p;
        else if (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')
            continue;               /* ignore whitespace/CR */
        else
            break;                  /* non-hex terminator (e.g. an AT command) */
    }

    if (n & 1u)
        n--;                        /* drop the trailing nframes hint -> even nibble count */
    size_t bytes = n / 2;
    if (bytes < 1)
        return 0;
    if (bytes > 3)
        bytes = 3;

    for (size_t i = 0; i < bytes; i++)
    {
        char t[3] = { hex[2 * i], hex[2 * i + 1], 0 };
        out[i] = (uint8_t)strtol(t, NULL, 16);
    }
    return bytes;
}

/* True if msg is the PCM's single-frame POSITIVE response to the request bytes in req[]. */
static bool polllog_match(const twai_message_t *m, const uint8_t *req, size_t rl)
{
    if (m->identifier != POLLLOG_RX_ID || m->extd || m->rtr)
        return false;
    if (m->data_length_code < 3)
        return false;
    if ((m->data[0] & 0xF0u) != 0x00u)                 /* PCI type 0 -> single frame only */
        return false;
    if (m->data[1] != (uint8_t)(req[0] + 0x40u))       /* positive-response service echo */
        return false;
    if (rl >= 2 && m->data[2] != req[1])               /* PID echo (mode-01 PID / mode-22 hi) */
        return false;
    if (rl >= 3 && m->data[3] != req[2])               /* PID echo (mode-22 lo) */
        return false;
    return true;
}

#if POLLLOG_HYBRID
/*
 * Hybrid: decode one drained NON-response frame against the configured broadcast filters and push
 * any matches to the wide CSV with source "CANFLT". Mirror of fast_log's fastlog_decode_frame(),
 * keyed to poll_log's own s_cfg. parameter_t.timer is repurposed as a per-channel throttle (the
 * AutoPID task isn't running in POLL_LOG, so that field is otherwise unused). Takes the same brief
 * autopid_lock as the response decode; never nested (a frame is EITHER our response OR broadcast).
 * A stale 0x7E8 response frame matches no can_filter, so it is correctly ignored here.
 */
static void polllog_decode_broadcast(const twai_message_t *msg)
{
    if (s_cfg == NULL || msg == NULL || msg->rtr)
        return;
    if (!autopid_lock(20))
        return;

    const bool extd = (msg->extd != 0);
    const int64_t now = esp_timer_get_time();

    for (uint32_t fi = 0; fi < s_cfg->can_filters_count; fi++)
    {
        can_filter_t *f = &s_cfg->can_filters[fi];
        if (f->frame_id != msg->identifier || f->is_extended != extd)
            continue;

        /* evaluate_expression() does NOT bounds-check B0..B7: feed a full zero-padded 8-byte buf. */
        uint8_t buf[8] = {0};
        uint8_t n = msg->data_length_code;
        if (n > 8)
            n = 8;
        memcpy(buf, msg->data, n);

        for (uint32_t pi = 0; pi < f->parameters_count; pi++)
        {
            parameter_t *p = &f->parameters[pi];
            if (!p->enabled || p->expression == NULL || p->name == NULL || p->name[0] == '\0')
                continue;
            if (now < p->timer)                        /* per-channel throttle */
                continue;
            p->timer = now + (int64_t)POLLLOG_BCAST_PERIOD_MS * 1000;

            double result = 0;
            if (!evaluate_expression((uint8_t *)p->expression, buf, 0, &result))
                continue;
            if (!isfinite(result))
                continue;
            if (p->min != FLT_MAX && result < (double)p->min)
                continue;
            if (p->max != FLT_MAX && result > (double)p->max)
                continue;

            p->value = (float)result;
            csv_logger_record(p->name, p->value, p->unit, "CANFLT");
        }
        /* no break: a frame_id may appear in more than one filter entry */
    }

    autopid_unlock();
}
#endif /* POLLLOG_HYBRID */

/*
 * Poll one PID: build the ISO-TP single-frame request, transmit, then wait (non-blocking drain)
 * up to POLLLOG_RESP_TIMEOUT_MS for the matching 0x7E8 reply. On a match, decode every enabled
 * parameter via evaluate_expression() on the RAW response bytes (B0=PCI, exactly the buffer shape
 * the expressions are authored against) and push to the wide CSV with source "PID" (matching the
 * column provider). Updates rolling turnaround stats.
 */
static void polllog_poll_one(pid_data_t *pid)
{
    if (!pid || !pid->enabled || !pid->cmd)
        return;

    uint8_t req[3];
    size_t rl = polllog_req_bytes(pid->cmd, req);
    if (rl == 0)
    {
        ESP_LOGW(TAG, "skip PID with unparseable cmd '%s'", pid->cmd);
        return;
    }

    /* Build the ISO-TP single-frame request (always an 8-byte OBD frame). */
    twai_message_t tx = {0};
    tx.identifier = POLLLOG_TX_ID;
    tx.data_length_code = 8;
    tx.data[0] = (uint8_t)rl;                 /* SF PCI = number of payload bytes */
    memcpy(&tx.data[1], req, rl);
    for (int i = 1 + (int)rl; i < 8; i++)
        tx.data[i] = POLLLOG_PAD;

    /* Drop any frames already queued so we time THIS request's response, not a stale one.
     * (Hybrid: decode each as broadcast first -- they're free fast-channel data, and decoding
     *  doesn't re-queue them, so the response timing below is unaffected.) */
    twai_message_t msg;
    while (can_receive(&msg, 0) == ESP_OK)
    {
#if POLLLOG_HYBRID
        polllog_decode_broadcast(&msg);
#endif
        /* discard for response-timing purposes */
    }

    const int64_t t_send = esp_timer_get_time();
    if (can_send(&tx, 0) != ESP_OK)           /* timeout 0: enqueue-or-fail, teardown-safe */
    {
        s_st.txfail++;
        s_cum_txfail++;
        vTaskDelay(1);  /* the only no-reply path that never waits: yield so a persistent TX-queue-full
                         * backlog (driver quiescing for OTA/sleep) can't spin this task -> WDT-safe */
        return;
    }

    const int64_t deadline = t_send + (int64_t)POLLLOG_RESP_TIMEOUT_MS * 1000;
    bool got = false;
    while (esp_timer_get_time() < deadline)
    {
        while (can_receive(&msg, 0) == ESP_OK)        /* non-blocking drain */
        {
            if (!polllog_match(&msg, req, rl))
            {
#if POLLLOG_HYBRID
                polllog_decode_broadcast(&msg);        /* free broadcast channels while we wait */
#endif
                continue;                              /* not our response -> done with this frame */
            }

            const int64_t rtt = esp_timer_get_time() - t_send;

            if (autopid_lock(20))
            {
                for (uint32_t j = 0; j < pid->parameters_count; j++)
                {
                    parameter_t *p = &pid->parameters[j];
                    if (!p->enabled || !p->expression || !p->name || p->name[0] == '\0')
                        continue;
                    double result = 0;
                    if (!evaluate_expression((uint8_t *)p->expression, (uint8_t *)msg.data, 0, &result))
                        continue;
                    if (!isfinite(result))
                        continue;
                    if (p->min != FLT_MAX && result < (double)p->min)
                        continue;
                    if (p->max != FLT_MAX && result > (double)p->max)
                        continue;
                    p->value = (float)result;
                    csv_logger_record(p->name, p->value, p->unit, "PID");
                }
                autopid_unlock();
            }

            s_st.sum_us += rtt;
            if (rtt < s_st.min_us) s_st.min_us = rtt;
            if (rtt > s_st.max_us) s_st.max_us = rtt;
            s_st.ok++;
            s_cum_ok++;
            got = true;
            break;
        }
        if (got)
            break;
        vTaskDelay(1); /* 1 ms @ 1000 Hz tick: teardown-safe yield while awaiting the reply */
    }

    if (!got)
    {
        s_st.timeout++;
        s_cum_timeout++;
    }
}

static void polllog_rx_task(void *arg)
{
    (void)arg;
    ESP_LOGI(TAG, "poll_log task started (NORMAL/on-bus, native-TWAI request/response, sole consumer)");
    s_active = true;   /* GET /poll_status now reports live counters */

    const int64_t start_us = esp_timer_get_time();
    bool guard_cleared = false;
    int64_t stats_t = start_us;
    polllog_stats_reset();

    for (;;)
    {
        /* One single-PID round-robin sweep over all configured polled PIDs. */
        for (uint32_t i = 0; i < s_cfg->pid_count; i++)
        {
            polllog_poll_one(&s_cfg->pids[i]);
            /* Phase B option (b): no per-PID delay. Every poll already yields inside
             * polllog_poll_one -- the response-wait loop sleeps vTaskDelay(1) until the reply lands
             * (the reply can't be queued before we send: we drain stale frames first), a timeout
             * sleeps the full window, and the lone no-wait path (TX-queue-full) yields explicitly.
             * So the idle task and the task watchdog stay fed without burning ~1 ms/PID of forced
             * sleep -- reclaiming ~20% of the sweep time the measure-first run was leaving on the table. */
        }

        /* Calculated channels (Task #17): after a full round-robin sweep every polled source
         * channel's p->value is freshest -- evaluate calc expressions over those and record each
         * as source "CALC". No-op when none configured; takes autopid_lock itself (we hold none here). */
        autopid_eval_calculated_channels();

        if (!guard_cleared && (esp_timer_get_time() - start_us) > POLLLOG_GUARD_STABLE_US)
        {
            s_polllog_guard = 0;
            guard_cleared = true;
            ESP_LOGI(TAG, "poll_log stable; crash-guard cleared");
        }

        const int64_t now = esp_timer_get_time();
        if (now - stats_t > POLLLOG_STATS_PERIOD_US)
        {
            const int total = s_st.ok + s_st.timeout + s_st.txfail;
            const float window_s = (float)(now - stats_t) / 1e6f;
            const float avg_ms = s_st.ok ? ((float)s_st.sum_us / (float)s_st.ok) / 1000.0f : 0.0f;
            const float min_ms = (s_st.ok && s_st.min_us != INT64_MAX) ? (float)s_st.min_us / 1000.0f : 0.0f;
            const float max_ms = (float)s_st.max_us / 1000.0f;
            ESP_LOGI(TAG,
                     "poll stats: ok=%d timeout=%d txfail=%d | rtt avg=%.2f min=%.2f max=%.2f ms | %.0f req/s",
                     s_st.ok, s_st.timeout, s_st.txfail, avg_ms, min_ms, max_ms,
                     window_s > 0 ? total / window_s : 0.0f);
            /* Publish this window's metrics for GET /poll_status (the cumulative ok/timeout/txfail
             * counters already update live on every poll; only the rate/rtt view is windowed). */
            s_win_ok = s_st.ok;
            s_win_timeout = s_st.timeout;
            s_win_txfail = s_st.txfail;
            s_win_rtt_avg_ms = avg_ms;
            s_win_rtt_min_ms = min_ms;
            s_win_rtt_max_ms = max_ms;
            s_win_req_s = (window_s > 0) ? (total / window_s) : 0.0f;
            polllog_stats_reset();
            stats_t = now;
        }
    }
}

void poll_log_init(char *id, uint32_t log_period)
{
    (void)id;
    (void)log_period;

    /* ---- One-shot crash-guard ------------------------------------------- */
    if (s_polllog_guard == POLLLOG_GUARD_ARMED)
    {
        s_polllog_guard = 0; /* disarm so the next boot retries */
        ESP_LOGW(TAG, "crash-guard was armed; skipping POLL_LOG bring-up this boot");
        return;
    }
    s_polllog_guard = POLLLOG_GUARD_ARMED;

    /* ---- Load the channel config (no AutoPID task, no ELM polling) ------- */
    s_cfg = autopid_load_config_only();
    if (s_cfg == NULL)
    {
        ESP_LOGE(TAG, "no config; POLL_LOG inactive");
        s_polllog_guard = 0;
        return;
    }
    if (s_cfg->pid_count == 0)
    {
        ESP_LOGW(TAG, "config has 0 polled PIDs; POLL_LOG is poll-only, nothing to request");
        s_polllog_guard = 0;
        return;
    }

#if POLLLOG_HYBRID
    /* Reset the per-param broadcast throttle timestamps repurposed in polllog_decode_broadcast(). */
    for (uint32_t fi = 0; fi < s_cfg->can_filters_count; fi++)
        for (uint32_t pi = 0; pi < s_cfg->can_filters[fi].parameters_count; pi++)
            s_cfg->can_filters[fi].parameters[pi].timer = 0;
#endif

    /* ---- Bring up native TWAI in NORMAL/on-bus mode (poll_log TRANSMITS) --- */
    /* can_set_silent(0) = NORMAL: unlike fast_log's LISTEN_ONLY, we must be on-bus to send
     * requests and to ACK the PCM's responses. Must precede can_enable() (no-op while ON_BUS). */
    can_set_silent(0);
    int rate = config_server_get_can_rate();
    can_set_bitrate((rate >= 0) ? (uint8_t)rate : (uint8_t)CAN_500K);
    can_enable();
    if (!can_is_enabled())
    {
        ESP_LOGE(TAG, "can_enable failed; POLL_LOG inactive");
        s_polllog_guard = 0;
        return;
    }

    /* ---- Create the sole-consumer poll task ----------------------------- */
    TaskHandle_t h = xTaskCreateStatic(polllog_rx_task, "polllog_rx",
                                       POLLLOG_RX_STACK_BYTES, NULL, POLLLOG_RX_TASK_PRIO,
                                       s_rx_task_stack, &s_rx_task_buf);
    if (h == NULL)
    {
        ESP_LOGE(TAG, "failed to create poll task; POLL_LOG inactive");
        s_polllog_guard = 0;
        return;
    }

    ESP_LOGI(TAG, "Phase B (measure-first) up: %lu polled PID(s), NORMAL/on-bus, single-PID round-robin",
             (unsigned long)s_cfg->pid_count);
}

/*
 * Build the WiFi-visible poll status as a small JSON object (caller free()s). Hand-rolled with
 * snprintf so the fast_log component needs no cJSON/json dependency. ok/timeout/txfail are
 * cumulative since the task started; rtt_* and req_s are the last 3 s window; win_* is that
 * window's poll counts. Returns "active":false with zeroed fields when POLL_LOG never ran, so
 * the endpoint is safe to call in any protocol mode.
 */
char *poll_log_get_status_json(void)
{
    char *buf = malloc(256);
    if (buf == NULL)
        return NULL;
    snprintf(buf, 256,
             "{\"active\":%s,\"ok\":%u,\"timeout\":%u,\"txfail\":%u,"
             "\"rtt_avg_ms\":%.2f,\"rtt_min_ms\":%.2f,\"rtt_max_ms\":%.2f,\"req_s\":%.1f,"
             "\"win_ok\":%u,\"win_timeout\":%u,\"win_txfail\":%u}",
             s_active ? "true" : "false",
             (unsigned)s_cum_ok, (unsigned)s_cum_timeout, (unsigned)s_cum_txfail,
             (double)s_win_rtt_avg_ms, (double)s_win_rtt_min_ms, (double)s_win_rtt_max_ms,
             (double)s_win_req_s,
             (unsigned)s_win_ok, (unsigned)s_win_timeout, (unsigned)s_win_txfail);
    return buf;
}
