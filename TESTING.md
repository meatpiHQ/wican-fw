# Running the WiCAN tests

One entry point: **`.\test.ps1`** (PowerShell, repo root). `.\test.ps1 list`
prints every runnable suite/check. The rig itself (the control plane, the
recovery ladder, the timeouts) is in `testing/bench_rig.md`, the preflight
every run starts with in `testing/preflight.md`.

**How this file is laid out (since 2026-10-31).** The table under "The test
kinds" is the index: one line per stage with its command, its verdict line,
its duration and a link to the stage's page under `testing/`. The page holds
what the stage covers, every PASS record with its numbers, and the traps met
along the way (the rows carried all of that in one cell until 2026-10-31,
when the file had grown to 380 KB). Read the row, then the page of the stage
you are about to run; a new bench lands a one-line row here and its page
there, and a run's record goes on the page.

**Two ESP-IDF installs since 2026-10-27** (v6.1 beside v6.0.2): `build.ps1`
and `test.ps1` take `-Idf 6.0.2|6.1` (default 6.1 since 6.0.0-beta.5, the
first release built with it; 6.0.2 stays installed) and set the matching
IDF tree and Python environment themselves (v6.1's own export.ps1 does not
find the installer's venv). Switching the IDF needs a fresh `build\`: delete
it, since `-Clean` would run the other IDF's fullclean on a foreign cache.
The first v6.1 build (2026-10-27) compiled clean (the 13 warnings are the
vendored components' and predate it: same compiler, same flags), re-resolved
`dependencies.lock` (idf 6.1.0, the managed components unchanged) and added
the 6.1 symbols to `sdkconfig`; the image grew about 25 KB (25 % of the app
partition free).
Release Test PASS 59/59 on it; the one measurable change is internal RAM: the
settled free after boot is 3.9 KB higher, the boot's floor 10 KB lower (39.7 KB),
set inside the HTTP server's start. The boot map prints `WICAN RAMMIN <step>=<min>`
since that day (the internal minimum at the end of every step that lowered it,
then `end=`), beside `WICAN RAMMAP` (the retained delta per step) and
`WICAN BOOTTIME`: a step that borrows RAM for a moment shows in RAMMIN only.
The Release Test's `supply_current_awake_band` (90..300 mA) assumes the ESPNetLink
on the DUT's USB port; with the PC cable there instead the PSU reads 88 to 91 mA.

**Every run writes a dated summary report to `test-reports\`** (results
table, performance numbers, DUT conditions, git revision): that folder is
the pass/fail + performance history; see `test-reports/README.md` for how to
compare runs. Raw per-stage logs go to `test-reports\logs\<run>\` (local
only). Suppress with `-NoReport`.

**One WICAN_TEST_AP on the air** (since 2026-10-02 the bench AP is the P4
`bench_ap` on its NCM link, NetworkManager profile `wican-bench-usb`; the
Pi's own hotspot profiles `wican-bench` and `wican-bench-w0` stay parked).
Two radios with that SSID make the DUT roam between them and drop every TCP
connection on each move: the 6.0.0-beta.5 release run (2026-10-27) paid two
WiFi drops and six MQTT error lines because a Pi reboot had re-armed the
twins through the Pi's pre-P4 boot service and the hotspot watchdog bounced
the internal radio under the DUT. The Pi scripts in `tools/testbench/pi`
park the twins while the P4 serves since that night (deploy with
`deploy_bench_pi.sh` on the Pi), and the bench preflight prints which radio
serves (`ap=P4 (wican-bench-usb)`) and refuses a run with a twin beside the
P4, naming the parking command. A DUT log reading `STA got IP 10.42.0.x` or
a bssid starting 2c:cf:67 means the twin is up.

**Every test directory has a `README.md` stating exactly what it covers and
what the expected passing output is** (Unity summary for host suites, ordered
serial markers for target apps, pytest verdict for HIL):

- `components/<comp>/host_test/README.md`: host unit suites (×28)
- `components/<comp>/test_apps/README.md`: self-contained on-target apps (×12)
- `components/wifi_manager/test_apps_hil/README.md`: the WiFi HIL suite

## The test kinds

| Command | What runs | Needs |
|---|---|---|
| `.\test.ps1 check` | **The quick bench check** (~10 s). What it covers, the records and the traps: [testing/check.md](testing/check.md) | PC + rpi001 |
| `.\test.ps1 host [component]` | **Host unit suites (components/*/host_test, 526 tests across 44 components as of 2026-07-26**. What it covers, the records and the traps: [testing/host.md](testing/host.md) | `ssh rpi001` reachable (the WSL form: gcc in WSL, the IDF tree on C:) |
| `.\test.ps1 target <component>` | **Builds + flashes that component's self-contained on-target app (components/<comp>/test_apps), captures serial,...** (prints `TEST DONE`). What it covers, the records and the traps: [testing/target.md](testing/target.md) | DUT console port (the preflight picks it; + rpi001 for ble_manager) |
| `.\test.ps1 hil` | **WiFi hardware-in-the-loop** pytest: real STA connect / reconnect / fallback / ban / AP-auto-disable against the Pi's AP (add `-Flash` to rebuild+flash the HIL app first) | DUT + `ssh rpi001` |
| `.\test.ps1 live` | **Live checks vs the composed main firmware** (prints `CLI WS PASS`). What it covers, the records and the traps: [testing/live.md](testing/live.md) | main fw on DUT at `-DutIp` + rpi001 |
| `.\test.ps1 blesec` | **BLE bonding + security end-to-end** (prints `BLE SECURITY PASS`). What it covers, the records and the traps: [testing/blesec.md](testing/blesec.md) | main fw on DUT + rpi001 + UB500 |
| `.\test.ps1 perf` | **Performance battery vs the composed main**. What it covers, the records and the traps: [testing/perf.md](testing/perf.md) | main fw on DUT + rpi001; 2026-10-28 on the issue-1023 branch build: MQTT RTT p50 8.6 ms / p95 19.6 ms, the paced drain PASS, WS latency p50 13.6 ms, obd_poll 709 polls in 30 s at p50 40.8 ms; the BLE A/B leg failed with SCAN FAILED because BLE is off in the DUT's settings (the canonical bench set): it SKIPs on that now, the fixture-absent pattern |
| `.\test.ps1 usbeth` | **USB-Ethernet bench** (prints `USB ETH TARGET PASS`). What it covers, the records and the traps: [testing/usbeth.md](testing/usbeth.md) | main fw on DUT + the adapter on the USB connector + Pi `eth-bench`/`eth-bench-usb` profile |
| `.\test.ps1 espnetlink` | **ESPNetLink LTE-dongle bench** (prints `ESPNETLINK TARGET PASS`). What it covers, the records and the traps: [testing/espnetlink.md](testing/espnetlink.md) | main fw on DUT + the espnetlink on the USB connector (usb_host_manager + usb_acm_cli enabled) |
| `.\test.ps1 stackaudit` | **Stack + memory audit, both halves**. What it covers, the records and the traps: [testing/stackaudit.md](testing/stackaudit.md) | static: local build; runtime: main fw on DUT + rpi001 |
| `.\test.ps1 sleep` | **Sleep-mode HIL** (prints `SLEEP BENCH PASS`, ~14 min). What it covers, the records and the traps: [testing/sleep.md](testing/sleep.md) | PSU (the FTDI port, COM50 today) + rpi001 |
| `bash tools/testbench/pi/bench_ap_net.sh up` | **bench_ap: the bench's home WiFi as an instrument**. What it covers, the records and the traps: [testing/bench_ap_net.md](testing/bench_ap_net.md) | rpi001 + the P4 board (both USB ports) |
| `.\test.ps1 sleepmatrix` | **Sleep robustness matrix** (prints `SLEEP MATRIX PASS`, ~90 min). What it covers, the records and the traps: [testing/sleepmatrix.md](testing/sleepmatrix.md) | PSU + rpi001 (+ betty for the VPN legs) |
| `.\test.ps1 conserve` | **Counter-conservation benches** (CAN / USB-NCM / WiFi → `* CONSERVATION PASS`): exactly-N accounting against a known generator, the §7 performance-truth net | PCAN; NCM leg needs the device-role flip |
| `.\test.ps1 autobaud` | **CAN listen before talk, auto bit rate and the autopid bus guard** (prints `CAN AUTOBAUD PASS`, ~9 min). What it covers, the records and the traps: [testing/autobaud.md](testing/autobaud.md) | PCAN on the DUT's bus + the ECU simulator (USB-NCM 192.168.8.1) + rpi001 (tunnel) |
| `.\test.ps1 profilee2e` | **A published vehicle profile end to end** (prints `AUTOPID PROFILE E2E PASS`, ~5 min). What it covers, the records and the traps: [testing/profilee2e.md](testing/profilee2e.md) | PC + PCAN_USBBUS2 on the DUT bus + the ECU simulator; the DUT on the bench AP, the PSU above the sleep voltage; 2026-10-28: the test route answered `chip busy (monitor/update)` for 2102 and 21045 when it started inside a passive filter's monitor window (a one-shot parked the poller and did not wait for the window in flight; the claim fails fast on a held MONITOR by design): `ap_core_scan_pause(true)` now waits up to 3 s for the poller's idle bit, PASS 38 on the fixed build |
| `python tools/testbench/obd/autopid_matrix_bench.py localhost:18081` | **AutoPID matrix: standard + custom + vehicle-specific PIDs and passive filters against the ECU simulator** (prints `AUTOPID MATRIX PASS`, ~7 min). What it covers, the records and the traps: [testing/autopid_matrix_bench.md](testing/autopid_matrix_bench.md) | main fw on DUT + ECU simulator reachable (192.168.8.1), autopid gates toggled and restored by the run |
| `.\test.ps1 wwh` | **OBD over UDS (ISO 27145 WWH-OBD, SAE J1979-2) as an autopid dialect** (prints `WWH OBD PASS`, ~7 min). What it covers, the records and the traps: [testing/wwh.md](testing/wwh.md) |
| `.\test.ps1 j1939` | **The J1939 listener, autopid on it, and active mode on native CAN** (prints `J1939 PASS`, ~3 min). What it covers, the records and the traps: [testing/j1939.md](testing/j1939.md) |
| `.\test.ps1 eutruck` | **An EU truck end to end: WWH-OBD and J1939 on one bus** (prints `EU TRUCK E2E PASS`, ~2.5 min). What it covers, the records and the traps: [testing/eutruck.md](testing/eutruck.md) | PCAN on the DUT's bus + the ECU simulator (USB-NCM 192.168.8.1) + rpi001 (tunnel, broker) |
| `.\test.ps1 crashnote` | **A device that crashed says where, still says it after a power cut, and stops crashing after the third time** (prints `CRASH NOTE PASS`, ~7 min). What it covers, the records and the traps: [testing/crashnote.md](testing/crashnote.md) | DUT console + PSU + `ssh rpi001` |
| `.\test.ps1 vpnpause` | **The VPN pauses on networks marked Pause VPN** (prints `VPN PAUSE PASS`, ~4 min). What it covers, the records and the traps: [testing/vpnpause.md](testing/vpnpause.md) | DUT on the bench AP + the paired dongle + rpi001 (`wg-bench`) + the DUT console for p3 |
| `.\test.ps1 vpnroutes` | **The split tunnel: only the listed networks go through WireGuard** (prints `VPN ROUTES PASS`, ~2 min). What it covers, the records and the traps: [testing/vpnroutes.md](testing/vpnroutes.md) | DUT on the bench AP + rpi001 (`wg-bench`, the receiver) |
| `.\test.ps1 slcan` | **slcan over TCP, and the trap first** (prints `SLCAN BRIDGE PASS`, ~3 min). What it covers, the records and the traps: [testing/slcan.md](testing/slcan.md) | PCAN on the DUT bus + rpi001 (python-can, sudo for slcand) |
| `.\test.ps1 j2534auto` | **A tester states the bit rate on a silent bus** (prints `J2534 AUTO BAUD PASS`, ~4 min). What it covers, the records and the traps: [testing/j2534auto.md](testing/j2534auto.md) | PCAN on the DUT bus + the simulator answering 7DF + rpi001 |
| `.\test.ps1 j2534std` | **A standard J2534 tool through the PassThru server** (prints `J2534 STD TOOL PASS`, ~1 min). What it covers, the records and the traps: [testing/j2534std.md](testing/j2534std.md) | PCAN on the DUT bus + the simulator answering 7E0, 7E1 and 7DF + rpi001 (+ the DLL installed and the 32-bit Python for leg D) |
| `python tools/testbench/system/autopid_names_bench.py [--wican auto] [--dut 10.42.1.194]` | **A config.json with a parameter name twice** (prints `AUTOPID NAMES PASS`, ~45 s). What it covers, the records and the traps: [testing/autopid_names_bench.md](testing/autopid_names_bench.md) | PC + rpi001 + DUT (console) |
| `python tools/testbench/obd/autopid_init_leak_bench.py [--dut localhost:18081] [--pcan PCAN_USBBUS2] [--cycles 4] [--period-ms 5000]` | **A custom row's init does not leak into the standard rows** (prints `AUTOPID INIT LEAK PASS`, ~2 min). What it covers, the records and the traps: [testing/autopid_init_leak_bench.md](testing/autopid_init_leak_bench.md) | PC + PCAN_USBBUS2 on the DUT bus + the ECU simulator; the DUT on the bench AP, the PSU above the sleep voltage; 2026-10-28: the bench moved to the 2026-10-24 frame of reference (the reply as printed with headers on: B3 for the first data byte of a single-frame reply, the extended-addressed odometer at [B5:B7]); on the stale numbering it read 13698817 for 123456 and 771 for RPM 800, the firmware was right; PASS again that evening on the moved numbering (the raw check takes the printed header) |
| `python tools/testbench/obd/autopid_bounds_bench.py [--dut localhost:18081] [--watch 20]` | **A full-scale reading is never dropped by its own range** (prints `AUTOPID BOUNDS PASS`, ~1.5 min). What it covers, the records and the traps: [testing/autopid_bounds_bench.md](testing/autopid_bounds_bench.md) | PC + the ECU simulator; the DUT on the bench AP, the PSU above the sleep voltage; 2026-10-28: the bench moved to the 2026-10-24 frame of reference (the table hands out B3*100/255 now, the custom rows read B3); on the stale numbering the custom rows read 18.43 (byte 2F, the PID echo), the firmware was right; PASS again that evening on the moved numbering (the raw check takes the printed header) |
| `python tools/testbench/obd/autopid_fp_forget_bench.py [--dut localhost:18081]` | **A car without a readable VIN can be forgotten, renamed and given a profile from the web UI** (prints `AUTOPID FP FORGET PASS`, ~2.5 min). What it covers, the records and the traps: [testing/autopid_fp_forget_bench.md](testing/autopid_fp_forget_bench.md) | PC + the ECU simulator (its default profile active, VIN `1WCAN0FW0P0000001`, its car in the vehicle store or learned by the bench); the DUT on the bench AP, the PSU above the sleep voltage; 2026-10-28: a precondition for a folded face on the VIN car: the new-car bench's fold-in had left the fixture car's fingerprint as a face and the detection then recognised it instead of forking (1 car, no fp: entry); PASS again that evening once the face was cleared (46 checks with the precondition) |
| `python tools/testbench/obd/autopid_profile_first_bench.py [--dut localhost:18081]` | **The car named before the probe: a new car stored under its profile, a car that answers nothing standard confirmed by that profile, a car...** (prints `AUTOPID PROFILE FIRST PASS`, ~3 min). What it covers, the records and the traps: [testing/autopid_profile_first_bench.md](testing/autopid_profile_first_bench.md) | PC + the ECU simulator (its default profile active, its car in the vehicle store or learned by the bench); the DUT on the bench AP, the PSU above the sleep voltage |
| `python tools/testbench/obd/autopid_newcar_bench.py [--dut localhost:18081]` | **A car WiCAN was not set up for: the bar, and the fold-in that brings the chosen rows back** (prints `AUTOPID NEWCAR PASS`, ~4 min). What it covers, the records and the traps: [testing/autopid_newcar_bench.md](testing/autopid_newcar_bench.md) | PC + rpi001 + the DUT + the ECU simulator (its default profile active, its car current in the store); 2026-10-28: the close puts /data/autopid/vehicles.json and the VIN car's file back as read at n0 and reloads them (a restart), so the fold-in's face does not outlive the bench; PASS 26 checks that evening with the close proven: the files put back, no face left |
| `python tools/testbench/vpn/vpn_migrate_bench.py [--dut localhost:18084] [--dut-ip 10.42.1.194] [--pi rpi001] [--only m1,m2] [--logdir DIR]` | **The VPN settings migration routes the tunnel address's network** (prints `VPN MIGRATE PASS`, ~5 min). What it covers, the records and the traps: [testing/vpn_migrate_bench.md](testing/vpn_migrate_bench.md) | DUT on the bench AP through rpi001 |
| `python tools/testbench/vpn/vpn_words_bench.py [--dut localhost:18085] [--dut-ip 10.42.1.194] [--pi rpi001] [--pi-ip 10.42.1.1] [--port 8197] [--only w1,w2] [--logdir DIR]` | **The HA webhook push carries vpn_manager's own state word** (prints `VPN WORDS PASS`, ~10 min). What it covers, the records and the traps: [testing/vpn_words_bench.md](testing/vpn_words_bench.md) | DUT on the bench AP through rpi001; wg-bench on the Pi |
| `python tools/testbench/system/factory_flash_bench.py [--restore] [--backup auto\|<ip>\|none]` | **A unit that ran the factory firmware, flashed with v6 without an erase** (prints `FACTORY FLASH PASS`, ~6 min). What it covers, the records and the traps: [testing/factory_flash_bench.md](testing/factory_flash_bench.md) | DUT console + PSU + rpi001 (wtest1 for the restore) |
| `python tools/testbench/system/ota_from_factory_bench.py --image <v6.bin> --legacy-dir <the v4.51p release zip, extracted> [--backup auto\|<ip>\|none] [--downgrade] [--skip-flash --ssid WiCAN_<id>] [--downgrade-only --ssid WiCAN_<id>]` | **A unit on the factory firmware updated to v6 the way a user does it: the v6 image through the legacy web UI's firmware page, and the way...** (prints `OTA FROM FACTORY PASS`, ~11 min). What it covers, the records and the traps: [testing/ota_from_factory_bench.md](testing/ota_from_factory_bench.md) | DUT console + PSU + rpi001 (wtest1 / wint0 on the DUT's AP) |
| `.\test.ps1 backup` | **The configuration backup that carries everything** (prints `CONFIG BACKUP PASS`, ~9 min). What it covers, the records and the traps: [testing/backup.md](testing/backup.md) | DUT on the bench AP through rpi001; a card in the DUT for the SD leg (SKIPPED without) |
| `python tools/testbench/system/restart_mqtt_quiet_bench.py --dut localhost:18081 [--restarts 2]` | **A planned restart with the MQTT broker connected leaves no error line** (prints `RESTART MQTT QUIET PASS`, ~1.5 min). What it covers, the records and the traps: [testing/restart_mqtt_quiet_bench.md](testing/restart_mqtt_quiet_bench.md) |
| `python tools/testbench/system/partition_migrate_bench.py [--legacy-table <partition-table.bin of the v4.51p release>] [--dut 10.42.1.194]` | **The partition table rewrite, measured** (prints `PARTITION MIGRATE PASS`, ~2 min). What it covers, the records and the traps: [testing/partition_migrate_bench.md](testing/partition_migrate_bench.md) | DUT console + PSU + rpi001 (the lease) |
| `python tools/testbench/system/downgrade_bench.py --image <v6.bin> [--build build] [--legacy-dir <the v4.51p zip, extracted>] [--resume-up --ssid WiCAN_<id>]` | **A unit on v6 with v6's own partition table taken back to the legacy firmware through v6's OTA page, and forward again through the legacy...** (prints `DOWNGRADE PASS`, ~10 min). What it covers, the records and the traps: [testing/downgrade_bench.md](testing/downgrade_bench.md) | DUT console + PSU + rpi001 (wtest1 / wint0 on the DUT's AP) |
| `.\test.ps1 dwc2` | **DWC2 kill-vs-ISR race hammer** (prints `DWC2 HAMMER PASS`, ~10 min). What it covers, the records and the traps: [testing/dwc2.md](testing/dwc2.md) | espnetlink dongle on the USB host port |
| `python tools\testbench\wifi\wican_fresh_bench.py --wican COMx --psu COMz` | **WiCAN Pro out-of-the-box scenario** (prints `WICAN FRESH PASS`, ~5 min). What it covers, the records and the traps: [testing/wican_fresh_bench.md](testing/wican_fresh_bench.md) | WiCAN console + PSU + rpi001 (`wtest0` hotspot, `wtest1` free) |
| `python tools\testbench\wifi\espnetlink_fresh_bench.py --wican COMx --dongle COMy --psu COMz [--mode usb_rndis\|wifi_modem]` | **ESPNetLink out-of-the-box scenario** (prints `ESPNL FRESH PASS`, ~8 min). What it covers, the records and the traps: [testing/espnetlink_fresh_bench.md](testing/espnetlink_fresh_bench.md) | both consoles + PSU + rpi001 (`wtest1` free) |
| `python tools\testbench\wifi\espnetlink_mode_bench.py` | **ESPNetLink transport-mode switching** (prints `ESPNL MODE PASS`, ~15 min). What it covers, the records and the traps: [testing/espnetlink_mode_bench.md](testing/espnetlink_mode_bench.md) | DUT console (CH344 channel B, COM254 today) + rpi001 (`espnl-client`) + paired dongle |
| `python tools\testbench\wifi\espnetlink_roam_bench.py` | **ESPNetLink roaming scenario** (prints `ESPNL ROAM PASS`, ~8 min). What it covers, the records and the traps: [testing/espnetlink_roam_bench.md](testing/espnetlink_roam_bench.md) | DUT console (CH344 channel B, COM254 today) + rpi001 (wtest0 borrowed for the run, `wican-bench` restored after) + paired dongle |
| `python tools\testbench\wifi\espnetlink_garage_bench.py [--mode usb_rndis\|wifi_modem] [--vpn A\|B\|none] [--only g1,...] [--console auto\|COMx\|none] [--psu auto\|none]` | **The garage story with the ESPNetLink** (prints `ESPNL GARAGE PASS`, ~35 min). What it covers, the records and the traps: [testing/garage.md](testing/garage.md) | PC + rpi001 (the P4 daemon up, `wican-bench-usb` active) + betty (ssh, `sudo wg`) + the PSU + the dongle on the connector |
| `python tools\testbench\usb\espnetlink_repair_hammer.py [--n 8] [--spacing 20] [--server betty\|none]` | **The repair hammer** (prints `ESPNL REPAIR HAMMER PASS`, ~8 min). What it covers, the records and the traps: [testing/espnetlink_repair_hammer.md](testing/espnetlink_repair_hammer.md) | DUT at home on the bench AP + the dongle on the connector in a USB mode + rpi001 + betty |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_espnl_easy.mjs` | **The ESPNetLink easy setup over the mock** (prints `ESPNL EASY PROBE PASS`). What it covers, the records and the traps: [testing/probe_espnl_easy.md](testing/probe_espnl_easy.md) | PC (Chrome) |
| `.\test.ps1 espnleasy` | **The easy-setup surfaces on the DUT** (prints `ESPNL EASY BENCH PASS`, ~3 min). What it covers, the records and the traps: [testing/espnleasy.md](testing/espnleasy.md) | PC + rpi001 + P4 |
| `.\test.ps1 espnlapn` | **The carrier settings relay in the USB modes** (prints `ESPNL APN RELAY PASS`, ~2 min). What it covers, the records and the traps: [testing/espnlapn.md](testing/espnlapn.md) | PC + rpi001 + the dongle on the connector in a USB mode with `usb_acm_cli.console` on |
| `python tools\testbench\wifi\espnetlink_ttff_bench.py [--n 6] [--bench-ap off\|keep]` | **Cold GPS time-to-first-fix series on the ESPNetLink** (prints `ESPNL TTFF PASS`). What it covers, the records and the traps: [testing/espnetlink_ttff_bench.md](testing/espnetlink_ttff_bench.md) | PSU + rpi001 (wtest1 free, `espnl-client` with the dongle's key) + the paired dongle on the WiCAN's connector + the P4 bench AP daemon for `--bench-ap off` |
| `python tools\testbench\ha\ha_webhook_gate_bench.py --dut <ip>[,<ip2>] (--pi rpi001 \| --serve 8199 --local-ip <own ip> \| --receiver <url>)` | **HA webhook poster with autopid OFF** (prints `HA WEBHOOK GATE PASS`, ~1.5 min). What it covers, the records and the traps: [testing/ha_webhook_gate_bench.md](testing/ha_webhook_gate_bench.md) | main fw on DUT + a receiver host; no vehicle needed |
| `python tools\testbench\ha\ha_webhook_sta_ip_bench.py --dut <addr[:port]> (--pi rpi001 [--pi-ip 10.42.1.1] \| --serve 8199 --local-ip <own ip> \| --receiver <url>)` | **The HA webhook push carries the station address** (prints `HA WEBHOOK STA IP PASS`, ~1 min). What it covers, the records and the traps: [testing/ha_webhook_sta_ip_bench.md](testing/ha_webhook_sta_ip_bench.md) | main fw on DUT on a station network + a receiver host (the Pi); no vehicle needed |
| `python3 tools/testbench/ha/ha_lte_push_bench.py --capture 8199` | **HA push over LTE via the ESPNetLink dongle + the GPS block** (prints `HA LTE PUSH PASS`, ~2 min). What it covers, the records and the traps: [testing/ha_lte_push_bench.md](testing/ha_lte_push_bench.md) | main fw on DUT + paired dongle with LTE/GPS + rpi001 (`wtest1` free; VPN mode: the route up + the bench HA) |
| `bash tools/testbench/vpn/wg_ha_route.sh up \| status \| down [--wipe]` | **HA-over-VPN bench route** (~3 min). What it covers, the records and the traps: [testing/wg_ha_route.md](testing/wg_ha_route.md) | betty (root ssh alias) + rpi001 + DUT on the hotspot |
| `.\test.ps1 logsinks` | **External log sinks conservation** (prints `LOG SINKS BENCH PASS`). What it covers, the records and the traps: [testing/logsinks.md](testing/logsinks.md) | main fw on DUT + rpi001 + SD card + ws_cli enabled |
| `python tools\testbench\obd\autopid_profile_bench.py <base-url> [--only SUBSTR] [--wide N] [--skip-wide]` | **AutoPID vehicle-profile import** (prints `AUTOPID PROFILE PASS`, ~2 min). What it covers, the records and the traps: [testing/autopid_profile_bench.md](testing/autopid_profile_bench.md) | main fw on DUT (tunnel or Pi-side address) + ECU simulator on the bus for the wide leg; 2026-10-28: the wide leg needs AutoPID polling (autopid.enabled on): with it off the 24 parameters stay null (0/24; the bench's own hint about a foreign chip state was wrong), 24/24 with it on, w00 = 6 = the length byte in the 2026-10-24 frame of reference; 87/87 profiles accepted; PASS that evening with AutoPID on |
| `python3 tools/testbench/usb/j2534_transport_probe.py <dut_ip>` | **J2534 transport probe** (~10 s). What it covers, the records and the traps: [testing/j2534_transport_probe.md](testing/j2534_transport_probe.md) | main fw on DUT + rpi001 + the ECU simulator answering 7DF |
| `python3 tools/testbench/usb/j2534_bench.py <dut_ip> --reflash [--tx 7E2 --rx 7EA]` | **J2534 ISO15765 reflash preamble over native ISO-TP** (prints `J2534 REFLASH PASS`, ~10 s). What it covers, the records and the traps: [testing/j2534_bench.md](testing/j2534_bench.md) | main fw on DUT + rpi001 + PCAN on the bus |
| `python tools/testbench/usb/j2534_tp20_gate_bench.py --dut localhost:18081 --j2534 localhost:18534` | **Raw CAN forwards whatever the tool sends: the VW TP 2.0 frame shapes** (prints `TP20 RAW CAN PASS`, ~75 s). What it covers, the records and the traps: [testing/j2534_tp20_gate_bench.md](testing/j2534_tp20_gate_bench.md) | PCAN on the DUT bus; the simulator for the 7DF leg |
| `python tools/testbench/usb/j2534_tp20_responder.py --blocks 240 --t3-ms 1` | **The speed a TP 2.0 tool gets through the DLL** (prints `TESTER DONE`, ~16 s). What it covers, the records and the traps: [testing/j2534_tp20_responder.md](testing/j2534_tp20_responder.md) | PCAN on the DUT bus; `j2534_server.enabled`; `usb_host_manager.role=device` with `device_class` ncm or cdc for the USB legs |
| `python tools/testbench/usb/j2534_vpw_bench.py <dut_ip> [--port 6809]` | **J2534 J1850 VPW (GM Class 2)** (prints `J2534 VPW PASS`, ~5 s). What it covers, the records and the traps: [testing/j2534_vpw_bench.md](testing/j2534_vpw_bench.md) | main fw on DUT + rpi001; a J1850 VPW node for the full PASS |
| `python tools/testbench/obd/eeprom_guard_bench.py <dut_ip> [--port 35000]` | **OBD chip EEPROM guard** (prints `EEPROM GUARD PASS`, ~10 s). What it covers, the records and the traps: [testing/eeprom_guard_bench.md](testing/eeprom_guard_bench.md) | main fw on DUT + rpi001 + the ELM bridge |
| `.\test.ps1 obdfw [-SkipSleep]` | **OBD chip firmware update** (prints `OBD FW PASS`, ~6 min). What it covers, the records and the traps: [testing/obdfw.md](testing/obdfw.md) | main fw on DUT + rpi001 + the ws_cli bridge |
| `.\test.ps1 canselftest` | **CAN self-test and chip diagnostics** (prints `CAN SELFTEST PASS`, ~3 min). What it covers, the records and the traps: [testing/canselftest.md](testing/canselftest.md) | main fw on DUT + rpi001 + the ECU simulator on the bus |
| `.\test.ps1 connections` | **Connections: bridges and their jacks** (prints `CONNECTIONS PASS`, ~90 s). What it covers, the records and the traps: [testing/connections.md](testing/connections.md) | main fw on DUT + rpi001; Chrome and the webui_preview Playwright on the PC for the page leg |
| `python tools/testbench/obd/run_be.py <base-url> tools/testbench/obd/uds_bindings.be [--tx 7E2 --rx 7EA]` | **Script UDS bindings** (prints `SCRIPT UDS PASS`, ~3 s). What it covers, the records and the traps: [testing/be_uds_bindings.md](testing/be_uds_bindings.md) | main fw on DUT + the ECU simulator |
| `python tools/testbench/obd/run_be.py <base-url> tools/testbench/obd/reflash.be --tx 7E2 --rx 7EA --timeout 90` | **Script-driven ECU flash** (prints `REFLASH OK`, ~2 s). What it covers, the records and the traps: [testing/be_reflash.md](testing/be_reflash.md) | main fw on DUT + PCAN on the bus + SD card |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_uds.mjs && node smoke.mjs` | **UDS Tool page probe** (prints `UDS PROBE PASS`). What it covers, the records and the traps: [testing/probe_uds.md](testing/probe_uds.md) | PC, node + jsdom |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_automate.mjs` | **Automate page probe** (prints `PROBE PASS`). What it covers, the records and the traps: [testing/probe_automate.md](testing/probe_automate.md) | PC, node + jsdom |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_rules.mjs` | **Rules builder probe** (prints `RULES PROBE PASS`). What it covers, the records and the traps: [testing/probe_rules.md](testing/probe_rules.md) | PC, node + jsdom |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_connections.mjs` | **Connections card probe** (prints `CONNECTIONS PROBE PASS`). What it covers, the records and the traps: [testing/probe_connections.md](testing/probe_connections.md) | PC, node + jsdom |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_diag.mjs` | **OBD chip and CAN self-test cards probe** (prints `DIAG PROBE PASS`). What it covers, the records and the traps: [testing/probe_diag.md](testing/probe_diag.md) |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_power.mjs` | **Power Saving page probe** (prints `POWER PROBE PASS`). What it covers, the records and the traps: [testing/probe_power.md](testing/probe_power.md) | PC, node + jsdom |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_sleep_soon.mjs` | **Sleep countdown bar and Keep awake probe** (prints `SLEEP SOON PROBE PASS`, ~60 s). What it covers, the records and the traps: [testing/probe_sleep_soon.md](testing/probe_sleep_soon.md) | PC, node + jsdom |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_setup.mjs` | **Quick Setup wizard probe** (prints `ALL PASS`). What it covers, the records and the traps: [testing/probe_setup.md](testing/probe_setup.md) | PC, node + jsdom |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_reconnect.mjs` | **Quick Setup reconnect screen probe** (prints `RECONNECT PROBE PASS`, ~75 s). What it covers, the records and the traps: [testing/probe_reconnect.md](testing/probe_reconnect.md) | PC, node + jsdom |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_wifi_try.mjs` | **The connection test on the WiFi step, over the mock** (prints `WIFI TRY PROBE PASS`, ~90 s). What it covers, the records and the traps: [testing/probe_wifi_try.md](testing/probe_wifi_try.md) | PC, node + jsdom |
| `python tools/testbench/wifi/wifi_try_bench.py [--wican auto] [--dut 10.42.1.194]` | **The connection trial on hardware** (prints `WIFI TRY PASS`, ~2.5 min). What it covers, the records and the traps: [testing/wifi_try_bench.md](testing/wifi_try_bench.md) | PC + rpi001 + DUT (console) |
| `node tools/testbench/wifi/reconnect_screen_bench.mjs [out dir] [--bind <LAN ip>] [--dut <ip>]` | **The reconnect screen on the real device** (prints `RECONNECT DEVICE PASS`, ~45 s). What it covers, the records and the traps: [testing/reconnect_screen_bench.md](testing/reconnect_screen_bench.md) | PC (Chrome) + rpi001 + DUT |
| `node test-reports/logs/usb_20261007/usb_real.mjs [out dir]` | **The USB step on the real device with the bench ESPNetLink** (prints `USB REAL PASS`, ~70 s). What it covers, the records and the traps: [testing/usb_real.md](testing/usb_real.md) | PC (Chrome) + rpi001 + DUT + the ESPNetLink on the connector |
| `node tools/testbench/obd/std_choice_bench.mjs [out dir] [--dut 10.42.1.194] [--legs plain,redetect,skip,reload,reboot]` | **The standard PIDs stay the user's choice on every Quick Setup path** (prints `STD CHOICE PASS`, ~10 min). What it covers, the records and the traps: [testing/probe_std_choice.md](testing/probe_std_choice.md) | PC (Chrome) + rpi001 + DUT + the ECU simulator |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_newcar.mjs` | **The new-car bar** (prints `NEWCAR PROBE PASS`). What it covers, the records and the traps: [testing/probe_newcar.md](testing/probe_newcar.md) | PC only (jsdom) |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_profile_filters.mjs` | **A vehicle profile's passive CAN filters, over the mock** (prints `PROFILE FILTERS PROBE PASS`). What it covers, the records and the traps: [testing/probe_profile_filters.md](testing/probe_profile_filters.md) |
| `node tools/testbench/obd/profile_filters_bench.mjs [out dir] [--dut 10.42.1.194] [--profile "Nissan: Leaf ZE1 (2018-)"] [--other "Nissan: Leaf ZE0/AZE0 (2011-2017)"]` | **A vehicle profile's passive CAN filters, imported from the web UI on the real device** (prints `PROFILE FILTERS PASS`, ~70 s). What it covers, the records and the traps: [testing/profile_filters_bench.md](testing/profile_filters_bench.md) |
| `node test-reports/logs/usb_20261007/usb_eth_real.mjs [out dir] [wired address]` | **The USB step on the real device with the USB Ethernet adapter** (prints `USB ETH REAL PASS`, ~30 s). What it covers, the records and the traps: [testing/usb_eth_real.md](testing/usb_eth_real.md) | PC (Chrome) + DUT + the ASIX adapter on the connector, its cable in a router on the PC's LAN |
| `.\test.ps1 usbgps` | **A USB GPS receiver is read by default** (prints `USB GPS PASS`, ~6 min). What it covers, the records and the traps: [testing/usbgps.md](testing/usbgps.md) | DUT on the bench AP through rpi001 + the u-blox on the connector (near a window for the fix legs) + the Pi for the HA receiver |
| `.\test.ps1 sdformat [-SdFormat]` | **The SD card format from the API** (prints `SD FORMAT PASS`, ~3 min). What it covers, the records and the traps: [testing/sdformat.md](testing/sdformat.md) | DUT on the bench AP through rpi001 + a card in the slot (one WiCAN cannot read, and `-SdFormat`, for the format legs) |
| `.\test.ps1 usbdetach` | **A warm restart in the USB device role drops off the bus first** (prints `USB DETACH PASS`, ~6 min). What it covers, the records and the traps: [testing/usbdetach.md](testing/usbdetach.md) | DUT USB-C cabled to this PC (the preflight's `connector -> PC`) + the DUT on the bench AP through rpi001 for the flip (or the device role set in the UI) + the PSU on the FTDI port for the restore |
| `.\test.ps1 abrp` | **ABRP against the real Iternio API** (prints `ABRP LIVE PASS`, ~5 min). What it covers, the records and the traps: [testing/abrp.md](testing/abrp.md) | DUT on the bench AP through rpi001 (+ the LAN router next to the Pi for the host routes); the credentials file |
| `.\test.ps1 logclock` | **Log timestamps that survive the clock's history** (prints `LOG CLOCK PASS`, ~10 min). What it covers, the records and the traps: [testing/logclock.md](testing/logclock.md) | DUT on the bench AP through rpi001 (ws_cli up) + the Pi (sudo, UDP 123) + the OWON PSU |
| `.\test.ps1 fwupdate` | **Firmware updates pulled from a manifest** (prints `FW UPDATE PASS`, ~20 min). What it covers, the records and the traps: [testing/fwupdate.md](testing/fwupdate.md) | PC + rpi001 (the bench CA at `~/wican-tls`, a server certificate for the hotspot address 10.42.1.1 signed by it at `~/wican-tls-fwbench` (made 2026-10-19; the bench falls back to `~/wican-tls`, whose certificate names 10.42.0.1 only), python3 on the Pi); the DUT on the bench AP running this build with the `cli <-> ws_cli` bridge of the canonical settings; the dongle rig for `--cellular` (downloads refuse over LTE, no override: Ali 2026-10-19) |
| `.\test.ps1 release` | **THE RELEASE TEST: the fast pre-release gate on the exact release image** (prints `RELEASE TEST PASS`, about 6 min). What it covers, the records and the traps: [testing/release.md](testing/release.md) | PC + rpi001 (the broker, the receiver, python3 + websocket), the DUT's console port free (the bench flashes and tails it), the PSU, the ECU simulator answering on the bus; no vehicle, no dongle; 2026-10-28 on the issue-1023 branch build (phase 2, the lean cards, the self-test restore fix): PASS 59 (heap floor 39740 B, 137 mA) on the second run; the first run's r5 broke the ws_obd pairing (every answer one command late) because `ws_live_test.py` paused the autopid groups from `/api/autopid/config`, which lists only the groups written in the table, while the bench DUT's 16 standard rows sit in the implicit default group: the test now pauses the runtime groups from `/api/autopid` and waits for the poller to go idle instead of sleeping 1.5 s; 6.0.0-beta.6 (tag v6.0.0p-beta.6 at d8388d2, clean build, 2026-10-28 22:31): PASS 59 on the exact image, sha256 7fff2882958c683a94f3798874ad26ca6b5f2ca05e7c8f443f2a604b1314942a, ELF 36e58abfd0bc9940 (archived under internal/elf), 305 s, heap floor 39740 B, 143 mA; record test-reports/release/7fff2882...json |
| `python3 -u field_check_pi.py <from> <to> [dut-ip] [channel]` | **THE FIELD CHECK: the release checklist's last step from the bench** (about 3 min). What it covers, the records and the traps: [testing/field_check_pi.md](testing/field_check_pi.md) | rpi001 (python3), the DUT on the P4, the console port for the flash, the lab gateway's internet |
| `.\test.ps1 espnlota` | **The ESPNetLink dongle updated through the WiCAN PRO** (prints `ESPNL OTA PASS`, ~12 min). What it covers, the records and the traps: [testing/espnlota.md](testing/espnlota.md) | PC + rpi001 (as for `fwupdate`); the DUT on the bench AP running this build; the ESPNetLink on the connector in `usb_rndis` mode with a WiFi uplink for the WiCAN (downloads never go over LTE); a dongle build in `C:\Users\Ali\Desktop\Projects\ESPNetLink_Project\firmware\espnetlink-fw\build` or `--dongle-image` |
| `python tools/testbench/system/heap_probe.py --dut localhost:18081 [--settle 150] [--only found,link_off] [--logdir DIR]` | **Internal-heap probe** (prints `HEAP PROBE DONE`, ~15 min). What it covers, the records and the traps: [testing/heap_probe.md](testing/heap_probe.md) | DUT through rpi001 (the tunnel) |
| `ssh rpi001 'curl -s --compressed -D - -o /dev/null http://10.42.1.194/ui/setup.js'` | **The UI after a flash**. What it covers, the records and the traps: [testing/the_ui_after_a.md](testing/the_ui_after_a.md) | rpi001 + DUT |
| `bash /tmp/trial_gap_bench.sh <channel hint: 11 or 0> [ssid] [NetworkManager profile holding its psk]` | **The WiFi trial as the phone on the access point sees it**. What it covers, the records and the traps: [testing/trial_gap_bench.md](testing/trial_gap_bench.md) | rpi001 (wtest1 as the phone; the P4 renamed, or the onboard radio, as the second network) + DUT |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_crash_note.mjs && node probe_safemode.mjs` | **The crash on the Status page, and the safe mode page** (prints `ALL PASS`). What it covers, the records and the traps: [testing/probe_crash_note.md](testing/probe_crash_note.md) | PC, node + jsdom |
| `python tools/testbench/wifi/quick_setup_bench.py [--wican auto] [--psu auto] [--no-erase] [--no-vehicle]` | **Quick Setup end to end on hardware** (prints `QUICK SETUP PASS`, ~15 min). What it covers, the records and the traps: [testing/quick_setup_bench.md](testing/quick_setup_bench.md) | DUT + PSU + rpi001 + ECU simulator |
| `python tools\testbench\system\data_destinations_bench.py --dut 10.42.1.194 --pi rpi001 --pi-ip 10.42.1.1 [--ap-if wtest1] [--observe 45] [--skip-wifi] [--skip-broker]` | **Data destinations end-to-end** (prints `DATA DEST PASS`, ~8 min). What it covers, the records and the traps: [testing/data_destinations_bench.md](testing/data_destinations_bench.md) | main fw on DUT + rpi001 (hotspot, mosquitto, ~/wican-tls bench CA); no vehicle needed |
| `python tools/testbench/rules_bench.py --base http://localhost:8081` | **Rules-engine bench** (prints `RULES BENCH PASS`). What it covers, the records and the traps: [testing/rules_bench.md](testing/rules_bench.md) | PC + DUT over the tunnel |
| `python tools/testbench/obd/uds_route_bench.py <base-url> [--tx 7E0 --rx 7E8] [--req "22 01 01"] [--min-ok-pct 90]` | **UDS route over the OBD chip** (prints `UDS ROUTE PASS`). What it covers, the records and the traps: [testing/uds_route_bench.md](testing/uds_route_bench.md) | main fw on DUT + the ECU simulator |
| `sudo env PYTHONPATH=/home/meatpi/.local/lib/python3.11/site-packages python3 -u tools/testbench/ble/ble_slcan_bridge_test.py --dut 192.168.0.10 [--flood] [--stage configure\|ble\|restore] [--keep-sta]` | **slcan over BLE** (prints `BLE SLCAN BRIDGE PASS`, ~6 min). What it covers, the records and the traps: [testing/ble_slcan_bridge_test.md](testing/ble_slcan_bridge_test.md) | main fw on DUT + rpi001 (UB500 + bleak + python3-dbus) + the ECU simulator answering 7DF; PCAN on the PC for the companion |
| `.\test.ps1 blehttp` | **The HTTP API over BLE** (prints `BLE API PASS`, ~8 min). What it covers, the records and the traps: [testing/blehttp.md](testing/blehttp.md) | main fw on DUT + rpi001 (UB500 + bleak + python3-dbus) + the ECU simulator answering 7E0 for the UDS leg; SD card for the `/sd` legs **2026-09-22 (protocol v2, notify mode):** the full run FAILED on plumbing only: the configure reboot dropped the DUT to the dongle AP (`configure_dut_back ip=None`), the L0 link gate then failed while the DUT was rebooting, and later the Pi's UB500 went deaf (0 advertisers in a raw `hcitool lescan` while the PC saw 21; USB re-enumeration, firmware reload, bluetoothd restart and `btmgmt power` did not revive it: it needs a re-plug), and the restore stage could not reach the DUT (its `sta_ble_handover` was put back by hand). The v2 client itself is verified by the probes: `ble_http_probe.py` on the Pi (tunnel-only, btmon arbiter: 0 holes in 12 + 12 downloads on the final firmware, host = air = client PDU counts) and the new `ble_http_probe_pc.py` on the PC's Intel adapter (6 x 64 KB + 2 x 512 KB + 6 x 64 KB, byte-exact, 0 holes; bleak/WinRT subscribes for INDICATIONS and refuses a CCCD write, so the PC runs the 5.6 KB/s indication path at its 45 ms interval). After Ali re-seated the dongle: **run 5 (2026-09-22 10:20) = `BLE API PASS` + `BLE STORAGE PASS`** with the WiFi witness alive, configure and restore stages OK. Downloads from the Pi in notify mode: 22-27 KB/s on /data and /sd (2.2-2.6 KB/s on indications the day before, same link), 512 KB byte-exact, SHA-256 witness OK, 0 holes over 2829 notifications (btmon count = device count), 0 tx_timeouts / rx_overflow, `out=notify`; uploads stay at ~3 KB/s (bleak/D-Bus WriteValue spacing, a Pi-client limit). Two pre-existing WARNs: the concurrent WiFi upload answered 404 instead of 503 (route path), and the simulator no longer serves DID F190 (`7F 22 31`; F187 positive). Regression the same morning: `.\test.ps1 blej2534` = `BLE J2534 PASS`, `RESULT: PASS` (the indicate-only FFF5/FFF6 channel on the changed GATT and channel layer) and `.\test.ps1 blecanraw` = `BLE CAN RAW PASS`, `RESULT: PASS` (the FFF1 data pipe). Runs 2-4 were plumbing: the test script's own v2 counter bug in the busy leg (the device's `400 hole` was correct), a crash on an unreachable witness (both fixed), and a `DUT unreachable` preflight while the STA sat on the dongle AP (the driver then hung 30 min; killed). **2026-10-31 on the Lean Bluetooth memory profile (ble_manager v5, the final image): `BLE API PASS` + `BLE STORAGE PASS` (609 s; downloads 30 KB/s on /data and /sd in notify mode, uploads 3 KB/s, 0 E lines, restore clean, the two WARNs as before).** **The same evening on build 1 (the BLE controller's code in flash, the erase caveat of that option under test): `BLE API PASS` + `BLE STORAGE PASS`, `RESULT: PASS`: the 512 KB uploads to /data (LittleFS erases under the live link) and to /sd byte-exact, downloads 24 to 26 KB/s on /data and 19 to 24 KB/s on /sd, 0 link drops, 0 E lines. Two plumbing FAILs in that run and none in the legs: `configure_dut_back` and `restore_dut_back` (`ip=None`, the DUT's station slow to re-associate after each reboot, the day's P4 flake), and a first run that day FAILed its three list / empty / rmdir checks on a leftover `/data/blebench/blob.bin` from the simulator bench's tunnel uploads (removed by hand; the simulator bench should delete its own file).** |
| `.\test.ps1 blej2534` | **J2534 PassThru over BLE** (prints `BLE J2534 PASS`, ~5 min). What it covers, the records and the traps: [testing/blej2534.md](testing/blej2534.md) | main fw on DUT + rpi001 (UB500 + bleak) + the ECU simulator answering 7DF / 7E0 |
| `.\test.ps1 blecanraw` | **Binary CAN records over BLE** (prints `BLE CAN RAW PASS`, ~3 min). What it covers, the records and the traps: [testing/blecanraw.md](testing/blecanraw.md) | main fw on DUT (`can_manager.enabled`) + rpi001 (UB500 + bleak) + the ECU simulator on the bus with `broadcast_enabled` |
| `.\test.ps1 blephy` | **BLE 5 as settings: PHY + advertising sets** (prints `BLE PHY PASS`, ~9 min). What it covers, the records and the traps: [testing/blephy.md](testing/blephy.md) |
| `.\test.ps1 blethru` | **BLE throughput with a controlled central** (prints `BLE THROUGHPUT PASS`, ~10 min). What it covers, the records and the traps: [testing/blethru.md](testing/blethru.md) |
| `.\test.ps1 bleram` | **The Bluetooth memory profile** (prints `BLE RAM PASS`, ~8 min). What it covers, the records and the traps: [testing/bleram.md](testing/bleram.md) |
| `.\test.ps1 all` | host + every target app + hil (live/perf are separate: `all` leaves a test app, not the main firmware, on the DUT) | both |

Options: `-Port COM10` (DUT serial; when absent the preflight falls back to the
CH344 quad-UART channel B, then CH342 A, both found by PnP name - see the
physical-setup matrix), `-BenchHost rpi001`, `-DutIp` (default: resolve on the
Pi, the hotspot's DHCP lease for hostname `wican_<DeviceId>` first, then
mDNS minus the DUT's own AP subnet; pass an address to pin it), `-DeviceId 68ee8f5a653d` (the bench
unit), `-NoReport`, `-SkipBenchCheck`.

## The bench preflight (runs first, every kind)

Every run (except `list`) starts with a ~10 s **bench preflight** that verifies the PHYSICAL bench matches what the selected kind needs, fails fast with a specific message when it doesn't, and records the findings in the report's conditions. `-SkipBenchCheck` bypasses it.

The whole section: [testing/preflight.md](testing/preflight.md).

## The system bench (the composed main firmware, end to end)

`tools/testbench/system/system_bench.py` (run ON rpi001, after `erase-flash` + flashing the root `build/`): the real product journey, factory-AP onboarding at `192.168.0.10` → configure wifi + a bridge over `/api` → submit-reboot → STA join → OBD chip + live ECU through the configured TCP bridge → OBD-polling perf...

The whole section: [testing/system_bench.md](testing/system_bench.md).

## BenchBoard: the interactive test dashboard (2026-07-26)

**`C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe tools\benchboard\benchboard.py`** → `http://127.0.0.1:8777/`, a local web UI over the whole bench: catalog of tools + tests from the repo-root `benchboard.toml` (grouped, searchable, per-test parameter forms), live-streamed runs with PASS/FAIL/METRIC...

The whole section: [testing/benchboard.md](testing/benchboard.md).

## Bench layout: `tools/testbench/` (domain folders since 2026-07-26)

| Folder | Contents |
|---|---|
| `lib/` | shared plumbing: `benchlib.py` (timeout policy + RIG/DUT classification), `bench.py`/`dut.py` (pytest fixtures), `wsmin.py`, `owon_psu.py`, `serial_capture.py` |
| `pi/` | the rpi001 rig toolkit (bench-health / recovery ladder / udev roles), installed by `deploy_bench_pi.sh` |
| `actors/` | bus responders/generators the benches spawn: `pcan_obd_ecu.py`, `pcan_uds_ecu.py`, `pcan_dbc_broadcast.py`, `pcan_reflash_ecu.py`, `wg_bench_server.sh` |
| `fixtures/` | test data (DBC files, …) |
| `system/` | composed-firmware benches: `system_bench.py`, stack audit, event manager, data logger, log sinks |
| `can/` | CAN/TWAI + translators: conservation, bus-off recovery, canfan, slcan, DBC, mqtt_can |
| `obd/` | OBD chip / ELM / autopid / DTC / UDS benches (+ `.be` scripts) |
| `ble/` | all BLE legs incl. `obd_ble_bridge_test.py` |
| `wifi/` | WiFi + network-transport legs: conservation, gate, profile A/B, mqtt/ws/cli/socket benches |
| `sleep/` | sleep bench + matrix (+ `SLEEP_MATRIX.md`) |
| `usb/` | USB device/host: NCM conservation, dwc2 hammer, espnetlink, usb_eth, j2534, the device-role restart detach |
| `vpn/` | WG/TS bench targets + the betty soak scripts |
| `ha/` | Home Assistant webhook benches |

`requirements.txt` and `run_host_tests.sh` stay at the root. Scripts still
run standalone (`python3 tools/testbench/<dir>/<script>.py`); sibling
imports are same-dir, shared code is imported from `lib/` via a relative
`sys.path` shim inside each script that needs it.

## Bench rig: control plane, recovery ladder, timeout policy (2026-07-26)

The bench-Pi standardization plan (`docs/bench_pi_standardization_plan.md`) is EXECUTED. What it changed:

The whole section: [testing/bench_rig.md](testing/bench_rig.md).

## One-time setup

- **Dev host:** `pip install -r tools/testbench/requirements.txt` into the IDF
  python env (`C:\Espressif\tools\python\v6.0.2\venv`). On this LAN add
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org`.
- **Bench Pi (rpi001)**: already provisioned; to rebuild from scratch:
  `sudo apt install gcc cmake ninja-build libbsd-dev`, clone ESP-IDF v6.0.2 to
  `~/esp-idf`, run `python3 tools/idf_tools.py install-python-env`, and keep
  the `dnsmasq`/`hostapd` **services disabled** (they break NetworkManager's
  hotspot DHCP; the binaries stay available). BLE = TP-Link UB500 dongle
  (onboard BT disabled), see `testing/bench_rig.md` (the former
  `components/TESTBENCH.md` is gone).
- After a bench session, reflash the main firmware
  (`build\` at repo root → `python -m esptool ... write-flash "@flash_args"`).

## Reaching the DUT's AP (factory or configured)

**Never join the DUT's AP from the dev PC's own WiFi.** The PC's WiFi *is*
its internet uplink (ssh to rpi001 now rides the ethernet control plane,
but the internet doesn't), joining the DUT AP takes the dev session
offline, and Windows auto-roams back to the home profile mid-test anyway
(both bitten 2026-07-20). AP-side access always goes through **rpi001**,
exactly like `system_bench.py` does:

```powershell
# free the internal radio (it hosts the bench hotspot), join, pin the routes
ssh rpi001 "sudo nmcli connection down wican-bench-w0"
ssh rpi001 "sudo nmcli device wifi connect 'WiCAN_<id>' password '@meatpi#' ifname wint0 name wican-dut"
ssh rpi001 "sudo nmcli connection modify wican-dut ipv4.never-default yes ipv4.route-metric 4000 ipv4.routes 192.168.0.10/32"
ssh rpi001 "curl -s http://192.168.0.10/api/status"
# afterwards: delete wican-dut, bring wican-bench-w0 back up
```

(Join on `wint0`, the internal radio: the USB sticks intermittently wedge
as scanners; 2026-07-26 lesson, same reason system_bench onboards there.)

The route pinning matters: the AP subnet `192.168.0.0/24` collides with
LANs the Pi (and the dev PC) may already sit on, an unpinned second /24
blackholes the Pi's own uplink including the ssh session driving it.
When the DUT is already a STA on the bench hotspot, skip the AP entirely
and talk to its `10.42.x.x` lease from the Pi.

## Autopid testing caution (2026-07-21)

**`pause_follow_sleep` is ON by default**: autopid pauses PID REQUESTS
whenever the battery reads below the sleep voltage (default 13.10 V),
legacy `disable_pid_requests` parity. **On the bench this silently
stalls every autopid/OBD-polling leg if the PSU sits below ~13.2 V**
(the classic 12.5 V bench level qualifies!): polls stop, tests time out
with no error. Before autopid legs (live
`obd_poll`, DTC scans, the future autopid-profile matrix row): either
hold the bench at >= 13.5 V, or set `pause_follow_sleep=false` (or an
explicit low `pause_below_mv`) for the run and restore after. Boot log
line `autopid: request pause follows sleep voltage (13.10 V)` confirms
the follow mode is active (verified on hw 2026-07-21).

## ELM327 app responsiveness bench (PC-run, 2026-09-08)

`python tools\testbench\obd\elm_app_bench.py [dut[:port]] [--dut-ip 10.42.1.194]` (→ `ELM APP PASS`, ~1 min; the request loop `elm_latency_probe.py` is copied to rpi001 and runs there over the hotspot, the API is read through the tunnel). Born from the field report "Car Scanner feels slow, the RPM dial is choppy,...

The whole section: [testing/elm327_app_bench.md](testing/elm327_app_bench.md).

## Recording what an OBD app really sends: the ELM TCP tap (2026-09-23)

`python tools\testbench\obd\elm_tcp_tap.py <wican-ip> [--port 35000] [--log tap.log] [--quiet] [--once]` on any machine that sits on the same network as the phone and the WiCAN (a laptop on the WiCAN's AP in the car; on the bench rpi001's `wtest1` parked on the DUT AP, 192.168.0.11). Point the app (Car Scanner,...

The whole section: [testing/elm_tcp_tap.md](testing/elm_tcp_tap.md).

## Large-payload OBD bench: VTFullyRequestCk / 4 KB ISO-TP (PC-run, PCAN, 2026-09-08)

`python tools\testbench\obd\vt_large_bench.py [dut[:port]] [--dut-ip 10.42.1.194] --ecu pcan [--pcan PCAN_USBBUS2] [--tx 64,512,2048,4064] [--rx 64,512,2048,4094] [--capture-args "--no-headers --spaces"]` (→ `VT LARGE PASS`, ~2.5 min; v6.0.2 IDF venv python). The MIC chip's 4 KB ISO-TP paths end to end through the...

The whole section: [testing/large_payload_obd.md](testing/large_payload_obd.md).

## ATMA flood bench (PC-run, PCAN, 2026-09-08)

`python tools\testbench\obd\atma_flood_bench.py [dut[:port]] [--dut-ip 10.42.1.194] --source pcan [--pcan PCAN_USBBUS2] [--rate 2500] [--count 10000] [--id 0x123]` (→ `ATMA FLOOD PASS`, ~30 s; use the v6.0.2 IDF venv python: python-can + isotp live there). Streams a CAN flood through the MIC chip's monitor mode...

The whole section: [testing/atma_flood.md](testing/atma_flood.md).

## Autopid DTC/DBC PCAN benches (PC-run, 2026-07-22)

`tools/testbench/obd/dtc_bench_test.py [dut_ip] [pcan]` (→ `DTC BENCH PASS`) and `dbc_bench_test.py` (→ `DBC BENCH PASS`) drive the DUT over HTTP with PCAN as the "car" side; run them from the PC with the IDF venv python (has python-can). When the DUT USB is in CH342 mode, flip it to NCM first (usb_host_manager...

The whole section: [testing/autopid_dtc_dbc_pcan.md](testing/autopid_dtc_dbc_pcan.md).

## Troubleshooting

- **Every HIL test fails/errors immediately** → the DUT serial port is held by
  something else (VS Code serial monitor, `idf.py monitor`, a still-running
  test/flash job). One process owns the console port (COM254 today, see the
  preflight's `serial=` line) at a time; close it and re-run.
  The suite now exits early with a clear message in this case.
- **`ap_up` fails with "IP configuration could not be reserved"** → the
  `dnsmasq` service got (re-)enabled on rpi001; run
  `ssh rpi001 "sudo systemctl disable --now dnsmasq"`.
- **HIL timeouts on `HIL READY`** → the HIL app isn't flashed (main firmware
  is on the DUT); use `.\test.ps1 hil -Flash`.
- **`live`/`perf` stages fail with "unreachable" or missing summaries** → the
  DUT isn't running the composed main, isn't on the bench hotspot at
  `-DutIp`, or the needed channels/bridges/mqtt aren't enabled in settings.
  The report's "DUT conditions" lines show what the device was actually
  running.
- **`rpi001` doesn't resolve / ssh hangs** → mDNS on the LAN is flaky;
  pin the alias to the Pi's current LAN IP in `~/.ssh/config`
  (`Host rpi001` → `HostName <ip>`) and add its host key to
  known_hosts. The IP is DHCP: re-pin if the router moves it.
- **The DUT is gone from the network in the middle of a run** → read the
  supply first (`python tools\testbench\lib\owon_psu.py`): about 47 mA
  with the LED breathing red is a **parked** DUT (three crashes in a row,
  `WICAN PARK streak=...` on the console), 73 mA the ROM's download mode,
  160 mA an awake DUT that lost the bench AP. The table, and how to get the
  crash that parked it, are in "When the DUT disappears in the middle of a
  test" above.
- **Opening the DUT serial port REBOOTS it** (DTR/RTS auto-reset): never
  start a serial capture during a live bench run; pull
  `/api/logs/ring` over HTTP instead (it holds the recent log lines).
- **A PC-bench stage fails three seconds in with `no tunnel to the DUT
  through rpi001 (port 18081 busy?)`** (2026-10-13): another session holds
  an ssh tunnel on 18081 (a Claude session working the DUT through
  `ssh -N -L 18081:<dut>:80 rpi001`, say). Every such stage opens its own
  tunnel on that port. Run the bench by hand through the existing tunnel
  (the row's `--dut localhost:18081` form, the same checks and logs, no
  dated report) or close the other tunnel first.

## Notes

- `target` verification is currently completion + no-crash; the strict ordered
  marker assertions live in each `pytest_<comp>.py` and run once
  pytest-embedded is adopted (CHECKLIST Phase 0).
- **`all`, `hil`, and several target apps WRITE the shared settings
  partition** (wifi_manager/HIL rewrite the WiFi config; the
  bridge_manager/socket_manager apps persist their loopback test configs:
  main then degrades those components to defaults at the next boot).
  **Take a settings backup first** (`GET /api/settings/backup` → file on
  the Pi), and after the run: reflash the main firmware, restore over the
  DUT's AP or the hotspot (`POST /api/settings/backup`), then re-PUT the
  canonical bench channels/bridges/sockets (the set above) if the backup
  predates them. `/api/bridges` + `/api/sockets` show at a glance what is
  configured-but-down after a partial restore (proven 2026-07-06).
- Everything the scripts do is plain ssh/nmcli/esptool: `testing/bench_rig.md`
  and `testing/preflight.md` document the manual equivalents for debugging
  (the former `components/TESTBENCH.md` is gone).
