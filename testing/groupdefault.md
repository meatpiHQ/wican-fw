# An Automate group stored off stays off

Command: `.\test.ps1 groupdefault` (PC; it opens the tunnel and runs `python tools\testbench\obd\autopid_group_default_bench.py [--dut localhost:18081] [--watch 12] [--boot-watch 45]`)

Needs: main fw on DUT + rpi001 + the ECU simulator answering 7DF (the rows are `0104`, `0105`, `010C` and `010D`), the PSU above the sleep voltage. Prints `AUTOPID GROUP DEFAULT PASS`. About 2.5 min, one planned restart (two more when Automate was off at the start), no console, no PCAN, no write of the OBD chip. The unit's tables and settings are put back as found.

The index row and the other stages: [../TESTING.md](../TESTING.md). The fix: meatpi-components pull request #17 (Jeff Munk, `autopid_config.c`), GitHub issue #1058; the `autopid` README, "Config file shape"; `components/HTTP_API.md`, the note of 2026-11-24 under `/api/autopid/group`. The host half: `.\test.ps1 host autopid` (`test_group_default.c`, 2 cases, [host.md](host.md)).

## What it covers

The report (2026-11-11, a 2024 Nissan Ariya on 6.0.0-beta.7): two groups stored with `"enabled_default": false` and switched on by rules when the car starts polled at every boot and 0.5 s after every `PUT /api/autopid/config`, with the car off. On that car a body module that is asked while the car sleeps stores CAN trouble codes. The parser read a group's starting state from `"enabled"` only; the web UI and the setup page write `"enabled_default"`.

The bench stores four groups with one row each:

| Group | Its words | What must hold |
|---|---|---|
| `off_ui` | `"enabled_default": false` | off, its row never asked: the issue's case |
| `off_doc` | `"enabled": false` | off, its row never asked: the word that worked before, the control |
| `on_ui` | `"enabled_default": true` | on, its row asked: what the web UI writes for a group |
| `on_none` | neither word | on, its row asked |

A group's state is read from `groups[]` of `GET /api/autopid`, and a row is judged by the time of its last value there (`ts_us`): "not asked" means its time did not move once in the watch, while the rows of the two groups that are on moved in that same watch. So a watch in which nothing was polled at all cannot pass.

| Leg | What is done | What must hold |
|---|---|---|
| L0 preflight | the unit answers; the tables and the `autopid` settings are saved; Automate and the custom rows switched on when they were off | saved, the poller on |
| L1 the PUT | the four groups go in by `PUT /api/autopid/config`; 12 s of samples every 0.4 s, from the first answer | `off_ui` and `off_doc` off in every sample and their rows never asked; `on_ui` and `on_none` on and asked |
| L2 the switch | `POST /api/autopid/group` turns `off_ui` on; 12 s of samples. Then the same tables by PUT again; 12 s of samples | switched on: its row is asked (the group is whole, a rule can start it), `off_doc` stays off. After the PUT: off again, its row not asked once |
| L3 the restart | `POST /api/restart`; 45 s of samples from the first answer of the new boot | the same as L1, from the first sample (the report: the first reply about 5 s after the start) |
| L4 restore | the tables and the settings back | accepted; `log_errors` 0, no fault, `unexpected_resets` as at the start |

## Records

**2026-11-25 12:47 to 12:58, bench DUT 14c19f457fd9, ECU sim v2 v1.00-28.**

- **Red on 6.0.0-beta.15 as published** (`v6.0.0p-beta.15`, ota_1), 12:47: `AUTOPID GROUP DEFAULT FAIL`, 47 checks, 6 failed, all six the issue's: `off_ui` reported on in 27 of 27 samples after the PUT and its row asked (`GrpOffUi` ts_us none to 26861815352), the same after the second PUT (28 of 28), and on in 99 of 99 samples after the restart, its row first read 3.4 s into the boot. `off_doc`, the two groups that are on, the switch and the restore passed: the old parser kept a group off for `"enabled"` and for nothing else.
- **Green with pull request #17** (image sha256 `a5e9aad3`, firmware `c2c4baf` + components `c39793d` and our header note, by upload into ota_0), 12:52: `AUTOPID GROUP DEFAULT PASS`, 47 checks, 0 failed. `off_ui` off in every sample of every leg and `GrpOffUi` without a value until the switch; switched on, it was read (ts_us 42549432 to 53979810); after the same PUT it was off again; after the restart off in 99 of 99 samples over 45 s while `GrpOnUi` and `GrpOnNone` were read from 3.4 s and 5.4 s on.
- **Through the runner**, 12:56: `.\test.ps1 groupdefault -DeviceId 14c19f457fd9`, PASS, 47 checks, 95 s (`test-reports/2026-11-25_1256_groupdefault.md`).
- Each run: one planned restart, `log_errors` 0, no fault, `unexpected_resets` 0, the unit's 29 rows back.

## Traps

- **The simulator must answer.** A row that nobody answers has no value and no time either, so the two "asked" checks fail and name the row; the two "not asked" checks would pass by themselves, which is why the bench asks for both in one watch.
- **A value from before the PUT is not a poll.** A PUT of the same names keeps nothing of the old values, but the bench does not rely on that: it compares each row's time with the time in the first sample of the same watch.
- **The build's name does not change between two work builds** (`fw_build` reads `v6.0.0p-beta.15` for the published image and for the one with the fix): name an image by its sha256 and its slot.
