# Automate rows on MS, single-wire and CH CAN beside the car's OBD protocol

Command: `.\test.ps1 multibus` (PC; it opens the tunnel and runs `python tools\testbench\obd\autopid_bus_bench.py [--dut localhost:18081] [--only test,speed,poll,guard,park,app]`)

Needs: main fw on DUT + rpi001 + ECU sim v2 with its four CAN buses wired to the DUT's OBD connector, Automate off at the start. Prints `AUTOPID BUS PASS`. About 6 min, two planned restarts of the DUT (Automate on, Automate back), seven restarts of the simulator, no write of the OBD chip. The unit's tables, its Automate settings and the simulator are put back as found.

The index row and the other stages: [../TESTING.md](../TESTING.md). The basis: [chip_can_slot_bench.md](chip_can_slot_bench.md) (the chip reaches these buses through a definition in its RAM), [chip_word_class_bench.md](chip_word_class_bench.md) (a preset is stored by the chip at every selection). The host half: `.\test.ps1 host autopid` (`test_bus.c`, 11 cases, [host.md](host.md)). The page: `node tools\webui_preview\probe_row_bus.mjs` (`ROW BUS PROBE PASS`, 26 checks over the mock).

## What it covers

A row of the Automate tables has a bus: the car's OBD protocol (the default), MS CAN, single-wire CAN or CH CAN, and a speed for a car whose bus does not run at the usual one. The firmware asks the rows of a bus together, takes the OBD chip there through an AT user protocol defined in the chip's RAM, and brings it home as soon as no row of another bus is due. The simulator's four CAN interfaces answer the same OBD requests, so "answered on that bus" is checked by the value (800 rpm) and, where it matters, by the bus being the only one at that speed.

