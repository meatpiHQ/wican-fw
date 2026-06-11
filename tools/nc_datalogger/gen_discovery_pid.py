#!/usr/bin/env python3
"""Generate discovery_auto_pid.json for NC Datalogger Phase 0 bus discovery.

For each candidate frame ID this emits 12 raw parameters: bytes B0-B7 and the
big-endian word pairs [B0:B1], [B2:B3], [B4:B5], [B6:B7]. Loaded as
auto_pid.json's top-level can_filters, these run via autopid's existing
ATCRA -> ATMA monitor loop and feed every value straight into the CSV
datalogger (see NC_DATALOGGER_PLAN.md Phase 0 / Phase 2).

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

CANDIDATE_IDS = [
    # HCAN0 / MS-CAN
    0x200, 0x201, 0x211, 0x215, 0x216, 0x218, 0x21A, 0x231,
    # HCAN1 / HS-CAN
    0x240, 0x420, 0x430, 0x4B0, 0x4EC, 0x4F0, 0x4F1, 0x0EC,
]

PERIOD_MS = 1000


def make_filter(frame_id: int) -> dict:
    tag = f"ID{frame_id:03X}"
    parameters = []

    for b in range(8):
        parameters.append({
            "name": f"{tag}_B{b}",
            "expression": f"B{b}",
            "unit": "raw",
            "class": "none",
            "period": PERIOD_MS,
        })

    for lo, hi in ((0, 1), (2, 3), (4, 5), (6, 7)):
        parameters.append({
            "name": f"{tag}_W{lo}{hi}",
            "expression": f"[B{lo}:B{hi}]",
            "unit": "raw",
            "class": "none",
            "period": PERIOD_MS,
        })

    return {
        "frame_id": f"0x{frame_id:03X}",
        "parameters": parameters,
    }


def main():
    config = {
        "car_model": "NC Datalogger - Phase 0 Discovery",
        # Keep the Mazda profile's vehicle-specific can_filters (RPM/Speed/Pedal,
        # Load/Coolant/Throttle/IAT) and the AAT 0146 poll active alongside the
        # discovery filters, for cross-reference and the Phase 0 AAT probe.
        "car_specific": "enable",
        "can_filters": [make_filter(fid) for fid in CANDIDATE_IDS],
    }
    print(json.dumps(config, indent=4))


if __name__ == "__main__":
    main()
