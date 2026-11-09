#!/usr/bin/env python3
"""ECU sim v2 health probe: `/api/info` + `/api/ecu/status` over its USB
network link, read-only. `--url auto` finds the simulator (lib/sim_net.py:
WICAN_SIM_URL, else 10.8.8.1, else 192.168.8.1)."""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
import sim_net  # noqa: E402  (the simulator's own link on any LAN)

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="auto")
args = ap.parse_args()

base = sim_net.sim_url(args.url)
sim_net.install(base)
try:
    with urllib.request.urlopen(base + "/api/info", timeout=5) as r:
        info = json.loads(r.read().decode("utf-8", errors="replace"))
    with urllib.request.urlopen(base + "/api/ecu/status", timeout=5) as r:
        st = json.loads(r.read().decode("utf-8", errors="replace"))
except (OSError, ValueError) as e:
    print(f"ECU sim unreachable at {base} ({e})")
    sys.exit(1)
if not sim_net.is_simulator(info):
    print(f"{base} answers but is not the ECU simulator ({str(info)[:80]})")
    sys.exit(1)
hs = st.get("hs_can") or {}
bus = (f"HS {hs.get('bitrate', st.get('bitrate'))} bit/s "
       f"{'up' if hs.get('running', st.get('running')) else 'DOWN'}")
print(f"ECU SIM OK {base} fw {info.get('fw_version', '?')} {bus} profile "
      f"{st.get('profile', '?')}")
sys.exit(0)
