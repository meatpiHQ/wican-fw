# Profiles for Land Rover Vehicles

## Range Rover Evoque (L538) Diesel

Derived from an ELM327 session capture of a **2015 Evoque L538** (VIN `SALVA2AC4F…`, ECM
calibration `EJ32-12K532-VSF`). `ATDPN` reported `A6`, i.e. ISO 15765-4 CAN 11-bit / 500 kbaud, hence
`ATSP6`.

Reported by the ECM (`7E8`):

| Query | Mask       | Meaning                     |
| ----- | ---------- | --------------------------- |
| 0100  | `983B2013` | 01,04,05,0B,0C,0D,0F,10,13,1C,1F,20 |
| 0120  | `B01BA001` | 21,23,24,2C,2D,2F,30,31,33,40 |
| 0140  | `C4DF0000` | 41,42,46,49,4A,4C,4D,4E,4F,50 |

Note that **0111 (throttle position), 0106 (short term fuel trim), 0143 (absolute load) and 01A6
(odometer) are not supported** — the mask stops at 0x50, so nothing above PID 0x60 exists. This is
why the generic ICE profile only partly works on this car, and why odometer has to come from a
manufacturer PID.

Driver demand therefore has to come from `0149`/`014A` (absolute pedal position D and E). `014C`
(commanded throttle actuator) *is* supported, but on a diesel that is the intake throttle flap the
ECM commands for EGR and shutdown — not driver demand — so it is published as `THROTTLE_CMD` rather
than `THROTTLE`.

