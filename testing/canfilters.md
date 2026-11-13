# The CAN filters of Automate, on the device

Command: `.\test.ps1 canfilters` (the bench unit: `-DeviceId 14c19f457fd9`), or by hand `python tools/testbench/obd/can_filters_bench.py [--dut localhost:18081] [--pcan PCAN_USBBUS1] [--no-page] [--out DIR]` behind `ssh -N -L 18081:<the unit's address>:80 rpi001`

Needs: the main firmware on the unit on the bench AP, ECU sim v2 as the car, the PC's PCAN on the unit's bus at 500 kbit/s, the supply above the sleep voltage; for the page leg node, Chrome and the Playwright devDependency of tools/webui_preview

The index row and the other stages: [../TESTING.md](../TESTING.md).

## Tools, wiring, procedure

- **Tools:** a PEAK PCAN-USB on the PC with python-can 4.6.1 (`interface="pcan"`); ECU sim v2 (firmware v1.00-16 on the day of the record) with its HS CAN at 500 kbit/s, 11-bit OBD answers on; Python 3 on the PC; for the page leg node 20 or later, Chrome and Playwright (`npm install` once in `tools/webui_preview`).
- **Wiring:** the unit in ECU sim v2's OBD socket (pins 6 and 14 = HS CAN, pin 16 = the supply); one PCAN channel on the simulator's DB9 J2, which is that same bus. The other PCAN channel sits on DB9 J3 (MS CAN, 125 kbit/s) and is not used: the bench finds the right channel by listening, so swapped plugs do no harm. The bus is terminated by the simulator.
- **Host setup:** the unit reachable through the bench Pi (`rpi001`); `test.ps1` opens the tunnel on port 18081 itself.
- **Procedure:** `.\test.ps1 canfilters -DeviceId <id>`; nothing to prepare on the unit, its tables and autopid settings are saved first and put back at the end.
- **Pass:** the last line is `CAN FILTERS PASS` (every check `[ok]`, the planned restarts counted, no new E line, no new fault, no unexpected restart). Any `[FAIL]` line names the check; the answers of the test route and the page's screenshots are in the run's folder.

## What it covers

The firmware's half of the CAN filters list the web UI got on 2026-11-14
([probe_can_filters.md](probe_can_filters.md) has the page over the mock),
and the page itself on the unit.

The frames come from the PC's PCAN, so every value is known: 0x284 (the
vehicle's filter, `02 1E 02 1F 0B 7C 4E 20` = 30 km/h, later 60), 0x3B3 (a
custom filter, 5 then 7), 0x4D0 (a custom filter on a multiplexed message:
page 1 reads 40, page 2 reads 80), the 29-bit 0x18DAF110 (a custom filter,
34), each every 50 ms; 0x6F1 is sent by nobody. ECU sim v2 acknowledges
them and answers the standard PID; the unit's own protocol on it is the
11-bit one at 500 kbit/s (6).

- **f0** the unit, its tables and autopid settings saved; the PCAN channel
  on the unit's bus found by listening while the unit asks the car one
  standard request (the adapter's two plugs may be swapped: the bench
  takes the other channel by itself and says so); Automate on with its
  three switches on.
- **f1** a table of five filters and nothing else, across a planned
  restart: the four values of the 11-bit IDs arrive, the 29-bit ID is
  read too (the window opens on the protocol of the ID's width), the ID
  nobody sends stays empty.
- **f2** Test for a filter (`POST /api/autopid/test` with `filter`): the
  frame whole with its data and two values, the exchange (`ATH1`,
  `ATCRA284`, `ATCAF0`, `ATMA`, the frame, `ATCAF1`, `ATCRA`); a
  multiplexed message read page by page, with no value for a page that
  never comes; a 29-bit ID with `ATTP7` ahead of its receive filter and
  `ATTP6` at the end; a filter without parameters shows the frame; an ID
  nobody sends answers not ok with the reason after the listen time; the
  four refusals (no ID, an ID beyond 29 bits, a listen time of 10 ms, an
  expression that does not parse) are 400 with a reason.
- **f3** a standard PID beside the filters is read between the windows,
  and the filters' values follow the bus (30 to 60, 5 to 7).
- **f3b** behind a row that leaves the chip on the 29-bit protocol: a
  custom row whose init is `ATSP7` and no other PID (so nothing brings
  the car's protocol back); the PCAN hears that row go out as a 29-bit
  request; the 11-bit filters go on following the bus (60 to 30, 7 to 5)
  and the 29-bit one reads; then the table with the standard PID again.
- **f3p** the unit's own page in Chrome (`can_filters_page.mjs`, skipped
  with `--no-page`): the CAN filters list on Custom PIDs with its live
  values (the 29-bit ID's too); Add CAN filter, the ID typed as a plain
  `644` reads `0x284` (decimal, as on the 4.x page) and as `0x284` the
  same, a listen time, an expression and a unit; Test shows the whole
  frame and 60 km/h; Apply Configuration, the unit's table carries the
  filter as typed with no tag, the unit reads it and the row shows it
  live; on Vehicle Specific the vehicle's filter under its name, and a
  filter added there arrives tagged `profile` and is read; no page error.
  Screenshots in the run's folder.
- **f4** the switches, a planned restart each: `specific_enabled` off =
  the vehicle's filters rest (the page's one too) while the custom filters
  and the PID go on, and Test still reads a resting filter's frame;
  `custom_enabled` off = the opposite.
- **f5** the tables and the settings go back and are read back as found;
  no new E line in the unit's log ring, no new fault, no restart beyond
  the planned ones.

The run puts its own table in before anything is listened for, so that no
row of the unit's own tables decides what the chip is on.

## What it does not cover

- **A car.** The car is ECU sim v2 and the broadcast frames come from the
  PC's PCAN: four IDs at 20 frames a second each, the 29-bit one among the
  11-bit ones on the same bus at the same time. A car's bus carries a
  thousand frames a second and more. The window's receive filter is the
  chip's own; what a flood does to the monitor path is the subject of
  [atma_flood.md](atma_flood.md), not of this bench.
- **250 kbit/s.** The pair 8 and 9 is in the mapping
  (`ap_flt_window_proto()`, the host test `test_flt_window_proto`) and has
  not been on a bus.
- **A car whose own OBD protocol is the 29-bit one.** Leg f3b puts the
  chip there with a row's `ATSP7`; ECU sim v2 stayed set to its 11-bit
  answers.

## Records

**2026-11-14 04:23: `CAN FILTERS PASS`, 59 checks, 152 s, on the final
image of the day** (report `test-reports/2026-11-14_0423_canfilters.md`).
The same morning the AutoPID matrix bench ran on that code as the
window's regression: its filter leg green (the simulator's broadcast on
0x0C0 decoded, an ID nobody sends empty), see
[autopid_matrix_bench.md](autopid_matrix_bench.md) for its other legs.

