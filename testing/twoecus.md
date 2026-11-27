# A request two ECUs answer

Command: `.\test.ps1 twoecus` (PC; it opens the tunnel and runs `python tools\testbench\obd\autopid_two_ecus_bench.py [--dut localhost:18081] [--pcan PCAN_USBBUS1] [--shots 8] [--watch 40]`)

Needs: PC + PCAN_USBBUS1 on the DUT bus + ECU sim v2 on the bus (the ACK source, and the standard rows' ECU); main fw on the DUT on the bench AP, the PSU above the sleep voltage. Prints `AUTOPID TWO ECUS PASS`, 28 checks. About 2.5 min, no restart, no setting changed, no write of the OBD chip. One row is added to the unit's table for the poll leg and taken out; the simulator is told to stay silent for one identifier and told to answer it again.

The index row and the other stages: [../TESTING.md](../TESTING.md). The fix: `autopid_resp.c` (`ap_resp_row_frames`), the `autopid` README, "Which ECU a row reads when several answer one request"; `components/HTTP_API.md`, the note of 2026-11-27 under `/api/autopid/test`. The host half: `.\test.ps1 host autopid` (`test_frames.c`, 2 cases, [host.md](host.md)).

## What it covers

The report (Discord, 2026-11-24, a 6.0.0 beta; the car and its table were not known): an odometer that reads FFFFFF = 16,777,215 km about half of the time, right the other half, sometimes changing with a restart of the device and sometimes not, and never wrong on firmware 4.5x.

What was found. A row that sets no request header of its own (no `ATSH` in its init) goes out on the functional id, 7DF, or 18DB33F1 on a 29-bit protocol, and every ECU that knows the identifier answers. The published Renault Zoe and Kangoo profiles and the Jaguar I-Pace one ask their odometer this way. Firmware 4.52p evaluated a custom or vehicle row over every line of the reply in arrival order, so the answer that came first was the one decoded. v6 read the ECU with the lowest id for every row. An ECU with a lower id that answers behind the real one therefore became the row's value on v6 and never was on 4.x, and `FF FF FF` is what an ECU sends for a mileage it does not hold.

The stage plays two ECUs on the PCAN for one identifier, `22 2006` (the Renault odometer, decoded `[B4:B6]`): A on 7EC holds 123456 km, B on 7EA is the other one (`actors/pcan_two_ecus.py`: each ECU's data or its refusal, who answers first, the gap between them, 11-bit or 29-bit ids).

| Leg | What is played | What must be read |
|---|---|---|
| L0 | nothing: the simulator silenced for the identifier | `NO DATA`: only the played ECUs answer from here on |
| L1 | A first, B `FF FF FF` 15 ms later | 123456 by a vehicle row and by a custom row, both ECUs' lines in the chip's reply |
| L2 | B refuses (`7F 22 31`), behind A and ahead of it | 123456 both times |
| L3 | B first with a mileage of its own (43981), then A first | 43981, then 123456: the order decides, not the id |
| L4 | A first, B behind it, the row typed `std` | 43981: a standard row keeps the lowest id |
| L5 | 29-bit ids: the request on 18DB33F1, A on 18DAF1DA, B on 18DAF10A with `FF FF FF` behind it | 123456 |
| L6 | A first, B `FF FF FF` behind it, the row in the unit's table | every value the poller publishes is 123456, the row is re-read, both ECUs answered every request |
| L7 | restore | the table as found, the simulator answering again, `0100` answered by the simulator |

Every leg but L6 goes through `POST /api/autopid/test` with the row's type, which is the poller's own path. L6 is the poller.

## Records

**2026-11-27 01:26, bench DUT 14c19f457fd9, `v6.0.0p-beta.15-9-g0090b4e` (before the fix): `AUTOPID TWO ECUS FAIL`, 9 of 28.** L1: 16777215, 8 shots of 8, for the vehicle row and for the custom row, with both lines in the reply (`7EC 06 62 20 06 01 E2 40`, `7EA 06 62 20 06 FF FF FF`). L2: no value, 8 of 8, with the refusal behind the answer and ahead of it. L3 with A first: 43981 (B's). L5: 16777215 with 29-bit ids. L6: the poller published 16777215, 4 readings of 4. L4 and L3 with B first passed, as they must on either firmware. (Two of the nine were the poll leg's own floor, set too high for a 30-row table: see the traps.)

**2026-11-27 01:29, the same unit, image sha256 `3a00ecf2` (`v6.0.0p-beta.15-14-g5a2db8e` with the fix): `AUTOPID TWO ECUS PASS`, 28 checks**, by hand through the tunnel. **01:32 through the runner: PASS, 28 checks, 130 s** (`test-reports/2026-11-27_0132_twoecus.md`). L1 123456 8 of 8 for both row types, L2 123456 with the refusal on either side, L3 43981 and 123456, L4 43981, L5 123456, L6 six readings in 40 s, all 123456, the played ECUs asked 5 times in the watch and both answered 5 times. The table back at 29 rows, `0100` answered `7E8 06 41 00 18 3F 80 03`.

**2026-11-27 02:05, the same unit, the final image sha256 `5eb2daa3` (one line of the test route and a comment later): `AUTOPID TWO ECUS PASS`, 28 checks, 130 s** (`test-reports/2026-11-27_0205_twoecus.md`): five readings in the poll leg's 40 s, all 123456, the played ECUs asked 4 times in the watch and both answered 4 times.

**The other stages on the fixed build (2026-11-27), every row goes through the changed path:** `profilee2e` 40 checks, no failed poll (01:35 on `3a00ecf2`, 02:07 on `5eb2daa3`); `multibus` 169 checks (01:45 and 01:56, one on each image); `kline` 58 checks (01:39, on `3a00ecf2`: the pre-CAN reply's one view); `host autopid` 206 tests on the final sources (01:55). The unit afterwards: its 44 settings groups and 29 rows as found, 0 E lines, 0 faults, 0 unexpected resets.

**The legacy firmware's own code on the same text (2026-11-27).** `parse_elm327_response()` and `evaluate_expression()` copied verbatim from the tag `v4.52p` and compiled on the host: `7EC 06 62 20 06 01 E2 40` then `7EA 06 62 20 06 FF FF FF` gives `response.data` = `06 62 20 06 01 E2 40 06 62 20 06 FF FF FF` and `[B4:B6]` = 123456; the two lines the other way round give 16777215; a refusal behind the answer gives 123456.

**How late may the second ECU answer and still be in the reply (2026-11-27, before the fix).** B's answer 15, 30, 45, 60, 90, 130, 200 and 400 ms behind A's, 12 shots each: in the reply 12 of 12 at every delay. The baseline sets `ATST96` (600 ms), and the chip waits that long for more answers on some shots (about 630 ms a shot) and returns early on others (80 ms with B 15 ms behind), with B's line in both. So whether a second ECU shows in the reply is not a matter of chip timing inside 400 ms: it shows when it answers.

**2026-11-27 12:04, the same unit, the image sha256 `8d73381c` (this fix and the detection change of the same day, GitHub issue #1022, in one build): `AUTOPID TWO ECUS PASS`, 28 checks, 129 s.** Two runs before it on that image were red for the bench's state, not for the firmware (the two traps added below): 11:38, started 12 s behind `kline` (the preflight's `no app or diagnostic hold on the chip`), and 12:02 with Automate off (24 of 28, the four checks of the poll leg).

## Traps

- **The stage wants the unit as it is in use: Automate on.** Its poll leg watches the poller read the row. With Automate off (as `kline`, `precan` and `multibus` want it) the Test route's legs pass and the poll leg reads `0 readings seen in 40 s`.
- **Not right behind a stage that ends with the chip's stored cells read** (`kline`, `precan`: `chip_nvm`'s snapshot is an app session, and the firmware takes the chip back ten seconds behind its last word). Started inside those seconds the preflight says `no app or diagnostic hold on the chip` and the stage is over in 4 s.

- **Two actors on one PCAN channel.** A second actor started while the first still runs cannot open the channel, and the device reads `NO DATA` for every shot. The bench stops one actor before it starts the next and waits 0.8 s.
- **The chip's receive filter on a functional request.** With the header 7DF the chip prints answers from 7E8 to 7EF only. A played ECU on 763 answered every request and never showed in a reply. The second ECU of this stage sits on 7EA for that reason.
- **The simulator answers the identifier too.** It refuses a `22` request it does not know with `7F 22 31` on 7E8, on the functional id as well. Left on, it is a third ECU with the lowest id in every leg. The bench silences it for the identifier (`POST /api/ecu/errors/22/2006`, `no_response`) and removes the entry at the end; L0 proves the silence with a shot that must read `NO DATA`.
- **The simulator keeps the entry, switched off.** After the `DELETE` its error list still shows `22_2006` with `"enabled": false` until its next restart. It answers the identifier again (the bench's last leg does not ask it, `0100` is its check).
- **`kline` and `multibus` want Automate off at their start.** On a unit that has it on they end in 4 s with `FAIL: Automate is off at the start`: a precondition, not a result. Switch the autopid `enabled` setting off (a submit and a restart), run them, and put it back.
- **The poll leg's row comes round with the whole table.** Its period is 1 s, but beside the 29 rows of the bench unit it is read about every 8 s. The watch is 40 s and asks for three readings.
- **A row before it must not lend it a header.** The legs' row names the functional header and clears the receive filter in its own init (`ATSP6;ATSH7DF;ATCRA;`), because a row with no init takes what the row before it left on the chip, as on the legacy firmware.
