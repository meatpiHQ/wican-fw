# OBD chip option words: which are RAM, and what the chip keeps

Command: `python tools\testbench\obd\chip_word_class_bench.py [--dut localhost:18081] [--no-reset]` (PC, the DUT through an ssh tunnel `ssh -N -L 18081:<dut>:80 rpi001`)

Needs: main fw on DUT + rpi001 + the `/ws/obd` bridge, AutoPID off. No simulator, no bus (no request is sent). Prints `CHIP WORDS DONE` and a table. About 10 min, two planned restarts (one with a slow bring-up of about 8.5 s and one W line), two writes of the chip's store (the one preset it chooses, and the way back). A measurement: it fails only when an option word is kept by the chip, when something changed that no word explains, or when the unit or the chip's stored protocol is not as found at the end.

The index row and the other stages: [../TESTING.md](../TESTING.md). The tool it reads the chip with: `chip_nvm.py` ([protocol_baseline_bench.md](protocol_baseline_bench.md)).

## What it covers

The protocol work may set per-bus options on the OBD chip: the K-line's baud rate and init address, the J1850 in-frame response, the single-wire CAN transceiver's mode, a CAN bit rate. The rule for it is RAM words only, and only words the bench has seen not survive a reset, because the chip's store is rated for a limited number of writes. This bench is that classification, and it replaces the datasheet's wording wherever the two disagree.

