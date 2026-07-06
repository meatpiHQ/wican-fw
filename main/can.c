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
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include <string.h>
#include "esp_twai.h"
#include "esp_twai_onchip.h"
#include "can.h"
#include "hw_config.h"
#if HW_HAS_MCP2515
#include "driver/spi_master.h"
#include "esp_twai_mcp2515.h"
#endif

#define TAG 		__func__

enum bus_state
{
    OFF_BUS = 0,
    ON_BUS = 1,
	END_BUS
};

typedef struct {
	twai_frame_t frame;
	uint8_t data[TWAI_FRAME_MAX_LEN];
} can_tx_slot_t;

typedef struct {
	twai_message_t msg;
	can_bus_t bus;
} can_rx_item_t;

// Indexed by the CAN_5K..CAN_1000K rate codes in can.h
static const uint32_t can_bitrate_bps[] = {
	5000, 10000, 20000, 25000, 50000, 100000,
	125000, 250000, 500000, 800000, 1000000
};

static can_cfg_t can_cfg[CAN_BUS_COUNT];
static twai_node_handle_t can_node[CAN_BUS_COUNT];
// Mutex guarding each node handle's lifecycle: can_disable() deletes the node,
// so create/delete/transmit are serialized under it to prevent use-after-free.
// Don't hold it across a blocking call.
static SemaphoreHandle_t node_lock[CAN_BUS_COUNT];
// One queue shared by all buses; can_receive() returns the bus tag.
static QueueHandle_t can_rx_queue = NULL;
// Per-bus "enabled" bits (BIT(bus)). These are state flags, not events:
// set by can_enable(), cleared by can_disable(), never consumed by waiters.
// can_receive() parks on wait-any until some bus is up (keeps can_rx_task
// asleep while CAN is off); can_send() reads its bus's bit as a fast-fail
// gate. Only touched from task context (the FromISR set is deferred/lossy).
static EventGroupHandle_t s_can_event_group = NULL;
#define CAN_ENABLE_BIT(bus)		BIT(bus)
#define CAN_ENABLE_BIT_ANY		((1 << CAN_BUS_COUNT) - 1)

// The node driver queues a POINTER to the caller's twai_frame_t and transmits
// from it later (no copy), so every in-flight TX needs to have long enough lifetime.
// can_send() copies the message into a slot from this pool; on_tx_done frees
// it. The pool size doubles as the driver tx_queue_depth, and a counting
// semaphore (taken with the caller's timeout) provides the legacy
// block-until-queue-space behavior.
// 32 is the most the uint32_t slot bitmask can track, and buys ~7 ms of burst
// absorption at 500 kbit/s -- the legacy 8-deep queue (~2 ms) overflowed on
// ordinary vehicle-bus burst clusters.
#define CAN_TX_SLOT_COUNT	32
static can_tx_slot_t tx_slot[CAN_BUS_COUNT][CAN_TX_SLOT_COUNT];
// Counting semaphore whose count == number of free TX slots
// can_send() takes it with the caller's timeout.
// can_on_tx_done() gives it back from ISR context.
// Ordering rule: take BEFORE node_lock, never while holding it.
static SemaphoreHandle_t tx_slot_sem[CAN_BUS_COUNT];
// Bitmask that tracks which slots are free, guarded by tx_slot_num
static uint32_t tx_slot_used[CAN_BUS_COUNT];
// Spinlock for the slot bitmask above, which is the one piece of TX state
// shared with ISR context (can_on_tx_done), so we can't use a mutex.
static portMUX_TYPE tx_slot_mux = portMUX_INITIALIZER_UNLOCKED;

// RX frames lost because the shared rx queue was full (rx task too slow).
// Incremented from can_on_rx_done (ISR context), logged rate-limited from
// can_receive() in task context.
static volatile uint32_t rx_drop_count[CAN_BUS_COUNT];
static uint32_t rx_drop_logged[CAN_BUS_COUNT];
static int64_t rx_drop_log_us[CAN_BUS_COUNT];

// TX frames dropped by can_send() (bus down, or no free slot within the
// caller's timeout). Counted and logged in place--can_send() is task
// context, unlike the RX drop path above.
static uint32_t tx_drop_count[CAN_BUS_COUNT];
static int64_t tx_drop_log_us[CAN_BUS_COUNT];