A scanner may list **relative accelerator pedal position** for this car; that is `015A`, which the
mask puts outside the supported set (the `0140` mask's last two bytes are `0000`). It reads a
constant 0 % and is not in the profile.

These are mask-supported and confirmed live on the car, but are left out as diagnostic counters
rather than telemetry: `0121` distance with MIL on, `0130` warm-ups since codes cleared, `014D`/`014E`
time run with MIL on / since codes cleared, and `0150` maximum MAF.

### Addressing

Standard PIDs are broadcast to `7DF` with the ELM "expect 1 response" suffix (`010C1` rather than
`010C`), because both the ECM (`7E8`) and TCM (`7E9`) answer several of them. `7E8` always won
arbitration in the capture, so the first response is the ECM's.

The mode-22 PIDs need the header moved off `7DF` — `ATSH7E0` for the ECM, `ATSH760` for the ABS. A
plain `ATSH` is sufficient; the capture needed no `ATCRA` or `ATFCSH` for either module, and all the
responses are single-frame. Because `pid_init` is re-sent before every poll of its own PID, each
group carries an explicit header and the profile is order-independent.

**Adapter matters for the non-ECM modules.** The capture contains two adapters: a generic `OBDII`
BLE clone and a WiCAN. The ABS (`760`) and TCM (`7E1`) modules answered in exactly one session out of
five — the one using the WiCAN, with the engine running. Every session on the clone returned
`NO DATA` for those two modules while the ECM (`7E0`) answered fine throughout. So low-level
addressing to non-powertrain modules is an adapter capability here, not a car limitation.

### PID Listing

All expressions below were replayed against every matching frame in the capture; the observed ranges
are shown. `LV_V` is an especially good check — it matched the adapter's own `ATRV` readings
(11.7 V key-off, 14.4–14.5 V charging) to within 0.05 V.

| Supported | Name                | PID    | Module     | Expression      | Observed in capture      |
| :-------: | ------------------- | ------ | ---------- | --------------- | ------------------------ |
|    ✅     | ENGINE_RPM          | 010C   | ECM (7DF)  | `[B3:B4]/4`     | 0, 786–800 idle, 1871    |
|    ✅     | SPEED               | 010D   | ECM (7DF)  | `B3`            | 0–23 km/h                |
|    ✅     | COOLANT_TMP         | 0105   | ECM (7DF)  | `B3-40`         | 30–86 °C                 |
|    ✅     | INTAKE_AIR_TMP      | 010F   | ECM (7DF)  | `B3-40`         | 17–38 °C                 |
|    ✅     | OUTSIDE_TEMPERATURE | 0146   | ECM (7DF)  | `B3-40`         | 13–21 °C                 |
|    ✅     | MAF                 | 0110   | ECM (7DF)  | `[B3:B4]/100`   | 0–24.24 g/s              |
|    ✅     | FUEL                | 012F   | ECM (7DF)  | `B3/2.55`       | 16–63 %                  |
|    ✅     | FUEL_PRESSURE       | 0123   | ECM (7DF)  | `[B3:B4]*10`    | 410 kPa off, 275–437 bar |
|    ✅     | LV_V                | 0142   | ECM (7DF)  | `[B3:B4]/1000`  | 11.76 V / 14.34–14.74 V  |
|    ✅     | ODOMETER            | DD01   | ECM (7E0)  | `[B4:B6]`       | 70468 → 72827 km         |
|    ✅     | WHEEL_SPD_FR        | 2B06   | ABS (760)  | `B4`            | 0 km/h (stationary)      |
|    ✅     | WHEEL_SPD_FL        | 2B07   | ABS (760)  | `B4`            | 0 km/h (stationary)      |
|    ✅     | WHEEL_SPD_RR        | 2B08   | ABS (760)  | `B4`            | 0 km/h (stationary)      |
|    ✅     | WHEEL_SPD_RL        | 2B09   | ABS (760)  | `B4`            | 0 km/h (stationary)      |
|    ✅     | ENGINE_LOAD         | 0104   | ECM (7DF)  | `B3*100/255`    | 20.78 % idle             |
|    ✅     | INTAKE_MAN_PRESS    | 010B   | ECM (7DF)  | `B3`            | 104 kPa idle             |
|    ✅     | BARO_PRESSURE       | 0133   | ECM (7DF)  | `B3`            | 100 kPa                  |
|    ✅     | RUN_TIME            | 011F   | ECM (7DF)  | `[B3:B4]`       | 8639 s                   |
|    ✅     | LAMBDA              | 0124   | ECM (7DF)  | `[B3:B4]/32768` | 2 (pinned at ceiling)    |
|    ✅     | EGR_DUTY            | 012C   | ECM (7DF)  | `B3*100/255`    | 0 % idle                 |
|    ✅     | EGR_ERROR           | 012D   | ECM (7DF)  | `B3*100/128-100`| −0.78 %                  |
|    ✅     | DIST_SINCE_CLR      | 0131   | ECM (7DF)  | `[B3:B4]`       | 3433 km                  |
|    ✅     | ACC_PEDAL_D         | 0149   | ECM (7DF)  | `B3*100/255`    | 0 % (foot off)           |
|    ✅     | ACC_PEDAL_E         | 014A   | ECM (7DF)  | `B3*100/255`    | 0 % (foot off)           |
|    ✅     | THROTTLE_CMD        | 014C   | ECM (7DF)  | `B3*100/255`    | 9.8 % idle               |
|    ✅     | EGT_1               | 03F4   | ECM (7E0)  | `B4*5`          | 125 °C idle, 545 °C peak |
|    ✅     | EGT_2               | 03F5   | ECM (7E0)  | `B4*5`          | 105–365 °C               |
|    ✅     | EGT_3               | 03F6   | ECM (7E0)  | `B4*5`          | 105–325 °C               |
|    ✅     | DPF_DIFF_PRES       | 0405   | ECM (7E0)  | `B4`            | 0 kPa idle, 7 kPa peak   |
|    ✅     | DPF_SOOT_MASS       | 042C   | ECM (7E0)  | `[B4:B5]/655.35`| 9.24 g / 19.89 g pre-regen |
|    ✅     | DPF_DIST_SINCE_REGEN| 0434   | ECM (7E0)  | `[B4:B7]/655.35`| 132.03 km / 1148.79 km   |
|    ✅     | DIST_SINCE_OIL_SERVICE | 0542 | ECM (7E0) | `[B4:B5]`       | 21422 → 23781 km         |
|    ⚠️     | BOOST_ACT_POS_D     | 03DF   | ECM (7E0)  | `[B4:B5]/655.35`| 50.0 % off, 41–79 % running |

The odometer identification is independently corroborated three ways: the byte layout matches the
`22DD01` / `[B4:B6]` mapping already used by the Ford profiles in this repo; the absolute value is
plausible for an 11-year-old car; and its per-session deltas track standard PID 0131 (distance since
codes cleared) 1:1 across the capture (+8, +76, +2275 km vs +8, +76, +2281 km).

The four wheel speeds come from the [OBDb](https://github.com/OBDb) community database, which names
`222B06`–`222B09` on header `760` identically for Ford. **Note the left/right order is not the
intuitive one** — `06` is front *right*, `07` is front *left*, `08` rear *right*, `09` rear *left*.
Each is a single byte read directly as km/h. All four read `0x00` in the capture, consistent with
standard PID `010D` also reading 0 in that session, so the byte position is confirmed but the scale
is not yet proven against a moving car.

The same database is worth checking for the trap it documents: on the instrument cluster (`720`) the
`22DD01` DID is **trip distance with a /10 scale**, not the odometer — so the header matters as much
as the PID.

### Decoding the ECM `03xx`–`05xx` block

Cross-checking that block against OBDb was informative mainly for what it ruled out. The unknowns sit
*adjacent* to documented Ford PIDs without landing on them — `2203DF` next to Ford's `2203DC` (high
pressure fuel pressure desired), `220405` next to `220403`/`220404` (knock sensors), `220542` next to
`220548`/`22054B` (low fuel pressure, oil life), `220396` next to `220393` (A/C compressor pressure).
OBDb's Ford coverage is petrol/EcoBoost-oriented — VCT, misfire, knock, octane — and a search across
its Ford and JLR repositories for `dpf|soot|regen|exhaust|egt|egr|glow|turbo|boost` returns nothing.
This ECM's `03xx`–`05xx` block is evidently remapped for a diesel, so the Ford definitions do not
transfer even though the body and chassis modules match exactly.

What did crack it was a **Car Scanner session on the same car**, which labels these sensors by name
without exposing the DIDs behind them. Replaying the ELM capture against that named list — at the
same odometer reading, 72827 km — pins every one of them:

| PID    | Bytes | Scanner label                          | Scale            | How it was pinned                            |
| ------ | :---: | -------------------------------------- | ---------------- | -------------------------------------------- |
| 2203F4 |   1   | Exhaust gas temperature sensor 1       | `B4*5` °C        | final poll `0x19` → exactly the 125 °C shown |
| 2203F5 |   1   | Exhaust gas temperature sensor 2       | `B4*5` °C        | same block, damped response                  |
| 2203F6 |   1   | Exhaust gas temperature sensor 3       | `B4*5` °C        | same block, near-flat post-DPF               |
| 220405 |   1   | Differential pressure of the DPF       | `B4` kPa         | 0 at idle → 7 by 2000 rpm, monotonic         |
| 22042C |   2   | Soot concentration                     | `[B4:B5]/655.35` g | 9.24 g vs 9.41 g a minute later at idle    |
| 220434 |   4   | Distance since last DPF regen.         | `[B4:B7]/655.35` km | reads **132.03 km**, scanner reads 132.03 km |
| 220542 |   2   | Distance after oil service              | `[B4:B5]` km     | reads **23781 km**, scanner reads 23781 km   |
| 2203DF |   2   | Boost pressure actuator desired position | `[B4:B5]/655.35` % | in-range only — *not* confirmed            |
| 220396 |   2   | —                                      | —                | `0000` in all 128 samples, including at load |

The **`/655.35` divisor** (i.e. `×100/65535`) is the key. It is shared by three DIDs in the block and
is what makes the numbers fall out:

- `220434` advanced 697674 → 752960 over an 84 km stretch of odometer. `55286 / 655.35 = 84.36 km`.
  Over a shorter 76 km leg it gives 75.68 km. It then reads 86525 → **132.03 km**, which is the
  distance-since-regeneration the scanner displays to the last decimal place.
- `22042C` under the same divisor gives 9.24 g against the scanner's 9.41 g. The car was stationary
  and idling between the two readings, which is exactly why distance had frozen while soot kept
  creeping up — the discrepancy is the corroboration, not a problem with it.
- `2203DF` gives 50.0 % with the engine off and 41–79 % running, which suits a VGT actuator command
  and brackets the 74.9 % the scanner showed. No sample lines up directly, so this one stays a guess.

The three EGT sensors behave the way a diesel exhaust train should, which is what confirms both the
mapping and the `×5` scale independently of the single matching sample:

| rpm bucket | n  | EGT 1 | EGT 2 | EGT 3 |
| ---------- | -- | ----- | ----- | ----- |
| 750+       | 74 | 216 °C | 208 °C | 234 °C |
| 1500+      | 13 | 358 °C | 278 °C | 268 °C |
| 2000+      |  4 | 412 °C | 279 °C | 230 °C |

Sensor 1 nearly doubles across the range as a pre-turbo sensor does, sensor 2 is damped, and sensor 3
is thermally buffered downstream and barely moves. A plain `×5` is right rather than the `(A*5)−40`
seen elsewhere: the −40 variant would put the final sample at 85 °C against the scanner's 125 °C.

The earlier reading of `22042C`/`220434` as a *pair* of regeneration counters was half right — they
do reset together at a regeneration, but only `220434` is a distance. `220396` is now ruled out
rather than merely unexplained: it stays at `0000` under load, so it is not the DPF differential
pressure it sits near in the block.

### Still unidentified

| PID    | Bytes | Observed                                             | Guess                                          |
| ------ | :---: | ---------------------------------------------------- | ---------------------------------------------- |
| 223302 |   2   | `219E` — ABS (`760`), one sample                      | Ford puts steering angle at `223201`, not this |
| 221E69 |   1   | `0x58` = 88 — TCM (`7E1`), one sample                 | ATF temperature — see below                    |

The scanner reports **three ATF temperatures, and reports them from the TCM**, not the ECM. That
makes `221E69` on `7E1` a much better candidate for `TRAN_F_TEMP` than the untested `221674` on `7E0`
currently in the profile: `0x58 − 40 = 48 °C` is a plausible reading, and the scanner showed 72 °C on
its third ATF channel in a warmed-up car. Worth a targeted test — but the two must not both be
polled into `TRAN_F_TEMP`, so `221674` stays until `221E69` is confirmed.

Two further DIDs the profile polled were genuinely rejected with `7F 22 31` (requestOutOfRange) and
are not supported on this car: `221E3F` and `22DF0A`, both on the TCM.

One sensor the scanner names has **no matching DID anywhere in the capture**, because the capture
never polled for it: *distance to next service*. It displayed as 0 km, so it may well be unsupported
on this car rather than merely unlocated.

### Unverified candidates — now in the profile, pending a real test

> Everything in this section is **in the profile but unverified**. None of it was polled in the
> capture. It is there so a single drive shows which PIDs answer; expect `NO DATA` from some, and
> treat every scale as a hypothesis until checked against a known-good reading. Once the responses
> are known, the survivors should get real scales and the rest should be deleted.

Where a scale was documented it is used; where the source formula was corrupt or missing, the value
is left **raw** (`unit: none`) rather than invented, so the true scale can be derived from what comes
back. Temperatures use the modern Ford `B4-40` celsius convention seen on `22F405` in
`ford/focus_rs_mk3.json`, not the legacy imperial formulas.

| Header | PID      | Parameter             | Expression                  | Confidence in scale |
| ------ | -------- | --------------------- | --------------------------- | ------------------- |
| `7E0`  | `221310` | `ENGINE_OIL_TEMP`     | `B4-40`                     | convention only     |
| `7E0`  | `221674` | `TRAN_F_TEMP`         | `B4-40`                     | convention only; Ford uses `[B4:B5]/16` at `221E1C`, so this may be wrong |
| `7E0`  | `2216A8` | `INTAKE_AIR_TEMP_2`   | `B4-40`                     | convention only     |
| `7E0`  | `22168E` | `FUEL_TEMP`           | `B4-40`                     | convention only     |
| `7E0`  | `2216AE` | `EXH_BACK_PRES`       | `[B4:B5]` raw               | none — source formula corrupt |
| `7E0`  | `221433` | `EXH_BACK_PRES_DUTY`  | `B4*100/255`                | Ford duty convention |
| `7E0`  | `221450` | `GLOW_PLUG_TIME`      | `[B4:B5]`                   | good — documented `A*256+B` seconds |
| `7E0`  | `221451` | `GLOW_PLUG_LAMP_TIME` | `[B4:B5]`                   | good — documented `A*256+B` seconds |
| `760`  | `222B00` | `PARK_BRAKE`          | `B4`                        | binary              |
| `760`  | `222B0D` | `BRAKE_PRES`          | `[B4:B5]` raw               | none                |
| `760`  | `222B11` | `LAT_ACCEL`           | `[B4:B5]` raw               | none                |
| `760`  | `223201` | `STEER_ANGLE`         | `([B4:B5]-32768)/10`        | guess — assumes offset binary |
| `751`  | `222076`–`222079` | `TYRE_P_FL/FR/RR/RL` | `B5*0.19915`         | OBDb gives `bix 8`, ×0.01373 bar; ×14.5038 converts to the psi this repo uses |
| `726`  | `224027` | `BATT_AGE`            | `[B4:B5]` raw               | none                |
| `726`  | `224028` | `LV_SOC`              | `B4`                        | Ford convention     |
| `726`  | `224029` | `BATT_TEMP`           | `B4-40`                     | Ford convention     |
| `726`  | `22402B` | `LV_A`                | `([B4:B5]-32768)/256`       | good — matches `ford/transit.json` exactly |

Note `223201` is Ford's steering angle; the ABS also answered `223302` in the capture, which is *not*
in any database consulted, so the two are probably different things.

#### Deliberately excluded

These were left out because they would publish a second, unverified value over a sensor this profile
already reads from a **verified** standard PID — adding them destroys good data rather than adding
new data. If a standard PID ever turns out to be wrong, swap in the alternative rather than running
both.

| Candidate            | Header | Would collide with            |
| -------------------- | ------ | ----------------------------- |
| `22113C`             | `7E0`  | `EGR_DUTY` from `012C`        |
| `221172`, `22402A`   | `7E0`, `726` | `LV_V` from `0142`     |
| `2216C1`             | `7E0`  | `FUEL` from `012F`            |
| `221670`, `22168C`   | `7E0`  | `FUEL_PRESSURE` from `0123`   |
| `229924`             | `733`  | `OUTSIDE_TEMPERATURE` from `0146` |
| `222813`–`222816`    | `726`  | the `751` TPMS set used above |

### Provenance of the candidates

Other modules were probed with `22F180`/`22F188`/`22F18C`/`22F190`/`22F191`, but only for identity
data, so no live parameters were captured from them. Modules that answered: `703` AWDCM, `716` SDLC,
`720` dashboard, `726` BCM, `730` PSCM, `732` GSM, `734` headlamp, `736` parking assist, `737`
SRS/RCM, `760` ABS, `792` terrain response, `797` SASM, `7E0` ECM, `7E1` TCM.

Two sources supplied the candidates above. [OBDb](https://github.com/OBDb) covers this car's body and
chassis modules accurately — its `Land-Rover` set uses headers `732`, `733`, `751`, `7E0`, `7E4`, all
of which match the module map derived independently from the capture. Its ECM entries point at a
`11xx`/`16xx` DID block entirely separate from the Ford `03xx`–`05xx` range this ECM was observed
answering on, which is the most promising place to look for diesel parameters standard OBD does not
expose.

That block is corroborated by the legacy Ford EEC PID dictionary (imperial units, pre-DPF era), which
independently gives the same meanings — and adds scale factors OBDb omits:

| DID    | Ford name | Meaning                        | Ford scale       | OBDb Land Rover agrees? |
| ------ | --------- | ------------------------------ | ---------------- | ----------------------- |
| `1172` | VPWR      | Vehicle power / battery volts  | `A*0.0625` V     | yes, "Battery voltage"  |
| `168E` | EFTA      | Engine fuel temperature bank 1 | °F, garbled      | yes, "Fuel rail temperature" |
| `16C1` | FLI       | Fuel level indicator           | percent          | yes, "Fuel tank level"  |

Further candidates from the same dictionary that have **no standard-OBD equivalent on this car**, so
would be a real gain if they answer — all untested here:

| DID    | Ford name | Meaning                                  | Candidate parameter   |
| ------ | --------- | ---------------------------------------- | --------------------- |
| `1310` | EOT       | Engine oil temperature                   | `ENGINE_OIL_TEMP`     |
| `1674` | TFT       | Transmission fluid temperature           | `TRAN_F_TEMP`         |
| `16A8` | IAT2      | Intake air temperature sensor 2 (charge) | `INTAKE_AIR_TEMP_2`   |
| `16AE` | EBPV      | Exhaust back pressure sensor voltage     | —                     |
| `1433` | EPR       | Exhaust back pressure regulator duty     | —                     |
| `1450` | GPCT      | Glow plug control time                   | —                     |
| `1451` | GPLT      | Glow plug lamp time                      | —                     |
| `113C` | EGRVR     | EGR valve regulator percent              | —                     |
| `168C` | FRP       | Injector / rail pressure (PSI)           | `FUEL_PRESSURE`       |

Two caveats before trusting any of these. The dictionary is imperial and several formulas are
visibly corrupt in the source (`1139 ECT (A*2.996)???`, `1674 TFT A*31.25 = 0-8000 F`), so treat the
scales as hypotheses to verify against a known-good reading. And it is a pre-DPF list — it contains
no soot, regeneration or particulate entries at all, so it cannot explain the `22042C`/`220434`
counter pair or the `2203F4`/`F5`/`F6` triple.
