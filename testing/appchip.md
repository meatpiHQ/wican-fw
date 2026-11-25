# appchip: an app on the OBD chip beside AutoPID

Command: `.\test.ps1 appchip [-SkipCap]` (PC; it opens the tunnels and runs `python tools\testbench\obd\app_chip_bench.py [--dut localhost:18081] [--elm localhost:18350] [--skip-cap] [--rounds 6]`)

Verdict line: `APP CHIP PASS` (52 checks; 49 with `-SkipCap`). About 6 minutes, 3.5 with `-SkipCap`. No restart (two when Automate was off at the start), no chip write, no console, no PCAN.

Needs: the main firmware on the DUT on the bench AP, TCP 35000 and `/ws/obd` bridged to the chip (the default connections), the ECU simulator answering 7E0 (on 7E8) and 7E2 (on 7EA), the unit's own CAN controller on the bus (it is the monitors' traffic).

GitHub issue #1078, meatpi-components pull request #14 (Jeff Munk) and what was built on top of it on 2026-11-25.

## What it covers

An app on a bridge and AutoPID share the one OBD chip. The stage checks how the chip changes hands:

| Leg | What is checked | What was wrong before |
|---|---|---|
| L0 preflight | the tables and the settings saved, the bench's table in (eight rows that set a header and a receive filter, asked back to back: the chip is never idle), a burst of marked frames goes out | - |
| L1 first | six apps come one after the other while the poller polls; each one's first command (`ATI`) is answered by the chip, the poller loses no poll to it, and the driver's counters say so (`client.turn_waits` grew, `client.turn_forced` did not) | the command landed in the poller's request: `STOPPED`, nothing run |
| L2 reset | `ATZ`, `ATD`, `ATZ` as an app's first command: the banner (or `OK`), every marked frame in the monitor behind it, the car's protocol, `0100` answered by 7E8 alone | the reset was lost, so the monitor showed the one id of the poller's receive filter and `0100` was answered by the ECU the poller's last row had addressed |
| L3 hold | 25 s of `ATMA`: no `STOPPED`, the marked frames to the end, `stats.paused_client` true and `client_idle_ms` 0 in every sample, 409 from `POST /api/can/send` with `"bus": "obd"` and from `POST /api/autopid/test`; the app's next byte stops the monitor; the poller is back about 10 s later | the monitor was cut 10 s in, then the poller's answers on the app's stream |
| L4 gone | the app closes its connection 5 s into a monitor, once on TCP 35000 and once on `/ws/obd`: the poller is back about 10 s after the app's last byte, and `client.gone` counted the hold | with the hold alone, the poller stayed off until the cap of two minutes |
| L5 quiet | an app sends `ATI` and then stays connected and silent for 18 s while the poller polls again: it receives nothing; `subscribers[].withheld` on `/api/obd_chip` grew; its next command (`ATRV`) is answered and costs the poller no poll | the app received the poller's answers |
| L6 the cap | `ATMA` left alone: the poller still off after a minute, `STOPPED` on the app's stream at two minutes, the poller polls again | (the hold's own limit) |
| L7 restore | the tables and the settings back, the poller polls them, no E line, no fault, no unexpected restart | - |

The last check of the run counts every session's first command (14 sessions): all answered by the chip.

How an answer is judged as the chip's reply to a command: one prompt, and before it the reply alone (`ELM327 v<n>.<n>` for `ATI` and `ATZ`, `OK` for `ATD`), with the command's echo taken off. A word of the poller's on the app's stream is so not taken for the app's answer.

The marked frames: six frames from the unit's own CAN controller (`POST /api/can/send`, `"bus": "can"`), ids 7E8, 7EA, 321, 123, 18DAF110 and 1ABCDE01, data `03 DE AD <n> 00 00 00 00`. A monitor's text says which of them the chip showed, so a receive filter left on the chip shows as one id of six.

## Records

All on the bench unit 14c19f457fd9, 2026-11-25, through the runner (`.\test.ps1 appchip -DeviceId 14c19f457fd9`). The four builds differ in the components tree alone; an image is named by its sha256, the build string is the same for all.

