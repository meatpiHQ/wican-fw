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

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include  "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_event.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include <string.h>
#include "comm_server.h"
#include "lwip/sockets.h"
#include "driver/twai.h"
#include "can.h"
#include "slcan_port.h"   /* slcan_port_conn_gen(): owning 35001 connection generation (dead-man's-switch) */
#include "hw_config.h"

static EventGroupHandle_t s_can_event_group = NULL;
static StaticEventGroup_t xCanEventGroupBuffer;
#define CAN_ENABLE_BIT 		BIT0
/* Single-CAN-owner interlock (no-reboot coexistence, task #36 / plan §5): set by
 * the fast-read/fast-write codecs for the whole duration of a flash/read so the
 * datalogger poll task parks instead of injecting a stray 0x7E0 frame into the
 * ECU's ISO-TP reassembly mid-TransferData (which soft-bricks). At most one TX
 * producer owns the bus while this is set. */
#define FLASH_ACTIVE_BIT 	BIT1

/* === Host-owned coexistence state: datalog-park + host-bus-claim (dead-man's-switch) =======
 * docs/internal/WICAN_DEADMAN_AUTORESUME.md.
 *
 * DELIBERATELY NOT event-group bits. The two host-owned states (advisory datalog pause, and
 * the host bus-claim that fences the UDS auth window) live ENTIRELY under the s_park_mux
 * spinlock as {active flag + token + owner-generation + u64 deadline}. This is the brick fix
 * for the reaper TOCTOU: with the flag and its lease in ONE lock, the dead-man reaper can
 * COMPARE-AND-ACT atomically (reap clears only if the token+deadline are still the exact ones
 * it sampled), so a fresh host arm/renew landing between the reaper's snapshot and its action
 * bumps the token/deadline and ABORTS the reap -- it can never destroy a fresh, live claim and
 * un-park the poller into a security-unlocked session.
 *
 * FLASH_ACTIVE_BIT stays the codec-owned event-group bit, written ONLY at the four codec sites
 * (INV-1, untouched). can_should_park() ORs the lock-free active-flag reads with it. The REST
 * layer never touches FLASH_ACTIVE_BIT, so a stray/duplicate resume can never un-park a LIVE
 * flash -- BIT1 remains the sole brick-safety guarantee, park/claim are advisory pre-park. */
static portMUX_TYPE s_park_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t s_token_seq = 0;            /* monotonic token source (0 is reserved) */

/* The host bus-claim and the datalog-park are two instances of the SAME dead-man lease shape
 * -- {active flag + token + owner-generation + u64 deadline} -- so they share ONE lease_t type
 * and ONE set of generic primitives (lease_arm/renew/release/reap/token) instead of two
 * byte-identical copies (task #43). Both instances are guarded by the single s_park_mux and draw
 * tokens from the single shared s_token_seq above, so claim/park tokens can never collide and the
 * reaper's one-lock cross-lease snapshot stays torn-free. Every field is volatile so the lock-free
 * .active reads on the hot park path keep the exact single-byte semantics of the old volatile
 * statics; token/owner_gen/deadline are only ever touched under s_park_mux. */
typedef struct {
	volatile bool     active;       /* lease raised; READ LOCK-FREE (can_should_park / *_active) */
	volatile uint32_t token;        /* 0 = disarmed */
	volatile uint32_t owner_gen;    /* slcan_port conn-gen that armed/last-renewed the lease */
	volatile uint64_t deadline_us;  /* 0 = disarmed; else esp_timer deadline */
} lease_t;

static lease_t s_claim = {0};   /* host bus-claim raised over the UDS auth window (auth fence) */
static lease_t s_park  = {0};   /* host REST datalog-pause (advisory pre-park)                 */
static volatile uint32_t s_last_bus_activity_ms = 0; /* last TWAI TX or RX (atomic 32-bit ms) */
static volatile bool     s_stuck_flash_alarm = false;

