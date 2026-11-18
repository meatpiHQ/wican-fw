# The Bluetooth data pipe under overload

Command: `.\test.ps1 bleoverload` (= `python tools\testbench\ble\ble_overload_bench.py --dut-ip <the DUT's address as rpi001 sees it> [--pcan PCAN_USBBUS2] [--legs str] [--t-fps 2000] [--t-secs 12] [--r-writes 60] [--log-level '*=none'] [--btmon]`; the central's half, `ble_overload_pi.py`, is started on rpi001 through `ble_pi_run.py`).

Needs: main firmware on the DUT (`can_manager.enabled`, 500 kbit/s) + rpi001 with its Bluetooth adapter (UB500 + bleak) + the PCAN on the DUT's bus (`WICAN_PCAN_DUT`) + ECU sim v2 reachable (the bench switches its answers and its broadcast off and puts them back). Prints `BLE OVERLOAD PASS`, 13 checks, about 3 min, two planned restarts (the settings for the run, and back as found).

The index row and the other stages: [../TESTING.md](../TESTING.md). slcan over Bluetooth without overload: [ble_slcan_bridge_test.md](ble_slcan_bridge_test.md). Binary records over Bluetooth: [blecanraw.md](blecanraw.md).

## What it covers

The data pipe is the characteristic pair FFF1 (the unit notifies) and FFF2 (the central writes). A connection row of the unit joins it to something else; here it is `can <-slcan-> ble`, slcan text, one line per CAN frame. A phone's link carries about 15 KB a second on this bench, a CAN bus makes more than that as soon as it is busy, so a monitor over Bluetooth runs with the pipe overloaded as a matter of course. The stage asserts what the unit does then: what it cannot deliver it drops in whole lines and counts, and it keeps answering the central, also while it cannot send on the bus itself.

The bus carries the bench's frames alone: AutoPID is off for the run and ECU sim v2 is quiet (it keeps acknowledging). The PC owns the PCAN (the flood, and listening to what the unit sends), the Pi is the central.

| Check | What is asserted |
|---|---|
| S0 | the control: five lines written on a quiet bus reach it, in order; this also shows that the PCAN hears the unit |
| T0 | leg T, 2,000 frames a second of `0x123` with 8 data bytes for 12 s or longer (44 KB a second as slcan text): the unit's CAN interface received every frame and the connection row took every one |
| T1 | the link was overloaded (frames did not arrive), or the leg proves nothing |
| T2 | what arrived at the central is whole lines only: nothing that is no slcan line, no line that is not the flood's, the frame numbers rising |
| T3 | every byte that did not arrive is counted by the unit: `pipe.tx_drop_bytes` + `pipe.notify_drop_bytes` on `/api/ble` |
| T4 | a line written every half second all through the flood (a write with response) is answered within 2 s each time, and its frame reaches the bus |
| R0 | leg R, writes while the unit cannot send (the PCAN keeps the bus full with `0x001`, which wins every arbitration, and that same flood keeps the pipe toward the central overloaded; the central writes 60 times 35 lines of `0x700`, each write with response): every write was answered, none with an error |
| R1 | every line of every write was handed to the CAN interface (`tx` on `/api/can`) |
| R2 | each of those frames is on the bus (`tx_done`, and the PCAN saw as many) or in the interface's loss counter (`tx_lost`: a frame that loses every arbitration is given up, so that a bus that is gone does not hold a client for ever), and nothing else is on the bus |
| R4 | the link stayed up through both legs |

Two more checks are the configure and the restore stage of the central's half (settings as found, no new fault, no unexpected reset, no E line from the Bluetooth tags).

Why slcan and not the binary records of `blecanraw`: a connection row hands a translated stream over in units of up to 1 KB, and that is where a full queue could cut one. With the raw row the units are a record or two, and a packet of 490 bytes holds exactly 35 of its 14-byte records, so neither the cut nor a lost packet shows there (the first run of this bench, 2026-11-18 12:46).

## Records

**2026-11-18 12:54, the firmware before the changes (image `95b79c95...`, the bench by hand): `BLE OVERLOAD FAIL`.** S0 green. Leg T, 38,006 frames at 2,000 a second, 13,525 lines arrived (15.5 KB a second). T2 red: 19 pieces that are no slcan line (`t1238B000000000`, `70B000000000000`): a unit cut at a 128-byte chunk when the transmit queue was full. T3 red: 538,252 bytes did not arrive and no route had a counter for them. T4 red: two writes during the flood, neither answered within 8 s, neither reached the unit (its session summary counted 5 writes, the control's), and the central ended the link 36 s later, its ATT timeout of 30 s. The legs behind it could not be judged: the link was gone.

**How the cause of T4 was found (13:41 to 14:34).** With the cut and the counting fixed (T2 green; T3 green and exact, 434,984 bytes not arrived and 434,984 counted) T4 stayed red. An HCI trace on the Pi (`--btmon`): three writes during the flood were answered within 25 ms; the fourth was sent, the unit's radio took it 190 ms later (`Number of Completed Packets`), and no answer ever came, while notifications kept arriving. The unit's console, in the stage's capture, at that moment: `E NimBLE: ble_att_svr_pkt rc=6`. The host had no buffer for the answer: the notifications waiting in it had emptied the pool it takes small buffers from (the README of `ble_manager` has the mechanism). That line was not in the unit's log ring a minute later: the stack's two INFO lines per notification had wrapped it, which is why the stage captures the console and the stack's tag is at WARN now.

**2026-11-18 15:32, with the changes (image `c905eda7...`, the stage): `BLE OVERLOAD PASS`, 13 checks, 186 s.** Leg T: 24,242 frames, 10,256 lines arrived (18.1 KB a second), nothing cut; 307,692 bytes did not arrive and 307,692 are counted (`tx_drops` 13,794 units, `tx_holds` 40, `buffer_waits` 6,025); 11 of 11 writes answered (the slowest 0.62 s) and their frames on the bus. Leg R: 60 of 60 writes answered (the slowest 1.14 s); 2,100 lines, `tx` 2,100, `tx_lost` 2,100 (each frame tried 17 times), none on the bus; the link up through both legs.

**Leg R before it was redefined (14:59, image `aff6b7f1...`).** The leg first asserted that every line of a write the unit accepts reaches the bus once it is released. It was written to show another finding, made by reading: a write that the connection row's queue has no room for is taken in part and acknowledged. It did not show. The unit took all 240 writes, handed all 8,400 lines to its CAN interface, and that interface gave up on each frame after its retries (`tx_lost`): the row's queue never filled. The change written for that finding was taken out again, and the leg asserts what the unit does. A bus with no node that acknowledges, at a low bit rate, with writes faster than the interface gives frames up, would be the place to look.

**What the console costs (2026-11-18 15:32 to 16:05, image `c905eda7...`, leg T, three runs a side, `--log-level`).** With the stack's tag back at INFO (`NimBLE=info`: its two lines for every notification, about 130 lines a second during the flood) the link carried 13.1, 18.6 and 16.5 KB a second, 16.1 on average. As the firmware ships now (the tag at WARN): 18.1, 19.5 and 19.6, 19.1 on average, and the slowest answer to a write 0.62, 0.60 and 0.54 s (1.45, 0.61 and 0.55 s with the lines on). In the two pairs run back to back the quiet side was 5 % and 19 % higher. Three runs a side over a radio link is not a tight measurement, but the direction is the same in each pairing. One run with every tag off (`'*=none'`) gave 15.5 KB a second, inside the same spread. The same comparison on a full CAN bus over TCP showed no difference (the unit prints about a line a second there): [canmax.md](canmax.md).

## Traps

- The central's writes are sent WITH response, so that a write the unit does not answer is seen: that was the defect (the central gave the link up 30 s after it).
- `--log-level` sets log levels on the unit for the run through `PUT /api/logs/level` (`'*=none'` = no log output at all, `NimBLE=info` = the stack's two lines per notification back). They last until the unit's next restart, which the restore stage is. `--btmon` adds an HCI trace on the Pi and prints what the central's radio did with its writes.
- The frame numbers of the three legs do not overlap (leg R from 0, leg S from 1,000,000, leg T from 2,000,000), so a frame that turns up late is attributed to its leg.
- A run that dies between the stages leaves the settings changed (the connection row, AutoPID off, the station kept beside Bluetooth): `python tools\testbench\ble\ble_pi_run.py ble_overload_pi.py --dut <address> --stage restore` puts them back from the state file on the Pi.
- In leg R the unit sends in whatever gaps the PCAN leaves. That is fine: R2 counts what got through against `tx_done` and the rest against `tx_lost`.
