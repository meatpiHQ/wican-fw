/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <sys/cdefs.h>
#include <sys/param.h>
#include <stdatomic.h>
#include <assert.h>
#include <string.h>
#include "esp_check.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/idf_additions.h"
#include "freertos/event_groups.h"
#include "esp_twai.h"
#include "esp_twai_mcp2515.h"
#include "esp_private/twai_interface.h"
#include "mcp2515_regdefs.h"

#define TAG "mcp2515"

#define TWAI_MCP2515_MODE_TIMEOUT_US      100000
#define TWAI_MCP2515_IDLE_EVENT_BIT       BIT0
#define TWAI_MCP2515_DEFAULT_OSC_HZ       8000000
#define TWAI_MCP2515_SPI_MAX_HZ           10000000
#define MCP2515_SPI_CMD_OVERHEAD_BYTES    2
#define MCP2515_MAX_BURST_REG_BYTES       13  /* SIDH..D7 block: ID(4) + DLC(1) + DATA(8) */
#define MCP2515_SPI_BURST_BUF_BYTES       (MCP2515_SPI_CMD_OVERHEAD_BYTES + MCP2515_MAX_BURST_REG_BYTES)

#define MCP2515_CANINTE_ENABLE_MASK       (MCP2515_INT_RX0IF | MCP2515_INT_RX1IF | MCP2515_INT_TX0IF | MCP2515_INT_ERRIF | MCP2515_INT_MERRF)

typedef struct {
    struct twai_node_base api_base;
    spi_device_handle_t spi_dev;
    gpio_num_t int_gpio;
    uint32_t timestamp_resolution_hz;
    twai_event_callbacks_t cbs;
    void *user_data;
    QueueHandle_t tx_queue;
    EventGroupHandle_t event_group;
    const twai_frame_t *p_curr_tx;

    _Atomic twai_error_state_t state;
    twai_node_record_t history;
    atomic_bool hw_busy;
    atomic_bool rx_isr;
    atomic_bool rx_pending;
    twai_frame_t rx_cache;
    uint8_t rx_data[TWAI_FRAME_MAX_LEN];

    struct {
        uint32_t enable_loopback: 1;
        uint32_t enable_listen_only: 1;
    } flags;
} twai_mcp2515_ctx_t;

static inline uint64_t mcp2515_time_us_to_timestamp(uint64_t time_us, uint32_t resolution)
{
    if (resolution > 1000000) {
        return time_us * (resolution / 1000000);
    } else if (resolution > 0) {
        return time_us / (1000000 / resolution);
    }
    return 0;
}

static void mcp2515_encode_id(const twai_frame_header_t *hdr, uint8_t id_buf[4])
{
    if (hdr->ide) {
        uint32_t id = hdr->id & TWAI_EXT_ID_MASK;
        id_buf[0] = (id >> 21) & 0xFF;
        id_buf[1] = ((id >> 16) & 0x03) | MCP2515_SIDL_EXIDE | ((id >> 13) & 0xE0);
        id_buf[2] = (id >> 8) & 0xFF;
        id_buf[3] = id & 0xFF;
    } else {
        uint32_t id = hdr->id & TWAI_STD_ID_MASK;
        id_buf[0] = (id >> 3) & 0xFF;
        id_buf[1] = (id & 0x07) << 5;
        id_buf[2] = 0;
        id_buf[3] = 0;
    }
}

static uint32_t mcp2515_decode_id(const uint8_t id_buf[4], bool *is_ext)
{
    bool ext = !!(id_buf[1] & MCP2515_SIDL_EXIDE);
    uint32_t id;
    if (ext) {
        id = ((uint32_t)id_buf[0] << 21) |
             ((uint32_t)(id_buf[1] & MCP2515_SIDL_SID_MASK) << 13) |
             ((uint32_t)(id_buf[1] & MCP2515_SIDL_EID_HIGH_MASK) << 16) |
             ((uint32_t)id_buf[2] << 8) |
             id_buf[3];
        id &= TWAI_EXT_ID_MASK;
    } else {
        id = ((uint32_t)id_buf[0] << 3) | ((id_buf[1] >> 5) & 0x07);
        id &= TWAI_STD_ID_MASK;
    }
    *is_ext = ext;
    return id;
}

static esp_err_t mcp2515_spi_transfer(twai_mcp2515_ctx_t *ctx, const uint8_t *tx, uint8_t *rx, size_t len)
{
    spi_transaction_t t = {
        .length = len * 8,
        .tx_buffer = tx,
        .rx_buffer = rx,
    };
    return spi_device_polling_transmit(ctx->spi_dev, &t);
}

static esp_err_t mcp2515_write_reg(twai_mcp2515_ctx_t *ctx, uint8_t reg, uint8_t val)
{
    uint8_t tx[3] = {MCP2515_CMD_WRITE, reg, val};
    return mcp2515_spi_transfer(ctx, tx, NULL, sizeof(tx));
}

// The MCP2515 supports auto-increment of register address after each byte written, so we can write multiple bytes in one transaction.
static esp_err_t mcp2515_write_regs(twai_mcp2515_ctx_t *ctx, uint8_t start_reg, const uint8_t *data, size_t len)
{
    uint8_t tx[MCP2515_SPI_BURST_BUF_BYTES] = { MCP2515_CMD_WRITE, start_reg };
    if (len > MCP2515_MAX_BURST_REG_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }
    memcpy(tx + MCP2515_SPI_CMD_OVERHEAD_BYTES, data, len);
    return mcp2515_spi_transfer(ctx, tx, NULL, len + MCP2515_SPI_CMD_OVERHEAD_BYTES);
}

static esp_err_t mcp2515_read_reg(twai_mcp2515_ctx_t *ctx, uint8_t reg, uint8_t *val)
{
    uint8_t tx[3] = {MCP2515_CMD_READ, reg, 0};
    uint8_t rx[3] = {};
    ESP_RETURN_ON_ERROR(mcp2515_spi_transfer(ctx, tx, rx, sizeof(tx)), TAG, "spi read reg failed");
    *val = rx[2];
    return ESP_OK;
}

