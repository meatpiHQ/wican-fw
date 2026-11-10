# OBD chip protocol save: read back first, one ATSP per boot

Command: `python tools\testbench\obd\protocol_save_bench.py [--dut localhost:18081] [--profile <json>] [--write]` (PC, the DUT through an ssh tunnel `ssh -N -L 18081:<dut>:80 rpi001`, ECU sim v2 at its own address)

Needs: main fw on DUT + rpi001 + ECU sim v2 on the bus at 500 kbit/s + the `/ws/obd` bridge. Prints `PROTOCOL SAVE PASS`. Without `--write`: no write of the chip, about 20 s, two simulator restarts at most. With it: TWO writes of the chip's protocol store (the flip and the way back), two planned restarts of the DUT, four simulator restarts, about 90 s.

The index row and the other stages: [../TESTING.md](../TESTING.md). The sibling stage for the switches that must NOT write: [attpstore.md](attpstore.md). The driver's side: the components repo's obd_chip README, "Protocol save" and "Read back first, one `ATSP` per boot".

## What it covers

After a vehicle detection the firmware saves the car's protocol in the chip (`obd_chip_protocol_save`, a real `ATSP`: the one deliberate write of the chip's own memory). Two things wrote more than needed until 2026-11-10:

- a new car on a protocol the chip already held got an `ATSP` that changed nothing (the caller compares the car's record, the driver did not know what the chip held);
- the one-per-boot limit counted acknowledged saves, so after a lost answer the next detection sent the word again, in the same boot and in every later one.

Since then the bring-up reads the chip's stored protocol (`ATDPN` right after the chip's reset: `eeprom_guard.stored_protocol`, in the chip's words), the save sends nothing when the chip holds the protocol (`protocol_kept`), and the one `ATSP` of a boot is counted when it goes out.

The unit's own car is never touched. The simulator plays a SECOND car: a profile with another VIN (the soak's fixture by default, `1WCAN0FW0P0000002`), uploaded as `save_probe.json` and reloaded warm after every id format change (a simulator reboot puts it back on `default.json`). To the DUT that is a new car each time its record is forgotten.

| Leg | What is done | Must read |
|---|---|---|
| as found | `GET /api/obd_chip`; `ATZ`, `ATE0`, `ATDPN` over `/ws/obd`; the log ring; the vehicle store and the live tables noted | `stored_protocol` equals what the reset shows; the bring-up's line reads `started: chip ... memory off, stored protocol <p>` |
| kept | the simulator on the stored protocol's id format, the second car playing; `POST /api/autopid/vehicles/detect` | the car found on that protocol; `protocol_saves` +0, `protocol_kept` +1; the car's `chip_protocol` = the protocol; the ring reads `ATSP<p> not sent: the chip's store holds <p> already`; the store unchanged over the bridge |
| write (`--write`) | the simulator on the other id format; the detection of the second car there; a planned restart; the car forgotten and detected again | ONE `ATSP` (`protocol_saves` +1, `stored_protocol` the new one, the ring reads `saved to the chip's EEPROM`); after the restart the bring-up reads the new protocol and a reset over the bridge shows the same (the read-back); the second detection sends nothing (`protocol_kept` +1, `protocol_saves` +0) |
| the way back (`--write`) | the simulator on the as-found id format; the detection; a planned restart | ONE `ATSP`; the bring-up reads the as-found protocol |
| restore | the second car forgotten, the unit's own car activated, the simulator on `default.json`, `save_probe.json` deleted | the vehicle store (keys, names, protocols, `chip_protocol`, profiles) and `GET /api/autopid/config` as found |

Not covered here, by construction: the lost answer. A real chip answers its `ATSP`; the host suite stages the loss with a fake chip (`.\test.ps1 host obd_chip`, `test_save_lost_answer_is_not_written_twice` and the five `test_save_*` beside it).

## Records

- 2026-11-10 14:03, by hand as the session's probe script (the same legs; bench DUT 44bd8d73ca79 on `v6.0.0p-beta.9-1-g108a42d-dirty` with the components tree's obd_chip change, chip MIC3624 V2.3.24, the store as found `6`, the unit's own car `1WC 0001`): **`PROTOCOL SAVE PASS` 37 checks with `--write`**. kept: the second car found on 6 in 6 s, saves +0, kept +1, `ATSP6 not sent: the chip's store holds 6 already ('6')`. write: on 29-bit the car found on 7, `ATSP7 saved to the chip's EEPROM`, saves +1; after the restart `stored_protocol` `7` and the bridge's reset reads 7; the car forgotten and detected again: saves +0, kept +1. The way back: `ATSP6` once, the restart reads 6. The unit's car, its tables and the simulator as found. Two store writes.
- 2026-11-10 14:15, the bench from the repo with the fixture profile, without `--write`: `PROTOCOL SAVE PASS` 15 checks, 16 s, no write.

## Traps

- **The unit's vehicle store is not empty.** The bench unit carries the simulator's own car (`1WC 0001`); a bench that forgets "the" car to get a new one would delete its name and tables. Hence the second VIN.
- **A simulator reboot ends the second car.** Every id format change reboots the simulator, which then plays `default.json` again: the bench reloads `save_probe.json` after each one. A warm reload of `default.json` does not make it the active profile for a `DELETE` of the other (409): the restore submits the simulator's settings once (a reboot) when that happens.
- **The detection makes the second car current.** The restore activates the unit's own car again (`POST /api/autopid/vehicles/<key>/activate`), which puts its tables back; the bench compares them.
- `stored_protocol` is the bring-up's read: an app that wrote the store later in the boot would not show until the next start. The as-found check compares it with a reset over the bridge and fails on such a drift, which is information.
- With `--write` a run cut short between the flip and the way back leaves the store on the other protocol. The firmware works the same (it sets the protocol itself at every start); to put the store back, one `ATSP<p>` through the wire probe (`POST /api/obd_chip/probe {"kind":"probe","cmd":"ATSP6"}`, one write).

**2026-11-10 22:32, regression for that evening's autopid changes (the image `36ce9508...` of `testing/profile_protocol_bench.md`), without `--write`: `PROTOCOL SAVE PASS`, 15 checks, 0 failed, no chip write.**

**2026-11-11 00:02, regression for the protocol closed behind the bring-up's warm start (the image sha256 `359d87ed...` of `testing/autobaud.md`), without `--write`: `PROTOCOL SAVE PASS`, 15 checks, 0 failed, no chip write.** `ATDPN` behind the close reads the store as before.
