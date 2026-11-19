# The most frames a CAN bus can carry

Command: `.\test.ps1 canmax` (= `python tools\testbench\can\can_maxrate_bench.py --dut localhost:18081 --slcan localhost:18333 --pcan <the PCAN channel on the DUT's bus> [--tiers 500:std8,500:std0,1000:std0!] [--secs 10] [--keep-sim] [--no-flash-wait]`; the stage opens the tunnel through rpi001 for the web API and for TCP 3333).

Needs: PCAN on the DUT's bus (`WICAN_PCAN_DUT`, the adapter's plugs may be swapped) + rpi001 + ECU sim v2 reachable (the bench moves its HS CAN to the tier's bit rate and switches its answers off). Prints `CAN MAX RATE PASS`, 38 checks, about 4 min, three planned restarts of the DUT (the settings for 500 kbit/s, for 1000 kbit/s, and back as found).

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
| `the unit's flash was at rest all through it` | `health.flash` of `/api/status` (writes, erases) did not move between the start and the end of the tier |

**The flash at rest (since 2026-11-19).** A flash erase holds every task of the unit for 23 ms and more; only the CAN receive interrupt runs meanwhile, and the frames of that time wait in the receive queue. The queue holds 256 frames: 61 ms of a full 500 kbit/s bus of 8-byte frames, 30 ms at 1000 kbit/s. And the unit does erase after each of the bench's restarts: about 25 s after a restart that changed a setting it refreshes its configuration backup (28 to 32 writes, 4 to 7 erases of 4 KB), which is exactly where the first tier of each bit rate ran. So the five tiers above hold with the flash at rest, and the bench makes sure of it: after each restart it waits until the unit has been up 40 s and its flash counters have stood still for 3 s (about 30 s more per bit rate), and it reads the counters before and after every tier. `--no-flash-wait` runs the tiers at once, as the bench did before, to see what a refresh inside a tier costs (the record of 2026-11-19).

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

**2026-11-18 16:32, the web page under a full bus, after three changes (image `537b7f86...`): `CAN MAX RATE PASS`, 33 checks.** Every frame taken in the five tiers the unit must carry. The web API's slowest answer: 0.50 s, 0.17 s, 0.13 s, 0.56 s and 0.21 s, 22 answers of 22 in each tier. A poll of `/api/status` every 100 ms from the Pi beside the stage: nothing above 1.1 s outside the stage's three planned restarts. No task watchdog warning on the console.

What the same stage showed before, on the image of that noon (`95b79c95...`, 13:01 to 14:42, four runs): the slowest answer per tier between 0.5 s and 3.4 s (6.3 s in one run), 12 or 13 polls above 1 s, and on the console of three of the four runs `task_wdt: Task watchdog got triggered` for the idle task of core 0 (it did not run for 5 s; no reset). Sampled every 2 s from `/api/status/tasks`: the TCP/IP task used about a whole core, both idle tasks 2 to 6 %, the web server's task a tenth of a core. Counted on the Pi: about 950 TCP segments a second from the slcan port whatever the tier, 60 to 240 bytes each, 55,229 in the run.

The three changes, each measured with a run of its own: (1) a stream's units leave together every 3 ms (the pace of `tx_backlog`): 24,585 segments of 307 bytes, the TCP/IP task at about a third of a core, one core 40 to 55 % idle; (2) a unit that releases waiting bytes leaves with them in one send (the first cut of the pace still made 720 segments a second: two sends per release); (3) the web server's task on core 1 (it had ended up on core 0 or core 1 by chance, and core 0 is the busy one: with (1) and (2) alone a boot with the server on core 0 still had answers of 1.1 s and 3.0 s). Logging made no difference here: the same image with every log tag off (`--log-level '*=none'`) and with logging as it is gave the same segments, the same task shares and the same slow answers; the unit prints about a line a second during a tier.

**The `std0` tier at 500 kbit/s is at the edge (2026-11-18 afternoon).** 10,417 frames a second lost nothing in the five runs up to 13:01. In three of the next four on the same image it lost 3, 2 and 4 of 104,166 frames, each counted in `rx_overrun` (the controller's FIFO ran over before the driver's task emptied it). With the pace as it is now (the runs of 15:43, 16:07 and 16:32): none lost in three of three. The WiFi task's share of core 0 was higher in those afternoon runs (45 to 67 % against 37 %), which is the radio's weather and not something the stage controls. So: every bus at 500 kbit/s is carried, and a full bus of frames with no data bytes can cost a few frames in 100,000, counted.

**2026-11-19: the first tier after a restart lost frames in three runs of eight; the cause was the bench (image `f444c3eb...`, ELF `eb50e90bb22b268f`).** Six runs in a row had passed up to 11:06. From 12:07 on the `std8` tier, the first after each restart, lost 1 to 18 frames in three of six runs (500 kbit/s once: 1 of 42,025, `rx_overrun`; 1000 kbit/s three times: 3, 18 and 3 of 84,050, `rx_overrun` or `rx_missed`), with two images of ECU sim v2 alike. With the flash counters read around every tier the picture was the same in every run: the first tier of each bit rate had the configuration backup's refresh inside it.

| Run of `--tiers 1000:std8,500:std8` | The flash during the 1000 kbit/s tier | Lost of 84,050 | The flash during the 500 kbit/s tier | Lost of 42,025 |
|---|---|---|---|---|
| `--no-flash-wait`, 1 | erases +4, writes +29 | 2 (`rx_overrun`) | erases +7, writes +32 | 0 |
| `--no-flash-wait`, 2 | erases +4, writes +29 | 13 (`rx_missed`) | erases +4, writes +29 | 0 |
| `--no-flash-wait`, 3 | erases +4, writes +29 | 0 | erases +7, writes +32 | 0 |
| with the wait, 1 to 3 | at rest | 0, 0, 0 | at rest | 0, 0, 0 |

Then the whole stage: `CAN MAX RATE PASS`, 38 checks, 238 s, the flash at rest 31 s after each restart was up (40 s of uptime). Report `test-reports/2026-11-19_1314_canmax.md`.

What it says about the unit, apart from the bench: a refresh of the configuration backup inside a 1000 kbit/s bus full of 8-byte frames (8,400 a second) cost 2 to 18 frames in five of the eleven runs of that day that had it there, and inside a full 500 kbit/s bus of 8-byte frames one frame in one run of eleven (the refresh inside the tier read from the flash counters in the three runs of the table; in the eight runs of the stage before them it follows from the bench's timing, which was the same). Each lost frame is counted. The queue of 256 frames was sized for 500 kbit/s (2026-11-17, [conserve.md](conserve.md)); twice the queue would cover the `rx_missed` part at 1000 kbit/s for another 3.8 KB of internal memory, and not the `rx_overrun` part, which is the controller's own FIFO.

## Traps

- The first tier after a restart is not the place for a line-rate measurement: the unit refreshes its configuration backup about 25 s after a restart that changed a setting, and a flash erase at the line rate can cost frames (the record of 2026-11-19). The bench waits for the flash to rest and fails a tier that had a write inside it; a new line-rate bench must do the same (`Dut.wait_flash_at_rest()` here, `wait_flash_at_rest()` in the flow control bench).

- `WICAN_PCAN_DUT` must name the PCAN channel on the DUT's bus. With the other channel the adapter sits on the simulator's 125 kbit/s bus, gets nothing out and its driver refuses every frame once its queue is full; a first version of the bench retried for ever. It now stops when the adapter took no frame for 5 s, and the 20-frame check fails first.
- `bus.send()` of python-can returns when the adapter's driver has the frame, not when the frame is on the wire: 42,000 frames are handed over in two seconds and take ten to leave. The bench waits out the time the line rate needs, then reads the counters.
- ECU sim v2 refuses 1000 kbit/s on its HS CAN while its J1939 side is on (`hs_j1939 needs 250 or 500 kbit/s`). The bench switches every answer and the broadcast of that node off for the run and puts the keys back as found.
- A unit whose image was installed by `POST /api/ota/upload` and not confirmed yet falls back to its previous image when the stage starts: the console capture opens the console port, which resets the unit, and a reset is not a planned restart, so nothing confirms the image before it (seen 2026-11-18: the first run of this stage then measured the release image). Flash by the console port, or `POST /api/restart` once the uploaded image is up and check `/api/fw_update` before the run; the header line of the bench prints the build and the partition.
- The stage's console capture opens the console port just before the bench starts, which can reset the unit. A request through the ssh tunnel to a unit that is restarting fails at once, so the bench's first request is asked again for up to 40 s (2026-11-18: one run ended with `no DUT at localhost:18081` seven seconds into the stage).
- The rate on the wire is read through the web API, and above about 12,000 frames a second the API may not answer during the burst: the bench then prints `not measured`. That is why the tier above the unit does not assert `the bus was full`.
- The bus must be quiet: the bench checks for three seconds after its restart and fails `the bus is quiet` otherwise (AutoPID still on, the simulator broadcasting).