static esp_err_t mcp2515_read_regs(twai_mcp2515_ctx_t *ctx, uint8_t start_reg, uint8_t *data, size_t len)
{
    uint8_t tx[MCP2515_SPI_BURST_BUF_BYTES] = { MCP2515_CMD_READ, start_reg };
    uint8_t rx[MCP2515_SPI_BURST_BUF_BYTES] = {};
    if (len > MCP2515_MAX_BURST_REG_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }
    ESP_RETURN_ON_ERROR(mcp2515_spi_transfer(ctx, tx, rx, len + MCP2515_SPI_CMD_OVERHEAD_BYTES), TAG, "spi read regs failed");
    memcpy(data, rx + MCP2515_SPI_CMD_OVERHEAD_BYTES, len);
    return ESP_OK;
}

static esp_err_t mcp2515_bit_modify(twai_mcp2515_ctx_t *ctx, uint8_t reg, uint8_t mask, uint8_t data)
{
    uint8_t tx[4] = {MCP2515_CMD_BIT_MODIFY, reg, mask, data};
    return mcp2515_spi_transfer(ctx, tx, NULL, sizeof(tx));
}

static esp_err_t mcp2515_reset_chip(twai_mcp2515_ctx_t *ctx)
{
    uint8_t tx = MCP2515_CMD_RESET;
    ESP_RETURN_ON_ERROR(mcp2515_spi_transfer(ctx, &tx, NULL, 1), TAG, "mcp2515 reset failed");
    esp_rom_delay_us(20);
    return ESP_OK;
}

static esp_err_t mcp2515_set_mode(twai_mcp2515_ctx_t *ctx, uint8_t mode)
{
    ESP_RETURN_ON_ERROR(mcp2515_bit_modify(ctx, MCP2515_REG_CANCTRL, MCP2515_CANCTRL_REQOP_MASK, mode), TAG, "set mode failed");
    uint64_t start = esp_timer_get_time();
    while ((esp_timer_get_time() - start) < TWAI_MCP2515_MODE_TIMEOUT_US) {
        uint8_t canstat = 0;
        ESP_RETURN_ON_ERROR(mcp2515_read_reg(ctx, MCP2515_REG_CANSTAT, &canstat), TAG, "read canstat failed");
        if ((canstat & MCP2515_CANSTAT_OPMOD_MASK) == mode) {
            return ESP_OK;
        }
    }
    return ESP_ERR_TIMEOUT;
}

static twai_error_state_t mcp2515_get_state_from_eflg(uint8_t eflg)
{
    if (eflg & MCP2515_EFLG_TXBO) {
        return TWAI_ERROR_BUS_OFF;
    }
    if (eflg & (MCP2515_EFLG_TXEP | MCP2515_EFLG_RXEP)) {
        return TWAI_ERROR_PASSIVE;
    }
    if (eflg & (MCP2515_EFLG_EWARN | MCP2515_EFLG_TXWAR | MCP2515_EFLG_RXWAR)) {
        return TWAI_ERROR_WARNING;
    }
    return TWAI_ERROR_ACTIVE;
}

static twai_error_flags_t mcp2515_make_err_flags(uint8_t eflg, uint8_t txbctrl)
{
    (void)eflg;
    /* MCP2515 does not expose reliable protocol-level error subtype bits
     * (ACK/Form/Stuff/Bit) like on-chip TWAI controllers. TXERR only indicates
     * a transmit bus error occurred, so mapping it directly to bit_err is
     * misleading. Keep only flags that are unambiguous from MCP2515 status.
     */
    twai_error_flags_t flags = {};
    flags.arb_lost = !!(txbctrl & MCP2515_TXBCTRL_MLOA);
    return flags;
}

static esp_err_t mcp2515_calc_timing(uint32_t osc_hz, const twai_timing_basic_config_t *timing, uint8_t *cnf1, uint8_t *cnf2, uint8_t *cnf3)
{
    ESP_RETURN_ON_FALSE(timing && timing->bitrate, ESP_ERR_INVALID_ARG, TAG, "classic timing config is required");
    uint32_t total_div = osc_hz / (2 * timing->bitrate);
    if (total_div == 0) {
        return ESP_ERR_INVALID_ARG;
    }

    uint32_t best_brp = 0;
    uint32_t best_tq = 0;
    for (uint32_t brp = 1; brp <= 64; brp++) {
        uint32_t tq = total_div / brp;
        if ((tq * brp) != total_div) {
            continue;
        }
        /*
         * MCP2515 timing allows a wider minimum TQ range by register limits,
         * but this driver intentionally keeps tq >= 8 as a conservative policy
         * to avoid very coarse timing granularity and poor sample-point
         * placement at low TQ values. tq <= 25 is the MCP2515 hardware limit.
         */
        if (tq >= 8 && tq <= 25) {
            best_brp = brp;
            best_tq = tq;
            break;
        }
    }
    ESP_RETURN_ON_FALSE(best_brp, ESP_ERR_INVALID_ARG, TAG, "bitrate can't achieve");

    uint32_t sample_point = timing->sp_permill ? timing->sp_permill : 800;
    uint32_t tseg1 = (best_tq * sample_point) / 1000 - 1;
    if (tseg1 < 2) {
        tseg1 = 2;
    }
    if (tseg1 > 16) {
        tseg1 = 16;
    }

    uint32_t tseg2 = best_tq - tseg1 - 1;
    if (tseg2 < 2) {
        tseg2 = 2;
        tseg1 = best_tq - tseg2 - 1;
    }
    if (tseg2 > 8) {
        tseg2 = 8;
        tseg1 = best_tq - tseg2 - 1;
    }
    ESP_RETURN_ON_FALSE((tseg1 >= 2) && (tseg1 <= 16), ESP_ERR_INVALID_ARG, TAG, "invalid timing split");

    uint32_t phseg1 = MIN(8, tseg1 - 1);
    uint32_t prop = tseg1 - phseg1;
    ESP_RETURN_ON_FALSE((prop >= 1) && (prop <= 8), ESP_ERR_INVALID_ARG, TAG, "invalid propseg");
    ESP_RETURN_ON_FALSE((phseg1 >= 1) && (phseg1 <= 8), ESP_ERR_INVALID_ARG, TAG, "invalid phseg1");

    uint32_t sjw = (tseg2 > 4) ? 4 : tseg2;
    if (sjw < 1) {
        sjw = 1;
    }

    *cnf1 = ((sjw - 1) << 6) | (best_brp - 1);
    *cnf2 = MCP2515_CNF2_BTLMODE | ((phseg1 - 1) << 3) | (prop - 1);
    /* twai_timing_basic_config_t::ssp_permill is reused as an intent signal
     * to enable MCP2515 triple sampling (SAM), because MCP2515 has no SSP
     * position register.
     */
    if (timing->ssp_permill != 0) {
        *cnf2 |= MCP2515_CNF2_SAM;
    }
    *cnf3 = (tseg2 - 1);
    return ESP_OK;
}

