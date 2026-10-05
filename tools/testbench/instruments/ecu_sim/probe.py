#!/usr/bin/env python3
"""ECU-simulator health probe: GET its web root, read-only."""
import argparse
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "../lib"))
import sim_net  # noqa: E402  (the simulator's own link on any LAN)

sim_net.install()

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
