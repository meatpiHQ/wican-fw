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

#ifndef POLL_LOG_H
#define POLL_LOG_H

#include <stdint.h>
#include <stdbool.h>

/*
 * POLL_LOG mode (Task #18, Phase B "measure-first"): native-TWAI request/response
 * polling of the configured mode-01/22 PIDs, with NO ELM327 emulation and NO hardcoded
 * 100 ms inter-poll delay. Single-PID round-robin -- the simplest correct poller -- so we
 * can measure the REAL per-request turnaround on the PCM before adding batching.
 *
 * Sibling to fast_log (passive broadcast). Selected via the "poll_log" protocol string.
 */
void poll_log_init(char *id, uint32_t log_period);

/*
 * Live poll metrics for GET /poll_status, as a malloc'd JSON string the caller must free().
 * Safe to call in any protocol mode -- returns {"active":false,...} when POLL_LOG isn't running.
 * Shape: {active, ok, timeout, txfail (cumulative), rtt_avg_ms/min/max, req_s (last 3 s window),
 * win_ok/win_timeout/win_txfail (that window's counts)}.
 */
char *poll_log_get_status_json(void);

/*
 * Engine/quiesce state, derived purely from whether the ECU is answering polls (Stage 1).
 * Safe to call in any protocol mode: when POLL_LOG is not the active mode they report
 * "engine running / bus not idle" so other modes and stale reads never suppress logging.
 *  - poll_log_engine_running(): true while the ECU is answering (or POLL_LOG inactive).
 *  - poll_log_quiesced():       true while the bus is flipped to LISTEN_ONLY (engine off).
 *  - poll_log_bus_idle_ms():    ms since the last received frame while quiesced; UINT32_MAX
 *                               while actively polling / outside POLL_LOG (Route-B sleep sensor).
 */
bool     poll_log_engine_running(void);
bool     poll_log_quiesced(void);
uint32_t poll_log_bus_idle_ms(void);

#endif /* POLL_LOG_H */
