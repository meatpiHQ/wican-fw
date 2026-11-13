# A pre-CAN car found without being asked for, and by the detection

Command: `.\test.ps1 precan [-Detect]` (PC; it opens the tunnel and runs `python tools\testbench\obd\precan_contact_bench.py [--dut localhost:18081] [--only test,startup] [--detect] [--can off|on]`)

Needs: main fw on DUT + rpi001 + ECU sim v2 (firmware v1.00-16 or later for `-Detect`) with its K-line and J1850 buses wired to the DUT's OBD connector, Automate off at the start, the unit's own car a CAN one with a VIN. Prints `PRECAN CONTACT PASS`. About 8 min, seven planned restarts of the DUT, about thirteen restarts of the simulator, no write of the OBD chip. With `-Detect`: about 1.5 min more, three more restarts, and three writes of the chip's stored protocol (the one save a detection makes by design: to 2, to 5 and back). The unit's tables, its Automate settings, the vehicle store and the simulator are put back as found.

The index row and the other stages: [../TESTING.md](../TESTING.md). The host half: `.\test.ps1 host autopid` (`test_dialect_precan_candidates`, the walk's order in `test_precan_protocol_table`), [host.md](host.md). The neighbour stage: [kline_accept_bench.md](kline_accept_bench.md) (the Test call, the poll, the trouble codes on these protocols).

## What it covers

A unit plugged into a K-line or SAE J1850 car polled the protocol of the car it knew, gave it up for the chip's own search and never found the car. The walk that looks for whoever is there ended behind the CAN protocols, and the detection did not try SAE J1850. Now the walk goes on to SAE J1850 and the K-line (`2`, `1`, `5`, `3`, `4`, the cheapest first), in the detection always and at first contact when nobody else talks on the CAN pins.

ECU sim v2 plays one car at a time. Its ECUs all carry the VIN of the bench unit's own car, so first contact meets "the same car on another protocol": the store records the protocol and nothing is written to the OBD chip. A car the unit has never seen is made by forgetting the unit's own car first (`-Detect`).

| Leg | What is done | What must hold |
|---|---|---|
| the unit's own CAN node | as found; `--can off` or `--can on` sets it for the run with the first restart and puts it back with the last | `/api/can` reads it so behind the first restart. The two states answer "is anybody else on the CAN pins" in two ways (a running node acknowledges the OBD chip's requests and counts what it hears while the chip is quiet; with the node off a listener looks). A unit from the factory has the node off; the bench's unit has it on |
| test, a CAN car on 250 kbit/s that says nothing until asked (2026-11-13) | Automate off; the simulator as a CAN car on 250 kbit/s alone, its broadcast off, a restart; ten requests of the Test route on protocol 8, one every 1.5 s | all ten answered. With the unit's own CAN node on (fixed at 500 kbit/s) that node cannot read the talk, goes listen-only and its probe says `unreadable` for good: the unit's own conversation, which must not stop the unit (before the fix: four of twelve refused with "the vehicle bus carries traffic at a bitrate that could not be read") |
| startup, an ISO 9141-2 car | three rows in the tables, the simulator as an ISO 9141-2 car alone, Automate on with the protocol on Automatic (a planned restart) | the car's record names protocol 3 within 90 s and the unit's own log line says `contact: a vehicle answers on protocol 3` in that boot; the rows are answered round after round (800 rpm); the K-line session is up; no protocol save, the chip's store as it was |
| startup, traffic and no answer | the simulator as a CAN vehicle that answers nothing standard (HS CAN on with its broadcast frames, OBD and UDS answers off), a restart; 40 s watched from the moment the stored protocol is given up | the simulator received the walk's requests (four frames or more) and the driver counted no K-line start: SAE J1850 and the K-line are left alone; the record stays |
| startup, nobody | every bus of the simulator off, a restart; 45 s watched | the driver counts three to twelve K-line starts that failed (three a round of the walk: the gap grows), no poll is answered, the record stays, no session |
| startup, a car turns up | the simulator becomes a J1850 VPW car in that boot | found on protocol 2 by the next round (within 120 s), the rows answered, no K-line session, no protocol save |
| startup, a car asleep, then a CAN car on 250 kbit/s (2026-11-13) | every bus of the simulator off, a restart, 25 s; then the simulator becomes a CAN car on 250 kbit/s that says nothing until asked, in that boot | behind the 25 s the bus guard's picture is `silent`: the chip's own requests on 500 kbit/s, which the unit's own CAN node acknowledges and reads as frames when it is on, do not name the bus (before the fix: `vehicle bus live at 500 kbit/s` for the boot, protocols 8 and 9 never asked, the car not found in 155 s). The car is found on protocol 8 by a later round (within 150 s), the rows answered, no protocol save |
| startup, the way back | the simulator as the unit's own CAN car, a restart | found on its CAN protocol again, the rows answered, no protocol save |
| detect, a car never seen (`-Detect`) | the unit's own car forgotten (`DELETE /api/autopid/vehicles/<key>`), the simulator as a J1850 VPW car, Automate on (a planned restart); nobody asks for a detection | within 120 s the store holds the car again with protocol 2 and its VIN, the job is `done` with its supported PIDs, the log says `contact: a vehicle answers on protocol 2`; the chip holds 2 after ONE save |
| detect, by hand (`-Detect`) | a restart with the simulator as an ISO 14230-4 car, `POST /api/autopid/vehicles/detect`; then the same on the unit's own CAN car | the job is `done` on protocol 5 with the VIN (five messages of seven data bytes) and the supported PIDs, the record names 5 and the chip holds 5 after ONE save; then the same for the CAN protocol; the chip's store reads as found |
| the chip | `chip_nvm`'s snapshot before and after | the 40 stored cells the same, `protocol_saves` 0 (with `-Detect`: the cells the same again after the way back) |
| the unit | at the end | the tables, the Automate settings, the vehicle store (the car, its name, its protocol) and the simulator as found, no E line in the log, the boot count moved by the planned restarts, no unexpected reset |