// Bus-off recovery: neither driver recovers on its own, so a state-change
// callback (ISR) flags the bus and wakes a task that recreates the node.
// Full recreate rather than twai_node_recover(): the frame in flight at
// bus-off gets no on_tx_done, so bare recovery would leak one TX slot per
// bus-off; teardown + can_tx_slots_reset starts clean.
static TaskHandle_t can_recovery_task_handle = NULL;
static volatile uint32_t can_busoff_pending;	// bitmask, set from ISR
static uint32_t can_busoff_count[CAN_BUS_COUNT];

// Callback to receive frames from driver (ISR context): drain the frame out
// of the hardware, tag it with its bus, and hand it to can_receive() via the
// shared queue--all protocol work happens later in task context
static bool can_on_rx_done(twai_node_handle_t handle, const twai_rx_done_event_data_t *edata, void *user_ctx)
{
	(void)edata;
	// Bus id was registered as the callback's user_ctx in can_enable()
	can_rx_item_t item = { .bus = (can_bus_t)(uintptr_t)user_ctx };
	// Point the driver's output buffer directly at the queue item's payload
	// array, so the data lands in its final place with no second copy
	twai_frame_t rx_frame = {
		.buffer = item.msg.data,
		.buffer_len = sizeof(item.msg.data),
	};

	// Only legal inside this callback; pulls the pending frame from hardware
	if (twai_node_receive_from_isr(handle, &rx_frame) != ESP_OK)
	{
		return false;
	}

	// Convert new twai_frame_t api that the MCP2515 driver uses to the legacy
	// twai_message_t that the rest of the firmware uses.
	// ss/self have no equivalent in the twai_frame_t api
	item.msg.flags = 0;
	item.msg.identifier = rx_frame.header.id;
	item.msg.extd = rx_frame.header.ide;
	item.msg.rtr = rx_frame.header.rtr;
	// For classic CAN, dlc==len. This will need to be updated if we ever support FD
	item.msg.data_length_code =
			(rx_frame.header.dlc > TWAI_FRAME_MAX_DLC) ? TWAI_FRAME_MAX_DLC : rx_frame.header.dlc;

	// Queue full = frame dropped: count it here, log it from task context
	// task_woken -> tell the driver to context-switch on ISR exit if this
	// send unblocked a higher-priority task (i.e. can_rx_task).
	BaseType_t task_woken = pdFALSE;
	if (xQueueSendFromISR(can_rx_queue, &item, &task_woken) != pdTRUE)
	{
		rx_drop_count[item.bus]++;
	}
	return (task_woken == pdTRUE);
}

// Callback when the driver finishes transmitting a frame (ISR context):
// return its backing TX slot to the pool. This is the only thing that
// replenishes the slots a can_send() may be blocked waiting on.
static bool can_on_tx_done(twai_node_handle_t handle, const twai_tx_done_event_data_t *edata, void *user_ctx)
{
	(void)handle;
	// Bus id was registered as the callback's user_ctx in can_enable()
	can_bus_t bus = (can_bus_t)(uintptr_t)user_ctx;
	// The driver hands back the exact twai_frame_t pointer we submitted;
	// recover the slot containing it
	can_tx_slot_t *slot = __containerof(edata->done_tx_frame, can_tx_slot_t, frame);

	portENTER_CRITICAL_ISR(&tx_slot_mux);
	tx_slot_used[bus] &= ~BIT(slot - &tx_slot[bus][0]);
	portEXIT_CRITICAL_ISR(&tx_slot_mux);

	// Release the count so a blocked can_send() can claim the slot;
	// task_woken as in can_on_rx_done
	BaseType_t task_woken = pdFALSE;
	xSemaphoreGiveFromISR(tx_slot_sem[bus], &task_woken);
	return (task_woken == pdTRUE);
}

static bool can_on_state_change(twai_node_handle_t handle, const twai_state_change_event_data_t *edata, void *user_ctx)
{
	(void)handle;
	if (edata->new_sta != TWAI_ERROR_BUS_OFF)
	{
		return false;
	}

	can_bus_t bus = (can_bus_t)(uintptr_t)user_ctx;
	__atomic_fetch_or(&can_busoff_pending, BIT(bus), __ATOMIC_SEQ_CST);

	BaseType_t task_woken = pdFALSE;
	if (can_recovery_task_handle != NULL)
	{
		vTaskNotifyGiveFromISR(can_recovery_task_handle, &task_woken);
	}
	return (task_woken == pdTRUE);
}

