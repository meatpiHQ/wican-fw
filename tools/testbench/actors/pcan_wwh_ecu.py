#!/usr/bin/env python3
"""A WWH-OBD (ISO 27145 / SAE J1979-2 OBDonUDS) vehicle on the PCAN: the
independent reference peer of the WWH benches (TASK_j1939_wwh.md). Real
ISO-TP (python-can + can-isotp), one or two ECUs, 11-bit or 29-bit ids.

Addressing
  11-bit: functional 7DF, ECU n: request 7E0+n, response 7E8+n
  29-bit: functional 18DB33F1, ECU address a: request 18DAaaF1,
          response 18DAF1aa (--ecu 0x00 engine, --ecu2 0x3D aftertreatment)

Services (ISO 27145-3)
  22 F810            -> 62 F8 10 <protocol id>           (--no-f810: refused)
  22 F400/F420/...   -> 62 F4 xx <4-byte support bitmap>
  22 F4xx            -> 62 F4 xx <data, the J1979 PID encoding>; up to 6 DIDs
  22 F800, F802, F804, F80A -> info types (F802 = VIN, multi-frame)
  19 42 33 <status mask> <severity mask>
                     -> 59 42 33 FF FF <format> (sev dtc dtc dtc status)*
  19 55 33           -> 59 55 33 FF <format> (dtc dtc dtc status)*
  14 FF FF 33        -> 54 (confirmed + pending cleared, permanent kept)
  10 xx, 3E 00       -> positive
  anything else      -> NRC on a physical request, SILENCE on a functional
                        one (ISO 14229: no 11/12/31 on functional requests)
  J1979 classic (01, 09, 03 ...) -> not supported, unless --also-classic
  --pending N        -> N x `7F sid 78` before every 19 and 22 F802 answer

Personality --sprinter: the Mercedes Sprinter VS30 of the field report
(TASK_j1939_wwh.md): 29-bit only, no F810, ECUs 58 (engine), 59 and 5D
(SCR) with the report's support bitmaps and answers (58: 22F40C ->
0B ED = 763 rpm; 5D alone has F485, a 13-byte answer). --late-5a SECS adds
ECU 5A after that many seconds (it turns up when the engine starts) with
its own UNSCALED F40C (02 FB): a reader that takes it shows 190 rpm.

  --traffic HZ       -> a broadcast frame (18FF1021) HZ times a second:
                        the bus is live for a listener; the bus errors the
                        adapter sees are counted (`bus_errors` in ECU DONE,
                        and once a second in an `ECU STAT {json}` line)
  --truck            -> an EU truck (TASK_j1939_wwh.md phase 7): the J1939
                        truck of pcan_j1939_truck.py broadcasts on the same
                        bus (its PGN set, DM1, address claim, requests
                        answered) with the SAME VIN; --truck-sa, --truck-dtcs
                        (spn-fmi-oc,...), --truck-prev, --truck-set as the
                        truck's --sa / --dtcs / --prev / --set; its events
                        print as `TRUCK {json}` lines and its statistics as
                        `truck` in ECU DONE. One process: PCAN gives a
                        channel to one client
  --stop-file PATH   -> stop cleanly (ECU DONE printed) once PATH exists

  python pcan_wwh_ecu.py [secs] [--pcan PCAN_USBBUS2] [--bitrate 500]
        [--id-format 11|29] [--ecu 0x00] [--ecu2 0x3D]
        [--dtcs P0420:08:02,P2463-1F:04:04] [--dtcs2 ...]
        [--permanent P0420] [--dtc-format 04|02] [--vin ...]
        [--set rpm=1250,...] [--pending N] [--no-f810] [--also-classic]
        [--sprinter] [--late-5a SECS] [--traffic HZ] [--stop-file PATH]
        [--selftest]
  DTC syntax: <code>[:<status hex>[:<severity hex>]]; code = P0420 or
  P0420-1F (format 04) or <spn>-<fmi> (format 02).

Prints `ECU READY ...`, one `ECU {json}` line per request and
`ECU DONE {json}`. Run on the PC with the v6.0.2 IDF venv python.
"""
import argparse
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FUNC_11, FUNC_29 = 0x7DF, 0x18DB33F1
FGID_EMISSIONS = 0x33
STATUS_PENDING, STATUS_CONFIRMED = 0x04, 0x08

DEFAULTS = {
    "rpm": 1250.0, "speed": 64.0, "coolant": 88.0, "load": 40.0,
    "throttle": 22.0, "intake": 35.0, "maf": 18.5, "runtime": 1234.0,
    "fuel": 55.0, "baro": 100.0, "volt": 27.8, "ambient": 19.0,
    "oil": 95.0, "fuel_rate": 12.5, "odo": 123456.7, "cat_temp": 380.0,
}

