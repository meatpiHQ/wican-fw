# Running the WiCAN tests

One entry point: **`.\test.ps1`** (PowerShell, repo root). Bench details and
inventory live in `components/TESTBENCH.md`. `.\test.ps1 list` prints every
runnable suite/check.

**Every run writes a dated summary report to `test-reports\`** (results
table, performance numbers, DUT conditions, git revision) — that folder is
the pass/fail + performance history; see `test-reports/README.md` for how to
compare runs. Raw per-stage logs go to `test-reports\logs\<run>\` (local
only). Suppress with `-NoReport`.

**Every test directory has a `README.md` stating exactly what it covers and
what the expected passing output is** (Unity summary for host suites, ordered
serial markers for target apps, pytest verdict for HIL):

- `components/<comp>/host_test/README.md` — host unit suites (×28)
- `components/<comp>/test_apps/README.md` — self-contained on-target apps (×12)
- `components/wifi_manager/test_apps_hil/README.md` — the WiFi HIL suite

## The test kinds

| Command | What runs | Needs |
|---|---|---|
| `.\test.ps1 check` | **The quick bench check**: only the bench preflight below (~10 s), then `RESULT: PASS/FAIL`. Run it after re-plugging anything: it names the console port it found (`serial=COM254 (CH344 B)`), the DUT address it resolved, and the FTDI (PSU) / `MeatPi USB-CAN` (ECU sim) ports | PC + rpi001 |
| `.\test.ps1 host [component]` | Host **unit** suites (`components/*/host_test`, 526 tests across 44 components as of 2026-07-26 — the summary prints a per-suite and TOTAL count) on the bench Pi's IDF linux target — syncs sources automatically. Optional component name runs just that suite | `ssh rpi001` reachable |
| `.\test.ps1 target <component>` | Builds + flashes that component's **self-contained on-target app** (`components/<comp>/test_apps`), captures serial, verifies `TEST DONE` with no crash signatures. `ble_manager` is special: waits for `BLE READY`, then drives the DUT from rpi001's BLE controller (`ble_bench.py`) and passes on `BLE BENCH PASS` | DUT console port (the preflight picks it; + rpi001 for ble_manager) |
| `.\test.ps1 hil` | **WiFi hardware-in-the-loop** pytest: real STA connect / reconnect / fallback / ban / AP-auto-disable against the Pi's AP (add `-Flash` to rebuild+flash the HIL app first) | DUT + `ssh rpi001` |
| `.\test.ps1 live` | **Live checks vs the composed main firmware**: CLI over WebSocket (`cli_ws_test.py` → `CLI WS PASS`; incl. `system -t` task monitor) + OBD over WebSocket (`ws_live_test.py` → `WS LIVE PASS`; it pauses/restores every autopid group via `POST /api/autopid/group` — autopid co-masters the chip and its traffic fans out to `/ws/obd`) + real-DBC parser cross-check (`dbc_real_test.py` → `DBC REAL PASS`; uploads the fixture DBCs, diffs every signal against the host reference) + the USB-Ethernet / espnetlink benches (auto-**SKIP** when hardware absent) + **network-trust lockdown sweep LAST** (`wifi_gate_live_test.py` → `WIFI GATE PASS`: flips `sta_trusted` false, probes EVERY `/api` route × 4 methods + UI + WS over the STA address expecting 403/405 only, USB admin + MQTT must stay up; reboots the DUT twice, restores). Needs the canonical bench settings (below) | main fw on DUT at `-DutIp` + rpi001 |
| `.\test.ps1 blesec` | **BLE bonding + security end-to-end** (`ble_security_test.py` → `BLE SECURITY PASS`). BLE sits on the internal-RAM cliff with the full stack, so it auto-switches the DUT to **WiFi-off / BLE-on** (~53 KB free), runs three scenarios on the Pi's UB500 dongle — **gate** (a WRONG passkey can't pair, so no characteristic is readable), **pair** (the correct passkey pairs via MITM and the gated Device-Info read works), **persist** (after a DUT reboot the bonded central reconnects with **no** passkey — NVS bond persistence) — then restores `apsta` + BLE-off. Reboots the DUT ~3×; leaves it on WiFi. Runs on the PC (USB link + ssh to the Pi) | main fw on DUT + rpi001 + UB500 |
| `.\test.ps1 perf` | **Performance battery vs the composed main** — every scenario's numbers land in the report: MQTT RTT (512B) + paced 4000B drain (`mqtt_bench.py`, needs mqtt enabled against the Pi broker; the device silently ignores `size > 4000` = `BENCH_MAX_SIZE`), WS echo latency via `/ws/can` + OBD-poll via `/ws/obd` (`ws_bench.py`), BLE A/B RTT (`ble_ab.py`, runs **last** — a BLE connect makes interface_manager suspend WiFi, which also skips its TCP throughput legs by design; clean-radio BLE TX = manual `ble_blast.py`) | main fw on DUT + rpi001 |
| `.\test.ps1 usbeth` | **USB-Ethernet bench** (`usb_eth_bench.py` → `USB ETH TARGET PASS`): adapter status, HTTP + throughput over the usb-eth netif, reboot re-enumeration The wire may land on either Pi shared profile (`eth0` `eth-bench` 10.42.1.x, or - since 2026-08-26 - a USB-Ethernet adapter on the Pi as `eth1`, profile `eth-bench-usb` 10.42.2.1/24); the script accepts any `10.42.` lease by default, an optional 2nd arg pins the prefix. The DUT address (1st arg) takes a comma-separated candidate list — BOTH Pi hotspots broadcast `WICAN_TEST_AP` (wint0 10.42.0.x, wtest0 10.42.1.x) and the DUT may re-join either after the reboot leg (`10.42.0.194,10.42.1.194` for the 68ee8f5a653d unit; the .62 pin belongs to the other unit). Re-run 2026-08-26 (AX88772B on the WiCAN, cdc_ncm adapter on the Pi, espnetlink build v4.51p_beta): PASS 10/10, HTTP sample 392 KB/s, attaches=1 after reboot; iperf2 over the wire TCP TX 6.45 / RX 7.71 Mbit/s, UDP `-b 20` 7.00 Mbit/s 0% loss 1.68 ms jitter (matches the 2026-07-13 baseline in `iperf_manager/README.md`) | main fw on DUT + the adapter on the USB connector + Pi `eth-bench`/`eth-bench-usb` profile |
| `.\test.ps1 espnetlink` | **ESPNetLink LTE-dongle bench** (`espnetlink_bench.py` → `ESPNETLINK TARGET PASS`): the RNDIS data path (enum + IP) AND the CDC-ACM management console (`ver`, `lte -s` signal/operator/PPP, carrier IP). `--prove-lte` (manual) isolates the Pi's WiFi internet and confirms SNTP over the modem. **Since 2026-08-24 the default `espnetlink.mode=wifi_modem` CUTS the dongle's USB data after pairing** — this USB-data bench needs `espnetlink.mode=usb_ncm`/`usb_rndis` (or `auto_pair=false`) + `usb_acm_cli.enabled`. **2026-08-25: three selectable transports** (`wifi_modem`/`usb_ncm`/`usb_rndis` — the pairing pass sets the dongle's `usb_dev_ethernet.class` to match, one dongle reboot). Bench-verified full cycle: wifi_modem steady (cut, GPS from power-on) -> usb_ncm (the AP-path `usb_data` restore un-cuts the boot-cut dongle, `driver cdc_ncm`, uplink espnetlink_usb, GPS + SNTP over the wire) -> usb_rndis (class flip + re-enum as RNDIS, `driver rndis`, GPS + SNTP) -> wifi_modem (key re-read, cut, fix survives); the WiFi-modem path is verified from the console instead: boot log `identified … → credentials ok → usb gone → STA got IP 192.168.80.x → uplink: none -> espnetlink (AP)`, `espnetlink` → `gps: valid=1`, `rtc -s` syncs via the dongle, `espnetlink repair` cuts again with 0 reboots / 0 `E` lines (see `components/espnetlink_link/README.md`) | main fw on DUT + the espnetlink on the USB connector (usb_host_manager + usb_acm_cli enabled) |
| `.\test.ps1 stackaudit` | **Stack + memory audit, both halves** (2026-07-22, born from the silent PSRAM-stack overflow): (1) *static* — `tools/stack_audit.py` reads the compiler's per-function frame sizes (`-fstack-usage` is permanently on; `.su` files land next to every `.obj`) and cross-references them against every `xTaskCreate*` stack size (PSRAM vs internal detected), flagging frames > 40% of a same-component task stack for human triage; (2) *runtime* — `stack_audit_test.py` on the Pi exercises the deep paths (DTC scan job, test-a-PID), then asserts EVERY live task's `stack_hw` >= 512 B (warn < 1024), the ephemeral job tasks' exit-log watermarks (`dtc job stack_hw=` / `std scan stack_hw=` via `/api/logs/ring`), and heap floors on BOTH placements (internal min_free >= 20 KB + largest_block >= 16 KB, PSRAM min_free >= 1 MB). The runtime half also runs inside `live` | static: local build; runtime: main fw on DUT + rpi001 |
| `.\test.ps1 sleep` | **Sleep-mode HIL** (`sleep_bench_test.py` → `SLEEP BENCH PASS`, ~10 min): OWON PSU walks the 12 V input across the sleep/wake thresholds; entry proven by supply current, wake by `/api/restart/history` | PSU (the FTDI port, COM50 today) + rpi001 |
| `.\test.ps1 sleepmatrix` | **Sleep robustness matrix** (`sleep_matrix_test.py` → `SLEEP MATRIX PASS`, ~90 min): every historical breaker as a scenario — VPN soaks (betty), BLE, logger, ghost SSID, boot-loop guard, periodic/critical — see `tools/testbench/sleep/SLEEP_MATRIX.md` | PSU + rpi001 (+ betty for the VPN legs) |
| `.\test.ps1 conserve` | **Counter-conservation benches** (CAN / USB-NCM / WiFi → `* CONSERVATION PASS`): exactly-N accounting against a known generator, the §7 performance-truth net | PCAN; NCM leg needs the device-role flip |
| `.\test.ps1 dwc2` | **DWC2 kill-vs-ISR race hammer** (`dwc2_hammer_test.py` → `DWC2 HAMMER PASS`, ~10 min): ACM mixed-timeout kill floods + NCM OUT blasts + mid-flight teardown churn vs the 2026-07-17 crash class | espnetlink dongle on the USB host port |
| `python tools\testbench\wifi\wican_fresh_bench.py --wican COMx --psu COMz` | **WiCAN Pro out-of-the-box scenario** (→ `WICAN FRESH PASS`, ~5 min): erase-flash + flash, PSU cold boot with the console captured from power-on; asserts the first-boot defaults (wifi_manager mode=ap, 0 STA networks, derived `WiCAN_<id>` AP, USB host enabled, espnetlink wifi_modem unpaired), then plays the new user: the Pi joins the AP with the default `@meatpi#`, loads the web UI + `/api/settings` + the wifi_manager schema, stages home WiFi (the Pi `wican-bench` hotspot) + apsta through the settings API exactly as the UI does, submits (ONE planned reboot) and expects the STA on the home network WHILE the phone stays on the AP, the AP still up, the API still answering; planned reboots only, 0 E lines. `--no-erase` re-runs on an already-fresh unit. Bench 2026-08-31: PASS (STA on the home network 9–34 s after the config reboot; a dongle on the connector pairs in the background = one extra planned reboot, accounted for) | WiCAN console + PSU + rpi001 (`wtest0` hotspot, `wtest1` free) |
| `python tools\testbench\wifi\espnetlink_fresh_bench.py --wican COMx --dongle COMy --psu COMz` | **ESPNetLink out-of-the-box scenario** (→ `ESPNL FRESH PASS`, ~8 min): erase-flash + flash BOTH devices, PSU cold boot with both consoles captured from power-on, the Pi joins the fresh `WiCAN_<id>` AP as the user's phone and STAYS there; asserts the dongle's first-boot AP-password provisioning, the PAIRING HOLD while the WiCAN AP still has the factory password (identify + key read, then parked in `hold` with the warning, no VBUS cycle, 2026-09-08; the dongle build under test must report `api >= 7`, a stale dongle binary fails here instead of masquerading as a WiCAN bug), the user's mandatory AP-password change (reboot), then identify → credentials → store (wifi mode ap→apsta) → cut → ONE WiCAN reboot, then the STA joining the dongle AP + uplink=espnetlink WHILE the client sits on the WiCAN AP (field-hit 2026-08-31: the AP-client STA pause blocked the first association forever), the user's view via `GET /api/espnetlink` over the WiCAN AP, the stored key read back (and pushed into the Pi's `espnl-client` profile — the fresh dongle has a new password), planned-reboots-only, 0 E lines on both consoles. `--no-client` = bare from-scratch path; `--no-erase` = re-run on the already-fresh pair. Also plays the deterministic AP-client hazard (phone parked, `espnetlink repair` VBUS-cycles the dongle, the STA must re-join ≤150 s — unfixed wifi_manager: never; fixed: +68 s). Bench 2026-08-31: PASS (zero-touch pairing 18–21 s from power-on, STA on the dongle AP at ~29 s) | both consoles + PSU + rpi001 (`wtest1` free) |
| `python tools\testbench\wifi\espnetlink_mode_bench.py` | **ESPNetLink transport-mode switching** (→ `ESPNL MODE PASS`, ~15 min): the full `espnetlink.mode` transition matrix — wifi_modem → usb_ncm (AP-path `usb_data` restore un-cuts the boot-cut dongle, `boot_cut` clears, NO dongle reboot, driver cdc_ncm) → usb_rndis (class flip = exactly ONE dongle reboot, re-enum RNDIS) → wifi_modem (cut, `boot_cut` re-armed, dongle NOT rebooted — a live GPS fix survives) → usb_rndis DIRECT (restore + class flip in ONE ensure pass, still one dongle reboot) → restore. Every WiCAN reboot must be planned (restart_tracker `unexpected resets` may not grow); all assertions are OBSERVED state over the console + the dongle's `/api/wifi_modem` (HTTP statuses are advisory — fresh-AP associations lose response status lines routinely); the WiCAN's STA address is read live from the console (DHCP lease moves after cold cycles). `wifi:Invalid MMIE` (esp_wifi PMF noise) and the IDF HTTP client's single connect-failure triplet (a poll racing the dongle's planned mid-transition reboot) are whitelisted. Bench 2026-08-25: PASS, 0 unexpected resets, 0 E lines. Bench 2026-09-08: PASS 31/31 on a freshly erased dongle (current tree, api 7) + the reworked WiCAN pairing states; leg 1 now expects ONE dongle reboot iff the dongle's `lte_upstream_pppos.ncm_share` was still False before the switch (a fresh dongle submits it once), no reboot otherwise | DUT console (CH344 channel B, COM254 today) + rpi001 (`espnl-client`) + paired dongle |
| `python tools\testbench\wifi\espnetlink_roam_bench.py` | **ESPNetLink roaming scenario** (→ `ESPNL ROAM PASS`, ~8 min): the drive-away / drive-home story in `espnetlink.mode=wifi_modem` — away on the dongle (uplink=espnetlink, GPS polls + fix) → a temp Pi hotspot broadcasts the DUT's OWN primary SSID/key, read from `/api/settings/backup` ("drive home") → roam-to-preferred lands within `sta_roam_interval_s`+120 s, uplink=wifi, dongle polls STOP → hotspot down ("drive away") → fallback to the dongle ≤150 s with the GPS fix surviving (the dongle is never rebooted) → home again, zero console E lines. Console-observed only (a client on the DUT's AP pauses its STA reconnects); the temp AP pins `ipv4.addresses 10.42.9.1/24` (NM's default shared subnet collides with the other hotspot and rolls the fresh AP back). Bench 2026-08-24: roam-home 116/66 s, fallback 18 s, fix valid +3 s | DUT console (CH344 channel B, COM254 today) + rpi001 (wtest0 borrowed for the run, `wican-bench` restored after) + paired dongle |
| `python tools\testbench\ha\ha_webhook_gate_bench.py --dut <ip>[,<ip2>] (--pi rpi001 \| --serve 8199 --local-ip <own ip> \| --receiver <url>)` | **HA webhook poster with autopid OFF** (→ `HA WEBHOOK GATE PASS`, ~1.5 min, 2 planned reboots): the fresh-device regression found 2026-09-08 — a fresh WiCAN ships `autopid.enabled=false` and the poster skipped every lap on that bit, so HA's discovery push got `201 Created` and no telemetry ever left the device (every HA entity "unavailable", no error anywhere). Disables autopid (PUT + submit), registers a receiver, expects ≥2 status-only pushes (`status.device_id`/`fw_version`/`hw_version` present, no `autopid_data`) and `GET /api/webhook` `status:"ok"`, restores autopid, DELETEs the webhook. Receiver on any host BOTH sides reach: `--pi rpi001` (the Pi AP gateway, `ha_webhook_bench.py` pattern; written from that pattern but NOT exercised on 2026-09-08 - the Pi was down), `--serve` (this PC — used with the PC's Mobile Hotspot broadcasting `WICAN_TEST_AP` while the bench Pi was down), or `--receiver`. Bench 2026-09-08 on the hotspot rig: fails before the fix (0 posts in 45 s, `status:"disabled"` 0/0), passes after. Canonical rig 2026-09-09, run ON rpi001 (`python3 ha_webhook_gate_bench.py --dut 10.42.1.194,10.42.0.194 --serve 8199 --local-ip 10.42.1.1`): PASS. `--dut` takes a comma-separated candidate list because the DUT re-joins EITHER hotspot twin after its reboot leg while both are up (the first run failed on a fixed address exactly that way; `wican-bench-w0` was then parked per the brief §6.2). The bench restores autopid AND the `ha_webhooks.interval_s` it found (the registration persists 5 s; DELETE keeps it). Real-HA delivery was proven the same day through a TCP relay to `rpi002:8123`: HA logged `Received MeatPi device webhook` for the pushes (then 403 identity-mismatch, expected — the bench unit is not the entry's device) | main fw on DUT + a receiver host; no vehicle needed |
| `python3 tools/testbench/ha/ha_lte_push_bench.py --capture 8199` (ON rpi001) · `… --webhook-id <id> --ha-local http://<ha>:8123 --ha-remote https://<x>.ui.nabu.casa [--expect-403]` | **HA push over LTE via the ESPNetLink dongle + the GPS block** (→ `HA LTE PUSH PASS`, ~2 min): the car-on-the-road leg. Downs `wican-bench` so the DUT roams to the paired dongle AP (its fallback network), joins that AP with the free radio (`espnl-client`) to keep reaching the DUT at 192.168.80.x, asserts `uplink=espnetlink`, LTE connected and a live fix on the WiCAN side, then either (a) **`--capture`**: a receiver on the free radio takes the pushes and the bench asserts the contract `gps` block `{latitude, longitude, accuracy, altitude, speed, heading, satellites}` + the `gps_*` sensors in `autopid_data`, or (b) the **PRO dual-URL registration** exactly as the integration sends it (`url` = HA local http, `urls[1]` = the external HTTPS URL) and watches the poster: the local URL is unreachable from LTE, so every success went through the HTTPS failover (`--expect-403` for a webhook id that belongs to another device: HA's identity rejection is the proof of arrival). Restores webhook/interval/`wican-bench`/the free radio and waits for the DUT back on the bench. Bench 2026-09-09: `--capture` PASS (fix -37.9052/145.1451, accuracy 4 m, 18 sats; GPS becomes valid ~45 s after the roam, the espnetlink poll starts with the link); the Nabu Casa leg reached *an* HA over LTE + TLS 13/13 (2xx) but NOT rpi002's — its stored `remote_domain` is served by another instance on the same account (marked POSTs: LAN URL logged + 403, remote URL 200 + nothing logged), so delivery INTO the dev HA over the internet is still unproven; see the brief §7. Note: with the local URL dead every cycle logs the IDF connect-failure E triplet before the failover (whitelisted, but a road-side device does this forever - open item in the ha_webhooks README). **(c) `--dut-vpn 10.9.0.2` = the REAL test (2026-09-09, PASS):** with the `wg_ha_route.sh` route up and a bench HA on rpi001 (`ha-bench.service`, HA 2026.2.3 + the integration copied from rpi002, `internal_url http://10.9.0.3:8123`), HA itself registered the tunnel webhook URL on the DUT; the bench downs the hotspot, reaches the DUT at 10.9.0.2 THROUGH the tunnel (LTE → betty → rpi001, 10 s after the roam), and the push count grew over LTE (+3 in 80 s, 0 failures) with a live fix. HA's log: `Received MeatPi device webhook` for every push and `device_tracker: Updated GPS location: -37.9051, 145.1450 (accuracy: 3m)` - the Location entity left "unavailable" for the first time (the firmware `gps` block), plus the `gps_*`, battery, VPN-status sensors. In this mode the bench leaves HA's registration untouched (a DELETE silences the device until the entry is reloaded - burned once). Integration finding: while the DUT was on LTE HA's re-registration ("Connection info changed") timed out 3/3 - the 10 s `WEBHOOK_REGISTRATION_TIMEOUT` is spent on the dead cached/host candidates (10.42.1.194) before the `vpn_ip` candidate (10.9.0.2) gets a turn; harmless here (the device keeps pushing to the URL it has) but the road-side buttons need the VPN candidate FIRST when the push came in over it | main fw on DUT + paired dongle with LTE/GPS + rpi001 (`wtest1` free; VPN mode: the route up + the bench HA) |
| `bash tools/testbench/vpn/wg_ha_route.sh up \| status \| down [--wipe]` (PC) | **HA-over-VPN bench route** (→ `WG HA ROUTE UP`, ~3 min, one DUT reboot): betty = WireGuard server 10.9.0.1 (udp 51820), rpi001 (the bench HA, see below) = peer .3, the DUT = peer .2 through its own `vpn_manager` (split mode, AllowedIPs 10.9.0.0/24, no default route). The bench HA's `internal_url` is `http://10.9.0.3:8123`, so the integration registers a webhook URL the device reaches from the bench hotspot AND from LTE, and HA reaches the device at its tunnel address 10.9.0.2 (the integration's `vpn_ip` backup). Same key rules as `wg_public_soak.sh` (server key born on betty, never printed; DUT key never leaves the device). rpi001's own tunnel rides its `Nachos_8042_5G 2` uplink on wtest1. 2026-09-09: UP first try (rpi001↔betty ping, DUT connected, rpi001→DUT ping + HTTP through betty) | betty (root ssh alias) + rpi001 + DUT on the hotspot |
| `.\test.ps1 logsinks` | **External log sinks conservation** (`log_sinks_bench_test.py` → `LOG SINKS BENCH PASS`, runs ON the Pi; also a `live` stage): gates-closed defaults (port closed, counters zero, ws_log migration present), then per-sink conservation vs the `logsinks emit` known generator — TCP tail exactly-N, UDP collector exactly-N, `/ws/log` stream, SD file content + rotation + retention, per-sink `in == out + dropped` identity, restore. Reboots the DUT 3× | main fw on DUT + rpi001 + SD card + ws_cli enabled |
| `python tools\testbench\obd\autopid_profile_bench.py <base-url> [--only SUBSTR] [--wide N] [--skip-wide]` | **AutoPID vehicle-profile import** (→ `AUTOPID PROFILE PASS`, ~2 min, no vehicle): PUTs EVERY published profile (`vehicle_profiles.json` on GitHub main, or `--profiles FILE`) through the web UI's import conversion (byte-index shift, degree sign, duplicate-name rename) and expects `200 {"ok":true}` for each; then polls one custom PID on `0100` carrying 24 parameters (the ECU simulator answers it) and expects all 24 decoded in `GET /api/autopid` with no new E line; restores the config it found and reads it back equal. Born 2026-09-16 from "Profile rejected: 220105: more than 16 parameters" on picking Hyundai Ioniq5/6: `AP_PARAMS_PER` 16 rejected 11 of 80 profiles (Hyundai/Kia BMS DIDs decode 20–32 values, Xpeng 192), then a typo in three Hyundai/Kia profiles (cell 158 labelled `HV_C_V_168` twice) tripped the duplicate-name rule; cap now 256 with the poller's copy in PSRAM, the importer renames repeats and says so. Bench 2026-09-16 through the ssh tunnel: PASS | main fw on DUT (tunnel or Pi-side address) + ECU simulator on the bus for the wide leg |
| `python3 tools/testbench/usb/j2534_transport_probe.py <dut_ip>` (ON rpi001) | **J2534 transport probe** (prints `J2534 TRANSPORT: raw CAN PASS or FAIL, ISO15765 bind PASS or FAIL`, ~10 s): what the PassThru server can do on THIS build, proven on the bus: a raw CAN WRITE of `7DF [02 01 00 ..]` must step the native `/api/can` tx counter and bring the ECU's `06 41 00 ..` back as RX_MSG; then CONNECT(ISO15765) without ids + a FLOW_CONTROL filter (pattern 7E8 / fc 7E0) must open an ISO-TP session and answer `10 02` with `50 02`. Public builds have no `can_isotp()` provider (the stock `ext_manager` registers none), so the bind leg FAILs with `E j2534_server: ISO15765 not available in this build`: expected until a provider ships; with the internal add-on pack both legs PASS. The older `j2534_bench.py` handshake bench passes on both builds because its CONNECT(ISO15765) carries no ids and never binds. Needs `j2534_server.enabled` + `allow_lan` (from the Pi's STA side), both reboot-to-apply; restore after. Bench 2026-09-16, public build BEFORE the native provider: raw CAN PASS (tx 6 to 7), ISO15765 bind FAIL; with `can_isotp_esp` (same day): raw CAN PASS, ISO15765 bind PASS (`10 02` -> `50 02 00 32 01 F4` as ISO15765 RX_MSG). Needs `j2534_server.enabled` + `allow_lan` (reboot-to-apply; restore afterwards) | main fw on DUT + rpi001 + the ECU simulator answering 7DF |
| `python3 tools/testbench/usb/j2534_bench.py <dut_ip> --reflash [--tx 7E2 --rx 7EA]` (ON rpi001) with `python tools/testbench/actors/pcan_reflash_ecu.py 150 --scenario happy [--req 7E2 --resp 7EA]` running on the PC (PCAN_USBBUS2) | **J2534 ISO15765 reflash preamble over native ISO-TP** (prints `J2534 REFLASH PASS`, ~10 s): CONNECT with ids, `10 02`, seed/key `27 01/02`, the multi-frame `34` -> `74`, the `31 01 FF00` erase routine. `--tx/--rx` + the actor's `--req/--resp` pick the CAN pair: use one the ECU simulator does not answer (it answers 7E0/7E1/7E4.. and cannot be switched off while its settings API is gone) and PAUSE autopid (`POST /api/autopid/group {"name":"default","enabled":false}`) so its polls do not land on the same ECU. The UDS isotp transport releases an ECU 5 s after its last request (one session per rx id), so a UDS probe of the pair right before makes the CONNECT fail; wait. Bench 2026-09-16, public build: PASS on 7E2/7EA | main fw on DUT + rpi001 + PCAN on the bus |
| `python tools/testbench/obd/eeprom_guard_bench.py <dut_ip> [--port 35000]` (ON rpi001) | **OBD chip EEPROM guard** (prints `EEPROM GUARD PASS`, ~10 s): talks to the MIC like an ELM app over the TCP bridge and proves the driver guard (`obd_chip_guard.h`): `ATSP6` is rewritten to `ATTP6` (chip answers OK, `/api/obd_chip` `eeprom_guard.rewrites` +1), `ATPP 0C SV 23` (UART re-baud) and `STWBR` are refused with the ELM `?` (`.blocked` +1, the chip never saw them), `ATPPS` (summary read) passes, the chip still answers after. Needs the TCP ELM bridge (`obd0`/`br_tcp_obd`, canonical bench). Bench 2026-09-16: PASS | main fw on DUT + rpi001 + the ELM bridge |
| `python tools/testbench/obd/run_be.py <base-url> tools/testbench/obd/uds_bindings.be [--tx 7E2 --rx 7EA]` | **Script UDS bindings** (prints `SCRIPT UDS PASS` + `SCRIPT RUN PASS`, ~3 s): runs a Berry script through `POST /api/scripts/run` exercising uds()/uds_ok/uds_nrc/uds_nrc_str, obd_claim/obd_request/obd_isotp_tx/rx/obd_release against the ECU simulator (7E0/7E8; `--tx/--rx` rewrite the script's `var TX/RX` header). Needs `script_engine.enabled`. Bench 2026-09-16: 13/13 on the native ISO-TP path with autopid running, and on the forced obd_chip path | main fw on DUT + the ECU simulator |
| `python tools/testbench/obd/run_be.py <base-url> tools/testbench/obd/reflash.be --tx 7E2 --rx 7EA --timeout 90` with `python tools/testbench/actors/pcan_reflash_ecu.py 200 --scenario happy --req 7E2 --resp 7EA` on the PC | **Script-driven ECU flash** (prints `REFLASH OK` + `SCRIPT RUN PASS`, ~2 s): `reflash.be` streams `/sd/fw/ecu.bin` (640 B on the bench card) as TransferData blocks after session, seed/key, erase and RequestDownload, then TransferExit and the ECU's checkMemory CRC routine. Needs `script_engine.allow_reflash` (reboot-to-apply; restore afterwards) and the SD card. Turn the UDS Exclusive switch on (`POST /api/uds {"exclusive":true}`) so autopid is off the bus. Bench 2026-09-16 over native ISO-TP: REFLASH OK, 5 blocks, `71 01 02 02 00` = CRC verified | main fw on DUT + PCAN on the bus + SD card |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_uds.mjs && node smoke.mjs` | **UDS Tool page probe** (prints `UDS PROBE PASS`, 18 checks): path badge + provider chip from the mocked `GET /api/uds`, the Exclusive bus switch and its round-trip (`AutoPID paused` chip after a request), P2* label, 29-bit, the terminal (sent › and reply ‹ lines with the inline decode: VIN as text, NRC name; raw JSON toggle; Clear; persistence in localStorage; a sent line clicks back into the form), the settings card with `exclusive`. WITHOUT `MEATPI_COMPONENTS_PATH` the preview builds from the stale mirror under `components/meatpi/` and tests the OLD page (it prints the source path — check it). 2026-09-16: PASS, smoke 23 routes 0 errors | PC, node + jsdom |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_automate.mjs` | **Automate page probe** (prints `PROBE PASS`, ~58 checks): Settings/Parameters/Destinations/HA tabs, PID lists (collapsed parameters, scan results merge + dedup), and since 2026-09-17 the Vehicle Specific flow: ONE `Vehicle profile` row + `Choose profile` dialog over the mocked `vehicle_profiles.json` (makes left / models right, make click lists its models, search narrows both and auto-selects a single match with highlight, disabled `Use this profile` until a model is selected, ArrowDown/Enter), staging without any config PUT (banner + `Unsaved edits`), Discard restoring the device's state, Apply PUTting the config once then the save-settings modal (Save, restart later -> one settings PUT, header Saved, Restart now notice on the card, fields still filled after leaving and re-entering the page), Test modal transcript, Add PID, custom PIDs surviving the import. 2026-09-17: PASS | PC, node + jsdom |
| `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_rules.mjs` | **Rules builder probe** (prints `RULES PROBE PASS`, 23 checks): sentence rows with the technical line, `·auto` tag and runtime badges from the mocked `/api/events/rules`; Templates menu; the charging template read back into Trigger / Only if / Then; adding a LIVE-value condition (autopid parameter expanded from the `autopid.` prefix) with its badge and preview text; Edit as JSON showing `when[].value` + `undo` and the round trip back; Add staging the rule (header dirty, sentence row with the undo tail); the WiFi template (network picker, rate, undo) and a rate edit; edit locks the name; duplicate suffixes `_2`; a bad name is refused with the modal open. 2026-09-17: PASS | PC, node + jsdom |
| `python tools\testbench\system\data_destinations_bench.py --dut 10.42.1.194 --pi rpi001 --pi-ip 10.42.1.1 [--ap-if wtest1] [--observe 45] [--skip-wifi] [--skip-broker]` | **Data destinations end-to-end** (→ `DATA DEST PASS`, ~8 min, 2 planned reboots): the Automate → Data destinations feature (component `data_destinations`, 2026-09-19) against receivers the bench controls on rpi001 (`tools/testbench/actors/dd_receiver.py`, started as a transient systemd unit: plain HTTP :8199, HTTPS :8443 with the bench server certificate, mutual-TLS HTTPS :8444, a mock Iternio/ABRP endpoint :8445 that checks `token` + `tlm` JSON with `utc` + the api_key as `?api_key=` or `Authorization: APIKEY`, and a paho subscriber on the Pi's mosquitto) and mosquitto :1883. Legs: cert_manager uploads through `/api/certs/upload` (bench CA as `ddbench`, CA + client pair as `ddmtls`) and the readback; 8 destinations + MQTT in one submit; steady state (per-row deliveries with headers/query/first-push shape/tlm contents/mTLS peer CN/retained MQTT, the `hsx` row WITHOUT a cert set and the `mtx` row without a client cert must fail with 0 success — the cert set is what makes the TLS legs pass — counters consistent, E lines = only the IDF client's TLS/connect lines); `POST /api/destinations/test`; an ABRP logical error (HTTP 200 + `status:error` counted as a failure, recovery after); server down (fails counted, ≥ 3 consecutive → backoff ≥ 10 s, attempts spaced, recovery within 90 s with the backoff cleared); broker down (`skipped_offline` grows, NO fail/backoff, resumes); WiFi down (`--ap-if wtest1`: the Pi's phone profile on the DUT's own AP keeps the API reachable: `network:false`, skips grow, fails do not, deliveries resume after the hotspot returns); restore (settings + reboot, cert sets deleted, receiver stopped, mosquitto + hotspot up). Bench 2026-09-19 (run 8, from the PC through `ssh -N -L 8082:10.42.1.194:80 rpi001` with `--dut 127.0.0.1:8082` - the PC has no route to the hotspot subnet): **PASS 77/77 in 531 s** - server-back recovery 35 s, rejoin after the hotspot returned 175 s (27-33 s on two earlier runs; the rig's reason-204 assoc stall can hold the DUT on the dongle AP for up to 300 s, hence the 330 s budget + the wtest1 rescan nudge), 22 whitelisted IDF-client E lines all from the two deliberately failing TLS rows, 0 unexpected resets. The first runs of this bench found two real firmware bugs, both fixed the same day: the ~10 KB destination table copied onto the MAIN task's stack in `_start()` (stack-overflow panic at boot) and cert_manager's lazy PEM load running on the poster's PSRAM stack (`cache_utils.c:126` assert on the first HTTPS delivery with a cert set, a panic/reboot loop every ~25 s that looked like a web server stalling 15 s at a time from outside). Bench-side lessons (fixed in the script): a `pkill -f dd_receiver` matches the ssh session's own shell and kills it, `nmcli con up` on an ACTIVE hotspot bounces it, and a post-submit wait must key on the restart tracker's boot counter, not on the first answered probe. | main fw on DUT + rpi001 (hotspot, mosquitto, ~/wican-tls bench CA); no vehicle needed |
| `python tools/testbench/rules_bench.py --base http://localhost:8081` | **Rules-engine bench** (prints `RULES BENCH PASS`, 23 checks, 4 restarts; `--skip-wifi` / `--skip-stack` drop phases B / D): the registry surfaces (`wifi.sta` source, `wifi.*` live values, `undoable` flags, `GET /api/events/rules`, `events_src`/`events_act` headroom in `health.caps`); a `wifi.sta` while-rule fires on the STA connect at boot and overrides the group rate (skip with `--skip-wifi`); a timer rule with a live `${time.epoch}` condition + undo is undone by the dispatcher's 1 s re-check once the DEVICE clock passes the deadline (the DUT's clock differs from the PC's by minutes: the script derives now from restart_tracker boot_time + uptime); phase D stacks the WiFi and the epoch while-rules on ONE group plus a 3 s timer rule with a 10 s cooldown (both arm; the epoch undo restores the CONFIGURED rate, the documented stacking semantic; the cooldown holds the beat to about one run per window with `stats.suppressed` growing); the device's rules are restored at the end. Host side: `.	est.ps1 host event_manager` runs the 28-test suite incl. 12 replayed rule-combination scenarios. 2026-09-17: PASS | PC + DUT over the tunnel |
| `python tools/testbench/obd/uds_route_bench.py <base-url> [--tx 7E0 --rx 7E8] [--req "22 01 01"] [--min-ok-pct 90]` | **UDS route over the OBD chip** (prints `UDS ROUTE PASS`): N requests through `POST /api/uds/request` with autopid running. TWO gates: HARD 0 corrupt (a request is never answered ok with another requester's payload — the transaction hold `obd_chip_txn_begin` + the SID check guarantee it, verified 0/0 even under load) and throughput `--min-ok-pct`. Chip-transport acceptance is run with autopid PAUSED (`POST /api/autopid/group {"name":"default","enabled":false}`) or a light config: 20/20 at ~107 ms. Under autopid's 32-param multiframe flood on the same single-MCU simulator (`--tx 7E4 --req "22 01 01"`), throughput drops to ~2/20 (clean errors, 0 corrupt) — the chip-transport ceiling that native ISO-TP (`can_manager/TASK_isotp_public.md` Phase 2) removes. Bench 2026-09-16 (chip backend): PASS paused; 0 corrupt under load. With the native provider (`--expect-backend isotp`, autopid RUNNING): 20/20 on 7E0 (median 22 ms) and 20/20 on the same-ECU 7E4 target (median 28 ms), 0 corrupt — the contention is gone. Chip path (settings `backend=obd_chip`, reboot) with the UDS Exclusive switch on (`POST /api/uds {"exclusive":true}`): 20/20, **median 3 ms** (the ELM response-count digit + setup skip, 2026-09-16); without the switch the known 4/20 ceiling under autopid's flood | main fw on DUT + the ECU simulator |
| `sudo env PYTHONPATH=/home/meatpi/.local/lib/python3.11/site-packages python3 -u tools/testbench/ble/ble_slcan_bridge_test.py --dut 192.168.0.10 [--flood] [--stage configure\|ble\|restore] [--keep-sta]` (ON rpi001) with `python tools/testbench/can/pcan_watch_flood.py --secs 240 --marker rpi001` started first on the PC | **slcan over BLE** (prints `BLE SLCAN BRIDGE PASS`, the companion `PCAN WATCH PASS`; ~6 min, 2 planned reboots): the `can <-slcan-> ble` bridge end to end, born from the meatpi-components PR #1 check (2026-09-20: "22-byte SLCAN lines truncated on FFF1/FFF2, first PDU dropped on a full BLE TX queue"; neither is in the NimBLE build). Configure = BLE on + `br_ble_can` + wifi `apsta`→`ap` (STA off for the leg; `--keep-sta` keeps it) in one reboot, the baseline kept in `/tmp/ble_slcan_bridge.json` across re-runs. BLE leg (HCI trace via btmon, summarised at the end) = adapter power-cycle, `nmcli con down wican-bench-ap` (a station on the DUT's own AP stops BLE: `ap_ble_exclusive`), scan on the ADVERTISED name or connect to the identity address when bonded (a bonded DUT is invisible to a bleak scan), pair, then A: a 22 B `t7DF8020100..` written right after subscribe must come back as ONE complete 22 B `t7E8` line; B: 20 requests 150 ms apart = 20 answers, nothing split at 20 B; C: a 27 B 29-bit line, then VIN `0902` with the bench answering the flow control from 7E0 (3 lines, the VIN decodes; WARN-only, sim side); D (`--flood`): 30 requests while the companion floods 0x123 at 1000 fps (reported, not judged: monitor-all over BLE drops on a full TX queue by design); E: 5 requests ~8 s after the flood must be answered again (a TX path that stays wedged after an overload FAILS here). Restore = settings back + reboot, no new faults, 0 unexpected resets. The companion proves every FFF2 write reached the bus with DLC 8 (the A/B/C/D/E padding signatures) and floods on the Pi's marker file, so the flood lands on D/E and never on the connect retries. **2026-09-20 22:50** (first version, STA hunting on the hotspot): A answered in 76 ms as one 22 B notification, B 20/20 all 22 B (notifications 22-176 B, MTU 256), the 27 B write on the bus with DLC 8, D 2/30 answers at ~1 KB/s with ~1100 `tx queue full` drops/s. **2026-09-21** (this version, 7 BLE legs): every write reached the bus (52/52 + the 29-bit one, DLC 8) but the link would not hold: `Connection Failed to be Established (0x3e)` / `Connection Timeout (0x08)` on the Pi, supervision timeouts on the DUT, one session paired then starved (24 notifications in 35 s), one died mid passkey rounds with the DUT silent for > 4 s right after `wifi:mode : null` (the AP stop at connect); independent of bond state, STA state, adapter reset and BlueZ supervision 420 ms → 2 s; RSSI -68. Rig/DUT issue, open (brief §7). Firmware finding from the E leg + the ring: the NimBLE backend's `s_congested` latch (`ble_manager_gatt_nimble.c` `notify_handle`) cleared only on a successful notify, but `ble_manager_io.c`'s TX task never called notify while it was set, so after one ENOMEM burst BLE data TX was dead for the rest of the boot (`tx queue full` at the CAN frame rate, even while a later client is still pairing). **Fixed 2026-09-21** (self-expiring congestion window + bounded retry in `blm_gatt_notify_handle`, reset on connect/disconnect; `ble_manager/README.md`): leg E is the regression witness | main fw on DUT + rpi001 (UB500 + bleak + python3-dbus) + the ECU simulator answering 7DF; PCAN on the PC for the companion |
| `.\test.ps1 blehttp` (= `python tools/testbench/ble/ble_pi_run.py ble_http_pi.py --dut <ip> --stage configure\|ble\|restore`, the Pi script runs ON rpi001 under `sudo env PYTHONPATH=...`) | **The HTTP API over BLE** (`ble_http_pi.py` → `BLE API PASS` + a nested `BLE STORAGE PASS`; ~8 min, 2 planned reboots): the `http` stream channel FFF3/FFF4 (`ble_http/BLE_HTTP_PROTOCOL.md`) that replays framed HTTP requests against the device's own web server over loopback. Configure = BLE on (passkey 421337) + `ble_http.enabled` + `interface_manager.sta_ble_handover` OFF so the STA stays up as the WiFi witness (`--wifi-off` = wifi mode `ap`, no witness: the fallback for a rig where BLE will not hold beside WiFi), baseline kept in `/tmp/ble_http_bench.json`. BLE leg = bond hygiene, `nmcli con down wican-bench-ap` (a station on the DUT's AP stops BLE), `bits.ble_enabled` over WiFi, L0 link gate (2 connect/pair/disconnect cycles, `BLE API BLOCKED: link` stops the run), FFF3/FFF4 present, L1 `GET /api/status` + `/api/info` (device_id) with the WiFi witness seeing `ble_connected`, L2 `ble_http` settings GET + schema + identical PUT round trip (no pending reboot), **L3 storage** on `/data` and `/sd`: `info` vs the WiFi witness, `mkdir`, raw upload 1 KB / 64 KB / 512 KB honouring CREDIT, download byte-exact, the 64 KB file's SHA-256 cross-checked through `GET /api/fs/download` over WiFi, `list`, `delete`, `METRIC upload_*/download_* KB/s`; L4 tunnel + route errors (403 `path` outside `/api/`, 400 invalid path, 404 file / route, 429 `busy` for a second request mid-upload, then that upload completes; a WiFi upload racing the BLE one: one side 503, WARN-only), L5 link dropped at 128 KB of a 512 KB upload → no torn file / `.tmp` after reconnect, a fresh upload completes, **L6 UDS through the tunnel** (`POST /api/uds/request` `22 F1 90` → `62 F1 90` + the simulator's VIN, `10 02` → `50 02`, `/api/uds/session` begin → `session_active` → end), L7 `GET /api/logs/ring` streamed (chunked text). Restore = settings back + reboot, no new faults, 0 unexpected resets, 0 `E` lines from the BLE/J2534/HTTP tags (other tags' pre-existing E lines, e.g. the data logger's SD `disk I/O error` on the bench card, are reported as WARN). **Bench 2026-09-21: BLE API PASS + BLE STORAGE PASS** (STA up beside BLE, MTU 517, 17 characteristics): every transfer byte-exact on `/data` and `/sd` with device tx == on-air (btmon) == client rx and 0 stream resyncs; uploads 1.9-2.1 KB/s, downloads 2.2-2.6 KB/s (indications, coex-limited bench link); `GET /api/status` RTT ~1 s over the tunnel; UDS `22 F1 87` -> `62 F1 87 "WCAN-ECU-SIM"` in 4 ms bus time; WARN only: the simulator answered `22 F1 90` (VIN) with NRC 0x31 that day, and the racing WiFi upload got 404 instead of 503 (the two uploads did not overlap at the fs layer). Findings on the way, all fixed the same day: the rebuilt GATT table pointed at stack-scoped UUID literals (`ble_uuid_flat rc=3`, 0 characteristics visible); `esp_http_client_read()` returns 0 at CHUNK boundaries, not only at the end (SD download ended after 57 KB); NimBLE silently lost ~1 in 100 notifications under sustained TX (btmon 144 of 146 on air with rc 0) -> the stream channels now use INDICATIONS; the indication budget must be the ATT 30 s because a central confirms only after its queued writes went out (2 s stalled every upload); BlueZ keeps a per-bond GATT cache (drop the bond once after a table change); the bench must leave the DUT's AP before judging `bits.ble_enabled`. Known limitation recorded in `ble_http/README.md`: the web server serves one request at a time, so a long BLE transfer holds the web UI for WiFi clients | main fw on DUT + rpi001 (UB500 + bleak + python3-dbus) + the ECU simulator answering 7E0 for the UDS leg; SD card for the `/sd` legs **2026-09-22 (protocol v2, notify mode):** the full run FAILED on plumbing only: the configure reboot dropped the DUT to the dongle AP (`configure_dut_back ip=None`), the L0 link gate then failed while the DUT was rebooting, and later the Pi's UB500 went deaf (0 advertisers in a raw `hcitool lescan` while the PC saw 21; USB re-enumeration, firmware reload, bluetoothd restart and `btmgmt power` did not revive it: it needs a re-plug), and the restore stage could not reach the DUT (its `sta_ble_handover` was put back by hand). The v2 client itself is verified by the probes: `ble_http_probe.py` on the Pi (tunnel-only, btmon arbiter: 0 holes in 12 + 12 downloads on the final firmware, host = air = client PDU counts) and the new `ble_http_probe_pc.py` on the PC's Intel adapter (6 x 64 KB + 2 x 512 KB + 6 x 64 KB, byte-exact, 0 holes; bleak/WinRT subscribes for INDICATIONS and refuses a CCCD write, so the PC runs the 5.6 KB/s indication path at its 45 ms interval). After Ali re-seated the dongle: **run 5 (2026-09-22 10:20) = `BLE API PASS` + `BLE STORAGE PASS`** with the WiFi witness alive, configure and restore stages OK. Downloads from the Pi in notify mode: 22-27 KB/s on /data and /sd (2.2-2.6 KB/s on indications the day before, same link), 512 KB byte-exact, SHA-256 witness OK, 0 holes over 2829 notifications (btmon count = device count), 0 tx_timeouts / rx_overflow, `out=notify`; uploads stay at ~3 KB/s (bleak/D-Bus WriteValue spacing, a Pi-client limit). Two pre-existing WARNs: the concurrent WiFi upload answered 404 instead of 503 (route path), and the simulator no longer serves DID F190 (`7F 22 31`; F187 positive). Regression the same morning: `.	est.ps1 blej2534` = `BLE J2534 PASS`, `RESULT: PASS` (the indicate-only FFF5/FFF6 channel on the changed GATT and channel layer) and `.	est.ps1 blecanraw` = `BLE CAN RAW PASS`, `RESULT: PASS` (the FFF1 data pipe). Runs 2-4 were plumbing: the test script's own v2 counter bug in the busy leg (the device's `400 hole` was correct), a crash on an unreachable witness (both fixed), and a `DUT unreachable` preflight while the STA sat on the dongle AP (the driver then hung 30 min; killed). |
| `.\test.ps1 blej2534` (= `ble_pi_run.py ble_j2534_pi.py --dut <ip> --stage configure\|ble\|restore`, ON rpi001) | **J2534 PassThru over BLE** (`ble_j2534_pi.py` → `BLE J2534 PASS`; ~5 min, 2 planned reboots): the `j2534` stream channel FFF5/FFF6 carrying `J2534_WIRE_PROTOCOL.md` (the TCP client of `j2534_bench.py` ported onto a bleak byte stream). Configure = `j2534_server.enabled` + `allow_lan` (the second-tester leg comes from the Pi's STA side over TCP) + `exclusive`, BLE on, `sta_ble_handover` OFF, one reboot; `/api/j2534` must show `listening` + `transport:"none"` and the channel must be in the GATT table (ble_j2534 keys on `j2534_server.enabled`). BLE leg = L0 link gate + channel present, L1 HELLO (wire v1) / OPEN (device 1) + `/api/j2534` `client_connected` + `transport:"ble"` (+ `autopid_paused`, WARN), L2 CONNECT CAN + WRITE_MSGS `7DF [02 01 00]` → RX_MSG from `7E8` starting `06 41 00 18 3F 80 03` (the ECU simulator) with the native `/api/can` tx counter as witness (WARN), L3 PASS filter 7E8 still answered / STOP / BLOCK 7E8 silences / DISCONNECT, L4 CONNECT ISO15765 7E0/7E8 + `22 F1 90` → `62 F1 90` + VIN, `10 02` → `50 02`, reflash gate `34 ..` → `ERR_NOT_SUPPORTED`, L5 a second tester over TCP while BLE holds the session → `ACK ERR_DEVICE_IN_USE` (0x1A) and the BLE session intact, L6 `METRIC hello_rtt_p50/p95`, `uds_request_to_rx_p50` (report only), L7 BLE link dropped mid-session (no CLOSE) → `/api/j2534` `client_connected:false` + `transport:"none"` within 5 s, a TCP HELLO/OPEN/CLOSE then succeeds, a second BLE session HELLO/OPEN/CLOSE clean. Restore as above. **Bench 2026-09-21: BLE J2534 PASS** (19 characteristics, MTU 517): every leg green; HELLO RTT p50 188 ms / p95 300 ms; `autopid_paused:true` during the session; the second tester's TCP HELLO answered `0x1A`; WARN only: the simulator's `0100` bitmask read `00 00 00 00` (not `18 3F 80 03`) and `22 F1 90` answered NRC 0x31 that day (the ECU-name DID `22 F1 87` is the PASS check). Same day on the refactored transport seam over TCP: `j2534_bench.py` → `J2534 TARGET PASS`, `j2534_gate_test.py` → `J2534 GATE TEST PASS`. Found on the way: `ble_j2534`'s channel registration must run in `_start()` (after the settings boot pass), an init-time `j2534_server_is_enabled()` always read false; and BlueZ's per-bond GATT cache hides newly appended characteristics until the bond is dropped (the bench pairs fresh) | main fw on DUT + rpi001 (UB500 + bleak) + the ECU simulator answering 7DF / 7E0 |
| `.\test.ps1 blecanraw` (= `ble_pi_run.py ble_can_raw_pi.py --dut <ip> --stage configure\|ble\|restore`, ON rpi001) | **Binary CAN records over BLE** (`ble_can_raw_pi.py` → `BLE CAN RAW PASS`; ~3 min, 2 planned reboots): the `can <-raw-> ble` bridge on the data pipe FFF1/FFF2, i.e. the compact dialect a phone app should use instead of slcan ASCII (one record per frame, `id u32 LE, flags, dlc, ts_us u32 LE, data`, 10-18 B, the timestamp taken in the CAN RX interrupt; `BLE_API.md` 4.1/4.2). Configure = BLE on + bridge row `br_ble_can` next to the 5 standing bridges (the `can` jack fans out) + `sta_ble_handover` OFF, one reboot; the bench then waits for the STA on the hotspot (a post-reboot resolve can land on the DUT's AP address, which the BLE leg takes the Pi off). BLE leg = pair fresh, subscribe FFF1, parse the record stream by `6 + dlc`: A the live bus (the simulator's broadcast) for 8 s: records/s, bytes/record, records straddling notifications, 0 malformed; B 20 x `7DF 02 01 00 ..` written as records 150 ms apart → 20 answers `7E8 06 41 00 ..`; C a 29-bit record steps the native `/api/can` tx counter; D `br_ble_can` stats. **Bench 2026-09-21: PASS** (three runs). Pre-timestamp layout: 59 records in 8 s at exactly 14.0 B/record, 0 malformed, 0 straddling, 20/20 answered p50 176 ms. Timestamped layout (final): 698 records in 8 s at exactly 18.0 B/record (ids 730/738/7A0/7A8/7B3/7BB/7C6/7CE), 0 malformed, 0 straddling, timestamps all non-zero and monotonic, device time span 8.08 s vs the Pi's 7.96 s over the same records (the ISR clock is right; a first run read 15.5 s over 8 s because the pipe was draining a backlog, so the check is judged only when records ~= bus rx), bus rx +687 vs 698 records, 20/20 answered p50 364 ms / max 771 ms, the 29-bit write stepped tx 20→21. A middle run on a burstier sim boot (7EC at 44 fps on top) got 16/20 at p50 552 ms: the reply shares the notification pipe's drop budget, so the check is >= 15/20 with 20/20 as a WARN. `send_errors` in the bridge stats are the frames pumped while no BLE client was connected (harmless, noted in the brief) | main fw on DUT (`can_manager.enabled`) + rpi001 (UB500 + bleak) + the ECU simulator on the bus |
| `.\test.ps1 blephy` (= Pi stages `ble_pi_run.py ble_phy_bench_pi.py --stage configure\|adv\|switch\|restore` ON rpi001 + PC legs `ble_phy_pc.py --phy 1m\|2m` on THIS PC's Bluetooth adapter) | **BLE 5 as settings: PHY + advertising sets** (`BLE PHY PASS`; ~9 min, 3 planned reboots): `ble_manager` v4 `phy` (1m\|2m\|coded\|auto) and `advertising` (legacy\|extended\|both), `TASK_ble5_phy_adv.md`. Configure = BLE on + ble_http on + `sta_ble_handover` ON (the product default: WiFi suspends during the link, the July 77 KB/s condition) + phy=1m/legacy; the DUT is reached over the hotspot or, when its STA sits on the client-isolated dongle AP after a reason-204 association fail, through its own AP (`reach()`), and a submit that changed nothing is not waited for as a reboot. `adv` = bond drop + fresh scan under btmon: the legacy PDU set (name, FFF0 UUID, `MeatPi`) must be on air and, with `both`, an extended (non-legacy) report with its secondary PHY too; then one UB500 connect reading `phy_tx/phy_rx` through the tunnel (`1m` must stay 1M; on `2m` the UB500 drops the link = the known rig WARN). PC legs (WinRT custom pairing that PROVIDES the passkey; bleak 3.0.2 in the v5.5.3 venv): the live PHY through `GET /api/ble` over the tunnel, 1 x 64 KB upload (write-without-response), 3 x 64 KB download (indications, byte-exact), 10 x `GET /api/status` RTT, `METRIC phy{1m,2m}_*`. **Bench 2026-09-21: PASS.** legacy: 2 legacy / 0 extended reports; both: 3 legacy / 1 extended (secondary PHY LE 2M). PC Intel adapter, WiFi suspended: 1M upload 50.6 KB/s, download 5.58 KB/s, RTT p50 484 ms; 2M upload 51.9 KB/s, download 5.69 KB/s, RTT p50 531 ms; 0 tx_timeouts / rx_overflow / resyncs; restore clean (0 own-tag E lines, 0 new faults, 0 unexpected resets). **Reading:** the PHY changes nothing measurable on this device because neither direction is air-time bound: uploads run at ~50 KB/s on either PHY (BlueZ's D-Bus WriteValue path on the Pi gives 1.7-2 KB/s, a client artefact: 222 ms between writes in btmon), downloads are ONE 490 B indication per ATT round trip (btmon via `ble_phy_trace_pi.py`: the central confirms in 0.3 ms, the device sends the next indication a median 148 ms / p90 380 ms later), so 2.5-5.7 KB/s at any PHY and ~0.5 s per tunnelled GET. Faster downloads need the OUT direction on notifications with app-side credits (protocol change, open). Three firmware traps found by this bench (all fixed): `BLE_HS_ADV_TX_PWR_LVL_AUTO` in the AD makes the host issue the legacy Read-Adv-Tx-Power HCI command that the controller refuses under the extended-adv API (first build advertised nothing); `phy=1m` must SET the 1M-only default preference or a PC Intel adapter moves the link to 2M on its own; the connect-time Security Request broke Windows apps' custom pairing (WinRT `Failed`, no ceremony asked) and is gone. **Rig limit:** the TP-Link UB500 (RTL8761BU fw 0xdfc6d922) completes the PHY update to 2M and then both sides Connection Timeout (0x08) on the first 2M event, WiFi on or off; the PC's Intel adapter runs 2M fine, hence the two-host bench. |
| `.\test.ps1 blethru` (= `ble_sim_central_bench.py --dut <ip>` on the PC; the ECU simulator at USB-NCM 192.168.8.1 is the BLE central) | **BLE throughput with a controlled central** (`BLE THROUGHPUT PASS`; ~10 min): the simulator runs `ble_central_bench` (meatpi-components; NimBLE central with esp-idf `throughput_app` link tuning: MTU 517, DLE 251, connection interval 7.5 ms, `phy` 1m then 2m, KeyboardOnly passkey injection). Per PHY: connect/pair/discover (peer mtu/itvl/phy/dle checked), `notify` 60 s (the DUT blasts FFF1 via `POST /api/ble/blast` sent through the tunnel; the central counts), `write` 60 s (write-without-response to FFF2), `read` 10 s (DIS 2A29), `tunnel_up` + `tunnel_down` 64 KB and 512 KB (byte-exact; since 2026-09-22 the downloads run in **notify mode** = FFF3 CCCD `0x0001`, the client pays CREDIT frames and the v2 frame counter must show 0 holes) + one 512 KB download on indications for comparison, 0 pairing failures, the DUT's `/api/ble` counters afterwards. Thresholds at 1M: notify >= 300 kbps, write >= 400 kbps (the reference's ~340 / ~500 minus margin); 2M reported with 2M/1M ratios. **Bench 2026-09-21 (runs 5 and 6): every radio leg PASS on both PHYs, every tunnel transfer byte-exact.** 1M: notify 485-488 kbps (reference ~340), write 420 kbps (reference ~500, threshold 400 met), tunnel up 548/486 kbps, tunnel down 236/221 kbps, DIS read 15.5 ms per round trip. **2M: notify 967 kbps, write 974 kbps, tunnel up 969/827 kbps, tunnel down 241/225 kbps** (2M/1M: notify 1.98x, write 2.32x; downloads are indication-round-trip bound, so the PHY does not move them). The verdict line still says FAIL for the two DUT-settings plumbing steps (`dut_phy_auto_for_the_run`, `dut_phy_restored`): the WiCAN's STA sits on the client-isolated dongle AP after every reset and the AP-path fallback timed out; the radio results are unaffected. **Found on the way:** (1) a 514 B write-without-response is dropped silently by the WiCAN (490 B cap, NimBLE attribute limit 512) - the first tunnel stall; (2) a BLE stop/start on the WiCAN (a station joining its AP) is followed by `E BLE_INIT: Malloc failed` and the next big download dies (`httpd send: 11`, `tx credit timeout`); without the restart the same 512 KB download passes; the WiCAN boots with ~8 KB free internal heap (`WICAN MEM internal_free=7991`) - open firmware item; (3) the simulator now has OTA (`build_flash.ps1 -ota`, two OTA slots), so no BOOT button. **Runs 7-8 (2026-09-22, ble_http protocol v2, notifications + credits):** downloads now run at the radio's rate: **1M 466-483 kbps (60 KB/s, 2.2x indications), 2M 951-971 kbps (119 KB/s, 4.3x)**; the indicate comparison leg stays at 214-225 kbps on either PHY. Run 7 lost ONE PDU in 2 of 4 notify downloads (the counter flagged both, `exact=False holes=1`); the tunnel-only `ble_http_probe.py` with btmon on the Pi proved the WiCAN's host counted PDUs that never reached the air (7 of 24 downloads, 1-4 consecutive PDUs; BlueZ clean). Root cause: the ESP32-S3 controller mallocs each ACL TX buffer from the internal heap at TX time with `CONFIG_BT_CTRL_BLE_STATIC_ACL_TX_BUF_NB=0`, and the WiCAN has ~8 KB free; `=12` -> 0 holes in 12 probe runs and **run 8: every notify download byte-exact, 0 holes on both PHYs** (1M notify 479, write 389, up 554/480, down 477/466; 2M notify 966, write 956, up 1038/863, down 961/971 kbps). Run 8's only FAIL line is `ref_write_ge_400kbps_1m` at 389 kbps (417-420 in runs 6-7); re-run alone right after (simulator at 1M, 3 x 60 s): **490, 490, 490 kbps** (the reference's ~500), so the 389 was a one-off; the same session's 512 KB notify download: byte-exact, 0 holes, 478 kbps. The final build (TX primitives split into `ble_manager_gatt_tx.c`) re-verified with the probe: 12 of 12 clean, 1747 PDUs, host = air = client. |
| `.\test.ps1 all` | host + every target app + hil (live/perf are separate — `all` leaves a test app, not the main firmware, on the DUT) | both |

Options: `-Port COM10` (DUT serial; when absent the preflight falls back to the
CH344 quad-UART channel B, then CH342 A, both found by PnP name - see the
physical-setup matrix), `-BenchHost rpi001`, `-DutIp` (default: resolve on the
Pi, the hotspot's DHCP lease for hostname `wican_<DeviceId>` first, then
mDNS minus the DUT's own AP subnet; pass an address to pin it), `-DeviceId 68ee8f5a653d` (the bench
unit), `-NoReport`, `-SkipBenchCheck`.

## The bench preflight (runs first, every kind)

Every run (except `list`) starts with a ~10 s **bench preflight** that
verifies the PHYSICAL bench matches what the selected kind needs, fails fast
with a specific message when it doesn't, and records the findings in the
report's conditions. `-SkipBenchCheck` bypasses it.

What it detects: available COM ports (auto-falls-back `-Port` → COM10 → the
CH344 quad-UART's channel B → CH342 A; every fixture is matched by its PnP
NAME because COM numbers move on each re-plug - on 2026-09-19 the CH344 went
from COM12-15 to COM252-255 and the FTDI PSU port from COM2016/17 to COM50 -
and nothing is opened, since opening the WiCAN console resets the DUT),
whether a CH342 is enumerated (= the DUT's USB connector is cabled to the
PC), the DUT's address (hotspot lease / mDNS by device id), Pi reachability, DUT
online + firmware version, USB-Ethernet adapter presence (`/api/usb`),
mosquitto on :1883. It also prints the FTDI `USB Serial Port` (the OWON PSU
on this rig) and `MeatPi USB-CAN Device` (the ECU simulator's CDC console)
ports it sees.

**The port detector** behind it is `tools/testbench/detect_ports.py`. Run
it by hand after re-plugging anything: it prints a role table
(`wican_console`, `dongle_uart`, `psu`, `ecu_sim_cdc`, `ch342_console`,
`ch342_obd`, spares), `--json` for machines, `--role psu` for shell
substitution, `--probe-psu` to confirm the OWON by `*IDN?` (the only mode
that opens anything, and only FTDI ports), `--dut-ip` to add the WiCAN's
hotspot address via the Pi; every run refreshes the gitignored
`tools/testbench/bench_ports.json`. The manual benches default their
`--psu-port` / `--bridge` / `--elm-port` to `auto`, which asks the same
detector through `lib/bench_ports.py` (`resolve(value, role, default)`),
and the BenchBoard PSU instrument's `port` is `auto` too. No bench script
carries a COM number any more; a new fixture gets a role in the detector.

**The physical-setup matrix** — the DUT's USB-C connector can serve exactly
ONE role at a time, so some legs are mutually exclusive by wiring:

| Connector wiring | COM ports | Enables | Excludes |
|---|---|---|---|
| → PC cable (CH342 device role) | COM7 (A=console) + COM6 (B=`usb_obd` port) | usb_obd/port-B passthrough legs; flash via CH342 | `usbeth` (auto-SKIPs), espnetlink |
| → USB-Ethernet adapter (host) | CH342 gone | `usbeth` bench, eth-uplink testing | usb_obd/port-B legs |
| → espnetlink dongle (host) | CH342 gone | `espnetlink` bench (RNDIS LTE data + CDC-ACM `acm` console; `--prove-lte` isolation leg) | usbeth, usb_obd/port-B legs |

**The connector's DEVICE-side role is SOFTWARE-switched** (found
2026-07-21): with the PC cable attached, `usb_host_manager` settings
pick the mux -- `enabled:false` (default) = CH342 (COM6/7);
`enabled:true, role:"device", device_class:"ncm"` = the ESP's native
USB presents CDC-NCM to the PC (adapter at 192.168.82.2, DUT at
192.168.82.1; COM6/7 VANISH -- console-reset recovery is gone, WiFi +
PSU-cycle remain). Switching BACK to CH342 needs the settings change
**plus a real power cycle** (the mux GPIO only re-defaults at POR).
`usb_ncm_conservation_test.py` uses this flip.

The **UART0 serial path (the external adapter on COM10 when fitted, else the
CH344 quad-UART's channel B, PnP name `USB-Enhanced-SERIAL-B CH344`: 2 Mbaud
console, esptool at 460800 only; channel A is the dongle's now-unwired UART,
C/D spare)** works in EVERY wiring — target/hil/all always run through it.
Opening it resets the DUT, whatever DTR/RTS you preset. Legs whose
fixture is absent report **SKIP** (yellow), not FAIL: the run stays green and
the report says exactly why a leg didn't run.

A failing stage no longer aborts the run: every stage is recorded in the
report and the runner exits non-zero at the end if anything failed.

**Canonical bench settings** (what the bench DUT persists so `live` + `perf`
pass end to end; verified green 2026-07-05): websocket channels `ws_obd` +
`ws_can` + `ws_cli` all enabled; bridges `br_obd = obd<->ws_obd` (real chip
over WS: ws_live + obd_poll), `br_cli = cli<->ws_cli`, `br_echo =
ws_can<->obd0` (the Pi-held echo path for the WS latency numbers); socket
`obd0` enabled; mqtt enabled against the Pi broker. The single-consumer rule
is why this exact split exists — `obd` can feed only one bridge, so USB
passthrough (`obd<->usb_obd`) or a BLE bridge (`ble<->obd0`) require
swapping a bridge out; put this set back afterwards. 2026-07-07: the
canonical set GREW — socket `slcan0:3333` + can_manager enabled.
autopid stays `backend=obd_chip` canonically.

Components with a `target` app today: `settings_manager`, `filesystem`,
`http_server_manager`, `wifi_manager`, `log_manager`, `dev_status_manager`,
`restart_tracker`, `obd_chip`, `api_http`, `socket_manager`,
`bridge_manager`, `ble_manager`. (`restart_tracker`, `log_manager` and
`api_http` are two-phase — they reboot the DUT mid-test (PSRAM `.noinit`
survival / the real settings submit-then-reboot cycle); `obd_chip` requires
the live OBD bench — chip + ECU simulator, see
`components/obd_chip/test_apps/README.md`; `socket_manager` and
`bridge_manager` are lwIP-loopback self-contained.)

## The system bench (the composed main firmware, end to end)

`tools/testbench/system/system_bench.py` (run ON rpi001, after `erase-flash` +
flashing the root `build/`): the real product journey — factory-AP
onboarding at `192.168.0.10` → configure wifi + a bridge over `/api` →
submit-reboot → STA join → OBD chip + live ECU through the configured TCP
bridge → OBD-polling perf → memory/fragmentation stability asserts.
Expected final line: `SYSTEM BENCH PASS` (first verified 2026-07-04).
Manual-only (it erases and reconfigures the DUT) — not wired into test.ps1.

Other manual benches (run ON rpi001 unless noted): `ble_blast.py` (clean-radio
BLE TX ceiling — needs the `blast` CLI fired over UART from the PC side, see
its docstring for the choreography), `socket_bench.py`, `ble_perf.py`,
`obd_bench_check.py` / `obd_vt_isotp_check.py` /
`obd_monitor_injection_check.py` (PCAN + ECU sim bench, run from the PC).
`sleep_bench_test.py` (→ `SLEEP BENCH PASS`: run from the **PC** — the
OWON P4305 bench PSU (the FTDI `USB Serial Port`; its COM number drifts, COM50
since 2026-09-19, `*IDN?` answers `OWON,P4305`) walks the DUT's 12 V input across the
sleep/wake thresholds, proves light-sleep entry by supply current and
voltage wake via `/api/restart/history`; needs sleep_manager enabled +
the DUT on a bench hotspot; temporarily sets `sleep_delay_min=1` and
restores it; leaves the bench at 13.5 V — see `components/TESTBENCH.md`
§1 for the PSU and its 15 V ceiling).
`can_conservation_test.py` (→ `CAN CONSERVATION PASS`: run from the
**PC**, IDF venv python + PCAN on the DUT bus — exactly-N frame
accounting at three load tiers; the §7 performance-truth regression
net; first run verified 2026-07-21).
**Counter-delta rule (2026-07-22, from the ISR drain-loop postmortem):
a deterministic, unexplained counter delta FAILS the bench — never
park it as noise.** The 30x rx_count drain-loop bug had a stable ×432
fingerprint that was seen and parked; every conservation bench asserts
exact accounting (sent == rx + counted losses) for this reason. A
delta that repeats across runs has a cause; find it or fail.
`wifi_conservation_test.py` (→ `WIFI CONSERVATION PASS`: Pi iperf2
UDP client vs the DUT's fw iperf server over ws_cli, byte accounting
both sides, delta < 5%; verified 2026-07-21).
`usb_ncm_conservation_test.py` (→ `USB NCM CONSERVATION PASS`: PC
sends exactly-N UDP bytes over the CDC-NCM link to the DUT's fw iperf
server, byte accounting via `iperf -r` over ws_cli THROUGH the same
link; needs the software role-flip above; 0.001% delta verified
2026-07-21).
`dwc2_hammer_test.py` (→ `DWC2 HAMMER PASS`, `.\test.ps1 dwc2`: run
from the **PC** with the espnetlink dongle on the USB host port +
`usb_host_manager`/`usb_acm_cli` enabled — targeted stress of the
2026-07-17 `usbh_kill_urb`-vs-channel-IRQ crash class: ACM
mixed-timeout kill floods, NCM UDP OUT blasts, mid-flight `iperf -a`
teardown churn; asserts no re-enumeration/reboot/faults; first PASS
2026-07-22, 2 rounds 30/30).
`sleep_matrix_test.py` (→ `SLEEP MATRIX PASS`: run from the **PC**,
~1 h — sleep entry under load, one scenario per subsystem that can be
mid-flight when the voltage drops, incl. the 10-min WireGuard asleep
soak; scenario map + not-yet-automated rows in
`tools/testbench/sleep/SLEEP_MATRIX.md`; `--only key1,key2` runs a subset).
`ts_bench_target.py` (→ `TS TARGET PASS`: brings the LOCAL-ONLY headscale
up on the Pi, disposable preauth key kept Pi-side, DUT to
`type=tailscale`, asserts CONNECTED + tailnet IP + registration, and —
when the static tailscale build is present in /tmp — a real peer with
ICMP + HTTP through the tunnel; full teardown incl. headscale down.
Run with the DUT ip; needs sudo on the Pi).

**VPN bench ruling (meatpi 2026-07-21): the betty VPS rigs are the
CANONICAL VPN benches** — `wg_public_soak.sh` (self-verifying since
2026-07-21: handshake + ICMP-through-tunnel after `up`; split mode
keeps the AllowedIPs NETWORK address = the BUG_WG_NETIF_ADDR
regression gate) and `ts_public_soak.sh` (fresh headscale state each
run = the phantom-peers gate; full register/tunnel/peer legs). They
exercise the real internet path (NAT, public endpoint, keepalives;
set `WG_BENCH_DNS=<name>` to run the firmware DNS-endpoint path too).
The rpi001-local rigs above (`vpn_bench_target.py`,
`ts_bench_target.py`) are the OFFLINE FALLBACK when betty is
unreachable. The sleep matrix's `wg_betty_soak`/`ts_betty_soak` legs
build on the betty rigs.

## BenchBoard: the interactive test dashboard (2026-07-26)

**`C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe tools\benchboard\benchboard.py`**
→ `http://127.0.0.1:8777/` — a local web UI over the whole bench:
catalog of tools + tests from the repo-root `benchboard.toml` (grouped,
searchable, per-test parameter forms), live-streamed runs with
PASS/FAIL/METRIC highlighting and cancel, a queue with resource locks
(one DUT = sequential by default), verdict classes incl. a distinct
**RIG FAULT** badge, sqlite history with full logs and captured METRIC
lines, instrument health chips, and a per-run **progress bar** (flow
steps and `PROGRESS k/n` output lines are exact; otherwise a dim
elapsed/budget estimate). Generic tool — design record
`docs/benchboard_plan.md`, usage `tools/benchboard/README.md`;
Python 3.11+ (stdlib only; plain `python` may be the Store 3.10 — use
the IDF v6 venv). Its own test suite: `tools/benchboard/tests/`
(`benchboard-selftest` in the UI). Adding a bench? Print a final
`<NAME> PASS` line, exit non-zero on failure, add a `[[test]]` entry.

