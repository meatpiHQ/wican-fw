# Hyundai Ioniq Electric 38kWh (2020–2021) — WiCAN AutoPID Profile

## Vehicle Information

|                |                                          |
| -------------- | ---------------------------------------- |
| **Make/Model** | Hyundai Ioniq Electric (AE EV)           |
| **Battery**    | 38.3 kWh, 88 cells in series             |
| **Years**      | 2020–2021 facelift                       |
| **Tested on**  | 2021 Ioniq Electric 38kWh                |
| **CAN bus**    | 500 kbps, 11-bit ID (ISO 15765-4, ATSP6) |

## Compatibility

> [!WARNING]
> This profile was developed and tested on a **2021 Ioniq Electric 38kWh** only.
>
> It is **not compatible** with:
>
> - **2017–2019 Ioniq Electric 28kWh** — different battery (fewer cells), different byte positions
> - **Ioniq Hybrid / Plug-in Hybrid** — different powertrain architecture
>
> If you have an earlier model, some parameters may show incorrect values. Contributions welcome!

## Parameters

### BMS — Battery Management System (ECU 0x7E4)

| Parameter          | Schema Name     | PID     | Formula                        | Unit | Description                    |
| ------------------ | --------------- | ------- | ------------------------------ | ---- | ------------------------------ |
| State of Charge    | `SOC`           | 2201019 | B10/2                          | %    | BMS internal SOC               |
| Displayed SOC      | `SOC_D`         | 2201057 | B33/2                          | %    | Dashboard SOC                  |
| State of Health    | `SOH`           | 2201057 | [B34:B35]/10                   | %    | Battery degradation            |
| HV Voltage         | `HV_V`          | 2201019 | [B19:B20]/10                   | V    | Pack voltage                   |
| HV Current         | `HV_A`          | 2201019 | [B17:B18]/10                   | A    | Discharge current (unsigned\*) |
| HV Power           | `HV_W`          | 2201019 | HV_V × HV_A                    | W    | Calculated power               |
| Max Cell Voltage   | `HV_C_V_MAX`    | 2201019 | B31/50                         | V    | Highest cell                   |
| Max Cell ID        | `HV_C_V_MAX_NO` | 2201019 | B30                            | —    | Cell number                    |
| Min Cell Voltage   | `HV_C_V_MIN`    | 2201019 | B34/50                         | V    | Lowest cell                    |
| Min Cell ID        | `HV_C_V_MIN_NO` | 2201019 | B33                            | —    | Cell number                    |
| Battery Temp Max   | `HV_T_MAX`      | 2201019 | B22-40                         | °C   | Hottest module                 |
| Battery Temp Min   | `HV_T_MIN`      | 2201019 | B23-40                         | °C   | Coldest module                 |
| Coolant Inlet Temp | `HV_T_I`        | 2201019 | B26-40                         | °C   | Battery cooling loop           |
| 12V Battery        | `LV_V`          | 2201019 | B38/10                         | V    | Auxiliary battery              |
| Energy Charged     | `KWH_CHARGED`   | 2201019 | ([B49:B50]×65536+[B51:B52])/10 | kWh  | Lifetime total                 |
| AC Charging        | `CHARGING`      | 2201019 | (B15&32)/32                    | 0/1  | AC charger active              |
| DC Charging        | `CHARGING_DC`   | 2201019 | (B15&64)/64                    | 0/1  | DC fast charge active          |
| Drive Mode         | `DRIVE_MODE`    | 2201057 | B31                            | —    | Current drive mode             |

\* HV current is unsigned. During regenerative braking, values wrap around (e.g. ~6500 = negative current). Post-process with: `if value > 3000: current = value - 6553.6`

### MCU — Motor Control Unit (ECU 0x7E2)

| Parameter         | Schema Name | PID  | Formula      | Unit | Description                                         |
| ----------------- | ----------- | ---- | ------------ | ---- | --------------------------------------------------- |
| Gear State        | `GEAR`      | 2101 | B10          | —    | Raw gear byte (0x59=Park, 0x80=Drive, 0x82=Reverse) |
| Accelerator Pedal | `THROTTLE`  | 2101 | B13          | —    | 6=off, 32=full demand                               |
| Speed Proxy       | `SPEED`     | 2101 | B20×3.97+2.3 | km/h | Estimated speed (±3 km/h, ~4 km/h steps)            |

> [!NOTE] > **Speed and RPM:** The Ioniq Electric's VCU (0x7E0) is on a separate CAN bus not accessible through the OBD-II diagnostic port's primary bus. Standard OBD-II PIDs 0x0C (RPM) and 0x0D (speed) are therefore unavailable from any adapter that connects to the diagnostic CAN bus (pins 6/14). The `SPEED` parameter is a proxy derived from MCU byte d14, validated against GPS at R=0.997 across 67 km of driving (3 drive sessions). It provides ~4 km/h resolution, sufficient for efficiency analysis but not for real-time dashboards.

### Climate — FATC (ECU 0x7B3)

| Parameter         | Schema Name | PID     | Formula  | Unit | Description          |
| ----------------- | ----------- | ------- | -------- | ---- | -------------------- |
| Cabin Temperature | `T_CAB`     | 2201006 | B11/2-40 | °C   | Interior temperature |

### TPMS — Tire Pressure (ECU 0x7A0)

