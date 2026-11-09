#!/usr/bin/env python3
"""DUT supply health probe: ECU sim v2's `/api/power` (read-only), or a
legacy OWON's *IDN? when `--psu` names a COM port."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))

ap = argparse.ArgumentParser()
ap.add_argument("--psu", default="auto",
                help="auto = ECU sim v2's supply; http://host = that simulator; COMx = an OWON")
args = ap.parse_args()

import bench_psu  # noqa: E402

try:
    psu = bench_psu.open_psu(args.psu, verbose=False)
except SystemExit as e:
    print(f"supply absent ({e})")
    sys.exit(1)
try:
    st = psu.status() if hasattr(psu, "status") else None
    v = psu.meas_voltage()
    ma = psu.meas_current() * 1000
    on = psu.output_on()
    faults = ""
    if st and any((st.get("faults") or {}).values()):
        faults = " FAULT " + ",".join(k for k, f in st["faults"].items() if f)
    print(f"SUPPLY OK {v:.2f} V {ma:.0f} mA {'on' if on else 'OFF'} "
          f"(set {psu.voltage_setpoint():.2f} V){faults}")
finally:
    psu.close()
sys.exit(0)
