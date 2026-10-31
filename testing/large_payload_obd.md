# Large-payload OBD bench: VTFullyRequestCk / 4 KB ISO-TP (PC-run, PCAN, 2026-09-08)

Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index row and the other stages: [../TESTING.md](../TESTING.md).

`python tools\testbench\obd\vt_large_bench.py [dut[:port]] [--dut-ip 10.42.1.194] --ecu pcan [--pcan PCAN_USBBUS2] [--tx 64,512,2048,4064] [--rx 64,512,2048,4094] [--capture-args "--no-headers --spaces"]`
(→ `VT LARGE PASS`, ~2.5 min; v6.0.2 IDF venv python). The MIC chip's
4 KB ISO-TP paths end to end through the product path (TCP:35000 → bridge
→ UART → chip → CAN; `vt_large_capture.py` runs on rpi001) against
`pcan_memory_ecu.py`, a python-can/isotp UDS "memory ECU" on 7E4/7EC
(byte at address A = A & 0xFF; `0x23` serves it up to 4094 B, `0x36`/`0x3D`
verify what they receive and send the tester box's `7F 36 78` pending
first). The simulator's ECU is switched off for the run through its own
settings API (it answers the whole 7E0-7E7 range) and restored after.
Default framing = the MTPS-OBD tester's (`wican_pro_tester/MTPS-OBD+Log.TXT`):
`ATH1 ATS0 ATCAF1 ATAL`, `ATSH7E4`, `VTFullyRequestCk<LLLL>36<bsc><data><CCCC>`,
NO `1` hint on the reads. Legs: tx 64/512/2048/4064 B (the ECU must
answer `76 xx` after its pending = every byte verified), rx
64/512/2048/4094 B (byte-exact at the client), UART overflow / fan-out /
socket drop counters, byte conservation both ways, and the command-engine
path via `/api/autopid/test` (WARN: that route's raw buffer is
AP_RESP_MAX 1024 chars). **Bench 2026-09-08: PASS in both header modes**,
tx 4064 B: 8 157-char line, chip transmit ~220 ms (+ the ATST wait ≈
630 ms wall); rx 4094 B: 586 raw lines / 11.7 KB (headers on) or 587 `N:`
rows / 14.7 KB (headers off), ~555 ms wall, 0 overflows, 0 drops, bytes
conserved (chip rx == socket out). Chip facts learned: the `1` hint ends
a multi-frame response after its FIRST FRAME with headers on (2 KB → `NO
DATA`); assembly + automatic flow control only for 7E0-7E7/7E8-7EF
(740/748 printed raw First Frames, `ATFCSH/FCSD/FCSM1` did not help);
the engine accumulator OBD_RESP_MAX was 4 KB and truncated these
(`response … exceeded 4096 bytes`): now 16 KB. Open: autopid's own
AP_RESP_MAX (1024 chars) caps what the runner / test route can read.
