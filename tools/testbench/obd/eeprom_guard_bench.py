#!/usr/bin/env python3
"""OBD chip EEPROM-guard bench  ->  EEPROM GUARD PASS / FAIL

Talks to the MIC3624 exactly like an ELM app does (the TCP ELM bridge,
port 35000 by default) and proves the driver's guard (obd_chip_guard.h):

  ATI              the bridge path is alive (chip identifies itself)
  ATSP6            rewritten to ATTP6 on the wire: the chip answers OK and
                   GET /api/obd_chip eeprom_guard.rewrites steps by one
  ATPP 0C SV 23    the UART re-baud that bricks the link: refused, the app
                   sees the ELM "?", eeprom_guard.blocked steps by one, the
                   chip never saw it
  STWBR            refused the same way
  ATPPS            the programmable-parameter SUMMARY (a read) passes
  ATI              the chip still answers at its UART baud afterwards

usage: eeprom_guard_bench.py <dut_ip> [--port 35000] [--http http://<dut>]
  run ON rpi001 (the ELM port is reachable from the hotspot side); the HTTP
  base defaults to http://<dut_ip>.
Needs: main firmware on the DUT with the TCP ELM bridge enabled (the bench
DUT's canonical settings). Autopid yields the chip to the "app" for 10 s
after each write, which is what a real app gets too.
"""
import json
import socket
import sys
import time
import urllib.request


def http_json(url):
    with urllib.request.urlopen(url, timeout=6) as r:
        return json.load(r)


class Elm:
    def __init__(self, ip, port):
        self.s = socket.create_connection((ip, port), timeout=3)
        self.s.settimeout(0.3)
        self.drain()

    def drain(self):
        try:
            while self.s.recv(4096):
                pass
        except (socket.timeout, OSError):
            pass

    def cmd(self, text, wait_s=1.5):
        self.drain()
        self.s.sendall((text + "\r").encode())
        buf = b""
        t0 = time.time()
        while time.time() - t0 < wait_s:
            try:
                chunk = self.s.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk
            if b">" in chunk:
                break
        return buf.decode(errors="replace").replace("\r", " ").replace("\n", " ").strip()


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(__doc__)
        return 2
    ip = args[0]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    port = int(opt("--port", 35000))
    base = opt("--http", "http://" + ip)
    guard = lambda: http_json(base + "/api/obd_chip").get("eeprom_guard", {})
    fails = []

    def check(name, ok, detail=""):
        print(("PASS" if ok else "FAIL") + ": " + name + ((" (" + detail + ")") if detail else ""))
        if not ok:
            fails.append(name)

    g0 = guard()
    print("guard counters before:", g0)
    try:
        e = Elm(ip, port)
    except OSError as err:
        print("EEPROM GUARD FAIL: cannot connect %s:%d (%s)" % (ip, port, err))
        return 1

    r = e.cmd("ATI")
    check("bridge path alive: ATI identifies the chip", "ELM327" in r or "MIC" in r, r)

    r = e.cmd("ATSP6")
    g1 = guard()
    check("ATSP6 rewritten to ATTP6 (chip OK, rewrites +1)",
          "OK" in r and g1.get("rewrites") == g0.get("rewrites", 0) + 1,
          "reply %r, rewrites %s -> %s" % (r, g0.get("rewrites"), g1.get("rewrites")))

    r = e.cmd("ATPP 0C SV 23")
    g2 = guard()
    check("ATPP 0C SV 23 refused with the ELM ? (blocked +1)",
          "?" in r and g2.get("blocked") == g1.get("blocked", 0) + 1,
          "reply %r, blocked %s -> %s" % (r, g1.get("blocked"), g2.get("blocked")))

    r = e.cmd("STWBR")
    g3 = guard()
    check("STWBR refused (blocked +1)", "?" in r and g3.get("blocked") == g2.get("blocked", 0) + 1, "reply %r" % r)

    r = e.cmd("ATPPS", wait_s=2.5)
    g4 = guard()
    check("ATPPS (summary read) passes untouched",
          g4 == g3 and len(r) > 3 and "?" not in r.split(">")[0].strip()[:1], "reply %r" % r[:80])

    r = e.cmd("ATI")
    check("chip still answers after the refused re-baud", "ELM327" in r or "MIC" in r, r)
    e.s.close()

    for f in fails:
        print("FAIL:", f)
    print("EEPROM GUARD", "PASS" if not fails else "FAIL")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