static void can_recovery_task(void *arg)
{
	(void)arg;
	while (1)
	{
		ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

		// Tear down first so can_send() fails fast (bus enable bit cleared)
		// instead of burning its full timeout per frame against a dead node.
		uint32_t pending = __atomic_exchange_n(&can_busoff_pending, 0, __ATOMIC_SEQ_CST);
		for (int bus = 0; bus < CAN_BUS_COUNT; bus++)
		{
			if (!(pending & BIT(bus)) || !can_is_enabled((can_bus_t)bus))
			{
				pending &= ~BIT(bus);
				continue;
			}
			can_busoff_count[bus]++;
			ESP_LOGW(TAG, "bus %d: bus-off #%lu, recreating node (%lu TX dropped so far)",
					 bus, can_busoff_count[bus], tx_drop_count[bus]);
			can_disable((can_bus_t)bus);
		}

		if (pending == 0)
		{
			continue;
		}

		// Let the bus settle; also rate-limits recreate flapping when the
		// fault (e.g. shorted bus) persists across recoveries.
		vTaskDelay(pdMS_TO_TICKS(250));

		for (int bus = 0; bus < CAN_BUS_COUNT; bus++)
		{
			if (pending & BIT(bus))
			{
				can_enable((can_bus_t)bus);
			}
		}
	}
}

// Reset TX slot accounting to "all free". Only call with the bus node torn
// down (or never created).
static void can_tx_slots_reset(can_bus_t bus)
{
	portENTER_CRITICAL(&tx_slot_mux);
	tx_slot_used[bus] = 0;
	portEXIT_CRITICAL(&tx_slot_mux);
	// Frames still queued in the driver at disable time are dropped without 
	// an on_tx_done callback, so the semaphore must be refilled by hand.
	while (xSemaphoreGive(tx_slot_sem[bus]) == pdTRUE)
	{
	}
}

// Best-effort translation of the stored legacy filter/mask (raw SJA1000
// single-filter register values, as set by slcan Fxxx/Mxxx: std ID in bits
// [31:21], mask bit 1 = "don't care") to the node API (bare 11-bit id, mask
// bit 1 = "must match"). Extended-ID acceptance through the legacy register
// layout is not representable; frames are filtered as standard IDs.
// Must be called before twai_node_enable().
static void can_apply_filter(can_bus_t bus)
{
	if (can_cfg[bus].mask == 0xFFFFFFFF)
	{
		return;	// legacy accept-all; node default is already accept-all
	}

	twai_mask_filter_config_t fcfg = {
		.id = (can_cfg[bus].filter >> 21) & 0x7FF,
		.mask = ((~can_cfg[bus].mask) >> 21) & 0x7FF,
		.is_ext = false,
	};

	ESP_LOGW(TAG, "bus %d: legacy filter 0x%08lX/0x%08lX applied as std-id %03lX mask %03lX",
			 bus, can_cfg[bus].filter, can_cfg[bus].mask, fcfg.id, fcfg.mask);

	esp_err_t err = twai_node_config_mask_filter(can_node[bus], 0, &fcfg);
#if HW_HAS_MCP2515
	// The MCP2515 has two independent RX filter groups and an unconfigured
	// group defaults to accept-all (RXM1 = 0), which would bypass group 0
	// entirely--mirror the filter into group 1 so it actually takes effect.
	if (err == ESP_OK && bus == CAN_BUS_1)
	{
		err = twai_node_config_mask_filter(can_node[bus], 1, &fcfg);
	}
#endif
	if (err != ESP_OK)
	{
		ESP_LOGE(TAG, "bus %d: filter config failed: %s", bus, esp_err_to_name(err));
	}
}

#if HW_HAS_MCP2515

