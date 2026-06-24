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
 * Native-TWAI fast datalogger -- Phase A (passive broadcast capture).
 *
 * Why this exists: the AutoPID path logs every channel at ~0.6Hz because it is one serial
 * ELM327-emulation loop -- 14 polled PIDs each paced by a hardcoded vTaskDelay(100ms) plus
 * a once-per-cycle ATMA broadcast window (~1700ms/cycle), which starves even the broadcast
 * frames that are already on the bus at 50-100Hz. This module reads those broadcast frames
 * straight off the native TWAI controller (the same controller the ELM327 software emulator
 * uses, so it is proven on the vehicle bus) and decodes them with the existing
 * evaluate_expression() + the existing wide-CSV writer. Result: RPM/VSS/ECT/TPS etc. log at
 * bus rate instead of 0.6Hz.
 *
 * Single-consumer discipline: in FAST_LOG mode this task is the SOLE twai_receive() caller.
 * can_rx_task() is gated off for FAST_LOG in main.c so it never competes for frames (TWAI
 * delivers each frame to exactly one waiter). We go through can_receive()/can.c rather than
 * raw twai_* so the CAN_ENABLE_BIT / can_block() teardown fence quiesces us safely during an
 * OTA upload or sleep-entry can_disable().
 */

#include <string.h>
#include <math.h>
#include <float.h>
#include <stdbool.h>
#include <stdint.h>

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

#include "fast_log.h"

static const char *TAG = "fast_log";

/* ---- Tunables ----------------------------------------------------------- */
#define FASTLOG_RX_TASK_PRIO       5            /* == can_rx_task; sole TWAI consumer in FAST_LOG */
#define FASTLOG_RX_STACK_BYTES     (1024 * 8)   /* INTERNAL RAM (brick invariant) */
#define FASTLOG_RX_DRAIN_MAX       64           /* frames drained per wake before yielding */
#define FASTLOG_RX_PACE_MS         2            /* loop pacing; broadcast frames arrive ~10-20ms */
#define FASTLOG_RECORD_PERIOD_MS   10           /* per-param record throttle (~100Hz cap/channel) */
#define FASTLOG_GUARD_STABLE_US    (15LL * 1000 * 1000) /* clear crash-guard after 15s stable */

/* ---- Independent one-shot RTC crash-guard ------------------------------- */
/* Distinct magic from the CSV logger's guard (0xA11C0DE5). If a prior boot armed this and
 * never cleared it (a crash during FAST_LOG bring-up), skip FAST_LOG for one boot so a
 * startup fault self-recovers instead of boot-looping. Recovery (safe-mode/AP/OTA) runs
 * upstream of fast_log_init() and is unaffected either way. */
#define FASTLOG_GUARD_ARMED        0xFA571A6Du
static RTC_NOINIT_ATTR uint32_t s_fastlog_guard;

static autopid_config_t *s_cfg = NULL;

/* Static task storage -> .bss -> INTERNAL RAM (no PSRAM on the hot capture path). */
static StaticTask_t s_rx_task_buf;
static StackType_t  s_rx_task_stack[FASTLOG_RX_STACK_BYTES];

/*
 * Decode one received CAN frame against the configured broadcast filters and push any
 * matching channels into the wide CSV. Lean equivalent of process_can_filter_frame()'s
 * inner loop, WITHOUT the per-frame ESP_LOGI spam and WITHOUT its 100ms period gate (we
 * throttle to FASTLOG_RECORD_PERIOD_MS instead so the wide grid always has fresh values).
 * Reuses evaluate_expression() unchanged -- the broadcast buffer is the raw 8 CAN bytes
 * (B0..B7), zero-padded, exactly the shape autopid feeds it.
 */
static void fastlog_decode_frame(const twai_message_t *msg)
{
    if (s_cfg == NULL || msg == NULL)
        return;
    if (msg->rtr)                    /* remote-request frames carry no data */
        return;

    /* Brief, bounded lock. Uncontended in FAST_LOG mode (the AutoPID task is not running;
     * only the CSV column provider takes this mutex, briefly, while building the header). */
    if (!autopid_lock(20))
        return;

    const bool extd = (msg->extd != 0);
    const int64_t now = esp_timer_get_time();

    for (uint32_t fi = 0; fi < s_cfg->can_filters_count; fi++)
    {
        can_filter_t *f = &s_cfg->can_filters[fi];
        if (f->frame_id != msg->identifier || f->is_extended != extd)
            continue;

        /* evaluate_expression() does NOT bounds-check B0..B7, so always feed a full,
         * zero-padded 8-byte buffer even for short (<8 byte) frames. */
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

            /* Per-param throttle. parameter_t.timer is unused while the AutoPID task is not
             * running, so we repurpose it as the "next-allowed" timestamp (microseconds). */
            if (now < p->timer)
                continue;
            p->timer = now + (int64_t)FASTLOG_RECORD_PERIOD_MS * 1000;

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
        /* Do not break: a frame_id may, in principle, appear in more than one filter entry. */
    }

    autopid_unlock();
}

