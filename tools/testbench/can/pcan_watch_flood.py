#!/usr/bin/env python3
"""PC-side companion for ble_slcan_bridge_test.py (and any bench that writes
slcan lines into the bus from the far side): watch the bus through PCAN and
prove the frames arrived with their full payload; optionally flood it so the
far side can measure its path under overload and its recovery afterwards.

  python tools/testbench/can/pcan_watch_flood.py --secs 240 --marker rpi001
         [--flood-secs 24 --fps 1000]
  python tools/testbench/can/pcan_watch_flood.py --secs 170 --pi rpi001
         --flood-epoch <E, Pi clock>

--marker HOST polls HOST:/tmp/ble_slcan_flood_go over ssh every 2 s and floods
0x123 for --flood-secs the moment the Pi-side bench creates it (the bench
does that once its BLE link is up, so the flood lands on legs D/E and not on
the connect retries). --flood-epoch is the wall-clock alternative (--pi
measures the PC-Pi clock offset; the PC ran 14 s ahead on 2026-09-20).
Writes every watched frame to --out (jsonl) and ends with a verdict line:
  PCAN WATCH PASS   every signature the BLE bench writes was seen, DLC 8
  PCAN WATCH FAIL: ...
Needs python-can with the PCAN backend (the IDF venv python has it).
"""
import argparse
import json
import subprocess
import sys
import threading
import time

import can

WATCH = {0x7DF, 0x18DB33F1, 0x7E8, 0x7E0}
MARKER = "/tmp/ble_slcan_flood_go"


def ssh(host, cmd, timeout=15):
    try:
        return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
                               host, cmd], capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


def pi_offset(host):
    """PC clock minus the Pi's, in seconds (0 when unreachable)."""
    t0 = time.time()
    out = ssh(host, "date +%s")
    try:
        return round(((t0 + time.time()) / 2) - int(out))
    except ValueError:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="PCAN_USBBUS2")
    ap.add_argument("--secs", type=float, default=240)
    ap.add_argument("--out", default="pcan_watch.jsonl")
    ap.add_argument("--marker", default="", help="ssh host whose marker file starts the flood")
    ap.add_argument("--pi", default="", help="ssh host to measure the clock offset against")
    ap.add_argument("--flood-epoch", type=float, default=0.0,
                    help="unix time (Pi clock when --pi is given) to start the flood")
    ap.add_argument("--flood-secs", type=float, default=24)
    ap.add_argument("--fps", type=float, default=1000)
    a = ap.parse_args()

    flood_at = 0.0
    if a.flood_epoch:
        offset = pi_offset(a.pi) if a.pi else 0
        flood_at = a.flood_epoch + offset
        print(f"flood at {flood_at:.0f} (PC clock, offset {offset:+d}s) for "
              f"{a.flood_secs}s at {a.fps:.0f} fps", flush=True)
    if a.marker:
        ssh(a.marker, f"rm -f {MARKER}")
        print(f"flood when {a.marker}:{MARKER} appears, {a.flood_secs}s at {a.fps:.0f} fps",
              flush=True)

    bus = can.Bus(interface="pcan", channel=a.channel, bitrate=500000)
    state = {"stop": False, "sent": 0, "flooded": False, "t_flood": None}
    t_end_all = time.time() + a.secs

    def flooder():
        if flood_at:
            d = flood_at - 2 - time.time()
            if d > 0:
                time.sleep(d)
        elif a.marker:
            while not state["stop"] and time.time() < t_end_all:
                if ssh(a.marker, f"test -e {MARKER} && echo go") == "go":
                    break
                time.sleep(2)
            else:
                return
        else:
            return
        state["flooded"] = True
        state["t_flood"] = time.time()
        print("flood start", flush=True)
        period = 1.0 / a.fps
        t_end = time.time() + a.flood_secs
        nxt = time.time()
        while time.time() < t_end and not state["stop"]:
            n = state["sent"]
            msg = can.Message(arbitration_id=0x123, is_extended_id=False,
                              data=[0xF0, n & 0xFF, (n >> 8) & 0xFF, 0, 0, 0, 0, 0])
            try:
                bus.send(msg, timeout=0.05)
                state["sent"] += 1
            except can.CanError:
                pass
            nxt += period
            s = nxt - time.time()
            if s > 0:
                time.sleep(s)
        print(f"flood done: {state['sent']} frames sent", flush=True)

    threading.Thread(target=flooder, daemon=True).start()
    rows = []
    total = 0
    with open(a.out, "w") as f:
        while time.time() < t_end_all:
            m = bus.recv(timeout=0.2)
            if m is None:
                continue
            total += 1
            if m.arbitration_id in WATCH:
                r = {"t": time.time(), "id": f"{m.arbitration_id:X}", "ext": m.is_extended_id,
                     "dlc": m.dlc, "data": m.data.hex().upper()}
                rows.append(r)
                f.write(json.dumps(r) + "\n")
    state["stop"] = True
    bus.shutdown()

    # ---- the BLE bench's write signatures (see ble_slcan_bridge_test.py)
    def find(pred):
        return [r for r in rows if pred(r)]

    def padded(tag):
        return find(lambda r: r["id"] == "7DF" and r["data"].startswith("020100" + tag)
                    and r["data"].endswith("445566"))
    sig = {
        "A_immediate": find(lambda r: r["id"] == "7DF" and r["data"] == "020100A1A2A3A4A5"),
        "B_sequential": find(lambda r: r["id"] == "7DF" and r["data"].startswith("020100B0")
                             and r["data"].endswith("112233")),
        "C_ext_29bit": find(lambda r: r["id"] == "18DB33F1" and r["data"] == "020100C1C2C3C4C5"),
        "C_vin_request": find(lambda r: r["id"] == "7DF" and r["data"] == "0209020000000000"),
        "D_flood_phase": padded("D0"),
        "E_after_flood": padded("E0"),
    }
    expect = {"A_immediate": 1, "B_sequential": 20, "C_ext_29bit": 1, "C_vin_request": 1}
    if state["flooded"]:
        expect["D_flood_phase"] = 30
        expect["E_after_flood"] = 5
    fails = []
    for k, want in expect.items():
        got = sig[k]
        ok = len(got) >= want and all(r["dlc"] == 8 for r in got)
        print(("PASS" if ok else "FAIL") + f": {k}  ({len(got)}/{want} on bus, "
              f"dlc={sorted({r['dlc'] for r in got}) or '-'})", flush=True)
        if not ok:
            fails.append(k)
    fc = find(lambda r: r["id"] == "7E0" and r["data"].startswith("300000"))
    print(f"info: flow control from BLE {len(fc)}x; capture {total} frames in {a.secs:.0f}s, "
          f"{len(rows)} watched -> {a.out}; flood "
          + (f"{state['sent']} frames" if state["flooded"] else "not triggered"), flush=True)
    print(("PCAN WATCH FAIL: " + ", ".join(fails)) if fails else "PCAN WATCH PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
