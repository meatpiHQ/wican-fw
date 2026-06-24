/*
 * NC Flash fast ROM WRITE — autonomous in-firmware SD-staged flash (Option B).
 * See ncflash_fastwrite.h for the rationale, wire protocol, and safety model.
 */

#include "ncflash_fastwrite.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/twai.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "cJSON.h"

#include "can.h"
#include "types.h"
#include "sdcard.h"

#define TAG "ncflash_fastwrite"

/* ===== HARD SAFETY GATE =====================================================
 * 0 = DRY-RUN ONLY: a 'W' live ('L') command is REFUSED. The firmware can only
 *     verify the digest, walk the SD blocks, and stream progress — it NEVER
 *     issues RequestDownload/TransferData, so it cannot erase or write the ECU.
 * 1 = LIVE ENABLED (Phase 5 build, recoverable ECU only).
 * Keep this 0 for the Phase 4 dry-run build. */
#define NCFW_ALLOW_LIVE 1

#define TESTER_ID 0x7E0u
#define ECU_ID 0x7E8u
#define FRAME_TIMEOUT_MS 200
#define FC_TIMEOUT_MS 2000        /* ECU may be slow to Flow-Control while busy (e.g. SBL init/erase) */
#define RESP_FIRST_TIMEOUT_MS 5000 /* a block's first response can lag seconds (erase after the SBL) */
#define RESP_PENDING_TIMEOUT_MS 5000
#define MAX_PENDING 24            /* ~ host TIMEOUT_RESPONSE_PENDING_MAX budget for 0x78 retries */

/* Granular ISO-TP sub-failure codes surfaced in the FWERR nrc field so a flash
 * abort says exactly WHERE a block died (vs the old opaque 0xFF). */
#define FWSUB_FF_SEND  0xE1 /* First Frame can_send failed */
#define FWSUB_FC_TO    0xE2 /* no Flow Control from ECU (recv timeout) */
#define FWSUB_FC_BAD   0xE3 /* Flow Control was not Clear-To-Send */
#define FWSUB_CF_SEND  0xE4 /* a Consecutive Frame can_send failed */
#define FWSUB_ACK_TO   0xE5 /* no response after the block (ECU silent) */
#define FWSUB_ACK_PCI  0xE6 /* response was not a single-frame */
#define FWSUB_ACK_SID  0xE7 /* response SID was neither the expected positive nor an NRC */
#define TX_QUEUE_SEND_TIMEOUT_MS 2000 /* host-gone abort threshold (never block forever) */
#define PROG_EVERY_N 16               /* stream NCFWPROG every N blocks */
#define MAX_MANIFEST_BYTES 8192

#define UDS_NRC 0x7F
#define NRC_RESPONSE_PENDING 0x78

#define FW_ROMS_DIR SD_CARD_MOUNT_POINT "/roms"

/* Streaming + block scratch (one fast-op at a time, in can_tx_task; static keeps
 * it off the task stack). s_fw_msg holds the ISO-TP payload: [0x36] + one block. */
static xdev_buffer s_out;
static uint8_t s_fw_chunk[4096];          /* CRC32 file reader */
static uint8_t s_fw_msg[1 + 1024 + 8];    /* 0x36 + up to a 1 KB block */

/* Re-entry guard: a fast-op owns the CAN bus exclusively. */
static volatile int s_fwbusy;

typedef struct {
    int manifest_version;
    uint32_t download_addr, download_size, block_size;
    uint32_t sbl_offset, sbl_len, program_offset, program_len;
    uint32_t image_len, image_crc32;
} fw_manifest_t;

/* ---- small helpers --------------------------------------------------------*/

/* zlib-compatible reflected CRC-32 step (matches host wican_sd_package.crc32). */
static uint32_t fw_crc32_step(uint32_t crc, const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len; i++)
    {
        crc ^= data[i];
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return crc;
}

/* Bounded send of s_out to the host TCP queue (the wedge-safe primitive: never
 * portMAX_DELAY — a host disconnect must time out into the clean teardown). */
static int tx_send(QueueHandle_t *tx_queue)
{
    return (xQueueSend(*tx_queue, &s_out, pdMS_TO_TICKS(TX_QUEUE_SEND_TIMEOUT_MS)) == pdTRUE)
               ? 0
               : -1;
}

/* Stream a NUL-terminated ASCII line. Returns 0 on success, -1 if host gone. */
static int fw_emit(QueueHandle_t *tx_queue, const char *line)
{
    size_t n = strlen(line);
    if (n > DEV_BUFFER_LENGTH) n = DEV_BUFFER_LENGTH;
    s_out.usLen = (int)n;
    s_out.dev_channel = DEV_WIFI;
    memcpy(s_out.ucElement, line, n);
    return tx_send(tx_queue);
}