// One-time bring-up shared by every node create/delete cycle: the SPI bus,
// the RST pin state, and the GPIO ISR service all outlive the node
// (twai_node_delete removes only the SPI device and the INT handler).
// Ordering per the reference firmware: RST high -> spi_bus_initialize ->
// gpio_install_isr_service -> twai_new_node_mcp2515.
static esp_err_t can_mcp2515_bus_init(void)
{
	static bool bus_ready = false;

	if (bus_ready)
	{
		return ESP_OK;
	}

	// RST is active-low: hold HIGH for normal run
	gpio_config_t rst_cfg = {
		.pin_bit_mask = BIT64(MCP2515_RST_GPIO_NUM),
		.mode = GPIO_MODE_OUTPUT,
		.pull_up_en = GPIO_PULLUP_DISABLE,
		.pull_down_en = GPIO_PULLDOWN_DISABLE,
		.intr_type = GPIO_INTR_DISABLE,
	};
	esp_err_t err = gpio_config(&rst_cfg);
	if (err != ESP_OK)
	{
		return err;
	}
	gpio_set_level(MCP2515_RST_GPIO_NUM, 1);

	spi_bus_config_t bus_cfg = {
		.sclk_io_num = MCP2515_SCLK_GPIO_NUM,
		.mosi_io_num = MCP2515_MOSI_GPIO_NUM,
		.miso_io_num = MCP2515_MISO_GPIO_NUM,
		.quadwp_io_num = GPIO_NUM_NC,
		.quadhd_io_num = GPIO_NUM_NC,
	};
	err = spi_bus_initialize(MCP2515_SPI_HOST, &bus_cfg, SPI_DMA_CH_AUTO);
	if (err != ESP_OK)
	{
		return err;
	}

	err = gpio_install_isr_service(0);
	if (err != ESP_OK && err != ESP_ERR_INVALID_STATE)	// INVALID_STATE = already installed
	{
		return err;
	}

	bus_ready = true;
	return ESP_OK;
}

static esp_err_t can_bus1_create_node(void)
{
	esp_err_t err = can_mcp2515_bus_init();
	if (err != ESP_OK)
	{
		return err;
	}

	twai_mcp2515_node_config_t config = {
		.io_cfg = {
			.int_gpio = MCP2515_INT_GPIO_NUM,
			.cs_gpio = MCP2515_CS_GPIO_NUM,
		},
		.spi_clock_hz = MCP2515_SPI_CLOCK_HZ,
		.oscillator_hz = MCP2515_OSCILLATOR_HZ,
		.bit_timing = {
			.bitrate = can_bitrate_bps[can_cfg[CAN_BUS_1].rate],
			.sp_permill = CAN_SAMPLE_POINT_PERMILL,
		},
		// MCP2515 hardware supports exactly -1 (auto retry) / 0 (one-shot)
		.fail_retry_cnt = can_cfg[CAN_BUS_1].auto_tx ? -1 : 0,
		.timestamp_resolution_hz = 0,
		.tx_queue_depth = CAN_TX_SLOT_COUNT,
		.flags = {
			// MCP2515 loopback is internal (no ACK needed)
			.enable_loopback = can_cfg[CAN_BUS_1].loopback ? 1 : 0,
			.enable_listen_only = can_cfg[CAN_BUS_1].silent ? 1 : 0,
		},
	};

	return twai_new_node_mcp2515(MCP2515_SPI_HOST, &config, &can_node[CAN_BUS_1]);
}
#endif

// Build the bus-0 on-chip node from the stored per-bus config. Mode flags
// (silent/loopback) are creation-time-only in the node API--that's why
// enable/disable is node create/delete instead of a persistent node.
static esp_err_t can_bus0_create_node(void)
{
	twai_onchip_node_config_t config = {
		.io_cfg = {
			.tx = TX_GPIO_NUM,
			.rx = RX_GPIO_NUM,
			.quanta_clk_out = GPIO_NUM_NC,
			.bus_off_indicator = GPIO_NUM_NC,
		},
		.bit_timing = {
			.bitrate = can_bitrate_bps[can_cfg[CAN_BUS_0].rate],
			.sp_permill = CAN_SAMPLE_POINT_PERMILL,
		},
		.fail_retry_cnt = can_cfg[CAN_BUS_0].auto_tx ? -1 : 0,	// -1 = retry forever
		// == slot pool size, so a held tx_slot_sem count implies queue space
		.tx_queue_depth = CAN_TX_SLOT_COUNT,
		.intr_priority = 0,
		.flags = {
			.enable_listen_only = can_cfg[CAN_BUS_0].silent ? 1 : 0,
			// self_test (no ACK needed) so loopback works partner-free
			.enable_loopback = can_cfg[CAN_BUS_0].loopback ? 1 : 0,
			.enable_self_test = can_cfg[CAN_BUS_0].loopback ? 1 : 0,
		},
	};

	return twai_new_node_onchip(&config, &can_node[CAN_BUS_0]);
}

