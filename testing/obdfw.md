# OBD chip firmware update

Command: `.\test.ps1 obdfw [-SkipSleep]` = `python3 tools/testbench/obd/obd_fw_bench.py <dut_ip> [--skip-sleep] [--interrupt-at N]` (ON rpi001)

Needs: main fw on DUT + rpi001 + the ws_cli bridge

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**OBD chip firmware update** (issue #1023, 2026-10-28; prints `OBD FW PASS`, ~6 min, 2 planned restarts + 1 sleep/wake): `/api/obd_chip` carries `fw.builtin` (V2.3.24) and `fw.current` (V2.3.22, V2.3.24: the v4.52p list; v6 used to downgrade a .24 chip to .22), the `obd_chip` console command prints the chip's VTVERS; `POST /api/obd_chip/update` without `force` on a current chip ends ESP_OK with 0 records; with `force` every record goes out (3557 in 13 s), a second POST meanwhile is 409, the chip comes back through a fresh bring-up at 2 Mbaud on the packaged version; `obd_chip update --force` cut by `POST /api/restart` at 1200 records leaves the chip in download mode (READY high, answers `?` at 115200 only) and the next boot's auto-update recovers it (every record, 64 s at 115200, then 2 Mbaud again); `sleep test 25` typed mid-stream: the device stays up until the job is done (the sleep entry waits), sleeps, wakes by its timer, the chip is current. Needs the ws_cli bridge (`br_cli`, canonical bench) and `obd_chip.auto_update` on. Bench 2026-10-28: PASS (25 checks, 178 s, and again on the final build, 187 s; the recovery after the cut stream took 64 s at 115200, the forced reflash 19 s with the fresh bring-up)
