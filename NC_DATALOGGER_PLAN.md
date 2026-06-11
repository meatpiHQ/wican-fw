# NC Custom Datalogger — Plan

Builds on the Stream 2 CSV logger MVP (`STREAM2_LOGGER_PLAN.md`,
`components/csv_logger/`). Goal: a Tactrix-OpenPort-class track/road logger
for a Romdrop-flashed 2009 NC Miata (LF9VEB ROM), grounded in the
`nc-flash-re` static RE docs (`can_bus.md`, `cluster_communication.md`)
instead of community guesswork.

Three data sources, in order of preference per channel:

1. **Broadcast sniff** (passive, free, fastest) — ECU/ABS/cluster periodic frames
2. **OBD-II Mode 01 poll** (slow, serialized) — only for channels not broadcast
3. **Romdrop UDS RAM read** (custom 0x046→0x040 channel) — arbitrary RAM floats

---

## Ground truth from the RE docs (deltas vs. shipped profile)

The ROM docs confirm some of `vehicle_profiles/mazda/mx5_nc.json` and break
other parts of it **on this specific car**:

| Finding | Impact |
|---|---|
| 0x201 B0-1 RPM, scale 0.25 (ROM constant verified) | Shipped `[B0:B1]/4` ✓ |
| 0x201 B6 accel pedal, scale 0.5, max 200=100% | Shipped `B6/2` ✓ (ROM settles the RaceChrono `*2` typo) |
| **0x201 B4-5 is Romdrop-patched** to cruise data (`fn_romdrop_cruise_update`, 0xFFFF6AEC) | Shipped `VehicleSpeed [B4:B5]` is likely **wrong on this car** (valid on stock ROM). Need alternate VSS source. |
| 0x200 B7 throttle, scale 0.5, max 200=100%, error 0xFF | TP available at 100 Hz as `B7/2` — better than 0x240 B3 (whose `/2.55` scale RaceChrono itself flags as doubtful; 0x240 TX packing is inline code, undocumented in ROM) |
| 0x200/0x215 carry 6 torque words (offsets -10000 / -512), unit ~BMEP kPa (LOW confidence) | New channels impossible over OBD Mode 01 |
| 0x420 B0 = ECT gauge, `B0-40` °C | Redundant ECT cross-check for 0x240 B1 |
| AAT appears in **none** of the 13 ECU TX messages | AAT stays a Mode 01 `0146` poll — or gets dropped if unsupported |
| ECU has two CAN controllers: HCAN0 (0x200/0x201/0x215/0x211/0x216/0x218/0x21A/0x231 + OBD 0x7DF/0x7E8) and HCAN1 (0x240/0x420/0x4B0/0x4EC/0x4FF/0x046/0x040/0x0EC) | The OBD port pins 6/14 are by definition on the 0x7E8 bus (HCAN0). Whether HCAN1 IDs are visible at the port (shared physical bus, or gateway) is **the** open question — RaceChrono users empirically read 0x240/0x4B0 at the port, but it must be confirmed on this car before betting channels on it |
| Romdrop adds 0x046 (RX cmd) → 0x040 (UDS resp), 0x4FF NVRAM broadcast, 0x0EC flex-fuel RX | Custom side-channel for RAM-variable logging (Phase 3) |
| TX gated by `ram_trans_mode` bits 6/7 + cranking rate gate | Frames appear shortly after key-on; logger's existing ignition gating is compatible |

### Firmware constraint (audited)

`autopid` monitors CAN filters **one ID at a time**: per filter, `ATCRA <id>`
→ `ATMA` slice (≤1.1 s, exits on first match) → next filter
(`components/autopid/autopid.c:4133-4179`). With N filters each channel
updates at best every N slices. Fine at 2 filters / dashboard rates;
unusable for 5-8 IDs at logging rates. Fixing this is Phase 2 and is the
core build of this plan.

---

## Phase 0 — On-car bus discovery (no code, ~1 evening)

Hook a terminal to the WiCAN's ELM327 TCP port (or web console), then:

1. `ATSP6` `ATH1` `ATCAF0` `ATMA` — capture ≥60 s at idle and a short drive.
2. **ID census**: which of {0x200, 0x201, 0x215, 0x231, 0x211, 0x216, 0x218,
   0x21A} (HCAN0) and {0x240, 0x420, 0x4B0, 0x4EC, 0x4FF, 0x081, 0x085}
   (HCAN1/DSC) appear at the port. This single capture decides the Phase 1
   channel map.
3. **VSS arbitration**: log raw 0x201 B4-5 while driving at a known steady
   speed; compare against `([B4:B5]/100)-100` km/h, the speedometer, and
   0x4B0 wheel speeds. Decide VSS source: 0x201 (if Romdrop left it alone),
   0x4B0 (if visible), or 0x211 B0-1 (cluster speed — scaling TBD from same
   capture; sentinels 0xFFFE/0xFFFF = invalid).