#define TAG 		__func__
enum bus_state
{
    OFF_BUS = 0,
    ON_BUS = 1,
	END_BUS
};
static const twai_timing_config_t twai_timing_config[] = {
	{.brp = 800, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 400, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 200, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 128, .tseg_1 = 16, .tseg_2 = 8, .sjw = 3, .triple_sampling = false},
	{.brp = 80, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 40, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 32, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 16, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 8, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 4, .tseg_1 = 16, .tseg_2 = 8, .sjw = 3, .triple_sampling = false},
	{.brp = 4, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
};

//static EventGroupHandle_t s_can_event_group;
//
static TimerHandle_t xCAN_EN_Timer;
//static uint8_t silent = 0;
//static uint8_t auto_retransmit = 0;
static uint8_t datarate = CAN_500K;
//static uint8_t bus_state = OFF_BUS;
//static uint32_t mask = 0xFFFFFFFF;
//static uint32_t filter = 0;
static can_cfg_t can_cfg = {.bus_state = END_BUS, .auto_bitrate = 0};

#define TWAI_CONFIG(tx_io_num, rx_io_num, op_mode) {.mode = op_mode, .tx_io = tx_io_num, .rx_io = rx_io_num,        \
                                                                    .clkout_io = TWAI_IO_UNUSED, .bus_off_io = TWAI_IO_UNUSED,      \
                                                                    .tx_queue_len = 100, .rx_queue_len = 100,                           \
                                                                    .alerts_enabled = TWAI_ALERT_NONE,  .clkout_divider = 0,        \
                                                                    .intr_flags = ESP_INTR_FLAG_LEVEL1}


static twai_general_config_t g_config_normal = TWAI_GENERAL_CONFIG_DEFAULT(TX_GPIO_NUM, RX_GPIO_NUM, TWAI_MODE_NORMAL);
static twai_general_config_t g_config_silent = TWAI_GENERAL_CONFIG_DEFAULT(TX_GPIO_NUM, RX_GPIO_NUM, TWAI_MODE_LISTEN_ONLY);
//static const twai_general_config_t g_config_no_ack = TWAI_GENERAL_CONFIG_DEFAULT(TX_GPIO_NUM, RX_GPIO_NUM, TWAI_MODE_NO_ACK);

static twai_filter_config_t f_config = TWAI_FILTER_CONFIG_ACCEPT_ALL();

//block tx/rx
void can_block(void)
{
	xEventGroupClearBits(s_can_event_group, CAN_ENABLE_BIT);

	if( xTimerIsTimerActive( xCAN_EN_Timer ) != pdFALSE )
	{
		xTimerReset( xCAN_EN_Timer, 0 );
		xTimerStop(xCAN_EN_Timer, 0);
	}
	vTaskDelay(pdMS_TO_TICKS(1));//wait for rx to finish
}
//unblock tx/rx
void can_unblock(void)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}

	if( xTimerIsTimerActive( xCAN_EN_Timer ) == pdFALSE )
	{
		xTimerStart( xCAN_EN_Timer, 0 );
	}
	else
	{
		xTimerReset( xCAN_EN_Timer, 0 );
	}
}

/* --- Single-CAN-owner interlock (task #36 / plan §5) -----------------------
 * A flash/read codec sets FLASH_ACTIVE_BIT before it takes the bus and clears
 * it on EVERY exit path (success / host-gone / abort / socket-close). The
 * datalogger poll task short-sleeps and skips while it is set, so at most one
 * TX producer ever drives the single TWAI controller. NULL-group-safe so it is
 * callable before can_init(). */
void can_flash_active_set(void)
{
	if(s_can_event_group)
		xEventGroupSetBits(s_can_event_group, FLASH_ACTIVE_BIT);
}

void can_flash_active_clear(void)
{
	if(s_can_event_group)
		xEventGroupClearBits(s_can_event_group, FLASH_ACTIVE_BIT);
}

bool can_flash_active(void)
{
	return s_can_event_group &&
		   (xEventGroupGetBits(s_can_event_group) & FLASH_ACTIVE_BIT) != 0;
}

/* --- Host-requested datalog pause (advisory pre-park) ---------------------------
 * s_park.active is the BIT2 replacement, written ONLY under s_park_mux by the lease
 * arm/release/reap below. NULL-safe (a plain volatile bool, valid before can_init). */
bool can_datalog_park_active(void)
{
	return s_park.active;
}

/* True while the single TWAI controller is reserved by ANY of: a flash codec
 * (FLASH_ACTIVE_BIT, event group), a host REST datalog-pause (s_park.active), or a host
 * bus-claim over the UDS auth window (s_claim.active). The datalogger poll task and the
 * AutoPID poll task park on THIS so neither injects a stray frame while the bus is reserved.
 * The triple OR is the brick interlock: even if a stray resume cleared the park flag,
 * FLASH_ACTIVE_BIT or the claim flag independently keeps every producer parked. The two
 * flags are lock-free single-byte reads; FLASH_ACTIVE_BIT is read via the event group.
 * NULL-group-safe (can_flash_active() handles a NULL group; the flags default false). */
