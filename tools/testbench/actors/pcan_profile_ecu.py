#!/usr/bin/env python3
"""The ECU(s) of a vehicle profile, played on the PCAN from a script file.

`tools/testbench/obd/autopid_profile_e2e_bench.py` writes the script from a
published vehicle profile: one entry per request id the profile talks to
(the `ATSH` of its PID inits), the reply id (`ATCRA`), and the full reply
payload for every request the profile sends; plus the broadcast frames the
profile's passive filters capture. This actor answers the requests with
ISO 15765-2 (single frame, or first frame + flow control wait + consecutive
frames, honouring the block size and the separation time the tester asks
for) and sends the broadcasts at their period. Everything else on the bus
(the simulator's 7DF / 7E0..7E7 traffic) is counted as a witness and left
alone.

script.json:
  {"ecus": [{"req_id": "0x79B", "resp_id": "0x7BB", "fc_from": "0x79B",
             "responses": {"2101": "6101<hex payload, PCI-free>", ...}}],
   "broadcasts": [{"id": "0x284", "data": "<hex, up to 8 bytes>",
                   "gap_ms": 50}],
   "cf_gap_ms": 1, "unknown_nrc": "11"}

usage: pcan_profile_ecu.py SCRIPT.json [secs] [--pcan PCAN_USBBUS2]
                           [--summary FILE]
Prints one line per request (`REQ`), reply (`RSP`, first frame `FF`,
flow control seen `FC`) and per 1000 broadcast frames. `--summary FILE`
rewrites a JSON summary every second (atomic rename) so a bench that
kills this actor still reads the counts:
  {"requests": {"2101": N, ...},   requests seen per key (hex, no hint)
   "answered": {"2101": N, ...},   replies sent per key
   "unknown": N,                   requests with no reply in the script
   "fc_ok": N, "fc_timeout": N,    multi-frame replies: flow control
                                   received / waited for in vain
   "broadcast_sent": {"0x284": N}, frames sent per broadcast id
   "witness": {"func_7df": N, "phys_7ex": N, "other": N},
   "last": [...]}                  the last 30 REQ/RSP/FC lines
"""
import argparse
import json
import os
import time

import can

FC_WAIT_S = 1.0


def hexint(v):
    return int(str(v), 0)


class Ecu:
    def __init__(self, spec):
        self.req_id = hexint(spec["req_id"])
        self.resp_id = hexint(spec["resp_id"])
        self.fc_from = hexint(spec.get("fc_from", spec["req_id"]))
        self.responses = {k.upper(): bytes.fromhex(v)
                          for k, v in spec.get("responses", {}).items()}


class Broadcast:
    def __init__(self, spec):
        self.id = hexint(spec["id"])
        self.data = bytes.fromhex(spec["data"])
        self.gap = float(spec.get("gap_ms", 50)) / 1000.0
        self.due = 0.0
        self.sent = 0


