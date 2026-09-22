#!/usr/bin/env python3
"""The PHY transfer legs from the PC's own Bluetooth adapter (Windows, WinRT).

Why this exists: the bench Pi's UB500 (RTL8761BU) cannot hold a 2M link with
the ESP32-S3 (both sides time out on the first 2M connection event,
2026-09-21) while the PC's Intel adapter runs 2M fine. So the 1M vs 2M
comparison is measured from here, same central for both PHYs. Pairing uses
a custom WinRT ceremony that PROVIDES the fixed passkey (bleak's own pair()
is Just-Works only).

  python tools/testbench/ble/ble_phy_pc.py --phy 2m [--passkey 421337] [--size 65536]

Prints the same PASS/METRIC lines as ble_phy_bench_pi.py's transfer leg,
with the live PHY read through the tunnel (GET /api/ble over BLE). Set the
DUT's `phy` between runs (settings PUT + submit; the Pi's AP path helper
/tmp/dut_set_ble.py or the web UI).
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bleak import BleakClient, BleakScanner  # noqa: E402

from ble_http_pi import Tunnel  # noqa: E402
from ble_link import UUID_HTTP_IN, UUID_HTTP_OUT, Stream  # noqa: E402
import ble_phy_bench_pi as B  # noqa: E402  (transfer_leg, check, metric, PHY_NAME)


async def pair_with_pin(client, passkey):
    """WinRT custom pairing that answers a ProvidePin request with the DUT's
    fixed passkey (MITM, authenticated); ConfirmOnly accepted too."""
    from winrt.windows.devices.enumeration import (DeviceInformation, DevicePairingKinds,
                                                   DevicePairingProtectionLevel,
                                                   DevicePairingResultStatus)
    info = await DeviceInformation.create_from_id_async(
        client._backend._requester.device_information.id)  # noqa: SLF001
    if info.pairing.is_paired:
        print("  (already paired with Windows)", flush=True)
        return True

    seen = []

    def handler(sender, args):
        kind = args.pairing_kind
        seen.append(str(kind))
        if kind == DevicePairingKinds.PROVIDE_PIN:
            args.accept_with_pin(f"{passkey:06d}")
        elif kind == DevicePairingKinds.DISPLAY_PIN:
            print(f"  (Windows shows pin {args.pin})", flush=True)
            args.accept()
        else:
            args.accept()  # ConfirmOnly / ConfirmPinMatch

    custom = info.pairing.custom
    token = custom.add_pairing_requested(handler)
    kinds = (DevicePairingKinds.PROVIDE_PIN | DevicePairingKinds.CONFIRM_ONLY |
             DevicePairingKinds.CONFIRM_PIN_MATCH | DevicePairingKinds.DISPLAY_PIN)
    try:
        res = await custom.pair_with_protection_level_async(
            kinds, DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION)
    finally:
        custom.remove_pairing_requested(token)
    ok = res.status == DevicePairingResultStatus.PAIRED
    print(f"  pairing: status {res.status} protection {res.protection_level_used} "
          f"ceremonies asked {seen} ({'ok' if ok else 'failed'})", flush=True)
    return ok


async def run(a):
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
        print("FATAL: no WiC_ advertiser seen by the PC adapter")
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
    chars = [c.uuid for s in client.services for c in s.characteristics]
    print(f"connected: MTU {mtu} (payload {cap}), {len(chars)} characteristics, "
          f"http channel {'present' if UUID_HTTP_OUT in chars else 'MISSING'}", flush=True)
    s = Stream(client, UUID_HTTP_OUT, UUID_HTTP_IN, cap)
    await s.start()
    t = Tunnel(s)
    await asyncio.sleep(2.0)
    st, rsp, b = await t.get("/api/ble")
    b = b if isinstance(b, dict) else {}
    want = 2 if a.phy == "2m" else (3 if a.phy == "coded" else 1)
    B.check(f"{a.phy}_link_phy_negotiated", st == 200 and b.get("phy_tx") == want and b.get("phy_rx") == want,
            f"phy_tx {B.PHY_NAME.get(b.get('phy_tx'), b.get('phy_tx'))} phy_rx "
            f"{B.PHY_NAME.get(b.get('phy_rx'), b.get('phy_rx'))} (setting {b.get('phy')}, witness tunnel, "
            f"central = PC adapter)")
    B.SIZE = a.size
    leg = await B.transfer_leg(t, None, a.phy, t)
    B.check(f"{a.phy}_no_stream_resync", t.resync == 0 and not t.stray, f"{t.resync} resyncs, {len(t.stray)} stray")
    print(f"RESULT {a.phy}: upload {leg['upload_kbs']:.2f} KB/s, download {leg['download_kbs']:.2f} KB/s, "
          f"rtt p50 {leg['rtt_p50_ms']:.0f} ms, notifications {s.notifs}", flush=True)
    await s.stop()
    await client.disconnect()
    fails = [n for n, ok in B.results if not ok]
    print(("PC PHY LEG FAIL: " + ", ".join(fails)) if fails else f"PC PHY LEG {a.phy} PASS", flush=True)
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--phy", default="2m", help="the DUT's configured phy (label + expected negotiation)")
    ap.add_argument("--passkey", type=int, default=421337)
    ap.add_argument("--size", type=int, default=65536)
    a = ap.parse_args()
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