ENGINE_PIDS = (0x01, 0x04, 0x05, 0x0C, 0x0D, 0x0F, 0x10, 0x11, 0x1F, 0x2F,
               0x33, 0x42, 0x46, 0x5C, 0x5E, 0xA6)
AFTERTREATMENT_PIDS = (0x01, 0x3C, 0x42)

# The Sprinter VS30 (the field report): the PID sets give the reported
# 22F400 bitmaps (58: 98 18 A0 13; 59 and 5D: 98 18 00 01), the rest of each
# set is taken from the DIDs the owner saw answered.
SPRINTER_VIN = "W1V907633NP000001"
SPRINTER_RPM = 763.25               # 22F40C -> 0B ED on ECU 58
SPRINTER = (
    (0x58, (0x01, 0x04, 0x05, 0x0C, 0x0D, 0x11, 0x13, 0x1C, 0x1F, 0x2F, 0x31,
            0x33, 0x42, 0x46, 0x5C, 0x5E), {}),
    (0x59, (0x01, 0x04, 0x05, 0x0C, 0x0D, 0x42), {}),
    (0x5D, (0x01, 0x04, 0x05, 0x0C, 0x0D, 0x7A, 0x85, 0x91, 0x94),
     {0x85: bytes.fromhex("07001B001BB400000000")}),
)
# the unit that answers once the engine runs: engine speed WITHOUT the /4
SPRINTER_LATE = (0x5A, (0x0C,), {0x0C: bytes.fromhex("02FB")})


def _clamp(v, hi):
    return max(0, min(hi, int(round(v))))


def pid_data(pid, v, mil, n_dtc):
    """J1979 PID encodings (the data bytes after the PID)."""
    if pid == 0x01:
        return bytes([(0x80 if mil else 0) | (n_dtc & 0x7F), 0x07, 0x65, 0x04])
    if pid == 0x04:
        return bytes([_clamp(v["load"] * 255 / 100, 255)])
    if pid == 0x05:
        return bytes([_clamp(v["coolant"] + 40, 255)])
    if pid == 0x0C:
        return _clamp(v["rpm"] * 4, 0xFFFF).to_bytes(2, "big")
    if pid == 0x0D:
        return bytes([_clamp(v["speed"], 255)])
    if pid == 0x0F:
        return bytes([_clamp(v["intake"] + 40, 255)])
    if pid == 0x10:
        return _clamp(v["maf"] * 100, 0xFFFF).to_bytes(2, "big")
    if pid == 0x11:
        return bytes([_clamp(v["throttle"] * 255 / 100, 255)])
    if pid == 0x1F:
        return _clamp(v["runtime"], 0xFFFF).to_bytes(2, "big")
    if pid == 0x2F:
        return bytes([_clamp(v["fuel"] * 255 / 100, 255)])
    if pid == 0x33:
        return bytes([_clamp(v["baro"], 255)])
    if pid == 0x3C:
        return _clamp((v["cat_temp"] + 40) * 10, 0xFFFF).to_bytes(2, "big")
    if pid == 0x42:
        return _clamp(v["volt"] * 1000, 0xFFFF).to_bytes(2, "big")
    if pid == 0x46:
        return bytes([_clamp(v["ambient"] + 40, 255)])
    if pid == 0x5C:
        return bytes([_clamp(v["oil"] + 40, 255)])
    if pid == 0x5E:
        return _clamp(v["fuel_rate"] * 20, 0xFFFF).to_bytes(2, "big")
    if pid == 0xA6:
        return _clamp(v["odo"] * 10, 0xFFFFFFFF).to_bytes(4, "big")
    if pid == 0x13:
        return bytes([0x03])                        # O2 sensors present
    if pid == 0x1C:
        return bytes([0x06])                        # EOBD
    if pid == 0x31:
        return _clamp(v["odo"] % 65535, 0xFFFF).to_bytes(2, "big")
    if pid == 0x7A:
        return bytes.fromhex("01006400000000")      # DPF bank 1, 7 bytes
    if pid == 0x91:
        return bytes(5)
    if pid == 0x94:
        return bytes(12)
    return None


def support_bitmap(ids, base):
    """The 4-byte support bitmap of range @base (ids base+1 .. base+0x20);
    the last bit says a later range has something."""
    bm = 0
    for i in ids:
        if base < i <= base + 0x20:
            bm |= 1 << (32 - (i - base))
    if any(i > base + 0x20 for i in ids):
        bm |= 1
    return bm.to_bytes(4, "big")


