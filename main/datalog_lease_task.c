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

/*
 * Dead-man's-switch reaper for no-reboot coexistence (task #36).
 * Design + invariants: docs/internal/WICAN_DEADMAN_AUTORESUME.md (host repo).
 *
 * Brick-safety is enforced by BITS, not time budgets: the resume gate is
 * !flash_active && !host_bus_claim, so no legitimate multi-second UDS auth (7F..78 response
 * pending, up to 60 s/request) can ever expire out from under the host. The TTL leases only
 * decide when a host is GONE; the bits decide when the bus is OWNED.
 */

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_heap_caps.h"

#include "can.h"
#include "csv_logger.h"
#include "datalog_lease_task.h"

#define TAG "datalog_reaper"

static void datalog_lease_task(void *arg)
{
    (void)arg;
    uint64_t flash_since_us = 0;   /* task-local: when FLASH_ACTIVE_BIT was first seen set */
    bool alarm_raised = false;

    for (;;)
    {
        vTaskDelay(pdMS_TO_TICKS(COEXIST_REAPER_TICK_MS));

        can_coexist_snapshot_t s;
        can_coexist_snapshot(&s);

        const bool bus_idle = (s.bus_idle_ms >= COEXIST_BUS_IDLE_QUIESCE_MS);

        /* --- Stuck-flash ALARM (INV-7) ------------------------------------------------
         * A flash that holds FLASH_ACTIVE_BIT past the ceiling is wedged (host vanished
         * mid-TransferData AND the SD self-complete also stalled). Raise a loud, sticky
         * alarm so the operator power-cycles. We NEVER clear BIT1 here: auto-clearing the
         * brick guarantee is itself brick-unsafe. While a flash owns the bus we touch
         * nothing else. */
        if (s.flash_active)
        {
            if (flash_since_us == 0) { flash_since_us = s.now_us; }
            else if (!alarm_raised && (s.now_us - flash_since_us) > COEXIST_STUCK_FLASH_CEILING_US)
            {
                can_set_stuck_flash_alarm(true);
                alarm_raised = true;
                ESP_LOGE(TAG, "NCSTUCKFLASH flash held > %llus -- power cycle required",
                         (unsigned long long)(COEXIST_STUCK_FLASH_CEILING_US / 1000000ULL));
            }
            continue;
        }

        /* Flash is clear: reset the stuck timer + drop any prior alarm. */
        flash_since_us = 0;
        if (alarm_raised) { can_set_stuck_flash_alarm(false); alarm_raised = false; }

        /* --- CLAIM-REAP (INV-8) -------------------------------------------------------
         * The host raised the bus-claim for the auth window then vanished: the claim lease
         * expired, its owning 35001 socket is gone, the bus has gone idle, AND a teardown
         * grace has elapsed (the ECU drops its programming session on host silence). The
         * reap is a COMPARE-AND-ACT: can_host_bus_claim_reap() re-validates the sampled
         * (token,deadline) under the lock and clears ONLY if the claim is still that exact,
         * still-expired lease -- so a fresh op=bus_claim / renew landing between this
         * snapshot and the reap bumps the token/deadline and ABORTS, never destroying a live
         * claim. Clears ONLY the claim flag, never FLASH_ACTIVE_BIT. */
        if (s.host_bus_claimed)
        {
            const bool teardown_elapsed =
                s.claim_armed && (s.now_us > s.claim_deadline_us + COEXIST_TEARDOWN_GRACE_US);
            if (s.claim_expired && !s.claim_owner_alive && bus_idle && teardown_elapsed &&
                can_host_bus_claim_reap(s.claim_token, s.claim_deadline_us))
            {
                s.host_bus_claimed = false;  /* allow the datalog-reap below this tick */
                ESP_LOGW(TAG, "NCCLAIMREAP host bus-claim reaped (host gone mid-claim)");
            }
        }

        /* A live claim still fences the bus (a legitimately slow auth, or a fresh claim that
         * aborted the reap above) -> never resume. */
        if (s.host_bus_claimed) { continue; }

        /* --- DATALOG-REAP (INV-4) -----------------------------------------------------
         * Resume the datalogger ONLY when it is parked, no flash, no claim, the host is
         * provably gone (park lease expired AND its owning socket gone) and the bus is
         * provably idle. Same compare-and-act guard: can_park_lease_reap() lowers the park
         * flag only if the lease is still the sampled (token,deadline); a fresh op=pause in
         * the gap aborts. Only THEN restore the pre-pause mode. Never touches BIT1. */
        if (s.datalog_parked && s.park_expired && !s.park_owner_alive && bus_idle &&
            can_park_lease_reap(s.park_token, s.park_deadline_us))
        {
            datalog_restore_mode();
            ESP_LOGW(TAG, "NCDLAUTORESUME datalogger auto-resumed (host gone, bus idle)");
        }
    }
}

void datalog_lease_task_start(void)
{
    static StackType_t *stack = NULL;
    static StaticTask_t tcb;
    if (stack != NULL) { return; }  /* idempotent: already started */

    /* On every chip target this firmware builds for (esp32s3/esp32c3) StackType_t is a 1-byte
     * word, so the depth arg and the buffer size are the same number of bytes. 4 KB leaves
     * headroom for the rare datalog_restore_mode()->csv_logger_set_manual_override(true) path. */
    const uint32_t stack_bytes = 1024 * 4;
    stack = heap_caps_malloc(stack_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (stack == NULL)
    {
        ESP_LOGE(TAG, "failed to allocate dead-man reaper stack -- auto-resume DISABLED");
        return;
    }
    /* Priority 2: above idle, below the comm/codec tasks (prio 5). Latency is TTL-dominated,
     * not tick-dominated, so a low priority is fine. */
    xTaskCreateStatic(datalog_lease_task, "datalog_reaper", stack_bytes, NULL, 2, stack, &tcb);
    ESP_LOGI(TAG, "dead-man reaper started (1 Hz, brick-safe datalog auto-resume)");
}
