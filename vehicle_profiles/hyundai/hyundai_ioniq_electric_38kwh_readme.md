# Hyundai Ioniq Electric 38kWh (2020-2021) WiCAN AutoPID Profile

## Vehicle Information
- **Make/Model:** Hyundai Ioniq Electric (AE EV)
- **Battery:** 38.3 kWh (88 cells in series)
- **Years:** 2020-2021 facelift
- **Tested on:** 2021 Ioniq Electric 38kWh

## Compatibility Note

⚠️ **This profile was developed and tested exclusively on a 2021 Ioniq Electric 38kWh.**

It may **NOT** be compatible with:
- **2017-2019 Ioniq Electric 28kWh** - Different battery configuration (fewer cells), possibly different byte positions
- **Ioniq Hybrid / Plug-in Hybrid** - Different powertrain architecture

If you have an earlier model, some parameters may not work or show incorrect values. Contributions to add support for other variants are welcome!

## Profile Features

### ✅ Confirmed Working Parameters

| Parameter | ECU | PID | Description |
|-----------|-----|-----|-------------|
| SOC_BMS | 7E4 | 2201019 | Battery state of charge (%) |
| SOC_Display | 7E4 | 2201057 | Dashboard displayed SOC (%) |
| SOH_pct | 7E4 | 2201057 | State of health (%) |
| HV_Voltage_V | 7E4 | 2201019 | High voltage battery voltage |
| HV_Current_A | 7E4 | 2201019 | HV battery current (positive=discharge, overflow for regen*) |
| Cell_01-88_V | 7E4 | 220102-04 | All 88 individual cell voltages |
| Cell_V_Max/Min | 7E4 | 2201019 | Highest/lowest cell voltage |
| Batt_Max/Min_Temp_C | 7E4 | 2201019 | Battery temperature range |
| Batt_Inlet_Temp_C | 7E4 | 2201019 | Battery coolant inlet temperature |
| Aux_Batt_V | 7E4 | 2201019 | 12V auxiliary battery voltage |
| Isolation_kOhm | 7E4 | 2201019 | HV isolation resistance |
| Charged/Discharged_kWh | 7E4 | 2201019 | Lifetime energy counters |
| Capacitor_V | 7E4 | 220101 | DC link capacitor voltage |
| Charging_AC | 7E4 | 2201019 | AC charging active (1=yes) |
| Charging_DC | 7E4 | 2201019 | DC fast charging active (1=yes) |
| Cabin_Temp_C | 7B3 | 2201006 | Cabin temperature |
| Gear_Park/Drive/Reverse | 7E2 | 2101 | Current gear state |
| Regen_Level | 7E2 | 2101 | Regenerative braking level |
| Inverter_Temp_C | 7E2 | 2101 | Motor inverter temperature |
| TPMS_FL/FR/RL/RR_psi | 7A0 | 22C00B | Tire pressures |
| Odometer_km | 7C6 | 22B002 | Odometer reading |

### ⚠️ Known Limitations

| Parameter | Issue | Notes |
|-----------|-------|-------|
| HV_Current_A | Unsigned overflow during regen | Values >6000A indicate negative current; convert with: `(value × 10 - 65536) / 10` |
| Motor RPM | Not available | Ioniq EV does not expose motor RPM via OBD-II |
| Vehicle Speed | Not in this profile | Available via GPS or needs further reverse engineering |
| Outside_Temp_C | Byte position unconfirmed | Removed pending SavvyCAN verification |

### ECU Addresses Used

| ECU | Request ID | Response ID | Description |
|-----|------------|-------------|-------------|
| BMS | 7E4 | 7EC | Battery Management System |
| VMCU | 7E2 | 7EA | Vehicle Motor Control Unit |
| FATC | 7B3 | 7BB | Climate Control |
| TPMS | 7A0 | 7A8 | Tire Pressure Monitoring |
| CLU | 7C6 | 7CE | Cluster (Instrument Panel) |

## Installation

1. Open WiCAN web interface
2. Go to AutoPID settings
3. Select "Hyundai Ioniq Electric 38kWh" from car model dropdown
4. Or import the JSON file manually

## Notes

- **Signed Current Values:** WiCAN doesn't support signed integers. During regenerative braking, HV_Current_A shows large values (~6400-6550) which represent negative current. Post-process in your logging software.
- **Cell Voltage Gaps:** Bytes B16, B24, B32, B40 are skipped in cell voltage PIDs due to ISO-TP frame boundaries.
- **Charging Detection:** Use `Charging_AC` or `Charging_DC` flags. `Charge_State_Raw` (B15) provides the raw byte for debugging.

## Credits

This profile was developed through extensive reverse engineering and testing by:
- **Sándor Balikó** (GitHub: @sbaliko) - Vehicle owner, PID discovery, formula development, and validation
- **Claude (Anthropic AI)** - Log analysis, debugging assistance, and documentation

Special thanks to:
- [WiCAN/MeatPi](https://github.com/meatpiHQ/wican-fw) for the excellent OBD-II WiFi adapter
- [Car Scanner Pro](https://www.carscanner.info/) used for initial PID discovery

## Version History

- **v1.0** (2026-02-07): Initial release with 88 cell voltages, charging status, TPMS, and core BMS parameters

## License

This profile is provided as-is for the WiCAN community. Feel free to use and modify.