def j2012_bytes(code):
    """'P0420' or 'P0420-1F' -> 3 bytes (2-byte code + failure type)."""
    ftb = 0
    if "-" in code:
        code, f = code.split("-")
        ftb = int(f, 16)
    letter = "PCBU".index(code[0].upper())
    hi = (letter << 6) | ((int(code[1]) & 0x3) << 4) | int(code[2], 16)
    lo = (int(code[3], 16) << 4) | int(code[4], 16)
    return bytes([hi, lo, ftb])


def j1939_bytes(code):
    """'3226-4' -> SPN low, SPN mid, SPN high 3 bits + FMI (J1939-73)."""
    spn, fmi = (int(x) for x in code.split("-"))
    return bytes([spn & 0xFF, (spn >> 8) & 0xFF,
                  (((spn >> 16) & 7) << 5) | (fmi & 0x1F)])


def parse_dtcs(csv, fmt):
    """-> [dict(raw=3 bytes, status, severity, text)]"""
    out = []
    for tok in [t.strip() for t in csv.split(",") if t.strip()]:
        parts = tok.split(":")
        raw = j1939_bytes(parts[0]) if fmt == 0x02 else j2012_bytes(parts[0])
        out.append({"raw": raw, "text": parts[0].upper(),
                    "status": int(parts[1], 16) if len(parts) > 1
                    else STATUS_CONFIRMED,
                    "severity": int(parts[2], 16) if len(parts) > 2
                    else 0x02})
    return out


class WwhEcu:
    """One ECU's application layer: handle(request, functional) -> the list
    of response payloads to send in order ([] = stay silent)."""

    def __init__(self, addr, pids, dtcs, permanent, a, raw=None):
        self.addr = addr
        self.raw = dict(raw or {})      # pid -> the bytes this ECU answers
        self.pids = tuple(pids)
        self.dtcs = dtcs
        self.permanent = permanent
        self.values = dict(DEFAULTS)
        self.values.update(a.values)
        self.vin = a.vin
        self.fmt = a.dtc_format
        self.f810 = None if a.no_f810 else a.f810
        self.pending = a.pending
        self.classic = a.also_classic
        self.clears = 0

    # -- helpers --
    def mil(self):
        return any(d["status"] & STATUS_CONFIRMED for d in self.dtcs)

    def neg(self, sid, nrc, functional):
        if functional and nrc in (0x11, 0x12, 0x31, 0x7E, 0x7F):
            return []
        return [bytes([0x7F, sid, nrc])]

    def with_pending(self, sid, resp):
        return [bytes([0x7F, sid, 0x78])] * self.pending + [resp]

    def did(self, did):
        """The data record of one DID, or None when unsupported."""
        hi, lo = did >> 8, did & 0xFF
        if hi == 0xF4:
            if lo % 0x20 == 0:
                bm = support_bitmap(self.pids, lo)
                return bm if lo == 0 or any(bm) else None
            if lo in self.raw:
                return self.raw[lo]
            if lo in self.pids:
                return pid_data(lo, self.values, self.mil(),
                                sum(1 for d in self.dtcs
                                    if d["status"] & STATUS_CONFIRMED))
            return None
        if did == 0xF800:
            return support_bitmap((0x02, 0x04, 0x0A), 0)
        if did == 0xF802:
            return self.vin.encode("ascii")
        if did == 0xF804:
            return b"WICANBENCHCAL001"
        if did == 0xF80A:
            return (b"ECM\x00-WWH bench ECU" + b"\x00" * 20)[:20]
        if did == 0xF810:
            return None if self.f810 is None else bytes([self.f810])
        return None

    # -- services --
    def svc_22(self, req, functional):
        body = req[1:]
        if len(body) < 2 or len(body) % 2 or len(body) > 12:
            return self.neg(0x22, 0x13, functional)
        out = b""
        vin = False
        for i in range(0, len(body), 2):
            did = (body[i] << 8) | body[i + 1]
            rec = self.did(did)
            if rec is not None:
                out += body[i:i + 2] + rec
                vin = vin or did == 0xF802
        if not out:
            return self.neg(0x22, 0x31, functional)
        resp = b"\x62" + out
        return self.with_pending(0x22, resp) if vin else [resp]

    def svc_19(self, req, functional):
        if len(req) < 2:
            return self.neg(0x19, 0x13, functional)
        sub = req[1]
        if sub == 0x42:
            if len(req) != 5:
                return self.neg(0x19, 0x13, functional)
            if req[2] != FGID_EMISSIONS:
                return self.neg(0x19, 0x31, functional)
            resp = bytes([0x59, 0x42, req[2], 0xFF, 0xFF, self.fmt])
            for d in self.dtcs:
                if d["status"] & req[3] and d["severity"] & req[4]:
                    resp += bytes([d["severity"]]) + d["raw"] \
                        + bytes([d["status"]])
            return self.with_pending(0x19, resp)
        if sub == 0x55:
            if len(req) != 3:
                return self.neg(0x19, 0x13, functional)
            if req[2] != FGID_EMISSIONS:
                return self.neg(0x19, 0x31, functional)
            resp = bytes([0x59, 0x55, req[2], 0xFF, self.fmt])
            for d in self.permanent:
                resp += d["raw"] + bytes([d["status"]])
            return self.with_pending(0x19, resp)
        return self.neg(0x19, 0x12, functional)

    def svc_14(self, req, functional):
        if len(req) != 4:
            return self.neg(0x14, 0x13, functional)
        if req[1:4] != bytes([0xFF, 0xFF, FGID_EMISSIONS]):
            return self.neg(0x14, 0x31, functional)
        self.dtcs = []
        self.clears += 1
        return [b"\x54"]

    def svc_classic(self, req):
        sid = req[0]
        if sid == 0x01 and len(req) >= 2:
            out = b""
            for pid in req[1:7]:
                if pid % 0x20 == 0:
                    out += bytes([pid]) + support_bitmap(self.pids, pid)
                elif pid in self.pids:
                    out += bytes([pid]) + pid_data(
                        pid, self.values, self.mil(), len(self.dtcs))
            return [b"\x41" + out] if out else []
        if sid == 0x09 and req[1:2] == b"\x02":
            return [b"\x49\x02\x01" + self.vin.encode("ascii")]
        if sid == 0x03:
            codes = [d["raw"][:2] for d in self.dtcs
                     if d["status"] & STATUS_CONFIRMED]
            return [bytes([0x43, len(codes)]) + b"".join(codes)]
        return []

    def handle(self, req, functional):
        if not req:
            return []
        sid = req[0]
        if sid == 0x22:
            return self.svc_22(req, functional)
        if sid == 0x19:
            return self.svc_19(req, functional)
        if sid == 0x14:
            return self.svc_14(req, functional)
        if sid == 0x3E:
            if len(req) == 2 and req[1] == 0x80:
                return []
            return [b"\x7E\x00"] if len(req) == 2 \
                else self.neg(sid, 0x13, functional)
        if sid == 0x10 and len(req) == 2:
            return [bytes([0x50, req[1], 0x00, 0x32, 0x01, 0xF4])]
        if sid in (0x01, 0x02, 0x03, 0x04, 0x06, 0x07, 0x09, 0x0A) \
                and self.classic:
            return self.svc_classic(req)
        return self.neg(sid, 0x11, functional)


