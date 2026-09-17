# Hyundai Ioniq Electric 38kWh (2020–2021) — WiCAN AutoPID Profile

## Vehicle

| | |
|---|---|
| **Make / Model** | Hyundai Ioniq Electric (AE) |
| **Battery** | 38.3 kWh (88S2P, 88 cells) |
| **Model years** | 2020–2021 (facelift, 38kWh variant) |
| **OBD protocol** | ISO 15765-4 (CAN 11-bit, 500 kbps) |

## Profile

| File | PIDs | Parameters | Polling |
|------|------|------------|---------|
| `hyundai_ioniq_electric_38kwh_2021.json` | 6 | 33 | ~1 s cycle |

## Parameters

### MCU — Motor Control Unit (0x7E2)

PID `21014` — Vehicle dynamics (4 response frames)

| Parameter | Formula | Unit | Description |
|-----------|---------|------|-------------|
| SPEED | `(S20*256+B19)*0.01609344` | km/h | Vehicle speed. Signed little-endian int16 in hundredths of mph. Resolution: 0.016 km/h. Negative = reverse. |
| ENGINE_RPM | `(S20*256+B19)*0.967711` | RPM | Motor RPM. Fixed gear ratio ≈ 60.1 RPM per km/h. |
| GEAR | `B10` | — | 0x21 = Park, 0x22 = Neutral, 0x28 = Drive |
| THROTTLE | `B12` | — | Accelerator pedal position (0–255) |

### BMS — Battery Management System (0x7E4)

PID `2201019` — Main battery data (9 response frames)

| Parameter | Formula | Unit | Description |
|-----------|---------|------|-------------|
| SOC | `B10/2` | % | State of charge (BMS raw) |
| HV_V | `[B19:B20]/10` | V | HV pack voltage |
| HV_A | `(S17*256+B18)/10` | A | HV pack current (signed — negative = regen/discharge) |
| HV_W | `((S17*256+B18)/10)*([B19:B20]/10)` | W | HV power (computed from current × voltage) |
| HV_T_MAX | `B22` | °C | Highest module temperature |
| HV_T_MIN | `B23` | °C | Lowest module temperature |
| HV_T_I | `B26` | °C | Inlet coolant temperature |
| HV_C_V_MAX | `B31/50` | V | Highest cell voltage |
| HV_C_V_MAX_NO | `B30` | — | Cell number of highest voltage |
| HV_C_V_MIN | `B34/50` | V | Lowest cell voltage |
| HV_C_V_MIN_NO | `B33` | — | Cell number of lowest voltage |
| CHARGING | `(B15&32)/32` | bool | AC charging active |
| CHARGING_DC | `(B15&64)/64` | bool | DC fast charging active |
| LV_V | `B38/10` | V | 12V auxiliary battery voltage |
| KWH_CHARGED | `([B49:B50]*65536+[B51:B52])/10` | kWh | Cumulative energy charged (lifetime) |

PID `2201057` — SOH and drive mode (7 response frames)

| Parameter | Formula | Unit | Description |
|-----------|---------|------|-------------|
| SOH | `[B34:B35]/10` | % | State of health |
| SOC_D | `B33/2` | % | Display SOC (dashboard value) |
| DRIVE_MODE | `B31` | — | Drive mode selector |

### TPMS — Tire Pressure Monitoring (0x7A0)

PID `22C00B4` — Tire data (4 response frames)

Each tire is encoded as 4 bytes: `[pressure] [temperature] [status] [0x00]`, preceded by a 4-byte header (FF FF 00 00). Pressure raw unit is 0.2 PSI.

| Parameter | Formula | Unit | Description |
|-----------|---------|------|-------------|
| TYRE_P_FL | `B10/72.5` | bar | Front left pressure |
| TYRE_P_FR | `B14/72.5` | bar | Front right pressure |
| TYRE_P_RL | `B19/72.5` | bar | Rear left pressure |
| TYRE_P_RR | `B23/72.5` | bar | Rear right pressure |
| TYRE_T_FL | `B11-50` | °C | Front left temperature |
| TYRE_T_FR | `B15-50` | °C | Front right temperature |
| TYRE_T_RL | `B20-50` | °C | Rear left temperature |
| TYRE_T_RR | `B25-50` | °C | Rear right temperature |

Note: The CF3 sequence byte (0x23) at B24 sits between RR pressure (B23) and RR temperature (B25).

### HVAC — Climate Control (0x7B3)

PID `2201006` — Climate data (6 response frames)

