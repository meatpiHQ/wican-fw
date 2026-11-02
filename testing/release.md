# THE RELEASE TEST: the fast pre-release gate on the exact release image

Command: `.\test.ps1 release` (= `python tools/testbench/release/release_test.py --dut localhost:18081 --dut-ip 10.42.1.194 --pi rpi001 --console COM254`; by hand add `[--image build/x.bin] [--no-flash] [--only r5,r8] [--tunnel] [--cap-s 900] [--logdir DIR]`)

Needs: PC + rpi001 (the broker, the receiver, python3 + websocket), the DUT's console port free (the bench flashes and tails it), the PSU, the ECU simulator answering on the bus; no vehicle, no dongle; 2026-10-28 on the issue-1023 branch build (phase 2, the lean cards, the self-test restore fix): PASS 59 (heap floor 39740 B, 137 mA) on the second run; the first run's r5 broke the ws_obd pairing (every answer one command late) because `ws_live_test.py` paused the autopid groups from `/api/autopid/config`, which lists only the groups written in the table, while the bench DUT's 16 standard rows sit in the implicit default group: the test now pauses the runtime groups from `/api/autopid` and waits for the poller to go idle instead of sleeping 1.5 s; 6.0.0-beta.6 (tag v6.0.0p-beta.6 at d8388d2, clean build, 2026-10-28 22:31): PASS 59 on the exact image, sha256 7fff2882958c683a94f3798874ad26ca6b5f2ca05e7c8f443f2a604b1314942a, ELF 36e58abfd0bc9940 (archived under internal/elf), 305 s, heap floor 39740 B, 143 mA; record test-reports/release/7fff2882...json

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