def build_ecus(a):
    if getattr(a, "sprinter", False):
        # the first ECU carries --dtcs / --permanent, the SCR unit --dtcs2
        own = {0x58: (a.dtcs, a.permanent), 0x5D: (a.dtcs2, a.permanent2)}
        return [WwhEcu(addr, pids,
                       parse_dtcs(own.get(addr, ("", ""))[0], a.dtc_format),
                       parse_dtcs(own.get(addr, ("", ""))[1], a.dtc_format),
                       a, raw)
                for addr, pids, raw in SPRINTER]
    ecus = [WwhEcu(a.ecu, ENGINE_PIDS, parse_dtcs(a.dtcs, a.dtc_format),
                   parse_dtcs(a.permanent, a.dtc_format), a)]
    if a.ecu2 is not None:
        ecus.append(WwhEcu(a.ecu2, AFTERTREATMENT_PIDS,
                           parse_dtcs(a.dtcs2, a.dtc_format),
                           parse_dtcs(a.permanent2, a.dtc_format), a))
    return ecus


def ids_for(a, index, ecu):
    """-> (physical request id, response id, functional id)."""
    if a.id_format == 29:
        return (0x18DA00F1 | (ecu.addr << 8), 0x18DAF100 | ecu.addr, FUNC_29)
    return 0x7E0 + index, 0x7E8 + index, FUNC_11


# ---- the PCAN shell -----------------------------------------------------------