**Full coverage since 2026-07-27** (`docs/benchboard_full_coverage_plan.md`):
every test type in this doc is now on the dashboard — target test apps
via the `target-cycle` flow (flash one component's app on the `serial`
instrument's port, capture the verdict, then ALWAYS restore the main
firmware and wait for the DUT to return, even when the app fails
mid-run), the web UI jsdom smoke as `webui-smoke` (regenerates
preview.html each run), and the public-export release gates as the
`export-gates` action (git stays manual).

**Narration convention** (2026-07-26, "show what's going on"): a bench
must never go silent for long — (1) announce each phase before starting
it ("tier 2/3 (50% load): sending exactly 5000 frames…"), (2) print a
heartbeat every ~5–10 s inside any window longer than ~10 s (storm
counts, reboot waits, measurement windows), (3) print `PROGRESS k/n`
when milestone k of n completes — BenchBoard's bar tracks it exactly
(flows get this for free from their steps). New prints use
`flush=True`; BenchBoard runs scripts with `-u` but console pipes may
buffer.

**Instruments = plugin folders** (`tools/testbench/instruments/<id>/`,
one folder per piece of bench equipment: `instrument.toml` + probe
script; `_template/` is the starter, `README.md` there is the format
spec). **Porting to another bench edits NOTHING tracked**: copy
`benchboard.local.example.toml` → `benchboard.local.toml` (gitignored)
and set your COM ports / IPs / device id / `enabled=false` for gear you
don't have. `${python}` and `${<instrument>.<key>}` substitute into
every manifest cmd, so each machine-specific value lives in exactly one
place.

