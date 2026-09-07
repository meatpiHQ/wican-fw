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
| `.\test.ps1 host [component]` | Host **unit** suites (`components/*/host_test`, 526 tests across 44 components as of 2026-07-26 — the summary prints a per-suite and TOTAL count) on the bench Pi's IDF linux target — syncs sources automatically. Optional component name runs just that suite | `ssh rpi001` reachable |
| `.\test.ps1 target <component>` | Builds + flashes that component's **self-contained on-target app** (`components/<comp>/test_apps`), captures serial, verifies `TEST DONE` with no crash signatures. `ble_manager` is special: waits for `BLE READY`, then drives the DUT from rpi001's BLE controller (`ble_bench.py`) and passes on `BLE BENCH PASS` | DUT on COM7 (+ rpi001 for ble_manager) |
| `.\test.ps1 hil` | **WiFi hardware-in-the-loop** pytest: real STA connect / reconnect / fallback / ban / AP-auto-disable against the Pi's AP (add `-Flash` to rebuild+flash the HIL app first) | DUT + `ssh rpi001` |
| `.\test.ps1 live` | **Live checks vs the composed main firmware**: CLI over WebSocket (`cli_ws_test.py` → `CLI WS PASS`; incl. `system -t` task monitor) + OBD over WebSocket (`ws_live_test.py` → `WS LIVE PASS`; it pauses/restores every autopid group via `POST /api/autopid/group` — autopid co-masters the chip and its traffic fans out to `/ws/obd`) + real-DBC parser cross-check (`dbc_real_test.py` → `DBC REAL PASS`; uploads the fixture DBCs, diffs every signal against the host reference) + the USB-Ethernet / espnetlink benches (auto-**SKIP** when hardware absent) + **network-trust lockdown sweep LAST** (`wifi_gate_live_test.py` → `WIFI GATE PASS`: flips `sta_trusted` false, probes EVERY `/api` route × 4 methods + UI + WS over the STA address expecting 403/405 only, USB admin + MQTT must stay up; reboots the DUT twice, restores). Needs the canonical bench settings (below) | main fw on DUT at `-DutIp` + rpi001 |
| `.\test.ps1 blesec` | **BLE bonding + security end-to-end** (`ble_security_test.py` → `BLE SECURITY PASS`). BLE sits on the internal-RAM cliff with the full stack, so it auto-switches the DUT to **WiFi-off / BLE-on** (~53 KB free), runs three scenarios on the Pi's UB500 dongle — **gate** (a WRONG passkey can't pair, so no characteristic is readable), **pair** (the correct passkey pairs via MITM and the gated Device-Info read works), **persist** (after a DUT reboot the bonded central reconnects with **no** passkey — NVS bond persistence) — then restores `apsta` + BLE-off. Reboots the DUT ~3×; leaves it on WiFi. Runs on the PC (USB link + ssh to the Pi) | main fw on DUT + rpi001 + UB500 |
| `.\test.ps1 perf` | **Performance battery vs the composed main** — every scenario's numbers land in the report: MQTT RTT (512B) + paced 4000B drain (`mqtt_bench.py`, needs mqtt enabled against the Pi broker; the device silently ignores `size > 4000` = `BENCH_MAX_SIZE`), WS echo latency via `/ws/can` + OBD-poll via `/ws/obd` (`ws_bench.py`), BLE A/B RTT (`ble_ab.py`, runs **last** — a BLE connect makes interface_manager suspend WiFi, which also skips its TCP throughput legs by design; clean-radio BLE TX = manual `ble_blast.py`) | main fw on DUT + rpi001 |
| `.\test.ps1 usbeth` | **USB-Ethernet bench** (`usb_eth_bench.py` → `USB ETH TARGET PASS`): adapter status, HTTP + throughput over the usb-eth netif, reboot re-enumeration The wire may land on either Pi shared profile (`eth0` `eth-bench` 10.42.1.x, or - since 2026-08-26 - a USB-Ethernet adapter on the Pi as `eth1`, profile `eth-bench-usb` 10.42.2.1/24); the script accepts any `10.42.` lease by default, an optional 2nd arg pins the prefix. The DUT address (1st arg) takes a comma-separated candidate list — BOTH Pi hotspots broadcast `WICAN_TEST_AP` (wint0 10.42.0.x, wtest0 10.42.1.x) and the DUT may re-join either after the reboot leg (`10.42.0.194,10.42.1.194` for the 68ee8f5a653d unit; the .62 pin belongs to the other unit). Re-run 2026-08-26 (AX88772B on the WiCAN, cdc_ncm adapter on the Pi, espnetlink build v4.51p_beta): PASS 10/10, HTTP sample 392 KB/s, attaches=1 after reboot; iperf2 over the wire TCP TX 6.45 / RX 7.71 Mbit/s, UDP `-b 20` 7.00 Mbit/s 0% loss 1.68 ms jitter (matches the 2026-07-13 baseline in `iperf_manager/README.md`) | main fw on DUT + the adapter on the USB connector + Pi `eth-bench`/`eth-bench-usb` profile |
| `.\test.ps1 espnetlink` | **ESPNetLink LTE-dongle bench** (`espnetlink_bench.py` → `ESPNETLINK TARGET PASS`): the RNDIS data path (enum + IP) AND the CDC-ACM management console (`ver`, `lte -s` signal/operator/PPP, carrier IP). `--prove-lte` (manual) isolates the Pi's WiFi internet and confirms SNTP over the modem. **Since 2026-08-24 the default `espnetlink.mode=wifi_modem` CUTS the dongle's USB data after pairing** — this USB-data bench needs `espnetlink.mode=usb_ncm`/`usb_rndis` (or `auto_pair=false`) + `usb_acm_cli.enabled`. **2026-08-25: three selectable transports** (`wifi_modem`/`usb_ncm`/`usb_rndis` — the pairing pass sets the dongle's `usb_dev_ethernet.class` to match, one dongle reboot). Bench-verified full cycle: wifi_modem steady (cut, GPS from power-on) -> usb_ncm (the AP-path `usb_data` restore un-cuts the boot-cut dongle, `driver cdc_ncm`, uplink espnetlink_usb, GPS + SNTP over the wire) -> usb_rndis (class flip + re-enum as RNDIS, `driver rndis`, GPS + SNTP) -> wifi_modem (key re-read, cut, fix survives); the WiFi-modem path is verified from the console instead: boot log `identified … → credentials ok → usb gone → STA got IP 192.168.80.x → uplink: none -> espnetlink (AP)`, `espnetlink` → `gps: valid=1`, `rtc -s` syncs via the dongle, `espnetlink repair` cuts again with 0 reboots / 0 `E` lines (see `components/espnetlink_link/README.md`) | main fw on DUT + the espnetlink on the USB connector (usb_host_manager + usb_acm_cli enabled) |
| `.\test.ps1 stackaudit` | **Stack + memory audit, both halves** (2026-07-22, born from the silent PSRAM-stack overflow): (1) *static* — `tools/stack_audit.py` reads the compiler's per-function frame sizes (`-fstack-usage` is permanently on; `.su` files land next to every `.obj`) and cross-references them against every `xTaskCreate*` stack size (PSRAM vs internal detected), flagging frames > 40% of a same-component task stack for human triage; (2) *runtime* — `stack_audit_test.py` on the Pi exercises the deep paths (DTC scan job, test-a-PID), then asserts EVERY live task's `stack_hw` >= 512 B (warn < 1024), the ephemeral job tasks' exit-log watermarks (`dtc job stack_hw=` / `std scan stack_hw=` via `/api/logs/ring`), and heap floors on BOTH placements (internal min_free >= 20 KB + largest_block >= 16 KB, PSRAM min_free >= 1 MB). The runtime half also runs inside `live` | static: local build; runtime: main fw on DUT + rpi001 |
| `.\test.ps1 sleep` | **Sleep-mode HIL** (`sleep_bench_test.py` → `SLEEP BENCH PASS`, ~10 min): OWON PSU walks the 12 V input across the sleep/wake thresholds; entry proven by supply current, wake by `/api/restart/history` | PSU on COM2016 + rpi001 |
| `.\test.ps1 sleepmatrix` | **Sleep robustness matrix** (`sleep_matrix_test.py` → `SLEEP MATRIX PASS`, ~90 min): every historical breaker as a scenario — VPN soaks (betty), BLE, logger, ghost SSID, boot-loop guard, periodic/critical — see `tools/testbench/sleep/SLEEP_MATRIX.md` | PSU + rpi001 (+ betty for the VPN legs) |
| `.\test.ps1 conserve` | **Counter-conservation benches** (CAN / USB-NCM / WiFi → `* CONSERVATION PASS`): exactly-N accounting against a known generator, the §7 performance-truth net | PCAN; NCM leg needs the device-role flip |
| `.\test.ps1 dwc2` | **DWC2 kill-vs-ISR race hammer** (`dwc2_hammer_test.py` → `DWC2 HAMMER PASS`, ~10 min): ACM mixed-timeout kill floods + NCM OUT blasts + mid-flight teardown churn vs the 2026-07-17 crash class | espnetlink dongle on the USB host port |
| `python tools\testbench\wifi\wican_fresh_bench.py --wican COMx --psu COMz` | **WiCAN Pro out-of-the-box scenario** (→ `WICAN FRESH PASS`, ~5 min): erase-flash + flash, PSU cold boot with the console captured from power-on; asserts the first-boot defaults (wifi_manager mode=ap, 0 STA networks, derived `WiCAN_<id>` AP, USB host enabled, espnetlink wifi_modem unpaired), then plays the new user: the Pi joins the AP with the default `@meatpi#`, loads the web UI + `/api/settings` + the wifi_manager schema, stages home WiFi (the Pi `wican-bench` hotspot) + apsta through the settings API exactly as the UI does, submits (ONE planned reboot) and expects the STA on the home network WHILE the phone stays on the AP, the AP still up, the API still answering; planned reboots only, 0 E lines. `--no-erase` re-runs on an already-fresh unit. Bench 2026-08-31: PASS (STA on the home network 9–34 s after the config reboot; a dongle on the connector pairs in the background = one extra planned reboot, accounted for) | WiCAN console + PSU + rpi001 (`wtest0` hotspot, `wtest1` free) |
| `python tools\testbench\wifi\espnetlink_fresh_bench.py --wican COMx --dongle COMy --psu COMz` | **ESPNetLink out-of-the-box scenario** (→ `ESPNL FRESH PASS`, ~8 min): erase-flash + flash BOTH devices, PSU cold boot with both consoles captured from power-on, the Pi joins the fresh `WiCAN_<id>` AP as the user's phone and STAYS there; asserts the dongle's first-boot AP-password provisioning, the PAIRING HOLD while the WiCAN AP still has the factory password (machine idle + warning, 2026-09-07), the user's mandatory AP-password change (reboot), then identify → credentials → store (wifi mode ap→apsta) → cut → ONE WiCAN reboot, then the STA joining the dongle AP + uplink=espnetlink WHILE the client sits on the WiCAN AP (field-hit 2026-08-31: the AP-client STA pause blocked the first association forever), the user's view via `GET /api/espnetlink` over the WiCAN AP, the stored key read back (and pushed into the Pi's `espnl-client` profile — the fresh dongle has a new password), planned-reboots-only, 0 E lines on both consoles. `--no-client` = bare from-scratch path; `--no-erase` = re-run on the already-fresh pair. Also plays the deterministic AP-client hazard (phone parked, `espnetlink repair` VBUS-cycles the dongle, the STA must re-join ≤150 s — unfixed wifi_manager: never; fixed: +68 s). Bench 2026-08-31: PASS (zero-touch pairing 18–21 s from power-on, STA on the dongle AP at ~29 s) | both consoles + PSU + rpi001 (`wtest1` free) |
| `python tools\testbench\wifi\espnetlink_mode_bench.py` | **ESPNetLink transport-mode switching** (→ `ESPNL MODE PASS`, ~15 min): the full `espnetlink.mode` transition matrix — wifi_modem → usb_ncm (AP-path `usb_data` restore un-cuts the boot-cut dongle, `boot_cut` clears, NO dongle reboot, driver cdc_ncm) → usb_rndis (class flip = exactly ONE dongle reboot, re-enum RNDIS) → wifi_modem (cut, `boot_cut` re-armed, dongle NOT rebooted — a live GPS fix survives) → usb_rndis DIRECT (restore + class flip in ONE ensure pass, still one dongle reboot) → restore. Every WiCAN reboot must be planned (restart_tracker `unexpected resets` may not grow); all assertions are OBSERVED state over the console + the dongle's `/api/wifi_modem` (HTTP statuses are advisory — fresh-AP associations lose response status lines routinely); the WiCAN's STA address is read live from the console (DHCP lease moves after cold cycles). `wifi:Invalid MMIE` (esp_wifi PMF noise) and the IDF HTTP client's single connect-failure triplet (a poll racing the dongle's planned mid-transition reboot) are whitelisted. Bench 2026-08-25: PASS, 0 unexpected resets, 0 E lines | DUT console COM1175 + rpi001 (`espnl-client`) + paired dongle |
| `python tools\testbench\wifi\espnetlink_roam_bench.py` | **ESPNetLink roaming scenario** (→ `ESPNL ROAM PASS`, ~8 min): the drive-away / drive-home story in `espnetlink.mode=wifi_modem` — away on the dongle (uplink=espnetlink, GPS polls + fix) → a temp Pi hotspot broadcasts the DUT's OWN primary SSID/key, read from `/api/settings/backup` ("drive home") → roam-to-preferred lands within `sta_roam_interval_s`+120 s, uplink=wifi, dongle polls STOP → hotspot down ("drive away") → fallback to the dongle ≤150 s with the GPS fix surviving (the dongle is never rebooted) → home again, zero console E lines. Console-observed only (a client on the DUT's AP pauses its STA reconnects); the temp AP pins `ipv4.addresses 10.42.9.1/24` (NM's default shared subnet collides with the other hotspot and rolls the fresh AP back). Bench 2026-08-24: roam-home 116/66 s, fallback 18 s, fix valid +3 s | DUT console COM1175 + rpi001 (wtest0 borrowed for the run, `wican-bench` restored after) + paired dongle |
| `.\test.ps1 logsinks` | **External log sinks conservation** (`log_sinks_bench_test.py` → `LOG SINKS BENCH PASS`, runs ON the Pi; also a `live` stage): gates-closed defaults (port closed, counters zero, ws_log migration present), then per-sink conservation vs the `logsinks emit` known generator — TCP tail exactly-N, UDP collector exactly-N, `/ws/log` stream, SD file content + rotation + retention, per-sink `in == out + dropped` identity, restore. Reboots the DUT 3× | main fw on DUT + rpi001 + SD card + ws_cli enabled |
| `.\test.ps1 all` | host + every target app + hil (live/perf are separate — `all` leaves a test app, not the main firmware, on the DUT) | both |

Options: `-Port COM10` (DUT serial; see the physical-setup matrix), `-BenchHost rpi001`,
`-DutIp 10.42.0.62` (DUT on the bench hotspot), `-DeviceId 14c19f44e349`
(bench unit), `-NoReport`, `-SkipBenchCheck`.

## The bench preflight (runs first, every kind)

Every run (except `list`) starts with a ~10 s **bench preflight** that
verifies the PHYSICAL bench matches what the selected kind needs, fails fast
with a specific message when it doesn't, and records the findings in the
report's conditions. `-SkipBenchCheck` bypasses it.

What it detects: available COM ports (auto-falls-back `-Port` → COM10 →
CH342), whether a CH342 is enumerated (= the DUT's USB connector is cabled to
the PC), Pi reachability, DUT online + firmware version, USB-Ethernet adapter
presence (`/api/usb`), mosquitto on :1883.

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

The **UART0 external USB-serial adapter (COM10 console/flash, COM11 spare)**
works in EVERY wiring — target/hil/all always run through it. Legs whose
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
OWON P4305 bench PSU on COM2016 walks the DUT's 12 V input across the
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
  test/flash job). One process owns COM7 at a time; close it and re-run.
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
