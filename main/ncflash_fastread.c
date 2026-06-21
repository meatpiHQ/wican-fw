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
#define FRAME_TIMEOUT_MS 100 /* per-frame N_Cr-style timeout over local CAN */
#define BLOCK_RETRIES 3      /* idempotent reads: re-request a lost block */
#define UDS_RMBA 0x23        /* ReadMemoryByAddress */
#define UDS_RMBA_POS 0x63    /* positive response SID */

/* Reassembly + streaming scratch (single fast-read runs at a time, in the
 * can_tx_task context, so static is safe and keeps it off the task stack). */
static uint8_t s_resp[READ_BLOCK + 8];
static xdev_buffer s_out;

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
    if (recv_matching(&rx, FRAME_TIMEOUT_MS) != 0) return -1;

    uint8_t pci = rx.data[0] >> 4;
    int total;
    int idx;

    if (pci == 0x0) /* Single Frame (short response, e.g. a 7F NRC) */
    {
        total = rx.data[0] & 0x0F;
        if (total < 1 || total > 7) return -1;
        memcpy(s_resp, &rx.data[1], total);
        *out_len = total;
        return 0;
    }
    if (pci != 0x1) return -1; /* expected First Frame */

    total = ((rx.data[0] & 0x0F) << 8) | rx.data[1];
    if (total < 8 || total > (int)sizeof(s_resp)) return -1;
    memcpy(s_resp, &rx.data[2], 6);
    idx = 6;

    /* Flow Control: CTS, BS=0 (send all), STmin=0 — we drain at CAN speed. */
    uint8_t fc[8] = {0x30, 0x00, 0x00, 0, 0, 0, 0, 0};
    if (send_frame(fc) != 0) return -1;

    uint8_t expected_seq = 1;
    while (idx < total)
    {
        if (recv_matching(&rx, FRAME_TIMEOUT_MS) != 0) return -1;
        if ((rx.data[0] >> 4) != 0x2) return -1;          /* not a CF */
        if ((rx.data[0] & 0x0F) != expected_seq) return -1; /* sequence gap */
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

    ESP_LOGI(TAG, "fast read start=0x%06lX len=0x%06lX",
             (unsigned long)start, (unsigned long)length);

    /* Take exclusive ownership of the CAN bus: pause the frame-forwarding task
     * so it cannot consume the ECU's responses, then flush stale RX frames. */
    TaskHandle_t rx_task = xTaskGetHandle("can_rx_task");
    if (rx_task) vTaskSuspend(rx_task);
    twai_message_t junk;
    while (can_receive(&junk, 0) == ESP_OK) { /* drain */ }

    int rc = 0;
    uint32_t addr = start;
    uint32_t remaining = length;
    int64_t t0 = esp_timer_get_time();
    int yield_ctr = 0;

    while (remaining > 0)
    {
        uint16_t blk = (remaining > READ_BLOCK) ? READ_BLOCK : (uint16_t)remaining;
        int ok = 0;
        for (int attempt = 0; attempt < BLOCK_RETRIES && !ok; attempt++)
        {
            int rlen = 0;
            if (read_one_block(addr, blk, &rlen) != 0) continue;
            /* Must be a positive RMBA response carrying the full block. */
            if (rlen < 1 + blk || s_resp[0] != UDS_RMBA_POS) continue;
            ok = 1;
            s_out.usLen = blk;
            s_out.dev_channel = DEV_WIFI;
            memcpy(s_out.ucElement, &s_resp[1], blk);
            xQueueSend(*tx_queue, &s_out, portMAX_DELAY);
        }
        if (!ok)
        {
            ESP_LOGE(TAG, "block 0x%06lX failed after %d tries; aborting",
                     (unsigned long)addr, BLOCK_RETRIES);
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