| Parameter            | Schema Name | PID    | Formula | Unit | Description |
| -------------------- | ----------- | ------ | ------- | ---- | ----------- |
| Front Left Pressure  | `TYRE_P_FL` | 22C00B | B10/5   | psi  |             |
| Front Right Pressure | `TYRE_P_FR` | 22C00B | B14/5   | psi  |             |
| Rear Left Pressure   | `TYRE_P_RL` | 22C00B | B19/5   | psi  |             |
| Rear Right Pressure  | `TYRE_P_RR` | 22C00B | B23/5   | psi  |             |
| Front Left Temp      | `TYRE_T_FL` | 22C00B | B11-50  | °C   |             |
| Front Right Temp     | `TYRE_T_FR` | 22C00B | B15-50  | °C   |             |
| Rear Left Temp       | `TYRE_T_RL` | 22C00B | B20-50  | °C   |             |
| Rear Right Temp      | `TYRE_T_RR` | 22C00B | B24-50  | °C   |             |

### Cluster (ECU 0x7C6)

| Parameter | Schema Name | PID    | Formula   | Unit | Description    |
| --------- | ----------- | ------ | --------- | ---- | -------------- |
| Odometer  | `ODOMETER`  | 22B002 | [B13:B14] | km   | Total distance |

## ECU Map

| ECU  | Request ID | Response ID | CAN Bus                | Name                                      |
| ---- | ---------- | ----------- | ---------------------- | ----------------------------------------- |
| BMS  | 0x7E4      | 0x7EC       | Diagnostic (pins 6/14) | Battery Management System                 |
| MCU  | 0x7E2      | 0x7EA       | Diagnostic (pins 6/14) | Motor Control Unit                        |
| —    | 0x7E3      | 0x7EB       | Diagnostic (pins 6/14) | Unknown (responds to Tester Present)      |
| —    | 0x7E5      | 0x7ED       | Diagnostic (pins 6/14) | Unknown (responds to Tester Present)      |
| VCU  | 0x7E0      | 0x7E8       | Separate bus           | Vehicle Control Unit — **not accessible** |
| FATC | 0x7B3      | 0x7BB       | Diagnostic (pins 6/14) | Climate Control                           |
| TPMS | 0x7A0      | 0x7A8       | Diagnostic (pins 6/14) | Tire Pressure Monitoring                  |
| CLU  | 0x7C6      | 0x7CE       | Diagnostic (pins 6/14) | Instrument Cluster                        |

## Known Limitations

| Item                    | Details                                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **Motor RPM**           | Not available — VCU is on a separate CAN bus                                                                       |
| **True vehicle speed**  | Not available — `SPEED` proxy from MCU d14 (±3 km/h)                                                               |
| **HV current sign**     | Unsigned encoding; regen shows as overflow (>6000A)                                                                |
| **Cell voltages**       | All 88 cells validated but not included — parameter names need to be added to WiCAN schema first (see Future Work) |
| **Outside temperature** | Byte position unconfirmed — removed pending validation                                                             |
| **Battery current**     | Not found in BMS 0x0101 (bytes 5–8 always zero); likely in BMS 0x0102–0x0104                                       |

## Installation

1. Open the WiCAN web interface
2. Go to **Automate** → **Vehicle Specific**
3. Upload the JSON file, or select the profile from the dropdown after it's merged
4. Reboot the WiCAN

## Future Work

- [ ] Add 88 individual cell voltage parameters (requires schema PR to add `HV_C_V_01`–`HV_C_V_88` to allowed names)
- [ ] Identify battery current signal in BMS DIDs 0x0102–0x0104
- [ ] Explore unknown ECUs 0x7E3 and 0x7E5 (OBC? EPCU?)
- [ ] Validate outside temperature byte position

## Methodology

All byte positions were determined through systematic reverse engineering:

1. **PID discovery** — Service 0x22 DID sweeps across BMS, MCU, HVAC, TPMS, and Cluster ECUs using SavvyCAN and python-can
2. **Raw log analysis** — Car Scanner Pro ELM327 logs parsed to identify ISO-TP frame boundaries and byte positions
3. **GPS ground truth** — Three drive sessions (67.1 km total) with concurrent GPS logging to validate speed proxy (R=0.997) and disprove earlier incorrect mappings
4. **Cross-validation** — All formulas verified against Car Scanner Pro decoded values and physical measurements (dashboard SOC, tire pressure gauge)

## Credits

Developed through extensive reverse engineering and testing by:

- **Sándor Balikó** ([@sbaliko](https://github.com/sbaliko)) — vehicle owner, PID discovery, formula development, and validation
- **Claude** (Anthropic) — log analysis, byte mapping, GPS correlation, and documentation

Special thanks to:

- [WiCAN / MeatPi](https://github.com/meatpiHQ/wican-fw) for the OBD-II WiFi adapter
- [Car Scanner Pro](https://www.carscanner.info/) used for initial PID discovery and cross-validation
- [JejuSoul/OBD-PIDs-for-HKMC-EVs](https://github.com/JejuSoul/OBD-PIDs-for-HKMC-EVs) community reference

## Version History

| Version | Date       | Changes                                                                                                                                                               |
| ------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| v1.0    | 2026-02-07 | Initial release with cell voltages and custom parameter names                                                                                                         |
| v2.0    | 2026-03-03 | Schema-compliant names, fixed B-number errors (HV_V, T_CAB, cell IDs), added speed proxy, tire temps, power calculation. Removed cell voltages pending schema update. |

## License

This profile is provided as-is for the WiCAN community. Feel free to use and modify.
