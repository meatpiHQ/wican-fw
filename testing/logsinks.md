# External log sinks conservation

Command: `.\test.ps1 logsinks`

Needs: main fw on DUT + rpi001 + SD card + ws_cli enabled

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**External log sinks conservation** (`log_sinks_bench_test.py` → `LOG SINKS BENCH PASS`, runs ON the Pi; also a `live` stage): gates-closed defaults (port closed, counters zero, ws_log migration present), then per-sink conservation vs the `logsinks emit` known generator: TCP tail exactly-N, UDP collector exactly-N, `/ws/log` stream, SD file content + rotation + retention, per-sink `in == out + dropped` identity, restore. Reboots the DUT 3×
