# What the image does on every vehicle bus (the baseline), and the chip memory snapshot

Commands:

- `python tools\testbench\obd\protocol_baseline_bench.py [--dut localhost:18081] [--only iso9141,vpw,...] [--issue1045] [--issue1045-poll]`
- `python tools\testbench\obd\chip_nvm.py snapshot|diff|check [--dut localhost:18081]`
- `python tools\testbench\lib\bench_bus.py only <bus>` (the simulator in a one-bus state by hand)

(PC, the DUT through an ssh tunnel `ssh -N -L 18081:<dut>:80 rpi001`, ECU sim v2 at its own address)

Needs: main fw on DUT + rpi001 + ECU sim v2 with every bus wired to the DUT's OBD connector + the `/ws/obd` bridge. The baseline prints `PROTOCOL BASELINE DONE` and a table: about 80 s per bus, 16 min for the twelve, two planned restarts of the DUT (its own CAN node off for the run and back), twelve restarts of the simulator, no write of the OBD chip.

The index rows and the other stages: [../TESTING.md](../TESTING.md). The siblings: [autobaud.md](autobaud.md) (the bus guard and bit rates), [profile_protocol_bench.md](profile_protocol_bench.md) and [protocol_save_bench.md](protocol_save_bench.md) (the chip's one protocol save), [attpstore.md](attpstore.md) (switches that must not write).

## What it is

A measurement, not a pass/fail of the product. The firmware knows the four ISO 15765-4 CAN protocols and nothing else; the OBD chip also speaks J1850 PWM and VPW, ISO 9141-2, KWP2000, MS CAN, CH CAN and single-wire CAN, and ECU sim v2 plays an ECU on each. Before anything is built for those buses this bench writes down what happens there on a given image. It FAILS only on what must hold whatever the product can do: the rig staged right, the unit rode through, **nothing wrote the OBD chip**, everything as found at the end.

Per bus, with the simulator in a ONE-bus state (`bench_bus.sim_only()`: the bus answers, every other bus is off, as on one car; with every bus live an automatic search settles on whatever it tries first):

| Step | What is done | What is recorded |
|---|---|---|
| app path | an ELM app over `/ws/obd`: reset, echo off, headers on, `ATM0`, `VT SHOW_BUS`, the protocol by its preset (`ATSPn` as an app sends it, `STP nn`, `VTP 2Cn`), then `0100`, `010C`, `010D`, `0902`, `03` | answered or not, the time of the first request (it carries the bus's start-up) and of a later one, the replies as the chip prints them |
| the chip's own search | from a reset: `ATSP0`, `0100` | found or not, how long, what `ATDPN` says |
| firmware | `POST /api/autopid/test`: `010C`, the preset as the row's init, the standard expression `[B3:B4]*0.25`, twice | ok or the error, the time, the bytes the expression saw, the value (the simulator's 800 rpm) |
| the chip | `chip_nvm`'s snapshot against the one of the start | SAME (a check), and the firmware's guard counters |

`--issue1045` adds, on the ISO 9141-2 leg, the calls of GitHub issue #1045: the Test button's (`010C1`, init `ATSP3;`, three times) and the detection job. `--issue1045-poll` adds AutoPID polling with `std_init` `ATSP3;` and is NOT part of a plain run: a poll that answers lets first contact identify the car, and a car it takes for a new one gets its protocol saved in the chip (one write, and one more to put it back).

## The chip memory snapshot (`chip_nvm.py`)

Reads every stored cell of the chip that can be read, through the app path, and writes nothing: the identity strings (`ATI`, `AT@1`, `AT@2`, `STDI`, `STI`, `STMFR`, `VTI`, `VTD`, `VTPROI`, `VTVERS`, `VT RD_MAC`, `VT RD_UDS`, `VT RDSN`), the programmable parameters (`ATPPS`), sleep and wake (`STSLCS`, `VTPPSWS`, `VT POWERMANAGE`), the user byte and the 256-byte user area (`ATRD`, `VT RD_EE`), the three protocol tables with their wake-up sequence links (`VT AT_PROTOCOL_ALL`, `VT ST_PROTOCOL_ALL`, `VT VT_PROTOCOL_ALL`), the sixteen wake-up sequences (`VT DISP_CAN_WM n`, `VT DISP_ISO_WM n`), and the stored protocol. 40 cells; `diff` names every cell that reads differently; `check` reads all of them twice and wants them equal. It is the bench's write detector whoever wrote: the firmware counts only its own deliberate writes.

## Records

**2026-11-11 01:39 to 01:55, bench DUT 44bd8d73ca79, chip MIC3624 V2.3.24, image sha256 `359d87ed...` (ELF `3473a54d3e458898`, the trees of that night): `PROTOCOL BASELINE DONE`, 12 buses, 47 checks, 0 failed, with `--issue1045`. Nothing wrote the chip on any leg (40 cells, the store `6`).** Logs `test-reports/logs/2026-11-11_phase0/`.

| Bus (the preset) | The chip, through the app path: first `0100`, a later request | The chip's own search | The firmware's Test path | The value (800 rpm) |
|---|---|---|---|---|
| ISO 15765-4 CAN 11/500 (`ATSP6`) | 555 ms, 0.49 s | found, 6.6 s (`A6`) | ok, 617 ms | right |
| ISO 15765-4 CAN 29/500 (`ATSP7`) | 548 ms, 0.50 s | found, 6.7 s (`A7`) | ok, 616 ms | right |
| ISO 15765-4 CAN 11/250 (`ATSP8`) | 552 ms, 0.49 s | found, 6.8 s (`A8`) | ok, 618 ms | right |
| ISO 15765-4 CAN 29/250 (`ATSP9`) | 552 ms, 0.49 s | found, 6.9 s (`A9`) | ok, 617 ms | right |
| MS CAN 125k, pins 3 and 11 (`STP 53`) | 550 ms, 0.49 s | NOT found, 7.5 s | ok, 616 ms | right |
| CH CAN 500k, pins 12 and 13 (`VTP 2C3`) | 556 ms, 0.49 s | NOT found, 7.5 s | ok, 107 ms | right |
| single-wire CAN 33.3k, pin 1 (`STP 63`) | 562 ms, 0.51 s | NOT found, 7.5 s | ok, 113 ms | right |
| ISO 9141-2, 5-baud init (`ATSP3`) | 3330 ms (`BUS INIT: ...OK`), 0.77 s | found, 4.7 s (`A3`) | **`request failed/timeout` at 1500 ms** | - |
| ISO 14230-4 KWP, 5-baud init (`ATSP4`) | 3304 ms, 0.74 s | found, 4.7 s (`A4`) | **`request failed/timeout` at 1500 ms** | - |
| ISO 14230-4 KWP, fast init (`ATSP5`) | 1219 ms, 0.74 s | found as `A4`, 4.7 s | "ok", 1179 ms | **wrong: 15428** |
| SAE J1850 VPW (`ATSP2`) | 566 ms, 0.50 s | found, 7.5 s (`A2`) | "ok", 683 ms | **wrong: 6852** |
| SAE J1850 PWM (`ATSP1`) | 559 ms, 0.50 s | found, 7.0 s (`A1`) | "ok", 624 ms | **wrong: 6852** |

What that says about this image:

- **The chip talks on all twelve.** Every bus answered the five requests through the app path with its preset and an acknowledged `ATM0`. Single-wire CAN needed no transceiver word (`STCSWM`, `ATTM`): the preset carries its mode.
- **The chip's own search knows the nine ELM protocols only.** It finds each in 4.7 to 7.5 s and never MS, CH or single-wire CAN (`UNABLE TO CONNECT` after 7.5 s). On the KWP ECU it settles on the 5-baud variant (`A4`) whichever init the ECU also takes.
- **MS, CH and single-wire CAN already work through the firmware's Test path** when the preset is the row's init (`STP53;`, `VTP2C3;`, `STP63;`): the reply is a CAN reply and the standard expression decodes it.
- **A protocol with a slow start fails in the firmware.** The first request on ISO 9141-2 and on KWP with the 5-baud init takes 3.3 s; the request timeout is 1.5 s for every protocol (`AP_REQ_TIMEOUT`). The Test path fails every time, and each try starts the bus again.
- **A pre-CAN reply decodes wrong, and the Test path says ok.** With the car's CAN prelude in effect (headers on) a K-line or J1850 reply is printed with its three header bytes and its checksum (`84 F1 10 41 0C 0C 80 5E`, `48 6B 10 41 0C 0C 80 02`, `41 6B 10 41 0C 0C 80 8D`). The reply parser takes the eight bytes for data and rebuilds an ISO-TP frame around them (`10 08 84 F1 10 41 0C 0C 21 80 5E ..`), so `[B3:B4]*0.25` reads 15428 or 6852 where the engine turns 800. A poll would drop such a reply (its echo check); the Test button shows the number.
- **Replies as printed, for the reply layout to come:** ISO 9141-2 and VPW `48 6B 10 <data> <checksum>`, PWM `41 6B 10 ..`, KWP `8n F1 10 ..` with the length in the first byte; the VIN (`0902`) in five messages on ISO 9141-2, VPW and PWM (`48 6B 10 49 02 01 00 00 00 31 40` ...) and in ONE message on KWP (`94 F1 10 49 02 01 31 57 43 ..`); three trouble codes per `03` message without a count byte on ISO 9141-2 and J1850, with one on KWP.
- **A request without the response-count digit costs about 0.75 s on a K-line** (the chip waits for further ECUs) and 0.5 s elsewhere; the issue's `010C1` took 188 ms on the car.
- **`VT SHOW_BUS` reads `BUS SILENT` on every bus**: the simulator's buses are quiet until asked, like a K-line or a J1850 bus on a car. The chip cannot tell such a bus by listening.

**GitHub issue #1045, its v6 half, reproduced on the ISO 9141-2 leg:** the Test button's call (`010C1`, init `ATSP3;`) `request failed/timeout` three times at 1499 to 1500 ms, the transcript ending `> 010C1`, `< (no reply)`; the detection job `failed` after 3 s with `no ECU response (ignition on?)` (it walks the four CAN protocols and never tries a K-line); the vehicle store untouched. The same request through the app path is answered in 3.3 s. The polling step was not run.

## Findings on the way (2026-11-11)

- **An ST or VT protocol preset survives the firmware's warm start.** After `STP 53` the chip reads `ATDPN` `53 [ST]`, and still does behind `ATWS` (an app's `ATZ`, the bring-up's reset). A temporary AT protocol does not: behind `ATTP1` or `ATTP2` the warm start comes back on the stored protocol. So after a session on MS, CH or single-wire CAN the bring-up's read of the stored protocol finds `53 [ST]`, which it does not take for a stored protocol (`stored_protocol` empty: the next save cannot know the chip holds it already), and the chip stays on that preset until the first switch. The snapshot tool reads the store behind `ATTP3` and again behind `ATTP5`; its first version read it behind the preset and reported three writes that had not happened (the store read `6` at the next bring-up and in every later snapshot).
- **The chip's UART loses bytes on a long output.** An answer of about 500 bytes and more (`ATPPS`, the protocol tables) arrives cut now and then: 3 of 24 long reads in one series, 7 `rx overflow (fifo) - input flushed` lines in an hour of this bench (`uart.rx_overflows`). It is the hardware FIFO, not the 18 KB ring: at 2,000,000 baud its 128 bytes fill in 640 µs, and the driver leaves the FIFO-full threshold at its default (120 bytes), which gives the interrupt 40 µs. Short OBD replies sit under the threshold and are not affected. FIXED the same day in the components repo (the driver is called at 32 bytes and reports the fullest it found the FIFO): the reproduction, the measurement and the records are in [chip_uart_burst_bench.md](chip_uart_burst_bench.md). `chip_nvm` still reads a word again when the counter moved, for an image before the fix. With the same work its reader stopped taking the `>` inside `STSLCS` (`VL_WAKE: OFF, >13.20V FOR 1 s`) for the prompt: that cell was stored without its last two lines in the snapshots of this page's run (251 of 302 bytes), so a change there would not have shown.
- **The J2534 J1850 VPW channel does not reach a VPW car (reproduced 2026-11-11, fixed the same day).** The channel's init sends `ATTP2` and then a list that begins with `ATD` (`j2534_channel_legacy.c`, the components repo). `ATD` puts the chip back on its stored protocol: over the app path, with the simulator on VPW alone, `ATTP2` then `0100` reads `48 6B 10 41 00 18 3F 80 03 0F` and `ATDPN` reads `2`; behind `ATD`, `ATDPN` reads `6`. So `python tools\testbench\usb\j2534_vpw_bench.py` against the unit (its J2534 server switched on for the run) connects, logs `J1850 VPW channel up`, has its write accepted and gets no reply, twice of twice (`J2534 VPW SKIP`, whose text still blames a CAN-only simulator), and the chip reads `ATDPN` `6` afterwards: the request went out on CAN. The reply check of that bench had never run against a VPW node. No write of the chip in it (the snapshot the same, 40 cells; `protocol_saves` 0). FIXED the same day (`ATD` ahead of `ATTP2`, and three ST pass filters that let no reply through removed; the bench is the stage `.\test.ps1 j2534vpw` now): [j2534_vpw_bench.md](j2534_vpw_bench.md).

## Traps

- **An ssh tunnel wedges under many short connections.** The first whole run stalled after 25 minutes: a snapshot then made 80 HTTP connections, twelve of them a run, and the Windows ssh client stopped forwarding. The unit was fine (it answered from rpi001 all along) but the last leg and the restore did not run, and the unit's CAN node setting had to be put back by hand. `chip_nvm` keeps ONE connection open for its counter reads since; a new tunnel per long run is cheap insurance. The bench stops at the next leg when the unit does not answer.
- **The simulator's one-bus state is not its default.** It boots with every bus live; `sim_only()` restarts it (about 8 s) and the bench puts its settings back at the end. A run cut short leaves it on one bus: `python tools\testbench\lib\bench_bus.py sim --restore` covers the HS CAN only, the bench's own restore (or the simulator's page) the rest.
- **`kwp5` and `kwpfast` are the same simulator state** (its KWP ECU answers both inits); the adapter picks the init with the preset.
- The detection job of `--issue1045` cannot store anything on a K-line today. When the firmware learns pre-CAN protocols it will, and the step then needs the bench's own car (see [profile_protocol_bench.md](profile_protocol_bench.md)) so that the unit's car is not touched and the save is counted.
