#!/usr/bin/env python3
"""Broadcast one CAN frame periodically — the "car" side of an autopid
filter window (DBC bench). Defaults match dbc_bench_test.py's fixture:
id 0x123, payload FF 38 64 10 27 00 00 00 →
  BenchTorque  [S0:S1]          = -200 Nm
  BenchTemp    B2-40            = 60 degC
  BenchSpeed   (B3+B4*256)*0.25 = 2500 rpm

--alt makes the sender ALTERNATE between --data and --alt each frame —
the two mux pages of a multiplexed message (dbc_bench leg 4b).

  python pcan_dbc_broadcast.py [secs] [--id 0x123] [--data FF38641027000000]
                               [--alt FF386410272A0100]
                               [--gap 0.02] [--pcan PCAN_USBBUS2]
"""
import argparse
import time

import can


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("secs", nargs="?", type=float, default=60)
    ap.add_argument("--id", default="0x123")
    ap.add_argument("--data", default="FF38641027000000")
    ap.add_argument("--alt", default="")
    ap.add_argument("--gap", type=float, default=0.02)
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    a = ap.parse_args()

    frame_id = int(a.id, 0)
    payloads = [bytes.fromhex(a.data)]
    if a.alt:
        payloads.append(bytes.fromhex(a.alt))
    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    n = 0
    end = time.time() + a.secs
    print(f"broadcasting 0x{frame_id:X} x{len(payloads)} payloads "
          f"every {a.gap*1000:.0f}ms")
    try:
        while time.time() < end:
            msg = can.Message(arbitration_id=frame_id,
                              is_extended_id=frame_id > 0x7FF,
                              data=payloads[n % len(payloads)])
            try:
                bus.send(msg, timeout=0.1)
                n += 1
            except can.CanError:
                pass
            time.sleep(a.gap)
    finally:
        bus.shutdown()
    print(f"sent {n} frames")


if __name__ == "__main__":
    main()
