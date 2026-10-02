#!/usr/bin/env python3
"""Holds the bench_ap board's UART console open (one open = one reset; the
cp210x driver asserts DTR/RTS on every open and the board's auto-reset circuit
turns that into a POWERON reset, so a per-command open would drop the AP every
time the bench talks). Logs every console line with a timestamp to
/tmp/bench_ap.console.log and relays commands received on 127.0.0.1:5590:
the client sends one line "<wait_seconds> <command>", the daemon writes the
command to the UART and returns what the console printed within that time.
usage: bench_ap_daemon.py [--port /dev/ttyUSB0] [--listen 5590]
   nohup python3 ~/wican/tools/testbench/pi/bench_ap_daemon.py >/dev/null 2>&1 &"""
import argparse
import socket
import threading
import time

import serial

LOG = "/tmp/bench_ap.console.log"


class Console:
    def __init__(self, port):
        self.ser = serial.Serial(None, 115200, timeout=0.1, dsrdtr=False, rtscts=False)
        self.ser.port = port
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        self.lock = threading.Lock()
        self.buf = []            # (t, line) ring
        self.log = open(LOG, "a", encoding="utf-8")
        threading.Thread(target=self.reader, daemon=True).start()

    def reader(self):
        pending = b""
        while True:
            data = self.ser.read(4096)
            if not data:
                continue
            pending += data
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                text = line.decode("utf-8", "replace").rstrip("\r")
                t = time.time()
                with self.lock:
                    self.buf.append((t, text))
                    if len(self.buf) > 4000:
                        del self.buf[:1000]
                self.log.write(f"{time.strftime('%H:%M:%S', time.localtime(t))}.{int(t * 1000) % 1000:03d} {text}\n")
                self.log.flush()

    def command(self, cmd, wait):
        t0 = time.time()
        self.ser.write((cmd + "\r\n").encode())
        time.sleep(wait)
        with self.lock:
            return [txt for t, txt in self.buf if t >= t0 and txt.strip() and not txt.strip().startswith("bench_ap>")]


def serve(con, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(4)
    while True:
        c, _ = srv.accept()
        try:
            req = c.recv(1024).decode("utf-8", "replace").strip()
            wait_s, _, cmd = req.partition(" ")
            try:
                wait = min(60.0, max(0.2, float(wait_s)))
            except ValueError:
                wait, cmd = 1.5, req
            lines = con.command(cmd, wait)
            c.sendall(("\n".join(lines) + "\n").encode())
        except OSError:
            pass
        finally:
            c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--listen", type=int, default=5590)
    a = ap.parse_args()
    con = Console(a.port)
    serve(con, a.listen)


if __name__ == "__main__":
    main()