## Records

**2026-11-13 00:39 to 00:47, before the change (bench DUT 14c19f457fd9, chip MIC3624 V2.3.24, image ELF `a36bf8b3288986a4`), by hand.**

- **What a try costs**, through the Test call with the protocol as its init, Automate off:

| The car | `2` VPW | `1` PWM | `5` fast init | `3` ISO 9141-2 | `4` KWP 5 baud |
|---|---|---|---|---|---|
| nobody | `NO DATA` 0.67 s | `NO DATA` 0.62 s | `ERROR` 0.99 s | `ERROR` 2.80 s | `ERROR` 2.80 s |
| J1850 VPW | answers 0.68 s | `NO DATA` 0.61 s | `ERROR` 0.99 s | `ERROR` 2.80 s | `ERROR` 2.80 s |
| J1850 PWM | `NO DATA` 0.67 s | answers 0.62 s | `ERROR` 0.99 s | `ERROR` 2.80 s | `ERROR` 2.80 s |
| ISO 9141-2 | `NO DATA` 0.67 s | `NO DATA` 0.61 s | `ERROR` 0.99 s | answers 3.29 s | `ERROR` 2.44 s |
| ISO 14230-4 | `NO DATA` 0.67 s | `NO DATA` 0.62 s | answers 1.18 s | `ERROR` 2.44 s | answers 3.27 s |

  No try on one protocol is answered by a car on another. The walk with nobody there: 7.9 s.
