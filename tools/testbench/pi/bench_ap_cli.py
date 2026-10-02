#!/usr/bin/env python3
"""Send one command to the bench_ap board and print the reply (runs on rpi001;
the PC calls it over ssh). Goes through bench_ap_daemon.py (127.0.0.1:5590),
which holds the UART open: a direct open resets the board (cp210x asserts
DTR/RTS on open, the board's auto-reset circuit fires). Falls back to a
direct open only with --direct, for the first bring-up.
usage: bench_ap_cli.py "ap" | "ap off" | "ap on" | "ap kick aa:bb:cc:dd:ee:ff" [--wait 1.5] [--direct]"""
import argparse
import socket
import sys
import time


def via_daemon(cmd, wait, port):
    s = socket.create_connection(("127.0.0.1", port), timeout=wait + 5)
    s.sendall(f"{wait} {cmd}".encode())
    out = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        out += chunk
    s.close()
    return out.decode("utf-8", "replace")


def direct(cmd, wait, port):
    import serial
    s = serial.Serial(None, 115200, timeout=0.2, dsrdtr=False, rtscts=False)
    s.port = port
    s.dtr = False
    s.rts = False
    s.open()
    with s:
        time.sleep(0.3)
        s.reset_input_buffer()
        s.write((cmd + "\r\n").encode())
        t0 = time.time()
        out = b""
        while time.time() - t0 < wait:
            out += s.read(4096)
    text = out.decode("utf-8", "replace")
    return "\n".join(l.rstrip() for l in text.splitlines() if l.strip() and not l.strip().startswith("bench_ap>"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--wait", type=float, default=1.5)
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--daemon-port", type=int, default=5590)
    ap.add_argument("--direct", action="store_true", help="open the UART directly (resets the board)")
    a = ap.parse_args()
    if a.direct:
        print(direct(a.cmd, a.wait, a.port))
        return 0
    try:
        print(via_daemon(a.cmd, a.wait, a.daemon_port), end="")
    except OSError as e:
        print(f"bench_ap daemon not reachable ({e}); start it: nohup python3 ~/wican/tools/testbench/pi/bench_ap_daemon.py >/dev/null 2>&1 &", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