4. **TP cross-check**: 0x200 B7/2 vs 0x240 B3 (try both /2 and /2.55) at
   idle and WOT.
5. **AAT probe**: send `0146` — supported or NO DATA.
6. Save the raw capture into the repo (`docs/nc_captures/`) as the reference
   artifact.

## Phase 1 — Profile v2 (data-only, ~half day after Phase 0)

Update `vehicle_profiles/mazda/mx5_nc.json` (shared, must stay stock-ROM
safe) and a personal `car_data.json` overlay for Romdrop-specific deltas:

- Add 0x200: `Throttle [B7]/2`, `ActualTorque [B0:B1]-10000`,
  `RequestedTorque [B2:B3]-10000`, `TorqueLimit [B4:B5]-10000`
  (unit label "raw" until BMEP-kPa is validated).
- Add 0x215 torques (`-512` offset) only if Phase 0 shows them useful.
- Add 0x420: `ECTGauge B0-40` as ECT cross-check.
- Add 0x4B0 wheel speeds + 0x085 brake (+0x081 steering, needs signed-expr
  check) if visible at the port and car has DSC.
- Fix VSS per Phase 0 verdict; keep stock-ROM `[B4:B5]` variant in the
  shared profile if it validates on stock, with a doc note about Romdrop.
- Keep or drop the `0146` AAT poll per probe result.

## Phase 2 — Firmware: simultaneous multi-ID monitor (core build, ~2-3 days)

Replace the per-ID time-slice with one shared monitor window using the STN
chip's filter set (WiCAN PRO always has an STN; keep the existing ATCRA
slicing as fallback for plain-ELM hardware):

1. **Arm once per cycle**: `STFCP` (clear pass filters), then
   `STFAP <id>,7FF` for every enabled filter ID, `ATH1`, `ATCAF0`, `STM`.
   All selected IDs stream concurrently.
2. **Dispatch parser**: extend `autopid_atma_parser` from single
   `expected_frame_id` to an ID→`can_filter_t` lookup so each monitor line
   routes to the right filter's `process_can_filter_frame()`.
3. **Bounded window**: monitor for a configurable window (default ~300 ms),
   break with `\r`, service due Mode 01 / custom polls, restore filters,
   repeat. Poll hole ≈50-100 ms per cycle instead of today's
   N×(slice+reinit).
4. **CSV logger v2**: add optional `raw_hex` column (payload already in
   `response_t`), per-channel sample counters, and a dropped-rows
   high-water stat in the web UI.
5. **Throughput budget**: 0x201+0x4B0 at 100 Hz + the rest ≈ 250-450
   frames/s ≈ 25-45 kB/s CSV — well inside UART1/SD margins, but verify
   queue depth on the bench; decode throttling stays per-parameter
   (`period`), raw capture rate is governed by the window duty cycle.

## Phase 3 — Romdrop UDS RAM channels (exploratory, after protocol docs)

The genuinely custom layer: log RAM floats that never hit the bus (spark
advance, knock retard, target/actual AFR, fuel trims, ethanol %).

- **Blocked on input**: 0x046 command format + session/security rules from
  `nc-flash-re` (e.g. `romdrop_diff.md` / UDS protocol doc). The CAN doc
  names the channel but not the payloads.
- **Likely zero-firmware path**: autopid custom PID with
  `init: "ATSH046;ATCRA040;ATCAF0;"`, raw-hex `cmd`, `rxheader 040` —
  works if requests are single-frame and sessionless. If ISO-TP or
  keepalive is needed, reuse Stream 1's session code when it lands.
- **Note**: 0x0EC (flex-fuel ethanol) is a plain broadcast — if visible at
  the port it's a Phase 1 sniff channel, no UDS needed.
- **Safety rule** (inherited from Stream 2 plan): all logging pauses the
  instant an NC-Flash session starts; UDS channels doubly so.

## Phase 4 — Polish (optional)

- Offline `tools/csvlog_pivot.py`: long→wide CSV (one row per timestamp,
  column per channel) for MegaLogViewer/RaceChrono import. Keep the
  on-device format long/append-only.
- Auto-offload finished logs over FTP/Wi-Fi when home SSID seen.

## Decisions needed from operator

1. Phase 0 captures (needs the car + 30 min).
2. Does the car have DSC? (Determines 0x081/0x085/0x4B0 channels.)
3. Romdrop diagnostic protocol docs for Phase 3.
4. Long vs. wide CSV as the primary deliverable format (plan assumes long
   on-device + offline pivot).