// Bring a bus up from its stored config: create node -> filter -> callbacks
// -> enable -> publish the enable bit.
void can_enable(can_bus_t bus)
{
	// s_can_event_group is NULL only before can_init() (config server
	// starts earlier in app_main and could call in during that window)
	if (bus >= CAN_BUS_COUNT || s_can_event_group == NULL)
	{
		return;
	}
	// node_lock synchronizes the enable sequence with
	// can_send()/can_disable() touching the node handle.
	xSemaphoreTake(node_lock[bus], portMAX_DELAY);

	if (can_cfg[bus].bus_state == ON_BUS)
	{
		// Bus is already on
		xSemaphoreGive(node_lock[bus]);
		return;
	}

	esp_err_t err;
	if (bus == CAN_BUS_0)
	{
		err = can_bus0_create_node();
	}
	else
	{
#if HW_HAS_MCP2515
		err = can_bus1_create_node();
#else
		err = ESP_ERR_NOT_SUPPORTED;
#endif
	}

	if (err != ESP_OK)
	{
		ESP_LOGE(TAG, "bus %d: node create failed: %s", bus, esp_err_to_name(err));
		can_node[bus] = NULL;
		xSemaphoreGive(node_lock[bus]);
		return;
	}

	// Filter config is only legal while the node is stopped, so it must
	// happen here, before twai_node_enable()
	can_apply_filter(bus);

	// Register the ISR callbacks with the bus id as their user_ctx
	twai_event_callbacks_t cbs = {
		.on_rx_done = can_on_rx_done,
		.on_tx_done = can_on_tx_done,
		.on_state_change = can_on_state_change,
	};
	err = twai_node_register_event_callbacks(can_node[bus], &cbs, (void *)(uintptr_t)bus);
	if (err == ESP_OK)
	{
		can_tx_slots_reset(bus);	// fresh node: all slots are free
		err = twai_node_enable(can_node[bus]);
	}
	if (err != ESP_OK)
	{
		// Don't panic: bus 1 comes up at every boot by default, and a wedged
		// MCP2515 (created OK over SPI but refusing to enable) must degrade
		// to a dead bus, not a boot loop.
		ESP_LOGE(TAG, "bus %d: node enable failed: %s", bus, esp_err_to_name(err));
		twai_node_delete(can_node[bus]);
		can_node[bus] = NULL;
		xSemaphoreGive(node_lock[bus]);
		return;
	}

	// Clear the queue on enable. The queue is shared, so this also drops
	// any pending frames of the other bus. This can happen at boot, or at
	// runtime during bus recovery. In-flight frames on the other bus will be
	// lost. TODO(ejones): maybe eventually we can split into one rx queue per bus?
	xQueueReset(can_rx_queue);

	can_cfg[bus].bus_state = ON_BUS;
#ifdef CAN_STDBY_GPIO_NUM
	if (bus == CAN_BUS_0)
	{
		gpio_set_level(CAN_STDBY_GPIO_NUM, 0);	// transceiver out of standby
	}
#endif
	// Publish "bus up" last: unparks can_receive(), opens can_send()'s gate
	xEventGroupSetBits(s_can_event_group, CAN_ENABLE_BIT(bus));

	xSemaphoreGive(node_lock[bus]);
}

