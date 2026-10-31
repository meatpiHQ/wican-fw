# Performance battery vs the composed main

Command: `.\test.ps1 perf`

Needs: main fw on DUT + rpi001; 2026-10-28 on the issue-1023 branch build: MQTT RTT p50 8.6 ms / p95 19.6 ms, the paced drain PASS, WS latency p50 13.6 ms, obd_poll 709 polls in 30 s at p50 40.8 ms; the BLE A/B leg failed with SCAN FAILED because BLE is off in the DUT's settings (the canonical bench set): it SKIPs on that now, the fixture-absent pattern

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Performance battery vs the composed main**, every scenario's numbers land in the report: MQTT RTT (512B) + paced 4000B drain (`mqtt_bench.py`, needs mqtt enabled against the Pi broker; the device silently ignores `size > 4000` = `BENCH_MAX_SIZE`), WS echo latency via `/ws/can` + OBD-poll via `/ws/obd` (`ws_bench.py`), BLE A/B RTT (`ble_ab.py`, runs **last**: a BLE connect makes interface_manager suspend WiFi, which also skips its TCP throughput legs by design; clean-radio BLE TX = manual `ble_blast.py`)
