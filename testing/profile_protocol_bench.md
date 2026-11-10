# A profile with ATSP6 and ATSP7 rows beside the chip's one protocol save

Command: `python tools\testbench\obd\profile_protocol_bench.py [--dut localhost:18081] [--write] [--only flip,byprofile,write]` (PC, the DUT through an ssh tunnel `ssh -N -L 18081:<dut>:80 rpi001`, ECU sim v2 at its own address)

Needs: main fw on DUT + rpi001 + ECU sim v2 on the bus at 500 kbit/s + the `/ws/obd` bridge, the chip's store reading `6`. Prints `PROFILE PROTOCOL PASS`. Without `--write`: no write of the chip, five planned restarts of the DUT, about 6 min. With it: TWO writes of the chip's protocol store (the flip and the way back), nine planned restarts, about 9 min.

The index row and the other stages: [../TESTING.md](../TESTING.md). The siblings: [protocol_save_bench.md](protocol_save_bench.md) (the save itself), [attpstore.md](attpstore.md) (the switches that must not write), [obdchipsoak.md](obdchipsoak.md) (the same profile for hours), [autopid_init_leak_bench.md](autopid_init_leak_bench.md) (what a row leaves behind for the next row type).

## What it covers

The firmware saves ONE protocol per car in the OBD chip (a real `ATSP` after the detection job). Most vehicle profiles pin a protocol themselves, and some pin both: the Audi / VW MEB profile has `ATSP7` rows for the battery ECU beside `ATSP6` rows for the gateway. Ali, 2026-11-10: "Keep the one protocol save per car, but we need to make sure it does not affect cars with ATSP6 and ATSP7 in there car profiles". Four things have to hold, and this bench reads each of them from the device:

1. the rows of such a profile poll right whatever protocol the chip holds;
2. polling them never writes the chip (every `ATSP` of a row goes out as `ATTP` behind an acknowledged `ATM0`);
3. the profile's protocol never becomes what is saved: the save takes only a protocol a standard request answered on;
4. the detection job, which decides what is saved, works while such rows are being polled.

The third and the fourth did not hold until 2026-11-10.

- **The profile's protocol was saved.** A car that answers no standard request is confirmed by the Quick Setup's probe with the profile's own requests, and the wizard names the protocol of the profile's first `ATSP`. The detection job handed that protocol to the save. On the bench the probe named 7, the request that answered was a row pinned on 6, and `ATSP7` was written to a chip that held 6. `ap_veh_chip_learns()` in the components repo's autopid decides now (host test `test_veh_chip_learns_only_a_standard_answer`).
- **The detection job ran on top of what a row had left in the chip.** A profile row's own flow control (`ATFCSH17FC007B;ATFCSD300000;ATFCSM1`) stayed in effect under the job and under the identity check: on the wire `0902` came back as its first frame only (`7E8 10 14 49 02 01 31 57 43`) until an `ATFCSM0`. With only profile rows in the tables the job failed with "no ECU response (ignition on?)", 3 of 3. Every prelude of the firmware's own carries the undo of what the tables moved now, and the job asks for it before its first request (`send_prelude_clean()`, `ap_runner_undo_tables()` in `autopid_baseline.c`).

The simulator plays a car of the bench's own (the soak's fixture with the VIN `1WCAN0FW0P0000003`, uploaded as `profile_proto.json` and made the simulator's active profile, so it survives the simulator's reboots). The DUT gets six rows for it: `EngineRPM` and `VehicleSpeed` (standard) and four rows of the published MEB profile, `SOC` and `BATT_TEMP` on `ATSP7`, `RANGE` and `OUTSIDE_TEMPERATURE` on `ATSP6`, in an order that changes the protocol at every row. The unit's own car is never touched.