static void fastlog_rx_task(void *arg)
{
    (void)arg;
    ESP_LOGI(TAG, "fast_log RX task started (LISTEN_ONLY, sole TWAI consumer)");

    const int64_t start_us = esp_timer_get_time();
    bool guard_cleared = false;
    int64_t calc_next_us = 0;   /* Task #17: throttle the calculated-channel pass */
    twai_message_t msg;

    for (;;)
    {
        /* Drain all currently-available frames with a NON-BLOCKING receive (timeout 0), the
         * same pattern can_rx_task uses. Timeout 0 is REQUIRED for teardown safety: a
         * can_disable() from the OTA-upload handler (config_server) or sleep entry calls
         * can_block(), which clears CAN_ENABLE_BIT and waits only ~1ms before
         * twai_driver_uninstall(). A blocking twai_receive() could still be parked inside the
         * driver during that uninstall and PANIC. With timeout 0 every twai_receive() returns
         * at once, so can_block()'s short wait quiesces us and the next can_receive() parks
         * safely on CAN_ENABLE_BIT instead of inside the driver. */
        int drained = 0;
        while (can_receive(&msg, 0) == ESP_OK)
        {
            fastlog_decode_frame(&msg);
            if (++drained >= FASTLOG_RX_DRAIN_MAX)
                break; /* periodic yield so a flooded bus can't starve other tasks */
        }

        /* Calculated channels (Task #17): throttled per-wake pass. Broadcast has no sweep
         * boundary, so this is best-effort -- a source still un-decoded fails its calc until its
         * frame arrives. Gate to FASTLOG_RECORD_PERIOD_MS to match the per-channel record cap. */
        {
            const int64_t now_us = esp_timer_get_time();
            if (now_us >= calc_next_us)
            {
                autopid_eval_calculated_channels();
                calc_next_us = now_us + (int64_t)FASTLOG_RECORD_PERIOD_MS * 1000;
            }
        }

        vTaskDelay(pdMS_TO_TICKS(FASTLOG_RX_PACE_MS));

        if (!guard_cleared && (esp_timer_get_time() - start_us) > FASTLOG_GUARD_STABLE_US)
        {
            s_fastlog_guard = 0;
            guard_cleared = true;
            ESP_LOGI(TAG, "fast_log stable; crash-guard cleared");
        }
    }
}

void fast_log_init(char *id, uint32_t log_period)
{
    (void)id;
    (void)log_period;

    /* ---- One-shot crash-guard ------------------------------------------- */
    if (s_fastlog_guard == FASTLOG_GUARD_ARMED)
    {
        s_fastlog_guard = 0; /* disarm so the next boot retries */
        ESP_LOGW(TAG, "crash-guard was armed; skipping FAST_LOG bring-up this boot");
        return;
    }
    s_fastlog_guard = FASTLOG_GUARD_ARMED;

    /* ---- Load the channel config (no AutoPID task, no ELM polling) ------- */
    s_cfg = autopid_load_config_only();
    if (s_cfg == NULL)
    {
        ESP_LOGE(TAG, "no config; FAST_LOG inactive");
        s_fastlog_guard = 0;
        return;
    }
    if (s_cfg->can_filters_count == 0)
    {
        ESP_LOGW(TAG, "config has 0 broadcast filters; Phase A is broadcast-only, nothing to capture");
        s_fastlog_guard = 0;
        return;
    }

    /* Reset the per-param throttle timestamps we repurpose in fastlog_decode_frame(). */
    for (uint32_t fi = 0; fi < s_cfg->can_filters_count; fi++)
        for (uint32_t pi = 0; pi < s_cfg->can_filters[fi].parameters_count; pi++)
            s_cfg->can_filters[fi].parameters[pi].timer = 0;

    /* ---- Bring up native TWAI in LISTEN_ONLY (Phase A never transmits) --- */
    /* can_set_silent()/can_set_bitrate() must precede can_enable() (they no-op while ON_BUS). */
    can_set_silent(1);
    int rate = config_server_get_can_rate();
    can_set_bitrate((rate >= 0) ? (uint8_t)rate : (uint8_t)CAN_500K);
    can_enable();
    if (!can_is_enabled())
    {
        ESP_LOGE(TAG, "can_enable failed; FAST_LOG inactive");
        s_fastlog_guard = 0;
        return;
    }

    /* ---- Create the sole-consumer RX task ------------------------------- */
    /* s_cfg is already published; nothing the task dereferences is created after this. */
    TaskHandle_t h = xTaskCreateStatic(fastlog_rx_task, "fastlog_rx",
                                       FASTLOG_RX_STACK_BYTES, NULL, FASTLOG_RX_TASK_PRIO,
                                       s_rx_task_stack, &s_rx_task_buf);
    if (h == NULL)
    {
        ESP_LOGE(TAG, "failed to create RX task; FAST_LOG inactive");
        s_fastlog_guard = 0;
        return;
    }

    ESP_LOGI(TAG, "Phase A up: %lu broadcast filter(s), LISTEN_ONLY, <=%dHz/ch",
             (unsigned long)s_cfg->can_filters_count, 1000 / FASTLOG_RECORD_PERIOD_MS);
}