static esp_err_t mcp2515_load_tx_frame(twai_mcp2515_ctx_t *ctx, const twai_frame_t *frame)
{
    /*
     * Current TX policy:
     * - Only TXB0 is used for transmission.
     * - Frames are serialized by software queue order (FIFO).
     * - TX buffer priority bits (TXP[1:0]) are not configured.
     *
     * Future enhancement: use TXB1/TXB2 and map driver-level scheduling to
     * MCP2515 TX buffer priorities.
     */
    uint8_t txb0ctrl = 0;
    ESP_RETURN_ON_ERROR(mcp2515_read_reg(ctx, MCP2515_REG_TXB0CTRL, &txb0ctrl), TAG, "read txb0ctrl failed");
    assert((txb0ctrl & MCP2515_TXBCTRL_TXREQ) == 0);

    uint8_t tx_data[13] = {};
    mcp2515_encode_id(&frame->header, &tx_data[0]);
    uint8_t dlc = frame->header.dlc ? MIN(frame->header.dlc, TWAI_FRAME_MAX_DLC) : frame->buffer_len;
    tx_data[4] = (frame->header.rtr ? MCP2515_DLC_RTR : 0) | (dlc & MCP2515_DLC_LEN_MASK);
    if (frame->buffer_len) {
        memcpy(&tx_data[5], frame->buffer, frame->buffer_len);
    }
    ESP_RETURN_ON_ERROR(mcp2515_write_regs(ctx, MCP2515_REG_TXB0SIDH, tx_data, sizeof(tx_data)), TAG, "write tx frame failed");
    uint8_t rts = MCP2515_CMD_RTS_TXB0;
    ESP_RETURN_ON_ERROR(mcp2515_spi_transfer(ctx, &rts, NULL, 1), TAG, "request-to-send failed");
    return ESP_OK;
}

static bool mcp2515_tx_is_success(twai_mcp2515_ctx_t *ctx)
{
    uint8_t txb0ctrl = 0;
    if (mcp2515_read_reg(ctx, MCP2515_REG_TXB0CTRL, &txb0ctrl) != ESP_OK) {
        return false;
    }
    return (txb0ctrl & (MCP2515_TXBCTRL_ABTF | MCP2515_TXBCTRL_MLOA | MCP2515_TXBCTRL_TXERR)) == 0;
}

static esp_err_t mcp2515_start_next_tx_from_queue(twai_mcp2515_ctx_t *ctx, BaseType_t *do_yield)
{
    if (xQueueReceiveFromISR(ctx->tx_queue, &ctx->p_curr_tx, do_yield)) {
        atomic_store(&ctx->hw_busy, true);
        return mcp2515_load_tx_frame(ctx, ctx->p_curr_tx);
    }
    atomic_store(&ctx->hw_busy, false);
    xEventGroupSetBitsFromISR(ctx->event_group, TWAI_MCP2515_IDLE_EVENT_BIT, do_yield);
    return ESP_OK;
}

static esp_err_t mcp2515_start_next_tx_from_queue_task(twai_mcp2515_ctx_t *ctx)
{
    if (xQueueReceive(ctx->tx_queue, &ctx->p_curr_tx, 0) == pdTRUE) {
        atomic_store(&ctx->hw_busy, true);
        return mcp2515_load_tx_frame(ctx, ctx->p_curr_tx);
    }
    atomic_store(&ctx->hw_busy, false);
    xEventGroupSetBits(ctx->event_group, TWAI_MCP2515_IDLE_EVENT_BIT);
    return ESP_OK;
}

