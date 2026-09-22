#!/usr/bin/env python3
"""PC twin of ble_http_probe.py: the HTTP-over-BLE tunnel (protocol v2, notify
mode) from the PC's own adapter through bleak/WinRT. No WiFi path and no
btmon: it measures what an app sees. Uploads a blob through the tunnel, then
downloads it N times: byte-exact, v2 counter holes, CREDIT frames sent, the
device's PDU counters (GET /api/ble through the tunnel) per run.

  C:\\Espressif\\tools\\python\\v5.5.3\\venv\\Scripts\\python.exe tools\\testbench\\ble\\ble_http_probe_pc.py [--runs 6] [--size 65536] [--passkey 421337]
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bleak import BleakClient, BleakScanner  # noqa: E402

from ble_http_pi import Tunnel, rand_blob  # noqa: E402
from ble_link import UUID_HTTP_IN, UUID_HTTP_OUT, Stream  # noqa: E402
from ble_phy_pc import pair_with_pin  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--size", type=int, default=65536)
    ap.add_argument("--passkey", type=int, default=421337)
    ap.add_argument("--force-notify", type=int, default=1, help="write CCCD 0x0001 on FFF3 (WinRT picks indications otherwise)")
    a = ap.parse_args()

    hit = None
    for _ in range(3):
        found = await BleakScanner.discover(timeout=8.0, return_adv=True)
        for d, adv in found.values():
            if (adv.local_name or d.name or "").startswith("WiC_"):
                hit = (d, adv)
                break
        if hit:
            break
    if not hit:
        print("FATAL: no WiC_ advertiser seen by the PC adapter", flush=True)
        return 1
    d, adv = hit
    print(f"found {adv.local_name} {d.address} rssi {adv.rssi}", flush=True)
    client = BleakClient(d, timeout=30.0)
    await client.connect()
    if not await pair_with_pin(client, a.passkey):
        await client.disconnect()
        return 1
    await asyncio.sleep(1.0)
    mtu = getattr(client, "mtu_size", 23) or 23
    cap = max(20, min(490, mtu - 3))
    s = Stream(client, UUID_HTTP_OUT, UUID_HTTP_IN, cap)
    await s.start()
    if a.force_notify:
        # bleak/WinRT writes CCCD 0x0002 (indications) when a characteristic
        # offers both; an app that wants the fast path writes 0x0001 itself
        # (Android: ENABLE_NOTIFICATION_VALUE). Write it here and the device
        # switches its OUT mode on the SUBSCRIBE event (GET /api/ble `out`).
        ch = client.services.get_characteristic(UUID_HTTP_OUT)
        cccd = next((d for d in ch.descriptors if str(d.uuid).startswith("00002902")), None)
        if cccd is None:
            print("  (no CCCD descriptor exposed by WinRT; staying with its choice)", flush=True)
        else:
            try:
                await client.write_gatt_descriptor(cccd.handle, b"\x01\x00")
                print("  CCCD 0x0001 written: notifications", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"  (CCCD write refused by WinRT: {str(e)[:80]})", flush=True)
    t = Tunnel(s)          # notify mode: credits + counter
    await asyncio.sleep(1.5)

    async def http_channel():
        st, _, ble = await t.get("/api/ble")
        if not isinstance(ble, dict):
            return {}
        return next((c for c in ble.get("channels", []) if c.get("name") == "http"), {})

    blob = rand_blob(a.size, 99)
    path = "/data/probe_pc.bin"
    await t.request("POST", "/api/fs/mkdir?path=/data")
    t0 = time.monotonic()
    st, _, body = await t.request("POST", f"/api/fs/upload?path={path}", blob,
                                  ct="application/octet-stream", timeout=120)
    print(f"tunnel upload {st} {body[:60]} in {time.monotonic() - t0:.1f}s "
          f"({a.size / 1024 / max(time.monotonic() - t0, 1e-3):.1f} KB/s)", flush=True)
    ch = await http_channel()
    print(f"device: out={ch.get('out')} modes={ch.get('out_modes')} MTU {mtu} cap {cap}", flush=True)

    total_holes = 0
    for run in range(a.runs):
        n0, rx0, h0, c0 = s.notifs, s.rx_bytes, t.holes, t.credits_sent
        d0 = await http_channel()
        t0 = time.monotonic()
        stc, rsp, back = await t.request("GET", f"/api/fs/download?path={path}", timeout=120)
        dt = time.monotonic() - t0
        d1 = await http_channel()
        ok = back == blob
        dev = (d1.get("tx_notifications", 0) + d1.get("tx_indications", 0)
               - d0.get("tx_notifications", 0) - d0.get("tx_indications", 0))
        print(f"run {run}: {'OK' if ok else 'LOSS'} status {stc} {len(back)}/{a.size} B in {dt:.2f}s "
              f"({a.size / 1024 / dt:.1f} KB/s) holes {t.holes - h0} credits {t.credits_sent - c0} | "
              f"device tx {dev} PDUs (timeouts {d1.get('tx_timeouts')}) | client {s.notifs - n0} PDUs "
              f"{s.rx_bytes - rx0} B | resync {t.resync}", flush=True)
        total_holes += t.holes - h0

    await t.request("DELETE", f"/api/fs/file?path={path}")
    print(f"total holes {total_holes}, credits sent {t.credits_sent}, client PDUs {s.notifs}", flush=True)
    await s.stop()
    await client.disconnect()
    return 0 if total_holes == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
