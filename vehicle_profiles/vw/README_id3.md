# VW ID.3 WiCAN profile

This directory contains the WiCAN vehicle profile for the Volkswagen ID.3.

## Charging state

PID `227448` exposes two related values:

- `DRIVE_MODE` is the raw value from byte 4. The observed mode values are `0` for standby, `1` for driving, `4` for AC charging, and `6` for DC charging.
- `CHARGING` extracts bit 2 from byte 4 (`B4:2`). It is `1` in charging modes 4 and 6, and `0` in driving mode 1.

The mode values and bit interpretation are based on the [evDash VW ID.3 implementation](https://github.com/nickn17/evDash/blob/master/src/CarVWID3.cpp).

### Tested on the car

While the car was on standby, WiCAN reported `CHARGING = 0`. While it was charging on AC at 1.3 kW, WiCAN reported `DRIVE_MODE = 4` and `CHARGING = 1`. The validation status for other parameters is described below. `CHARGING` means that charging is active; it does not indicate only that a cable is plugged in.

With the car displaying 79% state of charge, WiCAN returned `SOC_D = 78.77%` and `SOC = 76.8%`. `SOC_D` closely matches the car's rounded display; `SOC` is the separate BMS value and was 2.2 percentage points lower in this reading.

With the car displaying 340 km of range, WiCAN returned `RANGE = 340 km`.

With the car displaying 198,571 km, WiCAN returned `ODOMETER = 198571 km`.

For the ID.3 Pro S, PID `222AB2` returned `62 2A B2 05 5F 04 00`. The current profile formula reads the first two data bytes (`05 5F`) in 0.05 kWh units and reports `68.75 kWh`. The evDash four-byte formula gives `68.748 kWh`, so the profile's reading agrees to rounding. The PID is described by evDash as maximum battery energy content; this reading is not the same as confirming the pack's nominal 77 kWh rating.

### High-voltage battery parameters

The meanings below follow the [evDash VW ID.3 implementation](https://github.com/nickn17/evDash/blob/master/src/CarVWID3.cpp). These are BMS/vehicle readings, not direct measurements made by WiCAN:

- `HV_CAPACITY_KWH` (`222AB2`) is the reported maximum HV battery energy content converted to kWh. It is not the model's nominal or usable capacity specification. On this Pro S, it reported `68.75 kWh`; do not interpret that value alone as evidence that the 77 kWh pack is undersized.
- `HV_V` (`221E3B`) is the high-voltage battery pack voltage in volts.
- `HV_A` (`221E3D`) is the high-voltage battery pack current in amperes. Its four-byte value spans the first and consecutive ISO-TP frames. WiCAN's byte indexes include the ISO-TP frame-control bytes, so the expression reads bytes `B5:B7` and `B9`; using `B4:B7` incorrectly includes the PID byte and gives a huge value. The profile applies the evDash offset and scaling formula.
- `HV_T_MAX` (`221E0E`) and `HV_T_MIN` (`221E0F`) are the maximum and minimum temperatures reported across the HV battery, in °C. The evDash comments indicate that the responses also include the corresponding temperature-point index; this profile extracts only the temperature.
- `HV_C_V_MAX` (`221E33`) and `HV_C_V_MIN` (`221E34`) are the highest and lowest HV battery cell voltages, in volts. They do not identify which cell has that voltage.

The current `HV_T_MIN`/`HV_T_MAX` observations were close to the independent outside thermometer while the battery was near ambient temperature. That single comparison supports plausibility, but does not characterize battery temperature under other conditions. The other HV parameters have not all been independently checked against diagnostic equipment.

### HV current cross-check while charging

During AC charging, the WiCAN readings were `HV_V = 378 V` and `HV_A = 25.04–25.12 A`, corresponding to about `9.47–9.50 kW` at the battery. At the same time, Peblar reported `10.322 kW` from the charger. At a lower charging rate, Peblar reported `1.303 kW` while WiCAN reported `376 V` and `2.68 A` (about `1.01 kW` at the battery). These are useful plausibility checks, not exact matches: Peblar measures AC input while `HV_V × HV_A` estimates battery-side power, and conversion losses and vehicle loads separate the readings.

The CO₂ and humidity PID initialisation sets request header `746` and response filter `7B0`. After adding the missing `ATSH746` request-header command, the WiCAN CO₂ test returned `25400 ppm`. This value is not validated: a direct ELM request returned no data, and the evDash reference labels the PID as interior CO₂ in ppm but leaves its parser unfinished. Compare against an independent cabin CO₂ meter before relying on the value.

The WiCAN humidity test returned `196%`. Since relative humidity cannot exceed 100%, the `HUMIDITY` byte currently has no validated percentage scaling. Compare it with an independent cabin hygrometer before using it.

The WiCAN battery temperature test returned `BATT_TEMP = 12.5 °C`; the same check returned `HV_T_MIN = 12.38 °C` and `HV_T_MAX = 13.13 °C`. An independent thermometer read 12.4 °C outside, close to the battery reading. This supports the reading's plausibility while the car is near ambient temperature; battery temperature can differ after driving or charging.