- **"Detect my vehicle" on a J1850 VPW car**: `failed` after 10 s, tried 6, 7, 5, 3, 4, "no ECU response (ignition on?)".
- **Start-up on an ISO 9141-2 car** (the unit's own car, known on protocol 6; Automate on, Automatic): in 152 s no poll answered and 172 failed, the K-line never started (`kline.starts` 0), the record stayed on 6. The log: `stored protocol 6: no answer in 3 polls, using the chip's protocol search this boot`, and nothing behind it.

**2026-11-13 00:59, the first build of the change (ELF `a6ad8651b2bb8c50`): `PRECAN CONTACT FAIL`, 27 checks, 7 red.** The K-line was not tried once, not even with every bus of the simulator off. The walk left the pre-CAN protocols out whenever the bus guard's picture said "live", and it said `vehicle bus live at 500 kbit/s` on a car with no CAN at all: the bench unit's own CAN node is enabled, acknowledges the OBD chip's requests and reads them as frames (`can_manager: probe: bus live at 500 kbit/s (2 frames, 0 ms)` at 5.2 s of the boot, behind the first polls). The fix: the question is asked behind the CAN tries with the chip quiet (`ap_guard_others_on_can()`): what the running node hears in 300 ms of the chip's silence, or a look of a node that only listens.

**2026-11-13 01:16 to 01:21, the second build (image sha256 `d5f857f6`, ELF `b98759068f5bd16a`), the start-up leg as it stood: `PRECAN CONTACT PASS`, 27 checks, 0 failed, 4 planned restarts.** 01:22 to 01:28 with the detection leg as it stood (the detection by hand on a J1850 VPW car, on an ISO 14230-4 car and back): `PRECAN CONTACT PASS`, 37 checks, 7 planned restarts, 346 s; the detection on the VPW car `done` after 7 s (tried 6, 7, 2), on the ISO 14230-4 car after 10 s (tried 6, 7, 2, 1, 5), back on CAN after 3 s. Three writes of the chip's stored protocol: 01:23:29 6 to 2, 01:23:56 2 to 5, 01:24:16 5 to 6.

**2026-11-13 01:30, the leg "traffic and no answer" at its first run: red by the bench.** The unit's log ring outlives a restart, the bench took the line of the boot before for this boot's and opened its window while the polls on the stored K-line protocol were still failing (two starts counted). `contact_lines()` now drops what stands in front of the last fall of the uptime.

**2026-11-13 01:38 to 01:45, the same image, the stage as it is now with every leg: `PRECAN CONTACT PASS`, 39 checks, 0 failed, 8 planned restarts (`.\test.ps1 precan -Detect`, 422 s).**

- **A car never seen, a J1850 VPW one**: 10 s after the unit was back the store held the car again (protocol 2, the VIN, the ECU at address 10), stored by a detection nobody asked for (`done`, 16 PIDs; its rows stored off, as for every new car). The chip's store went from 6 to 2 with one save.
- **The detection by hand on an ISO 14230-4 car**: `done` after 10 s, tried 6, 7, 2, 1, 5, the VIN from five messages, 16 PIDs, one fast init at the simulator; the chip 2 to 5. Back on the unit's own CAN car: 3 s, the chip 5 to 6.
- **Start-up on an ISO 9141-2 car**: the unit's line `contact: a vehicle answers on protocol 3` at 19.99 s of its uptime, the record on 3, eight answers and no failure of the rows in the three rounds behind it, the K-line session up after one start (the fast init tried and refused before it).
- **Traffic on the CAN pins and no answer**: the simulator received 27 frames of the walk in 40 s and the driver counted no K-line start.
- **Nobody there**: six K-line starts that failed in 45 s (two rounds), eleven failed polls, the record unchanged. **A J1850 VPW car turning up in that boot**: found at 81.2 s of the uptime, the rows answered, no session.
- **The way back**: the unit's own CAN car found at 12.3 s of the uptime.
- Three writes of the chip's stored protocol, all in the detect leg: 01:38:59 6 to 2, 01:39:26 2 to 5, 01:39:46 5 to 6. 40 cells the same at the end, no E line, boot 17 to 25 with eight planned restarts.

**2026-11-13 01:45 to 02:11, the same image, what followed.**

- Regression: `KLINE ACCEPT PASS`, 58 checks (`.\test.ps1 kline`, 372 s); `RELEASE TEST PASS`, 72 checks, 351 s (the running ELF `b98759068f5bd16a`); `.\test.ps1 host autopid` PASS, 176 tests.
- **The unit's own CAN node off, as a unit from the factory has it** (`python tools\testbench\obd\precan_contact_bench.py --only startup --can off`, 02:05 to 02:10): `PRECAN CONTACT PASS`, 31 checks, 0 failed, 5 planned restarts. `/api/can` read `enabled` false and `running` false in the run. The ISO 9141-2 car found at 19.7 s of the uptime, 27 frames of the walk and no K-line start with traffic on the CAN pins, six failed K-line starts in 45 s with nobody there, the J1850 VPW car found at 86.1 s, the CAN car at 12.4 s; the node on again at the end. With the node off the question "is anybody else on the CAN pins" is answered by a listener's look, not by the running node's counters: both ways are run.
- **The default stage as it is now** (`.\test.ps1 precan`, 02:11 to 02:17, the node on as found): `PRECAN CONTACT PASS`, 30 checks, 0 failed, 5 planned restarts, 347 s. The ISO 9141-2 car at 20.2 s of the uptime, the J1850 VPW car at 81.6 s, the CAN car at 12.5 s; 40 cells the same, no protocol save, no E line.

**2026-11-13 07:44 to 09:38, the same image on a simulator that is not ours (a third-party OBD-II simulator box, one protocol at a time), by hand.** The unit's Automate on, the protocol on Automatic. The simulator's ECU answers from address 0x11 before CAN (`7E8` and `18DAF111` on CAN), claims every PID, and shows on its screen what it plays: every value and all fifteen trouble codes below were compared with that screen.

| the simulator plays | found at start-up, the chip holding the protocol before | the rows, the trouble codes | "Detect my vehicle" |
|---|---|---|---|
| SAE J1850 VPW | 14.4 s (a car the store did not know: stored by a detection nobody asked for, 92 rows) | 8 values equal, 5 stored + 5 pending + 5 permanent codes, each list from two messages, the freeze frame's code | 9 s, tried 6 7 8 9 2 |
| ISO 9141-2 | 19.2 s (the same car by its VIN, no protocol save) | equal, the fifteen codes | 15 s, tried 6 7 8 9 2 1 5 3 |
| ISO 14230-4, 5 baud init | 24.6 s | equal, the fifteen codes | 17 s, tried 6 7 8 9 2 1 5 3 4 |
| ISO 14230-4, fast init | 20.5 s | equal, the fifteen codes | 12 s, tried 6 7 8 9 2 1 5 |
| CAN 11 bit 500 kbit/s | 12.5 s | equal, the fifteen codes | 1 s, tried 6 |
| CAN 29 bit 500 kbit/s | 13.0 s | equal, the fifteen codes | 5 s, tried 6 7 |
| CAN 11 bit 250 kbit/s | see below | equal, the fifteen codes (the unit's own CAN node off) | 4 s, tried 6 8 |
| CAN 29 bit 250 kbit/s | 15.7 s (the unit's own CAN node off) | equal, the fifteen codes | 5 s, tried 6 8 9 |
| SAE J1850 PWM | no answer from that simulator: `NO DATA` with the chip alone too | - | - |

- The simulator's ISO 14230-4 sends the legislated answers in messages of seven data bytes (the VIN in five, the codes three a message), as ECU sim v2 does.
- **A finding, open:** a 250 kbit/s CAN bus that carries nothing until it is asked, and the unit's own CAN node on (a unit from the factory has it off). The node, fixed at 500 kbit/s, acknowledges the OBD chip's 500 kbit/s tries, the bus guard says "vehicle bus live at 500 kbit/s" and refuses protocols 8 and 9: 143 s of a boot, the car not found. A car the unit knows on 250 kbit/s already is answered with the node on (880 polls in 112 s, none failed, the node listen-only by itself). With the node off: the table.
- The simulator supplies 11.6 V at the unit: Automate's request pause (it follows the sleep voltage) was switched off for the run, and the unit was kept awake with `POST /api/sleep/hold` (under 11.90 V it sleeps 5 min behind every boot, sleep setting or not).
- Nine writes of the OBD chip's stored protocol, each the one save of a detection: 6 to 2, 3, 4, 5, 6, 7, 8, 9 and back to 6. The unit as found at the end: its settings, tables, vehicle store, the chip's 40 stored cells, no fault, no unexpected reset.

**2026-11-13 09:51 to 11:10, the open finding of the morning reproduced with ECU sim v2 and fixed (image sha256 `99e2ae3c`, ELF `4800c77470986ccc`).**

- Reproduction on the image before (ELF `b98759068f5bd16a`), the unit's own CAN node on. Nobody on the bus at the boot (every bus of the simulator off, a car asleep): that node read the OBD chip's own requests on protocol 6 as frames (3, then 11, no error) and the bus guard said `vehicle bus live at 500 kbit/s`. The simulator then became a CAN car on 250 kbit/s with its broadcast off: not found in 155 s, and not one frame reached the simulator. The same with the node off: found 29 s behind its wake-up. A car that is on 250 kbit/s at the boot already is found on this bench either way (fresh from its restart the simulator destroys the chip's 500 kbit/s frames, the unit's node sees errors and goes listen-only; the third-party simulator let them pass).
- The Test route on that car, Automate off, the node on: 8 of 12 requests on protocol 8 answered, four refused with "the vehicle bus carries traffic at a bitrate that could not be read (not 250 or 500 kbit/s)".
- The cause and the fix are in the bus guard (components, autopid README, "What a node of the unit's own reports"): a node that runs acknowledges the chip's requests and cannot read its talk at the other bit rate, so its picture counts only when it hears something while the chip is quiet.
- After: the car found 30 s behind its wake-up with the node on; 12 of 12 requests answered. `.\test.ps1 precan` with the two new legs: `PRECAN CONTACT PASS`, 38 checks, 0 failed, 7 planned restarts, 494 s (the node on as found: ten of ten Test requests, the guard `vehicle bus silent` behind 25 s of a car asleep with 4 of the chip's frames read by the node, the car on protocol 8 at 75.2 s of the uptime, 38 s behind its wake-up). The same with `--can off`: PASS, 39 checks, the car at 75.5 s. `KLINE ACCEPT PASS`, 58 checks; `RELEASE TEST PASS`, 72 checks, 356 s; `.\test.ps1 host autopid` PASS, 178 tests.
- `.\test.ps1 autobaud` (the bus guard's legs with traffic on the wire) said nothing that morning: its PCAN adapter was not on the unit's bus (the stage's first check: 3 of 599 frames on the bus).

**2026-11-13 12:18 to 14:08, the same change finished on the stage `autobaud` (its page has the record: [autobaud.md](autobaud.md)).** With the adapter on the bus, that stage and a look by hand with the unit's CAN node on showed what the morning's build was missing: on a bus that woke up at another bit rate the chip was parked late or not at all (the node's counters, which the build listened to, are dead once the chip's own unanswered requests have masked the node's interrupts). The guard now watches the CAN receive line itself while the chip is quiet, keeps "live" only on frames the node reads in that quiet, and the node is started again when it comes back from a mismatch under that mask. On the final image (sha256 `d68fb530`, ELF `7dfddff5a8df9dd8`), this stage again at 13:48: **`PRECAN CONTACT PASS`, 38 checks, 0 failed, 7 planned restarts, 495 s** (ten of ten Test requests on the 250 kbit/s car with the node on; the car that wakes up in that boot on protocol 8 at 75.4 s of the uptime, 37 s behind its wake-up, the rows answered behind it, nothing written to the OBD chip). Not found, with the node on and fixed at 500 kbit/s: a 250 kbit/s car whose bus carries traffic of its own (parked, nothing sent; so it was before the change). The sentence of that park names the node's bit rate and its setting since the image of 14:43 (sha256 `5d4c54be`, ELF `017f3e07d4fb6ddc`); this stage on it at 14:56: `PRECAN CONTACT PASS`, 38 checks, 0 failed, 7 planned restarts, 492 s.

## Traps

- **The unit's log ring outlives a restart.** A line of the boot before reads like one of this boot. The uptime in front of the lines falls back where a new boot begins.
- **The bench unit's own CAN node is enabled** (the bench's canonical set) and acknowledges the OBD chip's CAN requests. On a car with no CAN the bus guard therefore reports `vehicle bus live at 500 kbit/s`, and a CAN try reads `NO DATA` after the reply wait where a unit with the node off reads `CAN ERROR` at once. Judge "is somebody on the CAN pins" by what is heard while the chip is quiet.
- **The simulator's ECUs carry the same VIN on every bus.** First contact on another of its buses is the unit's own car on another protocol: the record's protocol moves and the chip is not written. The record's ECU set follows too (`10:183F8003` on the K-line and J1850, `7E8:183F8003` on CAN); the stage ends on the CAN car, so it reads the CAN way afterwards.
- **A detection writes the chip's stored protocol, once a boot.** The detect leg restarts between its three detections, or the second save is put off.
- **Forgetting the unit's own car** leaves the store empty until the next contact. A detect leg cut short puts the car back by a start on the CAN car (the stage's last step does it), with one save of the chip's protocol.
- **A round of the walk with nobody there takes about 11 s** (the CAN tries, then 7.9 s of pre-CAN ones), and the gap between two rounds doubles from 10 s to 80 s. A car that turns up late in a boot is found by the next round, up to 80 s later.
