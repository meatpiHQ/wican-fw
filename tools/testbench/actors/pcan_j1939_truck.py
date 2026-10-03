#!/usr/bin/env python3
"""A J1939 truck on the PCAN: the independent reference peer of the J1939
benches (TASK_j1939_wwh.md). Codec = lib/j1939_ref.py.

What it does on the bus (29-bit, 250k or 500k):
  - broadcasts the PGN set of j1939_ref.PGN_SET from the engine (--sa) at
    their J1939 periods with the values of j1939_ref.DEFAULTS (--set k=v),
  - DM1 every second: one frame for 0 or 1 active DTC, BAM for more,
  - answers Request (PGN 59904): single-frame PGNs directly; VIN, DM2,
    component id through the transport protocol (BAM when the request was
    global, RTS/CTS when it was sent to the truck); DM3 / DM11 clear the
    lists and are acknowledged; an unknown PGN gets a NACK when the request
    was destination specific and silence when it was global,
  - sends its address claim at start and on request, and can contend for
    another node's address (--contend),
  - with --ask SA:PGN[,SA:PGN..]: sends a Request for each group to that
    node (SA 0xFF = everyone) every --ask-period seconds, as an instrument
    cluster asking a tool for its address claim (EE00) or for a group it
    does not have (the tool must NACK); the acknowledgments it hears are
    logged (`ackm` events) and counted,
  - with --on-request-period S: sends its on-request groups (vehicle
    distance, engine hours, fuel consumption in a frame each, the VIN by BAM)
    every S seconds, as it would on a vehicle whose instrument cluster keeps
    asking. A listener that never asks sees them only this way,
  - faults for the negative legs (--fault, see FAULTS).

  python pcan_j1939_truck.py [secs] [--pcan PCAN_USBBUS2] [--bitrate 250]
        [--sa 0x00] [--vin 1WCANJ1939TRUCK01] [--dtcs 110-0-5,3226-4-1]
        [--prev 100-1-2] [--lamps mil=1,awl=1] [--set rpm=1800,speed_kmh=60]
        [--period-ms N] [--count N] [--twin 0x11] [--contend 0xF9:win]
        [--on-request-period S] [--flood FPS] [--flood-prio 0]
        [--fault bam_drop:2] [--ask 0xF9:EE00,0xF9:FE00] [--ask-period 2]
        [--stop-file PATH] [--selftest]

Prints `TRUCK READY ...`, one `TRUCK {json}` line per event and
`TRUCK DONE {json}` (per-PGN tx counts, foreign frames seen, bus errors).
--stop-file PATH stops it cleanly once PATH exists (never kill it while it
transmits: the PEAK driver wedges). Run it on
the PC with the v6.0.2 IDF venv python (python-can). A listen-only DUT does
not ACK: another node (the ECU simulator) must be on the bus at this
bitrate or nothing leaves the adapter.
"""
import argparse
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import j1939_ref as J  # noqa: E402

FAULTS = ("bam_drop:K  skip TP.DT packet K of every BAM",
          "bam_reorder  swap the first two TP.DT packets of every BAM",
          "bam_stall  stop every BAM after its first packet",
          "rts_abort  answer a CTS with a connection abort",
          "na  broadcast EEC1 and CCVS1 as not available (FF)",
          "err  broadcast EEC1 and CCVS1 with the error indicator (FE)")

BAM_GAP = 0.055        # s between BAM packets (J1939-21: 50..200 ms)
DT_GAP = 0.003         # s between RTS/CTS data packets
T3 = 1.25              # s: no CTS after RTS / no EOMA after the last packet
FILLER_PGN = 0xFF10    # proprietary B, the --flood frames
FILLER_SA = 0x21


def parse_dtcs(csv):
    out = []
    for tok in [t for t in csv.split(",") if t.strip()]:
        parts = tok.split("-")
        out.append((int(parts[0]), int(parts[1]),
                    int(parts[2]) if len(parts) > 2 else 1))
    return out


