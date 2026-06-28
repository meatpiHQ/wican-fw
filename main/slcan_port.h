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

#ifndef __SLCAN_PORT_H__
#define __SLCAN_PORT_H__

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include <stdint.h>

/*
 * Always-on dedicated SLCAN TCP listener (no-reboot coexistence, task #36).
 *
 * The stock comm_server owns the configured port and serves whatever protocol
 * is persisted (poll_log/realdash/elm327/slcan/...). When the device boots in a
 * datalogger mode the flash codecs are unreachable on that port because
 * dispatch is gated behind `if(protocol == SLCAN)` in main.c::can_tx_task.
 *
 * This second listener is *protocol-independent*: every frame it receives is
 * tagged `DEV_SLCAN_PORT` and pushed onto the SAME RX queue the stock server
 * uses, so main.c::can_tx_task can dispatch it straight to the fast-read/write
 * + slcan codecs regardless of the persisted protocol -- the host flashes with
 * NO protocol-switch reboot. Replies for these (self-contained) codecs come
 * back on a DEDICATED tx queue drained here to this port's own socket, so they
 * never collide with the stock port's traffic.
 *
 * Self-contained on purpose: it shares none of comm_server's module state, so
 * adding it cannot perturb the hardware-proven datalogger/flash path.
 *
 *   rx_queue : shared with the stock server (== main.c xMsg_Rx_Queue).
 *   tx_queue : private to this port  (== main.c xMsg_SlcanPort_Tx_Queue).
 *
 * Returns 0 on success, -1 on task/alloc failure.
 */
int8_t slcan_port_init(uint32_t port, QueueHandle_t *rx_queue, QueueHandle_t *tx_queue);

/* Non-zero while a client is connected to the dedicated port. */
int8_t slcan_port_is_open(void);

/* Owning connection generation for the dead-man's-switch (WICAN_DEADMAN_AUTORESUME.md):
 * returns the current accept() generation while a client is connected (PORT_OPEN_BIT), else
 * 0. The reaper compares this against the generation stamped when the host paused/claimed: a
 * change (host reconnected) or 0 (socket gone) means the original owner is no longer present.
 * Monotonic and never 0 while open, so 0 unambiguously means "no owner". */
uint32_t slcan_port_conn_gen(void);

#endif /* __SLCAN_PORT_H__ */
