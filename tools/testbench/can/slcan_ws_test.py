#!/usr/bin/env python3
"""Bench-test the slcan translator over the WebSocket transport.

Reconfigures the DUT so bridge `br_slcan_ws` = can <-> ws_can (/ws/can) with
the `slcan` translator (br_echo is removed for the run — ws_can is
single-consumer), reboots, then:
  * TX: send a Lawicel `t...` line as a WS binary frame -> PCAN sees the frame.
  * RX: PCAN injects a frame -> a Lawicel `t...` line arrives as a WS frame.

Run on the machine with PCAN (Windows, IDF venv python — has python-can):
  python slcan_ws_test.py [dut_ip] [pcan_channel]
Defaults: dut 192.168.82.1, pcan PCAN_USBBUS2. WS client = wsmin.py (no deps).
Restore the canonical bridge set afterwards (br_echo ws_can<->obd0).
"""
import json
import sys
import time
import urllib.request

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
from wsmin import WS                     # noqa: E402

DUT = sys.argv[1] if len(sys.argv) > 1 else "192.168.82.1"
PCAN = sys.argv[2] if len(sys.argv) > 2 else "PCAN_USBBUS2"
BASE = "http://" + DUT
WS_PATH = "/ws/can"


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip().startswith(("{", "[")) else t


def configure():
    """br_slcan_ws = can<->ws_can/slcan; drop br_echo (ws_can single-consumer)."""
    bm = api("/api/settings/bridge_manager")
    bridges = [b for b in bm.get("bridges", [])
               if b["name"] not in ("br_slcan_ws", "br_slcan", "br_echo")]
    bridges.append({"name": "br_slcan_ws", "a": "can", "b": "ws_can",
                    "translator": "slcan", "enabled": True})
    api("/api/settings/bridge_manager", "PUT",
        {"bridges": bridges, "cli": True} if "cli" in bm else {"bridges": bridges})
    api("/api/settings/submit", "POST")
    print("configured br_slcan_ws (can<->ws_can/slcan); rebooting…")


def wait_up():
    for _ in range(40):
        try:
            api("/api/status")
            return True
        except Exception:
            time.sleep(1.5)
    return False


def main():
    import can  # python-can

    configure()
    time.sleep(6)
    if not wait_up():
        print("SLCAN WS FAIL: DUT did not come back")
        sys.exit(1)
    time.sleep(2)

    fails = []

    def check(name, ok, detail=""):
        print(("PASS" if ok else "FAIL") + ": " + name
              + ("  ({})".format(detail) if detail else ""))
        if not ok:
            fails.append(name)

    bus = can.Bus(interface="pcan", channel=PCAN, bitrate=500000)
    ws = WS(DUT, 80, WS_PATH)
    ws.settimeout(3)
    # python-can slcan clients open with S6/O; harmless — the codec absorbs them
    ws.send(b"S6\rO\r")
    time.sleep(0.3)
    for _ in ws.pump():
        pass  # drain any greeting

    # ---- TX: WS slcan -> CAN (PCAN observes) ----
    while bus.recv(timeout=0):
        pass
    ws.send(b"t1004DEADBEEF\r")  # id 0x100, dlc 4, DE AD BE EF
    rx = None
    end = time.time() + 2
    while time.time() < end:
        m = bus.recv(timeout=0.3)
        if m and m.arbitration_id == 0x100:
            rx = m
            break
    check("TX ws/slcan->CAN (id 0x100 DEADBEEF)",
          rx is not None and bytes(rx.data) == bytes([0xDE, 0xAD, 0xBE, 0xEF]),
          rx and rx.data.hex())

    # ---- RX: CAN (PCAN injects) -> WS slcan ----
    bus.send(can.Message(arbitration_id=0x200, is_extended_id=False,
                         data=[0x11, 0x22]))
    line = None
    buf = b""
    end = time.time() + 2
    ws.settimeout(0.3)
    while time.time() < end and line is None:
        try:
            frames = [ws.recv_frame()]
        except (OSError, ConnectionError):
            frames = []
        for _, payload in frames:
            buf += payload
            for part in buf.split(b"\r"):
                if part.startswith(b"t200"):
                    line = part
    check("RX CAN->ws/slcan (t2002 1122)",
          line == b"t20021122", line)

    ws.close()
    bus.shutdown()
    if fails:
        print("SLCAN WS FAIL:", ", ".join(fails))
        sys.exit(1)
    print("SLCAN WS PASS")


if __name__ == "__main__":
    main()
