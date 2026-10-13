#!/usr/bin/env python3
"""A 48-byte SNTP responder for the bench (runs ON rpi001 as root: UDP 123).

Answers every request with THIS host's clock plus an offset, so a DUT
whose `rtc_manager.ntp_server` points at the Pi can be stepped forwards
or backwards on demand (the log clock bench, TESTING.md). The offset is
re-read from --offset-file at every request when that file exists (the
bench writes a number of seconds into it), else --offset applies. One
line per answer on stdout (`journalctl -u sntp_bench`).

  sudo python3 sntp_server.py [--offset 0] [--offset-file /tmp/sntp_offset]
                              [--port 123] [--bind 0.0.0.0]

The reply is what lwIP's client checks at its default level (mode server,
stratum != 0, the transmit timestamp as the time): originate = the
client's transmit stamp, receive = transmit = now + offset, stratum 2,
reference id LOCL.
"""
import argparse
import os
import socket
import struct
import sys
import time

NTP_DELTA = 2208988800  # 1900-01-01 to 1970-01-01


def ntp_stamp(t):
    sec = int(t) + NTP_DELTA
    frac = int((t - int(t)) * (1 << 32))
    return (sec & 0xFFFFFFFF, frac & 0xFFFFFFFF)


def read_offset(path, default):
    if path:
        try:
            with open(path) as f:
                return float(f.read().strip() or "0")
        except (OSError, ValueError):
            pass
    return default


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--offset", type=float, default=0.0, help="seconds added to the host clock")
    ap.add_argument("--offset-file", default="/tmp/sntp_offset",
                    help="a file holding the offset in seconds; re-read per request")
    ap.add_argument("--port", type=int, default=123)
    ap.add_argument("--bind", default="0.0.0.0")
    a = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((a.bind, a.port))
    print(f"SNTP READY on {a.bind}:{a.port} offset {a.offset:+.0f} s "
          f"(file {a.offset_file})", flush=True)
    n = 0
    while True:
        try:
            data, peer = s.recvfrom(1024)
        except OSError as e:
            print(f"recv: {e}", flush=True)
            time.sleep(0.5)
            continue
        if len(data) < 48:
            continue
        offset = read_offset(a.offset_file, a.offset)
        now = time.time() + offset
        li_vn_mode = data[0]
        vn = (li_vn_mode >> 3) & 0x7
        poll = data[2]
        ref_sec, ref_frac = ntp_stamp(now)
        reply = struct.pack("!BBBb", (0 << 6) | (vn << 3) | 4, 2, poll, -20)
        reply += struct.pack("!II", 0, 0)            # root delay, dispersion
        reply += b"LOCL"
        reply += struct.pack("!II", ref_sec, ref_frac)   # reference
        reply += data[40:48]                              # originate = client's transmit
        reply += struct.pack("!II", ref_sec, ref_frac)   # receive
        reply += struct.pack("!II", *ntp_stamp(time.time() + offset))  # transmit
        try:
            s.sendto(reply, peer)
        except OSError as e:
            print(f"send to {peer}: {e}", flush=True)
            continue
        n += 1
        print(f"SNTP {n} {peer[0]} offset {offset:+.0f} s -> {now:.3f}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
