# MS, CH and single-wire CAN through the OBD chip without a write of its store

Command: `python tools\testbench\obd\chip_can_slot_bench.py [--dut localhost:18081] [--rounds 5]` (PC, the DUT through an ssh tunnel `ssh -N -L 18081:<dut>:80 rpi001`, ECU sim v2 at its own address)

Needs: main fw on DUT + rpi001 + ECU sim v2 with its four CAN buses wired to the DUT's OBD connector + the `/ws/obd` bridge, AutoPID off. Prints `CHIP CAN SLOTS PASS`. About 3 min, six restarts of the simulator, at most one planned restart of the DUT, no write of the OBD chip.

The index row and the other stages: [../TESTING.md](../TESTING.md). The siblings: [chip_word_class_bench.md](chip_word_class_bench.md) (which words of the chip are RAM, and the finding that a preset is stored), [protocol_baseline_bench.md](protocol_baseline_bench.md) (every bus on an image, and `chip_nvm.py`).

## What it covers

A car with parameters on two CAN buses changes bus at every poll round. The OBD chip reaches the buses beside HS CAN in three ways, and the round can pay for only one of them:

| Way | Words | What it costs |
|---|---|---|
| a preset | `STP nn`, `VTP 2Cn` | STORED by the chip at every selection, with `ATM0` acknowledged or not ([chip_word_class_bench.md](chip_word_class_bench.md)) |
| a link | `ATLNKHS Ph`, `ATLNKMS Ph`, `ATLNKCH Ph`, `ATLNKSW Ph` | STORED: kept through a power-off (the records below) |
| a definition | `VTSET_CAN <slot>,<option>,<divisor>,<transceiver>[,<mode>]`, then `ATTP<slot>` | nothing: the id length and format, the bit rate and the transceiver of one of the AT user protocols B to F, in the chip's RAM, gone with a reset |

`<option>` is the byte of the programmable parameter 2C (`81` 11 bit ids with ISO 15765-4 formatting, `01` the same with 29 bit ids), `<divisor>` is 500 kbit/s divided by it (`01` 500, `04` 125, `0F` 33.3), `<transceiver>` is `HS_CAN`, `MS_CAN`, `CH_CAN` or `SW_CAN`, `<mode>` is the single-wire transceiver's (`3` normal).

| Step | What is done | What must hold |
|---|---|---|
| one bus, four times | ECU sim v2 in a one-bus state (MS CAN 11 bit, MS CAN 29 bit, CH CAN, single-wire CAN). `ATM0`, the definition, `ATDPCLK`, `ATTP<slot>`, `ATDP`; the simulator's bus waited for; `0100`; then `ATTP6` and `0100`; a warm start | `ATM0` and the definition acknowledged, the link table shows the slot on that transceiver, the chip names the protocol right, the request is answered with the right id, the same request on HS CAN is not (so the answer came over that transceiver), and after the warm start the link table, the protocol table and the protocol read as found |
| rotation | every bus of the simulator live: the three definitions once, then HS (`ATTP6`), MS (`ATTPB`), CH (`ATTPC`) and single-wire CAN (`ATTPF`) in turn, `0100` and `010C` on each, `--rounds` times | every switch taken, every request answered. The time of a switch, of the first request behind it and of a later one are printed as FACT lines |
| lifetime | `ATD` and `ATPC`, then `VTSET_CAN B`, then a warm start | the definitions are still there behind `ATD` and `ATPC`, the one-argument form puts that slot alone back, a warm start clears all of them |
| the chip | `chip_nvm`'s snapshot before and after (every readable stored cell, the link table among them) | the same; the stored protocol and the firmware's count of protocol saves as found |
| as found | the simulator's settings and its single-wire transceiver's mode; the unit's own CAN node | as found (one planned restart of the unit when its own CAN node is not: see the traps) |

## Records

**2026-11-11 12:23 to 12:25, bench DUT 44bd8d73ca79, chip MIC3624 V2.3.24, store 6, image sha256 `c8efdc03`: `CHIP CAN SLOTS PASS`, 4 one-bus legs, 5 rounds of 4 buses, 51 checks, 0 failed, 0 planned restarts. Again at 12:27: the same.** Logs `test-reports/logs/2026-11-11_chip_can_slots/`.

| Bus (the simulator alone on it) | Slot and definition | The chip names it | `0100` |
|---|---|---|---|
| MS CAN, 11 bit, 125 kbit/s | B: `VTSET_CAN B,81,04,MS_CAN` | `ISO 15765-4 (CAN 11/125)` | `7E8 06 41 00 18 3F 80 03` |
| MS CAN, 29 bit, 125 kbit/s | B: `VTSET_CAN B,01,04,MS_CAN` | `ISO 15765-4 (CAN 29/125)` | `18 DA F1 10 06 41 00 18 3F 80 03` |
| CH CAN, 11 bit, 500 kbit/s | C: `VTSET_CAN C,81,01,CH_CAN` | `ISO 15765-4 (CAN 11/500)` | `7E8 06 41 00 18 3F 80 03` |
| single-wire CAN, 11 bit, 33.3 kbit/s | F: `VTSET_CAN F,81,0F,SW_CAN,3` | `ISO 15765-4 (CAN 11/33)` | `7E8 06 41 00 18 3F 80 03` |

