# ESPNetLink roaming scenario

Command: `python tools\testbench\wifi\espnetlink_roam_bench.py`

Needs: DUT console (CH344 channel B, COM254 today) + rpi001 (wtest0 borrowed for the run, `wican-bench` restored after) + paired dongle

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**ESPNetLink roaming scenario** (→ `ESPNL ROAM PASS`, ~8 min): the drive-away / drive-home story in `espnetlink.mode=wifi_modem`, away on the dongle (uplink=espnetlink, GPS polls + fix) → a temp Pi hotspot broadcasts the DUT's OWN primary SSID/key, read from `/api/settings/backup` ("drive home") → roam-to-preferred lands within `sta_roam_interval_s`+120 s, uplink=wifi, dongle polls STOP → hotspot down ("drive away") → fallback to the dongle ≤150 s with the GPS fix surviving (the dongle is never rebooted) → home again, zero console E lines. Console-observed only (a client on the DUT's AP pauses its STA reconnects); the temp AP pins `ipv4.addresses 10.42.9.1/24` (NM's default shared subnet collides with the other hotspot and rolls the fresh AP back). Bench 2026-08-24: roam-home 116/66 s, fallback 18 s, fix valid +3 s
