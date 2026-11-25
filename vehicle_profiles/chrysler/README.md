# Chrysler Pacifica Hybrid

`pacifica_hybrid_2018.json` adds six PCM readings tested on a **2018 Pacifica Hybrid** with **WiCAN PRO 6.0.0-beta.7**. Other model years and firmware versions have not been tested. The existing `pacifica.json` profile is unchanged.

The profile retains the existing SOC request. Each PCM request selects transmit ID `7E0` and receive ID `7E8`; the SOC request explicitly restores the functional header and clears the receive filter. Expressions count the PCI/length byte as `B0`, matching the tested v6 replies and the published-profile parser. Guards check the expected PCI length, positive response SID `62`, and echoed DID before accepting a value.

## Captured replies

The examples below were recorded sequentially while parked; they are not simultaneous measurements. Both engine-off and brief engine-running samples were collected. Engine-running RPM samples ranged from 1,076 to 1,857.

| Request   | Parameter                       | Example reply after CAN ID | Decoded value                       |
| --------- | ------------------------------- | -------------------------- | ----------------------------------- |
| `22 022A` | Engine oil pressure             | `04 62 02 2A 8C`           | 81.22114 psi                        |
| `22 069E` | VVT oil pressure                | `04 62 06 9E 8A`           | 552 kPa                             |
| `22 069F` | VVT oil temperature             | `04 62 06 9F 78`           | 56 °C                               |
| `22 0234` | Cylinder 1 injector pulse width | `07 62 02 34 00 00 0D 20`  | 3.36 ms                             |
| `22 0255` | Cylinder 1 fuel mass            | `05 62 02 55 03 CF`        | 15.234375 mg/charge, inferred units |
| `22 02B2` | Cylinder 1 required fuel charge | `05 62 02 B2 03 8D`        | 14.203125 mg/charge, inferred units |

With the engine off, pressure and fuel-quantity samples were zero. Oil temperature remained nonzero. Injector pulse width briefly remained nonzero during an engine transition, so it should not alone be used to prove fuel consumption.

## Fuel-quantity units

The two fuel quantities use unsigned big-endian 16-bit values divided by 64. **Their mg/charge units are inferred, not factory-confirmed for these DIDs.** The `_EST` suffixes and parameter descriptions preserve that uncertainty. Neither signal is a direct fuel-flow measurement; this profile does not calculate gallons/hour, fuel used, or MPG.

- [OBDb Pacifica definitions](https://github.com/OBDb/Chrysler-Pacifica/blob/main/signalsets/v3/default.json) identify these DIDs and decoding scales, but mark the physical units unknown.
- [DiabloSport Trinity PID list, pages 23–24](https://www.catalograck.com/ImgVD/DIA/Trinity_Pidlist.pdf#page=23), under “07+ DCX Gas PIDS,” lists the matching fuel-mass and required-fuel-charge names in mg/charge.
- [Injector Dynamics: Chrysler injector characterization](https://injectordynamics.com/chrysler_injector_characterization/) describes Chrysler fuel mass per cylinder, per induction event.

Standard fuel-rate requests `01 5E` and `01 9D`, and standard airflow `01 10`, returned `NO DATA` during the test session and are not added.
