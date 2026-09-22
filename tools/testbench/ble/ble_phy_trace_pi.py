#!/usr/bin/env python3
"""Where does the time go on the BLE HTTP tunnel? One download + one upload
under btmon, then the per-packet timing read off the air.

Runs ON rpi001. Connects (+ pairs) to the DUT, starts btmon, does one 64 KB
GET /api/fs/download and one 64 KB POST /api/fs/upload through the `http`
channel, stops btmon and prints:

  - the link parameters the controllers agreed (connection interval, PHY)
  - indications (device -> app, the download): count, median and p90 of
    indication -> confirmation (the ATT round trip the device waits for)
    and of confirmation -> next indication (device-side turnaround)
  - write commands (app -> device, the upload): count, median spacing
    (the client's per-write cost: bleak/D-Bus vs an AcquireWrite fd)

Usage:
  sudo env PYTHONPATH=/home/meatpi/.local/lib/python3.11/site-packages \
      python3 -u ble_phy_trace_pi.py --dut 10.42.1.194 [--size 65536]
"""
import argparse
import asyncio
import os
import re
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ble_http_pi import Tunnel, rand_blob  # noqa: E402
from ble_link import (UUID_HTTP_IN, UUID_HTTP_OUT, Stream, api, bt_identity,  # noqa: E402
                      connect, prep_adapter, sh)

LOG = "/tmp/ble_phy_trace.log"
TS = re.compile(r"\[hci0\] (\d+\.\d+)\s*$")


def pct(v, p):
    if not v:
        return 0.0
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))]


def parse(txt):
    """Group the btmon dump into packets (a packet starts with '<' or '>')."""
    pkts = []
    cur = None
    for line in txt.splitlines():
        if line.startswith(("< ", "> ")):
            m = TS.search(line)
            cur = {"hdr": line, "t": float(m.group(1)) if m else None, "body": []}
            pkts.append(cur)
        elif cur is not None:
            cur["body"].append(line.strip())
    return pkts


def analyse(txt):
    pkts = parse(txt)
    out = {}
    ind, conf, wcmd = [], [], []
    for p in pkts:
        body = "\n".join(p["body"])
        if p["t"] is None:
            continue
        if "ATT: Handle Value Indication" in body:
            ind.append(p["t"])
        elif "ATT: Handle Value Confirmation" in body:
            conf.append(p["t"])
        elif "ATT: Write Command" in body:
            wcmd.append(p["t"])
        elif "LE Enhanced Connection Complete" in body or "LE Connection Update Complete" in body \
                or "LE Connection Complete" in body:
            m = re.search(r"Connection interval: ([\d.]+) msec", body)
            if m:
                out.setdefault("intervals_ms", []).append(float(m.group(1)))
        elif "LE PHY Update Complete" in body:
            tx = re.search(r"TX PHY: (.+)", body)
            rx = re.search(r"RX PHY: (.+)", body)
            out["phy"] = (tx.group(1).strip() if tx else "?", rx.group(1).strip() if rx else "?")
    # pair each indication with the next confirmation
    rt, turn = [], []
    ci = 0
    last_conf = None
    for t in ind:
        while ci < len(conf) and conf[ci] < t:
            ci += 1
        if ci < len(conf):
            rt.append((conf[ci] - t) * 1000)
            if last_conf is not None:
                turn.append((t - last_conf) * 1000)
            last_conf = conf[ci]
    gaps = [(b - a) * 1000 for a, b in zip(wcmd, wcmd[1:])]
    out.update(indications=len(ind), confirmations=len(conf), rt_ms=rt, turn_ms=turn,
               write_cmds=len(wcmd), write_gap_ms=gaps)
    return out


async def run(a):
    _, info = api(a.dut, "/api/info")
    info = info if isinstance(info, dict) else {}
    device_id = info.get("device_id") or a.device_id
    name = f"WiC_{device_id}"
    identity = bt_identity(info.get("mac") or a.sta_mac)
    if not device_id:
        print("FATAL: DUT unreachable over WiFi; pass --device-id and --sta-mac")
        return
    bonded = prep_adapter(identity, a.nm_profile)
    client, cap = await connect(name, identity, a.passkey, bonded=bonded, tries=6)
    s = Stream(client, UUID_HTTP_OUT, UUID_HTTP_IN, cap)
    await s.start()
    t = Tunnel(s)
    blob = rand_blob(a.size, 5)
    await t.request("POST", "/api/fs/mkdir?path=/data/blephy")
    subprocess.run(f"pkill -x btmon; rm -f {LOG}", shell=True)
    mon = subprocess.Popen(["btmon", "-w", LOG], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(0.5)

    t0 = time.monotonic()
    st, rsp, body = await t.request("POST", "/api/fs/upload?path=/data/blephy/trace.bin", blob,
                                    ct="application/octet-stream", timeout=180)
    up = time.monotonic() - t0
    print(f"upload   {st} {a.size} B in {up:.1f} s = {a.size / 1024 / up:.2f} KB/s", flush=True)
    t0 = time.monotonic()
    st, rsp, back = await t.request("GET", "/api/fs/download?path=/data/blephy/trace.bin", timeout=300)
    dn = time.monotonic() - t0
    print(f"download {st} {len(back)} B exact={back == blob} in {dn:.1f} s = {a.size / 1024 / dn:.2f} KB/s",
          flush=True)
    await t.request("DELETE", "/api/fs/file?path=/data/blephy/trace.bin")
    await t.request("DELETE", "/api/fs/file?path=/data/blephy")
    mon.terminate()
    await asyncio.sleep(0.5)
    await s.stop()
    await client.disconnect()

    r = analyse(sh(f"btmon -r {LOG} 2>/dev/null"))
    print(f"link: intervals {r.get('intervals_ms')} ms, PHY {r.get('phy', '(no update on air)')}, "
          f"payload cap {cap}", flush=True)
    print(f"indications {r['indications']} / confirmations {r['confirmations']}: "
          f"ind->conf median {statistics.median(r['rt_ms']) if r['rt_ms'] else 0:.1f} ms "
          f"p90 {pct(r['rt_ms'], 0.9):.1f} ms; conf->next ind median "
          f"{statistics.median(r['turn_ms']) if r['turn_ms'] else 0:.1f} ms p90 {pct(r['turn_ms'], 0.9):.1f} ms",
          flush=True)
    print(f"write commands {r['write_cmds']}: spacing median "
          f"{statistics.median(r['write_gap_ms']) if r['write_gap_ms'] else 0:.1f} ms "
          f"p90 {pct(r['write_gap_ms'], 0.9):.1f} ms", flush=True)
    print(f"trace kept at {LOG} (btmon -r {LOG})", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="10.42.1.194")
    ap.add_argument("--size", type=int, default=65536)
    ap.add_argument("--passkey", type=int, default=421337)
    ap.add_argument("--nm-profile", default="wican-bench-ap")
    ap.add_argument("--device-id", default="", help="fallback when the DUT is not reachable over WiFi")
    ap.add_argument("--sta-mac", default="", help="fallback (BT identity = STA MAC + 2)")
    a = ap.parse_args()
    asyncio.run(run(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
