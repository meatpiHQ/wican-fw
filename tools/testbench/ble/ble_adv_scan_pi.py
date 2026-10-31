#!/usr/bin/env python3
"""Is the DUT on air? One scan on the Pi's adapter for an advertiser by name.

Runs ON rpi001 (bleak on the UB500), shipped and started by ble_pi_run.py:

  python tools/testbench/ble/ble_pi_run.py ble_adv_scan_pi.py --name WiC_68ee8f5a653d [--seconds 8]

Prints `ADV SEEN name=<name> addr=<addr> rssi=<dBm>` and exits 0 when the
name was in a report, `ADV NOT SEEN` with the names it did see and exit 1
otherwise. The witness of the Bluetooth memory profile bench (a profile that
kills advertising must show up here, not in a phone's hands).
"""
import argparse
import asyncio
import sys

from bleak import BleakScanner


async def scan(name, seconds):
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    seen = []
    for dev, adv in found.values():
        label = adv.local_name or dev.name or ""
        seen.append(label or dev.address)
        if label == name:
            print(f"ADV SEEN name={name} addr={dev.address} rssi={adv.rssi}", flush=True)
            return 0
    print("ADV NOT SEEN " + name + "  (seen: " + ", ".join(sorted(set(seen))[:12]) + ")",
          flush=True)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", required=True)
    ap.add_argument("--seconds", type=float, default=8.0)
    a = ap.parse_args()
    return asyncio.run(scan(a.name, a.seconds))


if __name__ == "__main__":
    sys.exit(main())
