# AutoPID vehicle-profile import

Command: `python tools\testbench\obd\autopid_profile_bench.py <base-url> [--only SUBSTR] [--wide N] [--skip-wide]`

Needs: main fw on DUT (tunnel or Pi-side address) + ECU simulator on the bus for the wide leg; 2026-10-28: the wide leg needs AutoPID polling (autopid.enabled on): with it off the 24 parameters stay null (0/24; the bench's own hint about a foreign chip state was wrong), 24/24 with it on, w00 = 6 = the length byte in the 2026-10-24 frame of reference; 87/87 profiles accepted; PASS that evening with AutoPID on

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**AutoPID vehicle-profile import** (→ `AUTOPID PROFILE PASS`, ~2 min, no vehicle): PUTs EVERY published profile (`vehicle_profiles.json` on GitHub main, or `--profiles FILE`) through the web UI's import conversion (byte-index shift, degree sign, duplicate-name rename) and expects `200 {"ok":true}` for each; then polls one custom PID on `0100` carrying 24 parameters (the ECU simulator answers it) and expects all 24 decoded in `GET /api/autopid` with no new E line; restores the config it found and reads it back equal. Born 2026-09-16 from "Profile rejected: 220105: more than 16 parameters" on picking Hyundai Ioniq5/6: `AP_PARAMS_PER` 16 rejected 11 of 80 profiles (Hyundai/Kia BMS DIDs decode 20-32 values, Xpeng 192), then a typo in three Hyundai/Kia profiles (cell 158 labelled `HV_C_V_168` twice) tripped the duplicate-name rule; cap now 256 with the poller's copy in PSRAM, the importer renames repeats and says so. Bench 2026-09-16 through the ssh tunnel: PASS
