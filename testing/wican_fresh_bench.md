# WiCAN Pro out-of-the-box scenario

Command: `python tools\testbench\wifi\wican_fresh_bench.py --wican COMx --psu COMz`

Needs: WiCAN console + PSU + rpi001 (`wtest0` hotspot, `wtest1` free)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**WiCAN Pro out-of-the-box scenario** (→ `WICAN FRESH PASS`, ~5 min): erase-flash + flash, PSU cold boot with the console captured from power-on; asserts the first-boot defaults (wifi_manager mode=ap, 0 STA networks, derived `WiCAN_<id>` AP, USB host enabled, espnetlink wifi_modem unpaired), then plays the new user: the Pi joins the AP with the default `@meatpi#`, loads the web UI + `/api/settings` + the wifi_manager schema, stages home WiFi (the Pi `wican-bench` hotspot) + apsta through the settings API exactly as the UI does, submits (ONE planned reboot) and expects the STA on the home network WHILE the phone stays on the AP, the AP still up, the API still answering; planned reboots only, 0 E lines. `--no-erase` re-runs on an already-fresh unit. Bench 2026-08-31: PASS (STA on the home network 9-34 s after the config reboot; a dongle on the connector pairs in the background = one extra planned reboot, accounted for)
