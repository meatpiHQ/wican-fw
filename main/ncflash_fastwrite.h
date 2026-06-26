/*
 * NC Flash fast ROM WRITE — autonomous in-firmware SD-staged flash (Option B).
 *
 * The host stages a checksum-corrected ROM (+appended SBL) onto the SD card via
 * POST /upload/sd/<name> and a JSON manifest sidecar (see the host modules
 * src/ecu/wican_sd_package.py + wican_sd_upload.py), then sends ONE "W" command
 * over the SLCAN socket. The ESP32 then drives the ECU program sequence LOCALLY
 * over CAN at line rate (RequestDownload -> TransferData(SBL) ->
 * TransferData(program) -> TransferExit -> ECUReset), sourcing bytes from the SD
 * image and streaming progress markers back over the same TCP stream the
 * fast-read uses. WiFi is taken OUT of the flash loop — the brick driver of the
 * host-driven path (an unpaced CF burst overrunning the gateway) is gone.
 *
 * SAFETY (PRIME DIRECTIVE — a bad flash bricks an ECU):
 *   * The host keeps ALL secrets/compute (security key, SBL decrypt, checksum
 *     correction, dynamic diff). The firmware replays PLAIN BYTES only.
 *   * NO mid-stream resend, EVER. A dropped CF / missed 0x76 / SD error aborts the
 *     whole session (FWERR) -> the host restarts from scratch (re-auth + re-run).
 *   * NO block-sequence counter on TransferData ([0x36]+raw only).
 *   * Pre-erase integrity gate is HARD-BLOCKING: the firmware re-verifies the
 *     staged image CRC32 vs the manifest BEFORE any RequestDownload/erase. A
 *     corrupt upload aborts with ZERO ECU contact.
 *   * Clean teardown on EVERY exit (resume can_rx_task, drain RX, clear TWAI
 *     alerts) with BOUNDED xQueueSend (never portMAX_DELAY) so a host disconnect
 *     can never strand the task / wedge the bus mid-flash.
 *
 *   * NCFW_ALLOW_LIVE compile gate (default 0): when 0, the actual ECU-write path
 *     ('W' mode 'L') is REFUSED — the firmware can only DRY-RUN (verify digest +
 *     walk the SD blocks + stream progress, NO RequestDownload, NO erase, NO ECU
 *     contact). This makes a dry-run build physically incapable of a flash. Set
 *     to 1 only for the Phase 5 live-flash build on a recoverable ECU.
 *
 * Command framing on the SLCAN socket: 'W' + mode + staged filename + CR.
 *   mode 'D' = dry-run (no ECU write), mode 'L' = live (requires NCFW_ALLOW_LIVE).
 *   e.g. "WDLF9VEB_20260623-1800.bin\r"
 * The firmware reads /sdcard/roms/<name> (the staged image) and
 * /sdcard/roms/<stem>.json (the manifest) — it never trusts a path from the wire
 * beyond a sanitised leaf filename.
 *
 * Progress markers streamed back (newline-delimited ASCII, cannot collide with
 * SLCAN hex frames; the host resyncs on NCFWSYNC):
 *   NCFWSYNC\n                         once, after the firmware takes the bus
 *   NCFWPROG <done>/<total>\n          per N blocks
 *   NCFWDONE\n                         terminal success
 *   FWERR a=<addr> st=<stage> nrc=<XX>\n   terminal failure (mirror of fastread FRERR)
 */

#ifndef NCFLASH_FASTWRITE_H
#define NCFLASH_FASTWRITE_H

#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#define NCFLASH_FASTWRITE_CMD 'W'
/* Minimum command length: 'W' + mode + at least 1 filename char. */
#define NCFLASH_FASTWRITE_MIN_LEN 3

/* Build marker bumped to NCFRv5 once a LIVE-capable fastwrite ships (so the
 * host rev-gate in src/ecu/wican_sd_flash.py opens). A dry-run-only build keeps
 * the fast-read marker at NCFRv4. */
#define NCFLASH_FASTWRITE_VERSION "NCFWv1"

/* True if buf looks like a fast-write command (routed here before slcan_parse_str). */
int ncflash_is_fastwrite_cmd(const uint8_t *buf, int len);

/* Parse and execute a fast-write command, streaming progress to *tx_queue.
 * Returns 0 on success (NCFWDONE), <0 on a parse error / digest gate / abort
 * (FWERR already streamed). Suspends can_rx_task for the duration. */
int ncflash_fast_write(const uint8_t *buf, int len, QueueHandle_t *tx_queue);

#endif /* NCFLASH_FASTWRITE_H */