static void IRAM_ATTR mcp2515_gpio_isr(void *arg)
{
    twai_mcp2515_ctx_t *ctx = (twai_mcp2515_ctx_t *)arg;
    BaseType_t do_yield = pdFALSE;

    do {
        uint8_t canintf = 0;
        uint8_t eflg = 0;
        if (mcp2515_read_reg(ctx, MCP2515_REG_CANINTF, &canintf) != ESP_OK) {
            break;
        }
        if (canintf == 0) {
            break;
        }

        if (canintf & (MCP2515_INT_ERRIF | MCP2515_INT_MERRF)) {
            if (mcp2515_read_reg(ctx, MCP2515_REG_EFLG, &eflg) == ESP_OK) {
                if (eflg & (MCP2515_EFLG_RX0OVR | MCP2515_EFLG_RX1OVR)) {
                    mcp2515_bit_modify(ctx, MCP2515_REG_EFLG, MCP2515_EFLG_RX0OVR | MCP2515_EFLG_RX1OVR, 0);
                }
                twai_error_state_t old_sta = atomic_load(&ctx->state);
                twai_error_state_t new_sta = mcp2515_get_state_from_eflg(eflg);
                if (old_sta != new_sta) {
                    twai_state_change_event_data_t state_edata = {
                        .old_sta = old_sta,
                        .new_sta = new_sta,
                    };
                    atomic_store(&ctx->state, new_sta);
                    if (ctx->cbs.on_state_change) {
                        do_yield |= ctx->cbs.on_state_change(&ctx->api_base, &state_edata, ctx->user_data);
                    }
                }

                if (ctx->cbs.on_error) {
                    uint8_t txb0ctrl = 0;
                    mcp2515_read_reg(ctx, MCP2515_REG_TXB0CTRL, &txb0ctrl);
                    twai_error_event_data_t err_edata = {
                        .err_flags = mcp2515_make_err_flags(eflg, txb0ctrl),
                    };
                    ctx->history.bus_err_num++;
                    do_yield |= ctx->cbs.on_error(&ctx->api_base, &err_edata, ctx->user_data);
                }
            }
            mcp2515_bit_modify(ctx, MCP2515_REG_CANINTF, MCP2515_INT_ERRIF | MCP2515_INT_MERRF, 0);
        }

        if (canintf & MCP2515_INT_RX0IF) {
            uint8_t rx_buf[13] = {};
            if (mcp2515_read_regs(ctx, MCP2515_REG_RXB0SIDH, rx_buf, sizeof(rx_buf)) == ESP_OK) {
                bool is_ext = false;
                uint8_t dlc = rx_buf[4] & MCP2515_DLC_LEN_MASK;
                if (dlc > TWAI_FRAME_MAX_LEN) {
                    dlc = TWAI_FRAME_MAX_LEN;
                }
                memset(&ctx->rx_cache, 0, sizeof(ctx->rx_cache));
                ctx->rx_cache.header.id = mcp2515_decode_id(&rx_buf[0], &is_ext);
                ctx->rx_cache.header.ide = is_ext;
                ctx->rx_cache.header.rtr = is_ext ? !!(rx_buf[4] & MCP2515_DLC_RTR) : !!(rx_buf[1] & MCP2515_SIDL_SRR);
                ctx->rx_cache.header.dlc = dlc;
                ctx->rx_cache.header.timestamp = mcp2515_time_us_to_timestamp(esp_timer_get_time(), ctx->timestamp_resolution_hz);
                ctx->rx_cache.buffer = ctx->rx_data;
                ctx->rx_cache.buffer_len = dlc;
                if (dlc) {
                    memcpy(ctx->rx_data, &rx_buf[5], dlc);
                }
                atomic_store(&ctx->rx_pending, true);

                if (ctx->cbs.on_rx_done) {
                    twai_rx_done_event_data_t rx_edata = {};
                    atomic_store(&ctx->rx_isr, true);
                    do_yield |= ctx->cbs.on_rx_done(&ctx->api_base, &rx_edata, ctx->user_data);
                    atomic_store(&ctx->rx_isr, false);
                }
            }
            mcp2515_bit_modify(ctx, MCP2515_REG_CANINTF, MCP2515_INT_RX0IF, 0);
        }

        if (canintf & MCP2515_INT_RX1IF) {
            uint8_t rx_buf[13] = {};
            if (mcp2515_read_regs(ctx, MCP2515_REG_RXB1SIDH, rx_buf, sizeof(rx_buf)) == ESP_OK) {
                bool is_ext = false;
                uint8_t dlc = rx_buf[4] & MCP2515_DLC_LEN_MASK;
                if (dlc > TWAI_FRAME_MAX_LEN) {
                    dlc = TWAI_FRAME_MAX_LEN;
                }
                memset(&ctx->rx_cache, 0, sizeof(ctx->rx_cache));
                ctx->rx_cache.header.id = mcp2515_decode_id(&rx_buf[0], &is_ext);
                ctx->rx_cache.header.ide = is_ext;
                ctx->rx_cache.header.rtr = is_ext ? !!(rx_buf[4] & MCP2515_DLC_RTR) : !!(rx_buf[1] & MCP2515_SIDL_SRR);
                ctx->rx_cache.header.dlc = dlc;
                ctx->rx_cache.header.timestamp = mcp2515_time_us_to_timestamp(esp_timer_get_time(), ctx->timestamp_resolution_hz);
                ctx->rx_cache.buffer = ctx->rx_data;
                ctx->rx_cache.buffer_len = dlc;
                if (dlc) {
                    memcpy(ctx->rx_data, &rx_buf[5], dlc);
                }
                atomic_store(&ctx->rx_pending, true);

                if (ctx->cbs.on_rx_done) {
                    twai_rx_done_event_data_t rx_edata = {};
                    atomic_store(&ctx->rx_isr, true);
                    do_yield |= ctx->cbs.on_rx_done(&ctx->api_base, &rx_edata, ctx->user_data);
                    atomic_store(&ctx->rx_isr, false);
                }
            }
            mcp2515_bit_modify(ctx, MCP2515_REG_CANINTF, MCP2515_INT_RX1IF, 0);
        }

        /* TX done path currently handles TXB0 only. */
        if (canintf & MCP2515_INT_TX0IF) {
            if (ctx->cbs.on_tx_done) {
                twai_tx_done_event_data_t tx_edata = {
                    .is_tx_success = mcp2515_tx_is_success(ctx),
                    .done_tx_frame = ctx->p_curr_tx,
                };
                do_yield |= ctx->cbs.on_tx_done(&ctx->api_base, &tx_edata, ctx->user_data);
            }
            mcp2515_bit_modify(ctx, MCP2515_REG_CANINTF, MCP2515_INT_TX0IF, 0);
            mcp2515_start_next_tx_from_queue(ctx, &do_yield);
        }
    } while (gpio_get_level(ctx->int_gpio) == 0);

    if (do_yield) {
        portYIELD_FROM_ISR();
    }
}

