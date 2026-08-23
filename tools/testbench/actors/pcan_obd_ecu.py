#!/usr/bin/env python3
"""A stateful OBD-II ECU on the PCAN (11-bit, 500k) for the DTC bench.

Answers functional 0x7DF + physical (resp - 8), replies on **0x7E9** by
default (the "second ECU" slot) so it never collides with the hardware
bench ECU box at 0x7E8 — the DUT targets it with `dtc_rxheader: "7E9"`.
Full ISO-TP (SF + FF/CF with FC), scaffolding from pcan_uds_ecu.py.

--resp2 adds a SECOND independent responder (own id + own DTC lists) for
the multi-ECU functional-scan leg — both answer every 0x7DF request.
Keep each responder's stored list <= 2 codes in that mode so responses
stay single-frame (two ECUs streaming FF/CF would fight over the FC).

  01 01 -> 41 01 <MIL|count> ...      (reflects the live DTC list)
  03    -> 43 <count> <pairs>         (stored codes; multi-frame when >2)
  07    -> 47 <count> <pairs>         (pending codes)
  0A    -> 4A <count> <pairs>         (permanent codes)
  04    -> 44                         (clears stored+pending, MIL off;
                                       permanent codes SURVIVE, like a
                                       real ECU)
  else  -> silence (a real emissions ECU ignores unknown modes)

  python pcan_obd_ecu.py [secs] [--stored P0420,P0171] [--pending P0301]
                         [--permanent P0420] [--resp 0x7E9]
                         [--resp2 0x7EA] [--stored2 U0100]
                         [--pending2 ...] [--permanent2 ...]
                         [--pcan PCAN_USBBUS2]
Defaults: 120 s, stored P0420+P0171, pending P0301, permanent empty.
"""
import argparse
import time

import can

REQ_FUNC = 0x7DF


def code_to_bytes(code):
    letter = "PCBU".index(code[0].upper())
    hi = (letter << 6) | ((int(code[1]) & 0x3) << 4) | int(code[2], 16)
    lo = (int(code[3], 16) << 4) | int(code[4], 16)
    return bytes([hi, lo])


def parse_codes(csv):
    return [c.strip().upper() for c in csv.split(",") if c.strip()]


class Ecu:
    def __init__(self, bus, resp, stored, pending, permanent):
        self.bus = bus
        self.resp = resp
        self.phys = resp - 8          # 7E9 <- 7E1, 7EA <- 7E2, ...
        self.stored = stored
        self.pending = pending
        self.permanent = permanent
        self.cleared = 0

    def send_frame(self, data):
        self.bus.send(can.Message(arbitration_id=self.resp,
                                  is_extended_id=False,
                                  data=data + b"\x00" * (8 - len(data))))

    def send_isotp(self, payload):
        n = len(payload)
        if n <= 7:
            self.send_frame(bytes([n]) + payload)
            return
        ff = bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF]) + payload[:6]
        self.send_frame(ff)
        rest = payload[6:]
        t0 = time.time()
        while time.time() - t0 < 1.0:
            m = self.bus.recv(timeout=0.5)
            if m and m.arbitration_id in (REQ_FUNC, self.phys) and \
                    (m.data[0] & 0xF0) == 0x30:
                break
        sn = 1
        while rest:
            self.send_frame(bytes([0x20 | (sn & 0x0F)]) + rest[:7])
            rest = rest[7:]
            sn = (sn + 1) & 0x0F
            time.sleep(0.003)

    def dtc_payload(self, svc, codes):
        body = b"".join(code_to_bytes(c) for c in codes)
        return bytes([svc, len(codes)]) + body

    def handle(self, p):
        if p[:2] == b"\x01\x01":
            a = (0x80 if self.stored else 0x00) | (len(self.stored) & 0x7F)
            self.send_isotp(bytes([0x41, 0x01, a, 0x07, 0x65, 0x04]))
        elif p[:1] == b"\x03":
            self.send_isotp(self.dtc_payload(0x43, self.stored))
        elif p[:1] == b"\x07":
            self.send_isotp(self.dtc_payload(0x47, self.pending))
        elif p[:1] == b"\x0A":
            self.send_isotp(self.dtc_payload(0x4A, self.permanent))
        elif p[:1] == b"\x04":
            self.stored = []
            self.pending = []
            self.cleared += 1
            print(f"  [ECU 0x{self.resp:03X}] mode 04: cleared "
                  "(permanent codes survive)")
            self.send_isotp(bytes([0x44]))
        # unknown modes: silence, like a real emissions ECU


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("secs", nargs="?", type=float, default=120)
    ap.add_argument("--stored", default="P0420,P0171")
    ap.add_argument("--pending", default="P0301")
    ap.add_argument("--permanent", default="")
    ap.add_argument("--resp", default="0x7E9")
    ap.add_argument("--resp2", default="")
    ap.add_argument("--stored2", default="")
    ap.add_argument("--pending2", default="")
    ap.add_argument("--permanent2", default="")
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    a = ap.parse_args()

    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    ecus = [Ecu(bus, int(a.resp, 0), parse_codes(a.stored),
                parse_codes(a.pending), parse_codes(a.permanent))]
    if a.resp2:
        ecus.append(Ecu(bus, int(a.resp2, 0), parse_codes(a.stored2),
                        parse_codes(a.pending2),
                        parse_codes(a.permanent2)))
    for e in ecus:
        print(f"OBD ECU on {a.pcan}: req 0x{REQ_FUNC:03X}/0x{e.phys:03X}"
              f" -> resp 0x{e.resp:03X}")
        print(f"  stored={e.stored} pending={e.pending} "
              f"permanent={e.permanent}")

    phys_ids = {e.phys: e for e in ecus}
    ff_buf = b""
    ff_left = 0
    end = time.time() + a.secs
    try:
        while time.time() < end:
            m = bus.recv(timeout=0.5)
            if m is None:
                continue
            if m.arbitration_id == REQ_FUNC:
                targets = ecus
            elif m.arbitration_id in phys_ids:
                targets = [phys_ids[m.arbitration_id]]
            else:
                continue
            d = bytes(m.data)
            pci = d[0] & 0xF0
            if pci == 0x00:
                n = d[0] & 0x0F
                if n:
                    for e in targets:
                        e.handle(d[1:1 + n])
            elif pci == 0x10:
                ff_left = ((d[0] & 0x0F) << 8) | d[1]
                ff_buf = d[2:8]
                ff_left -= len(ff_buf)
                targets[0].send_frame(bytes([0x30, 0x00, 0x00]))
            elif pci == 0x20:
                take = min(7, ff_left)
                ff_buf += d[1:1 + take]
                ff_left -= take
                if ff_left <= 0:
                    for e in targets:
                        e.handle(ff_buf)
    finally:
        bus.shutdown()
    print(f"ECU stopped ({sum(e.cleared for e in ecus)} clears served)")


if __name__ == "__main__":
    main()
