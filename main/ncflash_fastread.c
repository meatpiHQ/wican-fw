/*
 * NC Flash fast ROM read — autonomous in-firmware ISO-TP read loop.
 * See ncflash_fastread.h for the rationale and the wire protocol.
 */

#include "ncflash_fastread.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/twai.h"
#include "esp_timer.h"
#include "esp_log.h"

#include "can.h"
#include "types.h"

#define TAG "ncflash_fastread"

#define TESTER_ID 0x7E0u /* UDS tester request CAN id */
#define ECU_ID 0x7E8u    /* NC ECU response CAN id */
#define READ_BLOCK 0x400 /* ECU hard-caps ReadMemoryByAddress at 0x400 */
#define FRAME_TIMEOUT_MS 100 /* per-Consecutive-Frame timeout over local CAN */
#define RESP_FIRST_TIMEOUT_MS 250 /* first response frame (ECU compute latency) */
#define RESP_PENDING_TIMEOUT_MS 600 /* wait for the real answer after a 0x78 */
#define MAX_PENDING 16       /* cap consecutive response-pending NRCs per block */
#define BLOCK_RETRIES 4      /* idempotent reads: re-request a lost block */
#define UDS_RMBA 0x23        /* ReadMemoryByAddress */
#define UDS_RMBA_POS 0x63    /* positive response SID */
#define UDS_NRC 0x7F             /* negative response SID */
#define NRC_RESPONSE_PENDING 0x78 /* requestCorrectlyReceived-ResponsePending */

/* Reassembly + streaming scratch (single fast-read runs at a time, in the
 * can_tx_task context, so static is safe and keeps it off the task stack). */
static uint8_t s_resp[READ_BLOCK + 8];
static xdev_buffer s_out;

/* Failure diagnostics: read_one_block records WHY/WHERE it gave up here, and the
 * abort path streams it into the TCP stream so the host can see what the ECU
 * actually sent (no UART/USB on the bench). Stage codes:
 *   1 no response to request (first recv timed out)
 *   2 too many response-pending NRCs
 *   3 unexpected PCI (not SF/FF) for first frame
 *   4 First Frame length out of range
 *   5 Flow Control send failed
 *   6 Consecutive Frame recv timed out
 *   7 not a Consecutive Frame
 *   8 CF sequence gap
 *   9 short SF / non-0x63 content (caller-side check) */
static int s_diag_stage;
static int s_diag_pending;
static uint8_t s_diag_frame[8];
static int s_diag_have_frame;

static void diag_set(int stage, const twai_message_t *rx, int pending)
{
    s_diag_stage = stage;
    s_diag_pending = pending;
    if (rx)
    {
        memcpy(s_diag_frame, rx->data, 8);
        s_diag_have_frame = 1;
    }
    else
    {
        s_diag_have_frame = 0;
    }
}

static uint8_t hex_nibble(uint8_t c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return 0xFF;
}

/* Parse 8 hex chars at buf into *out. Returns 0 on success. */
static int parse_hex32(const uint8_t *buf, uint32_t *out)
{
    uint32_t v = 0;
    for (int i = 0; i < 8; i++)
    {
        uint8_t n = hex_nibble(buf[i]);
        if (n == 0xFF) return -1;
        v = (v << 4) | n;
    }
    *out = v;
    return 0;
}

int ncflash_is_fastread_cmd(const uint8_t *buf, int len)
{
    return (len >= NCFLASH_FASTREAD_CMD_LEN - 1 &&
            buf[0] == NCFLASH_FASTREAD_CMD);
}

/* Receive the next frame addressed to us (ECU_ID) within timeout_ms, ignoring
 * any cross-traffic. Returns 0 on success. */
static int recv_matching(twai_message_t *msg, int timeout_ms)
{
    int64_t deadline = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    while (esp_timer_get_time() < deadline)
    {
        int64_t rem_ms = (deadline - esp_timer_get_time()) / 1000;
        if (rem_ms < 1) rem_ms = 1;
        if (can_receive(msg, pdMS_TO_TICKS(rem_ms)) == ESP_OK)
        {
            if (msg->identifier == ECU_ID && msg->rtr == 0) return 0;
            /* not our conversation — keep waiting */
        }
    }
    return -1;
}

/* Send an 8-byte (padded) CAN frame from the tester id. */
static int send_frame(const uint8_t *data8)
{
    twai_message_t tx = {0};
    tx.identifier = TESTER_ID;
    tx.extd = 0;
    tx.data_length_code = 8;
    memcpy(tx.data, data8, 8);
    return (can_send(&tx, pdMS_TO_TICKS(FRAME_TIMEOUT_MS)) == ESP_OK) ? 0 : -1;
}

/* One ReadMemoryByAddress request + full ISO-TP reassembly of the response into
 * s_resp. On success *out_len is the reassembled payload length (>= 1: the
 * 0x63 SID followed by the data). Returns 0 on success. */
