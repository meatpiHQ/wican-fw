# Stream 2 — CAN + OBD-II PID CSV Logger

## Status: MVP implemented (2026-06)

A deeper audit found that most of this plan already existed in the codebase:
the `autopid` component already interleaves passive STN CAN-filter monitoring
(`can_filter_t`, Pattern B below) with active PID polling through the single
STN/UART1 path, serialized by its own lock (`autopid_lock`). So instead of
building `broadcast_capture` / `pid_poller` / `adapter_owner` from scratch,
the MVP is a thin CSV sink:

- `components/csv_logger/` — queue + writer task. CSV to `/sdcard/logs/`,
  one file per ignition cycle, 8 MB rotation, ~1 s flush interval.
- Single hook in `autopid_prepare_parameter_value()` captures all three data
  paths (CANFLT broadcast, custom/specific PID, standard PID) as decoded
  `name,value,unit` rows. `raw_hex` from the original format is deferred —
  decoded values come free from autopid; raw frames do not.
- Ignition gating via `vehicle_ignition_state()` with a 3 s off-debounce so
  cranking dips don't split files. SD writes never block PID polling (records
  are dropped, and counted, if the queue backs up).
- Enabled via **CSV Datalog (SD)** in Logger Settings (`csv_log` config key,
  default disable). Docs: `docs/content/0.Config/7.CSV-Datalogger.md`.

The `adapter_owner` mutex is not needed yet: when Stream 1 (ECU flashing)
lands it should pause AutoPID (e.g. `DEV_AUTOPID_ENABLED_BIT` or
`autopid_lock`), which automatically silences this logger since no parameters
flow while AutoPID is idle.

Original parked design below, kept for reference.

---

Parked design doc. Stream 1 (ECU flashing) ships first; this branch holds the
investigation and plan so Stream 2 can resume cleanly later.

## Goal

Log, to an SD-card CSV file, two interleaved streams from a 2009 Mazda NC Miata:

- **Passive broadcast frames** on the vehicle CAN bus (RPM, load, coolant,
  throttle, VSS, etc. — whatever the PCM emits on `ATMA`-visible IDs).
- **Active OBD-II PID polls** (`Mode 01 <PID>`) for values not on the broadcast
  bus or sampled at a higher rate than the broadcast.

Output format: plain CSV (Tactrix OpenPort-compatible — no RR3 / proprietary
framing). One unified file per ignition cycle.

## Hardware constraints (audited on `upstream/wican-pro`)

- **Single CAN path.** ESP32-S3 TWAI pins exist but are not wired to the OBD-II
  connector on WiCAN PRO. The STN OBD chip on UART1 is the only path to the
  car's bus. Evidence: `main/hw_config.h:31-33` (no TWAI pins defined for
  WICAN_PRO), `main/main.c:905-942` (TWAI init commented out on the OBD paths),
  `main/elm327.c:1575-1597` (all traffic via `uart_write_bytes(UART_NUM_1,...)`).
- **Consequence:** passive sniff + active poll cannot run on two parallel
  channels. They must serialize through the STN chip.
- **SD is mounted and usable.** `main/sdcard.c` mounts `/sdcard` via
  `esp_vfs_fat_sdmmc_mount`. Public API in `main/sdcard.h`.
- **Ignition detection exists.** `vehicle_ignition_state()` in
  `main/vehicle.c:33` returns `VEHICLE_STATE_IGNITION_ON/OFF` from a voltage
  threshold (default 13.1 V, configurable). ADC read via
  `sleep_mode_get_voltage()` at `main/sleep_mode.c:615`.

## Transport strategy

Because there is only one path to the bus, broadcast capture and active polls
interleave through the STN chip. Two viable modes, both driven via
`elm327_run_command()` in `main/elm327.c`:

### Pattern A — `ATMA` (monitor all) + poll windows

1. `STN → ATMA` (monitor all frames). Stream UART output to parser.
2. When it's time to poll a PID, break out of monitor (`\r`), send
   `01 <PID>`, read response, re-enter `ATMA`.
3. Hole in broadcast capture ≈ 50–200 ms per poll.

Use when: you want everything on the bus and don't have a known-ID broadcast
list.

### Pattern B — filter + poll (recommended)

1. STN filter/header commands (`ATSH`, `ATCRA`, `STM` / `STFAP`) select specific
   broadcast IDs the logger cares about (RPM 0x201, load 0x420, etc. — operator
   supplies list).
2. STN streams only matching frames to UART1 while still accepting active
   requests.
3. Poll PIDs in a round-robin with configurable per-PID rate.

Use when: list of broadcast IDs is known (it is, for NC Miata).

**Recommendation: Pattern B.**

## Runtime gating

Logger is active only when **both** are true:

- `vehicle_ignition_state() == VEHICLE_STATE_IGNITION_ON`
- No active NC Flash session (i.e. no client currently holding the adapter
  for flashing / reading ROM / reading RAM / DTC ops).

Flashing is sacred — logger must stop and release the STN chip the instant a
flash session begins.

Implementation sketch:
- A single `adapter_owner` mutex in `main/elm327_bridge.c` (new).
  - Owners: `LOGGER`, `NC_FLASH`, `MQTT_BRIDGE`, `NONE`.
  - `NC_FLASH` preempts `LOGGER`. `LOGGER` yields on acquire.
- `logger_task` wakes on ignition-on event, acquires owner as `LOGGER`, runs.
  On ignition-off or owner preemption, flushes CSV and sleeps.

## CSV format (proposed)

```
timestamp_ms,source,can_id,pid,raw_hex,value,unit
12345,bcast,0x201,,08 1A 0F 00 00 00 00 00,1546.0,rpm
12350,poll,0x7E8,0x05,41 05 5A,90.0,c
12355,bcast,0x420,,7F 02 55 00 00 00 00 00,,
```

- `timestamp_ms`: monotonic, ms since logger start.
- `source`: `bcast` (passive) or `poll` (active).
- `can_id`: hex, upper-case, `0x` prefix.
- `pid`: hex, `0x` prefix, empty for broadcast.
- `raw_hex`: space-separated data bytes.
- `value`, `unit`: decoded if a decoder is wired, else blank (operator-friendly
  downstream — a Python post-processor can fill in decoders).

File naming: `/sdcard/logs/<YYYYMMDD_HHMMSS>.csv`. Rotate at 8 MB or on
ignition-off. RTC comes from SNTP when online, else monotonic fallback with an
`unknown_time_` prefix.

## Files to create

- `main/logger.c` / `main/logger.h` — task, CSV writer, rotation.
- `main/broadcast_capture.c` / `main/broadcast_capture.h` — Pattern B filter
  setup + UART parser.
- `main/pid_poller.c` / `main/pid_poller.h` — round-robin `Mode 01` scheduler.
- `main/adapter_owner.c` / `main/adapter_owner.h` — mutex + preemption.
- Config entries in `main/config_server.c` — enable/disable, PID list,
  broadcast ID list, poll rates.

## Open questions

- Exact broadcast ID list for NC Miata PCM (operator-supplied; not blocking).
- Exact PID poll list + per-PID rate (operator-supplied; not blocking).
- Whether to include decoded `value` / `unit` in v1 or ship raw-only and
  decode offline (recommendation: raw-only in v1).

## Estimate

~2 days once Stream 1 ships and the `adapter_owner` mutex design is validated
against Stream 1's flash-session lifecycle.

## Related

- Stream 1 lives on `feature/nc-flash-pro`. This branch stays parked until
  Stream 1 merges to `wican-pro`.
