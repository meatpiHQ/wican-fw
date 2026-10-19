#!/usr/bin/env python3
"""BLE multi-frame probe (2026-10-19): one BLE J2534 session, the ECU name (22 F1 87, a
15-byte answer) and the VIN (22 F1 90, 20 bytes) requested in turn, every frame the device
sends stamped on arrival (type, rx_status, bytes): the TxDone and START_OF_MESSAGE
indications, then the reassembled message. Written for the blej2534 gate's L4 VIN WARN,
which turned out to be the gate's own expected VIN typed one byte short (the answer always
came); kept as the timing witness for BLE multi-frame reports. `--gate-order` replays the
gate's CAN and filter legs first.

Run ON rpi001 between `ble_j2534_pi.py --stage configure` (BLE + J2534 on, the state file
written) and `--stage restore`:
  sudo env PYTHONPATH=/home/meatpi/.local/lib/python3.11/site-packages python3 -u ble_vin_probe.py
"""
import asyncio
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ble_j2534_pi as bj  # noqa: E402
from ble_link import (UUID_J2534_IN, UUID_J2534_OUT, Stream, bt_identity, connect,  # noqa: E402
                      find_dut, prep_adapter)

REQUESTS = ((b"\x22\xF1\x87", 2.0), (b"\x22\xF1\x90", 3.0), (b"\x22\xF1\x87", 2.0),
            (b"\x22\xF1\x90", 3.0), (b"\x10\x03", 1.5), (b"\x22\xF1\x90", 3.0),
            (b"\x22\xF1\x90", 3.0))


def fmt(body, rt):
    if rt != bj.RX_MSG or len(body) < bj.MSG.size:
        return "-", body.hex(" ").upper()
    f = bj.MSG.unpack_from(body, 0)
    return hex(f[1]), body[bj.MSG.size:bj.MSG.size + f[5]].hex(" ").upper()


async def run(saved, passkey, nm_profile):
    name = "WiC_%s" % saved["device_id"]
    identity = bt_identity(saved["sta_mac"])
    bonded = prep_adapter(identity, nm_profile, drop_bond=True)
    client, cap = await connect(name, identity, passkey, bonded=bonded)
    s = Stream(client, UUID_J2534_OUT, UUID_J2534_IN, cap)
    await s.start()
    t = bj.BleTester(s)
    print("HELLO", await t.call(bj.HELLO), "OPEN", await t.call(bj.OPEN), "cap", cap, flush=True)
    if "--gate-order" in sys.argv:
        # the blej2534 gate's L2 + L3 before its ISO15765 leg: a CAN channel,
        # 7DF [02 01 00] three times, a PASS then a BLOCK filter on 7E8, disconnect
        st, cch, _ = await t.call(bj.CONNECT, 0, struct.pack("<III", bj.PROT_CAN, 0, 500000))
        print("CONNECT CAN ->", hex(st), "ch", cch, flush=True)
        t.rx.clear()
        await t.write(cch, bj.can_msg(0x7DF, [0x02, 0x01, 0x00]))
        await t.collect_rx(1.5)
        print("  L2 rx:", [(c, d.hex(" ").upper()) for c, d in t.rx][:3], flush=True)
        st, fid, _ = await t.call(bj.START_FILTER, cch, struct.pack("<I", bj.PASS_FILTER) + bj.id_msg(0xFFFFFFFF) + bj.id_msg(0x7E8))
        t.rx.clear()
        await t.write(cch, bj.can_msg(0x7DF, [0x02, 0x01, 0x00]))
        await t.collect_rx(1.5)
        print("  L3 pass filter", hex(st), fid, "rx:", [(c, d.hex(" ").upper()) for c, d in t.rx][:3], flush=True)
        await t.call(bj.STOP_FILTER, cch, struct.pack("<I", fid))
        st, bid, _ = await t.call(bj.START_FILTER, cch, struct.pack("<I", bj.BLOCK_FILTER) + bj.id_msg(0xFFFFFFFF) + bj.id_msg(0x7E8))
        t.rx.clear()
        await t.write(cch, bj.can_msg(0x7DF, [0x02, 0x01, 0x00]))
        await t.collect_rx(1.0)
        print("  L3 block filter", hex(st), bid, "rx:", len(t.rx), flush=True)
        await t.call(bj.STOP_FILTER, cch, struct.pack("<I", bid))
        st, _, _ = await t.call(bj.DISCONNECT, cch)
        print("  DISCONNECT CAN ->", hex(st), flush=True)
    st, ch, _ = await t.call(bj.CONNECT, 0, struct.pack("<IIIII", bj.PROT_ISO15765, 0, 500000, 0x7E0, 0x7E8))
    print("CONNECT ISO15765 7E0/7E8 ->", hex(st), "ch", ch, flush=True)
    for req, secs in REQUESTS:
        t.rx.clear()
        t0 = time.monotonic()
        st, _, _ = await t.write(ch, bj.iso_msg(list(req)))
        tack = (time.monotonic() - t0) * 1000
        before = [d.hex(" ").upper() for c, d in t.rx]
        frames = []
        end = time.monotonic() + secs
        while time.monotonic() < end:
            try:
                rt, rseq, rch, body = await t._read_frame(max(0.05, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
            rxs, d = fmt(body, rt)
            frames.append(((time.monotonic() - t0) * 1000, rt, rch, rxs, d))
        print("%s -> ack %s after %.0f ms; before the ACK: %s" % (req.hex(" ").upper(), hex(st), tack, before), flush=True)
        for dt, rt, rch, rxs, d in frames:
            print("   +%6.0f ms  type 0x%02X ch %d rx_status %-5s %s" % (dt, rt, rch, rxs, d[:90]), flush=True)
        if not frames and not before:
            print("   (nothing)", flush=True)
    await t.call(bj.DISCONNECT, ch)
    await t.call(bj.CLOSE)
    await client.disconnect()


def main():
    saved = json.load(open(bj.STATE))
    nip, st = find_dut(saved.get("ip"), saved["device_id"], window=60)
    saved["ip"] = nip or saved.get("ip")
    passkey = int(sys.argv[1]) if len(sys.argv) > 1 else 421337
    asyncio.run(run(saved, passkey, "wican-bench-ap"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
