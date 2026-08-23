#!/usr/bin/env python3
"""PCAN health probe: open+shutdown the bus, no TX. Tries the configured
channel first, then the other USB slot (the enumeration order moves)."""
import argparse
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--channel", default="PCAN_USBBUS2")
ap.add_argument("--bitrate", type=int, default=500000)
args = ap.parse_args()

import can  # noqa: E402 — needs python-can (IDF venv has it)

others = [c for c in ("PCAN_USBBUS1", "PCAN_USBBUS2") if c != args.channel]
last = None
for ch in [args.channel] + others:
    try:
        bus = can.Bus(interface="pcan", channel=ch, bitrate=args.bitrate)
        bus.shutdown()
        print(f"PCAN OK {ch}")
        sys.exit(0)
    except Exception as e:              # noqa: BLE001 - report any
        last = e
print(f"PCAN absent ({last})")
sys.exit(1)
