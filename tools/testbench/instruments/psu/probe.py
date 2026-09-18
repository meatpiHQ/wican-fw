#!/usr/bin/env python3
"""OWON PSU health probe: *IDN? over SCPI/RS232, read-only."""
import argparse
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--port", default="auto", help="auto = the FTDI port detect_ports.py finds")
ap.add_argument("--baud", type=int, default=115200)
args = ap.parse_args()

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import bench_ports  # noqa: E402

args.port = bench_ports.resolve(args.port, "psu", "COM2016")

import serial  # noqa: E402 — pyserial (IDF venv has it)

try:
    s = serial.Serial(args.port, args.baud, timeout=2)
except serial.SerialException as e:
    print(f"PSU absent ({e})")
    sys.exit(1)
try:
    s.write(b"*IDN?\n")
    idn = s.readline().decode(errors="replace").strip()
finally:
    s.close()
if idn:
    print(f"PSU OK {idn[:40]}")
    sys.exit(0)
print(f"PSU port {args.port} open but no *IDN? answer (powered off?)")
sys.exit(1)