## Bench layout: `tools/testbench/` (domain folders since 2026-07-26)

| Folder | Contents |
|---|---|
| `lib/` | shared plumbing — `benchlib.py` (timeout policy + RIG/DUT classification), `bench.py`/`dut.py` (pytest fixtures), `wsmin.py`, `owon_psu.py`, `serial_capture.py` |
| `pi/` | the rpi001 rig toolkit (bench-health / recovery ladder / udev roles), installed by `deploy_bench_pi.sh` |
| `actors/` | bus responders/generators the benches spawn — `pcan_obd_ecu.py`, `pcan_uds_ecu.py`, `pcan_dbc_broadcast.py`, `pcan_reflash_ecu.py`, `wg_bench_server.sh` |
| `fixtures/` | test data (DBC files, …) |
| `system/` | composed-firmware benches — `system_bench.py`, stack audit, event manager, data logger, log sinks |
| `can/` | CAN/TWAI + translators — conservation, bus-off recovery, canfan, slcan, DBC, mqtt_can |
| `obd/` | OBD chip / ELM / autopid / DTC / UDS benches (+ `.be` scripts) |
| `ble/` | all BLE legs incl. `obd_ble_bridge_test.py` |
| `wifi/` | WiFi + network-transport legs — conservation, gate, profile A/B, mqtt/ws/cli/socket benches |
| `sleep/` | sleep bench + matrix (+ `SLEEP_MATRIX.md`) |
| `usb/` | USB device/host — NCM conservation, dwc2 hammer, espnetlink, usb_eth, j2534 |
| `vpn/` | WG/TS bench targets + the betty soak scripts |
| `ha/` | Home Assistant webhook benches |

