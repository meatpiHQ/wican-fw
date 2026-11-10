# OBD chip UART: a long output of the chip arrives whole

Command: `python tools\testbench\obd\chip_uart_burst_bench.py [--dut localhost:18081] [--boots 5] [--rounds 20] [--peak-max 96] [--late-max 0.5] [--only boots,quiet,busy]` (PC, the DUT through an ssh tunnel `ssh -N -L 18081:<dut>:80 rpi001`)

Needs: main fw on DUT + rpi001 + the `/ws/obd` bridge. No simulator, no bus. Prints `CHIP UART BURST PASS`. About 4 min: 5 planned restarts, 280 long reads. Nothing is written to the chip (every word is a display), no vehicle protocol is opened, no setting of the unit is changed.

The index row and the other stages: [../TESTING.md](../TESTING.md). Where the defect was found: [protocol_baseline_bench.md](protocol_baseline_bench.md). The driver's side: the components repo's obd_chip README, "The FIFO hand-over".

## What it covers

The OBD chip sends at 2,000,000 baud, a byte every 5 µs, into a hardware receive FIFO of 128 bytes. A short OBD reply fits into the FIFO whole, and a multi-frame reply is printed frame by frame. A long display of the chip (its parameter summary `ATPPS`, its sleep configuration `STSLCS`, its user area, its protocol tables: 300 to 1600 bytes in one unbroken burst) has to be taken out of the FIFO several times while it is still arriving, and each of those hand-overs has to start before the FIFO is full.

