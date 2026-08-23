#!/usr/bin/env python3
"""USB-NCM byte-conservation bench (standard §7 performance-truth).

Runs over the DUT's CDC-NCM device-role link (PC 192.168.82.2 <->
DUT 192.168.82.1 — the connector role is SOFTWARE-switched via
usb_host_manager settings, see TESTING.md). The PC sends EXACTLY N UDP
datagrams of known size to the DUT's fw iperf server (started over
ws_cli THROUGH the same NCM link); the server's `iperf -r` total must
account for every byte. USB bulk is a reliable transport — the bound
is tight (0.5%).

Run on the PC (needs the DUT in role=device/class=ncm):
    python tools/testbench/usb_ncm_conservation_test.py
Expected final line: USB NCM CONSERVATION PASS
"""
import re
import socket
import sys
import time

import websocket

DUT = "192.168.82.1"
N = 20000
SIZE = 1000
PACE_EVERY = 50      # datagrams between 1 ms breathers (~16 Mbit/s)
BOUND_PCT = 0.5


def main():
    ws = websocket.create_connection(f"ws://{DUT}/ws/cli", timeout=5)
    ws.settimeout(3)
    ws.send("iperf -a\n")
    time.sleep(1)
    ws.send("iperf -s -u -p 5001 -t 60\n")
    time.sleep(2)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = bytes(SIZE)
    t0 = time.time()
    for i in range(N):
        s.sendto(payload, (DUT, 5001))
        if i % PACE_EVERY == PACE_EVERY - 1:
            time.sleep(0.001)
    dt = time.time() - t0
    s.close()
    print(f"sent {N} x {SIZE} B = {N * SIZE} B in {dt:.1f}s "
          f"({N * SIZE * 8 / dt / 1e6:.1f} Mbit/s)")

    time.sleep(2)
    ws.send("iperf -r\n")
    out = ""
    t0 = time.time()
    while time.time() - t0 < 5:
        try:
            out += ws.recv()
        except Exception:
            break
    ws.send("iperf -a\n")
    ws.close()

    m = re.findall(r"total\s+(\d+)\s+KBytes", out)
    if not m:
        print("FAIL: no DUT iperf report:", out[-200:])
        print("USB NCM CONSERVATION FAIL")
        return 1
    rx = int(m[-1]) * 1024
    tx = N * SIZE
    delta = 100.0 * (tx - rx) / tx
    print(f"accounting: sent {tx} B, DUT received {rx} B, "
          f"delta {delta:.3f}%")
    ok = abs(delta) <= BOUND_PCT
    if not ok:
        print(f"FAIL: delta {delta:.3f}% > {BOUND_PCT}%")
    print("USB NCM CONSERVATION " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