static esp_err_t mcp2515_node_enable(twai_node_handle_t node)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    ESP_RETURN_ON_FALSE(atomic_load(&ctx->state) == TWAI_ERROR_BUS_OFF, ESP_ERR_INVALID_STATE, TAG, "node already enabled");

    /* Reliability-oriented semantics:
     * disable() pauses transmission but keeps queued frames.
     * enable() resumes normal operation and immediately restarts TX if pending
     * frames exist in software queue or one frame is already staged.
     */

    uint8_t mode = MCP2515_CANCTRL_MODE_NORMAL;
    if (ctx->flags.enable_loopback) {
        mode = MCP2515_CANCTRL_MODE_LOOPBACK;
    } else if (ctx->flags.enable_listen_only) {
        mode = MCP2515_CANCTRL_MODE_LISTEN_ONLY;
    }

    ESP_RETURN_ON_ERROR(mcp2515_set_mode(ctx, mode), TAG, "enable mode switch failed");
    ESP_RETURN_ON_ERROR(mcp2515_write_reg(ctx, MCP2515_REG_CANINTE, MCP2515_CANINTE_ENABLE_MASK), TAG, "enable interrupts failed");
    atomic_store(&ctx->state, TWAI_ERROR_ACTIVE);
    ESP_RETURN_ON_ERROR(gpio_intr_enable(ctx->int_gpio), TAG, "enable gpio interrupt failed");

    if (!atomic_load(&ctx->hw_busy)) {
        esp_err_t ret = mcp2515_start_next_tx_from_queue_task(ctx);
        if (ret != ESP_OK) {
            atomic_store(&ctx->hw_busy, false);
            return ret;
        }
    }
    return ESP_OK;
}

static esp_err_t mcp2515_node_disable(twai_node_handle_t node)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    ESP_RETURN_ON_FALSE(atomic_load(&ctx->state) != TWAI_ERROR_BUS_OFF, ESP_ERR_INVALID_STATE, TAG, "node already disabled");

    /* Keep tx_queue contents across disable/enable to favor reliable delivery.
     * Pending frames can resume transmission after the next enable().
     */

    gpio_intr_disable(ctx->int_gpio);
    ESP_RETURN_ON_ERROR(mcp2515_write_reg(ctx, MCP2515_REG_CANINTE, 0), TAG, "disable interrupts failed");
    ESP_RETURN_ON_ERROR(mcp2515_set_mode(ctx, MCP2515_CANCTRL_MODE_CONFIG), TAG, "set config mode failed");
    /* When disabling, drop current in-flight frame state and keep only queued
     * not-yet-started frames for resume-after-enable semantics.
     */
    ESP_RETURN_ON_ERROR(mcp2515_bit_modify(ctx, MCP2515_REG_TXB0CTRL, MCP2515_TXBCTRL_TXREQ, 0), TAG, "clear txreq failed");
    ESP_RETURN_ON_ERROR(mcp2515_bit_modify(ctx, MCP2515_REG_CANINTF, MCP2515_CANINTE_ENABLE_MASK, 0), TAG, "clear canintf failed");
    ctx->p_curr_tx = NULL;
    atomic_store(&ctx->hw_busy, false);
    atomic_store(&ctx->state, TWAI_ERROR_BUS_OFF);
    return ESP_OK;
}

static esp_err_t mcp2515_node_delete(twai_node_handle_t node)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    ESP_RETURN_ON_FALSE(atomic_load(&ctx->state) == TWAI_ERROR_BUS_OFF, ESP_ERR_INVALID_STATE, TAG, "delete node must when node stopped");

    gpio_isr_handler_remove(ctx->int_gpio);
    gpio_intr_disable(ctx->int_gpio);
    if (ctx->spi_dev) {
        spi_bus_remove_device(ctx->spi_dev);
    }
    if (ctx->tx_queue) {
        vQueueDeleteWithCaps(ctx->tx_queue);
    }
    if (ctx->event_group) {
        vEventGroupDeleteWithCaps(ctx->event_group);
    }
    free(ctx);
    return ESP_OK;
}

static esp_err_t mcp2515_node_register_callbacks(twai_node_handle_t node, const twai_event_callbacks_t *cbs, void *user_data)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    ESP_RETURN_ON_FALSE(atomic_load(&ctx->state) == TWAI_ERROR_BUS_OFF, ESP_ERR_INVALID_STATE, TAG, "register callback must when node stopped");
    memcpy(&ctx->cbs, cbs, sizeof(twai_event_callbacks_t));
    ctx->user_data = user_data;
    return ESP_OK;
}

static esp_err_t mcp2515_node_set_timing(twai_node_handle_t node, const twai_timing_advanced_config_t *bit_timing, const twai_timing_advanced_config_t *data_timing)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    ESP_RETURN_ON_FALSE(bit_timing && !data_timing, ESP_ERR_NOT_SUPPORTED, TAG, "FD timing is not supported");
    ESP_RETURN_ON_FALSE(atomic_load(&ctx->state) == TWAI_ERROR_BUS_OFF, ESP_ERR_INVALID_STATE, TAG, "config timing must when node stopped");

    uint8_t canstat = 0;
    ESP_RETURN_ON_ERROR(mcp2515_read_reg(ctx, MCP2515_REG_CANSTAT, &canstat), TAG, "read canstat failed");
    ESP_RETURN_ON_FALSE((canstat & MCP2515_CANSTAT_OPMOD_MASK) == MCP2515_CANCTRL_MODE_CONFIG,
                        ESP_ERR_INVALID_STATE, TAG, "timing config requires configuration mode");

    uint8_t cnf1 = (bit_timing->sjw - 1) << 6;
    cnf1 |= (bit_timing->brp > 0) ? (bit_timing->brp - 1) : 0;
    uint8_t cnf2 = MCP2515_CNF2_BTLMODE | ((bit_timing->tseg_1 - 1) << 3) | (bit_timing->prop_seg - 1);
    /* twai_timing_advanced_config_t::ssp_offset is reused as an intent signal
     * to enable MCP2515 triple sampling (SAM), because MCP2515 has no SSP
     * offset register.
     */
    if (bit_timing->ssp_offset != 0) {
        cnf2 |= MCP2515_CNF2_SAM;
    }
    uint8_t cnf3 = (bit_timing->tseg_2 - 1);
    ESP_RETURN_ON_ERROR(mcp2515_write_reg(ctx, MCP2515_REG_CNF1, cnf1), TAG, "write cnf1 failed");
    ESP_RETURN_ON_ERROR(mcp2515_write_reg(ctx, MCP2515_REG_CNF2, cnf2), TAG, "write cnf2 failed");
    ESP_RETURN_ON_ERROR(mcp2515_write_reg(ctx, MCP2515_REG_CNF3, cnf3), TAG, "write cnf3 failed");
    return ESP_OK;
}

