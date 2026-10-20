#!/usr/bin/env python3
"""An ECU behind ISO 15765-2 extended addressing on the PCAN (11-bit, 500k).

The BMW F/G-series scheme of issue #964, for the autopid init-leak bench
(`tools/testbench/obd/autopid_init_leak_bench.py`). A tester reaches the
ECU on id 0x6F1 with the ECU's address as the FIRST data byte (the chip
does that with `ATSH6F1;ATCEA60`: `6F1 60 03 22 D1 07`), and the ECU
answers on 0x600 + its address with the tester's address 0xF1 first
(`660 F1 06 62 D1 07 01 E2 40`). The simulator's 7DF / 7E0 traffic is not
its business: it never answers those ids, it only counts them as a witness
of what the DUT sent.

  22 D1 07  -> 62 D1 07 <odometer, 3 bytes>   (123456 km by default)
  01 xx     -> 7F 01 11                       (service 01 not supported:
                                               the negative response the
                                               report decoded as RPM 68.25,
                                               speed 1, coolant -39)
  else      -> 7F <svc> 11

usage: pcan_ext_addr_ecu.py [secs] [--addr 0x60] [--tester 0x6F1]
                            [--odometer 123456] [--pcan PCAN_USBBUS2]
                            [--summary FILE]
Prints one line per frame it answers (`REQ` / `RSP`) and per functional
or physical OBD request it witnesses (`BUS`). `--summary FILE` rewrites a
JSON summary every second (atomic rename) so a bench that kills this
actor still reads the counts:
  {"svc01_at_ecu": N,   service-01 requests that reached the ECU (the leak)
   "did_at_ecu": N,     22 D1 07 requests answered
   "other_at_ecu": N,   anything else addressed to the ECU
   "func_7df": N,       functional OBD requests seen on 0x7DF
   "phys_7ex": N,       physical requests seen on 0x7E0..0x7E7
   "svc01_7df": N,      service-01 requests on 0x7DF
   "last": [...]}       the last 20 REQ/BUS lines
"""
import argparse
import json
import os
import time

import can

REQ_FUNC = 0x7DF


class ExtEcu:
    def __init__(self, bus, addr, tester, odometer):
        self.bus = bus
        self.addr = addr                 # 0x60: the ECU's extended address
        self.tester = tester             # 0x6F1: the id the tester sends on
        self.resp = 0x600 + addr         # 0x660: the id the ECU answers on
        self.odometer = odometer
        self.svc01 = 0
        self.did = 0
        self.other = 0
        self.func_7df = 0
        self.phys_7ex = 0
        self.svc01_7df = 0
        self.last = []

    def note(self, line):
        print(line, flush=True)
        self.last.append(line)
        del self.last[:-20]

    def send(self, payload):
        # extended addressing: the tester's address first, then the PCI
        data = bytes([0xF1, len(payload)]) + payload
        data = data + b"\x00" * (8 - len(data))
        self.bus.send(can.Message(arbitration_id=self.resp,
                                  is_extended_id=False, data=data))
        self.note("RSP %03X %s" % (self.resp, data.hex(" ").upper()))

    def handle(self, p):
        svc = p[0]
        if svc == 0x22 and p[1:3] == b"\xD1\x07":
            self.did += 1
            km = self.odometer & 0xFFFFFF
            self.send(bytes([0x62, 0xD1, 0x07, km >> 16, (km >> 8) & 0xFF,
                             km & 0xFF]))
        elif svc == 0x01:
            self.svc01 += 1
            self.send(bytes([0x7F, 0x01, 0x11]))
        else:
            self.other += 1
            self.send(bytes([0x7F, svc, 0x11]))

    def summary(self):
        return {"svc01_at_ecu": self.svc01, "did_at_ecu": self.did,
                "other_at_ecu": self.other, "func_7df": self.func_7df,
                "phys_7ex": self.phys_7ex, "svc01_7df": self.svc01_7df,
                "last": list(self.last)}


def write_summary(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("secs", nargs="?", type=float, default=120)
    ap.add_argument("--addr", default="0x60")
    ap.add_argument("--tester", default="0x6F1")
    ap.add_argument("--odometer", type=int, default=123456)
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--summary", default="")
    a = ap.parse_args()

    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    ecu = ExtEcu(bus, int(a.addr, 0), int(a.tester, 0), a.odometer)
    print("EXT ECU on %s: tester 0x%03X + address 0x%02X -> answers on "
          "0x%03X, odometer %d km, %.0f s" % (a.pcan, ecu.tester, ecu.addr,
                                              ecu.resp, ecu.odometer,
                                              a.secs), flush=True)
    t0 = time.time()
    end = t0 + a.secs
    next_summary = t0 + 1.0
    try:
        while time.time() < end:
            m = bus.recv(timeout=0.2)
            now = time.time()
            if a.summary and now >= next_summary:
                write_summary(a.summary, ecu.summary())
                next_summary = now + 1.0
            if m is None:
                continue
            d = bytes(m.data)
            aid = m.arbitration_id
            if aid == REQ_FUNC or 0x7E0 <= aid <= 0x7E7:
                if aid == REQ_FUNC:
                    ecu.func_7df += 1
                else:
                    ecu.phys_7ex += 1
                n = d[0] & 0x0F if len(d) > 1 else 0
                if aid == REQ_FUNC and n >= 1 and d[1] == 0x01:
                    ecu.svc01_7df += 1
                ecu.note("BUS %7.3f %03X %s" % (now - t0, aid,
                                                d.hex(" ").upper()))
                continue
            if aid != ecu.tester or len(d) < 3 or d[0] != ecu.addr:
                continue
            pci = d[1] & 0xF0
            if pci == 0x00:
                n = d[1] & 0x0F
                if n:
                    ecu.note("REQ %7.3f %03X %s" % (now - t0, aid,
                                                    d.hex(" ").upper()))
                    ecu.handle(d[2:2 + n])
            elif pci == 0x10:
                # a multi-frame request: nothing here needs one; flow
                # control it and let the rest fall on the floor
                ecu.note("REQ %7.3f %03X %s (first frame, not served)"
                         % (now - t0, aid, d.hex(" ").upper()))
                bus.send(can.Message(arbitration_id=ecu.resp,
                                     is_extended_id=False,
                                     data=bytes([0xF1, 0x30, 0, 0, 0, 0, 0,
                                                 0])))
    finally:
        if a.summary:
            write_summary(a.summary, ecu.summary())
        bus.shutdown()
    print("SUMMARY " + json.dumps(ecu.summary()), flush=True)


if __name__ == "__main__":
    main()
