# OBD chip bring-up: the warm start and the PP 0F guard

Command: `.\test.ps1 obdbringup` = `python tools\testbench\obd\obd_bringup_bench.py --dut localhost:18081 [--restarts N] [--skip-pp]` (PC, the DUT through the tunnel, the console tailed beside it)

Needs: main fw on DUT + rpi001 + the `/ws/obd` bridge (`br_obd`, the canonical bench settings). AutoPID is left as found (on, the stored-protocol check after a restart is skipped and said so).

The index row and the other stages: [../TESTING.md](../TESTING.md). The benches that found both: `test-reports/logs/atz_soft_reset_2026-11-02/` and `test-reports/logs/pp0f_rewrite_2026-11-02/`; the command inventory: `internal/OBD_CHIP_COMMANDS_V6_VS_V4.md` (section 0.1).

## What it covers

Two bring-up changes of 2026-11-02 (Ali, after the comparison of v6 against 4.52p):

- **The warm start.** `chip_soft_reset()` sends `ATWS` instead of `ATZ`. The MIC3624's `ATZ` answer began with junk bytes (`00`, `00 F0 FD 00 00`, `BF 80 04`, `06 00 00 F8`) in 5 of 16 probes and a NUL among them ended the C string before `ELM`: about every third software restart since 2026-10-19 logged "soft reset not confirmed; hardware reset" and paid a reset pulse plus 1.5 s for a chip that had reset fine (10 of 17 captured boots). `ATWS`, what 4.x sent in place of every `ATZ`, answered clean 12 of 12 times in about 40 ms, and `obd_parse_extract` skips such bytes now as well.
- **The PP 0F guard.** PP 0F is the chip's Activity Monitor options register (default D5); 95 clears "allow wake from Low Power on activity", so the chip cannot wake itself on bus traffic while the ESP32 sleeps (legacy's `elm327_disable_wake_commands`, sent unconditionally after every chip firmware update). STSLCS does not report it, so the bring-up reads `ATPPS` and writes the pair back only when the value is not 95 or its enable is off (`eeprom_guard.pp_restores`). A chip firmware rewrite keeps the PP area (bench 2026-11-02), so on the stock images the write never happens.

| Leg | What is sent | Must read |
|---|---|---|
| as found | `GET /api/obd_chip`, `GET /api/obd_chip/report`, `GET /api/settings/autopid` | the chip ready and unclaimed; `ATPPS` reads `0F:95 N`; AutoPID's state noted |
| warm start (N planned restarts, default 3) | before each: `ATTP7` over `/ws/obd` (RAM only, no request, so no store write), `ATDPN`; the log ring cleared; `POST /api/restart`; after the boot the ring, `/api/obd_chip`, `ATDPN` over `/ws/obd` | `ATDPN` 7 before; after: `chip awake; warm start (ATWS)` and `started: chip ... memory off` in the ring, no `soft reset not confirmed`, no `chip not ready; hardware reset`; `bring-up done in` under 1500 ms; `ATDPN` back on the stored protocol (AutoPID off); `memory_off` 1, `pp_restores` 0 |
| PP 0F fault | the wire probe sends `ATPP0FOFF` (one EEPROM write: the enable flag); the report; a planned restart; the ring, the report, the counters; one more planned restart | the probe answered `OK`; `0F:95 F` before the restart; the ring carries `PP 0F reads 95 off (expected 95 on): restoring`; `0F:95 N` after; `pp_restores` 1; after the second restart no restoring line, `pp_restores` 0, the bring-up under 1500 ms (the guard is quiet on an intact chip). `--skip-pp` drops the leg |
| end | `/api/obd_chip`, the report | the chip ready, `0F:95 N`, 5 planned restarts |

The stage prints the bring-up lines the console tail caught (`chip awake; warm start`, `started: chip ... memory off`).

## Records

- 2026-11-02 01:08, the first run (bench DUT 68ee8f5a653d on dev-v6 at c87ce67 + the components tree's obd_chip change, chip MIC3624 V2.3.24, store 6, AutoPID off; `.\test.ps1 obdbringup`, report `test-reports/2026-11-02_0107_obdbringup.md`): `OBD BRINGUP PASS` (36 checks, 86 s, 5 planned restarts). The three warm-start restarts: `ATTP7` then `ATDPN` 7 before each; after each the ring read `chip awake; warm start (ATWS) instead of hardware reset` and `started: chip 'ELM327 v2.3' at 2000000 baud (ready=1, memory off)`, no `not confirmed`, `bring-up done in` 1079 / 1079 / 1080 ms (2147 ms with `ATZ` when it confirmed, about 5 s when it did not), `memory_off` 1, `pp_restores` 0, `ATDPN` back on 6. The PP 0F leg: the probe's `ATPP0FOFF` answered `OK`, the report `0F:95 F`, the restart's ring `W obd_chip: PP 0F reads 95 off (expected 95 on): restoring`, the report `0F:95 N`, `pp_restores` 1; the restart after it: no restoring line, `pp_restores` 0, 1080 ms. The console tail caught 11 bring-up lines.

## Traps

- The ring (`GET /api/logs/ring`, 16 KB) survives a software restart and is cleared by the bench before every one (`DELETE`), so the lines it reads are the new boot's alone.
- The PP 0F fault is written with `ATPP 0F OFF`, which the chip applies at its next reset: the restart is what makes the guard see `95 off`, and the guard's own `ATZ` after the restore is what re-applies 95. Three EEPROM writes of that parameter per run (rated for 10k).
- `ATTP7` before a restart proves the warm start drops the session: with AutoPID on the prelude would pin the profile's protocol right after the boot, so that check needs AutoPID off and the bench skips it otherwise.
- The tunnel is `ssh -L 18081:<dut>:80 rpi001`; `/ws/obd` goes through the same port.