static esp_err_t mcp2515_node_config_mask_filter(twai_node_handle_t node, uint8_t filter_id, const twai_mask_filter_config_t *mask_cfg)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    /*
     * Current filter implementation programs only identifier mask/filter fields
     * (SID/EID path).
     *
     * MCP2515 also supports data-byte filtering for the first two bytes of
     * standard data frames. This driver does not configure that feature yet.
     * Future extension can expose it via TWAI filter config and map it to the
     * corresponding RX filter register fields.
     */
    ESP_RETURN_ON_FALSE(mask_cfg, ESP_ERR_INVALID_ARG, TAG, "invalid argument: null");
    ESP_RETURN_ON_FALSE(filter_id < 2, ESP_ERR_INVALID_ARG, TAG, "Invalid mask filter id %d", filter_id);
    ESP_RETURN_ON_FALSE((!mask_cfg->no_classic) && (mask_cfg->no_fd), ESP_ERR_NOT_SUPPORTED, TAG, "MCP2515 only supports classic CAN");
    ESP_RETURN_ON_FALSE(!mask_cfg->dual_filter, ESP_ERR_NOT_SUPPORTED, TAG, "dual filter is not supported");
    ESP_RETURN_ON_FALSE(atomic_load(&ctx->state) == TWAI_ERROR_BUS_OFF, ESP_ERR_INVALID_STATE, TAG, "filter config must do when node stopped");

    uint8_t canstat = 0;
    ESP_RETURN_ON_ERROR(mcp2515_read_reg(ctx, MCP2515_REG_CANSTAT, &canstat), TAG, "read canstat failed");
    ESP_RETURN_ON_FALSE((canstat & MCP2515_CANSTAT_OPMOD_MASK) == MCP2515_CANCTRL_MODE_CONFIG,
                        ESP_ERR_INVALID_STATE, TAG, "filter config requires configuration mode");

    uint32_t num_ids = mask_cfg->num_of_ids ? mask_cfg->num_of_ids : 1;
    ESP_RETURN_ON_FALSE(num_ids > 0, ESP_ERR_INVALID_ARG, TAG, "invalid num_of_ids");
    if (mask_cfg->num_of_ids) {
        ESP_RETURN_ON_FALSE(mask_cfg->id_list, ESP_ERR_INVALID_ARG, TAG, "id_list is null");
    }

    uint32_t max_ids_in_group = (filter_id == 0) ? 2 : 4;
    ESP_RETURN_ON_FALSE(num_ids <= max_ids_in_group, ESP_ERR_INVALID_ARG, TAG, "num_of_ids exceeds filter capacity");

    uint8_t mask_buf[4] = {};
    uint32_t mask = mask_cfg->mask;

    twai_frame_header_t hdr = {
        .ide = mask_cfg->is_ext,
    };
    hdr.id = mask;
    mcp2515_encode_id(&hdr, mask_buf);

    static const uint8_t s_filter_group0_regs[] = {
        MCP2515_REG_RXF0SIDH,
        MCP2515_REG_RXF1SIDH,
    };
    static const uint8_t s_filter_group1_regs[] = {
        MCP2515_REG_RXF2SIDH,
        MCP2515_REG_RXF3SIDH,
        MCP2515_REG_RXF4SIDH,
        MCP2515_REG_RXF5SIDH,
    };
    /* Map generic mask-filter units to MCP2515 groups:
     *  - filter_id 0 -> RXM0 + {RXF0, RXF1}
     *  - filter_id 1 -> RXM1 + {RXF2, RXF3, RXF4, RXF5}
     */
    const uint8_t *group_regs = (filter_id == 0) ? s_filter_group0_regs : s_filter_group1_regs;
    uint8_t rxm_reg = (filter_id == 0) ? MCP2515_REG_RXM0SIDH : MCP2515_REG_RXM1SIDH;

    for (uint32_t i = 0; i < num_ids; i++) {
        uint8_t id_buf[4] = {};
        hdr.id = mask_cfg->num_of_ids ? mask_cfg->id_list[i] : mask_cfg->id;
        mcp2515_encode_id(&hdr, id_buf);
        uint8_t rxf_reg = group_regs[i];
        ESP_RETURN_ON_ERROR(mcp2515_write_regs(ctx, rxf_reg, id_buf, sizeof(id_buf)), TAG, "write filter failed");
    }
    ESP_RETURN_ON_ERROR(mcp2515_write_regs(ctx, rxm_reg, mask_buf, sizeof(mask_buf)), TAG, "write mask failed");
    return ESP_OK;
}

static esp_err_t mcp2515_node_recover(twai_node_handle_t node)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    ESP_RETURN_ON_FALSE(atomic_load(&ctx->state) == TWAI_ERROR_BUS_OFF, ESP_ERR_INVALID_STATE, TAG, "node not in bus off");

    /* Keep reported state as BUS_OFF until hardware indicates recovery via EFLG/TXBO clear interrupt. */
    ESP_RETURN_ON_ERROR(mcp2515_set_mode(ctx, MCP2515_CANCTRL_MODE_NORMAL), TAG, "recover failed");
    return ESP_OK;
}

