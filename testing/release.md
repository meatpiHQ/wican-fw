# THE RELEASE TEST: the fast pre-release gate on the exact release image

Command: `.\test.ps1 release` (= `python tools/testbench/release/release_test.py --dut localhost:18081 --dut-ip 10.42.1.194 --pi rpi001 --console COM254`; by hand add `[--image build/x.bin] [--no-flash] [--only r5,r8] [--tunnel] [--cap-s 900] [--logdir DIR]`)

Needs: PC + rpi001 (the broker, the receiver, python3 + websocket), the DUT's console port free (the bench flashes and tails it), the ECU sim v2 supply, ECU sim v2 answering on the bus; no vehicle, no dongle; 2026-10-28 on the issue-1023 branch build (phase 2, the lean cards, the self-test restore fix): PASS 59 (heap floor 39740 B, 137 mA) on the second run; the first run's r5 broke the ws_obd pairing (every answer one command late) because `ws_live_test.py` paused the autopid groups from `/api/autopid/config`, which lists only the groups written in the table, while the bench DUT's 16 standard rows sit in the implicit default group: the test now pauses the runtime groups from `/api/autopid` and waits for the poller to go idle instead of sleeping 1.5 s; 6.0.0-beta.6 (tag v6.0.0p-beta.6 at d8388d2, clean build, 2026-10-28 22:31): PASS 59 on the exact image, sha256 7fff2882958c683a94f3798874ad26ca6b5f2ca05e7c8f443f2a604b1314942a, ELF 36e58abfd0bc9940 (archived under internal/elf), 305 s, heap floor 39740 B, 143 mA; record test-reports/release/7fff2882...json

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**THE RELEASE TEST: the fast pre-release gate on the exact release image** (prints `RELEASE TEST PASS`, 59 checks, about 6 min, cap 15; 2026-10-21, `internal/TASK_release_test.md`, built after the beta.1 field crash because the gate that catches it existed and nobody ran it on the bytes that shipped). Three rules: the bytes under test are the bytes published (the bench flashes `build/`'s flash set over the console port, checks the running version and ELF id against the image's own descriptor, and writes `test-reports/release/<image sha256>.json`, which `release.py check` warns about and `publish` REFUSES without: fresh, `PASS`, complete, flashed; `--no-release-test` publishes marked "Not bench-tested"); a hard cap (every step has a budget, over it = FAIL); coverage by task (`MATRIX` maps every PSRAM-stack task to the step that drives it, `stack_audit.py --tasks-json` feeds the preflight, an unmapped task prints a WARN). Steps: r0 preflight (rig, image, the canonical settings, the matrix), r1 serial flash + boot (fw_version and `history.elf_sha` = the descriptor, boot 1), r2 boot health (0 unexpected resets, brake normal, 0 faults, registry headroom >= 2, boot flash writes/erases within budget, largest block >= 16 KB), r3 UI and API, r4 a settings round trip (autopid + the script engine on, one `cli` flip; submit, the `config_apply` record, read back), r5 OBD (16 autopid values from the simulator, then `ws_live_test.py` on the Pi with the groups paused first), r6 CAN frames flow (waits for the chip's post-session grace, then >= 20 frames in 10 s and no error delta: 300 measured), r7 MQTT (`mosquitto_sub` on the Pi: the retained `status` online and the live `autopid` / `dd` messages), r8 `ha_webhook_sta_ip_bench.py` unchanged (the PSRAM-stack canary; the three updater keys), r9 the updater's check (on the air-gapped bench it concludes `fetch_failed` in 45 s; the task survives), r10 an OTA upload of the same image on `/api/ota/upload` (the `ota_apply` record, the other slot runs it, the ELF id unchanged, 0 unexpected resets), r11 the writers (a backup copy, the log ring, a Berry script), r12 BLE (SKIP while the bench set keeps BLE off: the record names the skip, the publisher prints it), r13 the closing audit (`stack_audit_test.py` on the Pi with its two reboots counted, the restore submit, every reboot accounted, 0 faults, 0 E lines of our tags on the console and in the ring, the supply current in the awake band). `log_errors` is a metric, not an assertion: the air-gapped bench makes the updater's scheduled check log IDF's connect triplet, and the reboots make `mqtt_client` log.

**Runs 2026-10-21 on the beta.1 fix build:** 1 and 2 were bench assumptions (the settings listing shape, the chip's `STOPPED` on the first app command when autopid yields: `ws_live_test.py` sends a warm-up CR now, the CAN count inside the chip's grace, the script engine off in the bench set, IDF's `mqtt_client` lines); **run 3 found two firmware faults**: the task watchdog on IDLE0 4.5 s into the OTA upload (`esp_ota_begin` erased the whole 3.8 MB up front with the cache off; `ota_manager` erases as it writes now, `OTA_WITH_SEQUENTIAL_WRITES`) and a WebSocket client reaped in the millisecond it connected (the pre-handshake hook registers the fd before the 101; a fan-out send took the HTTP state for a ghost; `websocket_manager` has a 3 s handshake grace now); **run 4 on the fixed build: `RELEASE TEST PASS`, 59 checks, 326 s**, record `f525fadc...`, heap floor 48,203 B, `ha_webhook` headroom 6680 B, OTA upload 35 s (sector-by-sector erase), 0 watchdog lines. Logs: `test-reports/logs/release_<stamp>/` (console, ws_live, mqtt, ha_sta_ip, stack_audit, flash). A parked or dead DUT: the PSU cycle (`owon_psu.py cycle`); the stage opens its own tunnel on 18081

**2026-10-31 23:47 (the HTTP server task on a PSRAM stack and the flash_jobs worker, `internal/TASK_httpd_psram.md`, the dirty trees, not a tagged image): `RELEASE TEST PASS`, 60 passed, 0 failed, 325 s**, image sha256 0a483e21325144c2263209d23917d5f59aa412e7721e9ca4b885b76a805e89dd; 0 error lines at boot, after the OTA upload and at the close; the OTA upload of the same image through the direct session on the server's PSRAM stack; the writers leg (the backup copy, the log ring, a script); stack headroom fw_update 11,528 / ha_webhook 6,568 / autopid 9,480 B; internal floor 35,640 B (the BLE leg included); supply 132 to 136 mA with the dongle. Report `2026-10-31_2247_release.md`. Not a release record: the trees were uncommitted.

**2026-11-01 00:01 on the follow-ups image (`internal/TASK_httpd_psram_followups.md`: the
Tailscale stacks on PSRAM, the worker at 4 KB, the one-shot writers retired, the subnet
routes; not a tagged image, no release record): `RELEASE TEST PASS`, 59 passed, 0 failed,
r12 BLE skipped (off in the canonical settings), 306 s; sha256 `b8a021d7...`, ELF
`ee3893c4815097f1`; internal heap floor 49,252 B (39,740 on beta.6's tagged image), supply
133 to 146 mA; r10 the OTA upload on the server's PSRAM stack, r11 a script by `src` (the
loader by name is `script_file_bench.py`). Report `test-reports/2026-11-01_0001_release.md`.

**2026-11-01 02:02 on the lever 3 image (`internal/TASK_ble_host_psram.md`: the NimBLE host
task on a 6 KB PSRAM stack, the bond store on the flash worker; not a tagged image, no
release record; the rig without the dongle, the DUT's USB-C on the PC in the device role):
`RELEASE TEST PASS`, 59 passed, 0 failed, r12 BLE skipped (off in the canonical settings),
299 s; sha256 `aec28e7e...`, ELF `096117f1b0e696c4`; internal heap floor 51,032 B, supply 89
to 92 mA (the PC cable, no dongle: the band TESTING.md names for that wiring). The r0
coverage matrix lists `nimble_host` among the PSRAM-stack tasks with no release-test step
(r12 is the step and it is skipped on the canonical set; `blesec` covers it). Report
`test-reports/2026-11-01_0202_release.md`.

**2026-11-02 16:03 (the WireGuard teardown race fix and the 4.x parity of Bluetooth,
`internal/TASK_wg_teardown_race.md` + `internal/TASK_ble_address_pairing.md`; image
744a3f9c795df60d, `v6.0.0p-beta.6-12-ga5f15f8-dirty`): RELEASE TEST PASS** (342.3 s, every
step r0 to r13 green, the OTA upload of the same image, the writers, BLE, internal heap
floor 39,740 B). Report `test-reports/2026-11-02_1603_release.md`, the record under
`test-reports/release/`. Not the release image (nothing committed): the gate run of the
day's changes. The bridge table on the bench DUT carries the migrated `br_ble_obd` since
that image.

**2026-11-02 18:15 (the evening's image: the Bluetooth page warning, the one-second pairing
request, the release notes fetched by the browser; image 5c527b035274d3b5): r0 to r12 PASS,
r13 FAIL on `supply_current_awake_band` alone** (89 mA against the 90 mA floor; the
preflight had read the same device at 90 mA as the median of 89 to 92): a station-only
device without the dongle idles right at that floor on this bench, a parked or sleeping one
at 47 to 54 mA, so the floor is 75 mA since this run. The re-run is the next record.

**2026-11-02 18:25 (the same image 5c527b035274d3b5, the awake-current floor at 75 mA):
RELEASE TEST PASS** (r0 to r13 green, the OTA upload of the same image, the writers, BLE,
internal heap floor 55,080 B, supply 89 mA at the close). Report
`test-reports/2026-11-02_1724_release.md`. The final image of the day: the WireGuard and
Tailscale lwIP threading fix, Bluetooth as 4.x (the public address, the one-second pairing
request, the CCCD gate, the factory `br_ble_obd`, the page's warning), the release notes
fetched by the browser.

**2026-11-02 22:49 (THE RELEASE IMAGE of 6.0.0-beta.7: tag `v6.0.0p-beta.7` at 11dc7dd; `build.ps1 -Clean`, fw_build `v6.0.0p-beta.7`, image sha256
`5c5a4224db46d402...`, ELF `7fb29fe98c97a70e`): RELEASE TEST PASS** (59 passed, 0 failed,
r12 BLE skipped on the canonical set, 320 s, supply 91 mA at the close, the OTA upload of
the same image, the writers). Report `test-reports/2026-11-02_2249_release.md`, the record
`test-reports/release/5c5a4224...json` is the one `release.py publish` read. Published to
the beta channel the same hour (manifest cff01f2, the pre-release and the mirror with
their files). The field check from the bench follows in `internal/RELEASE_PROCESS.md`.

**2026-11-08 18:15 to 18:26 (THE RELEASE IMAGE of 6.0.0-beta.8: tag `v6.0.0p-beta.8` at c73c16b; `build.ps1 -Clean`, ESP-IDF 6.1, image sha256 93257575db008782fe5bb562f65be06ef1b3ceedcedc65b064b83442577c26ac, 4,074,816 B, ELF 493e4b0ef3d8c8f4 archived under internal/elf; the FRESH bench unit 44bd8d73ca79 for the first time):** three runs on the same bytes. 18:15 `RELEASE TEST FAIL` in r0: `psu_answers` (the OWON's first-query false negative, it answered 92 mA by hand right after) and `canonical_mqtt_enabled` / `canonical_can_enabled` (the fresh unit was still on its factory settings). 18:18, with MQTT to the Pi's broker (`mqtt://10.42.1.1:1883`, anonymous) and can_manager on at 500 kbit/s: `RELEASE TEST FAIL` on r5 (`autopid_polls_the_simulator`: 0 params with values) and r6 (`can_frames_flow`: 0 frames in 10 s, over budget) only, 57 of 60 green: the unit's 16 standard AutoPID rows were stored OFF (the std-choice rule), so the test's AutoPID enable polled nothing and the bus stayed silent; the simulator answered a hand 010C with 800 rpm. 18:26, with the 16 rows ticked in `/api/autopid/config` (live): **`RELEASE TEST PASS`, 59 checks, r12 skipped (BLE off in the bench set), 331 s**, r1 flash and boot 79.6 s, r5 7.0 s, r6 20.5 s, OTA upload 52.1 s, heap floor 57,744 B, supply 92 to 93 mA, report `test-reports/2026-11-08_1826_release.md`, record `test-reports/release/93257575...json`. Lesson: a new bench unit needs the canonical set (testing/preflight.md) applied by hand before its first release run; the unit keeps MQTT, CAN and the ticked rows now.


**2026-11-09 17:34 (regression for the Tailscale route list and the WireGuard driver's key
ageing, the uncommitted trees, not a tagged image and no release record): `RELEASE TEST PASS`,
59 passed, 0 failed, r12 BLE skipped (off in the canonical settings), 329 s**; image sha256
`13409483...`, ELF `8da296f7c66bb2eb`; 0 error lines at boot, after the OTA upload and at the
close, 0 flash writes and erases at boot; stack headroom fw_update 11,536 / ha_webhook 6,328 /
autopid 9,288 B; internal heap floor 58,160 B; supply 49 mA before and after (the PC cable).
Report `test-reports/2026-11-09_1734_release.md`.

**2026-11-10 13:02 (regression for the fail-closed `ATM0` of the OBD chip's request path, `testing/attpstore.md`; the dirty trees, not a tagged image, no release record meant): `RELEASE TEST PASS`, 59 passed, 0 failed, r12 BLE skipped (off in the canonical settings), 329 s**; image sha256 `a92def9f68711899fe233ebb166981a13689cf621a7107abbdc49d122017a4a2`, ELF `1324e791ac201732`, fw_build `v6.0.0p-beta.9-1-g108a42d-dirty`; r5 16 AutoPID values in 0.1 s and the ELM leg over `/ws/obd`; 0 error lines at boot, after the OTA upload and at the close; internal heap floor 62,032 B, supply 49 mA before and after. Report `test-reports/2026-11-10_1302_release.md`.

**2026-11-10 14:06 (regression for the OBD chip's protocol save read-back and the bring-up's stored-protocol read, `testing/protocol_save_bench.md`; the dirty trees, not a tagged image, no release record meant): `RELEASE TEST PASS`, 59 passed, 0 failed, r12 BLE skipped, 329 s**; image sha256 `a83a465c1a85483d52bc3cddb29f76049d7cb5bbed57e1caa02b80ba59cc826b`, ELF `2fceae08c4f6c01c`; 0 error lines at boot, after the OTA upload and at the close; internal heap floor 61,668 B, supply 51 to 52 mA. Report `test-reports/2026-11-10_1406_release.md`. The preflight's simulator line reads `unexpected 19 since its power-on` with no warning (the count is the simulator bench's deliberate crashes; `testing/preflight.md`).

**2026-11-10 17:05 and 17:33 (regression for the two autopid changes of `testing/profile_protocol_bench.md`: a profile's protocol is never saved in the chip, and the firmware's own requests undo what the rows of the tables left in the chip; the dirty trees, not a tagged image, no release record meant): `RELEASE TEST PASS` twice, 59 passed, 0 failed, r12 BLE skipped.** 17:05, the save's rule alone: 335 s, image sha256 `ec5966e3a01e46aaa0272409d28008cb15e06b9b2f0951ae92c64d008b57333d`, ELF `142b4e80f10827c1`, internal heap floor 57,712 B. 17:33, both changes: 331 s, image sha256 `00ed81be492c430bb8d558e9980ccb78f2079354efeda00f5e84293e306e39be`, ELF `4f17c40d1da6e076`, 0 error lines at boot, after the OTA upload and at the close, internal heap floor 58,160 B, autopid stack headroom 9,480 B, supply 49 to 50 mA. Reports `test-reports/2026-11-10_1705_release.md` and `test-reports/2026-11-10_1733_release.md`. A run at 17:04 stopped in 8 s with "no tunnel to the DUT through rpi001 (port 18081 busy?)": a bench's own tunnel was still open on that port.

**2026-11-10 22:17 (regression for the three autopid changes of that evening, `testing/profile_protocol_bench.md`: first contact behind a row that pins its own protocol, the probe's 29-bit reply hint, new tables send their own inits; and the Quick Setup's choice of the profile's requests; the dirty trees, not a tagged image, no release record meant): `RELEASE TEST PASS`, 59 passed, 0 failed, r12 BLE skipped, 333 s.** Image sha256 `36ce9508814fa901aeec2f76a1b3b687d47a1e8e76c6a610f8a033c5386cebe8`, ELF `cd9a9fd6f5b011dd`, 0 error lines at boot, after the OTA upload and at the close, internal heap floor 57,740 B, autopid stack headroom 9,368 B, supply 49 to 51 mA. Report `test-reports/2026-11-10_2217_release.md`.

**2026-11-10 23:10 (regression for the detection's profile probe asking the bus guard, `testing/autobaud.md` leg `c5`; the dirty trees, not a tagged image, no release record meant): `RELEASE TEST PASS`, 59 passed, 0 failed, r12 BLE skipped, 329 s.** Image sha256 `bcf98bfa3bfb3fd94317f4249d501413a07eb67187ebdff1c6a057bb1e41512a`, ELF `2f66f39585aaa5b6`, 0 error lines at boot, after the OTA upload and at the close, internal heap floor 61,680 B, autopid stack headroom 9,464 B, supply 49 to 51 mA. Report `test-reports/2026-11-10_2310_release.md`.
