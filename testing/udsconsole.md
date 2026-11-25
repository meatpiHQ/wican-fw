# udsconsole: a long UDS answer whole, on every path

Command: `.\test.ps1 udsconsole [-SkipChip]` (PC; it opens the tunnel and runs `python tools\testbench\obd\uds_console_bench.py [--dut localhost:18081] [--skip-chip]`)

Verdict line: `UDS CONSOLE PASS` (47 checks; 26 with `-SkipChip`). About 2.5 minutes. Two planned restarts for the chip leg (four when Automate was off at the start), none with `-SkipChip`. No chip write, no console port, no PCAN.

Needs: the main firmware on the DUT on the bench AP with its `cli` bridge to `/ws/cli` (a default connection), the ECU simulator answering 7E0 on 7E8 with UDS on, Automate on.

The report (2026-11-26, a user on 6.0.0-beta.15): "the uds command cuts the response at about 80 bytes".

## What it covers

The ECU is the simulator's own. Its synthetic memory (the byte at address A is A & 0xFF) gives an answer of any size that is checked to the byte (`23 14 <addr> <size>`, `23 24 ..` above 255 bytes), and its WriteMemoryByAddress (`3D 14 ..`) answers `7D` only when every byte of the request arrived.

| Leg | What is checked | What was wrong before |
|---|---|---|
| L0 preflight | the unit and `/ws/cli` answer, no app holds the chip, the tables saved, the ECU serves its memory | - |
| L1 the answer whole | the same request through `POST /api/uds/request` and through `uds -t 7E0 -r 7E8 <request>` on the console, answers of 7, 20, 84, 85, 128, 129, 201, 512, 513, 1024 and 4095 bytes on the path as found: the console's bytes are the route's, the line ends in `[<transport>, N ms]`, `positive (SID 0x63)` follows, and the `cli` bridge's loss counters did not move | the console's line ended after the 84th byte, without `[transport, ms]` (one `cmdline_printf`, a 256-byte buffer); the command showed 128 bytes at most; an answer over 512 bytes was an error. On the native path an answer of 1024 bytes or more was `ESP_ERR_TIMEOUT` on the route as well (P2, 250 ms, was the time for the whole answer) |
| L2 the request as typed | 7 bytes with every byte a word of its own, as `0x` words, as one `:` word and as one word: the 201-byte answer each time; 64 bytes with every byte a word: the ECU's `7D`; 65 words, one word of 65 bytes and one `:` word of 70 bytes: refused, and the last transaction on `GET /api/uds` only got older | the console kept 11 words of a line: a request of more than 6 bytes typed as bytes went out without the rest (`7F 23 13` from the ECU); 65 words went out as their first 6; the 70-byte word went out as its first 64 bytes |
| L3 the OBD chip behind a row that moved the flow control | the UDS transport set to the chip (a planned restart), one custom row `0105` with the init `ATSH7E0;ATCRA7E8;ATFCSH7E4;ATFCSD300000;ATFCSM1` polled every 250 ms. With the exclusive switch off: 20 reads of the VIN (20 bytes, three frames), each sent right behind a poll of the row (the bench waits for `polls_ok` to move, and counts it). With it on: answers of 20, 61, 201, 1024, 2400 and 4095 bytes, each the first request of a new conversation; then 201 and 4095 bytes through the console command. The row still reads 90 afterwards | the row's flow control stayed in effect under the UDS setup: the chip sent `30 00 00` to the row's ECU, the asked ECU sent its first frame and nothing more: 0 of 20. Behind that: a cut answer was asked for again 0.9 ms later, while the ECU still sent the rest of the first, and was cut again; the reply buffer held 8 KB of the chip's text (about 2290 bytes of answer); the parser refused the one message of 4095 bytes |
| L4 restore | the tables, the exclusive switch, the UDS settings (a planned restart), Automate's when they were changed | - |

Why the sizes: 84 and 85 are the last byte that fit the old console line and the first that did not; 128 / 129 and 512 / 513 the command's two old limits; 1024 the first size past P2 on the native path (291 ms); 2400 the first past the old 8 KB reply buffer on the chip; 4095 the largest ISO-TP message.

## Records

All on the bench unit 14c19f457fd9, 2026-11-26. The builds differ in the components tree alone.