def run_pcan(a):
    import can
    import isotp

    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=a.bitrate * 1000)
    notifier = can.Notifier(bus, [])
    mode = isotp.AddressingMode.Normal_29bits if a.id_format == 29 \
        else isotp.AddressingMode.Normal_11bits
    params = {"stmin": a.stmin, "blocksize": a.bs, "tx_padding": 0x00,
              "rx_flowcontrol_timeout": 1000,
              "rx_consecutive_frame_timeout": 1000, "max_frame_size": 4095}
    errors = []

    def on_error(e):
        errors.append(f"{e.__class__.__name__}: {e}")

    links = []
    stats = {"requests": 0, "functional": 0, "silent": 0, "negative": 0,
             "bus_errors": 0}
    lock = threading.Lock()
    stop = threading.Event()

    class BusErrors(can.Listener):
        """A bus error as PCAN reports it has a non-zero id (the kind);
        id 0 is only its error counter moving."""

        def on_message_received(self, msg):
            if msg.is_error_frame and msg.arbitration_id != 0:
                with lock:
                    stats["bus_errors"] += 1

    notifier.add_listener(BusErrors())

    def add_ecu(i, ecu):
        req_id, resp_id, func_id = ids_for(a, i, ecu)
        phys = isotp.NotifierBasedCanStack(
            bus, notifier, address=isotp.Address(mode, rxid=req_id,
                                                 txid=resp_id),
            params=params, error_handler=on_error)
        func = isotp.NotifierBasedCanStack(
            bus, notifier, address=isotp.Address(mode, rxid=func_id,
                                                 txid=resp_id),
            params=params, error_handler=on_error)
        phys.start()
        func.start()
        links.append((ecu, phys, func, req_id, resp_id))
        w = 8 if a.id_format == 29 else 3
        print(f"ECU READY addr=0x{ecu.addr:02X} req={req_id:0{w}X} "
              f"resp={resp_id:0{w}X} func={func_id:0{w}X} {a.bitrate}k "
              f"on {a.pcan} pids={len(ecu.pids)} dtcs="
              f"{[d['text'] for d in ecu.dtcs]}", flush=True)
        return links[-1]

    for i, ecu in enumerate(build_ecus(a)):
        add_ecu(i, ecu)

    def serve(ecu, phys, func, resp_id):
        """One ECU = one thread: an ECU whose First Frame gets no flow
        control (the tester filtered it out) waits its N_Bs alone and
        never delays the other ECU, like separate controllers."""
        while not stop.is_set():
            idle = True
            for stack, functional in ((phys, False), (func, True)):
                req = stack.recv(block=False)
                if req is None:
                    continue
                idle = False
                n_err = len(errors)
                out = ecu.handle(bytes(req), functional)
                for resp in out:
                    phys.send(resp)
                    t0 = time.time()
                    while phys.transmitting() and time.time() - t0 < 3:
                        time.sleep(0.0005)
                    if resp[2:3] == b"\x78":
                        time.sleep(0.02)
                with lock:
                    stats["requests"] += 1
                    stats["functional"] += 1 if functional else 0
                    stats["silent"] += 0 if out else 1
                    stats["negative"] += sum(
                        1 for r in out
                        if r[:1] == b"\x7F" and r[2:3] != b"\x78")
                    print("ECU " + json.dumps({
                        "ecu": f"{resp_id:X}", "func": functional,
                        "req": bytes(req).hex(),
                        "resp": [r.hex() for r in out],
                        "errors": errors[n_err:]}), flush=True)
            if idle:
                time.sleep(0.0005)

    workers = [threading.Thread(target=serve, args=(e, p, f, r), daemon=True)
               for e, p, f, _q, r in links]
    for w in workers:
        w.start()

    def traffic():
        """The bus of a vehicle that talks by itself: one broadcast frame,
        a.traffic times a second."""
        n = 0
        gap = 1.0 / a.traffic
        nxt = time.time()
        while not stop.is_set():
            try:
                bus.send(can.Message(arbitration_id=0x18FF1021,
                                     is_extended_id=True,
                                     data=n.to_bytes(8, "big")), timeout=0.05)
            except can.CanError:
                pass
            n += 1
            nxt += gap
            time.sleep(max(0.0, nxt - time.time()))

    if a.traffic > 0:
        workers.append(threading.Thread(target=traffic, daemon=True))
        workers[-1].start()

    truck = None
    if getattr(a, "truck", False):
        import pcan_j1939_truck as T
        import j1939_ref as J
        tlock = threading.Lock()

        def truck_tx(cid, data):
            try:
                bus.send(can.Message(arbitration_id=cid, is_extended_id=True,
                                     data=data), timeout=0.05)
            except can.CanError:
                with lock:
                    stats["truck_tx_errors"] = stats.get("truck_tx_errors", 0) + 1

        def truck_log(ev):
            print("TRUCK " + json.dumps(ev), flush=True)

        targs = argparse.Namespace(
            sa=a.truck_sa, values=a.truck_values, dtcs=T.parse_dtcs(a.truck_dtcs),
            prev=T.parse_dtcs(a.truck_prev), lamps={"mil": 1} if a.truck_dtcs else {},
            vin=a.vin, fault="", twin=None, contend=None, period_ms=0, count=0,
            on_request_period=0, asks=[], ask_period=2.0)
        truck = T.Truck(truck_tx, targs, truck_log)

        class TruckEars(can.Listener):
            """Every 29-bit frame of the others reaches the truck (the
            ISO-TP stacks keep theirs: the listener only reads)."""

            def on_message_received(self, msg):
                if msg.is_error_frame or not msg.is_extended_id:
                    return
                pgn = J.parse_id(msg.arbitration_id)[1]
                if pgn in (0xDA00, 0xDB00):
                    return          # the ECUs' ISO 15765 conversations
                with tlock:
                    truck.on_frame(msg.arbitration_id, bytes(msg.data),
                                   time.perf_counter())

        notifier.add_listener(TruckEars())

        def truck_clock():
            while not stop.is_set():
                with tlock:
                    truck.tick(time.perf_counter())
                time.sleep(0.001)

        workers.append(threading.Thread(target=truck_clock, daemon=True))
        workers[-1].start()
        print(f"TRUCK READY sa=0x{a.truck_sa:02X} {a.bitrate}k on {a.pcan} "
              f"dtcs={targs.dtcs} beside the WWH ECUs", flush=True)

    t_start = time.time()
    t_stat = t_start
    late_due = a.late_5a if getattr(a, "sprinter", False) else 0
    try:
        while time.time() - t_start < a.secs:
            if a.stop_file and os.path.exists(a.stop_file):
                break
            if a.traffic > 0 and time.time() - t_stat >= 1.0:
                t_stat = time.time()
                with lock:
                    print("ECU STAT " + json.dumps(
                        {"t": round(t_stat, 2),
                         "bus_errors": stats["bus_errors"]}), flush=True)
            if late_due > 0 and time.time() - t_start >= late_due:
                late_due = 0
                addr, pids, raw = SPRINTER_LATE
                e, p, f, _q, r = add_ecu(len(links),
                                         WwhEcu(addr, pids, [], [], a, raw))
                workers.append(threading.Thread(target=serve,
                                                args=(e, p, f, r),
                                                daemon=True))
                workers[-1].start()
            time.sleep(0.1)
    finally:
        stop.set()
        for w in workers:
            w.join(timeout=4)
        for _ecu, phys, func, _r, _t in links:
            phys.stop()
            func.stop()
        notifier.stop()
        bus.shutdown()
    stats["clears"] = sum(e.clears for e, *_ in links)
    stats["isotp_errors"] = errors[:10]
    if truck is not None:
        t = dict(truck.stats)
        t["tx"] = {f"{p:04X}": n for p, n in sorted(truck.sent.items())}
        stats["truck"] = t
    print("ECU DONE " + json.dumps(stats), flush=True)
    return 0


