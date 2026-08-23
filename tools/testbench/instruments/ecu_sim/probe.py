#!/usr/bin/env python3
"""ECU-simulator health probe: GET its web root, read-only."""
import argparse
import sys
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://192.168.8.1/")
args = ap.parse_args()

try:
    with urllib.request.urlopen(args.url, timeout=5) as r:
        r.read(64)
    print(f"ECU SIM OK {args.url}")
    sys.exit(0)
except OSError as e:
    print(f"ECU sim unreachable ({e})")
    sys.exit(1)