Until 2026-11-11 two things were against that. The UART driver was called for a hand-over when 120 bytes waited (the SDK's default), 8 bytes or 40 µs before the end of the FIFO. And its interrupt sat on the processor core that also serves the WiFi interrupts, behind them: one hand-over in 45 started later than 40 µs. The FIFO then ran over: the device lost a piece of the answer, dropped what it had buffered, counted it (`uart.rx_overflows` of `GET /api/obd_chip`) and logged `W obd_chip: rx overflow (fifo) - input flushed`.

Since then the driver is called at 32 bytes (96 bytes, 480 µs to start) and its interrupt lives on the other core. Three counters of `GET /api/obd_chip` show the margin: `uart.rx_fifo_peak`, the fullest any hand-over found the FIFO since the start (what lies above 32 is how late the latest one started), and `uart.rx_late` of `uart.rx_handovers`, how many hand-overs started 40 µs or more behind their interrupt.

What a cut answer looked like (`STSLCS`, the line `EXT_SLEEP:  OFF, LOW, FOR 64 ms`): `EXT_ s | VCH  OFF, LOW, FOR 64 ms`. A few bytes are gone and stale bytes of the FIFO (the end of the answer before) stand in their place. Other cuts lost 15 to 240 bytes, or the prompt, and the read then ran into its time-out.

| Leg | What is done | Must read |
|---|---|---|
| boots | `--boots` planned restarts. The bring-up reads `STSLCS` and `ATPPS` itself at every start, and what it reads decides whether the chip's sleep configuration and its PP 0F are written again | after each: `rx_overflows` 0, `rx_fifo_peak` at or under `--peak-max`, no warning of the chip driver about those reads in the log ring |
| reference | each of the seven long words read until two answers agree byte for byte with no overflow counted under them | a steady answer for all seven |
| quiet | `--rounds` times the seven words through `/ws/obd` (the app path), nothing else asked of the unit but its counters | every answer equal to the reference, no overflow |
| busy | the same while a second client asks `GET /api/status` as fast as it is answered (WiFi and the web server at work) | the same; the second client was served |
| at the end | the counters, the restart history | `rx_overflows` +0 over the run; `rx_fifo_peak` at or under `--peak-max` (96: a quarter of the FIFO, 160 µs, left); `rx_late` at or under `--late-max` per cent of `rx_handovers` (0.5); the boot count up by the planned restarts only, no unexpected reset |

The peak is ONE event and moves from run to run (46 to 72 on the bench unit). The share is what tells where the interrupt lives: 0.07 to 0.14 % on the app core, 2.2 % on the WiFi core, where a run's answers can all be whole and the margin still be thin.

`--boots 0` restarts nothing; the peak is then whatever the unit has seen since it started. On an image without these counters the bench still counts overflows and cut answers (that is how the reproduction ran).

Not covered, by construction: a flash write under a long output. The UART's interrupt does not run while the flash cache is off (milliseconds for a write, tens of them for an erase), so a long output during one still loses bytes at any threshold; a short reply waits in the FIFO. Closing that would mean the UART interrupt handler in RAM, which moves every UART's buffers into internal RAM (36 KB for this port alone).

## Records

All on bench DUT 44bd8d73ca79, chip MIC3624 V2.3.24, 2026-11-11, Bluetooth and AutoPID off (the unit's settings as found) except in the row that says so. 20 rounds are 280 long reads, 40 rounds 560.

| Image | Threshold | Interrupt on | Cut answers | Overflows | Peak | Verdict |
|---|---|---|---|---|---|---|
| the shipped code, image sha256 `359d87ed` (06:38) | 120 | core 0 | 40 of 280 (quiet 7 of 140, busy 33 of 140) | 44 | not reported | `FAIL` |
| the new driver code, the two settings left as they were (06:53) | 120 | core 0 | 34 of 280 (quiet 11, busy 23) | 39 | 128 | `FAIL` |
| the threshold alone (07:02) | 64 | core 0 | 0 of 280 | 0 | 106 | answers whole, 110 µs from a loss |
| the threshold alone, with a lateness count (07:11) | 32 | core 0 | 0 of 560 | 0 | 72 | answers whole |
| the threshold alone (07:20) | 32 | core 0 | 0 of 280 | 0 | 94 | answers whole, 170 µs from a loss |
| a higher interrupt level, with a lateness count (07:28) | 32 | core 0, level 3 | 0 of 560 | 0 | 48 | not taken, see below |
| the other core, with a lateness count (07:38) | 32 | the app core | 0 of 560 | 0 | 48 | answers whole |
| the other core (07:47) | 32 | the app core | 0 of 280 | 0 | 72 | answers whole |
| the fix, image sha256 `696ff06e` (07:54) | 32 | the app core | 0 of 280 | 0 | 46 | `CHIP UART BURST PASS`, 10 checks; 5 of 5892 hand-overs late (0.08 %) |
| the same image with Bluetooth on and AutoPID polling the simulator (08:12, 3 restarts) | 32 | the app core | 0 of 280 | 0 | 50 | `CHIP UART BURST PASS`, 10 checks; 8 of 5891 late (0.14 %) |
| **the fix as it is**, image sha256 `2610eab5` (08:34; one log string and comments apart from `696ff06e`) | 32 | the app core | 0 of 280 | 0 | 50 | **`CHIP UART BURST PASS`**, 10 checks; 6 of 5889 late (0.10 %) |

- **The second row is the A/B:** everything of the change but the two settings, and the loss is as before.
- **How late a hand-over starts** (a temporary count in the driver, bins of 40 µs, about 11500 hand-overs a run, the second client asking during half of them):

  | Interrupt on | Within 40 µs | Later | The latest, all runs |
  |---|---|---|---|
  | core 0, as shipped | 97.8 % | 2.2 %: 194, 30, 12, 8 and 1 per further 40 µs | 310 µs |
  | core 0, level 3 | 99.97 % | 3 hand-overs | under 120 µs (one run) |
  | the app core | 99.93 % | 8 hand-overs | 200 µs (once in five runs, about 35000 hand-overs) |

  With 40 µs to spare, 2.2 % late is one loss in 45 hand-overs, which is what the first two rows show. Most of the lateness was the wait behind other interrupts on core 0, WiFi's first of all. A rare late hand-over remains on either core (200 µs on the app core, 310 µs on core 0): the threshold is what covers it, with 280 µs left in the worst case seen.
- **Why the threshold alone was not kept.** At 32 on core 0 every answer was whole, but with one hand-over in 45 late and one of them 310 µs late: 170 µs from a loss, on a bench with Bluetooth off and no bus traffic. At 64 it was 110 µs.
- **Why not the higher level on core 0.** It measured as well as the other core. But Bluetooth's controller takes its interrupts at that level on core 0, and a core has two slots for it; the bench unit had Bluetooth off, so the run could not show a clash. On the app core the chip's UART competes with neither.
- **The bring-up's own long reads were not hit.** 20 planned restarts on the old settings: no overflow, and the peak exactly 120 every time (not one byte late: the unit is not busy yet when the bring-up runs, about 3 s after the start). So no write of the chip is known to have come from a cut read. The bring-up took 1178 to 1180 ms in every boot of this work, as before.
- What the apps receive did not change: `rx_max_chunk` 128 before and after, 106 bytes a chunk on average before and 116 after (the driver passes on whole 128-byte chunks in the middle of a burst, whatever the threshold).
- **Regression:** `RELEASE TEST PASS` 59 checks on image `696ff06e` (337 s) and again on `2610eab5` (335 s): no E line, internal heap floor 57636 and 57644 B, the Bluetooth step skipped (off in the canonical settings). The rest on `696ff06e`: `OBD BRINGUP PASS` 27 (`--skip-pp`, bring-up 1179 ms three times); `ATTP STORE PASS` 36 (`--skip-canary`); `PROTOCOL SAVE PASS` 15 (no write); the host suite `obd_chip` 40. `ELM APP PASS`: the hinted `010C 1` loop 118 req/s, p50 6.7 ms, p95 17.8 ms, the hint-less loop 22.4 req/s, p50 40.3 ms (the earlier records: 114 to 129 req/s, p50 5.2 to 7.5 ms; 23.4 req/s, 37.6 to 40.8 ms), so a short reply costs what it did. `ATMA FLOOD PASS` at 1000, 2500 and 4000 frames/s: every counter frame once and in order (4000, 10000, 16000), no malformed line, no UART overflow, no fan-out drop, bytes conserved; the peak read 38 under the floods (a monitor line ends in the rx-timeout, the FIFO is handed over rarely: 156 hand-overs in 30000 lines). The chip's store read `6` throughout, 0 saves, 0 PP restores; the unit's settings back as found.
- **Seen on the way, not part of this change: with AutoPID on, the start-up's chip firmware check now usually runs behind AutoPID's first contact.** The bring-up sets its done bit and then claims the chip for the version check (`chip reports: ...`); AutoPID, woken by that bit, claims it for its first requests. Whoever is first, both run. When AutoPID is first the line `bring-up done in` reads 3.6 to 4.6 s instead of 1.18 s. On the image before this change that happened in 2 of the release test's 5 boots with AutoPID on (4439 and 4580 ms); on this image in 5 of 5 there, in 5 of 5 further boots with Bluetooth off and in 5 of 9 with Bluetooth on (where the line reads 1.8 s when the check is first). The chip is up at the same moment either way (`started:` about 750 ms after the warm start), no poll failed, and the check ran in every boot. The check waits 5 s for the chip at most and is skipped for that boot when it does not get it, so the order is worth making fixed; it is a race in the bring-up, older than this change.

## Traps

- **A `>` inside an answer is not the prompt.** `STSLCS` prints `VL_WAKE: OFF, >13.20V FOR 1 s`. The bench's reader (`chip_nvm.Chip.cmd`) took the first `>` for the end until 2026-11-11: that answer lost its last two lines (251 of 302 bytes), and the next word went out while the chip was still printing. The prompt is a `>` at the start of a line with nothing behind it.
- **Flashing over the serial port resets the restart history.** The reset line is the power-on of the processor: the boot count starts at 1 again and the history is empty. The unit had 43 boots before this work.
- **The counters are counted since the start.** An overflow or a late hand-over an hour before the run is in the peak. The boots leg restarts first for that reason; the share is taken over the run.
- **A limit on the peak alone makes a flaky bench.** The first version failed a good image at a limit of 64 (a peak of 72, one hand-over in 5900). One late hand-over is not the margin gone; a share of them is.
- The seven words are displays of the bench unit's chip firmware (V2.3.24). A chip firmware that does not know one of them answers `?`, which is steady and short: the leg then proves nothing for that word, and the reference line prints the lengths.
- AutoPID may stay on (it yields to an app on `/ws/obd`). The bench's start check asks for a ready chip, not an unclaimed one: with AutoPID polling the chip is claimed for a request most of the time, and the first version of the bench refused to start on such a unit.
