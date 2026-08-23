#!/usr/bin/env python3
"""DUT USB CDC-NCM link probe: one ping, read-only."""
import argparse
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--ip", default="192.168.82.1")
args = ap.parse_args()

cmd = (["ping", "-n", "1", "-w", "2000", args.ip] if sys.platform == "win32"
       else ["ping", "-c", "1", "-W", "2", args.ip])
if subprocess.run(cmd, capture_output=True).returncode == 0:
    print(f"USB link OK {args.ip}")
    sys.exit(0)
print("USB link down (DUT USB not in device mode / cable)")
sys.exit(1)
