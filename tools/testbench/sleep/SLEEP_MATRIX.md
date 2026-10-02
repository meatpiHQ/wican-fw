# Sleep robustness matrix

> What can break sleep, mapped: every subsystem that can be mid-flight
> when the voltage drops, and every historical failure mode (meatpi
> 2026-07-21 list). Runner: `sleep_matrix_test.py` (PSU on COM2016 +
> HTTP via rpi001 + local COM6/COM7 — see TESTBENCH.md §1). Each
> scenario: configure → hold the condition live → 12.5 V →
> **entry** (current-delta within countdown+90 s) → **soak** asleep
> (no spurious wake, HTTP stays dead) → 14.0 V → **wake** (current +
> reachable + `power_wake`) → **forensics** (zero unexpected resets,
> exactly one new restart record, `/api/faults` empty) → restore.
>
> History informing this matrix: VPN + sleep = DELAYED panic (long
> after entry — hence the long soaks); OBD chip waking back up /
> refusing to stay asleep (legacy sleep-pin approach is the reference);
> sleep during active ECU polling broke something and caused spurious
> wake; STA connect-loop to an absent SSID at entry; BLE with a
> connected central torn down improperly.

| # | key | Condition live at entry | Soak | Status |
|---|-----|--------------------------|------|--------|
| 0 | `baseline` | idle APSTA on the bench AP (the `bench_ap` P4 since 2026-10-02) | 90 s | runner |
| 1 | `mqtt` | mqtt_manager connected to the Pi broker | 90 s | runner |
| 2 | `net_traffic` | HTTP hammer (≈5 req/s) + a held TCP:35000 socket through entry | 90 s | runner |
| 3 | `tcp_poll` | continuous ELM `010C` polling over TCP:35000 (the "sleeps while polling the ECU" bug) | 90 s | runner |
| 4 | `logger_can` | data_logger sqlite + `can_log` on, CAN traffic from polling = live DB/SD writes at entry | 90 s | runner |
| 5 | `elm_monitor` | `ATMA` streaming on the usb_obd UART (COM6) — chip mid-monitor when the sleep pin drops | 90 s | runner |
| 6 | `ble_client` | ble_manager on + a central (Pi UB500) holding a GATT connection | 90 s | runner (SKIP if bleak absent on Pi) |
| 7 | `wg_soak` | WireGuard connected (LOCAL wg-bench on the Pi) | **600 s** | runner — PASS 2026-07-21 |
| 8 | `ts_soak` | Tailscale via LOCAL headscale on the Pi | 600 s | superseded by `ts_betty_soak` on this bench (local binary re-provisioned 2026-07-21 if ever needed: `/tmp/headscale` v0.23.0) |
| 8a | `wg_betty_soak` | WireGuard against the **PUBLIC betty VPS** endpoint (real internet/NAT path — the closest reproduction of the historical delayed-panic conditions; `wg_public_soak.sh up split` → cycle → `down`) | **600 s** | runner |
| 8b | `ts_betty_soak` | Tailscale via the **PUBLIC headscale on betty** (`ts_public_soak.sh up` runs the register/tunnel legs, then cycle → `down`) | **600 s** | runner |
| 9 | `kitchen_sink` | mqtt + logger + tcp_poll simultaneously | 300 s | runner |
| 10 | `wifi_ghost` | the configured network is ABSENT: connect-retry loop live at entry. Since 2026-10-02 the `bench_ap` P4 plays the vanished network (`ap off`; the DUT's settings are untouched), the AP returns while the DUT sleeps and the wake reboot must rejoin on its own; current-only cycle, forensics deferred to the rejoin (exactly one reboot, zero unexpected). SKIPs without the P4: the old form changed the DUT's SSID and restored it by joining the DUT's own AP from the Pi's wtest0 with a password that had rotated, and re-armed the hotspot's autoconnect in its finally. Runs LAST (the one scenario that takes the DUT off the bench net) | 90 s | runner |
| 11 | `autopid_poll` | REAL autopid polling (configured profile + ECU sim): wire-proof of polling (sim rxlog), **`pause_follow_sleep` request-silence below sleep_mv (0 req/8 s) + resume**, then sleep entry mid-poll | 90 s | runner — PASS 2026-07-21 (14 req/6 s live, 0 paused, 12 resumed, clean cycle) |
| 12 | `script_engine` | a Berry script mid-run (`POST /api/scripts/run`, a sleep loop up to the runtime cap) | 90 s | runner (2026-10-01) |
| 12b | `forced_sleep` | `sleep test 30` over the **held console** (since 2026-10-02; the ws_cli channel ships parked, `/ws/cli` is a 404, which was the `CLI refused` of the 2026-10-01 runs): full entry + ~30 s of naps + wake-by-test-timer, `power_wake` record. SKIPs without a console | ~35 s | runner — PASS 2026-07-21 (ws_cli) |
| 13 | `periodic_critical` | periodic check-in wake (5-min interval, the schema's minimum since 2026-09-06, fires while asleep at 12.5 V) **+ the < 11.90 V CRITICAL floor** (at 11.75 V: an interval and a half of proven silence — nothing but voltage recovery may touch a critical battery) | 390 s + 450 s | runner — PASS 2026-07-21 (1-min interval); 2026-10-01 re-timed for the 5-min minimum |
| 14 | `bootloop_guard` | **the LAST-LINE battery defense** (legacy parity, hardened 2026-07-21): ≥3 unexpected resets below 12.10 V parks the device asleep — evaluated every loop AND with sleep **disabled** in settings (the guard task now always runs); panics injected via `restart_tracker --panic` over the console (counted as expected by the forensics); wake = normal voltage recovery; counter clears only on real power loss (PSRAM-retained); the cleanup (sleep re-enabled + the counter power cycle) runs in a `finally` since 2026-10-02 (a failed run had left sleep DISABLED for the four scenarios after it) | 60 s | runner |
| 15 | `usb_dongle` | USB host active with a device on the connector (the ESPNetLink dongle) at entry; the host must come back after the wake | 90 s | runner (2026-10-01; this path PANICKED before the CherryUSB deinit fix, see cherryusb/PROVENANCE.md) |
| 16 | `sd_absent` | external_storage unmounted/absent card at entry | 90 s | TODO |
| 17 | `ota_staged` | OTA image uploaded but device sleeps before the reboot | — | TODO (define expected: sleep wins? reboot wins?) |
| 18 | `button_config_mode` | runtime config-mode AP (button hold) active when voltage drops | 90 s | TODO (needs the physical button or a GPIO rig) |
| 19 | `j2534_on` | J2534 server listening on TCP 6809 at entry | 90 s | runner (2026-10-01) |
| 20 | `ble_idle_on` | BLE platform on, advertising, no central (the connected case is #6) | 90 s | runner (2026-10-01) |
| 21 | `destinations` | data_destinations publishing the configured rows (MQTT to the Pi) at entry | 90 s | runner (2026-10-01, SKIP without a configured row) |
| 22 | `critical_floor` | sleep DISABLED in settings, 11.75 V: the critical floor (under 11.90 V for 120 s) must sleep the device anyway, keep it asleep, and 14 V must wake it (`power_wake`) | ~3 min | runner (2026-10-01, Ali's floor) |

Chip-stays-asleep coverage: every scenario's forensics fail on an
`internal_recovery` record (= the OBD chip refused to stay asleep and
the nap loop gave up) and the soak fails on any sustained current rise
(= spurious wake or chip re-awake churn).

ECU-sim note: the sim shares the PSU feed (~40 mA standing) — all
current assertions are deltas against each scenario's own awake
baseline.

Rig note (2026-10-01, revised 2026-10-02): the runner checks the DUT is reachable before every scenario and recovers first (console reset, then a cycle of the bench AP), so one lost scenario no longer cascades into the rest; the 2026-10-01 runs lost 13 scenarios that way after four passes. **The runner no longer touches the Pi's own radios**: the bench AP is the `bench_ap` P4 instrument (`tools/testbench/instruments/bench_ap/`, `wican-bench-usb` active on the Pi), recover() and wifi_ghost drive it through `bench_ap_cli.py`, and without it recover() only resets the DUT and wifi_ghost SKIPs. The P4's console log (`/tmp/bench_ap.console.log`) gives the association forensics the matrix lacked. The bench CLI (`sleep test`, `restart_tracker --panic`) goes over the DUT console the runner already holds; the ws_cli channel ships parked.

Betty-leg note: set `WG_BENCH_DNS=<server name>` in the environment to
make the DUT's WG endpoint / TS control URL a DNS NAME instead of an
IP — that exercises the firmware's DNS-resolution path during VPN
bring-up and teardown (relevant to the still-open lwip-DNS-UAF, see
[[wican-wireguard-fixes]] context). Without it the legs run IP-only.

**Run log** — 2026-10-02 11:24 (the four scenarios that had run with sleep disabled behind the old bootloop_guard cleanup): usb_dongle, j2534_on, ble_idle_on, destinations PASS in 14 min (entry 66 to 69 s, 47 to 48 mA asleep, clean wakes and forensics, the USB host back after the usb_dongle wake). Build tally after runs 3 to 5: 18 of 22 PASS; left elm_monitor (COM6), the two betty legs, ts_soak (SKIP by design).

**Run log** — 2026-10-02 11:15 (the three harness-blocked scenarios, Pi radios out): forced_sleep, bootloop_guard, wifi_ghost PASS in 8 min; forced_sleep timed wake after 38 s; bootloop_guard 3 injected panics, guard sleep at 47 mA, counter cleared by the finally's power cycle; wifi_ghost asleep 70 s after the drop with the P4 AP off, rejoined on the wake reboot after `ap on`. With run 3 that is 14 of the 22 scenarios PASS on this build; left: elm_monitor (COM6), the betty legs, ts_soak (SKIP by design), and the rerun of the four scenarios that ran with sleep disabled after the old bootloop_guard (usb_dongle, j2534_on, ble_idle_on, destinations).

**Run log** — 2026-10-02 09:26 (over the `bench_ap` P4 link, console forensics, microlink join fix): 11 PASS incl. wg_soak 600 s, autopid_poll, kitchen_sink, periodic_critical, critical_floor; no panic, no reachability cascade; left: COM6, betty unreachable, the ws_cli path (forced_sleep, bootloop_guard), the sleep-disabled cascade after bootloop_guard, the wifi_ghost restore's wrong AP name.

**Run log** — 2026-10-01 23:08 (second run, Pi rebooted): baseline, net_traffic, tcp_poll, periodic_critical PASS; logger_can, ble_client, wg_soak (600 s) slept and woke cleanly, HTTP steps after the wake lost to the hotspot path; bootloop_guard could not inject panics over ws_cli and left sleep disabled, cascading into usb_dongle/j2534_on/ble_idle_on/destinations; critical_floor failed in that degraded state but passed a console-attached reproduction with BLE on; wifi_ghost's restore looked for the wrong AP SSID. The runner needs a console capture (forensics) before the next run.

**Run log** — 2026-10-01 (final build of the Quick Setup / sleep_manager v4 day, after the CherryUSB deinit fix): baseline, mqtt, net_traffic, tcp_poll **PASS**; the remaining scenarios were lost to the rig (station off the hotspot after a wake reboot: three failed attempts deprioritise the entry and the dongle AP is the fallback it lands on) and must be re-run with the dongle off the connector. Before the fix the sleep bench itself could not enter sleep (panic at entry with the USB host active).

**Run log** — 2026-07-21 (fw: can_core rendezvous + esp_timer policy
clock + battery read_now + LED-off + obd hard-reset-in-resleep):
baseline, mqtt, net_traffic, tcp_poll, logger_can, elm_monitor,
ble_client, wg_soak(600 s), kitchen_sink(300 s), wifi_ghost — **all
PASS**; ts_soak superseded by the betty leg. Same day:
**`ts_betty_soak` PASS + `wg_betty_soak` PASS** — both VPN types over
the REAL internet path with 10-min asleep soaks, zero panics/resets
(the historical delayed-panic did not reproduce; IP-endpoint runs —
the `WG_BENCH_DNS` DNS-path variant still owed). Betty-leg traps:
`up` returns while the DUT is mid-submit-reboot (the runner waits +
polls /api/vpn now), and the DUT twin-roams mid-scenario (one run lost
to the soak script curling a stale lease IP — rerun cleanly passed).
**FULL-SUITE SWEEP 2026-07-21 eve (final fw incl. pause_follow_sleep + UI volts): 13/13 PASS** — baseline, mqtt, net_traffic, tcp_poll, logger_can, elm_monitor, ble_client, wg_soak(600 s), wg_betty_soak(600 s), ts_betty_soak(600 s), kitchen_sink(300 s; one transient stale-lease start in the batch run, solo rerun clean), periodic_critical, bootloop_guard, wifi_ghost incl. AP restore; ts_soak SKIP by design. All forensics clean. Late addition: `wg_betty_soak` re-run with
`WG_BENCH_DNS` (DNS-name endpoint, fw-side resolution) — PASS;
`forced_sleep` + `autopid_poll` (incl. the pause_follow_sleep
wire-silence proof) added and PASS same night.

**FULL-SUITE SWEEP 2026-07-22 (fw: can_core on esp_driver_twai node
API + ELM monitor-queue fix + finite fail_retry_cnt): 16/16 PASS**
— 15 in the 92-min batch run (both betty soaks 600 s clean), then
`wifi_ghost` solo after a BENCH fix: `_wifi_ghost_restore`'s bare
`nmcli connection down wican-bench` only lasts seconds before
NetworkManager AUTOCONNECTS the hotspot back onto wlan1 — an AP-mode
wlan1 scans empty, so every join retry failed "No network with SSID
found" and the DUT stranded on the ghost SSID (rig.recover() can't
save it: COM7 TX is dead). The restore now holds
`connection.autoconnect no` for the whole join window and re-enables
it on both the fail path and the restore finally. Manual rescue (if it
ever strands again): same nmcli sequence by hand from rpi001, read the
live PSK (`nmcli -s -g 802-11-wireless-security.psk connection show
wican-bench`), PUT wifi_manager via http://192.168.0.10 + submit.

Same day: **`bootloop_guard` PASS** — 3 ws_cli-injected panics at
12.05 V with sleep DISABLED in settings → the guard parked the device
(98→61 mA, HTTP dead, 60 s stable), voltage wake clean
(`power_wake`, count preserved at 3), and a real power cycle cleared
the PSRAM counter to 0. Firmware hardened same day: guard evaluated
every loop + the sleep task now always runs (settings-independent).