`requirements.txt` and `run_host_tests.sh` stay at the root. Scripts still
run standalone (`python3 tools/testbench/<dir>/<script>.py`); sibling
imports are same-dir, shared code is imported from `lib/` via a relative
`sys.path` shim inside each script that needs it.

## Bench rig: control plane, recovery ladder, timeout policy (2026-07-26)

The bench-Pi standardization plan (`docs/bench_pi_standardization_plan.md`)
is EXECUTED. What it changed:

- **Ethernet control plane.** ssh `rpi001` = **192.168.90.2** over the
  direct PC↔Pi cable (static both ends, PC = 192.168.90.1, NO gateway on
  either side — the link must never carry a default route). A dead radio
  can no longer take the bench offline: `rfkill block all` leaves ssh,
  sync, and `sudo reboot` fully working (verified). `rpi001-wifi`
  (192.168.0.83) is the explicit fallback alias.
- **Radio role names** (udev-pinned by MAC on the Pi): `wint0` = internal
  brcmfmac (DUT hotspot `wican-bench-w0`, pinned **10.42.0.1/24** → DUT =
  10.42.0.62 again, the lease flip-flop is dead), `wtest0` = USB stick #1
  (`wican-bench` twin, pinned 10.42.1.1/24), `wtest1` = USB stick #2
  (home-LAN uplink, NON-load-bearing). Old `dnsmasq-wlan{0,1}.leases`
  paths keep working via boot-created symlinks.
