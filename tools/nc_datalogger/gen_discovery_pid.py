#!/usr/bin/env python3
"""Generate discovery_auto_pid.json for NC Datalogger Phase 0 bus discovery.

For each candidate frame ID this emits 12 raw parameters: bytes B0-B7 and the
big-endian word pairs [B0:B1], [B2:B3], [B4:B5], [B6:B7]. Loaded as
auto_pid.json's top-level can_filters, these run via autopid's existing
ATCRA -> ATMA monitor loop and feed every value straight into the CSV
datalogger (see NC_DATALOGGER_PLAN.md Phase 0 / Phase 2).

Naming (so the CSV columns are readable, not "ID200_B0"):
  <Subsystem>_<FRAME>_<pos>   e.g. EngTorq_200_B0, WheelSpd_4B0_W23
The <Subsystem> prefix is the documented ROLE of the frame (from can_bus.md
Section 3, reproduced below); <pos> is the raw byte/word position kept verbatim
so the discovery cross-reference still works (you can still see exactly which
physical byte moved). Where the byte-level signal is ALREADY reverse-engineered
and shipping in vehicle_profiles/mazda/mx5_nc.json, the parameter is named after
the real signal instead (RPM/Speed/Pedal on 0x201, Load/Coolant/Throttle/IAT on
0x240) -- still suffixed with _<FRAME>_<pos> so it stays unique and never
collides with the mx5_nc profile's own decoded columns (car_specific stays
"enable", so both run together). Values remain raw (unit "raw"); turning a raw
byte into a physical unit is Phase 1, after this capture confirms which IDs and
bytes actually move.

IMPORTANT: bytes whose meaning is NOT yet reverse-engineered are deliberately
left at the <Subsystem>_<FRAME>_<pos> form -- the prefix only claims the frame's
role, never an unproven per-byte meaning. Fill in a per-byte name here only once
can_bus.md documents that byte.

Candidate IDs and bus assignment are taken from nc-flash-re's can_bus.md
mailbox map (Section 3, Message Summary):

  HCAN0 / MS-CAN (powertrain bus, shares the OBD-II port wiring):
    0x200 engine torque/throttle, 0x201 RPM/cruise/pedal,
    0x211 cluster speed/cruise switches, 0x215 torque A/B/C,
    0x216 TCM/ABS wheel data, 0x218 chassis speed/brake,
    0x21A brake pressure/pedal, 0x231 AT gear position

  HCAN1 / HS-CAN (body bus -- visibility at the OBD port is the open question):
    0x240 powertrain->body, 0x420 cruise status/ECT gauge,
    0x430 cluster/EPS odometer, 0x4B0 ABS/DSC wheel speeds,
    0x4EC cruise control detail, 0x4F0 vehicle config 1 (displacement/fuel),
    0x4F1 vehicle config 2 (tire size), 0x0EC flex-fuel ethanol content (Romdrop)

Run: python3 gen_discovery_pid.py > discovery_auto_pid.json
"""

import json

PERIOD_MS = 1000

# Per-frame naming table, in the bus order they get monitored.
#   prefix  : short PascalCase label for the frame's documented ROLE.
#   known   : {position -> full parameter name} for bytes/words whose meaning is
#             ALREADY reverse-engineered (today: only what mx5_nc.json ships).
#             Anything not listed here falls back to <prefix>_<FRAME>_<pos>, which
#             claims the frame role but NOT a per-byte meaning.
# position keys are "B0".."B7" and "W01"/"W23"/"W45"/"W67".
FRAMES = [
    # ---- HCAN0 / MS-CAN ----
    (0x200, "EngTorq",   {}),                       # engine torque / throttle
    (0x201, "EngSpd", {                             # RPM / cruise / pedal
        "B0":  "RPM_201_B0",   "B1":  "RPM_201_B1",   "W01": "RPM_201_W01",
        "B4":  "Speed_201_B4", "B5":  "Speed_201_B5", "W45": "Speed_201_W45",
        "B6":  "Pedal_201_B6",
    }),
    (0x211, "Cluster",   {}),                       # cluster speed / cruise switches
    (0x215, "TorqABC",   {}),                       # torque A/B/C
    (0x216, "TcmAbs",    {}),                        # TCM / ABS wheel data
    (0x218, "Chassis",   {}),                        # chassis speed / brake
    (0x21A, "Brake",     {}),                        # brake pressure / pedal
    (0x231, "AtGear",    {}),                        # AT gear position
    # ---- HCAN1 / HS-CAN ----
    (0x240, "Pwrtrn", {                              # powertrain -> body
        "B0": "Load_240_B0",     "B1": "Coolant_240_B1",
        "B3": "Throttle_240_B3", "B4": "IAT_240_B4",
    }),
    (0x420, "CruiseEct", {}),                        # cruise status / ECT gauge
    (0x430, "Odo",       {}),                        # cluster / EPS odometer
    (0x4B0, "WheelSpd",  {}),                        # ABS / DSC wheel speeds
    (0x4EC, "CruiseCtl", {}),                        # cruise control detail
    (0x4F0, "VehCfg1",   {}),                        # vehicle config 1 (displacement/fuel)
    (0x4F1, "VehCfg2",   {}),                        # vehicle config 2 (tire size)
    (0x0EC, "FlexFuel",  {}),                        # flex-fuel ethanol content
]


def make_filter(frame_id: int, prefix: str, known: dict) -> dict:
    fid = f"{frame_id:03X}"

    def name_for(pos: str) -> str:
        return known.get(pos, f"{prefix}_{fid}_{pos}")

    parameters = []

    for b in range(8):
        pos = f"B{b}"
        parameters.append({
            "name": name_for(pos),
            "expression": f"B{b}",
            "unit": "raw",
            "class": "none",
            "period": PERIOD_MS,
        })

    for lo, hi in ((0, 1), (2, 3), (4, 5), (6, 7)):
        pos = f"W{lo}{hi}"
        parameters.append({
            "name": name_for(pos),
            "expression": f"[B{lo}:B{hi}]",
            "unit": "raw",
            "class": "none",
            "period": PERIOD_MS,
        })

    return {
        "frame_id": f"0x{fid}",
        "parameters": parameters,
    }


def main():
    config = {
        "car_model": "NC Datalogger - Phase 0 Discovery",
        # Keep the Mazda profile's vehicle-specific can_filters (RPM/Speed/Pedal,
        # Load/Coolant/Throttle/IAT) and the AAT 0146 poll active alongside the
        # discovery filters, for cross-reference and the Phase 0 AAT probe.
        "car_specific": "enable",
        "can_filters": [make_filter(fid, prefix, known) for fid, prefix, known in FRAMES],
    }
    print(json.dumps(config, indent=4))


if __name__ == "__main__":
    main()