class Truck:
    """The truck's behaviour, free of CAN and of the wall clock: tx(cid,
    data) sends a frame, every entry point takes `now` (seconds)."""

    def __init__(self, tx, a, log=lambda ev: None):
        self.tx_raw = tx
        self.log = log
        self.sa = a.sa
        self.values = dict(J.DEFAULTS)
        self.values.update(a.values)
        self.active = list(a.dtcs)
        self.previous = list(a.prev)
        self.lamps = dict(a.lamps)
        self.vin = a.vin
        self.fault = a.fault or ""
        self.twin = a.twin
        self.contend = a.contend
        self.period_override = a.period_ms
        self.count_limit = a.count
        self.asked_period = a.on_request_period
        self.asked_due = None
        self.asks = list(getattr(a, "asks", []) or [])   # [(da, pgn)]
        self.ask_period = getattr(a, "ask_period", 2.0)
        self.ask_due = None
        self.name = J.name_field(0x1A2B3, manufacturer=0x123, function=0)
        self.sent = {}                  # pgn -> frames transmitted
        self.due = {}                   # pgn -> next due time
        self.bams = []                  # queued (pgn, payload)
        self.bam = None                 # dict(pgn, packets, i, next)
        self.conn = None                # RTS/CTS session we originate
        self.stats = {"requests": 0, "nacks": 0, "acks": 0, "bams": 0,
                      "rts_ok": 0, "rts_aborted": 0, "claims_seen": [],
                      "contended": 0, "asks": 0, "acks_seen": 0,
                      "nacks_seen": 0}
        self.started = False

    # -- tx helpers --
    def tx(self, prio, pgn, data, da=J.GLOBAL, sa=None):
        self.tx_raw(J.can_id(prio, pgn, self.sa if sa is None else sa, da),
                    bytes(data))
        self.sent[pgn] = self.sent.get(pgn, 0) + 1

    def period(self, per_ms):
        return (self.period_override or per_ms) / 1000.0

    def dm1(self):
        return J.dm_encode(self.lamps if self.active else {}, self.active)

    def multi(self, pgn):
        """Payload of an on-request PGN, or None when unsupported."""
        if pgn == J.PGN_VI:
            return J.vin_payload(self.vin)
        if pgn == J.PGN_DM1:
            return self.dm1()
        if pgn == J.PGN_DM2:
            return J.dm_encode(self.lamps if self.previous else {},
                               self.previous)
        if pgn == J.PGN_CI:
            return b"MEATP*TRUCKSIM*0001**"
        if pgn == J.PGN_SOFT:
            return b"\x01WICAN-BENCH 1.0*"
        return J.encode_pgn(pgn, self.values)

    # -- periodic --
    def tick(self, now):
        if not self.started:
            self.started = True
            self.tx(6, J.PGN_ADDRESS_CLAIMED, self.name)
            for _n, pgn, _p, per in J.PGN_SET:
                if per:
                    self.due[pgn] = now
            self.due[J.PGN_DM1] = now + 0.5
        for _n, pgn, prio, per in J.PGN_SET:
            if not per or now < self.due[pgn]:
                continue
            self.due[pgn] += self.period(per)
            if self.count_limit and self.sent.get(pgn, 0) >= self.count_limit:
                continue
            data = J.encode_pgn(pgn, self.values)
            if pgn in (J.PGN_EEC1, J.PGN_CCVS1):
                if "na" in self.fault:
                    data = b"\xFF" * 8
                elif "err" in self.fault:
                    data = b"\xFE" * 8
            self.tx(prio, pgn, data)
            if self.twin is not None and pgn in (J.PGN_EEC1, J.PGN_CCVS1):
                other = dict(self.values, rpm=self.values["rpm"] + 1000,
                             speed_kmh=self.values["speed_kmh"] + 20)
                self.tx(prio, pgn, J.encode_pgn(pgn, other), sa=self.twin)
        if now >= self.due.get(J.PGN_DM1, now + 1):
            self.due[J.PGN_DM1] += 1.0
            payload = self.dm1()
            if len(payload) <= 8:
                self.tx(6, J.PGN_DM1, payload)
            elif not any(p == J.PGN_DM1 for p, _ in self.bams) and \
                    not (self.bam and self.bam["pgn"] == J.PGN_DM1):
                self.bams.append((J.PGN_DM1, payload))
        if self.asked_period:
            if self.asked_due is None:
                self.asked_due = now + 0.3
            if now >= self.asked_due:
                self.asked_due += self.asked_period
                self.send_asked()
        if self.asks:
            if self.ask_due is None:
                self.ask_due = now + 1.0
            if now >= self.ask_due:
                self.ask_due += self.ask_period
                for da, pgn in self.asks:
                    self.tx(6, J.PGN_REQUEST, J.request(pgn), da=da)
                    self.stats["asks"] += 1
                    self.log({"ev": "ask", "pgn": f"{pgn:04X}",
                              "da": f"{da:02X}"})
        self.bam_step(now)
        self.conn_step(now)

    def send_asked(self):
        """The on-request groups, as after a global request: single frames
        at once, the VIN through BAM."""
        for _n, pgn, prio, per in J.PGN_SET:
            if not per:
                self.tx(prio, pgn, J.encode_pgn(pgn, self.values))
        queued = any(p == J.PGN_VI for p, _ in self.bams)
        going = self.bam is not None and self.bam["pgn"] == J.PGN_VI
        if not queued and not going:
            self.bams.append((J.PGN_VI, J.vin_payload(self.vin)))

    # -- BAM (one at a time per source) --
    def bam_step(self, now):
        if self.bam is None:
            if not self.bams:
                return
            pgn, payload = self.bams.pop(0)
            packets = J.tp_packets(payload)
            if self.fault.startswith("bam_drop:"):
                k = int(self.fault.split(":")[1])
                packets = [p for p in packets if p[0] != k]
            elif self.fault == "bam_reorder" and len(packets) > 1:
                packets[0], packets[1] = packets[1], packets[0]
            elif self.fault == "bam_stall":
                packets = packets[:1]
            self.tx(7, J.PGN_TP_CM, J.tp_bam(len(payload), pgn))
            self.bam = {"pgn": pgn, "packets": packets, "i": 0,
                        "next": now + BAM_GAP}
            self.stats["bams"] += 1
            self.log({"ev": "bam", "pgn": f"{pgn:04X}", "size": len(payload),
                      "packets": len(packets)})
            return
        if now < self.bam["next"]:
            return
        b = self.bam
        self.tx(7, J.PGN_TP_DT, b["packets"][b["i"]])
        b["i"] += 1
        b["next"] = now + BAM_GAP
        if b["i"] >= len(b["packets"]):
            self.bam = None

    # -- RTS/CTS as originator --
    def conn_open(self, pgn, payload, da, now):
        if self.conn is not None:
            return                      # one session at a time; the peer retries
        self.conn = {"pgn": pgn, "payload": payload, "da": da,
                     "packets": J.tp_packets(payload), "todo": [],
                     "deadline": now + T3, "next": now, "wait": "cts"}
        self.tx(7, J.PGN_TP_CM, J.tp_rts(len(payload), pgn), da=da)
        self.log({"ev": "rts", "pgn": f"{pgn:04X}", "da": f"{da:02X}",
                  "size": len(payload)})

    def conn_abort(self, reason, why):
        c = self.conn
        self.tx(7, J.PGN_TP_CM, J.tp_abort(reason, c["pgn"]), da=c["da"])
        self.stats["rts_aborted"] += 1
        self.log({"ev": "rts_abort", "why": why})
        self.conn = None

    def conn_step(self, now):
        c = self.conn
        if c is None:
            return
        if c["todo"]:
            if now >= c["next"]:
                self.tx(7, J.PGN_TP_DT, c["packets"][c["todo"].pop(0) - 1],
                        da=c["da"])
                c["next"] = now + DT_GAP
                c["deadline"] = now + T3
            return
        if now > c["deadline"]:
            self.conn_abort(3, f"timeout waiting for {c['wait']}")

    def on_tp_cm(self, d, src, now):
        c = self.conn
        if c is None or src != c["da"] or J.pgn_from_le(d[5:8]) != c["pgn"]:
            return
        if d[0] == J.TP_CTS:
            if self.fault == "rts_abort":
                self.conn_abort(1, "fault rts_abort")
                return
            if d[1] == 0:               # hold the connection
                c["deadline"] = now + T3
                return
            last = min(d[2] + d[1] - 1, len(c["packets"]))
            c["todo"] = list(range(d[2], last + 1))
            c["wait"] = "cts or eoma"
        elif d[0] == J.TP_EOMA:
            ok = (d[1] | (d[2] << 8)) == len(c["payload"]) and \
                d[3] == len(c["packets"])
            self.stats["rts_ok" if ok else "rts_aborted"] += 1
            self.log({"ev": "eoma", "ok": ok, "raw": bytes(d).hex()})
            self.conn = None
        elif d[0] == J.TP_ABORT:
            self.stats["rts_aborted"] += 1
            self.log({"ev": "peer_abort", "reason": d[1]})
            self.conn = None

    # -- rx --
    def on_frame(self, cid, data, now):
        _prio, pgn, src, da = J.parse_id(cid)
        if src == self.sa or src == self.twin:
            return
        if pgn == J.PGN_ADDRESS_CLAIMED:
            self.stats["claims_seen"].append(
                {"sa": f"{src:02X}", "name": bytes(data).hex()})
            self.log({"ev": "claim_seen", "sa": f"{src:02X}",
                      "name": bytes(data).hex()})
            if self.contend and src == self.contend[0]:
                # win = a NAME that beats any other (lowest value)
                name = bytes(8) if self.contend[1] == "win" else b"\xFF" * 8
                self.tx(6, J.PGN_ADDRESS_CLAIMED, name, sa=src)
                self.stats["contended"] += 1
            return
        if pgn == J.PGN_ACKM and len(data) >= 8:
            # an acknowledgment: to everyone, naming the requester (byte 4)
            kind = "acks_seen" if data[0] == J.ACK else "nacks_seen"
            self.stats[kind] += 1
            self.log({"ev": "ackm", "from": f"{src:02X}", "control": data[0],
                      "for": f"{data[4]:02X}",
                      "pgn": f"{J.pgn_from_le(data[5:8]):04X}"})
            return
        if da not in (self.sa, J.GLOBAL):
            return
        if pgn == J.PGN_TP_CM:
            self.on_tp_cm(data, src, now)
        elif pgn == J.PGN_REQUEST and len(data) >= 3:
            self.on_request(J.pgn_from_le(data[:3]), src, da, now)

    def on_request(self, want, src, da, now):
        self.stats["requests"] += 1
        self.log({"ev": "request", "pgn": f"{want:04X}", "from": f"{src:02X}",
                  "to": f"{da:02X}"})
        if want == J.PGN_ADDRESS_CLAIMED:
            self.tx(6, J.PGN_ADDRESS_CLAIMED, self.name)
            return
        if want in (J.PGN_DM3, J.PGN_DM11):
            if want == J.PGN_DM3:
                self.previous = []
            else:
                self.active = []
            self.tx(6, J.PGN_ACKM, J.ackm(J.ACK, want, src))
            self.stats["acks"] += 1
            return
        payload = self.multi(want)
        if payload is None:
            if da != J.GLOBAL:          # a global request is never NACKed
                self.tx(6, J.PGN_ACKM, J.ackm(J.NACK, want, src))
                self.stats["nacks"] += 1
            return
        if len(payload) <= 8:
            self.tx(6, want, payload, da=src)
        elif da == J.GLOBAL:
            self.bams.append((want, payload))
        else:
            self.conn_open(want, payload, src, now)


