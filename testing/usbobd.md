# The `usb_obd` jack as a pipe

Command: `.\test.ps1 usbobd` (= `python tools\testbench\usb\usb_obd_pipe_bench.py --dut localhost:18081 --tcp localhost:18333 [--com auto]`; the stage opens the tunnel through rpi001 for the web API and for TCP 3333).

Needs: the DUT's USB-C cabled to this PC (the preflight line says `connector -> PC (CH342 ...)`; without it the stage SKIPs) + rpi001 for the tunnel + pyserial. Prints `USB OBD PIPE PASS`, 8 checks, about 50 s, two planned restarts of the DUT (the pipe, and the settings as found).

The index row and the other stages: [../TESTING.md](../TESTING.md). The cabling of the DUT's connector: [preflight.md](preflight.md).

## What it answers

How soon a short write from the PC is on its way, whether every byte of a stream arrives, and whether the bytes the unit loses on this port are counted where they can be read.

`usb_obd` is the unit as a USB serial device: the CH342's second port on the PC, 2,000,000 baud, the unit's UART2 behind it. Two defects, both found by reading and shown here on 2026-11-18:

1. **A short write left the jack 20 ms late.** The reader asked the UART driver for 128 bytes with a 20 ms limit, and the driver waits its whole limit again behind every arrival. An ELM command of a few bytes therefore waited 20 ms before it went on to the OBD chip. The reader now waits for the driver's events and reads what the driver holds.
2. **Bytes lost on the UART were counted nowhere.** The UART's interrupt waits for a flash write to end, its FIFO holds 128 bytes, and at this speed 128 bytes arrive in 0.64 ms: a stream from the PC loses bytes whenever the unit writes its flash. The reader now counts each time the UART reports that its FIFO or the driver's ring ran over (`rx_overruns`), and `/api/bridges` shows the counter (`losses`) with the jack's `rx_drops` beside it.

## What it covers

For the run the jack is bridged raw to the TCP server `slcan0` (`br_usb_pipe = usb_obd <-raw-> slcan0`; the rows that use `usb_obd` or `slcan0` are parked), so what the PC writes on the COM port comes back on a TCP client and the other way round, and nothing reaches the OBD chip. Only the usb_obd COM port is opened, with DTR and RTS left alone; the console port beside it is not touched.