// Tear a bus down: stops the interrupt sources (no callbacks after this), then
// delete frees the node.
void can_disable(can_bus_t bus)
{
	if (bus >= CAN_BUS_COUNT || s_can_event_group == NULL)
	{
		return;
	}

	// The node is deleted with node_lock held so a concurrent can_send() can never touch a freed handle
	xSemaphoreTake(node_lock[bus], portMAX_DELAY);

	if (can_cfg[bus].bus_state != ON_BUS)
	{
		// Bus is already off
		xSemaphoreGive(node_lock[bus]);
		return;
	}
	// The enable bit goes first so new senders fail fast instead of piling up on the lock.
	xEventGroupClearBits(s_can_event_group, CAN_ENABLE_BIT(bus));
#ifdef CAN_STDBY_GPIO_NUM
	if (bus == CAN_BUS_0)
	{
		gpio_set_level(CAN_STDBY_GPIO_NUM, 1);	// transceiver into standby
	}
#endif

	twai_node_disable(can_node[bus]);
	twai_node_delete(can_node[bus]);
	can_node[bus] = NULL;
	// Frames still queued in the driver are dropped since on_tx_done is no
	// longer registered, so reset the tx slots
	can_tx_slots_reset(bus);
	can_cfg[bus].bus_state = OFF_BUS;

	xSemaphoreGive(node_lock[bus]);
}

// Config setters. As in the legacy driver, they are no-ops while the bus is
// ON: values are latched into the node at the next can_enable(). Callers
// that reconfigure at runtime (slcan/gvret/elm327) always disable first.

void can_set_silent(can_bus_t bus, uint8_t flag)
{
	if (bus >= CAN_BUS_COUNT || can_cfg[bus].bus_state == ON_BUS)
	{
		return;
	}
	can_cfg[bus].silent = flag;
}

void can_set_loopback(can_bus_t bus, uint8_t flag)
{
	if (bus >= CAN_BUS_COUNT || can_cfg[bus].bus_state == ON_BUS)
	{
		return;
	}
	can_cfg[bus].loopback = flag;
}

uint8_t can_is_silent(can_bus_t bus)
{
	if (bus >= CAN_BUS_COUNT)
	{
		return 0;
	}
	return can_cfg[bus].silent;
}

void can_set_auto_retransmit(can_bus_t bus, uint8_t flag)
{
	if (bus >= CAN_BUS_COUNT || can_cfg[bus].bus_state == ON_BUS)
	{
		return;
	}
#if HW_HAS_MCP2515
	// One-shot mode (auto_tx = 0) is unusable on the MCP2515: the driver
	// only completes a frame (on_tx_done + start of the next queued TX) on
	// TX0IF, which a failed one-shot transmit never raises (only MERRF), so
	// every failure would leak a TX slot until the pool starves and bus-1 TX
	// stalls for good. Keep retry-forever; nothing calls this today anyway.
	if (bus == CAN_BUS_1 && flag == 0)
	{
		ESP_LOGW(TAG, "bus %d: one-shot TX unsupported on MCP2515, keeping auto-retransmit", bus);
		return;
	}
#endif
	can_cfg[bus].auto_tx = flag;
}

void can_set_filter(can_bus_t bus, uint32_t f)
{
	if (bus >= CAN_BUS_COUNT || can_cfg[bus].bus_state == ON_BUS)
	{
		return;
	}
	can_cfg[bus].filter = f;
}

void can_set_mask(can_bus_t bus, uint32_t m)
{
	if (bus >= CAN_BUS_COUNT || can_cfg[bus].bus_state == ON_BUS)
	{
		return;
	}
	can_cfg[bus].mask = m;
}

void can_set_bitrate(can_bus_t bus, uint8_t rate)
{
	if (bus >= CAN_BUS_COUNT || can_cfg[bus].bus_state == ON_BUS)
	{
		return;
	}
	if (rate >= CAN_AUTO)	// auto-baud not supported; also guards table bounds
	{
		rate = CAN_500K;
	}
	can_cfg[bus].rate = rate;
}

uint8_t can_get_bitrate(can_bus_t bus)
{
	if (bus >= CAN_BUS_COUNT)
	{
		return CAN_500K;
	}
	return can_cfg[bus].rate;
}