bool can_should_park(void)
{
	return can_flash_active() || s_park.active || s_claim.active;
}

/* --- Dead-man's-switch lease primitives (WICAN_DEADMAN_AUTORESUME.md) ----------------
 * ONE generic implementation drives BOTH leases; can_host_bus_claim_* / can_park_lease_*
 * below are thin wrappers that pick the s_claim or s_park instance (task #43). Every park/claim
 * state transition is done ENTIRELY under s_park_mux: the active flag, token, owner-generation
 * and deadline move together so no reader sees a torn lease and the reaper can compare-and-act
 * atomically. slcan_port_conn_gen() and esp_timer_get_time() are read BEFORE entering the
 * critical section (they take their own locks / are independently atomic) to keep the critical
 * section short and avoid lock nesting. Tokens come from the single shared s_token_seq; 0 is the
 * reserved "disarmed / no token" sentinel so a stale token never matches.
 *
 *  arm     : issue a fresh token + deadline, raise the flag (op=bus_claim / op=pause).
 *  renew   : token-matched deadline bump + owner re-stamp (op=keepalive, in-flash reconnect).
 *  release : token-matched clear (handler op=bus_release / op=resume); 0 = unconditional.
 *  reap    : token+deadline-matched clear for the reaper -- clears ONLY if the lease is STILL
 *            the exact (token,deadline) the reaper sampled AND still expired, so a host
 *            arm/renew landing in the snapshot->act gap aborts the reap (the brick fix). */

/* Lower a lease's four fields together. MUST be called with s_park_mux held. */
static inline void lease_clear(lease_t *L)
{
	L->active = false;
	L->token = 0;
	L->owner_gen = 0;
	L->deadline_us = 0;
}

static uint32_t lease_arm(lease_t *L, uint64_t ttl_us)
{
	uint64_t now = (uint64_t)esp_timer_get_time();
	uint32_t owner = (uint32_t)slcan_port_conn_gen();
	portENTER_CRITICAL(&s_park_mux);
	uint32_t tok = ++s_token_seq;
	if(tok == 0) { tok = ++s_token_seq; }   /* skip the reserved 0 sentinel on wrap */
	L->token = tok;
	L->owner_gen = owner;
	L->deadline_us = now + ttl_us;
	L->active = true;                        /* raised LAST: the other fields are coherent first */
	portEXIT_CRITICAL(&s_park_mux);
	return tok;
}

static bool lease_renew(lease_t *L, uint32_t token, uint64_t ttl_us)
{
	uint64_t now = (uint64_t)esp_timer_get_time();
	uint32_t owner = (uint32_t)slcan_port_conn_gen();
	bool ok = false;
	portENTER_CRITICAL(&s_park_mux);
	if(L->active && L->token == token)
	{
		L->deadline_us = now + ttl_us;
		if(owner != 0) { L->owner_gen = owner; }  /* re-stamp on in-flash reconnect */
		ok = true;
	}
	portEXIT_CRITICAL(&s_park_mux);
	return ok;
}

static bool lease_release(lease_t *L, uint32_t token)
{
	bool ok = false;
	portENTER_CRITICAL(&s_park_mux);
	if(L->active && (token == 0 || L->token == token))
	{
		lease_clear(L);
		ok = true;
	}
	else if(!L->active && token == 0)
	{
		ok = true;   /* already released + unconditional -> idempotent success */
	}
	portEXIT_CRITICAL(&s_park_mux);
	return ok;
}

static bool lease_reap(lease_t *L, uint32_t token, uint64_t deadline_us)
{
	uint64_t now = (uint64_t)esp_timer_get_time();
	bool ok = false;
	portENTER_CRITICAL(&s_park_mux);
	/* Clear ONLY if the lease is STILL the exact one the reaper sampled (token+deadline)
	 * AND still expired. Any arm (new token) or renew (new deadline) in the gap aborts. */
	if(L->active && L->token == token &&
	   L->deadline_us == deadline_us && deadline_us != 0 && now > deadline_us)
	{
		lease_clear(L);
		ok = true;
	}
	portEXIT_CRITICAL(&s_park_mux);
	return ok;
}

static uint32_t lease_token(lease_t *L)
{
	portENTER_CRITICAL(&s_park_mux);
	uint32_t t = L->token;
	portEXIT_CRITICAL(&s_park_mux);
	return t;
}

