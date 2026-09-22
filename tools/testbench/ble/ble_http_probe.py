#!/usr/bin/env python3
"""Focused download probe for the HTTP-over-BLE tunnel (runs ON rpi001).

Uploads a 64 KB file, downloads it through the tunnel N times with btmon
tracing, and prints for each run: device tx PDUs/bytes (GET /api/ble delta,
read THROUGH THE TUNNEL so no WiFi path is needed), ATT PDUs + bytes seen ON
AIR by btmon, client rx PDUs/bytes, v2 counter holes. Locates a PDU loss:
device host -> air -> BlueZ/bleak. With --device-id/--mac the DUT needs no
WiFi at all (the STA often sits on the client-isolated dongle AP).

  sudo env PYTHONPATH=... python3 -u ble_http_probe.py --device-id 68ee8f5a653d --mac 68:EE:8F:5A:65:3C [--runs 3] [--size 65536]
  sudo env PYTHONPATH=... python3 -u ble_http_probe.py --dut 10.42.1.194 [--runs 3] [--size 65536]
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ble_link import (UUID_HTTP_IN, UUID_HTTP_OUT, Stream, api, bt_identity,  # noqa: E402
                      channel_stats, connect, prep_adapter, sh)
from ble_http_pi import Tunnel, rand_blob  # noqa: E402

TRACE = "/tmp/ble_http_probe.log"


def air_stats(path):
    txt = sh(f"btmon -r {path} 2>/dev/null")
    lens = [int(m) for m in re.findall(
        r"ATT: Handle Value (?:Notification|Indication) \(0x1[bd]\) len (\d+)", txt)]
    return len(lens), sum(lens) - 2 * len(lens)  # minus the 2-byte handle per PDU


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dut", default="10.42.1.194")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--size", type=int, default=65536)
    ap.add_argument("--passkey", type=int, default=421337)
    ap.add_argument("--acquire", type=int, default=1)
    ap.add_argument("--device-id", default="")
    ap.add_argument("--mac", default="", help="STA MAC from /api/info (BLE identity = STA MAC + 2)")
    a = ap.parse_args()

    if a.device_id and a.mac:
        device_id, mac = a.device_id, a.mac
    else:
        _, info = api(a.dut, "/api/info")
        device_id, mac = info["device_id"], info["mac"]
    name = f"WiC_{device_id}"
    identity = bt_identity(mac)
    blob = rand_blob(a.size, 99)
    path = "/data/probe.bin"

    bonded = prep_adapter(identity, "wican-bench-ap")
    client, cap = await connect(name, identity, a.passkey, bonded=bonded)
    s = Stream(client, UUID_HTTP_OUT, UUID_HTTP_IN, cap)
    if not a.acquire:
        s.start = lambda: client.start_notify(UUID_HTTP_OUT, None)  # unused
    await s.start()
    t = Tunnel(s)

    async def http_channel():
        stc, _, ble = await t.get("/api/ble")
        ch = next((c for c in ble.get("channels", []) if c.get("name") == "http"), {}) if isinstance(ble, dict) else {}
        return ch

    await t.request("POST", "/api/fs/mkdir?path=/data")
    st, _, body = await t.request("POST", f"/api/fs/upload?path={path}", blob,
                                  ct="application/octet-stream", timeout=120)
    print(f"tunnel upload {st} {body[:60]}", flush=True)
    ch = await http_channel()
    print(f"device: out={ch.get('out')} modes={ch.get('out_modes')} mtu cap={cap}", flush=True)

    for run in range(a.runs):
        subprocess.run(f"pkill -x btmon; rm -f {TRACE}", shell=True)
        mon = subprocess.Popen(["btmon", "-w", TRACE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(1)
        rx0 = s.rx_bytes
        n0 = s.notifs
        h0 = t.holes
        d0 = await http_channel()          # its own response PDUs are inside every count below
        t0 = time.monotonic()
        stc, rsp, back = await t.request("GET", f"/api/fs/download?path={path}", timeout=120)
        dt = time.monotonic() - t0
        await asyncio.sleep(0.5)
        d1 = await http_channel()
        await asyncio.sleep(0.5)
        mon.terminate()
        await asyncio.sleep(0.5)
        air_n, air_bytes = air_stats(TRACE)
        ok = back == blob
        dev_pdus = (d1.get('tx_notifications', 0) + d1.get('tx_indications', 0)
                    - d0.get('tx_notifications', 0) - d0.get('tx_indications', 0))
        print(f"run {run}: {'OK' if ok else 'LOSS'} status {stc} {len(back)}/{a.size} B in {dt:.1f}s "
              f"({a.size / 1024 / dt:.1f} KB/s) holes {t.holes - h0} | device tx {dev_pdus} PDUs "
              f"{d1.get('tx_bytes', 0) - d0.get('tx_bytes', 0)} B (timeouts {d1.get('tx_timeouts')}, "
              f"stalls?) | air {air_n} PDUs {air_bytes} B | client {s.notifs - n0} PDUs "
              f"{s.rx_bytes - rx0} B | resync {t.resync}", flush=True)
        t.resync = 0

    await t.request("DELETE", f"/api/fs/file?path={path}")
    print(f"total holes {t.holes}, credits sent {t.credits_sent}, client PDUs {s.notifs}", flush=True)
    await s.stop()
    await client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