/* Stream a terminal FWERR diagnostic (best-effort; host may already be gone). */
static void fw_emit_err(QueueHandle_t *tx_queue, uint32_t addr, int stage, int nrc)
{
    char line[64];
    int n = snprintf(line, sizeof(line), "\r\nFWERR a=%06lX st=%d nrc=%02X\r\n",
                     (unsigned long)addr, stage, nrc & 0xFF);
    if (n > 0)
    {
        s_out.usLen = n;
        s_out.dev_channel = DEV_WIFI;
        memcpy(s_out.ucElement, line, (size_t)n);
        (void)tx_send(tx_queue);
    }
    ESP_LOGE(TAG, "FWERR a=%06lX st=%d nrc=%02X", (unsigned long)addr, stage, nrc & 0xFF);
}

/* Reject anything but a simple leaf filename with an extension. */
static int fw_name_is_safe(const char *name)
{
    if (!name || name[0] == '\0' || name[0] == '.') return -1;
    size_t n = strlen(name);
    if (n > 96) return -1;
    int has_dot = 0;
    for (size_t i = 0; i < n; i++)
    {
        char c = name[i];
        if (c == '/' || c == '\\') return -1;
        if (c == '.' && i + 1 < n && name[i + 1] == '.') return -1;
        if (c == '.') has_dot = 1;
        int ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                 (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.';
        if (!ok) return -1;
    }
    return has_dot ? 0 : -1;
}

static int fw_mf_u32(cJSON *root, const char *key, uint32_t *out)
{
    cJSON *it = cJSON_GetObjectItem(root, key);
    if (!cJSON_IsNumber(it)) return -1;
    *out = (uint32_t)it->valuedouble; /* valuedouble is exact for our <2^32 ints */
    return 0;
}

static int fw_load_manifest(const char *path, fw_manifest_t *m)
{
    FILE *jf = fopen(path, "rb");
    if (!jf) return -1;
    fseek(jf, 0, SEEK_END);
    long sz = ftell(jf);
    fseek(jf, 0, SEEK_SET);
    if (sz <= 0 || sz > MAX_MANIFEST_BYTES) { fclose(jf); return -1; }
    char *txt = malloc((size_t)sz + 1);
    if (!txt) { fclose(jf); return -1; }
    size_t rd = fread(txt, 1, (size_t)sz, jf);
    fclose(jf);
    txt[rd] = '\0';
    cJSON *root = cJSON_Parse(txt);
    free(txt);
    if (!root) return -1;

    cJSON *mv = cJSON_GetObjectItem(root, "manifest_version");
    m->manifest_version = cJSON_IsNumber(mv) ? mv->valueint : -1;
    int bad = 0;
    bad |= fw_mf_u32(root, "download_addr", &m->download_addr);
    bad |= fw_mf_u32(root, "download_size", &m->download_size);
    bad |= fw_mf_u32(root, "block_size", &m->block_size);
    bad |= fw_mf_u32(root, "sbl_offset", &m->sbl_offset);
    bad |= fw_mf_u32(root, "sbl_len", &m->sbl_len);
    bad |= fw_mf_u32(root, "program_offset", &m->program_offset);
    bad |= fw_mf_u32(root, "program_len", &m->program_len);
    bad |= fw_mf_u32(root, "image_len", &m->image_len);
    bad |= fw_mf_u32(root, "image_crc32", &m->image_crc32);
    cJSON_Delete(root);
    return bad ? -1 : 0;
}

/* ---- LIVE-mode CAN/UDS primitives (compiled + reachable ONLY when a real flash
 *      is permitted; in a dry-run build mode 'L' is rejected before any of these
 *      can run, so the ECU is never contacted). --------------------------------*/

static int recv_matching(twai_message_t *msg, int timeout_ms)
{
    int64_t deadline = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    while (esp_timer_get_time() < deadline)
    {
        int64_t rem_ms = (deadline - esp_timer_get_time()) / 1000;
        if (rem_ms < 1) rem_ms = 1;
        if (can_receive(msg, pdMS_TO_TICKS(rem_ms)) == ESP_OK)
            if (msg->identifier == ECU_ID && msg->rtr == 0) return 0;
    }
    return -1;
}

/* Send an ISO-TP message (single- or multi-frame) of `total` payload bytes,
 * honoring the ECU's Flow Control. NO resend on error. Returns 0 on success or a
 * negative FWSUB_* sub-code so the caller can report exactly where it failed. */
static int fw_isotp_send(const uint8_t *payload, uint32_t total)
{
    twai_message_t tx = {0};
    tx.identifier = TESTER_ID;
    tx.extd = 0;
    tx.data_length_code = 8;

    if (total <= 7)
    {
        tx.data[0] = (uint8_t)total; /* Single Frame */
        for (uint32_t i = 0; i < total; i++) tx.data[1 + i] = payload[i];
        return (can_send(&tx, pdMS_TO_TICKS(FRAME_TIMEOUT_MS)) == ESP_OK) ? 0 : -FWSUB_FF_SEND;
    }

    /* First Frame */
    tx.data[0] = 0x10 | (uint8_t)((total >> 8) & 0x0F);
    tx.data[1] = (uint8_t)(total & 0xFF);
    uint32_t idx = 0;
    for (int i = 2; i < 8; i++) tx.data[i] = (idx < total) ? payload[idx++] : 0;
    if (can_send(&tx, pdMS_TO_TICKS(FRAME_TIMEOUT_MS)) != ESP_OK) return -FWSUB_FF_SEND;

    /* Flow Control (expect Clear-To-Send 0x30; BS/STmin honored). */
    twai_message_t fc;
    if (recv_matching(&fc, FC_TIMEOUT_MS) != 0) return -FWSUB_FC_TO;
    if ((fc.data[0] & 0xF0) != 0x30 || (fc.data[0] & 0x0F) != 0x00) return -FWSUB_FC_BAD;
    uint8_t stmin = fc.data[2];

    uint8_t seq = 1;
    while (idx < total)
    {
        twai_message_t cf = {0};
        cf.identifier = TESTER_ID;
        cf.extd = 0;
        cf.data_length_code = 8;
        cf.data[0] = 0x20 | (seq & 0x0F);
        for (int i = 1; i < 8; i++) cf.data[i] = (idx < total) ? payload[idx++] : 0;
        if (can_send(&cf, pdMS_TO_TICKS(FRAME_TIMEOUT_MS)) != ESP_OK) return -FWSUB_CF_SEND;
        seq = (seq + 1) & 0x0F;
        if (stmin > 0 && stmin <= 0x7F) vTaskDelay(pdMS_TO_TICKS(stmin)); /* ms range */
    }
    return 0;
}

/* Await a positive UDS response with SID `expect`, riding out 7F xx 78. Sets
 * *out_nrc to the real ECU NRC, or an FWSUB_ACK_* code, on failure. */
static int fw_await_positive(uint8_t expect, int *out_nrc)
{
    int pending = 0;
    for (;;)
    {
        twai_message_t rx;
        int to = pending ? RESP_PENDING_TIMEOUT_MS : RESP_FIRST_TIMEOUT_MS;
        if (recv_matching(&rx, to) != 0) { *out_nrc = FWSUB_ACK_TO; return -1; }
        if ((rx.data[0] >> 4) != 0x0) { *out_nrc = FWSUB_ACK_PCI; return -1; } /* want SF reply */
        uint8_t l = rx.data[0] & 0x0F;
        if (l >= 3 && rx.data[1] == UDS_NRC && rx.data[3] == NRC_RESPONSE_PENDING)
        {
            if (++pending > MAX_PENDING) { *out_nrc = NRC_RESPONSE_PENDING; return -1; }
            continue;
        }
        if (l >= 1 && rx.data[1] == expect) return 0;             /* positive */
        if (l >= 3 && rx.data[1] == UDS_NRC) { *out_nrc = rx.data[3]; return -1; }
        *out_nrc = FWSUB_ACK_SID;
        return -1;
    }
}

/* RequestDownload [0x34]+addr(4BE)+size(4BE) (KWP2000-style, no ALFID). */
static int fw_request_download(uint32_t addr, uint32_t size, int *nrc)
{
    uint8_t p[9] = {0x34,
                    (uint8_t)(addr >> 24), (uint8_t)(addr >> 16), (uint8_t)(addr >> 8), (uint8_t)addr,
                    (uint8_t)(size >> 24), (uint8_t)(size >> 16), (uint8_t)(size >> 8), (uint8_t)size};
    int s = fw_isotp_send(p, sizeof(p));
    if (s != 0) { *nrc = -s; return -1; }
    return fw_await_positive(0x74, nrc);
}

/* TransferData [0x36]+block (NO sequence counter), then await 0x76. s_fw_msg[0]
 * is preset to 0x36 by the caller; block bytes live at s_fw_msg[1..blen]. */
static int fw_transfer_data(uint32_t blen, int *nrc)
{
    s_fw_msg[0] = 0x36;
    int s = fw_isotp_send(s_fw_msg, blen + 1);
    if (s != 0) { *nrc = -s; return -1; }
    return fw_await_positive(0x76, nrc);
}

static int fw_transfer_exit(int *nrc)
{
    uint8_t p[1] = {0x37};
    int s = fw_isotp_send(p, 1);
    if (s != 0) { *nrc = -s; return -1; }
    return fw_await_positive(0x77, nrc);
}

static void fw_ecu_reset(void)
{
    uint8_t p[2] = {0x11, 0x01};
    (void)fw_isotp_send(p, 2); /* best-effort; no/late response is expected */
}

/* ---- command entry --------------------------------------------------------*/

int ncflash_is_fastwrite_cmd(const uint8_t *buf, int len)
{
    return (len >= NCFLASH_FASTWRITE_MIN_LEN && buf[0] == NCFLASH_FASTWRITE_CMD);
}

int ncflash_fast_write(const uint8_t *buf, int len, QueueHandle_t *tx_queue)
{
    if (!ncflash_is_fastwrite_cmd(buf, len)) return -1;

    char mode = (char)buf[1];

    /* Parse the staged leaf filename (buf[2..] up to CR/LF). */
    char name[97];
    int nlen = 0;
    for (int i = 2; i < len && nlen < (int)sizeof(name) - 1; i++)
    {
        uint8_t c = buf[i];
        if (c == '\r' || c == '\n') break;
        name[nlen++] = (char)c;
    }
    name[nlen] = '\0';
    if (fw_name_is_safe(name) != 0)
    {
        ESP_LOGE(TAG, "fast write rejected: unsafe name '%s'", name);
        fw_emit_err(tx_queue, 0, 8, 0);
        return -1;
    }

    if (s_fwbusy)
    {
        ESP_LOGW(TAG, "fast write refused: another fast-op in progress");
        return -1;
    }
    s_fwbusy = 1;

    /* Variables the cleanup label touches MUST be declared before any goto. */
    FILE *f = NULL;
    int was_suspended = 0;
    int host_gone = 0;
    int rc = 0;
    TaskHandle_t rx_task = NULL;
    fw_manifest_t m;

    char img_path[160];
    char man_path[160];
    char stem[97];
    strlcpy(stem, name, sizeof(stem));
    char *dot = strrchr(stem, '.');
    if (dot) *dot = '\0';
    snprintf(img_path, sizeof(img_path), "%s/%s", FW_ROMS_DIR, name);
    snprintf(man_path, sizeof(man_path), "%s/%s.json", FW_ROMS_DIR, stem);

    int live = (mode == 'L');

    ESP_LOGI(TAG, "fast write %s: %s", live ? "LIVE" : "dry-run", img_path);

    /* Take exclusive CAN ownership (mirrors live; also exercises the teardown). */
    rx_task = xTaskGetHandle("can_rx_task");
    if (rx_task) { vTaskSuspend(rx_task); was_suspended = 1; }
    twai_message_t junk;
    while (can_receive(&junk, 0) == ESP_OK) { /* drain */ }
    xdev_buffer txjunk;
    while (xQueueReceive(*tx_queue, &txjunk, 0) == pdTRUE) { /* discard */ }

    if (!sdcard_is_mounted()) { fw_emit_err(tx_queue, 0, 1, 0); rc = -2; goto cleanup; }

    /* 1. Manifest. */
    if (fw_load_manifest(man_path, &m) != 0) { fw_emit_err(tx_queue, 0, 2, 0); rc = -2; goto cleanup; }
    if (m.manifest_version != 1 || m.block_size == 0 || m.block_size > 1024 ||
        m.sbl_offset + m.sbl_len > m.image_len ||
        m.program_offset + m.program_len > m.image_len)
    {
        fw_emit_err(tx_queue, 0, 3, 0);
        rc = -2;
        goto cleanup;
    }

    /* 2. Open staged image, check declared size. */
    f = fopen(img_path, "rb");
    if (!f) { fw_emit_err(tx_queue, 0, 1, 0); rc = -2; goto cleanup; }
    fseek(f, 0, SEEK_END);
    long fsz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (fsz != (long)m.image_len) { fw_emit_err(tx_queue, 0, 4, 0); rc = -2; goto cleanup; }

    /* 3. PRE-ERASE INTEGRITY GATE (hard block): CRC32 over the whole staged image
     *    must equal the manifest. A corrupt upload aborts here with NO ECU contact. */
    {
        uint32_t crc = 0xFFFFFFFFu;
        size_t got;
        while ((got = fread(s_fw_chunk, 1, sizeof(s_fw_chunk), f)) > 0)
            crc = fw_crc32_step(crc, s_fw_chunk, got);
        crc ^= 0xFFFFFFFFu;
        if (crc != m.image_crc32)
        {
            ESP_LOGE(TAG, "digest gate FAIL: crc 0x%08lX != manifest 0x%08lX",
                     (unsigned long)crc, (unsigned long)m.image_crc32);
            fw_emit_err(tx_queue, 0, 5, 0);
            rc = -2;
            goto cleanup;
        }
        ESP_LOGI(TAG, "digest gate OK: crc 0x%08lX", (unsigned long)crc);
    }

    /* HARD SAFETY GATE: a live flash is only permitted in an NCFW_ALLOW_LIVE build. */
    if (live && !NCFW_ALLOW_LIVE)
    {
        ESP_LOGW(TAG, "live flash refused: this is a DRY-RUN build (NCFW_ALLOW_LIVE=0)");
        fw_emit_err(tx_queue, 0, 7, 0);
        rc = -2;
        goto cleanup;
    }

    uint32_t bs = m.block_size;
    uint32_t sbl_blocks = (m.sbl_len + bs - 1) / bs;
    uint32_t prog_blocks = (m.program_len + bs - 1) / bs;
    uint32_t total_blocks = sbl_blocks + prog_blocks;

    if (fw_emit(tx_queue, "NCFWSYNC\n") != 0) { host_gone = 1; rc = -3; goto cleanup; }

    if (live)
    {
        int nrc = 0;
        if (fw_request_download(m.download_addr, m.download_size, &nrc) != 0)
        {
            fw_emit_err(tx_queue, m.download_addr, 10, nrc);
            rc = -2;
            goto cleanup;
        }
    }

    /* 4/5. Walk SBL region then program region: read each block from the staged
     *      image and (live only) TransferData it. Stream NCFWPROG periodically. */
    uint32_t regions[2][2] = {
        {m.sbl_offset, m.sbl_len},
        {m.program_offset, m.program_len},
    };
    uint32_t done = 0;
    for (int r = 0; r < 2; r++)
    {
        uint32_t off = regions[r][0];
        uint32_t rem = regions[r][1];
        while (rem > 0)
        {
            uint32_t take = (rem > bs) ? bs : rem;
            if (fseek(f, (long)off, SEEK_SET) != 0 ||
                fread(&s_fw_msg[1], 1, take, f) != take)
            {
                fw_emit_err(tx_queue, off, 6, 0);
                rc = -2;
                goto cleanup;
            }

            if (live)
            {
                int nrc = 0;
                if (fw_transfer_data(take, &nrc) != 0)
                {
                    fw_emit_err(tx_queue, off, r == 0 ? 11 : 12, nrc);
                    rc = -2;
                    goto cleanup;
                }
            }

            off += take;
            rem -= take;
            done++;
            if ((done % PROG_EVERY_N) == 0 || done == total_blocks)
            {
                char line[40];
                snprintf(line, sizeof(line), "NCFWPROG %lu/%lu\n",
                         (unsigned long)done, (unsigned long)total_blocks);
                if (fw_emit(tx_queue, line) != 0) { host_gone = 1; rc = -3; goto cleanup; }
            }
            if ((done & 0x1F) == 0) vTaskDelay(1); /* WDT yield */
        }
    }

    if (live)
    {
        int nrc = 0;
        if (fw_transfer_exit(&nrc) != 0)
        {
            fw_emit_err(tx_queue, 0, 13, nrc);
            rc = -2;
            goto cleanup;
        }
        fw_ecu_reset(); /* best-effort */
    }

    (void)fw_emit(tx_queue, "NCFWDONE\n");
    ESP_LOGI(TAG, "fast write %s complete: %lu blocks",
             live ? "LIVE" : "dry-run", (unsigned long)total_blocks);
    rc = 0;

cleanup:
    if (f) fclose(f);
    if (was_suspended && rx_task) vTaskResume(rx_task);
    {
        twai_message_t drain;
        while (can_receive(&drain, 0) == ESP_OK) { /* discard stale RX */ }
    }
    {
        uint32_t alerts = 0;
        (void)twai_read_alerts(&alerts, 0);
    }
    if (host_gone)
    {
        xdev_buffer leftover;
        while (xQueueReceive(*tx_queue, &leftover, 0) == pdTRUE) { /* discard */ }
        ESP_LOGW(TAG, "fast write aborted: host stopped draining TCP (clean teardown)");
    }
    s_fwbusy = 0;
    return rc;
}
