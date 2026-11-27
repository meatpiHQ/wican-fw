# A full-scale reading is never dropped by its own range

Command: `python tools/testbench/obd/autopid_bounds_bench.py [--dut localhost:18081] [--watch 20]` (PC, the DUT through an ssh tunnel, the simulator through sim_net)

Needs: PC + ECU sim v2; the DUT on the bench AP, the PSU above the sleep voltage; 2026-10-28: the bench moved to the 2026-10-24 frame of reference (the table hands out B3*100/255 now, the custom rows read B3); on the stale numbering the custom rows read 18.43 (byte 2F, the PID echo), the firmware was right; PASS again that evening on the moved numbering (the raw check takes the printed header)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**A full-scale reading is never dropped by its own range** (prints `AUTOPID BOUNDS PASS`, 18 checks, 22 with its two planned restarts (taken when Automate or one of its three row types is off), ~1.5 min; issue #1004, 2026-10-20: a VW T6.1's full tank answered `41 2F FF`, the standard FuelTankLevel row decoded it as `B2*0.3921568692` = 100.000001646, a hair above its max of 100, and the poller dropped it: stale dashboard, MQTT kept the old value, the Test button showed 100.000001646). The ECU simulator's fuel level is set to 100 % (raw FF) and four rows are polled every second: the alfa-04 standard row as stored (must read exactly 100: clamped), a control row (CalcEngineLoad 25.1 %), a custom row with the same float noise (100 too: the tolerance) and a custom row decoding 102 against a max of 100 (stays dropped: a custom row's bounds are a filter); then `GET /api/autopid/std_table` must hand out the fuel row as `B2*100/255` with max 100 and the test route must decode the full tank as exactly 100 with it. Restores the simulator's level, the tables and the settings.

**Reproduced before the fix (2026-10-20):** FuelTankLevel and the noisy custom row null while the control row read, `polls_failed` +15 in 20 s, the table's expression `B2*0.3921568692`, the test route 100.000001646; PASS after it

## A profile's bounds are strings (2026-11-27)

Every published vehicle profile writes a parameter's `min` / `max` as strings (`"max": "100"`: 2740 of the 2740 bounds of the list's 90 cars) and the web page's import stores the parameter as it is. v6 read a bound only when it was a JSON number, so no range of a vehicle profile was applied; the legacy firmware read both. The field report behind it (Discord, 2026-11-24, a Mitsubishi Outlander PHEV): the profile's Odometer row (`22F0D0` behind `ATCRA18DAF1DB`, `[B4:B6]`, `"max": "1000000"`) read FFFFFF = 16,777,215 km about half of the time on v6 and never on 4.5x.

Three rows were added to the table, of the type `specific` and with their bounds as the page stores a profile's (strings), all on the simulator's full tank (`41 2F FF`):

| Row | Expression | Bounds | Must read | Why |
|---|---|---|---|---|
| `ProfileWrongScale` | `B3*0.4` = 102 | `"0"` .. `"100"` | nothing (dropped) | a profile's range is a filter: the reporter's 16777215 against `"1000000"` |
| `ProfileNoise` | `B3*0.3921568692` = 100.000001646 | `"0"` .. `"100"` | exactly 100 | the float-noise tolerance holds for a string bound too |
| `ProfileInside` | `B3*0.2` = 51 | `"0"` .. `"100"` | 51 | a reading inside a string range is not touched |

The bench switches the vehicle-specific row type on with the other two (`specific_enabled`), with the planned restart when it was off.

**Red before the fix (2026-11-27 12:35, unit 14c19f457fd9, image sha256 `88e4b978`, the tree without the parser change): `AUTOPID BOUNDS FAIL`**, 2 of 18: `ProfileWrongScale` published 102 against a max of `"100"`, `ProfileNoise` published 100.000001646. The four older rows right.

**Green with it (2026-11-27 12:39, the same unit, image sha256 `5ec68ef3`): `AUTOPID BOUNDS PASS`, 18 checks**: `ProfileWrongScale` null, `ProfileNoise` 100, `ProfileInside` 51, the older rows as before (`FuelTankLevel` 100, `FuelCustomNoise` 100, `FuelWrongScale` null), `polls_failed` +14 in 20 s (the two dropped rows), tables restored to the 29 rows found.

**The reporter's own row, by hand on the same day** (the profile's parameter copied as the page copies it, the ECU played on the PCAN as `18DAF1DB`, 29-bit, the simulator a 29-bit car for the run):

| Image | Bounds stored as | ECU answers | Published |
|---|---|---|---|
| without the fix | strings | `06 62 F0 D0 FF FF FF` | 16777215, every second |
| without the fix | numbers | `06 62 F0 D0 FF FF FF` | nothing (dropped) |
| with the fix | strings | `06 62 F0 D0 FF FF FF` | nothing (dropped) |
| with the fix | strings | `06 62 F0 D0 01 E2 40` | 123456 |

The test route (`POST /api/autopid/test`) applies no bounds: it shows 16777215 on both images.

**What the car itself answers is not on record (looked up 2026-11-27).** The Car Scanner logs of the same model that users sent (the same generation: 29-bit, protocol 7, `18DAF1DB`, `18DAF1DE` and `18DAF1E5` among its eight ECUs) never ask `22F0D0`, so when the ECU turns to `FF FF FF` stays unknown. They do show the habit behind the report: `18DAF1DB` answers an identifier it holds no data for with FF bytes, not with a refusal (`22F190`: 17 bytes of FF every complete time, 5 of 5, while three other ECUs answer the same request with their data). Six ECUs answer a `22` request on the functional id, which the profile's `ATCRA18DAF1DB` narrows to one. Every request in those logs went out on the functional id: whether the ECU answers the same when it is addressed directly is not recorded.

**Regressions on the image with the fix (2026-11-27):** `.\test.ps1 profilee2e` PASS, 40 checks (12:41: the published Leaf ZE1 profile, 128 parameters with their string ranges now applied, every published value right, `polls_failed` +0); `autopid_profile_bench.py` PASS (12:45: 90 of 90 published profiles accepted, the 24-parameter row read); `.\test.ps1 twoecus` PASS, 28 checks (12:45, `test-reports/2026-11-27_1245_twoecus.md`); `.\test.ps1 host autopid` PASS, 209 tests (12:48, [host.md](host.md)); the unit's own 29 rows (an Audi profile among them, 9 string bounds) all still read.

Traps:

- **A profile row needs the vehicle-specific type switched on.** With `specific_enabled` off the three `Profile*` rows are not polled and read null, which looks like "dropped" for the first one and like a failure for the other two. The bench stages the setting.
- **A dropped reading counts as a failed poll.** `polls_failed` rises while the bench runs (about 14 in 20 s for the two dropped rows); the bench's last L2 check only wants `polls_ok` to rise.
