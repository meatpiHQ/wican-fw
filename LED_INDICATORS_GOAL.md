# LED Activity Indicators — Goal Document (issue #19)

Branch: `feature/led-activity-indicators` · Issue: [#19](https://github.com/cdufresne81/nc-flash-wican-fw/issues/19)

## Status: implemented & bench-verified (2026-07-10)

Implemented in `8afb11c`; all acceptance criteria demonstrated against the
bench unit (192.168.1.169, build `v1.4.0-2-g8afb11c`). Notable results:

- **AC8**: dry-run flash (3584 blocks, 8.4 s) showed `flash_red` for the whole
  op. The LED then returned to `datalog_blue`, not `idle` — correct: the bench
  had a live CSV session open, so this run also proved the red>blue overlap
  arbitration (bare port-35001 command with no `op=pause`) end to end.
- **AC9**: session open (396 rows) → `datalog_blue`; `op=pause` → `idle`;
  `op=resume` restored auto mode.
- **AC10 nuance**: `/store_config` always schedules a reboot (by design, all
  keys), so "applies without reboot" is moot in the stock flow — the indicator
  does read the getter at every pattern program (`led_indicator.c`, task loop),
  so the value is live-applied from whatever `device_config` holds. Persistence
  verified: 510 survived the reboot; restored to the 260 default after.
- The old bench config.json predated the key; `/load_config` (raw file) showed
  no `led_blink_ms` while the boot parser correctly defaulted it — the snap
  coercion path is exercised on every legacy device.
- A 3.5 MB `ledtest.bin` + `ledtest.json` remain staged in `/sdcard/roms/` on
  the bench unit (reusable for future dry-run tests).

The manual eyes-on checklist below remains open (needs a human at the device).

Ready-to-paste goal condition:

```
/goal Implement LED_INDICATORS_GOAL.md on branch feature/led-activity-indicators until every acceptance criterion AC1-AC10 in that document is demonstrated in this session with its stated check shown in the output, and every constraint in that document still holds — or stop after 40 turns.
```

## Goal

Make the WiCAN PRO RGB status LED show what the device is doing:

1. **Fast-blink RED** while a flashing operation is executing (ECU fast-write,
   live or dry-run, and ECU fast-read — anything holding `FLASH_ACTIVE_BIT`).
2. **Fast-blink BLUE** while data logging is active (a CSV session file is open
   and the datalogger is not parked).
3. **User-controllable blink rate**: a slider in the web UI sets the flash rate
   of both indicators, persisted as a config key.
4. When no indicator is active (and the device is awake), the LED returns to the
   canonical idle state: solid blue `(0,0,200)` — the same state `app_main`
   sets at end of boot. Nothing may leave the LED unintentionally dark.

## Audited facts (verified 2026-07-10, multi-agent pass + adversarial re-check)

**LED driver** — AW2023 3-channel I2C controller at addr 0x45, wrapped by
`main/led.c`. Each color has its own PWM register, pattern-timing registers and
a pattern-mode (MD) bit, so red and blue hardware patterns can run
simultaneously (`main/led.c:35-46,168-193`). Pattern times are quantized to a
16-entry table — 0, 130, 260, 380, 510, 770, 1040, 1600 … 8300 ms; the minimum
nonzero step is **130 ms** (`main/led.c:61-78`). `led_get_actual_time_ms()`
exposes the quantization (`main/led.h:68`).

Driver hazards the design must absorb:

- `led_fast_blink(color, br, true)` programs hold=130 ms but **off=0 ms**
  (`main/led.c:219-221` — the comment claims 130 ms off; the code writes 0, so
  it may render near-solid, not blinking) and zeroes the other two channels'
  PWM (`main/led.c:233-238`). Disable blanks **all three** channels with no
  save/restore (`main/led.c:246-250`). Do not reuse it as-is.
- `led_set_level` never touches the MD bit (`main/led.c:142-144`), so a channel
  left in pattern mode keeps blinking whatever steady color is written later —
  every indicator exit must explicitly `led_disable_pattern()` per channel.
- `main/led.c:23` redefines `LED_I2C_TIMEOUT_MS` to **1000 ms** (led.h says
  100). A blink enable is ~8 I2C transactions → worst-case seconds of blocking.
  **No LED I2C may run on `can_tx_task`**, which dispatches the flash codecs
  inline (`main/main.c:263-291`).
- `led.c` keeps no state and has no mutex; today six modules write the chip
  directly, last-writer-wins.

**Flash lifecycle** — both codecs set a busy flag + `can_flash_active_set()` on
entry and clear on **every** exit path through a single cleanup label:
`main/ncflash_fastwrite.c:355-358` / `:537-571`, `main/ncflash_fastread.c:264-267`
/ `:389-417`. Dry-run (`WD`) holds the bit exactly like live (`WL`); the
fast-read version ping never sets it (`main/ncflash_fastread.c:241-250`).
`can_flash_active()` is therefore a complete, reliable predicate
(`main/can.c:170-186`). Caveat: hosts split large reads into ~128 KB `X`
commands, so the bit drops and re-sets between chunks of one logical read —
the red indicator needs off-hysteresis to avoid flicker.

**Datalog lifecycle** — the authoritative "logging is happening" state is
`csv_session_active` in `components/csv_logger/csv_logger.c:97` (private
static): set true at exactly one site (file opened, `:690`, emits
`EVL_DATALOG_OPEN`) and false at four sites (`:599,:617,:711,:738`, each emits
`EVL_DATALOG_CLOSE`). Sessions open lazily on the first record. A cooperative
host pauses logging before flashing (`op=pause`, `csv_logger.c:1188-1191`), but
a bare `op=bus_claim` (`:1214-1224`) or a direct port-35001 command leaves the
session open while `FLASH_ACTIVE_BIT` is set — **red+blue overlap is a real
case**, and red must win. `can_should_park()` (`main/can.c:204-207`) is the
flash-or-park-or-claim predicate.

**Existing LED states** (the indicator layer must coexist with all of these):
boot-complete solid blue `main/main.c:1153-1154`; safemode light-blue/yellow
`main/main.c:533,542`; config/AP mode rewrites all three PWM levels every 1 s
`main/config_mode.c:59-64`; MIC3624 OBD-chip update red fast-blink
`main/elm327.c:2918,3065` which **leaves the LED dark** on completion
(`:3029,:3143`); sleep paths write `(0,0,0)` `main/sleep_mode.c:825,966`;
boot-loop red breathing `main/sleep_mode.c:1062-1072`; console `led` command
`components/cmdline/cmd_led.c:40-130`.

**Infrastructure available**: `dev_status` bits BIT21–BIT23 are free
(`main/dev_status.h:60-62`); `config_mode_task` is the existing idiom for a
1 s-tick LED task gated on `DEV_AWAKE_BIT` (`main/config_mode.c:47-91`);
config keys follow the `csv_log`/`log_period` pattern (`main/config_server.c:205`
default JSON, `:2596-2613` parse+coerce, `:3377` getter); the web UI already
has `type="range"` sliders; UI source of truth is `main/web/homepage_full.html`
+ `main/web/src/main.js`, regenerated by `tools/build_web.py` and checked by
`tools/lint_web.py` (see `README.md:84`).

## Design

### New module: `main/led_indicator.c` / `.h` — single LED owner for indicators

A small task (its own task, ~250 ms tick, gated on `DEV_AWAKE_BIT` like
`config_mode_task`) that **polls** state and owns all indicator LED I2C:

- Red source: `can_flash_active()` — covers live flash, dry-run, and fast-read
  with zero changes to the codecs. Off-hysteresis: red is held ~1.5 s after the
  bit clears so chunked fast-reads don't flicker.
- Blue source: `csv_logger_session_active() && !can_should_park()` — requires a
  new one-line getter in `components/csv_logger` exposing `csv_session_active`.
  Blue is off while parked/claimed, and off during flash (red wins).
- Priority: **red > blue > idle**. On every state transition the task disables
  the pattern on channels it is leaving (MD-latch hazard) and programs the new
  state. Entering idle restores solid blue `(0,0,200)`.
- Deference: the task does nothing (and relinquishes the LED to idle-restore on
  exit) while config/AP mode is active — expose the existing `in_config_mode`
  state or a dev_status bit rather than fighting its 1 Hz rewrite. Safemode,
  boot-loop breathing, and sleep paths run in states where the indicator task
  is not ticking (pre-init or `DEV_AWAKE_BIT` cleared) and are left untouched.
- Blink pattern: explicit `led_set_pattern_ms` with `hold = off = <rate_ms>`
  (not `led_fast_blink`, whose off-time is 0). Brightness fixed: red 255,
  blue 200.
- MIC3624 update fix (small, in scope): after `elm327_update_obd*` disables its
  red blink, the LED currently stays dark; route the restore through
  `led_indicator` (or have the indicator task repaint idle on its next tick) so
  the LED ends at idle blue.

### Config key + UI slider: `led_blink_ms`

- New `device_config` field `led_blink_ms`, default `"260"` (260 ms on /
  260 ms off ≈ ~1.9 Hz — visibly "fast" while unambiguous), following the
  `log_period` string-key pattern: default JSON entry, parse, coercion of any
  invalid value to the default, and a `config_server_get_led_blink_ms()`
  getter. Valid values are the AW2023-representable steps
  **{130, 260, 380, 510, 770, 1040}**; anything else snaps to the nearest.
- Web UI: a slider (`type="range"`, 6 discrete stops mapping to the values
  above) in the settings area of `main/web/homepage_full.html`, labeled with
  the effective rate (e.g. "3.8 Hz … 0.5 Hz"), wired in `main/web/src/main.js`
  load/save exactly like existing keys, then `tools/build_web.py` regenerated.
- The indicator task reads the getter each time it programs a blink, so a saved
  change applies at the next indicator activation without reboot.

### Observability (what makes this /goal-checkable)

Add a `led_indicator` field to the `GET /check_status` JSON reporting the
current state as one of `"flash_red" | "datalog_blue" | "idle" | "deferred"`.
This is a genuine diagnostic (remote units have no visible LED) and it lets
acceptance be verified over HTTP against the bench device.

## Acceptance criteria

Each criterion states its check; the check output must appear in the session.

- **AC1 — Build.** `idf.py build` exits 0 under ESP-IDF v5.5.3 (env per
  `MEMORY.md` build notes: prepend Python 3.10 to PATH, source
  `C:\esp\esp-idf-v5.5.3\export.ps1`).
- **AC2 — Codecs untouched.** `git diff --stat wican-pro` shows **no changes**
  to `main/ncflash_fastwrite.c`, `main/ncflash_fastread.c`, or `main/can.c`
  coexistence logic (new read-only accessor additions to `can.c/h` are allowed
  only if needed; lease/park/claim semantics unchanged).
- **AC3 — Single LED owner.** `grep -n "led_set_pattern_ms\|led_fast_blink\|led_set_level" main/led_indicator.c`
  shows the indicator patterns programmed with explicit nonzero `off_time_ms`,
  and grep shows **no new** `led_` calls added to any other file (elm327.c may
  *lose* calls per the MIC fix).
- **AC4 — Pattern hygiene.** Code inspection shown in output: every indicator
  state transition path in `led_indicator.c` calls `led_disable_pattern()` on
  the channel(s) being left before writing the next state.
- **AC5 — Config key.** `grep -n led_blink_ms main/config_server.c main/config_server.h`
  shows: default `"260"` in the defaults JSON, parse + snap-to-valid-set
  coercion, and a getter. Invalid stored values coerce to the default (show the
  coercion code).
- **AC6 — UI slider.** `grep -n led_blink main/web/homepage_full.html main/web/src/main.js`
  shows a range input with the 6 discrete stops and load/save wiring;
  `python tools/build_web.py` and `python tools/lint_web.py` both exit 0.
- **AC7 — On-device deploy.** The built image is OTA-flashed to the bench unit
  (`POST http://192.168.1.169/upload/ota.bin`, per MEMORY.md) and
  `GET /check_status` returns the new `git_version` **and** a `led_indicator`
  field (value `"idle"` at rest).
- **AC8 — Red during dry-run flash.** Stage a dummy `.bin` + matching `.json`
  manifest via `POST /upload/sd/<name>` (schema: `fw_load_manifest` in
  `main/ncflash_fastwrite.c`; CRC32 zlib-compatible; make the image several MB
  so the op is pollable), open TCP 35001, send `WD<name>\r` (dry-run — **never**
  mode `L` on the bench). While `NCFWPROG` frames stream, `GET /check_status`
  shows `"led_indicator":"flash_red"`; after `NCFWDONE` (+ hysteresis window),
  it returns to `"idle"`. Both polls shown in output.
- **AC9 — Blue reporting.** If the bench bus has traffic and a CSV session
  opens (confirm via `GET /event_log/status` / `EVL_DATALOG_OPEN`),
  `GET /check_status` shows `"led_indicator":"datalog_blue"`, and
  `POST /datalog?op=pause` flips it off (then `op=resume&token=` restores).
  If no session can be opened on the bench, AC9 passes by showing the
  code path (blue predicate + /check_status wiring) plus an `event_log`
  correlation, and blue moves to the manual checklist.
- **AC10 — Rate persisted and applied.** Save a different `led_blink_ms` via
  the config API, `GET` the config back showing the persisted value, and show
  the code path where the indicator task reads the getter when programming a
  pattern (rate applies without reboot).

## Constraints

- **Never** send a live (`WL`) flash or any UDS write to the bench device; the
  bench may sit on a live CAN bus. Dry-run (`WD`) and fast-read ping only.
- No LED I2C on `can_tx_task` or inside either flash codec.
- Coexistence semantics (park/claim/reaper, `FLASH_ACTIVE_BIT` set/clear
  points, lease TTLs) are untouched.
- `sdkconfig` untouched; build stays CI-compatible
  (`.github/workflows/build-firmware.yml`, target esp32s3).
- Existing LED behaviors outside indicator scope (safemode colors, config-mode
  alternation, boot-loop breathing, sleep-off, console `led` command) keep
  working; the indicator defers rather than overrides them.
- Web UI edits go in `homepage_full.html`/`main.js` and are regenerated with
  `tools/build_web.py` — never hand-edit `main/web/src/homepage.html`.

## Manual hardware checklist (outside the /goal condition — human eyes required)

- [ ] Red blinks visibly (distinct on/off, not near-solid) during a dry-run
      flash; LED returns to solid blue afterwards.
- [ ] Blue blinks during real datalogging in the car; returns to solid blue
      when the session closes.
- [ ] Moving the slider visibly changes the blink rate at both extremes
      (130 ms vs 1040 ms).
- [ ] After a power-cycle with an SD-card MIC3624 update, the LED ends solid
      blue, not dark.
- [ ] Config/AP mode (button hold) still shows the yellow/blue 1 Hz
      alternation, and the LED behaves after leaving it.

## Deferred / out of scope

- ESP32 OTA (`/upload/ota.bin`) and SD-staged self-update indications.
- A distinct LED state for the 180 s stuck-flash alarm
  (`main/datalog_lease_task.c:64-75`) — candidate follow-up.
- LED brightness as a user setting (`led_set_max_current` exists, unused).
- Mutex/serialization for the legacy direct LED writers (console command,
  safemode) — pre-existing last-writer-wins behavior is unchanged.
