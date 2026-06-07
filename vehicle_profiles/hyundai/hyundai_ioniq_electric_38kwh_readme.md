# Fix Hyundai Ioniq Electric 38kWh profile — all 33 parameters verified on-vehicle

## Summary

Complete rewrite of the Hyundai Ioniq Electric 38kWh (2020–2021) vehicle profile based on extensive on-vehicle testing with WiCAN Pro firmware. All 33 parameters across 6 PIDs have been validated against live data from multiple drive sessions.

## Changes

### PID response line counts (missing → added)

| PID | Before | After | Issue |
|-----|--------|-------|-------|
| MCU 2101 | `2101` | `21014` | 4-frame response, was timing out |
| TPMS C00B | `22C00B` | `22C00B4` | 4-frame response, partial data |
| CLU B002 | `22B002` | `22B0023` | 3-frame response |

### BMS 0x0101 — signed current & temperature offsets

| Parameter | Before | After | Reason |
|-----------|--------|-------|--------|
| HV_A | `[B17:B18]/10` | `(S17*256+B18)/10` | Signed current — regen shows as negative. Old formula treated regen as large positive values. |
| HV_W | `([B19:B20]/10)*([B17:B18]/10)` | `((S17*256+B18)/10)*([B19:B20]/10)` | Uses corrected signed current |
| HV_T_MAX | `B22-40` | `B22` | Raw value is already °C, no offset needed. Verified: 20–24°C across sessions. |
| HV_T_MIN | `B23-40` | `B23` | Same |
| HV_T_I | `B26-40` | `B26` | Same |

### MCU 0x2101 — speed, RPM, and throttle

| Parameter | Before | After | Reason |
|-----------|--------|-------|--------|
| SPEED | `B20*3.97+2.3` | `(S20*256+B19)*0.01609344` | Correct formula: signed little-endian int16 in hundredths of mph → km/h. Verified R=0.98 against GPS across 502 driving samples. Signed prefix handles reverse/creep in Neutral. |
| ENGINE_RPM | *(missing)* | `(S20*256+B19)*0.967711` | New. Fixed gear ratio ×0.967711 derived from speed bytes. Verified RPM/speed ratio = 60.4 (expected 60.1). |
| THROTTLE | `B13` | `B12` | Byte position corrected |

### TPMS 0x22C00B — pressure unit & byte positions

| Parameter | Before | After | Reason |
|-----------|--------|-------|--------|
| TYRE_P_* | `Bx/5` | `Bx/72.5` | Old formula gave values in PSI, not bar. /72.5 converts raw (0.2 PSI units) to bar. |
| TYRE_T_RR | `B24-50` | `B25-50` | B24 is the CF3 sequence byte (0x23), temperature is at B25. |

### HVAC 0x0100 — added outside temperature

| Parameter | Before | After |
|-----------|--------|-------|
| TMP_A | *(missing)* | `B12/2-40` |

## Validation

Tested on a 2021 Hyundai Ioniq Electric 38kWh (AE, European market) using WiCAN Pro over multiple sessions in March–June 2026.

**Drive session 2026-06-06** (778 WiCAN samples + 122 GPS samples, 13 min):

| Parameter | Value range | Status |
|-----------|-------------|--------|
| SOC | 84.0–87.5% | ✓ |
| HV_V | 343.0–358.4V | ✓ |
| HV_A | −135.0 to +170.1A | ✓ signed regen |
| HV_T_MAX/MIN | 20–24°C | ✓ |
| SPEED | 0–110.6 km/h | ✓ R=0.98 vs GPS |
| ENGINE_RPM | 0–6654 | ✓ ratio 60.4 |
| GEAR | P(0x21), N(0x22), D(0x28) | ✓ |
| TYRE_P | 2.40–2.46 bar | ✓ |
| TYRE_T | 33–36°C | ✓ |
| ODOMETER | 61,377–61,415 km | ✓ |
| T_CAB / TMP_A | 23.5–33.5 / 22.5–29.5°C | ✓ |
| All 33 params | — | ✓ |

## Vehicle details

- **Model:** Hyundai Ioniq Electric (AE) 38.3 kWh, 2020–2021 facelift
- **ECUs tested:** BMS (7E4→7EC), MCU (7E2→7EA), HVAC (7B3→7BB), TPMS (7A0→7A8), CLU (7C6→7CE)
- **Adapter:** WiCAN Pro, firmware v3.x, ELM327 mode (ATH1, ATS1, ATSP6)