static esp_err_t mcp2515_node_get_info(twai_node_handle_t node, twai_node_status_t *status_ret, twai_node_record_t *record_ret)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    if (status_ret) {
        uint8_t tec = 0;
        uint8_t rec = 0;
        mcp2515_read_reg(ctx, MCP2515_REG_TEC, &tec);
        mcp2515_read_reg(ctx, MCP2515_REG_REC, &rec);
        status_ret->state = atomic_load(&ctx->state);
        status_ret->tx_error_count = tec;
        status_ret->rx_error_count = rec;
        status_ret->tx_queue_remaining = uxQueueSpacesAvailable(ctx->tx_queue);
    }
    if (record_ret) {
        *record_ret = ctx->history;
    }
    return ESP_OK;
}

static esp_err_t mcp2515_node_transmit(twai_node_handle_t node, const twai_frame_t *frame, int timeout)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    ESP_RETURN_ON_FALSE_ISR(!frame->header.fdf && !frame->header.brs && !frame->header.esi, ESP_ERR_NOT_SUPPORTED, TAG, "CAN FD fields not supported");
    ESP_RETURN_ON_FALSE_ISR(frame->buffer_len <= TWAI_FRAME_MAX_LEN, ESP_ERR_INVALID_ARG, TAG, "invalid frame length");
    ESP_RETURN_ON_FALSE_ISR(frame->header.dlc <= TWAI_FRAME_MAX_DLC, ESP_ERR_INVALID_ARG, TAG, "invalid frame dlc");
    ESP_RETURN_ON_FALSE_ISR(atomic_load(&ctx->state) != TWAI_ERROR_BUS_OFF, ESP_ERR_INVALID_STATE, TAG, "node is bus off");
    ESP_RETURN_ON_FALSE_ISR(!ctx->flags.enable_listen_only, ESP_ERR_NOT_SUPPORTED, TAG, "node is config as listen only");

    TickType_t ticks_to_wait = (timeout == -1) ? portMAX_DELAY : pdMS_TO_TICKS(timeout);
    xEventGroupClearBits(ctx->event_group, TWAI_MCP2515_IDLE_EVENT_BIT);
    bool false_var = false;
    if (atomic_compare_exchange_strong(&ctx->hw_busy, &false_var, true)) {
        ctx->p_curr_tx = frame;
        ESP_RETURN_ON_ERROR(mcp2515_load_tx_frame(ctx, frame), TAG, "load tx frame failed");
    } else {
        BaseType_t is_isr_context = xPortInIsrContext();
        BaseType_t yield_required = pdFALSE;
        if (is_isr_context) {
            ESP_RETURN_ON_FALSE_ISR(xQueueSendFromISR(ctx->tx_queue, &frame, &yield_required), ESP_ERR_TIMEOUT, TAG, "tx queue full");
        } else {
            ESP_RETURN_ON_FALSE(xQueueSend(ctx->tx_queue, &frame, ticks_to_wait), ESP_ERR_TIMEOUT, TAG, "tx queue full");
        }
        if (is_isr_context && yield_required) {
            portYIELD_FROM_ISR();
        }
    }
    return ESP_OK;
}

static esp_err_t mcp2515_node_wait_tx_done(twai_node_handle_t node, int timeout)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    ESP_RETURN_ON_FALSE(atomic_load(&ctx->state) != TWAI_ERROR_BUS_OFF, ESP_ERR_INVALID_STATE, TAG, "node is bus off");
    TickType_t ticks_to_wait = (timeout == -1) ? portMAX_DELAY : pdMS_TO_TICKS(timeout);
    if (atomic_load(&ctx->hw_busy) || uxQueueMessagesWaiting(ctx->tx_queue)) {
        if (TWAI_MCP2515_IDLE_EVENT_BIT != xEventGroupWaitBits(ctx->event_group, TWAI_MCP2515_IDLE_EVENT_BIT, pdFALSE, pdFALSE, ticks_to_wait)) {
            return ESP_ERR_TIMEOUT;
        }
    }
    return ESP_OK;
}

static esp_err_t mcp2515_node_receive_from_isr(twai_node_handle_t node, twai_frame_t *rx_frame)
{
    twai_mcp2515_ctx_t *ctx = __containerof(node, twai_mcp2515_ctx_t, api_base);
    ESP_RETURN_ON_FALSE_ISR(atomic_load(&ctx->rx_isr), ESP_ERR_INVALID_STATE, TAG, "rx can only called in `rx_done` callback");
    ESP_RETURN_ON_FALSE_ISR(atomic_load(&ctx->rx_pending), ESP_ERR_INVALID_STATE, TAG, "no pending rx frame");
    size_t copy_len = MIN(rx_frame->buffer_len, ctx->rx_cache.buffer_len);
    if (copy_len && rx_frame->buffer) {
        memcpy(rx_frame->buffer, ctx->rx_data, copy_len);
    }
    rx_frame->header = ctx->rx_cache.header;
    rx_frame->buffer_len = copy_len;
    atomic_store(&ctx->rx_pending, false);
    return ESP_OK;
}