// One-time OS-resource setup; touches no hardware (per-bus bring-up is
// can_enable()). Idempotent so a stray second call can't leak the handles.
void can_init(void)
{
	if (s_can_event_group != NULL)
	{
		return;
	}

	s_can_event_group = xEventGroupCreate();
	// Deeper than the legacy driver's 100: at 500 kbit/s a saturated bus
	// delivers ~4 frames/ms, so 256 rides out multi-ms scheduling stalls of
	// can_rx_task under TCP streaming load instead of silently dropping RX.
	can_rx_queue = xQueueCreate(256, sizeof(can_rx_item_t));
	xTaskCreate(can_recovery_task, "can_recovery", 3072, NULL, 6, &can_recovery_task_handle);

	for (int bus = 0; bus < CAN_BUS_COUNT; bus++)
	{
		node_lock[bus] = xSemaphoreCreateMutex();
		tx_slot_sem[bus] = xSemaphoreCreateCounting(CAN_TX_SLOT_COUNT, CAN_TX_SLOT_COUNT);
		can_cfg[bus] = (can_cfg_t){
			.bus_state = OFF_BUS,
			.auto_tx = 1,
			.rate = CAN_500K,
			.filter = 0,
			.mask = 0xFFFFFFFF,
		};
	}
}

// Pop the next frame (any bus, global arrival order) from the shared queue;
// *bus reports where it came from.
esp_err_t can_receive(twai_message_t *message, can_bus_t *bus, TickType_t ticks_to_wait)
{
	if (s_can_event_group == NULL)
	{
		return ESP_ERR_INVALID_STATE;
	}

	// Park until at least one bus is enabled
	xEventGroupWaitBits(s_can_event_group,
						CAN_ENABLE_BIT_ANY,
						pdFALSE,	// don't consume the bits: they're state, not events
						pdFALSE,	// wait-any: one live bus is enough
						portMAX_DELAY);

	// Log rx drops, max 1 log/s/bus
	for (int b = 0; b < CAN_BUS_COUNT; b++)
	{
		uint32_t dropped = rx_drop_count[b];
		if (dropped != rx_drop_logged[b])
		{
			int64_t now = esp_timer_get_time();
			if (now - rx_drop_log_us[b] > 1000000)
			{
				rx_drop_log_us[b] = now;
				rx_drop_logged[b] = dropped;
				ESP_LOGW(TAG, "bus %d: rx queue full, %lu frames dropped total", b, dropped);
			}
		}
	}

	// The caller's timeout applies to the actual wait-for-data
	can_rx_item_t item;
	if (xQueueReceive(can_rx_queue, &item, ticks_to_wait) != pdTRUE)
	{
		return ESP_ERR_TIMEOUT;
	}

	*message = item.msg;
	if (bus != NULL)	// bus tag is optional
	{
		*bus = item.bus;
	}
	return ESP_OK;
}