static int read_one_block(uint32_t addr, uint16_t size, int *out_len)
{
    /* ISO-TP Single Frame: PCI 0x07 (len 7) + RMBA + addr(4) + size(2). */
    uint8_t req[8] = {
        0x07, UDS_RMBA,
        (uint8_t)(addr >> 24), (uint8_t)(addr >> 16),
        (uint8_t)(addr >> 8), (uint8_t)(addr),
        (uint8_t)(size >> 8), (uint8_t)(size)};
    if (send_frame(req) != 0) return -1;

    twai_message_t rx;
    int total;
    int idx;

    /* Receive the first meaningful response frame. A read in a slow flash
     * region answers 7F 23 78 (requestCorrectlyReceived-ResponsePending) one or
     * more times before the real 0x63 response arrives ~200-300 ms later, so we
     * skip past every response-pending NRC (waiting the longer pending timeout)
     * until we see the actual answer. */
    int pending = 0;
    for (;;)
    {
        int to = pending ? RESP_PENDING_TIMEOUT_MS : RESP_FIRST_TIMEOUT_MS;
        if (recv_matching(&rx, to) != 0) { diag_set(1, NULL, pending); return -1; }

        uint8_t pci = rx.data[0] >> 4;
        if (pci == 0x0) /* Single Frame (short response, e.g. a 7F NRC) */
        {
            total = rx.data[0] & 0x0F;
            if (total < 1 || total > 7) { diag_set(3, &rx, pending); return -1; }
            /* response-pending (7F <sid> 78)? keep waiting for the real answer */
            if (total >= 3 && rx.data[1] == UDS_NRC &&
                rx.data[3] == NRC_RESPONSE_PENDING)
            {
                if (++pending > MAX_PENDING) { diag_set(2, &rx, pending); return -1; }
                continue;
            }
            memcpy(s_resp, &rx.data[1], total);
            *out_len = total;
            return 0;
        }
        if (pci != 0x1) { diag_set(3, &rx, pending); return -1; } /* want FF */
        break;                     /* real multi-frame response begins */
    }

    total = ((rx.data[0] & 0x0F) << 8) | rx.data[1];
    if (total < 8 || total > (int)sizeof(s_resp)) { diag_set(4, &rx, pending); return -1; }
    memcpy(s_resp, &rx.data[2], 6);
    idx = 6;

    /* Flow Control: CTS, BS=0 (send all), STmin=0 — we drain at CAN speed. */
    uint8_t fc[8] = {0x30, 0x00, 0x00, 0, 0, 0, 0, 0};
    if (send_frame(fc) != 0) { diag_set(5, NULL, pending); return -1; }

    uint8_t expected_seq = 1;
    while (idx < total)
    {
        if (recv_matching(&rx, FRAME_TIMEOUT_MS) != 0) { diag_set(6, NULL, pending); return -1; }
        if ((rx.data[0] >> 4) != 0x2) { diag_set(7, &rx, pending); return -1; }   /* not a CF */
        if ((rx.data[0] & 0x0F) != expected_seq) { diag_set(8, &rx, pending); return -1; } /* seq gap */
        int take = total - idx;
        if (take > 7) take = 7;
        memcpy(s_resp + idx, &rx.data[1], take);
        idx += take;
        expected_seq = (expected_seq + 1) & 0x0F;
    }
    *out_len = total;
    return 0;
}

