# Live checks vs the composed main firmware

Command: `.\test.ps1 live`

Needs: main fw on DUT at `-DutIp` + rpi001

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Live checks vs the composed main firmware**: CLI over WebSocket (`cli_ws_test.py` → `CLI WS PASS`; incl. `system -t` task monitor and, since 2026-10-11, the dispatcher's verdict lines: `bogus` → `Unknown command`, `led -c` → `command failed (1)`; the `Internal error` verdict of issue #980 is host-tested only, a healthy device cannot produce it) + OBD over WebSocket (`ws_live_test.py` → `WS LIVE PASS`; it pauses/restores every autopid group via `POST /api/autopid/group`: autopid co-masters the chip and its traffic fans out to `/ws/obd`; since 2026-10-21 it sends a warm-up CR and drains it before judging `ATI`: with autopid enabled the firmware yields the chip to the first app command by aborting the poll, and the chip's `STOPPED` for that abort lands on that command, which apps simply retry) + real-DBC parser cross-check (`dbc_real_test.py` → `DBC REAL PASS`; uploads the fixture DBCs, diffs every signal against the host reference) + the USB-Ethernet / espnetlink benches (auto-**SKIP** when hardware absent) + **network-trust lockdown sweep LAST** (`wifi_gate_live_test.py` → `WIFI GATE PASS`: flips `sta_trusted` false, probes EVERY `/api` route × 4 methods + UI + WS over the STA address expecting 403/405 only, USB admin + MQTT must stay up; reboots the DUT twice, restores). Needs the canonical bench settings (below)

**2026-10-31 (the HTTP server task on a PSRAM stack): PASS**, seven legs: `CLI WS PASS` (16 console checks, `system -m` prints the `Flash jobs:` counters line since that day), `WS LIVE PASS`, the real-DBC cross-check, the live stack audit, the event manager (settings written over HTTP are marshalled to the flash worker), the external log sinks conservation, the ESPNetLink leg. Report `2026-10-31_2134_live.md`.