- **Recovery ladder** (all reachable over ethernet, all idempotent):
  `bench-health` (seconds; `RIG OK` / `RIG FAULT <comp>: <reason>`;
  truth-tests each active hotspot by **BSSID** from a second radio —
  nmcli "activated" lies), `bench-radio-reset <role> [--usb]` (profile
  bounce → usbreset re-enumeration), `bench-recover` (probe → targeted
  level 0 → usbreset → level-1 Pi reboot with a marker + 10-min
  reboot-loop guard), `bench-startup.service` (asserts canonical state at
  boot, logs `BENCH READY`), `wican-bench-watchdog` (beacon watchdog v2,
  primary = the internal radio). Sources live in `tools/testbench/pi/`;
  install with `deploy_bench_pi.sh`.
- **One bench library, one timeout policy: `tools/testbench/lib/benchlib.py`.**
  New scripts MUST use it; existing ones migrate opportunistically
  (system_bench is migrated). The constants (single source of truth):

  | Constant | Value | Rationale |
  |---|---|---|
  | `SSH_CONNECT` | 10 s + 1 retry | Windows OpenSSH cold-handshake flake |
  | `HTTP_TRY` | 8 s | one API call on a healthy link |
  | `HTTP_RETRY_BUDGET` | 30 s (3 s apart) | rides a route flap; longer = rig fault |
  | `DUT_REBOOT_WINDOW` | 180 s | healthy ≈ 20–40 s; beyond 180 s = rig fault |
  | `OTA_UPLOAD` | 300 s | 3.4 MB at worst-case RF |
  | `BLE_SCAN_TRIES/WINDOW` | 3 × 8 s | existing ble_bench practice |

  **The classification rule:** when a step exhausts its budget, benchlib
  runs `bench-health` BEFORE failing. `RIG FAULT` → `bench-recover`, retry
  once; still faulty → the run fails as **`RIG FAULT <detail>`** (e.g.
  `SYSTEM BENCH RIG FAULT`, exit 2) — never blamed on firmware. `RIG OK` →
  the original DUT-side failure surfaces immediately. HTTP status errors
  (4xx/5xx) always surface immediately — they are answers, not transport
  faults.