int ncflash_fast_read(const uint8_t *buf, int len, QueueHandle_t *tx_queue)
{
    if (!ncflash_is_fastread_cmd(buf, len) ||
        len < NCFLASH_FASTREAD_CMD_LEN - 1)
        return -1;

    uint32_t start = 0, length = 0;
    if (parse_hex32(&buf[1], &start) != 0 ||
        parse_hex32(&buf[9], &length) != 0)
        return -1;
    if (length == 0) return -1;

    /* Version ping: stream a fixed build marker and return without touching CAN
     * so the host can confirm which fast-read firmware is actually live. */
    if (start == NCFLASH_FASTREAD_PING_ADDR)
    {
        static const char marker[] = NCFLASH_FASTREAD_VERSION;
        size_t mlen = sizeof(marker) - 1;
        s_out.usLen = (uint16_t)mlen;
        s_out.dev_channel = DEV_WIFI;
        memcpy(s_out.ucElement, marker, mlen);
        xQueueSend(*tx_queue, &s_out, portMAX_DELAY);
        ESP_LOGI(TAG, "fast read version ping -> %s", NCFLASH_FASTREAD_VERSION);
        return 0;
    }

    ESP_LOGI(TAG, "fast read start=0x%06lX len=0x%06lX",
             (unsigned long)start, (unsigned long)length);

    /* Take exclusive ownership of the CAN bus: pause the frame-forwarding task
     * so it cannot consume the ECU's responses, then flush stale RX frames. */
    TaskHandle_t rx_task = xTaskGetHandle("can_rx_task");
    if (rx_task) vTaskSuspend(rx_task);
    twai_message_t junk;
    while (can_receive(&junk, 0) == ESP_OK) { /* drain */ }

    /* Drop any CAN frames can_rx_task already queued for the host, then emit the
     * sync preamble. Frames still in the wifi TX task / TCP send buffer cannot
     * be flushed here, so the host resyncs on this marker rather than assuming a
     * clean stream. */
    xdev_buffer txjunk;
    while (xQueueReceive(*tx_queue, &txjunk, 0) == pdTRUE) { /* discard */ }
    {
        static const char sync[] = NCFLASH_FASTREAD_SYNC;
        s_out.usLen = (uint16_t)(sizeof(sync) - 1);
        s_out.dev_channel = DEV_WIFI;
        memcpy(s_out.ucElement, sync, sizeof(sync) - 1);
        xQueueSend(*tx_queue, &s_out, portMAX_DELAY);
    }

    int rc = 0;
    uint32_t addr = start;
    uint32_t remaining = length;
    int64_t t0 = esp_timer_get_time();
    int yield_ctr = 0;

    /* KNOWN LIMIT: a single very long stream degrades ~880 KB in (the WiFi/TCP
     * TX path stops draining xMsg_Tx_Queue under sustained load, so xQueueSend
     * below eventually blocks forever and the read appears to stall). Until that
     * device-side backpressure is fixed (see TX-queue work), the HOST splits a
     * full 1 MB read into ~128 KB commands (_FAST_READ_CHUNK in wican_transport.py),
     * each a fresh suspend/resume that resets this path. Keep both sides in sync. */
    while (remaining > 0)
    {
        uint16_t blk = (remaining > READ_BLOCK) ? READ_BLOCK : (uint16_t)remaining;
        int ok = 0;
        for (int attempt = 0; attempt < BLOCK_RETRIES && !ok; attempt++)
        {
            /* On a retry, drain any late/stale frames (e.g. a delayed answer to
             * the previous attempt) so they can't be mistaken for this block. */
            if (attempt > 0)
            {
                twai_message_t stale;
                while (can_receive(&stale, 0) == ESP_OK) { /* drain */ }
            }
            int rlen = 0;
            if (read_one_block(addr, blk, &rlen) != 0) continue;
            /* Must be a positive RMBA response carrying the full block. */
            if (rlen < 1 + blk || s_resp[0] != UDS_RMBA_POS)
            {
                s_diag_stage = 9;
                s_diag_pending = rlen; /* stage 9: pnd field carries rlen */
                memcpy(s_diag_frame, s_resp, 8);
                s_diag_have_frame = 1;
                continue;
            }
            ok = 1;
            s_out.usLen = blk;
            s_out.dev_channel = DEV_WIFI;
            memcpy(s_out.ucElement, &s_resp[1], blk);
            xQueueSend(*tx_queue, &s_out, portMAX_DELAY);
        }
        if (!ok)
        {
            ESP_LOGE(TAG, "block 0x%06lX failed after %d tries (st=%d); aborting",
                     (unsigned long)addr, BLOCK_RETRIES, s_diag_stage);
            /* Stream an ASCII diagnostic so the host can see the failure cause
             * even with no UART/USB on the bench (the host scans for "FRERR"). */
            char dg[96];
            int n = snprintf(
                dg, sizeof(dg),
                "\r\nFRERR a=%06lX st=%d pnd=%d hf=%d "
                "f=%02X%02X%02X%02X%02X%02X%02X%02X\r\n",
                (unsigned long)addr, s_diag_stage, s_diag_pending, s_diag_have_frame,
                s_diag_frame[0], s_diag_frame[1], s_diag_frame[2], s_diag_frame[3],
                s_diag_frame[4], s_diag_frame[5], s_diag_frame[6], s_diag_frame[7]);
            if (n > 0)
            {
                s_out.usLen = (uint16_t)n;
                s_out.dev_channel = DEV_WIFI;
                memcpy(s_out.ucElement, dg, (size_t)n);
                xQueueSend(*tx_queue, &s_out, portMAX_DELAY);
            }
            rc = -2; /* short stream -> NC Flash detects and falls back */
            break;
        }
        addr += blk;
        remaining -= blk;
        /* Yield periodically so the idle task / task-WDT is serviced during the
         * long CAN-bound loop (can_receive already blocks between frames). */
        if ((++yield_ctr & 0x1F) == 0) vTaskDelay(1);
    }

    if (rx_task) vTaskResume(rx_task);

    int64_t dt_ms = (esp_timer_get_time() - t0) / 1000;
    ESP_LOGI(TAG, "fast read done rc=%d, %lu bytes in %lld ms",
             rc, (unsigned long)(length - remaining), (long long)dt_ms);
    return rc;
}