# ---- the PCAN shell -----------------------------------------------------------

def flood(bus, can, fps, stop, counter, prio=6):
    gap = 1.0 / fps
    cid = J.can_id(prio, FILLER_PGN, FILLER_SA)
    nxt = time.perf_counter()
    while not stop.is_set():
        n = counter[0]
        try:
            bus.send(can.Message(arbitration_id=cid, is_extended_id=True,
                                 data=n.to_bytes(4, "little") + b"\x00" * 4))
            counter[0] = n + 1
        except can.CanError:
            time.sleep(0.001)           # transmit queue full: back off
        nxt += gap
        delay = nxt - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        else:
            nxt = time.perf_counter()


def run_pcan(a):
    import can

    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=a.bitrate * 1000)
    foreign = {}
    tx_errors = [0]

    def tx(cid, data):
        try:
            bus.send(can.Message(arbitration_id=cid, is_extended_id=True,
                                 data=data))
        except can.CanError:
            tx_errors[0] += 1

    def log(ev):
        print("TRUCK " + json.dumps(ev), flush=True)

    truck = Truck(tx, a, log)
    stop = threading.Event()
    filler = [0]
    if a.flood:
        threading.Thread(target=flood,
                         args=(bus, can, a.flood, stop, filler, a.flood_prio),
                         daemon=True).start()
    print(f"TRUCK READY sa=0x{a.sa:02X} {a.bitrate}k on {a.pcan} "
          f"dtcs={a.dtcs} fault={a.fault or 'none'}", flush=True)
    end = time.perf_counter() + a.secs
    errors = 0
    bus_errors = 0
    look = 0.0
    try:
        while time.perf_counter() < end:
            truck.tick(time.perf_counter())
            if a.stop_file and time.perf_counter() >= look:
                look = time.perf_counter() + 0.2
                if os.path.exists(a.stop_file):
                    break
            m = bus.recv(timeout=0.001)
            while m is not None:
                if m.is_error_frame:
                    errors += 1
                    # a bus error has a non-zero id (its kind); id 0 is only
                    # the adapter's error counter moving
                    if m.arbitration_id != 0:
                        bus_errors += 1
                elif m.is_extended_id:
                    truck.on_frame(m.arbitration_id, bytes(m.data),
                                   time.perf_counter())
                    key = f"{m.arbitration_id:08X}"
                    foreign[key] = foreign.get(key, 0) + 1
                else:
                    key = f"{m.arbitration_id:03X}"
                    foreign[key] = foreign.get(key, 0) + 1
                m = bus.recv(timeout=0)
    finally:
        stop.set()
        time.sleep(0.05)
        bus.shutdown()
    done = dict(truck.stats)
    done.update({"tx": {f"{p:04X}": n for p, n in sorted(truck.sent.items())},
                 "filler_tx": filler[0], "tx_errors": tx_errors[0],
                 "error_frames": errors, "bus_errors": bus_errors,
                 "foreign": dict(sorted(foreign.items())[:64]),
                 "foreign_ids": len(foreign)})
    print("TRUCK DONE " + json.dumps(done), flush=True)
    return 0