| Check | What is asserted |
|---|---|
| `the pipe is up` | the bridge row runs |
| `L1 a short write from the PC is on its way at once` | 40 writes of 9 bytes on the COM port, each timed until it is whole on the TCP client: the fastest took less than 20 ms. The other direction (TCP to COM) is measured and printed beside it |
| `V1 every line arrives once and in order, no loss counter moved` | 60,000 bytes as numbered lines at 50,000 bytes a second: all 7,500 lines, in order, with the path's loss counters at rest |
| `F1 the flash was written during the stream and lines were lost` | the same stream for 6 s while three files of 32 KB are uploaded to `/data`: `health.flash.erases` moved and lines are missing (that is the hardware's limit, not a defect) |
| `F2 the loss is in a counter of a route` | a loss counter of this path moved: `losses.a` of `br_usb_pipe` (`rx_overruns`, `rx_drops`) or the TCP server's `tx_drops` |
| `R1` to `R3` | settings back as found, only the planned restarts, no new E line |

## What it does not cover

The OBD chip behind the jack (the factory row `usb_obd <-> obd`): the chip's own reply time is not part of this. The `acm` jack (a USB serial device plugged into the unit as a host) and the `cli` jack: no bench here provokes their waits, see the report of 2026-11-18.

## Records

**2026-11-18 13:33, bench unit 14c19f457fd9, the firmware before the fix (image elf `7bb648422357a1e2`), COM469: `USB OBD PIPE FAIL` (L1, F2).**

| Leg | Result |
|---|---|
| L, COM to TCP, 40 writes | fastest 20.8 ms, median 21.5 ms, slowest 30.1 ms |
| L, TCP to COM, 40 writes | fastest 54.3 ms, median 67.6 ms, slowest 84.7 ms (the USB adapter's own read delay is in this direction) |
| V | 7,500 of 7,500 lines, in order |
| F | 3 uploads, flash erases 3 to 34, 4,043 of 37,500 lines lost, no loss counter of any route moved |

**2026-11-18 13:50, the same unit, the build with the fix (image elf `bafe0402df0c7399`): `USB OBD PIPE PASS`, 8 checks, 46 s.**

| Leg | Result |
|---|---|
| L, COM to TCP, 40 writes | fastest 1.8 ms, median 2.1 ms, slowest 8.4 ms |
| L, TCP to COM, 40 writes | fastest 54.2 ms, median 73.2 ms, slowest 90.1 ms (unchanged: the USB adapter) |
| V | 7,500 of 7,500 lines, in order, no loss counter moved |
| F | 3 uploads, flash erases 0 to 34, 4,169 of 37,500 lines lost, `losses.a.rx_overruns` of `br_usb_pipe` 0 to 172 |

The final build of the day (image elf `2d790852dd98d8ae`) has the same `usb_obd` code.

**2026-11-18 18:39, the same unit, the port's FIFO handed over at 32 bytes and its interrupt on the app core (image elf `eb50e90bb22b268f`): `USB OBD PIPE PASS`, 8 checks, 57 s.**

| Leg | Result |
|---|---|
| L, COM to TCP, 40 writes | fastest 1.7 ms, median 2.0 ms, slowest 4.1 ms |
| L, TCP to COM, 40 writes | fastest 54.1 ms, median 72.1 ms, slowest 88.8 ms |
| V | 7,500 of 7,500 lines, in order, no loss counter moved, in the first pass |
| F | 3 uploads, flash erases 4 to 32, 2,927 of 37,500 lines lost, `rx_overruns` 0 to 28 |

What led to that change. On the merged build of that evening leg V lost 5 lines of 7,500 with `rx_overruns` +1 in two stage runs (17:13 and 17:30, image elf `df9dcea581095b8c`). Leg V alone, pass after pass with the unit's flash counters read beside it, showed two different losses:

- A pass 21 s after the boot lost 124 to 145 lines with 43 to 50 overruns while the flash counters moved by 28 to 32 writes and 4 to 7 erases. That is the unit refreshing its configuration copies, 20 s after a boot that follows a settings change, which this bench's boot is. It is the loss leg F is about, at a moment the bench did not choose: leg V now starts when the unit has been up 30 s.
- 3 passes of 38 (image elf `29c018a9067b831d`) lost 4 to 8 lines, about 40 bytes, with the flash counters unchanged, and the unit's own line said `its FIFO ran over`. The UART's interrupt came when 120 of the FIFO's 128 bytes were full, which leaves it 40 us at 2 Mbaud, and it ran on core 0 behind WiFi's: the defect the OBD chip's port had until 2026-11-11 ([chip_uart_burst_bench.md](chip_uart_burst_bench.md)), with the same two settings as the fix. After it: 30 passes of 30 without a line lost. Leg F's three uploads cost 28 overruns and 2,927 lines where they cost 83 to 172 and 3,518 to 4,169 before.

## Traps

- The PC reads a COM port in blocks the USB adapter hands over every few milliseconds, so the direction TCP to COM is slow whatever the unit does (54 ms at its fastest here). It is printed, not asserted.
- The stream of leg F loses lines on every firmware: the UART's FIFO is 128 bytes and a flash erase holds its interrupt for about 23 ms. What the leg asserts is that the loss can be read.
- The unit writes its flash on its own too: its configuration copies are refreshed 20 s after a boot that follows a settings change, and the bench's restart is one. Leg V waits until the unit has been up 30 s, and a pass that lost lines while the flash counters of `/api/status` (`health.flash`) moved is made again, up to three times; the check's detail lists such passes. A loss with those counters unchanged fails the leg.
- The first request of the bench waits up to 40 s for an answer: the stage's tunnel may not stand yet (an ssh to the Pi took 7 s to begin on the evening of 2026-11-18).
- `rx_overruns` counts the times the UART ran over, not the bytes: the driver does not say how many it threw away.
- The bench parks `br_usb_obd` for the run. A run that dies before its restore leaves the PC's usb_obd port without the OBD chip behind it: run it again, or put the row back on the Connections page.