- **The rotation: 20 switches, 40 of 40 requests answered.** A switch is one `ATTP` word, 49 to 51 ms through the tunnel, which is what any word takes there. The first request behind a switch took 152 to 168 ms and a later one 153 to 163 ms on every bus: a switch costs its one word and nothing after it. (The 150 ms of a request are the chip's wait for more answers to a functional request, the same on HS CAN.) The three definitions together: 160 ms.
- **The definitions are RAM.** Behind a warm start slot B reads `HS_CAN` and `USER1 (CAN 11/125)` again, as found. The stored link is not touched by a definition. The snapshot read the same before and after (40 cells), `protocol_saves` 0.
- By hand the same morning, before the bench existed: eight rounds, 64 of 64; 29 bit ids on single-wire CAN (`VTSET_CAN F,01,0F,SW_CAN,3`: `18 DA F1 10 06 41 00 18 3F 80 03`); a true `ATZ` through the wire probe clears a definition as the warm start does.
- **Where the frames go, seen from outside.** A listen-only CAN adapter on the HS bus and a second one on the CH bus through a rotation: the HS request and its answer on the HS bus only, `7DF 02 01 00` and `7E8 06 41 00 ..` of the CH turn on the CH bus only, 11 bit ids, nothing of the MS or single-wire turns on either, no error frame on either bus.
- **A link word is stored.** `ATLNKMS PB` behind an acknowledged `ATM0`: the link table reads `PB: MS_CAN` after a warm start, after a true `ATZ`, after the bring-up's reset pulse (`soft reset not confirmed; hardware reset`) and after the unit's supply had been off for 60 s (the supply pin at 0.20 V, the processor back from `poweron`). Put back with `ATLNKHS PB`, the table as found. Two writes of the chip's store, booked here; the bench does not repeat them.
- **What this is for.** The firmware does not use any of it yet. It is the basis for parameters on MS, CH and single-wire CAN: the slots are defined again behind every reset of the chip, a bus is chosen with `ATTP`, and no preset or link word is ever sent.

Not covered: other bit rates than the three, 29 bit ids on CH CAN, J1939 formatting on a slot, a switch while an answer is under way, the header and filter words across a switch.

## Traps

- **Define a slot before choosing it.** `VTSET_CAN F,..` sent while the chip is on protocol F: the requests behind it read `NO DATA`. The datasheet says so ("before switching protocols").
- **The `XX` form is not taken**: `VTSET_CAN F,81,XX,XX,3` answers `?`. Give every argument.
- **Slot F as found is single-wire CAN without ISO 15765 formatting** (`USER5 (CAN 11/33)`): `0100` goes out as two raw bytes and the simulator answers `03 7F 00 11 ..`. The option byte `81` of the definition is what makes it OBD.
- **The single-wire transceiver's mode outlives a warm start** (a pin of the chip, not a stored cell): a request on slot F as found is on the wire only when an earlier definition or `ATTM 3` left the transceiver in its normal mode.
- **`ATD` turns the echo on again**: `ATE0` behind it.
- **ECU sim v2 behind a restart: an interface may answer late.** 8.1 s on CH CAN in the record above. In the eight runs before the bench waited for it, the single-wire leg's one request went unanswered eight times, the CH leg's three times, and the first three or four rounds of the rotation went unanswered on CH and single-wire CAN every time (24 or 28 of 40 answered), with the buses answering from about 9 s after the simulator's restart. The request is on the bus (a listen-only adapter read `7DF 02 01 00`, 11 bit, acknowledged), no `7E8` follows, the simulator's own request counter does not move, and its status says `running`. HS and MS CAN answered at once every time. So the bench asks until the bus answers (`wait_bus`, at most 30 s, the wait in the PASS line) and judges the chip on the requests after it; a request the simulator heard and the chip has no answer to is not waited for.
- **ECU sim v2 in a one-bus state on MS CAN: its answers show on the HS pins as well.** A listen-only adapter on the HS bus read the three `7E8` answers at 125 kbit/s, and counted 82 error frames at 500 kbit/s for the same three requests. The unit's own CAN node sits on the HS pins at 500 kbit/s: it counts receive errors (128) and is demoted to `listening`. With every bus of the simulator live it counted none over 64 requests. The bench restarts the unit once at the end when that node is not as found.
- **The simulator's DB9 relay and its single-wire transceiver's mode go back to their defaults with every restart of the simulator.**
- **Several `sim_only()` calls in a row: keep the settings as found yourself.** Each call's `previous` holds the keys that call changed, so the first one's does not undo what a later call changed (by hand on 2026-11-11 the MS interface stayed off that way).
