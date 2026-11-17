# The most frames a CAN bus can carry

Command: `.\test.ps1 canmax` (= `python tools\testbench\can\can_maxrate_bench.py --dut localhost:18081 --slcan localhost:18333 --pcan <the PCAN channel on the DUT's bus> [--tiers 500:std8,500:std0,1000:std0!] [--secs 10] [--keep-sim]`; the stage opens the tunnel through rpi001 for the web API and for TCP 3333).

Needs: PCAN on the DUT's bus (`WICAN_PCAN_DUT`, the adapter's plugs may be swapped) + rpi001 + ECU sim v2 reachable (the bench moves its HS CAN to the tier's bit rate and switches its answers off). Prints `CAN MAX RATE PASS`, 33 checks, about 3 min, three planned restarts of the DUT (the settings for 500 kbit/s, for 1000 kbit/s, and back as found).

The index row and the other stages: [../TESTING.md](../TESTING.md). The same rule at one rate with exact counters only: [conserve.md](conserve.md). The slcan path with a stream and a burst together: [slcan.md](slcan.md), legs B13 to B15.

## What it answers

How many frames a second a CAN bus can carry at all, and whether the unit takes every one of them when the bus is that full. A bus is full when one frame follows the other with only the three bits of interframe space between them, so the number follows from the bit rate and the length of the frame, stuff bits included:

| Frame | Bits on the wire | 500 kbit/s | 1000 kbit/s |
|---|---|---|---|
| standard identifier, no data (`std0`): the shortest frame there is | 48 | 10,416 frames/s | 20,833 frames/s |
| standard identifier, 8 data bytes (`std8`), the bench's counter payload | 119 | 4,203 frames/s | 8,405 frames/s |
| 29-bit identifier, 8 data bytes (`ext8`), the bench's counter payload | 140 | 3,572 frames/s | 7,144 frames/s |

47 bits is what the fields of a frame with no data add up to, and it is never seen on a wire: the seven zero bits that follow the identifier (RTR, IDE, r0 and a data length of 0) always cost one stuff bit. The bench picks its 512 `std0` identifiers so that each frame is exactly 48 bits. A car's own traffic is far below these numbers (1,000 to 2,500 frames a second on a 500 kbit/s bus), so this stage is the ceiling, not a drive.

**The answer (2026-11-18, the records below):** the unit takes a full bus without losing a frame up to 10,400 frames a second, which is every bus at 500 kbit/s whatever its frames, and a 1000 kbit/s bus full of 8-byte frames (8,400 a second). The first frames are lost at 11,900 a second (a 1000 kbit/s bus full of 4-byte frames: 2 in 10,000). The most classic CAN can carry at all, 20,833 a second, costs 11 to 18 %. Every lost frame is in a counter with a name, the TCP client keeps its connection, and nothing restarts.

## What it covers

For each bit rate the bench sets the unit's CAN interface to it, switches AutoPID off (the OBD chip then leaves the bus alone), switches the slcan server on with a `can` connection row (`br_slcan`), and moves ECU sim v2's HS CAN to the same bit rate with its answers and its broadcast off (it keeps acknowledging). A tier is a bit rate and a frame shape; the stage runs six.

Five tiers the unit must take whole (500 kbit/s with `std8`, `std0`, `ext8`; 1000 kbit/s with `std8`, `ext8`), exactly N frames back to back each, N = the line rate times 10 s:

| Check | What is asserted |
|---|---|
| `the PCAN reaches the unit's bus` (once per bit rate) | 20 frames sent, the unit's `rx` +20: a run on the wrong plug measures nothing |
| `the bus was full` | the rate at which the unit's own count rose (`rx` + `rx_missed`, sampled twice a second through the web API, the ramps at both ends left out) is within 5 % of the rate the frame's bit count allows |
| `the CAN driver took every frame` | `rx` up by exactly N, and `rx_missed`, `rx_overrun`, `rx_bad`, `dispatch_drops`, `rx_errors`, `bus_errors`, `bus_off`, `rx_storms`, `rx_deaf` did not move |
| `the TCP client got every frame, once and in order` | one slcan client on TCP 3333 (through the tunnel and the WiFi) counted N lines of the tier's shape, each carrying the number after the last one, the server's `tx_drops` unchanged, the connection not closed by the unit |
| `the web API answered all through it` | every poll of `/api/can` during the tier was answered within 8 s; the slowest answer is printed |

One tier above what the unit takes (1000 kbit/s with `std0`, written `1000:std0!`): frames are lost there, and what is asserted is the accounting:

| Check | What is asserted |
|---|---|
| `every frame the CAN driver lost is counted (rx_missed, rx_overrun)` | N minus `rx` is `rx_missed` plus what the controller's own FIFO lost, one frame or two for each `rx_overrun`; no other counter of the driver moved |
| `every frame the bridge lost is counted (dispatch_drops, tx_drops)` | what the driver took and the client did not get is `dispatch_drops` + `tx_drops`, give or take 2 |
| `the client kept its connection and the order of what it got` | not closed by the unit, no line out of order |
| `the unit is itself again after it` | the web API answers within 10 s of the end, the CAN interface is `running`, no bus-off |

At the end the settings go back as found (the unit and the simulator), and the stage asserts that, the count of planned restarts, no unexpected reset and no new E line.

Other tiers by hand: any data length is a shape (`std0` to `std8`, `ext4` to `ext8`; a shape with fewer than four data bytes carries its number in the identifier), for example `--tiers 1000:std4,1000:std3,1000:std2,1000:std1`.

## What it does not cover

One consumer, the slcan server over TCP on the WiFi station. The WebSocket monitor, Bluetooth, CAN over MQTT and the SD card logger were not put on a full bus here. The unit's own transmit side is not loaded either (the client only reads).

## Records

**2026-11-18 08:34, bench unit 14c19f457fd9, the build of that day (image sha256 `f9e289cf...`, flashed by the Release Test of 08:09, the client buffer at 64 KB), PCAN channel 2: `CAN MAX RATE PASS`, 33 checks, 177 s.** Report `test-reports/2026-11-18_0834_canmax.md`.

| Tier | On the wire | Frames sent | The CAN driver took | The TCP client got | The web API's slowest answer |
|---|---|---|---|---|---|
| 500 kbit/s `std8` | 4,212 frames/s | 42,025 | 42,025 | 42,025 | 2.29 s |
| 500 kbit/s `std0` | 10,462 frames/s | 104,166 | 104,166 | 104,166 | 2.00 s |
| 500 kbit/s `ext8` | 3,577 frames/s | 35,720 | 35,720 | 35,720 | 0.18 s |
| 1000 kbit/s `std8` | 8,462 frames/s | 84,050 | 84,050 | 84,050 | 1.59 s |
| 1000 kbit/s `ext8` | 7,188 frames/s | 71,440 | 71,440 | 71,440 | 0.25 s |
| 1000 kbit/s `std0`, above the unit | 20,348 frames/s | 208,333 | 185,881 (`rx_missed` +22,452) | 180,129 (`dispatch_drops` +5,752), 17,900 a second | 1.21 s |

The same image earlier that morning: the three tiers at 500 kbit/s whole at 08:21 and at 08:24, the two long shapes at 1000 kbit/s whole at 08:24, with the web API's slowest answer between 0.22 s and 3.04 s.

**2026-11-18 08:24 (the `std0` row, a run of the stage before it had its tier above the unit) and 08:29 (the others, by hand), the same image: where the first frames are lost.** 1000 kbit/s, back to back, 10 s a tier:

| Tier | Line rate | Lost at the CAN driver | Lost behind it | The TCP client got |
|---|---|---|---|---|
| `std4` | 11,920 frames/s | 20 of 119,200 (`rx_overrun` +20) | 0 | 119,180 |
| `std3` | 13,889 frames/s | 48 of 138,888 (`rx_overrun` +48) | 0 | 138,840 |
| `std2` | 15,625 frames/s | 53 of 156,250 (`rx_overrun` +53) | 859 (`dispatch_drops`) | 155,338 |
| `std1` | 17,857 frames/s | 95 of 178,571 (`rx_missed` +31, `rx_overrun` +64) | 5,518 (`dispatch_drops`) | 172,958 |
| `std0` | 20,833 frames/s | 31,304 of 208,333 (`rx_missed` +31,229, `rx_overrun` +66) | 5,746 (`dispatch_drops`) | 171,284 |

In each row the frames the client did not get are the sum of the counters, to within the frames an overrun of the controller's FIFO costs beyond one. From 11,900 frames a second on the web API answered once or not at all during the ten seconds (a slowest answer of 4.9 s and 9.9 s, two polls of 5 s unanswered in three of the five tiers); it answered again as soon as the bus let go. In the 08:34 run of the stage the same `std0` tier lost 11 % at the driver with no overrun and the API kept answering, so the share varies from run to run.

## Traps

- `WICAN_PCAN_DUT` must name the PCAN channel on the DUT's bus. With the other channel the adapter sits on the simulator's 125 kbit/s bus, gets nothing out and its driver refuses every frame once its queue is full; a first version of the bench retried for ever. It now stops when the adapter took no frame for 5 s, and the 20-frame check fails first.
- `bus.send()` of python-can returns when the adapter's driver has the frame, not when the frame is on the wire: 42,000 frames are handed over in two seconds and take ten to leave. The bench waits out the time the line rate needs, then reads the counters.
- ECU sim v2 refuses 1000 kbit/s on its HS CAN while its J1939 side is on (`hs_j1939 needs 250 or 500 kbit/s`). The bench switches every answer and the broadcast of that node off for the run and puts the keys back as found.
- A unit whose image was installed by `POST /api/ota/upload` and not confirmed yet falls back to its previous image when the stage starts: the console capture opens the console port, which resets the unit, and a reset is not a planned restart, so nothing confirms the image before it (seen 2026-11-18: the first run of this stage then measured the release image). Flash by the console port, or `POST /api/restart` once the uploaded image is up and check `/api/fw_update` before the run; the header line of the bench prints the build and the partition.
- The rate on the wire is read through the web API, and above about 12,000 frames a second the API may not answer during the burst: the bench then prints `not measured`. That is why the tier above the unit does not assert `the bus was full`.
- The bus must be quiet: the bench checks for three seconds after its restart and fails `the bus is quiet` otherwise (AutoPID still on, the simulator broadcasting).