// Queue a frame for transmission. Fire-and-forget: the message is copied
// into a pool slot the driver transmits from later, so ticks_to_wait is
// spent waiting for a free slot--not for the frame to reach the wire.
esp_err_t can_send(can_bus_t bus, twai_message_t *message, TickType_t ticks_to_wait)
{
	if (bus >= CAN_BUS_COUNT || s_can_event_group == NULL)
	{
		return ESP_ERR_INVALID_STATE;
	}

	// Advisory fast-fail so "bus not open" returns instantly without
	// blocking (gvret/slcan test for exactly this error); the
	// authoritative re-check happens under node_lock below
	if (!(xEventGroupGetBits(s_can_event_group) & CAN_ENABLE_BIT(bus)))
	{
		tx_drop_count[bus]++;
		int64_t now = esp_timer_get_time();
		if (now - tx_drop_log_us[bus] > 1000000)
		{
			tx_drop_log_us[bus] = now;
			ESP_LOGW(TAG, "bus %d: down (disabled), %lu frames dropped total",
					 bus, tx_drop_count[bus]);
		}
		return ESP_ERR_INVALID_STATE;
	}

	// Claim a TX slot; this is where the caller's ticks_to_wait is spent.
	// Ordering matters: never hold node_lock while blocking here, or a
	// full TX queue could stall can_disable() indefinitely.
	if (xSemaphoreTake(tx_slot_sem[bus], ticks_to_wait) != pdTRUE)
	{
		tx_drop_count[bus]++;
		int64_t now = esp_timer_get_time();
		if (now - tx_drop_log_us[bus] > 1000000)
		{
			tx_drop_log_us[bus] = now;
			ESP_LOGW(TAG, "bus %d: TX queue full, %lu frames dropped total", bus, tx_drop_count[bus]);
		}
		return ESP_ERR_TIMEOUT;
	}

	// Synchronize against enable/disable deleting the node out from under
	// us. Safe to wait unbounded: every holder keeps this lock only for
	// short non-blocking sections.
	xSemaphoreTake(node_lock[bus], portMAX_DELAY);

	if (can_cfg[bus].bus_state != ON_BUS || can_node[bus] == NULL)
	{
		xSemaphoreGive(node_lock[bus]);
		xSemaphoreGive(tx_slot_sem[bus]);
		return ESP_ERR_INVALID_STATE;
	}

	// Semaphore normally guarantees a free slot exists; find it. Completion
	// order can diverge from allocation order when a transmit fails, hence
	// the bitmask instead of a ring index. The pool can still come up empty
	// if can_tx_slots_reset() refilled the semaphore while we held a permit
	// waiting on node_lock (bus recreated under us) -- drop, don't assert.
	can_tx_slot_t *slot = NULL;
	portENTER_CRITICAL(&tx_slot_mux);
	for (int i = 0; i < CAN_TX_SLOT_COUNT; i++)
	{
		if (!(tx_slot_used[bus] & BIT(i)))
		{
			tx_slot_used[bus] |= BIT(i);
			slot = &tx_slot[bus][i];
			break;
		}
	}
	portEXIT_CRITICAL(&tx_slot_mux);
	if (slot == NULL)
	{
		xSemaphoreGive(node_lock[bus]);
		xSemaphoreGive(tx_slot_sem[bus]);	// no-op if reset already refilled it
		return ESP_ERR_TIMEOUT;
	}

	// Copy legacy twai_message_t -> the slot's stable twai_frame_t + data
	// (the driver only keeps a pointer, hence the copy into pool storage).
	// RTR frames carry no payload: buffer_len must be 0 or the driver
	// rejects the dlc/len mismatch. ss/self have no node-API equivalent.
	uint8_t dlc = (message->data_length_code > TWAI_FRAME_MAX_DLC)
					  ? TWAI_FRAME_MAX_DLC : message->data_length_code;
	memset(&slot->frame, 0, sizeof(slot->frame));
	slot->frame.header.id = message->identifier;
	slot->frame.header.dlc = dlc;
	slot->frame.header.ide = message->extd;
	slot->frame.header.rtr = message->rtr;
	memcpy(slot->data, message->data, dlc);
	slot->frame.buffer = slot->data;
	slot->frame.buffer_len = message->rtr ? 0 : dlc;

	// Timeout 0: the slot semaphore already did the waiting (queue space is
	// guaranteed), and staying non-blocking keeps the node_lock hold short
	esp_err_t err = twai_node_transmit(can_node[bus], &slot->frame, 0);

	xSemaphoreGive(node_lock[bus]);

	if (err != ESP_OK)
	{
		// Frame never reached the driver: put the slot straight back
		portENTER_CRITICAL(&tx_slot_mux);
		tx_slot_used[bus] &= ~BIT(slot - &tx_slot[bus][0]);
		portEXIT_CRITICAL(&tx_slot_mux);
		xSemaphoreGive(tx_slot_sem[bus]);
		// Callers test for ESP_ERR_INVALID_STATE (legacy "bus not enabled")
		if (err != ESP_ERR_INVALID_STATE && err != ESP_ERR_TIMEOUT)
		{
			ESP_LOGE(TAG, "bus %d: transmit failed: %s", bus, esp_err_to_name(err));
		}
	}
	return err;
}

bool can_is_enabled(can_bus_t bus)
{
	if (bus >= CAN_BUS_COUNT)
	{
		return false;
	}
	return can_cfg[bus].bus_state == ON_BUS;
}

// True if any bus is up--one atomic read instead of a per-bus loop
bool can_any_enabled(void)
{
	if (s_can_event_group == NULL)
	{
		return false;
	}
	return (xEventGroupGetBits(s_can_event_group) & CAN_ENABLE_BIT_ANY) != 0;
}

// Drops pending frames from ALL buses (the queue is shared); fine for its
// one caller (elm327 clearing stale frames before a request on bus 0)
void can_flush_rx(void)
{
	if (can_rx_queue != NULL)
	{
		xQueueReset(can_rx_queue);
	}
}