# ---- selftest (no adapter) ----------------------------------------------------

def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    def make(**kw):
        base = dict(sa=0x00, values={}, dtcs=[], prev=[], lamps={"mil": 1},
                    vin="1WCANJ1939TRUCK01", fault="", twin=None,
                    contend=None, period_ms=0, count=0, on_request_period=0,
                    asks=[], ask_period=2.0)
        base.update(kw)
        out = []
        return Truck(lambda cid, d: out.append((cid, d)),
                     argparse.Namespace(**base)), out

    def run(truck, t0, t1, step=0.001):
        t = t0
        while t < t1:
            truck.tick(t)
            t += step

    # 1 s of broadcast: claim first, EEC1 at 20 ms, DM1 "no fault" once
    truck, out = make()
    run(truck, 0.0, 1.0)
    ids = [c for c, _ in out]
    check("claim first", ids[0] == 0x18EEFF00)
    check("eec1 rate", 49 <= ids.count(0x0CF00400) <= 51)
    check("ccvs rate", 9 <= ids.count(0x18FEF100) <= 11)
    check("dm1 none", (0x18FECA00, bytes([0, 0xFF, 0, 0, 0, 0, 0xFF, 0xFF]))
          in out)
    check("eec1 data", (0x0CF00400, J.encode_pgn(J.PGN_EEC1, J.DEFAULTS))
          in out)

    # two active DTCs: DM1 through BAM, packets >= 50 ms apart, reassembles
    truck, out = make(dtcs=[(110, 0, 5), (3226, 4, 1)])
    stamps = []
    truck.tx_raw = lambda cid, d: (out.append((cid, d)), stamps.append(now[0]))
    now = [0.0]
    while now[0] < 1.2:
        truck.tick(now[0])
        now[0] += 0.001
    tp = [(c, d, t) for (c, d), t in zip(out, stamps)
          if J.parse_id(c)[1] in (J.PGN_TP_CM, J.PGN_TP_DT)]
    r = J.TpReassembler()
    got = None
    for c, d, _t in tp:
        if J.parse_id(c)[1] == J.PGN_TP_CM:
            r.feed_cm(d)
        else:
            got = r.feed_dt(d) or got
    check("dm1 bam", got == (J.PGN_DM1, J.dm_encode(
        {"mil": 1}, [(110, 0, 5), (3226, 4, 1)])) and not r.errors)
    check("bam ids", tp and tp[0][0] == 0x1CECFF00 and tp[1][0] == 0x1CEBFF00)
    gaps = [b[2] - a[2] for a, b in zip(tp, tp[1:])]
    check("bam pacing", gaps and min(gaps) >= 0.05)

    # global VIN request -> BAM; unknown PGN, global -> silence
    truck, out = make()
    run(truck, 0.0, 0.01)
    del out[:]
    truck.on_frame(0x18EAFFF9, J.request(J.PGN_VI), 0.01)
    truck.on_frame(0x18EAFFF9, J.request(0xFE00), 0.01)
    run(truck, 0.01, 0.4)
    cm = [d for c, d in out if c == 0x1CECFF00]
    check("vin bam", cm == [J.tp_bam(18, J.PGN_VI)])
    check("no nack on global", not any(J.parse_id(c)[1] == J.PGN_ACKM
                                       for c, _ in out))

    # destination-specific: single frame, NACK, then VIN over RTS/CTS
    truck, out = make()
    run(truck, 0.0, 0.01)
    del out[:]
    truck.on_frame(0x18EA00F9, J.request(J.PGN_HOURS), 0.01)
    check("hours", (0x18FEE500, J.encode_pgn(J.PGN_HOURS, J.DEFAULTS))
          in out)
    truck.on_frame(0x18EA00F9, J.request(0xFE00), 0.01)
    check("nack", (0x18E8FF00, J.ackm(J.NACK, 0xFE00, 0xF9)) in out)
    del out[:]
    truck.on_frame(0x18EA00F9, J.request(J.PGN_VI), 0.02)
    check("rts", (0x1CECF900, J.tp_rts(18, J.PGN_VI)) in out)
    truck.on_frame(0x1CEC00F9, J.tp_cts(3, 1, J.PGN_VI), 0.03)
    run(truck, 0.03, 0.1)
    dts = [d for c, d in out if c == 0x1CEBF900]
    check("dt", dts == J.tp_packets(J.vin_payload("1WCANJ1939TRUCK01")))
    truck.on_frame(0x1CEC00F9, J.tp_eoma(18, J.PGN_VI), 0.11)
    check("eoma", truck.stats["rts_ok"] == 1 and truck.conn is None)

    # no CTS: the truck aborts with reason 3 after T3
    truck, out = make()
    run(truck, 0.0, 0.01)
    truck.on_frame(0x18EA00F9, J.request(J.PGN_VI), 0.01)
    run(truck, 0.01, 1.5)
    check("rts timeout", (0x1CECF900, J.tp_abort(3, J.PGN_VI)) in out
          and truck.stats["rts_aborted"] == 1)

    # DM11 clears the active list and is acknowledged; DM2 answers
    truck, out = make(dtcs=[(110, 0, 5)], prev=[(100, 1, 2)])
    run(truck, 0.0, 0.01)
    del out[:]
    truck.on_frame(0x18EA00F9, J.request(J.PGN_DM2), 0.01)
    check("dm2", (0x18FECBF9, J.dm_encode({"mil": 1}, [(100, 1, 2)])) in out
          or (0x18FECB00, J.dm_encode({"mil": 1}, [(100, 1, 2)])) in out)
    truck.on_frame(0x18EA00F9, J.request(J.PGN_DM11), 0.02)
    check("dm11", truck.active == [] and
          (0x18E8FF00, J.ackm(J.ACK, J.PGN_DM11, 0xF9)) in out)

    # faults: a dropped packet must break the reference reassembly
    # (4 DTCs = 18 bytes = 3 packets, so dropping #2 leaves a gap)
    truck, out = make(dtcs=[(1, 1, 1), (2, 2, 2), (3, 3, 3), (4, 4, 4)],
                      fault="bam_drop:2")
    run(truck, 0.0, 1.2)
    r = J.TpReassembler()
    got = None
    for c, d in out:
        p = J.parse_id(c)[1]
        if p == J.PGN_TP_CM:
            r.feed_cm(d)
        elif p == J.PGN_TP_DT:
            got = r.feed_dt(d) or got
    check("bam_drop", got is None and r.errors)
    truck, out = make(fault="na")
    run(truck, 0.0, 0.1)
    check("na", (0x0CF00400, b"\xFF" * 8) in out)

    # contention: answer a claim for F9 with the winning NAME
    truck, out = make(contend=(0xF9, "win"))
    run(truck, 0.0, 0.01)
    truck.on_frame(0x18EEFFF9, J.name_field(5), 0.01)
    check("contend", (0x18EEFFF9, bytes(8)) in out)

    # exact-N
    truck, out = make(count=5, period_ms=10)
    run(truck, 0.0, 1.0)
    check("count", truck.sent[J.PGN_EEC1] == 5 and truck.sent[J.PGN_ET1] == 5)

    # the on-request groups without anybody asking: frames + the VIN by BAM
    truck, out = make(on_request_period=1)
    run(truck, 0.0, 1.0)
    check("asked hours", (0x18FEE500, J.encode_pgn(J.PGN_HOURS, J.DEFAULTS))
          in out)
    check("asked vd", (0x18FEE000, J.encode_pgn(J.PGN_VD, J.DEFAULTS)) in out)
    check("asked vin", (0x1CECFF00, J.tp_bam(18, J.PGN_VI)) in out)
    r = J.TpReassembler()
    got = None
    for c, d in out:
        p = J.parse_id(c)[1]
        if p == J.PGN_TP_CM:
            r.feed_cm(d)
        elif p == J.PGN_TP_DT:
            got = r.feed_dt(d) or got
    check("asked vin whole", got == (J.PGN_VI,
                                     J.vin_payload("1WCANJ1939TRUCK01")))

    if fails:
        print("J1939 TRUCK SELFTEST FAIL: " + "; ".join(fails))
        return 1
    print("J1939 TRUCK SELFTEST PASS")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("secs", nargs="?", type=float, default=120)
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--bitrate", type=int, default=250, choices=(250, 500))
    ap.add_argument("--sa", default="0x00")
    ap.add_argument("--vin", default="1WCANJ1939TRUCK01")
    ap.add_argument("--dtcs", default="", help="active: spn-fmi-oc,...")
    ap.add_argument("--prev", default="", help="previously active")
    ap.add_argument("--lamps", default="mil=1", help="mil=,rsl=,awl=,pl=")
    ap.add_argument("--set", default="", help="value overrides k=v,...")
    ap.add_argument("--period-ms", type=int, default=0,
                    help="one period for every broadcast PGN")
    ap.add_argument("--count", type=int, default=0,
                    help="stop each broadcast PGN after N frames")
    ap.add_argument("--twin", default="",
                    help="second source sending EEC1/CCVS1 (rpm +1000)")
    ap.add_argument("--contend", default="",
                    help="<sa>:win|lose: answer that node's address claim")
    ap.add_argument("--flood", type=int, default=0,
                    help="filler frames per second (PGN FF10 from 0x21)")
    ap.add_argument("--flood-prio", type=int, default=6, choices=range(8),
                    help="priority of the filler frames (0 beats everything "
                         "a tool sends: arbitration losses on its side)")
    ap.add_argument("--fault", default="", help="; ".join(FAULTS))
    ap.add_argument("--on-request-period", type=float, default=0,
                    help="send the on-request groups and the VIN (BAM) "
                         "every S seconds, as if a node kept asking")
    ap.add_argument("--ask", default="",
                    help="SA:PGN[,SA:PGN..]: request these groups from that "
                         "node every --ask-period s (SA 0xFF = everyone)")
    ap.add_argument("--ask-period", type=float, default=2.0)
    ap.add_argument("--stop-file", default="",
                    help="stop cleanly once this path exists")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    a.sa = int(a.sa, 0)
    a.twin = int(a.twin, 0) if a.twin else None
    a.dtcs = parse_dtcs(a.dtcs)
    a.prev = parse_dtcs(a.prev)
    a.lamps = {k: int(v) for k, v in
               (kv.split("=") for kv in a.lamps.split(",") if kv)}
    a.values = {k: float(v) for k, v in
                (kv.split("=") for kv in a.set.split(",") if kv)}
    unknown = [k for k in a.values if k not in J.DEFAULTS]
    if unknown:
        ap.error(f"unknown value(s) {unknown}; known: {sorted(J.DEFAULTS)}")
    if a.contend:
        sa, mode = a.contend.split(":")
        a.contend = (int(sa, 0), mode)
    else:
        a.contend = None
    a.asks = []
    for tok in [t for t in a.ask.split(",") if t.strip()]:
        sa, pgn = tok.split(":")
        a.asks.append((int(sa, 0), int(pgn, 16)))
    return run_pcan(a)


if __name__ == "__main__":
    sys.exit(main())