/* --- Host bus-claim (auth-window fence): wrappers over the s_claim instance --- */
uint32_t can_host_bus_claim_arm(uint64_t ttl_us) { return lease_arm(&s_claim, ttl_us); }
bool can_host_bus_claim_renew(uint32_t token, uint64_t ttl_us) { return lease_renew(&s_claim, token, ttl_us); }
bool can_host_bus_claim_release(uint32_t token) { return lease_release(&s_claim, token); }
bool can_host_bus_claim_reap(uint32_t token, uint64_t deadline_us) { return lease_reap(&s_claim, token, deadline_us); }
bool can_host_bus_claim_active(void) { return s_claim.active; }   /* lock-free volatile read */
uint32_t can_host_bus_claim_token(void) { return lease_token(&s_claim); }

/* --- Datalog-park (advisory pre-park): wrappers over the s_park instance --- */
uint32_t can_park_lease_arm(uint64_t ttl_us) { return lease_arm(&s_park, ttl_us); }
bool can_park_lease_renew(uint32_t token, uint64_t ttl_us) { return lease_renew(&s_park, token, ttl_us); }
bool can_park_lease_release(uint32_t token) { return lease_release(&s_park, token); }
bool can_park_lease_reap(uint32_t token, uint64_t deadline_us) { return lease_reap(&s_park, token, deadline_us); }
uint32_t can_park_token(void) { return lease_token(&s_park); }

uint32_t can_bus_idle_ms(void)
{
	uint32_t now_ms = (uint32_t)((uint64_t)esp_timer_get_time() / 1000ULL);
	return now_ms - s_last_bus_activity_ms;   /* unsigned wrap is fine (same modulus) */
}

void can_set_stuck_flash_alarm(bool on)
{
	s_stuck_flash_alarm = on;
}

bool can_stuck_flash_alarm(void)
{
	return s_stuck_flash_alarm;
}

/* One-tick snapshot for the dead-man reaper. The whole park/claim lease is read under
 * s_park_mux so the reaper never sees a torn lease; FLASH_ACTIVE_BIT and the conn-gen
 * (independently atomic) are read just outside it. The reaper passes the sampled token +
 * deadline back to can_*_reap(), which re-validates them under the lock before acting --
 * so a slightly-stale snapshot can never cause a wrong reap. owner_alive falls back to
 * "any client connected" when the owner-gen was never captured (0), so a bus_claim issued
 * before the 35001 socket opened does not collapse presence-detection to TTL-only. */
void can_coexist_snapshot(can_coexist_snapshot_t *out)
{
	if(out == NULL) { return; }
	uint64_t now = (uint64_t)esp_timer_get_time();
	uint32_t live_gen = (uint32_t)slcan_port_conn_gen();
	bool flash = can_flash_active();
	uint32_t idle_ms = (uint32_t)(now / 1000ULL) - s_last_bus_activity_ms;

	portENTER_CRITICAL(&s_park_mux);
	out->now_us            = now;
	out->flash_active      = flash;
	out->host_bus_claimed  = s_claim.active;
	out->datalog_parked    = s_park.active;
	out->claim_armed       = s_claim.active;
	out->claim_token       = s_claim.token;
	out->claim_deadline_us = s_claim.deadline_us;
	out->claim_expired     = (s_claim.deadline_us != 0 && now > s_claim.deadline_us);
	out->claim_owner_alive = (s_claim.owner_gen == 0) ? (live_gen != 0)
	                                                  : (live_gen == s_claim.owner_gen);
	out->park_armed        = s_park.active;
	out->park_token        = s_park.token;
	out->park_deadline_us  = s_park.deadline_us;
	out->park_expired      = (s_park.deadline_us != 0 && now > s_park.deadline_us);
	out->park_owner_alive  = (s_park.owner_gen == 0) ? (live_gen != 0)
	                                                 : (live_gen == s_park.owner_gen);
	out->bus_idle_ms       = idle_ms;
	portEXIT_CRITICAL(&s_park_mux);
}


void can_enable(void)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}
	
	twai_timing_config_t *t_config;
	t_config = (twai_timing_config_t *)&twai_timing_config[datarate];
//	t_config = (twai_timing_config_t *)&twai_timing_config[CAN_500K];
	f_config.acceptance_code = 0;
	f_config.acceptance_mask = 0xFFFFFFFF;

