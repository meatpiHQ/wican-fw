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


#ifndef __CAN_H__
#define __CAN_H__
#include "driver/twai.h"

#define CAN_5K				0
#define CAN_10K				1
#define CAN_20K				2
#define CAN_25K				3
#define CAN_50K				4
#define CAN_100K			5
#define CAN_125K			6
#define CAN_250K			7
#define CAN_500K			8
#define CAN_800K			9
#define CAN_1000K			10
#define CAN_AUTO			11
typedef struct {
	uint8_t bus_state;
	uint8_t silent;
	uint8_t loopback;
	uint8_t auto_tx;
	uint16_t brp;
	uint8_t phase_seg1;
	uint8_t phase_seg2;
	uint8_t sjw;
	uint32_t filter;
	uint32_t mask;
	uint8_t auto_bitrate;
}can_cfg_t;


void can_enable(void);
void can_disable(void);
void can_set_silent(uint8_t flag);
void can_set_loopback(uint8_t flag);
void can_set_auto_retransmit(uint8_t flag);
void can_set_filter(uint32_t f);
void can_set_mask(uint32_t m);
void can_set_bitrate(uint8_t rate);
esp_err_t can_receive(twai_message_t *message, TickType_t ticks_to_wait);
esp_err_t can_send(twai_message_t *message, TickType_t ticks_to_wait);
void can_init(uint8_t bitrate);
uint8_t can_is_silent(void);
bool can_is_enabled(void);
uint8_t can_get_bitrate(void);
uint32_t can_msgs_to_rx(void);

/* Single-CAN-owner interlock for no-reboot coexistence (task #36 / plan §5).
 * FLASH_ACTIVE_BIT (codec-owned, event group) is the brick-safety guarantee; the host-owned
 * datalog-park + bus-claim are advisory pre-parks held as s_park_mux-protected flags (NOT
 * event-group bits) so the reaper can reap them atomically. Poll tasks park on
 * can_should_park() (any of the three); non-datalog producers gate on can_flash_active(). */
void can_flash_active_set(void);
void can_flash_active_clear(void);
bool can_flash_active(void);
bool can_datalog_park_active(void);   /* true while a host op=pause is in effect */
bool can_should_park(void);           /* flash_active OR park OR claim */

/* === Dead-man's-switch / brick-safe datalog auto-resume ====================
 * Full design + invariants: docs/internal/WICAN_DEADMAN_AUTORESUME.md (host repo).
 *
 * Shared host<->firmware timing contract. Firmware values are *_US = host *_S * 1e6
 * (host: src/ecu/constants.py PARK_LEASE_TTL_S/HOST_CLAIM_LEASE_TTL_S/BUS_IDLE_
 * QUIESCE_MS/HOST_SESSION_TEARDOWN_GRACE_MS/STUCK_FLASH_CEILING_MS). Keep in sync. */
#define COEXIST_PARK_LEASE_TTL_US        (12ULL  * 1000000ULL)  /* advisory park host-gone backstop */
#define COEXIST_PARK_LEASE_TTL_MS        (12000U)
#define COEXIST_HOST_CLAIM_LEASE_TTL_US  (75ULL  * 1000000ULL)  /* > worst-case 60s 7F..78 auth + margin */
#define COEXIST_HOST_CLAIM_LEASE_TTL_MS  (75000U)
#define COEXIST_BUS_IDLE_QUIESCE_MS      (300U)                 /* SD flash drives blocks ~211ms apart */
#define COEXIST_TEARDOWN_GRACE_US        (3ULL   * 1000000ULL)  /* wait after claim-expiry before resume */
#define COEXIST_STUCK_FLASH_CEILING_US   (180ULL * 1000000ULL)  /* alarm only -- NEVER clears BIT1 */
#define COEXIST_REAPER_TICK_MS           (1000U)                /* dead-man reaper poll period */

/* Host bus-claim: host-asserted fence covering the WHOLE host-driven bus window (UDS auth
 * handshake + flash), raised by REST /datalog?op=bus_claim. It is the brick fence the
 * codec-owned FLASH_ACTIVE_BIT does NOT cover (the host runs 0x10/0x27 security BEFORE the
 * codec sets BIT1). Folded into can_should_park() so the reaper can never auto-resume the
 * poller into a live, security-unlocked session. Held as an s_park_mux flag + renewable lease;
 * arm/renew stamp the owning 35001 connection generation (slcan_port_conn_gen) internally so
 * the reaper can tell "host still connected" from "host vanished". All NULL-group-safe. */
uint32_t can_host_bus_claim_arm(uint64_t ttl_us);    /* raise claim + arm lease -> token */
bool     can_host_bus_claim_renew(uint32_t token, uint64_t ttl_us);       /* token-matched */
bool     can_host_bus_claim_release(uint32_t token); /* token-matched clear (0 = unconditional) */
bool     can_host_bus_claim_reap(uint32_t token, uint64_t deadline_us);   /* reaper compare-and-clear */
bool     can_host_bus_claim_active(void);
uint32_t can_host_bus_claim_token(void);             /* 0 = disarmed */

/* Datalog-park lease: host-gone detection for the advisory pause. Armed by op=pause; the
 * reaper resumes only when this lease has expired AND the owning socket is gone AND the bus
 * is idle. Arm raises the park flag; release/reap lower it. */
uint32_t can_park_lease_arm(uint64_t ttl_us);        /* raise park + arm lease -> token */
bool     can_park_lease_renew(uint32_t token, uint64_t ttl_us);           /* token-matched */
bool     can_park_lease_release(uint32_t token);     /* token-matched clear (0 = unconditional) */
bool     can_park_lease_reap(uint32_t token, uint64_t deadline_us);       /* reaper compare-and-clear */
uint32_t can_park_token(void);                        /* 0 = disarmed */

/* Bus-idle evidence: ms since the last TWAI TX or RX (whichever is later). */
uint32_t can_bus_idle_ms(void);

/* Stuck-flash alarm (reported in /datalog state JSON; NEVER clears FLASH_ACTIVE_BIT). */
void can_set_stuck_flash_alarm(bool on);
bool can_stuck_flash_alarm(void);

/* Atomic one-tick snapshot of all coexistence state for the dead-man reaper, taken under
 * the can.c park spinlock so the multi-field lease can't be read torn. The reaper passes
 * claim_token/claim_deadline_us (and park_token/park_deadline_us) back to can_*_reap(),
 * which re-validate them under the lock so a stale snapshot can never cause a wrong reap. */
typedef struct {
    uint64_t now_us;
    bool     flash_active;
    bool     host_bus_claimed;
    bool     datalog_parked;
    bool     claim_armed;
    bool     claim_expired;
    uint32_t claim_token;
    uint64_t claim_deadline_us;
    bool     claim_owner_alive;
    bool     park_armed;
    bool     park_expired;
    uint32_t park_token;
    uint64_t park_deadline_us;
    bool     park_owner_alive;
    uint32_t bus_idle_ms;
} can_coexist_snapshot_t;
void can_coexist_snapshot(can_coexist_snapshot_t *out);

#endif