| Step | What is done | What it tells |
|---|---|---|
| per word | the word with a value that is not its default, over `/ws/obd`; then every readable cell (`chip_nvm`'s list: the programmable parameters, the sleep configuration, the user area, the protocol tables, the wake sequences) and three live reads (`ATDPN`, `VTPBRD`, `STPBRR`) against the state before the word | the chip's answer (`OK`, or `?`: not taken here), and what the word changed that can be read |
| the preset | `ATM0` (must be acknowledged), then `STP 53` (MS CAN) as a row of its own, then the two bit-rate words behind it. No request is sent | the bit-rate words answer `?` on a legislated AT protocol and are taken only behind a preset |
| warm start | an app's `ATZ` (the firmware sends it as `ATWS`), the cells and live reads again | a cell that still holds what a word put there survives the warm start, which is the firmware's own reset at every software restart |
| hard reset | the chip's UART moved to 115200 with the volatile `STSBR` through the wire probe, a planned restart: the bring-up's warm start gets no answer, logs `soft reset not confirmed; hardware reset` and pulses the chip's reset pin; the chip comes back at its stored speed. The cells and live reads again | a cell that still holds what a word put there is kept by the chip |
| the way back | `ATTP3` and a warm start (a temporary AT protocol does not outlive one), `ATDPN`; a planned restart | the chip on its protocol as found, the firmware's read of the store as found, `protocol_saves` 0 |

## Records

**2026-11-11 10:38, bench DUT 44bd8d73ca79, chip MIC3624 V2.3.24, store 6, the image with the J2534 VPW change (sha256 `c8efdc03`): `CHIP WORDS DONE`, 20 rows, 8 checks, 0 failed, 2 planned restarts.**

| Bus | Word | Answer | Class |
|---|---|---|---|
| K-line | `ATIB 96`, `ATIIA 13`, `ATKW0`, `ATSW 80`, `ATWM 8133F13E`, `ATTA 13` | `OK` | no readable cell moved |
| J1850 | `ATIFR0`, `ATIFR H` | `OK` | no readable cell moved |
| timing | `ATST 40`, `ATAT2` | `OK` | no readable cell moved |
| single-wire CAN | `ATTM 1`, `ATTM 3`, `STCSWM 1`, `ATSWCL 1`, `VTSWRS 1`, `VTSWSC 1` | `OK` | no readable cell moved |
| bit rate | `STPBR 250000` on the AT protocol 6 (`VTPBR` the same) | `?` | not taken by the chip there |
| protocol preset | `STP 53` behind an acknowledged `ATM0`, no request | `OK` | **kept across the warm start and the hardware reset**: `ATDPN` `53 [ST]`, the bit rate the preset's 125000 |
| bit rate | `STPBR 250000`, `VTPBR 83333` behind the preset | `OK` | RAM: `VTPBRD` and `STPBRR` follow (250000, 83333), and both read the preset's 125000 again after the warm start |

- **"No readable cell moved" is all that can be said about the sixteen option words.** None of them has a read-back on this chip, so the bench cannot show that their value is gone after a reset. What it shows is that none of them changed a programmable parameter (the stored defaults of the K-line baud rate, the wakeup interval, the timeout and the rest are `ATPPS` entries, and `ATPPS` read the same throughout), the protocol tables, the sleep configuration, the user area or a wake sequence.
- **The bit-rate words are RAM and need a preset.** By hand the same day: behind `STP 53`, `VTP 2C3`, `STP 33` and `ATTPB` both words are taken and `VTPBRD` follows; after a warm start the preset's own rate is back (125000, 500000, 500000), and behind `ATTPB` the chip is back on the stored `6`. On a legislated AT protocol (`6`) both answer `?`.
- **A protocol preset is kept by the chip, with the memory function off and no request sent.** `STP 53` still read `53 [ST]` after a warm start, after a true `ATZ` sent through the wire probe, after a sleep word (`STSLEEP0`) and the wake, after the hardware reset pulse above (twice), and after the unit's supply had been off for 4 s (the processor restarted from power-on with its retained state lost; the chip logged `chip awake` and read `53 [ST]`). `ATTP3` and a warm start bring the chip back to the stored AT protocol, which read `6` every time: the AT protocol store was not touched.
- **The preset is STORED: the chip still has it after two minutes without power (2026-11-11 11:13, by hand).** `ATM0` acknowledged, `STP 53`, no request; the unit's supply off for 120 s (the simulator's pin 16 read 0.20 V, the unit answered nothing, and the console adapter's transmit line was held low for the whole time so that it could feed nothing; the unit's USB-C cannot power it); after the power-on the processor reported `poweron` with its retained state lost, and the chip read `ATDPN` `53 [ST]` and 125000. So `ATM0` does not stop it: the memory function governs the save behind an answered request on an AT protocol, and `STP` keeps its protocol by itself. The datasheet's wording (`STP hh` "Set protocol to hh" against `ATSP h` "Set Protocol to h and save it") says otherwise and is wrong for this chip. The first reading of the 4 s cut above ("the chip did not lose power") was mine and was wrong too: it had lost power, and the preset was in its store.
- **`ATTP` does not take a preset number.** `ATTP53`, `ATTP 53`, `ATTPC3`, `ATTP63` and `ATTP33` each answer `?`; the chip stays on `6`.
- **The link words are kept as well.** `ATDPCLK` on this chip as found: `P6` to `PE` on `HS_CAN`, `PF` on `SW_CAN` (the datasheet's examples show `PB` to `PE` on `MS_CAN`). `ATLNKCH P6` answers `OK`, the table reads `P6: CH_CAN`, `ATTP6` and `0100` are answered (`7E8 06 41 00 18 3F 80 03`), and the link is still there after a warm start; put back with `ATLNKHS P6`. Not taken through a power-off; by what the preset did it is to be treated as stored.
- **What is left as a RAM-only route.** An AT user protocol through `ATTP` is RAM (`ATTPB`: `ATDPN` `B`, back on `6` after a warm start), and `VTPBR` behind it sets the rate in RAM. Which pins it talks on is the link table, so MS and CH CAN need their link written ONCE (read first with `ATDPCLK`, written only when it differs, booked, like the PP 0F guard), not at every connect. `PF` (USER5, 11 bit, 33.3 kbit/s) is on the single-wire transceiver as found.
- **What this corrects.** The finding of 2026-11-01 that "an ST or VT switch writes the store too" was right in its conclusion and wrong in its reason: it took the write for the memory function's (an answered request with memory on), and `ATM0` for the cure. And the statement "nothing wrote the chip" of the baseline's MS, CH and single-wire CAN legs ([protocol_baseline_bench.md](protocol_baseline_bench.md)) is WRONG: each of those legs chose a preset. The snapshot could not see it, because it reads the store behind `ATTP3` and a warm start, which is also what takes the preset out again. Counted from the logs, the benches of 2026-11-10 and 2026-11-11 chose a preset about thirty times (the two baseline runs, the probes, this bench), none of them booked.
- **What it means for the firmware today.** After a session on a preset (an app, a profile row with `STP53;` as its init) the chip stays on that preset across every restart of the unit: the bring-up's read of the stored protocol finds `53 [ST]`, and requests go out on that bus until an AT protocol is chosen. The way back is `ATTPn` and a reset.
- Earlier the same day, the first form of the bench cut the unit's supply for 4 s as its last step and failed: `ATDPN` `53 [ST]` and `VT POWERMANAGE` `SLEEP: PWR EN | WAKE: UART` after it. That is how the stored preset was first seen. `VT POWERMANAGE` (how the chip last went to sleep and woke) is a status, not a setting, and came off `chip_nvm`'s list.

## Traps

- **A supply cut IS a power-off of the chip** (the unit restarts from power-on, its restart history starts again at 1, and the chip starts again too: `VT POWERMANAGE` then reads `SLEEP: PWR EN | WAKE: UART`). The bench's provoked reset pulse is the cheaper step and shows the same for the preset; what a pulse cannot show is that a kept value is stored, and for the preset the power-off above did.
- **`ATTPB` on this chip talks on the HS CAN pins at 125 kbit/s** (the link table as found). On the bench's 500 kbit/s bus the request ended in `CAN ERROR` and the unit's own CAN node counted 128 receive errors. Read `ATDPCLK` before sending anything behind a user protocol.
- **The wire probe takes one word without a space**: `STSBR115200`, not `STSBR 115200` (HTTP 400).
- **The provoked reset costs a slow bring-up**: 8.5 s instead of 1.2 s, with `W obd_chip: soft reset not confirmed; hardware reset` in the log. No E line. `STSBR` is volatile (the speed work of 2026-11-07): the chip is at its stored 2 Mbaud again after the pulse.
- **The chip is left on a preset between the steps.** The bench puts it back (`ATTP3`, a warm start) and restarts the unit so that the firmware's read of the store is right; a run killed in between leaves the chip on `53 [ST]`, and the same two words by hand put it back.
- A sleep word through the wire probe does not keep the chip asleep while the unit is awake: the unit holds the chip's power-enable pin, and the chip wakes at once (`SLEEP: CMD | WAKE: PWR EN`). The bring-up then finds it awake and sends no reset pulse.