# ---- selftest (no adapter) ----------------------------------------------------

def selftest():
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")

    def hx(s):
        return bytes.fromhex(s.replace(" ", ""))

    def make(**kw):
        base = dict(ecu=0x00, ecu2=0x3D, dtcs="P0420:08:02,P2463-1F:04:04",
                    dtcs2="", permanent="P0420", permanent2="",
                    dtc_format=0x04, vin="1WCANWWH0TRUCK001", values={},
                    pending=0, no_f810=False, f810=0x01, also_classic=False,
                    id_format=11)
        base.update(kw)
        a = argparse.Namespace(**base)
        return build_ecus(a), a

    (eng, aft), a = make()

    check("f810", eng.handle(hx("22 F8 10"), True), [hx("62 F8 10 01")])
    check("f400", eng.handle(hx("22 F4 00"), True),
          [hx("62 F4 00 98 1B 80 03")])
    check("f420", eng.handle(hx("22 F4 20"), True),
          [hx("62 F4 20 00 02 20 01")])
    check("f440", eng.handle(hx("22 F4 40"), True),
          [hx("62 F4 40 44 00 00 15")])
    check("f4a0", eng.handle(hx("22 F4 A0"), True),
          [hx("62 F4 A0 04 00 00 00")])
    check("f400 ecu2", aft.handle(hx("22 F4 00"), True),
          [hx("62 F4 00 80 00 00 01")])
    check("rpm", eng.handle(hx("22 F4 0C"), True), [hx("62 F4 0C 13 88")])
    check("speed", eng.handle(hx("22 F4 0D"), False), [hx("62 F4 0D 40")])
    check("two dids", eng.handle(hx("22 F4 0C F4 0D"), True),
          [hx("62 F4 0C 13 88 F4 0D 40")])
    check("one of two", eng.handle(hx("22 F4 0C F4 3C"), True),
          [hx("62 F4 0C 13 88")])
    check("unsupported functional", eng.handle(hx("22 F4 3C"), True), [])
    check("unsupported physical", eng.handle(hx("22 F4 3C"), False),
          [hx("7F 22 31")])
    check("mil", eng.handle(hx("22 F4 01"), True),
          [hx("62 F4 01 81 07 65 04")])
    check("vin", eng.handle(hx("22 F8 02"), True),
          [hx("62 F8 02") + b"1WCANWWH0TRUCK001"])

    # 19 42: confirmed, class A..C
    check("19 42 confirmed", eng.handle(hx("19 42 33 08 1E"), True),
          [hx("59 42 33 FF FF 04 02 04 20 00 08")])
    check("19 42 pending", eng.handle(hx("19 42 33 04 1E"), True),
          [hx("59 42 33 FF FF 04 04 24 63 1F 04")])
    check("19 42 class filter", eng.handle(hx("19 42 33 0C 04"), True),
          [hx("59 42 33 FF FF 04 04 24 63 1F 04")])
    check("19 42 none", aft.handle(hx("19 42 33 08 1E"), True),
          [hx("59 42 33 FF FF 04")])
    check("19 42 group", eng.handle(hx("19 42 FF 08 1E"), False),
          [hx("7F 19 31")])
    check("19 55", eng.handle(hx("19 55 33"), True),
          [hx("59 55 33 FF 04 04 20 00 08")])
    check("19 02 functional", eng.handle(hx("19 02 08"), True), [])
    check("19 02 physical", eng.handle(hx("19 02 08"), False),
          [hx("7F 19 12")])

    # classic services: a WWH ECU does not know them
    check("0100 functional", eng.handle(hx("01 00"), True), [])
    check("0100 physical", eng.handle(hx("01 00"), False), [hx("7F 01 11")])

    # clear: only the emissions group, permanent survives
    check("14 other group", eng.handle(hx("14 FF FF FF"), False),
          [hx("7F 14 31")])
    check("14", eng.handle(hx("14 FF FF 33"), True), [hx("54")])
    check("19 42 after clear", eng.handle(hx("19 42 33 0C 1E"), True),
          [hx("59 42 33 FF FF 04")])
    check("19 55 after clear", eng.handle(hx("19 55 33"), True),
          [hx("59 55 33 FF 04 04 20 00 08")])
    check("mil after clear", eng.handle(hx("22 F4 01"), True),
          [hx("62 F4 01 00 07 65 04")])

    # J1939-73 DTC format, pending frames, an ECU without F810, classic too
    (eng, _aft), _a = make(dtc_format=0x02, dtcs="3226-4:08:02",
                           permanent="", pending=2)
    check("19 42 j1939", eng.handle(hx("19 42 33 08 1E"), True),
          [hx("7F 19 78"), hx("7F 19 78"),
           hx("59 42 33 FF FF 02 02 9A 0C 04 08")])
    check("vin pending", len(eng.handle(hx("22 F8 02"), True)), 3)
    check("rpm not pending", len(eng.handle(hx("22 F4 0C"), True)), 1)
    (eng, _aft), _a = make(no_f810=True)
    check("no f810", eng.handle(hx("22 F8 10"), True), [])
    check("no f810 f400", eng.handle(hx("22 F4 00"), True),
          [hx("62 F4 00 98 1B 80 03")])
    (eng, _aft), _a = make(also_classic=True)
    check("classic 0100", eng.handle(hx("01 00"), True),
          [hx("41 00 98 1B 80 03")])
    check("classic 010C", eng.handle(hx("01 0C"), True), [hx("41 0C 13 88")])
    check("classic vin", eng.handle(hx("09 02"), True),
          [hx("49 02 01") + b"1WCANWWH0TRUCK001"])

    # the Sprinter VS30: the field report's bitmaps and answers
    ecus, _a = make(sprinter=True, id_format=29, no_f810=True,
                    vin=SPRINTER_VIN, values={"rpm": SPRINTER_RPM}, dtcs="",
                    permanent="")
    e58, e59, e5d = ecus
    check("sprinter f400 58", e58.handle(hx("22 F4 00"), True),
          [hx("62 F4 00 98 18 A0 13")])
    check("sprinter f400 59", e59.handle(hx("22 F4 00"), True),
          [hx("62 F4 00 98 18 00 01")])
    check("sprinter f400 5D", e5d.handle(hx("22 F4 00"), True),
          [hx("62 F4 00 98 18 00 01")])
    check("sprinter rpm", e58.handle(hx("22 F4 0C"), False),
          [hx("62 F4 0C 0B ED")])
    check("sprinter f485", e5d.handle(hx("22 F4 85"), True),
          [hx("62 F4 85 07 00 1B 00 1B B4 00 00 00 00")])
    check("sprinter f485 not on 58", e58.handle(hx("22 F4 85"), True), [])
    check("sprinter no f810", e58.handle(hx("22 F8 10"), True), [])
    check("sprinter no classic", e58.handle(hx("01 00"), True), [])
    late = WwhEcu(SPRINTER_LATE[0], SPRINTER_LATE[1], [], [], _a,
                  SPRINTER_LATE[2])
    check("sprinter 5A f400", late.handle(hx("22 F4 00"), True),
          [hx("62 F4 00 00 10 00 00")])
    check("sprinter 5A rpm unscaled", late.handle(hx("22 F4 0C"), True),
          [hx("62 F4 0C 02 FB")])
    check("sprinter vin", e58.handle(hx("22 F8 02"), False),
          [hx("62 F8 02") + SPRINTER_VIN.encode()])

    # ids
    (eng, aft), a = make(id_format=29)
    check("ids 29", ids_for(a, 0, eng), (0x18DA00F1, 0x18DAF100, 0x18DB33F1))
    check("ids 29 ecu2", ids_for(a, 1, aft),
          (0x18DA3DF1, 0x18DAF13D, 0x18DB33F1))
    a.id_format = 11
    check("ids 11", ids_for(a, 1, aft), (0x7E1, 0x7E9, 0x7DF))

    if fails:
        print("WWH ECU SELFTEST FAIL:\n  " + "\n  ".join(fails))
        return 1
    print("WWH ECU SELFTEST PASS")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("secs", nargs="?", type=float, default=120)
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--bitrate", type=int, default=500, choices=(250, 500))
    ap.add_argument("--id-format", type=int, default=11, choices=(11, 29))
    ap.add_argument("--ecu", default="0x00")
    ap.add_argument("--ecu2", default="", help="second ECU address ('' = none)")
    ap.add_argument("--dtcs", default="P0420:08:02,P2463-1F:04:04")
    ap.add_argument("--dtcs2", default="")
    ap.add_argument("--permanent", default="")
    ap.add_argument("--permanent2", default="")
    ap.add_argument("--dtc-format", default="04", choices=("04", "02"))
    ap.add_argument("--vin", default="1WCANWWH0TRUCK001")
    ap.add_argument("--set", default="", help="value overrides k=v,...")
    ap.add_argument("--pending", type=int, default=0)
    ap.add_argument("--f810", default="0x01", help="protocol id byte")
    ap.add_argument("--no-f810", action="store_true")
    ap.add_argument("--also-classic", action="store_true")
    ap.add_argument("--stmin", type=int, default=0)
    ap.add_argument("--bs", type=int, default=0)
    ap.add_argument("--sprinter", action="store_true")
    ap.add_argument("--late-5a", type=float, default=0,
                    help="--sprinter: ECU 5A turns up after SECS (0 = never)")
    ap.add_argument("--traffic", type=float, default=0,
                    help="broadcast frames a second (0 = a silent bus)")
    ap.add_argument("--truck", action="store_true",
                    help="the J1939 truck on the same bus (an EU truck)")
    ap.add_argument("--truck-sa", default="0x00")
    ap.add_argument("--truck-dtcs", default="", help="active: spn-fmi-oc,...")
    ap.add_argument("--truck-prev", default="", help="previously active")
    ap.add_argument("--truck-set", default="", help="truck value overrides k=v,...")
    ap.add_argument("--stop-file", default="")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    a.ecu = int(a.ecu, 0)
    a.ecu2 = int(a.ecu2, 0) if a.ecu2 else None
    a.truck_sa = int(a.truck_sa, 0)
    a.truck_values = {k: float(v) for k, v in
                      (kv.split("=") for kv in a.truck_set.split(",") if kv)}
    a.f810 = int(a.f810, 0)
    a.dtc_format = int(a.dtc_format, 16)
    a.values = {k: float(v) for k, v in
                (kv.split("=") for kv in a.set.split(",") if kv)}
    unknown = [k for k in a.values if k not in DEFAULTS]
    if unknown:
        ap.error(f"unknown value(s) {unknown}; known: {sorted(DEFAULTS)}")
    if a.sprinter:
        # the van as reported: 29-bit ids, no protocol identification
        a.id_format = 29
        a.no_f810 = True
        a.values.setdefault("rpm", SPRINTER_RPM)
        if a.vin == ap.get_default("vin"):
            a.vin = SPRINTER_VIN
    return run_pcan(a)


if __name__ == "__main__":
    sys.exit(main())