//	f_config.acceptance_code = can_cfg.filter;
//	f_config.acceptance_mask = can_cfg.mask;
	f_config.single_filter = 1;
	g_config_silent.intr_flags = ESP_INTR_FLAG_LEVEL3 | ESP_INTR_FLAG_SHARED;
	g_config_normal.intr_flags = ESP_INTR_FLAG_LEVEL3 | ESP_INTR_FLAG_SHARED;
	// Deepen the RX FIFO (TWAI_GENERAL_CONFIG_DEFAULT is only 5). The native FAST_LOG capture
	// (Task #18) accepts ALL frames and can briefly stall during SD/flash cache-disable
	// windows; a deeper queue prevents frame loss across the stall. Harmless for every other
	// mode -- strictly fewer drops, ~a few KB of internal RAM at install time.
	g_config_silent.rx_queue_len = 96;
	g_config_normal.rx_queue_len = 96;
	if(can_cfg.silent)
	{
		if(twai_driver_install(&g_config_silent, (const twai_timing_config_t *)t_config, &f_config) == ESP_OK)
		{
			ESP_LOGI(TAG, "start silent mode");
		}
		else
		{
			ESP_LOGE(TAG, "Failed to install TWAI driver in silent mode");
			return;
		}
	}
	else
	{
		if(twai_driver_install(&g_config_normal, (const twai_timing_config_t *)t_config, &f_config) == ESP_OK)
		{
			ESP_LOGI(TAG, "start normal mode");
		}
		else
		{
			ESP_LOGE(TAG, "Failed to install TWAI driver in normal mode");
			return;
		}
	}

	if(twai_start() == ESP_OK)
	{
		ESP_LOGI(TAG, "start twai");
	}
	else
	{
		ESP_LOGE(TAG, "Failed to start TWAI");
		return;
	}
	twai_clear_receive_queue();
	can_unblock();
	can_cfg.bus_state = ON_BUS;
	vTaskDelay(pdMS_TO_TICKS(2));
	gpio_set_level(CAN_STDBY_GPIO_NUM, 0);
}

void can_disable(void)
{
	if(can_cfg.bus_state == OFF_BUS)
	{
		return;
	}
	else if(can_cfg.bus_state == ON_BUS)
	{
		gpio_set_level(CAN_STDBY_GPIO_NUM, 1);
		can_block();
		if(twai_stop() == ESP_OK)
		{
			ESP_LOGI(TAG, "stop twai");
		}
		else
		{
			ESP_LOGE(TAG, "Failed to stop TWAI");
		}
		if(twai_driver_uninstall() == ESP_OK)
		{
			ESP_LOGI(TAG, "uninstall twai");
		}
		else
		{
			ESP_LOGE(TAG, "Failed to uninstall TWAI");
		}
		can_cfg.bus_state = OFF_BUS;
	}
}

void can_set_silent(uint8_t flag)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}

	can_cfg.silent = flag;
}
void can_set_loopback(uint8_t flag)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}

	can_cfg.loopback = flag;
}

uint8_t can_is_silent(void)
{
	return can_cfg.silent;
}
void can_set_auto_retransmit(uint8_t flag)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}

//	auto_retransmit = flag;
}

void can_set_filter(uint32_t f)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}

	can_cfg.filter = f;
}

void can_set_mask(uint32_t m)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}
	can_cfg.mask = m;
}

void can_set_bitrate(uint8_t rate)
{
	if(can_cfg.bus_state == ON_BUS || can_cfg.auto_bitrate)
	{
		return;
	}
	datarate = rate;
}
uint8_t can_get_bitrate(void)
{
	return datarate;
}
static void vCAN_EN_Callback( TimerHandle_t xTimer )
{
	xEventGroupSetBits(s_can_event_group, CAN_ENABLE_BIT);
}
void can_init(uint8_t bitrate)
{
	if(s_can_event_group == NULL)
	{
		s_can_event_group = xEventGroupCreateStatic(&xCanEventGroupBuffer);
		xCAN_EN_Timer= xTimerCreate
						   ( /* Just a text name, not used by the RTOS
							 kernel. */
							 "CANTimer",
							 /* The timer period in ticks, must be
							 greater than 0. */
							 pdMS_TO_TICKS(10),
							 /* The timers will auto-reload themselves
							 when they expire. */
							 pdFALSE,
							 /* The ID is used to store a count of the
							 number of times the timer has expired, which
							 is initialised to 0. */
							 ( void * ) 0,
							 /* Each timer calls the same callback when
							 it expires. */
							 vCAN_EN_Callback
						   );
	}
	if( xTimerIsTimerActive( xCAN_EN_Timer ) != pdFALSE )
	{
		xTimerStop( xCAN_EN_Timer, 0 );
	}

	can_cfg.auto_bitrate = 0;

	if(bitrate == CAN_AUTO)
	{
		bitrate = CAN_500K;
		// can_cfg.auto_bitrate = 1;
		// can_cfg.bus_state = OFF_BUS;
		// can_set_bitrate(CAN_100K);
	}
}