esp_err_t twai_new_node_mcp2515(twai_mcp2515_spi_bus_handle_t bus, const twai_mcp2515_node_config_t *node_config, twai_node_handle_t *node_ret)
{
    esp_err_t ret = ESP_OK;
    ESP_RETURN_ON_FALSE(node_config && node_ret, ESP_ERR_INVALID_ARG, TAG, "invalid argument");
    ESP_RETURN_ON_FALSE(GPIO_IS_VALID_GPIO(node_config->io_cfg.int_gpio), ESP_ERR_INVALID_ARG, TAG, "invalid int gpio");
    ESP_RETURN_ON_FALSE(GPIO_IS_VALID_OUTPUT_GPIO(node_config->io_cfg.cs_gpio), ESP_ERR_INVALID_ARG, TAG, "invalid cs gpio");
    ESP_RETURN_ON_FALSE((node_config->spi_clock_hz > 0) && (node_config->spi_clock_hz <= TWAI_MCP2515_SPI_MAX_HZ), ESP_ERR_INVALID_ARG, TAG, "spi_clock_hz out of range (max 10MHz)");
    ESP_RETURN_ON_FALSE((node_config->tx_queue_depth > 0) || node_config->flags.enable_listen_only, ESP_ERR_INVALID_ARG, TAG, "tx_queue_depth at least 1");
    ESP_RETURN_ON_FALSE((node_config->fail_retry_cnt == -1) || (node_config->fail_retry_cnt == 0), ESP_ERR_NOT_SUPPORTED, TAG, "MCP2515 only supports fail_retry_cnt -1/0");

    bool one_shot = (node_config->fail_retry_cnt == 0);
    twai_mcp2515_ctx_t *ctx = heap_caps_calloc(1, sizeof(twai_mcp2515_ctx_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    ESP_RETURN_ON_FALSE(ctx, ESP_ERR_NO_MEM, TAG, "no mem");

    ctx->timestamp_resolution_hz = node_config->timestamp_resolution_hz;
    ctx->int_gpio = node_config->io_cfg.int_gpio;
    ctx->flags.enable_loopback = node_config->flags.enable_loopback;
    ctx->flags.enable_listen_only = node_config->flags.enable_listen_only;
    atomic_store(&ctx->state, TWAI_ERROR_BUS_OFF);

    uint32_t tx_queue_depth = node_config->tx_queue_depth ? node_config->tx_queue_depth : 1;
    ctx->tx_queue = xQueueCreateWithCaps(tx_queue_depth, sizeof(twai_frame_t *), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    ctx->event_group = xEventGroupCreateWithCaps(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    ESP_GOTO_ON_FALSE(ctx->tx_queue && ctx->event_group, ESP_ERR_NO_MEM, err, TAG, "no_mem");

    // Configure SPI device for the node
    spi_device_interface_config_t devcfg = {
        .flags = 0,
        .clock_speed_hz = node_config->spi_clock_hz,
        .mode = 0,
        .spics_io_num = node_config->io_cfg.cs_gpio,
        .queue_size = 1, // we use the SPI polling API, so transaction queue size is always 1
    };
    ESP_GOTO_ON_ERROR(spi_bus_add_device(bus, &devcfg, &ctx->spi_dev), err, TAG, "adding spi device to bus failed");

    gpio_config_t int_cfg = {
        .pin_bit_mask = BIT64(node_config->io_cfg.int_gpio),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = 1,
        .pull_down_en = 0,
        .intr_type = GPIO_INTR_NEGEDGE,
    };
    ESP_GOTO_ON_ERROR(gpio_config(&int_cfg), err, TAG, "configure int gpio failed");
    ESP_GOTO_ON_ERROR(gpio_isr_handler_add(node_config->io_cfg.int_gpio, mcp2515_gpio_isr, ctx), err, TAG, "add gpio isr handler failed");
    ESP_GOTO_ON_ERROR(gpio_intr_disable(node_config->io_cfg.int_gpio), err, TAG, "disable int gpio failed");

    ESP_GOTO_ON_ERROR(mcp2515_reset_chip(ctx), err, TAG, "reset chip failed");
    ESP_GOTO_ON_ERROR(mcp2515_set_mode(ctx, MCP2515_CANCTRL_MODE_CONFIG), err, TAG, "switch to config mode failed");

    uint8_t cnf1 = 0;
    uint8_t cnf2 = 0;
    uint8_t cnf3 = 0;
    uint32_t osc_hz = node_config->oscillator_hz ? node_config->oscillator_hz : TWAI_MCP2515_DEFAULT_OSC_HZ;
    ESP_GOTO_ON_ERROR(mcp2515_calc_timing(osc_hz, &node_config->bit_timing, &cnf1, &cnf2, &cnf3), err, TAG, "calc timing failed");
    ESP_GOTO_ON_ERROR(mcp2515_write_reg(ctx, MCP2515_REG_CNF1, cnf1), err, TAG, "write cnf1 failed");
    ESP_GOTO_ON_ERROR(mcp2515_write_reg(ctx, MCP2515_REG_CNF2, cnf2), err, TAG, "write cnf2 failed");
    ESP_GOTO_ON_ERROR(mcp2515_write_reg(ctx, MCP2515_REG_CNF3, cnf3), err, TAG, "write cnf3 failed");
    /* Keep CLKOUT disabled by default. */
    ESP_GOTO_ON_ERROR(mcp2515_bit_modify(ctx, MCP2515_REG_CANCTRL, MCP2515_CANCTRL_CLKEN, 0), err, TAG, "disable clkout failed");

    if (one_shot) {
        ESP_GOTO_ON_ERROR(mcp2515_bit_modify(ctx, MCP2515_REG_CANCTRL, MCP2515_CANCTRL_OSM, MCP2515_CANCTRL_OSM), err, TAG, "set one shot mode failed");
    }

    ctx->api_base.enable = mcp2515_node_enable;
    ctx->api_base.disable = mcp2515_node_disable;
    ctx->api_base.del = mcp2515_node_delete;
    ctx->api_base.config_mask_filter = mcp2515_node_config_mask_filter;
    ctx->api_base.reconfig_timing = mcp2515_node_set_timing;
    ctx->api_base.transmit = mcp2515_node_transmit;
    ctx->api_base.transmit_wait_done = mcp2515_node_wait_tx_done;
    ctx->api_base.receive_isr = mcp2515_node_receive_from_isr;
    ctx->api_base.recover = mcp2515_node_recover;
    ctx->api_base.register_cbs = mcp2515_node_register_callbacks;
    ctx->api_base.get_info = mcp2515_node_get_info;

    *node_ret = &ctx->api_base;
    return ESP_OK;

err:
    if (ctx) {
        if (ctx->int_gpio >= 0) {
            gpio_isr_handler_remove(ctx->int_gpio);
        }
        if (ctx->spi_dev) {
            spi_bus_remove_device(ctx->spi_dev);
        }
        if (ctx->tx_queue) {
            vQueueDeleteWithCaps(ctx->tx_queue);
        }
        if (ctx->event_group) {
            vEventGroupDeleteWithCaps(ctx->event_group);
        }
        free(ctx);
    }
    return ret;
}
