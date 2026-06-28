/*
 * NC Flash fast ROM read — autonomous in-firmware ISO-TP read loop.
 *
 * The WiCAN's SLCAN-over-TCP path pays a WiFi round-trip per 1 KB ROM block
 * (~294 ms/block measured), so a 1 MB read takes ~5 min even at STmin=0. This
 * moves the read LOOP into the firmware: NC Flash (already authenticated with
 * the ECU over the normal SLCAN path) sends one "fast read [addr,len]" command;
 * the ESP32 then issues every UDS ReadMemoryByAddress(0x400) to the ECU LOCALLY
 * over CAN (~sub-ms round-trips), reassembles each ISO-TP response, and streams
 * the raw ROM bytes back over one TCP connection. The WiFi round-trip is paid
 * once instead of 1024×, approaching the CAN-bus ceiling (~50-60 s).
 *
 * READ-ONLY: this only ever sends ReadMemoryByAddress (SID 0x23). It never
 * authenticates and never writes — NC Flash owns the security/session.
 */

#ifndef NCFLASH_FASTREAD_H
#define NCFLASH_FASTREAD_H

#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

/* Command framing on the SLCAN socket: 'X' + 8 hex start addr + 8 hex length +
 * CR (18 bytes), e.g. "X0000000000100000\r" = read 0x000000..0x100000. */
#define NCFLASH_FASTREAD_CMD       'X'
#define NCFLASH_FASTREAD_CMD_LEN   18

/* Version ping: a fast-read whose start address is this sentinel streams back a
 * fixed build marker (NCFLASH_FASTREAD_VERSION bytes) WITHOUT touching CAN, so
 * the host can confirm exactly which fast-read build is live before a real read.
 * Bump the version string whenever the read loop's wire behaviour changes. */
#define NCFLASH_FASTREAD_PING_ADDR 0xFFFFFFFEu
/* This NCFRv<rev> marker is the SOLE wire-contract the host rev-gates on (NCFLASH_FASTWRITE_VERSION
 * is log-only, never streamed). The host (src/ecu/constants.py) reads it on the dedicated port to
 * detect capability: rev >= COEXIST_MIN_FW_REV(6) => no-reboot coexistence (dedicated SLCAN port
 * 35001). v6 means ONLY "the dedicated port exists + routes the protocol-agnostic codecs"; the
 * REST /datalog endpoint (36.C) is a SEPARATE capability the host soft-detects (404 degrades, never
 * aborts a flash). Keep this in lockstep with COEXIST_MIN_FW_REV. */
#define NCFLASH_FASTREAD_VERSION   "NCFRv6\n"   /* v6: no-reboot SLCAN coexistence (dedicated port 35001); v5: clean-teardown + live SD fastwrite */

/* Sync preamble streamed once, right after CAN forwarding is suspended and
 * before the first ROM byte. Any CAN frames already queued/in-flight toward the
 * host (TX queue + TCP send buffer) precede it; the host discards everything up
 * to and including this marker, then reads exactly `length` ROM bytes. The
 * token cannot occur in SLCAN frames (hex 0-9A-F; types t/T/r/R), so the host
 * locks onto it unambiguously even amid live CAN traffic. */
#define NCFLASH_FASTREAD_SYNC      "NCFRDATA"

/* True if buf looks like a fast-read command (so the caller routes it here
 * instead of to slcan_parse_str). */
int ncflash_is_fastread_cmd(const uint8_t *buf, int len);

/* Parse and execute a fast-read command, streaming the ROM bytes to *tx_queue
 * as xdev_buffer blocks. Suspends can_rx_task for the duration so it cannot
 * steal the ECU's response frames. Returns 0 on success; <0 on a parse error or
 * a block that could not be read after retries (in which case streaming stops,
 * so NC Flash sees a short read and can fall back to the SLCAN path). */
int ncflash_fast_read(const uint8_t *buf, int len, QueueHandle_t *tx_queue);

#endif /* NCFLASH_FASTREAD_H */
