#!/usr/bin/env python3
"""A moving dashboard on the bench CAN bus, from the PC's PCAN: what a
RealDash gauge needs to show life.

  C:\\Espressif\\tools\\python\\v6.0.2\\venv\\Scripts\\python.exe tools/testbench/can/dash_player.py
         [--channel PCAN_USBBUS1] [--bitrate 500000] [--secs 0] [--scale 1.0]

Frames (little-endian values, the RealDash default; the matching RealDash
XML is tools/testbench/can/fixtures/realdash_wican_bench.xml):

  id    period  bytes  value
  0x100  50 ms  0..1   RPM, a sawtooth 800 to 4000 over 8 s (RealDash target 37)
  0x101 100 ms  0      speed km/h, a triangle 0 to 120 over 20 s (target 81)
  0x102 500 ms  0      coolant C, 60 to 95 and back, slowly (target 14)
  0x103  20 ms  0..3   a frame counter (loss check on the receiving side)

About 82 frames/s (1.6 KB/s of RealDash frames on the pipe); --scale 2
doubles every period (half the load), --scale 0.5 halves it. Ctrl-C stops.
The ELM legs want this OFF (the chip's own bus is this bus): run it only
for the RealDash legs. Needs python-can with the PCAN backend (the IDF venv
python has it).
"""
import argparse
import struct
import sys
import time

import can


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--channel", default="PCAN_USBBUS1")
    ap.add_argument("--bitrate", type=int, default=500000)
    ap.add_argument("--secs", type=float, default=0, help="0 = until Ctrl-C")
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()

    bus = can.Bus(interface="pcan", channel=a.channel, bitrate=a.bitrate)
    periods = {0x100: 0.05 * a.scale, 0x101: 0.1 * a.scale,
               0x102: 0.5 * a.scale, 0x103: 0.02 * a.scale}
    nxt = {i: time.monotonic() for i in periods}
    t0 = time.monotonic()
    sent = 0
    counter = 0
    errors = 0
    last_report = t0
    print(f"dash player on {a.channel} at {a.bitrate} bit/s", flush=True)
    try:
        while not a.secs or time.monotonic() - t0 < a.secs:
            now = time.monotonic()
            t = now - t0
            for cid in sorted(periods, key=lambda i: nxt[i]):
                if now < nxt[cid]:
                    continue
                nxt[cid] += periods[cid]
                if cid == 0x100:
                    rpm = int(800 + (t % 8.0) / 8.0 * 3200)
                    data = struct.pack("<HBBBBBB", rpm, 0, 0, 0, 0, 0, 0)
                elif cid == 0x101:
                    ph = (t % 20.0) / 20.0
                    kph = int(240 * ph) if ph < 0.5 else int(240 * (1 - ph))
                    data = bytes([kph, 0, 0, 0, 0, 0, 0, 0])
                elif cid == 0x102:
                    ph = (t % 120.0) / 120.0
                    cool = int(60 + 70 * ph) if ph < 0.5 else int(60 + 70 * (1 - ph))
                    data = bytes([cool, 0, 0, 0, 0, 0, 0, 0])
                else:
                    counter = (counter + 1) & 0xFFFFFFFF
                    data = struct.pack("<II", counter, int(t * 1000) & 0xFFFFFFFF)
                try:
                    bus.send(can.Message(arbitration_id=cid, data=data, is_extended_id=False),
                             timeout=0.1)
                    sent += 1
                except can.CanError:
                    errors += 1
            if now - last_report >= 10:
                last_report = now
                print(f"  {round(t)} s: {sent} frames sent, {errors} send errors", flush=True)
            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()
    print(f"DASH PLAYER DONE frames={sent} errors={errors} secs={round(time.monotonic() - t0)}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