| Leg | What is done | What must hold |
|---|---|---|
| test | `POST /api/autopid/test` with `bus` `ms`, `ch`, `sw`, then without one and with `obd`; then five calls that must be refused | each answered with 800 rpm; the transcript has the slot's definition before its choice (`VTSET_CAN B,81,04,MS_CAN`, `ATTPB`), no `STP`, `VTP`, `ATLNK`, `ATSP` or `VTCFG` word, and behind the request the car's protocol and the definition taken back (`ATTP6`, `VTSET_CAN B`); 400 for an unknown bus, a speed without a bus, a speed the chip cannot make, a row of another bus with `ATSP6` or `STP53` in its init |
| speed | the simulator's MS CAN at 250 and its CH CAN at 125 kbit/s; a test with `bus_kbps`, and one without | with the speed: answered, the divisor of 500 kbit/s in the definition (`02`, `04`); at the bus's usual speed: `CAN ERROR`, no value |
| poll | seven rows (three on the car's protocol, two on MS CAN, one on CH CAN, one on single-wire CAN), a 2 s group, Automate on; six rounds watched | every row has the simulator's value, every bus counts its answers in every round and no failure, the chip changes bus three to five times a round (four = once to each other bus and home; seven rows one by one would be up to seven), and it is found on the car's protocol in at least half of the looks |
| guard | the simulator's HS CAN off for four rounds, then on again | the rows of the car's protocol are not answered, the rows of the three other buses still are, and with the HS bus back the car's rows are answered again |
| park | the simulator's MS CAN at 250 kbit/s while the rows ask at 125 | after three `CAN ERROR` replies in a row the rows on MS CAN rest (`parked`, `parks` +1, one W line that says why), the other buses go on, the rest ends by itself after 30 s, and with the bus back at 125 the rows are answered again |
| app | an ELM app over `/ws/obd` in the middle of the polling: `ATZ`, `ATH1`, `ATDPN`, `0100` | answered on the car's protocol (`6`, `7E8 06 41 00 ..`), the poller off the chip meanwhile (`paused_client`), and ten seconds after the app's last word the rows of all four buses are answered again |
| the chip | `chip_nvm`'s snapshot before and after; the chip read with Automate off | the 40 stored cells the same, `protocol_saves` 0, no refused switch, the stored protocol as found, `ATDPN` and the link table as found |
| the unit | at the end | the tables, the Automate settings and the simulator as found, no E line in the log, the boot count moved by the two planned restarts, no unexpected reset |

## Records

**2026-11-11 14:31 to 14:36, bench DUT 44bd8d73ca79, chip MIC3624 V2.3.24, store 6, image sha256 `fd6ff0cb` (ELF `50bbfd970fa5236f`, the trees of that day), ECU sim v2 v1.00-14: `AUTOPID BUS PASS`, 71 checks, 0 failed, 2 planned restarts (`.\test.ps1 multibus`, 279 s).**

- **Every row answered on its bus**: 800 rpm on the car's protocol, on MS CAN, on CH CAN and on single-wire CAN; over six rounds 18, 12, 6 and 6 answers and no failure.
- **24 changes of bus in six rounds of seven rows: 4.0 a round** (to MS CAN, to CH CAN, to single-wire CAN, home). The first form of the build went home whenever no row of another bus was due at that very moment and measured 5.7: the rows of the three buses had drifted apart by what the rows before them took, so each bus was its own trip. Now a row that is nearly due (a quarter of its period, 500 ms at most) is asked in the trip that is under way, once, and the rows of the other buses are due together from then on.
- **The chip was found on the car's protocol in 29 of 29 looks** (one every 0.35 s over six rounds): a trip over the three buses takes a fraction of the 2 s round.
- **Another speed**: MS CAN at 250 kbit/s (`VTSET_CAN B,81,02,MS_CAN`) and CH CAN at 125 kbit/s (`VTSET_CAN C,81,04,CH_CAN`) answered; at the usual speed both ended in `CAN ERROR`.
- **With the HS bus off** the three rows of the car's protocol failed and the rows of the other buses went on (8, 4 and 4 answers in four rounds); `paused_bus` stayed false (a silent HS bus is no verdict against it).
- **The rest**: `parked` 1 s after the simulator came back at 250 kbit/s (the three `CAN ERROR` replies had come while the simulator was restarting: nobody acknowledged then either), no answer on MS CAN while resting, 12, 4 and 4 answers on the other buses; the rest ended 31 s after it began and the rows were answered again.
- **Nothing written**: 40 cells the same, `protocol_saves` 0, stored protocol 6.

Regression on the same image: `RELEASE TEST PASS` (59 checks), `PROFILE PROTOCOL PASS` (88 checks: rows on `ATSP6` and `ATSP7`, the path this change re-arranged), `.\test.ps1 host autopid` PASS (163 tests).

Found while building it:

- **The poller sent the baseline into a test's request.** A test of a row on another bus takes the chip there from the web server's task while the poller is paused. The poller's own way home looked at where the chip was, found it on MS CAN with Automate off, and took it home in the middle of the test (three changes of bus per test instead of two, and the next test found the chip's state changed behind it). The poller now takes home only a chip it left on another bus itself.
- **A definition stays in the chip's RAM until a reset.** Home on the car's protocol the slot was still defined, so the chip's protocol table read differently from before (the snapshot's `VT AT_PROTOCOL_ALL`). The one-argument `VTSET_CAN <slot>` takes it back; the firmware sends it for every slot it defined once the chip is home.

## Traps

- **A request with a reply count (`010C1`) keeps a round short.** Without it the chip waits out its timeout (`ATST96`: about 600 ms) on a functional request, on every bus; seven such rows are a 4 s round.
- **The Test route answers 200 with `ok` true and `raw` `CAN ERROR`** when nobody acknowledges: the exchange with the chip worked. Judge by the value or the reply bytes.
- **`CAN ERROR` while the simulator restarts counts too**: the park leg's three replies came during the restart that changed the simulator's speed.
- **A row backs off after three failures** (its period times four): after the park the bench waits four periods before it looks for answers again.
- **ECU sim v2's single-wire transceiver sleeps by default** and goes back to sleep at every restart of the simulator: the bench wakes it after each one.
- **The bench's first snapshot wants the chip as a reset leaves it.** A definition left in the chip's RAM by a probe by hand made the two snapshots differ on `VT AT_PROTOCOL_ALL` (the first run of 14:12, with the build that did not yet take its definitions back).