**2026-11-14 04:05, `.\test.ps1 canfilters -DeviceId 14c19f457fd9`: `CAN
FILTERS PASS`, 59 checks, four planned restarts, 153 s** (report
`test-reports/2026-11-14_0405_canfilters.md`), on the build with the
legacy firmware's two window rules back (the formatting off, the protocol
of the ID's width). The 29-bit ID read within a second of the restart; its
test: `ATTP7`, `ATCRA18DAF110`, `ATCAF0`, `ATMA`, `18DAF110 11 22 33 44`,
`ATCAF1`, `ATCRA`, `ATTP6`; behind the `ATSP7` row the 11-bit values
followed the bus within a second. Stack headroom read on the unit after
filter windows and filter tests: the poller 9,208 B, the HTTP server task
7,224 B.

**2026-11-14 03:55, the same bench with `--no-page` on the build before
that one: 5 of 42 red, the reproduction.** Ali had asked how v4.50 handled
the filters: it tried protocol 7 for a 29-bit ID and 6 for an 11-bit one
(8 and 9 likewise) before the receive filter, and v6 did not. On that
build the 29-bit filter never got a value on this 11-bit car, its test
found no frame (no `ATTP` in the exchange), and behind the `ATSP7` row the
11-bit filters went stale: 60 and 7 stayed while the bus said 30 and 5.

**2026-11-14 03:39: `CAN FILTERS PASS`, 52 checks, 141 s**, the first
complete run (before the 29-bit legs existed).

**What the bench found on the way to that first run:**

- **Run 1 (03:21, 8 of 36 red), a defect older than this change.** The
  speed frame arrived as `02 1E 02` and the multiplexed pages as `01 28`
  and `02 50 00`: the window monitored with the OBD chip's CAN auto
  formatting on, which reads a frame's first byte as a length. `[B4:B5]`
  had no bytes, so the vehicle's filter never got a value. The frames of
  the earlier benches all started with a byte that is no valid length
  (they came whole, tagged `<DATA ERROR`). Fixed in `autopid_filter.c`:
  `ATCAF0` for the window, `ATCAF1` behind it, as v4.50 had it.
- **The same run:** its first tests ran on the unit's own tables, whose
  rows set `ATSP7`, and a test of an 11-bit ID found no frame while the
  chip sat on that protocol. Taken for the bench's own mistake then; it
  was the second thing v4.50 did and v6 did not (the record of 03:55).
- **Run 2 (03:30, 1 of 36 red), a defect of the new test route.** The
  multiplexed test answered `[40, null, null]` with both pages in its
  exchange: the handler let the poller go before it read the frames out,
  and the poller's next window took the same slots. The handler reads them
  first now.
- **Run 3 (03:35): PASS, 36 checks**, before the page leg was added.

## Traps

- The PCAN's two plugs are swapped on this bench at the moment. The bench
  listens before it sends, so it never puts 500 kbit/s frames on the
  simulator's MS CAN.
- A bench with broadcast frames should use first bytes 01 to 07 on
  purpose: a first byte that is no valid length hides the formatting
  defect of run 1.
- The log ring spans restarts: the bench compares it with what it held at
  the start instead of counting from zero.
- `test.ps1` holds port 18081 for its tunnel: a second session's tunnel on
  that port makes the stage stop at once.