| Parameter | Formula | Unit | Description |
|-----------|---------|------|-------------|
| T_CAB | `B11/2-40` | °C | Cabin temperature |
| TMP_A | `B12/2-40` | °C | Outside ambient temperature |

### CLU — Instrument Cluster (0x7C6)

PID `22B0023` — Dashboard data (3 response frames)

| Parameter | Formula | Unit | Description |
|-----------|---------|------|-------------|
| ODOMETER | `[B13:B14]` | km | Odometer reading (16-bit, max 65535 km) |

## ECU Map

| ECU | Request → Response | Accessible | Notes |
|-----|-------------------|------------|-------|
| MCU | 0x7E2 → 0x7EA | ✓ | Motor Control Unit — speed, RPM, pedals, gear |
| BMS | 0x7E4 → 0x7EC | ✓ | Battery — SOC, voltage, current, cells, temps |
| HVAC | 0x7B3 → 0x7BB | ✓ | Climate — cabin and outside temps |
| TPMS | 0x7A0 → 0x7A8 | ✓ | Tire pressures and temperatures |
| CLU | 0x7C6 → 0x7CE | ✓ | Instrument cluster — odometer |
| OBC | 0x7E5 → 0x7ED | ✓ | On-board charger (not yet decoded) |
| VCU | 0x7E0 → 0x7E8 | ✗ | Not on OBD-II diagnostic bus (behind gateway) |

## Speed Formula Details

The speed signal was reverse-engineered by correlating raw MCU bytes with Car Scanner Pro's decoded VMCU speed across 922 synchronised samples, achieving **R = 1.0000** (zero error on every sample).

The MCU stores vehicle speed as a **signed 16-bit little-endian value in hundredths of a mph**:

```
raw = signed(B20) × 256 + B19
speed_kmh = raw × 0.01609344
speed_mph = raw × 0.01
```

The WiCAN formula uses `S20` (signed byte prefix) for the high byte:
```
SPEED = (S20*256+B19)*0.01609344
```

The constant `0.01609344 = 1.609344 / 100` is the exact km-per-mile conversion divided by 100.

- **Resolution:** 0.016 km/h (0.01 mph)
- **Range:** tested 0–110 km/h forward, −1 km/h creep in Neutral
- **Zero speed:** B19 = 0x00, B20 = 0x00

ENGINE_RPM uses the same raw int16 multiplied by the fixed gear ratio constant (0.967711), derived from correlating BMS Drive Motor Speed with VMCU speed across multiple drive sessions.

## B-Index Convention

WiCAN's expression parser uses **0-based indexing**: `Bn = data[n]`. The data array is the raw concatenation of all CAN frame bytes (after stripping the CAN ID header), including ISO-TP PCI bytes and consecutive frame sequence bytes. This means:

- B0 = first byte of the first frame (typically the PCI byte 0x10 for multi-frame)
- CF sequence bytes (0x21, 0x22, 0x23...) appear in the data stream and must be skipped when mapping parameters
- For signed single bytes, use the `S` prefix: `S17` reads `data[17]` as int8 (−128 to 127)
- For signed multi-byte ranges, use `[S start:S end]` (big-endian only, start ≤ end)
- For little-endian int16, manually compose: `S_high*256+B_low`

## Architecture Notes

The Ioniq Electric uses a **gateway-filtered OBD-II bus**. The gateway ECU blocks all broadcast CAN traffic — only diagnostic request/response pairs pass through the OBD-II connector. This means:

- Standard OBD-II speed/RPM (Mode 01 PIDs 0x0D/0x0C via VCU 0x7E0) are **not accessible**
- Passive CAN sniffing captures zero broadcast frames
- Speed must be read from MCU service 0x21 PID 0x01 as described above

## Hardware Tested

- WiCAN Pro (firmware v4.50, ELM327 via WiFi/BLE)
- vLinker MC (STN2120 v5.8.1, ELM327 via Bluetooth)
- RH02 USB-to-CAN (SLCAN via python-can)

## Validation

All 33 parameters verified on a 2021 Hyundai Ioniq Electric 38kWh (European market) across multiple drive sessions from March–June 2026, including GPS-correlated speed validation (R = 0.98 over 502 driving samples, 778 total WiCAN samples).

## Compatibility

This profile was developed on a 2021 model. It should work on 2020–2021 Ioniq Electric 38kWh (facelift). The 28kWh pre-facelift model may use different byte positions. The Kona Electric and Niro EV share a similar platform and may have compatible BMS PIDs, but the MCU byte mapping should be verified independently.