- **Red: the build before** (6.0.0-beta.15 with components `main` 25594db, image `a5e9aad3`), 23:20: FAIL, 20 of 49 checks (`test-reports/2026-11-25_2320_appchip.md`).
  - first: 5 of 6 first commands answered `STOPPED`; 13 of the run's 14 sessions lost theirs.
  - reset: `ATZ`, `ATD`, `ATZ` all answered `STOPPED`; the monitors behind them showed `{"7E8": 3}`, `{"7E8": 3}`, `{"7EA": 3}` of six ids; one `0100` of three was answered `7EA 06 41 00 ..` (the header of the poller's last row).
  - hold: `STOPPED` 10 s in, `paused_client` `[True, True, True, False, False, False]`, `client_idle_ms` up to 22341, can.send through the chip and the test route both 200.
  - quiet: 7,151 bytes in 18 s on the silent app's stream. The cap: `STOPPED` at 10.5 s.
- **Pull request 14 alone** (components `main` with his four commits merged, image `c033ac6d`), 23:30: FAIL, 12 of 49 (`2026-11-25_2330_appchip.md`), 568 s.
  - **Green, his part:** L3 whole (25 s of `ATMA`, 708 bytes, no `STOPPED`, six bursts of six ids, `paused_client` true and `client_idle_ms` 0 in all six samples, 409 from can.send through the chip and from the test route with his sentence, the stop byte, the poller back at 10.5 s) and L6 whole (`STOPPED` at 120.7 s).
  - Red, what he had asked about: first (4 of 6 lost) and reset as before; gone on TCP and on `/ws/obd` "not within 30 s of the close" (the poller came back at the two minutes); quiet 7,198 bytes.
- **Ours on top, the first cut** (image `f3fe8e27`), 23:47: FAIL, 5 of 49 (`2026-11-25_2347_appchip.md`). Every first command was run by the chip, the reset cleaned it, gone 5.4 s behind the close on both transports, quiet 0 bytes, the cap at 120.7 s. The five: the answer to every first command had the tail of the poller's last answer in front of it (`\r>ELM327 v2.3`, once `OK\r\r>ELM327 v2.3`). The driver marked an app's command as under way when its bytes arrived, and they wait for the poller's pass before they go out: for that time the poller's answer was shown to the app. Marked now when the bytes go out; host case `test_client_a_write_during_a_turn_sees_it_and_keeps_the_next_one_off`.
- **Green** (image `059395ef`), 23:55: **PASS, 52 of 52**, 351 s (`2026-11-25_2355_appchip.md`).
  - first: 6 of 6, `polls_failed` unchanged, `turn_waits` 0 to 6 with the longest wait 39 ms, `turn_forced` 0.
  - reset: the banner (`\r\rELM327 v2.3\r\r>`) and `OK`, every marked frame three times in each monitor, `ATDPN` 6, `0100` from 7E8 in all three.
  - hold: as with his part alone; the poller back 10.6 s behind the stop byte.
  - gone: TCP 10.8 s behind `ATMA` and 5.5 s behind the close, `/ws/obd` 10.5 s and 5.4 s; `client.gone` 0 to 1 to 2.
  - quiet: 0 bytes in 18 s while `polls_ok` went 377 to 572; `ATRV` answered `13.6V\r\r>` and nothing else; `withheld` 10572 to 16208.
  - the cap: `STOPPED` on the app's stream at 120.7 s. 14 of 14 first commands in the run.
  - no E line, no fault, no unexpected restart; the tables back (29 rows).

The other stages on that image (2026-11-26, 00:11 to 00:31): `multibus` 135 checks (its leg with an ELM app in the middle of the polling), `cansend` 131 (its leg with an app on the chip), `groupdefault` 47, `profilee2e` 40 (the Leaf), `canfilters` 64 (the poller's filter windows, which now end when an app comes). All PASS; `multibus` after its bench learned to wait for the chip (the trap below).

Not on a bench: the Bluetooth jack's `peers` (one line in the glue, `ble_manager_is_connected()`), and the USB serial jack, which has none and keeps the two minutes.

## Traps

- **The simulator answers 7E0, 7E2 and 7E3 only.** A row on 7E1 gets `NO DATA`, and half the bench's rows then fail (the first cut of the table, 2026-11-25).
- **The bench's rows take about 36 ms each and follow each other without a pause** (the group's period of 100 ms is shorter than a round of eight; 83 polls in 3 s). That is wanted here: an app's first command meets a busy chip in nearly every round, and waits for the rest of that row (39 ms at most in the green run). A row with a bare `ATCRA` and no count of answers keeps the chip busy for its whole timeout instead (0.6 s with `ATST96`): an app's first command then waits that long.
- **The burst of L0 goes out while the poller polls**, and its frames on 7E8 and 7EA can be taken for the answer to the poller's request: one failed poll before L1 in the green run (`polls_failed 1 -> 1` from there on). The legs compare the count before and after themselves.
- **The first cut of the judge took a word of the poller's for the app's answer** (`OK` for `ATD`, a banner among garbled lines for `ATZ`): 3 of 14 first commands counted as answered on the build with pull request 14 alone, where 11 were lost and 3 were luck or cross-talk. The judge now wants one prompt and the reply alone.
- **Every session of a leg behind L2 starts with `ATI` and then `ATZ`.** On a build without the handover the `ATI` takes the loss and the legs behind it still measure what they are about.
- **The stage needs the poller to be back before each round** (10 s behind the last app byte), so a round costs at least 11 s.
- **On a build with the hold and without the release on disconnect, L4 waits for the two minutes twice** (the stage then takes about 13 minutes).
- **A bench that uses the chip as an app is an app to every other stage's Test call.** With pull request 14 `POST /api/autopid/test` answers 409 in the ten seconds after an app's last command. `.\test.ps1 multibus` failed on that the day it came in (its chip snapshot 9 s before its first Test call); the benches that do both now wait until no app holds the chip (`tools/testbench/lib/chip_free.py`). A new bench that reads the chip through `/ws/obd`, TCP 35000 or `chip_nvm` and then tests a row does the same.
- **A frame the unit's own CAN controller sends is seen by the OBD chip's monitor**: both sit on pins 6 and 14. The frames need somebody else on the bus to acknowledge them (the simulator).