class ProfileEcu:
    def __init__(self, bus, script):
        self.bus = bus
        self.ecus = [Ecu(e) for e in script.get("ecus", [])]
        self.by_req = {e.req_id: e for e in self.ecus}
        self.broadcasts = [Broadcast(b) for b in script.get("broadcasts", [])]
        self.cf_gap = float(script.get("cf_gap_ms", 1)) / 1000.0
        self.unknown_nrc = int(script.get("unknown_nrc", "11"), 16)
        self.requests = {}
        self.answered = {}
        self.unknown = 0
        self.fc_ok = 0
        self.fc_timeout = 0
        self.witness = {"func_7df": 0, "phys_7ex": 0, "other": 0}
        self.last = []
        self.t0 = time.time()

    def note(self, line):
        print(line, flush=True)
        self.last.append(line)
        del self.last[:-30]

    def send_raw(self, arb_id, data):
        data = bytes(data) + b"\x00" * (8 - len(data))
        self.bus.send(can.Message(arbitration_id=arb_id,
                                  is_extended_id=arb_id > 0x7FF,
                                  data=data[:8]), timeout=0.2)

    def wait_fc(self, ecu):
        """Block until the tester's flow control (PCI 0x3X) arrives on
        ecu.fc_from. Returns (flow_status, block_size, st_min_s) or None."""
        end = time.time() + FC_WAIT_S
        while time.time() < end:
            m = self.bus.recv(timeout=0.05)
            if m is None:
                continue
            d = bytes(m.data)
            if m.arbitration_id == ecu.fc_from and len(d) >= 1 and \
                    (d[0] & 0xF0) == 0x30:
                fs = d[0] & 0x0F
                bs = d[1] if len(d) > 1 else 0
                st = d[2] if len(d) > 2 else 0
                st_s = (st / 1000.0) if st <= 0x7F else \
                       ((st - 0xF0) / 10000.0 if 0xF1 <= st <= 0xF9 else 0)
                self.note("FC  %7.3f %03X %s (fs %d bs %d stmin %s)"
                          % (time.time() - self.t0, m.arbitration_id,
                             d.hex(" ").upper(), fs, bs, st))
                return fs, bs, st_s
            self.witness_frame(m)
        return None

    def send_isotp(self, ecu, payload):
        n = len(payload)
        if n <= 7:
            self.send_raw(ecu.resp_id, bytes([n]) + payload)
            self.note("RSP %7.3f %03X %s" % (time.time() - self.t0,
                                             ecu.resp_id,
                                             (bytes([n]) + payload).hex(" ").upper()))
            return True
        ff = bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF]) + payload[:6]
        self.send_raw(ecu.resp_id, ff)
        self.note("FF  %7.3f %03X %s (%d bytes, %d frames)"
                  % (time.time() - self.t0, ecu.resp_id, ff.hex(" ").upper(),
                     n, 1 + (n - 6 + 6) // 7))
        rest = payload[6:]
        sn = 1
        while rest:
            fc = self.wait_fc(ecu)
            if fc is None:
                self.fc_timeout += 1
                self.note("FC  timeout: %d bytes of the reply not sent"
                          % len(rest))
                return False
            self.fc_ok += 1
            fs, bs, st_s = fc
            if fs == 2:                 # overflow / abort
                self.note("FC  abort from the tester")
                return False
            if fs == 1:                 # wait
                continue
            gap = max(self.cf_gap, st_s)
            sent = 0
            while rest and (bs == 0 or sent < bs):
                cf = bytes([0x20 | (sn & 0x0F)]) + rest[:7]
                self.send_raw(ecu.resp_id, cf)
                rest = rest[7:]
                sn = (sn + 1) & 0x0F
                sent += 1
                if rest:
                    time.sleep(gap)
        self.note("RSP %7.3f %03X done (%d bytes)" % (time.time() - self.t0,
                                                      ecu.resp_id, n))
        return True

    def handle_request(self, ecu, req):
        key = req.hex().upper()
        self.requests[key] = self.requests.get(key, 0) + 1
        self.note("REQ %7.3f %03X %s" % (time.time() - self.t0, ecu.req_id,
                                         key))
        payload = ecu.responses.get(key)
        if payload is None:
            self.unknown += 1
            self.send_isotp(ecu, bytes([0x7F, req[0], self.unknown_nrc]))
            return
        if self.send_isotp(ecu, payload):
            self.answered[key] = self.answered.get(key, 0) + 1

    def witness_frame(self, m):
        aid = m.arbitration_id
        if aid == 0x7DF:
            self.witness["func_7df"] += 1
        elif 0x7E0 <= aid <= 0x7E7:
            self.witness["phys_7ex"] += 1
        elif aid not in self.by_req and \
                aid not in (e.resp_id for e in self.ecus):
            self.witness["other"] += 1

    def on_frame(self, m):
        ecu = self.by_req.get(m.arbitration_id)
        if ecu is None:
            self.witness_frame(m)
            return
        d = bytes(m.data)
        if len(d) < 2:
            return
        pci = d[0] & 0xF0
        if pci == 0x00:
            n = d[0] & 0x0F
            if 1 <= n <= 7 and len(d) >= 1 + n:
                self.handle_request(ecu, d[1:1 + n])
        elif pci == 0x10:
            # a multi-frame request: nothing in a profile needs one; flow
            # control it and let the rest fall on the floor
            self.note("REQ %7.3f %03X %s (first frame, not served)"
                      % (time.time() - self.t0, m.arbitration_id,
                         d.hex(" ").upper()))
            self.send_raw(ecu.resp_id, bytes([0x30, 0, 0]))

    def pump_broadcasts(self):
        now = time.time()
        for b in self.broadcasts:
            if now >= b.due:
                try:
                    self.send_raw(b.id, b.data)
                    b.sent += 1
                    if b.sent % 1000 == 0:
                        self.note("BC  %7.3f %03X sent %d" % (now - self.t0,
                                                             b.id, b.sent))
                except can.CanError as e:
                    self.note("BC  %03X send failed: %s" % (b.id, e))
                b.due = now + b.gap

    def summary(self):
        return {"requests": dict(self.requests), "answered": dict(self.answered),
                "unknown": self.unknown, "fc_ok": self.fc_ok,
                "fc_timeout": self.fc_timeout,
                "broadcast_sent": {"0x%03X" % b.id: b.sent
                                   for b in self.broadcasts},
                "witness": dict(self.witness), "last": list(self.last)}


def write_summary(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("secs", nargs="?", type=float, default=600)
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--summary", default="")
    a = ap.parse_args()

    with open(a.script, encoding="utf-8") as f:
        script = json.load(f)
    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    ecu = ProfileEcu(bus, script)
    print("PROFILE ECU on %s: %s; broadcasts %s; %.0f s" % (
        a.pcan,
        ", ".join("0x%03X -> 0x%03X (%d replies)" % (e.req_id, e.resp_id,
                                                      len(e.responses))
                  for e in ecu.ecus),
        ", ".join("0x%03X every %.0f ms" % (b.id, b.gap * 1000)
                  for b in ecu.broadcasts) or "none",
        a.secs), flush=True)
    end = ecu.t0 + a.secs
    next_summary = ecu.t0 + 1.0
    try:
        while time.time() < end:
            ecu.pump_broadcasts()
            m = bus.recv(timeout=0.005)
            now = time.time()
            if a.summary and now >= next_summary:
                write_summary(a.summary, ecu.summary())
                next_summary = now + 1.0
            if m is not None:
                ecu.on_frame(m)
    finally:
        if a.summary:
            write_summary(a.summary, ecu.summary())
        bus.shutdown()
    print("SUMMARY " + json.dumps(ecu.summary()), flush=True)


if __name__ == "__main__":
    main()