esp_err_t can_receive(twai_message_t *message, TickType_t ticks_to_wait)
{
	esp_err_t ret;
	static uint32_t rx_error;
	static uint8_t store_silent_flag = 0;
	static uint8_t bitrate_found = 1;

	xEventGroupWaitBits(s_can_event_group,
							CAN_ENABLE_BIT,
							pdFALSE,
							pdFALSE,
							portMAX_DELAY);

	// if(can_cfg.auto_bitrate)
	// {
	// 	ret = twai_receive(message, 0);

	// 	if(ret == ESP_OK)
	// 	{
	// 		rx_error = 0;
	// 		bitrate_found = 1;
	// 		if(bitrate_found == 0)
	// 		{
	// 			bitrate_found = 1;
	// 			if(store_silent_flag != can_cfg.silent)
	// 			{
	// 				can_cfg.silent = store_silent_flag;
	// 				can_disable();
	// 				can_enable();
	// 			}
	// 		}
	// 	}
	// 	else
	// 	{
	// 		rx_error++;
	// 		if(bitrate_found == 1)
	// 		{
	// 			bitrate_found = 0;
	// 			store_silent_flag = can_cfg.silent;
	// 		}

	// 		if(rx_error >=120)
	// 		{
	// 			ESP_LOGW(TAG, "try differnt baudrate");
	// 			rx_error = 0;

	// 			can_disable();
	// 			can_cfg.silent = 1;
	// 			datarate++;
	// 			datarate %= (CAN_1000K+1);
	// 			can_enable();
	// 		}
	// 	}
	// 	return ret;
	// }
	// else
	{
		esp_err_t ret = twai_receive(message, ticks_to_wait);
		if(ret == ESP_OK)
		{
			/* Stamp bus activity (dead-man's-switch bus-idle evidence): a received frame
			 * proves the bus is not quiescent. Single atomic 32-bit ms write. */
			s_last_bus_activity_ms = (uint32_t)((uint64_t)esp_timer_get_time() / 1000ULL);
		}
		return ret;
	}
}

/* NOTE (single-CAN-owner invariant, task #36): can_send() intentionally does NOT check
 * FLASH_ACTIVE_BIT here. The flash codecs themselves call can_send() WHILE holding the bit,
 * so a blanket reject would deadlock the flash. Single-ownership is therefore enforced at
 * each TX PRODUCER, which must park/skip while the bus is reserved:
 *   - poll_log polllog_rx_task ........ parks on can_should_park()   (poll_log.c)
 *   - AutoPID autopid_task ............ parks on can_should_park()   (autopid.c)
 *   - MQTT can/rx command handler ..... skips if can_flash_active()  (mqtt.c)
 *   - REALDASH/SAVVYCAN/SLCAN/ELM327 .. serialized: they run INLINE on can_tx_task, which
 *                                       is blocked inside the codec for the whole flash.
 * Any NEW producer added here MUST honor this contract or it can inject a stray frame into
 * the ECU's ISO-TP reassembly mid-TransferData and soft-brick. */
esp_err_t can_send(twai_message_t *message, TickType_t ticks_to_wait)
{
//	xEventGroupWaitBits(s_can_event_group,
//							CAN_ENABLE_BIT,
//							pdFALSE,
//							pdFALSE,
//							portMAX_DELAY);
	EventBits_t uxBits = xEventGroupGetBits(s_can_event_group);

	if(uxBits & CAN_ENABLE_BIT)
	{
		esp_err_t ret = twai_transmit(message, ticks_to_wait);
		if(ret == ESP_OK)
		{
			/* Stamp bus activity (dead-man's-switch bus-idle evidence): the single TX
			 * chokepoint. Single atomic 32-bit ms write. */
			s_last_bus_activity_ms = (uint32_t)((uint64_t)esp_timer_get_time() / 1000ULL);
		}
		return ret;
	}
	else return ESP_ERR_INVALID_STATE;
}

bool can_is_enabled(void)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return true;
	}
	else return false;
//	EventBits_t uxBits = xEventGroupGetBits(s_can_event_group);
//	return (uxBits & CAN_ENABLE_BIT);
}

uint32_t can_msgs_to_rx(void)
{
	twai_status_info_t status_info;

	twai_get_status_info(&status_info);

	return status_info.msgs_to_rx;
}
