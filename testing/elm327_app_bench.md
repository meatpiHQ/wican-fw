# ELM327 app responsiveness bench (PC-run, 2026-09-08)

Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index row and the other stages: [../TESTING.md](../TESTING.md).

`python tools\testbench\obd\elm_app_bench.py [dut[:port]] [--dut-ip 10.42.1.194]`
(→ `ELM APP PASS`, ~1 min; the request loop `elm_latency_probe.py` is
copied to rpi001 and runs there over the hotspot, the API is read
through the tunnel). Born from the field report "Car Scanner feels slow,
the RPM dial is choppy, values jump" against the component firmware.
Reproduced on the bench BEFORE the fix with autopid polling its 11
standard PIDs beside the app: 80 of 200 hint-less `010C` requests (8 of
200 hinted `010C 1`) came back with autopid's own lines (`0105 / 41 05
82`), `STOPPED` or `NO DATA` (the chip fans every line out to every
subscriber and an app command landing mid-poll stops the poll) versus
0 of 200 with autopid paused; and every response carried the RX task's
20 ms `uart_read_bytes` budget (hinted p50 26 ms for a ~3 ms chip
answer). Fix: autopid yields the chip while a bridged client is active
(10 s idle window, legacy parity), the chip RX task reads on UART
events, `TCP_NODELAY` on socket clients. Legs: preflight (autopid
enabled + polling, obd0 up) → hinted loop beside the configured autopid
(zero foreign/STOPPED/NO DATA, p50 ≤ 12 ms, p95 ≤ 40 ms, ≥ 40 req/s,
`paused_client` seen, `polls_ok` frozen) → hint-less loop (clean, p95 ≤
120 ms: the chip's own multi-ECU wait) → resume ≤ 15 s AND the resumed
polls succeed (the app leaves the chip with `ATS0`/`ATH1`/…; the poller
re-sends its protocol prelude, without that every resumed poll failed
to parse, 110 failures in a row on the first fixed build). Needs the ECU
simulator answering on the chip's bus, `autopid.enabled` with PIDs
configured, `obd0` + `br_tcp_obd` (defaults). **Bench 2026-09-08: PASS**,
hinted loop 129 req/s, p50 5.2 ms, p95 13.8 ms (one 214 ms WiFi
outlier in 200), 0 foreign answers, `paused_client` seen, `polls_ok`
frozen; hint-less loop 23.4 req/s, p50 37.6 ms, p95 69.6 ms, 0 foreign
answers; resume 9.7 s after the last command, +41 ok / +0 failed polls
after it; 0 new `E (` lines on that boot. Before the fix on the same
rig: hinted p50 26 ms at 35 req/s with 8/200 foreign answers, hint-less
p50 69 ms at 16 req/s with 80/200.

**Reference-adapter comparison**: `tools/testbench/obd/elm_compare.py`
prints one markdown table: the stored before/after rows
(`tools/testbench/obd/fixtures/elm_latency_2026-09-08.json`) plus live
rows for any `--tcp label=host[:port]` (a WiCAN) and `--serial
label=COMx[:baud]` (a USB ELM adapter such as an OBDLink SX/EX on the
same simulator bus; needs pyserial). Same probe, same two request
patterns (`010C 1` fast mode, plain `010C`), 200 requests each.
Procedure for the OBDLink: plug it into the simulator's OBD socket and
the PC, find its COM port (`python -m serial.tools.list_ports -v`), for
an FTDI-based adapter set the port's latency timer to 1 ms in Device
Manager (the 16 ms default lands on every response), then
`python tools\testbench\obd\elm_compare.py --serial obdlink=COMx:115200`.
Compare the hinted rows for the transport floor and the hint-less rows
for the chip's multi-ECU wait; the WiCAN's TCP row belongs on the Pi or
a PC on the DUT's WiFi, not through the ssh tunnel.
**Reference run 2026-09-08, vLinker FS** (MIC3322 V2.3.02 behind an FTDI
FT-X, COM39 @ 1 000 000 baud, replacing the WiCAN on the simulator bus;
`atma_flood_bench.py`/`vt_large_bench.py` take `--serial COM39:1000000`
for the same legs): hinted `010C 1` p50 15.9 ms at 62.5 req/s with stdev
0.1, a flat floor that is the FTDI latency timer's 16 ms default, not the
chip (the WiCAN after the fix: 5.2 ms at 129 req/s over WiFi); hint-less
`010C` p50 47.8 ms vs the WiCAN's 37.6; ATMA flood PASS at 1000 and 2500
frames/s (exactly-N) and `BUFFER FULL` after 2047 frames at 4000/s: its
1 Mbaud UART carries ~100 KB/s and 4000 frame lines/s need 116 KB/s,
where the WiCAN's chip at 2 Mbaud passed; 4 KB VT legs byte-exact both
ways (tx 4064 B 594 ms, rx 4094 B 556 ms, the same ~420 ms ATST-dominated
floor as the WiCAN's 630/557 ms). Set the FTDI latency timer to 1 ms
before quoting its hinted latency against the WiCAN.
