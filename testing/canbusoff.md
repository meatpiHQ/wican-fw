# The CAN transmitter after bus-off, again and again

Command: `.\test.ps1 canbusoff` (= `python tools\testbench\can\can_busoff_slots_bench.py --dut localhost:18081 --pcan <the PCAN channel on the DUT's bus> [--rounds 18]`; the stage opens the tunnel through rpi001 for the web API and the console over `/ws/cli`).

Needs: PCAN on the DUT's bus (`WICAN_PCAN_DUT`) + rpi001. The unit's CAN interface must be running at 500 kbit/s and allowed to talk. ECU sim v2 stays on the bus (it acknowledges) and is not touched. Prints `CAN BUSOFF SLOTS PASS`, 8 checks, about 40 s (18 rounds), no settings changed, no restart.

The index row and the other stages: [../TESTING.md](../TESTING.md). The first bus-off bench (a storm at the wrong bit rate, the recovery and its backoff): leg `canrec` of [live.md](live.md), `tools/testbench/can/can_recovery_bench.py`.

## What it answers

Whether the unit can still transmit after many bus-offs, whether every frame it took is accounted for, and whether it stays up.

Two defects were found with it on 2026-11-18:

1. **A frame that is inside the controller when the controller goes bus-off was forgotten.** The CAN driver keeps a frame in one of 16 slots until the interrupt that ends its attempts. The frame in the controller at a bus-off gets no such interrupt (the IDF node driver sends what its queue holds after the recovery and drops the frame it had handed to the hardware without a word), so its slot stayed taken until the node was next restarted, and the frame was in `tx` and in neither `tx_done` nor `tx_lost`. Sixteen bus-offs without a restart of the node in between (a line fault that gives only bit errors: a short, a stuck bus) and the unit transmits nothing until its CAN interface is restarted. The slot is given back at the bus-off now and the frame counted in `tx_lost`; frames in flight when the node is restarted are counted there too, so at rest `tx` = `tx_done` + `tx_lost`.
2. **The unit was reset by its interrupt watchdog.** The link policy takes a talking node off a bus it cannot read and starts it again (`link_demotions`). The IDF node driver sets an event bit from its interrupt each time its transmit queue runs empty, which FreeRTOS hands to the timer task with the event group's handle; deleting the node frees that event group. A node deleted right behind a transmission left the timer task setting a bit in freed memory, where it waited with interrupts off for a lock that was no longer one. The unit now waits for the timer task to run what it holds before it deletes a node.

## How a real bus-off is made

The PCAN keeps the bus full of id 0x000 frames with eight 00 bytes while the unit sends id 0x000 with eight FF bytes (`can send 000 FFFFFFFFFFFFFFFF` on the console). Both start together and tie in the arbitration; at the first data bit the unit sends recessive, reads dominant and raises a bit error: +8 on its transmit error counter each time, bus-off at 256, after 32 attempts (8 frames, each tried 4 times). Between two of the unit's frames the PCAN's frames go through, so the unit keeps reading good frames. Even so the link policy restarts the node in about seven rounds of ten: while the unit is error-passive, the PCAN's frame runs on through the unit's error delimiter, which the controller reports as form errors.

## What it covers

| Check | What is asserted |
|---|---|
| `B1 the unit transmits and the PCAN is on its bus` | one frame from the console, read on the PCAN byte for byte |
| `B2 no frame in flight before the first bus-off` | `tx` = `tx_done` + `tx_lost` on `/api/can` |
| `O0 the unit answered through every round` | printed only when it did not: the console or the web API stopped answering, or `boot_count` moved; the bench then prints the crash on record |
| `O1 every round ended in a real bus-off` | `bus_off` on `/api/can` moved in each of the `--rounds` rounds |
| `O2 the controller came back by itself each time` | `state` is `running` again without a restart of the unit |
| `O3 after every bus-off each frame is sent or counted lost` | `tx` minus `tx_done` minus `tx_lost` is 0 after every round, read once the counters stopped moving |
| `T1 all 20 frames reach the bus, in order` | 20 frames written one by one after the last round, read on the PCAN |
| `E1 one E line for each bus-off and no other` | the firmware reports a bus-off at E on purpose; `health.log_errors` moved by exactly the number of bus-offs |
| `R1 only the planned restarts, no reset of its own` | `boot_count` and `unexpected_resets` as at the start |

## What it does not cover

Sixteen bus-offs in a row without a restart of the node, the case in which the old firmware stopped transmitting for good: the bench's way of making a bus-off lets the link policy restart the node in most rounds, and a restart gave the slots back on the old firmware too. What the bench shows of the first defect is the accounting (one frame unaccounted with every bus-off); the slot table itself is proven by the host suite (`.\test.ps1 host can_manager`, `test_tx_slots.c`: 64 bus-offs with a frame in the controller cost no slot).

## Records

**2026-11-18 12:12, bench unit 14c19f457fd9, the firmware before the fixes (image elf `7bb648422357a1e2`), PCAN channel 2: no verdict, the unit reset in round 16.**

| Round | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| frames unaccounted (`tx` less `tx_done` and `tx_lost`) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
| node restarts by the link policy so far | 1 | 2 | 2 | 3 | 3 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 9 | 10 | 11 |

After round 15: `tx` 80 = `tx_done` 1 + `tx_lost` 64 + 15 unaccounted. In round 16 the console stopped answering and the unit came back with `boot_count` +1. Its crash report (`GET /api/restart/report`, stored 01:12:05 UTC): `interrupt watchdog at 0x403896ab, core 0, task "Tmr Svc", up 40 s`, the backtrace `spinlock_acquire` < `xEventGroupSetBits` < `vEventGroupSetBitsCallback` < `prvProcessReceivedCommands` < `prvTimerTask`, the other core idle.

**2026-11-18, the same unit, the builds with the fixes: `CAN BUSOFF SLOTS PASS` each time.**

| Run | Image elf | Bus-offs | Node restarts by the link policy | Frames unaccounted after any round | 20 frames after the last | Reset of the unit |
|---|---|---|---|---|---|---|
| 13:07 | `01aafe2f17843f60` | 18 of 18 | 10 | 0 | 20 on the bus | none |
| 13:52 | `bafe0402df0c7399` | 40 of 40 | 10 | 0 | 20 on the bus | none |
| 14:05, the final build | `2d790852dd98d8ae` | 40 of 40 | 21 | 0 | 20 on the bus | none |

98 bus-offs and 41 restarts of the node without a reset, where the firmware before the fix reset at its eleventh. In the run of 13:52 the node was restarted only ten times in forty bus-offs, so the slots lived through long rows of bus-offs, and the transmitter was whole at the end. After the last round of the final build: `tx` 201 = `tx_done` 1 + `tx_lost` 200.

## Traps

- `WICAN_PCAN_DUT` must name the PCAN channel on the DUT's bus; `B1` fails first otherwise.
- `bus.send()` of python-can returns when the adapter's driver has the frame: the PCAN's count of frames is what it handed over, not what was on the wire. The bench does not use it.
- The counters are read once they stopped moving (`tx`, `tx_done`, `tx_lost` equal in two reads a quarter of a second apart): read at once after the storm, the frames still in the driver's queue look unaccounted.
- The firmware logs each bus-off at E (`BUS-OFF #n -- initiating recovery`). A stage that asserts `log_errors` unchanged must not run in the same boot behind this one without taking its own baseline.
- On a firmware before the fix the bench ends by restarting the unit (a planned restart) so it leaves a unit that can transmit.