| Leg | What is done | Must read |
|---|---|---|
| flip | the car's standard requests on the protocol the chip holds; AutoPID on, a restart | the unknown car detected at first contact without a request; `protocol_saves` 0, `protocol_kept` 1; 40 s of polling: every poll answered, the six values the simulator's (SOC 50.8, RANGE 32768, BATT_TEMP 24, OUTSIDE_TEMPERATURE 14, EngineRPM 800, VehicleSpeed 0), nothing saved, refused or held, one `ATM0` per poll, no E line. The same after a detection run while polling (the rows kept), after a restart (the bring-up reads the same store) and after an app's `ATZ` over `/ws/obd` (which shows the store on the wire) |
| flip, only the profile's rows | the two standard rows taken out, 8 s of polling, the detection job on request | the job finishes and its result carries the car's VIN (the row's flow control was undone first); 20 s of polling right afterwards |
| byprofile | the simulator's `0100` and `0902` silenced (its fault injection), a restart; the detection with the wizard's body: the profile, the protocol of its first `ATSP` (the OTHER one), its first three requests with their inits | `confirmed_by` `profile`, a new car on the profile's protocol; **`protocol_saves` +0, the store unchanged, the car's `chip_protocol` empty**; the four profile rows poll right; a restart reads the same store |
| write (`--write`) | the car's standard requests on the other protocol, a restart | ONE `ATSP` at the detection; the six rows poll right on a chip that holds the other protocol; a restart reads it back; a detection run on the known car saves nothing; then the way back: ONE `ATSP`, a restart, the store as found |
| restore | the bench's cars forgotten, the unit's own car activated, AutoPID's settings, the simulator's profile and settings put back | the vehicle store, the live tables, the settings and the chip's store as found; the restarts all planned |

## Records

Bench DUT 44bd8d73ca79, chip MIC3624 V2.3.24, the store as found `6`, the unit's own car `1WC 0001`. Logs: `test-reports/logs/2026-11-10_profile_protocol/`.

- 2026-11-10 16:48, `v6.0.0p-beta.9-1-g108a42d-dirty` (the committed trees of that afternoon, before either change), `--only flip` as it was then: PASS 37 checks. 96 polls in 40 s with 96 `ATM0`, 0 failed; the same six values after the detection run, the restart and the app's `ATZ`. Polling the rows of such a profile was never the problem.
- 2026-11-10 16:52, the same image, `--only byprofile`: **the reproduction of the saved profile protocol, FAIL**. The ring: `detection: nothing standard answered, the profile's 222AB6 did (1 responder)` and then `ATSP7 saved to the chip's EEPROM (base protocol learned once this boot)`: the probe named 7, the answering request was the `ATSP6` row, the store went from 6 to 7 and the car's record said `chip_protocol` 7.
- 2026-11-10 17:12 and 17:14, the image with the save's rule (sha256 `ec5966e3...`, ELF `142b4e80f10827c1`): `--only byprofile` PASS 29 checks (`protocol_saves` +0, the store 6, `chip_protocol` empty), `--write --only write` PASS 36 checks.
- 2026-11-10 17:29, the same image, `--only flip` with the profile-rows-only step added: **the reproduction of the failed detection, FAIL 1 of 43**: `detection FAILED: 0 rows, protocol 6 (obd2), vin (none), fp bf3423ae`, "no ECU response (ignition on?)".
- **2026-11-10 17:40, the image with both changes (sha256 `00ed81be...`, ELF `4f17c40d1da6e076`, `RELEASE TEST PASS` 59 at 17:33), every leg with `--write`: `PROFILE PROTOCOL PASS`, 84 checks, 0 failed, 8 min 20 s, nine planned restarts, two store writes.** flip: 96 polls in 40 s, 0 failed, 96 `ATM0`; the detection with only the profile's rows live done, VIN `1WCAN0FW0P0000003`. byprofile: the profile's `222AB6` confirmed the car on protocol 7, nothing saved, the store 6. write: `ATSP7` once, the six rows on a chip holding 7 (96 polls, 0 failed), the restart read 7, then `ATSP6` once and the restart read 6. Behind it `AUTOPID INIT LEAK PASS` (22 checks) and `AUTOPID PROFILE FIRST PASS` (40 checks) on the same image.

## Traps

- **Forgetting the current car leaves its rows live and no car current.** A boot in that state polls rows that pin a protocol with nothing naming the car's own, and first contact then takes the row's protocol for the one the chip found (the first open finding below). The bench makes the unit's own car current before every restart that is to meet an unknown car.
- **The simulator's fault injection is cleared by `DELETE /api/ecu/errors/<mode>/<pid>` (the entry stays listed with `enabled` false) and by its reboot.** The bench deletes both entries whenever it puts the car on a bus.
- **The simulator answers a profile's own requests only on a bus that has an OBD link.** With `hs_obd11` and `hs_obd29` both off it answers nothing at all, the profile's requests included; a car that is silent to standard requests is staged with the fault injection on `0100` and `0902` instead.
- **The release stage wants port 18081 for its own tunnel.** A tunnel left open by a bench makes `.\test.ps1 release` fail in 8 s with "no tunnel to the DUT through rpi001 (port 18081 busy?)".
- A run cut short between the flip and the way back leaves the store on the other protocol: the restore's own way back runs when the store does not read as found, and its last check says so when that failed.

## Open findings (2026-11-10, reproduced while building this bench, not fixed)

- **In search mode the identity check takes the chip's current protocol for the one its search found**, and a row's `ATSP7` may have set it. Seen with no current car and only profile rows live: `vehicle identity: vin (none), 0 ECUs, uds (protocol re-detected)` after an `ATSP7` row: the identity asked on 7 while the car's standard requests live on 6, and the car was not identified until a later try followed an `ATSP6` row. The same can follow a stored protocol that stayed silent at boot.
- **The Quick Setup's probe does not confirm a car by requests whose replies carry a 29-bit id.** With only the two `ATSP7` requests in its body the detection failed, "no ECU response", while both answer on the wire through test-a-PID; with the `ATSP6` requests it confirmed the car. Not root-caused (the first place to look: the reply parser's 29-bit hint, which the poller sets per row and the probe does not).
