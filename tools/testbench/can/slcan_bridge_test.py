#!/usr/bin/env python3
"""Bench-test the slcan CAN translator end to end.

Reconfigures the DUT so bridge `br_slcan` = can <-> slcan0 (tcp:3333) with the
`slcan` translator, reboots, then:
  * TX: send a Lawicel `t...` line over TCP -> PCAN must see the CAN frame.
  * RX: PCAN injects a frame -> a Lawicel `t...` line must arrive over TCP.

Run on the machine with PCAN (Windows, IDF venv python — has python-can):
  python slcan_bridge_test.py [dut_ip] [pcan_channel]
Defaults: dut 192.168.82.1, pcan PCAN_USBBUS2.  Needs python-can.
"""
import json
import socket
import sys
import time
import urllib.request

DUT = sys.argv[1] if len(sys.argv) > 1 else "192.168.82.1"
PCAN = sys.argv[2] if len(sys.argv) > 2 else "PCAN_USBBUS2"
BASE = "http://" + DUT
PORT = 3333


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip().startswith(("{", "[")) else t


def configure():
    """Point br_slcan at can<->slcan0 with the slcan translator; keep others."""
    bm = api("/api/settings/bridge_manager")
    # drop our own bridges AND any standing bridge holding one of our
    # endpoints (a bridge already on slcan0 would collide at validation)
    ours = {"can", "slcan0"}
    bridges = [b for b in bm.get("bridges", [])
               if b["name"] not in ("br_slcan", "br_slcan_ws") and
               not ({b.get("a"), b.get("b")} & ours)]
    bridges.append({"name": "br_slcan", "a": "can", "b": "slcan0",
                    "translator": "slcan", "enabled": True})
    api("/api/settings/bridge_manager", "PUT",
        {"bridges": bridges, "cli": True} if "cli" in bm else {"bridges": bridges})
    # make sure the socket + CAN bus are up
    api("/api/settings/submit", "POST")
    print("configured br_slcan (can<->slcan0/slcan); rebooting…")


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
        print("SLCAN BRIDGE FAIL: DUT did not come back")
        sys.exit(1)
    time.sleep(2)

    fails = []

    def check(name, ok, detail=""):
        print(("PASS" if ok else "FAIL") + ": " + name + (f"  ({detail})" if detail else ""))
        if not ok:
            fails.append(name)

    bus = can.Bus(interface="pcan", channel=PCAN, bitrate=500000)
    s = socket.create_connection((DUT, PORT), timeout=5)
    s.settimeout(3)
    # python-can slcan clients open with S6/O; harmless — the codec absorbs them
    s.sendall(b"S6\rO\r")
    time.sleep(0.3)
    # drain any greeting
    try:
        s.recv(256)
    except Exception:
        pass

    # ---- TX: TCP slcan -> CAN (PCAN observes) ----
    # flush PCAN
    while bus.recv(timeout=0):
        pass
    s.sendall(b"t1004DEADBEEF\r")  # id 0x100, dlc 4, DE AD BE EF
    rx = None
    end = time.time() + 2
    while time.time() < end:
        m = bus.recv(timeout=0.3)
        if m and m.arbitration_id == 0x100:
            rx = m
            break
    check("TX slcan->CAN (id 0x100 DEADBEEF)",
          rx is not None and bytes(rx.data) == bytes([0xDE, 0xAD, 0xBE, 0xEF]),
          rx and rx.data.hex())

    # ---- RX: CAN (PCAN injects) -> TCP slcan ----
    buf = b""
    bus.send(can.Message(arbitration_id=0x200, is_extended_id=False,
                         data=[0x11, 0x22]))
    end = time.time() + 2
    line = None
    while time.time() < end:
        try:
            buf += s.recv(256)
        except socket.timeout:
            pass
        if b"\r" in buf:
            for part in buf.split(b"\r"):
                if part.startswith((b"t200", b"T")):
                    line = part
            if line:
                break
    check("RX CAN->slcan (t2002 1122)",
          line is not None and line == b"t2002 1122".replace(b" ", b""),
          line)

    s.close()
    bus.shutdown()
    if fails:
        print("SLCAN BRIDGE FAIL:", ", ".join(fails))
        sys.exit(1)
    print("SLCAN BRIDGE PASS")


if __name__ == "__main__":
    main()