- **Red: 6.0.0-beta.15** (`v6.0.0p-beta.15-4-g9dbb725-dirty`, the bench script by hand before the stage existed):
  - by hand, native path: `POST /api/uds/request` with `23 14 00 00 00 00 C8` answered 201 bytes; `uds -t 7E0 -r 7E8 231400000000C8` on `/ws/cli` printed `<- 63 00 01 .. 51 52 ]`, 84 bytes and no `[isotp, N ms]`; the same request typed as `23 14 00 00 00 00 C8` printed `<- 7F 23 13`.
  - the bench: 27 of 46 checks failed. L3: 0 of 20 reads, every size `ESP_ERR_INVALID_RESPONSE`. L1 on the route: 1024 and 4095 bytes `ESP_ERR_TIMEOUT`. L2: the 64 and the 65 words answered `7F 3D 13`, the 70-byte word `7E 00`. (L1 typed its request as bytes in that run, so its console failures there are the lost words; the cut line is the record by hand above.)
  - a passive capture of the bus (PCAN, listen only) with the car profile's own rows polling: `7E0 03 22 F1 90`, `7E8 10 14 62 F1 90 31 57 43`, then `710 30 00 00` (the flow control, to the ECU of the row polled last), and `7E0 03 22 F1 90` again.
- **Between** (the console and the flow control fixed, the native path's wait fixed, nothing else on the chip path): L1 and L2 green, L3 6 of 20. A capture: `7E0 30 00 00` at 3.159 ms, the request again at 4.095 ms, the first answer's `21 ..` and `22 ..` at 4.3 and 4.6 ms, the second answer's first frame at 4.8 ms, and so three times. With the wait in front of the second request (`test-reports/2026-11-26_0157_udsconsole.md`): 45 of 46, 4095 bytes on the chip `ESP_ERR_INVALID_SIZE`; with the 16 KB reply buffer (`2026-11-26_0202`): 2400 bytes pass, 4095 `ESP_ERR_INVALID_RESPONSE` (the parser and the padding).
- **Green: the fix**, 02:07, through the runner (`.\test.ps1 udsconsole -DeviceId 14c19f457fd9`): `UDS CONSOLE PASS`, 47 checks, 144 s (`test-reports/2026-11-26_0207_udsconsole.md`). Native path: 4095 bytes on the route and on the console in 1164 ms, 1024 in 294 ms. OBD chip: 20 of 20, 20 bytes in 30 ms, 201 in 119 ms, 1024 in 391 ms, 2400 in 836 ms, 4095 in 1390 ms. Behind it `uds_route_bench.py --n 30 --expect-backend isotp`: `UDS ROUTE PASS`, 30 of 30, median 2 ms; the unit's `log_errors` 0, no fault, no unexpected restart.
- Host: `.\test.ps1 host -Component cmdline_manager` 18 tests, `-Component uds_manager` 36 tests, `-Component can_isotp_esp` 6 tests, all pass.

## Traps

- The build string does not tell two builds of this work apart (the firmware repo's commit is the same): tell an uploaded image by the slot it runs from (`partition` on `/api/info` flips) or by its size.
- L1 types the request as ONE word on purpose: typed as bytes, the old console's lost words hid the cut line behind a `7F 23 13`. The typed forms are L2's.
- The chip leg's first part needs the poller to write to the chip between two requests, which is why it switches the exclusive option off at runtime (`POST /api/uds`) and counts the polls in front of every request: with the option on, the poller is held for the whole conversation and only its first request finds the row's setup.
- With the unit's own car profile instead of the bench's one row, the chip leg fails or passes by which row was polled last (a row with `ATFCSM1`, or a standard row behind which autopid put the flow control back). The first look at this fault read 16 of 16 failed, then 4 of 8.
- A script's `uds()`, `obd_request()` and `obd_isotp_rx()` had a limit of their own (128 bytes handed over, `nil` above 512) and were fixed the same day; they are not part of this stage, because the script engine is off by default: [be_uds_bindings.md](be_uds_bindings.md).
- The last run of the day on the final image (with the script change in): `test-reports/2026-11-26_0225_udsconsole.md`, `UDS CONSOLE PASS`, 47 checks.