## One-time setup

- **Dev host:** `pip install -r tools/testbench/requirements.txt` into the IDF
  python env (`C:\Espressif\tools\python\v6.0.2\venv`). On this LAN add
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org`.
- **Bench Pi (rpi001)** — already provisioned; to rebuild from scratch:
  `sudo apt install gcc cmake ninja-build libbsd-dev`, clone ESP-IDF v6.0.2 to
  `~/esp-idf`, run `python3 tools/idf_tools.py install-python-env`, and keep
  the `dnsmasq`/`hostapd` **services disabled** (they break NetworkManager's
  hotspot DHCP; the binaries stay available). BLE = TP-Link UB500 dongle
  (onboard BT disabled) — see `components/TESTBENCH.md` §2.
- After a bench session, reflash the main firmware
  (`build\` at repo root → `python -m esptool ... write-flash "@flash_args"`).

## Reaching the DUT's AP (factory or configured)

**Never join the DUT's AP from the dev PC's own WiFi.** The PC's WiFi *is*
its internet uplink (ssh to rpi001 now rides the ethernet control plane,
but the internet doesn't) — joining the DUT AP takes the dev session
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

(Join on `wint0`, the internal radio — the USB sticks intermittently wedge
as scanners; 2026-07-26 lesson, same reason system_bench onboards there.)

The route pinning matters: the AP subnet `192.168.0.0/24` collides with
LANs the Pi (and the dev PC) may already sit on — an unpinned second /24
blackholes the Pi's own uplink including the ssh session driving it.
When the DUT is already a STA on the bench hotspot, skip the AP entirely
and talk to its `10.42.x.x` lease from the Pi.

## Autopid testing caution (2026-07-21)

**`pause_follow_sleep` is ON by default**: autopid pauses PID REQUESTS
whenever the battery reads below the sleep voltage (default 13.10 V) —
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

`python tools\testbench\obd\elm_app_bench.py [dut[:port]] [--dut-ip 10.42.1.194]`
(→ `ELM APP PASS`, ~1 min; the request loop `elm_latency_probe.py` is
copied to rpi001 and runs there over the hotspot, the API is read
through the tunnel). Born from the field report "Car Scanner feels slow,
the RPM dial is choppy, values jump" against the component firmware.
Reproduced on the bench BEFORE the fix with autopid polling its 11
standard PIDs beside the app: 80 of 200 hint-less `010C` requests (8 of
200 hinted `010C 1`) came back with autopid's own lines (`0105 / 41 05
82`), `STOPPED` or `NO DATA` — the chip fans every line out to every
subscriber and an app command landing mid-poll stops the poll — versus
0 of 200 with autopid paused; and every response carried the RX task's
20 ms `uart_read_bytes` budget (hinted p50 26 ms for a ~3 ms chip
answer). Fix: autopid yields the chip while a bridged client is active
(10 s idle window, legacy parity), the chip RX task reads on UART
events, `TCP_NODELAY` on socket clients. Legs: preflight (autopid
enabled + polling, obd0 up) → hinted loop beside the configured autopid
(zero foreign/STOPPED/NO DATA, p50 ≤ 12 ms, p95 ≤ 40 ms, ≥ 40 req/s,
`paused_client` seen, `polls_ok` frozen) → hint-less loop (clean, p95 ≤
120 ms — the chip's own multi-ECU wait) → resume ≤ 15 s AND the resumed
polls succeed (the app leaves the chip with `ATS0`/`ATH1`/…; the poller
re-sends its protocol prelude — without that every resumed poll failed
to parse, 110 failures in a row on the first fixed build). Needs the ECU
simulator answering on the chip's bus, `autopid.enabled` with PIDs
configured, `obd0` + `br_tcp_obd` (defaults). **Bench 2026-09-08: PASS**
— hinted loop 129 req/s, p50 5.2 ms, p95 13.8 ms (one 214 ms WiFi
outlier in 200), 0 foreign answers, `paused_client` seen, `polls_ok`
frozen; hint-less loop 23.4 req/s, p50 37.6 ms, p95 69.6 ms, 0 foreign
answers; resume 9.7 s after the last command, +41 ok / +0 failed polls
after it; 0 new `E (` lines on that boot. Before the fix on the same
rig: hinted p50 26 ms at 35 req/s with 8/200 foreign answers, hint-less
p50 69 ms at 16 req/s with 80/200.

**Reference-adapter comparison** — `tools/testbench/obd/elm_compare.py`
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
0.1 — a flat floor that is the FTDI latency timer's 16 ms default, not the
chip (the WiCAN after the fix: 5.2 ms at 129 req/s over WiFi); hint-less
`010C` p50 47.8 ms vs the WiCAN's 37.6; ATMA flood PASS at 1000 and 2500
frames/s (exactly-N) and `BUFFER FULL` after 2047 frames at 4000/s — its
1 Mbaud UART carries ~100 KB/s and 4000 frame lines/s need 116 KB/s,
where the WiCAN's chip at 2 Mbaud passed; 4 KB VT legs byte-exact both
ways (tx 4064 B 594 ms, rx 4094 B 556 ms, the same ~420 ms ATST-dominated
floor as the WiCAN's 630/557 ms). Set the FTDI latency timer to 1 ms
before quoting its hinted latency against the WiCAN.

## Large-payload OBD bench: VTFullyRequestCk / 4 KB ISO-TP (PC-run, PCAN, 2026-09-08)

`python tools\testbench\obd\vt_large_bench.py [dut[:port]] [--dut-ip 10.42.1.194] --ecu pcan [--pcan PCAN_USBBUS2] [--tx 64,512,2048,4064] [--rx 64,512,2048,4094] [--capture-args "--no-headers --spaces"]`
(→ `VT LARGE PASS`, ~2.5 min; v6.0.2 IDF venv python). The MIC chip's
4 KB ISO-TP paths end to end through the product path (TCP:35000 → bridge
→ UART → chip → CAN; `vt_large_capture.py` runs on rpi001) against
`pcan_memory_ecu.py`, a python-can/isotp UDS "memory ECU" on 7E4/7EC
(byte at address A = A & 0xFF; `0x23` serves it up to 4094 B, `0x36`/`0x3D`
verify what they receive and send the tester box's `7F 36 78` pending
first). The simulator's ECU is switched off for the run through its own
settings API (it answers the whole 7E0–7E7 range) and restored after.
Default framing = the MTPS-OBD tester's (`wican_pro_tester/MTPS-OBD+Log.TXT`):
`ATH1 ATS0 ATCAF1 ATAL`, `ATSH7E4`, `VTFullyRequestCk<LLLL>36<bsc><data><CCCC>`,
NO `1` hint on the reads. Legs: tx 64/512/2048/4064 B (the ECU must
answer `76 xx` after its pending = every byte verified), rx
64/512/2048/4094 B (byte-exact at the client), UART overflow / fan-out /
socket drop counters, byte conservation both ways, and the command-engine
path via `/api/autopid/test` (WARN: that route's raw buffer is
AP_RESP_MAX 1024 chars). **Bench 2026-09-08: PASS in both header modes**
— tx 4064 B: 8 157-char line, chip transmit ~220 ms (+ the ATST wait ≈
630 ms wall); rx 4094 B: 586 raw lines / 11.7 KB (headers on) or 587 `N:`
rows / 14.7 KB (headers off), ~555 ms wall, 0 overflows, 0 drops, bytes
conserved (chip rx == socket out). Chip facts learned: the `1` hint ends
a multi-frame response after its FIRST FRAME with headers on (2 KB → `NO
DATA`); assembly + automatic flow control only for 7E0–7E7/7E8–7EF
(740/748 printed raw First Frames, `ATFCSH/FCSD/FCSM1` did not help);
the engine accumulator OBD_RESP_MAX was 4 KB and truncated these
(`response … exceeded 4096 bytes`) — now 16 KB. Open: autopid's own
AP_RESP_MAX (1024 chars) caps what the runner / test route can read.

## ATMA flood bench (PC-run, PCAN, 2026-09-08)

`python tools\testbench\obd\atma_flood_bench.py [dut[:port]] [--dut-ip 10.42.1.194] --source pcan [--pcan PCAN_USBBUS2] [--rate 2500] [--count 10000] [--id 0x123]`
(→ `ATMA FLOOD PASS`, ~30 s; use the v6.0.2 IDF venv python — python-can
+ isotp live there). Streams a CAN flood through the MIC chip's monitor
mode (`ATH1 ATS1 ATCAF0 ATMA`) to an ELM app on TCP:35000 (`atma_capture.py`
runs on rpi001, the app's seat) and places every lost frame at a hop:
PCAN sends `--count` frames whose data bytes 0..3 are a big-endian
counter (exactly-N: every counter once, in order), `/api/obd_chip` gives
the chip's UART bytes, overflows and per-subscriber fan-out drops,
`/api/bridges` + `/api/sockets` the pump and socket counters, and the
native TWAI's `/api/can` `rx` is the on-bus witness that separates a
chip-side loss from a bus-side one. `--source sim` uses the simulator's
`broadcast_enabled` instead (static payload, 5 ms floor — rate and line
integrity only). **Bench 2026-09-08 (final build): PASS at 1000, 2500 and
4000 frames/s** — 5000 / 10000 / 12000 frames, every counter seen once
in order, 0 malformed lines, 0 UART overflows, 0 fan-out drops, 0 socket
drops, bytes conserved chip → socket → client (348 KB at 4000/s = the
chip printing 56 KB/s with headers on), witness == sent. Before the
raw-bridge coalescing the same 2500/s run dropped 6052 chunks at the TCP
bridge's fan-out queue (one socket send per ~29-byte frame line) and
delivered 3684 of 10000 with spliced lines; the first coalescing cut
panicked (`prvNotifyQueueSetContainer`) — a member queue was read without
the queue set — fixed. Needs PCAN_USBBUS2 on the DUT bus, `can_manager`
running for the witness (optional), autopid may stay enabled (it yields).

## Autopid DTC/DBC PCAN benches (PC-run, 2026-07-22)

`tools/testbench/obd/dtc_bench_test.py [dut_ip] [pcan]` (→ `DTC BENCH PASS`)
and `dbc_bench_test.py` (→ `DBC BENCH PASS`) drive the DUT over HTTP
with PCAN as the "car" side; run them from the PC with the IDF venv
python (has python-can). When the DUT USB is in CH342 mode, flip it to
NCM first (usb_host_manager `{enabled:true, role:"device",
device_class:"ncm"}` + submit → PC talks to `192.168.82.1`; flipping
back needs the settings revert + a REAL power cycle — the bench PSU's
output off/on works remotely).

- **DTC**: legs 0-7 (gates, scan, conditional clear, new-code events)
  + **leg 6c multi-ECU** — `pcan_obd_ecu.py` answers as TWO responders
  (`--resp2 0x7EA`, own DTC lists; keep each stored list <= 2 codes so
  responses stay single-frame) with `dtc_rxheader` cleared; asserts the
  merged report (dedup pinned by a shared code, `ecus >= 2`, MIL OR +
  count sum). The hardware ECU-sim box (7E8) is a third live responder
  — list assertions are supersets on purpose.
- **DBC**: fixture has a real mux switch + an extended-mux message;
  asserts m1 signals compile WITH `mux_expr`/`mux_val`, extended stays
  listed-with-reason, and the LIVE leg broadcasts BOTH mux pages
  (`pcan_dbc_broadcast.py --alt`) with a decoy value on the wrong page
  that must NEVER leak into the parameter. Assertions are DELTA-based
  against the pre-bench config, so debris from an earlier aborted run
  can't fail it.
- `dbc_real_test.py` (also a `live` stage) — since simple-mux support,
  the four real DBC fixtures are **100% supported** (tesla 240/240 incl.
  its 28 muxed signals, ford 2150/2150, bmw, j1939) and stay field-exact
  vs the host reference parser.
- `wifi_profile_ab_test.py` (→ `WIFI PROFILE AB DONE`) — decision-grade
  `wifi_ram_profile` full-vs-lean comparison: internal-RAM floors,
  iperf2 TCP both directions, UDP + loss, HTTP RTT; switches the
  profile via submit-reboot and restores the starting profile.
- `mqtt_can_bench_test.py` (→ `MQTT CAN BENCH PASS`) — the CAN⇄MQTT
  bridge: gates-closed-first, RX conservation vs mosquitto, byte-exact
  TX via PCAN, custom topics; needs the Pi broker + PCAN. NOTE it
  PARKS any bridge that owns the single-consumer `can` jack for the
  run and restores everything after.
- `uds_dtc_bench_test.py` (→ `UDS DTC BENCH PASS`) — UDS 0x19/0x14
  DTC vs the ECU-sim box (sim REST at 192.168.8.1 from the PC):
  forced-`uds` scan, group clear, `auto`-prefers-OBD leg. The sim's
  19 02 ignores the status mask and its 0x14 ignores groupOfDTC —
  the bench works around both (documented in TESTBENCH.md row).

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
  known_hosts. The IP is DHCP — re-pin if the router moves it.
- **Opening the DUT serial port REBOOTS it** (DTR/RTS auto-reset) — never
  start a serial capture during a live bench run; pull
  `/api/logs/ring` over HTTP instead (it holds the recent log lines).

## Notes

- `target` verification is currently completion + no-crash; the strict ordered
  marker assertions live in each `pytest_<comp>.py` and run once
  pytest-embedded is adopted (CHECKLIST Phase 0).
- **`all`, `hil`, and several target apps WRITE the shared settings
  partition** (wifi_manager/HIL rewrite the WiFi config; the
  bridge_manager/socket_manager apps persist their loopback test configs —
  main then degrades those components to defaults at the next boot).
  **Take a settings backup first** (`GET /api/settings/backup` → file on
  the Pi), and after the run: reflash the main firmware, restore over the
  DUT's AP or the hotspot (`POST /api/settings/backup`), then re-PUT the
  canonical bench channels/bridges/sockets (the set above) if the backup
  predates them. `/api/bridges` + `/api/sockets` show at a glance what is
  configured-but-down after a partial restore (proven 2026-07-06).
- Everything the scripts do is plain ssh/nmcli/esptool — `components/TESTBENCH.md`
  §3b documents the manual equivalents for debugging.
