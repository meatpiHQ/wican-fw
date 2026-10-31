# ATMA flood bench (PC-run, PCAN, 2026-09-08)

Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index row and the other stages: [../TESTING.md](../TESTING.md).

`python tools\testbench\obd\atma_flood_bench.py [dut[:port]] [--dut-ip 10.42.1.194] --source pcan [--pcan PCAN_USBBUS2] [--rate 2500] [--count 10000] [--id 0x123]`
(→ `ATMA FLOOD PASS`, ~30 s; use the v6.0.2 IDF venv python: python-can
+ isotp live there). Streams a CAN flood through the MIC chip's monitor
mode (`ATH1 ATS1 ATCAF0 ATMA`) to an ELM app on TCP:35000 (`atma_capture.py`
runs on rpi001, the app's seat) and places every lost frame at a hop:
PCAN sends `--count` frames whose data bytes 0..3 are a big-endian
counter (exactly-N: every counter once, in order), `/api/obd_chip` gives
the chip's UART bytes, overflows and per-subscriber fan-out drops,
`/api/bridges` + `/api/sockets` the pump and socket counters, and the
native TWAI's `/api/can` `rx` is the on-bus witness that separates a
chip-side loss from a bus-side one. `--source sim` uses the simulator's
`broadcast_enabled` instead (static payload, 5 ms floor: rate and line
integrity only). **Bench 2026-09-08 (final build): PASS at 1000, 2500 and
4000 frames/s**, 5000 / 10000 / 12000 frames, every counter seen once
in order, 0 malformed lines, 0 UART overflows, 0 fan-out drops, 0 socket
drops, bytes conserved chip → socket → client (348 KB at 4000/s = the
chip printing 56 KB/s with headers on), witness == sent. Before the
raw-bridge coalescing the same 2500/s run dropped 6052 chunks at the TCP
bridge's fan-out queue (one socket send per ~29-byte frame line) and
delivered 3684 of 10000 with spliced lines; the first coalescing cut
panicked (`prvNotifyQueueSetContainer`): a member queue was read without
the queue set; fixed. Needs PCAN_USBBUS2 on the DUT bus, `can_manager`
running for the witness (optional), autopid may stay enabled (it yields).
