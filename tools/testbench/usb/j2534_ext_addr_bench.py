#!/usr/bin/env python3
"""ISO 15765 extended addressing through the J2534 server  ->  J2534 EXT ADDR PASS

ISO 15765-2 extended addressing puts one byte, the address extension, in front of every
frame of a message, so one CAN id carries several conversations and every frame holds one
byte less. A gateway that answers for the ECUs behind it speaks this (Toyota's from 2019
on: requests on 0x750, answers on 0x758). SAE J2534-1 names it ISO15765_ADDR_TYPE: the
message is [4-byte id][address extension][payload] both ways, and the FLOW_CONTROL
filter's pattern and flow-control message carry the id and the address extension of their
direction. The device refused the flag until 2026-11-19 (meatpi-components pull request 5,
bachajon); that work sent and received it, but its receive direction never ran on
hardware. This bench is that run.

The PCAN plays the gateway the way ISO 15765-2 writes it: requests on 0x6F5, answers on
0x6FD (ids the bench simulator does not answer), two ECUs behind it. The tester's frames
to the first are led by 41 and its answers by E9, the second 42 and 5F: the byte differs
by direction, so a device that mixes the two up fails. A third ECU speaks normal
addressing on 0x6F6 / 0x6FE. Every frame the device sends is read off the bus. An ECU
answers 22 hh ll ss with 0xhhll bytes (62 hh ll ss, then a pattern seeded by ss), 31 01 hh
ll ss with the single frame 71 01 and at once a message of 0xhhll bytes, anything else
with its bytes back (the first one + 0x40).

Legs (one planned restart to configure, one to restore):
  C  configure: j2534 on with allow_lan, autopid's groups paused
  A  the filters: two FLOW_CONTROL filters with ISO15765_ADDR_TYPE on the same two ids
     are two sessions that share one subscription of the CAN node; the flag with a
     message of four bytes is ERR_INVALID_MSG
  S  single frames: the address extension leads the frame and a frame holds six bytes;
     padded and bare; TxDone and the answer carry [id][address extension] and 0x80
  M  more than one frame, both ways: a 7-byte request, its flow control taken from the
     gateway; an answer with START_OF_MESSAGE in front of it; the device's own flow
     control led by the tester's address extension and following SET_CONFIG; 4095 bytes
     each way with every byte (683 frames)
  T  the two ECUs at once: their answers come frame by frame in turn on the one id and
     both arrive whole; a frame led by an address extension nobody listens to is left
     alone; one filter stopped, the other keeps the id
  O  what pull request 5 fixed beside it: five messages in one write get five TxDone
     (the ring held three), and an ECU's single-frame acknowledgement is read before
     the START_OF_MESSAGE of the answer that follows it on the same id; both in
     extended and in normal addressing
  N  normal addressing beside it, and a filter of the other kind on the gateway's ids:
     it takes the place of BOTH extended sessions and their filter ids are gone
  W  without a filter: a single frame still goes out, more than six bytes is
     ERR_NO_FLOW_CONTROL
  L  the limits: ten sessions on one channel (eight normal ones, as a tool that opens an
     engine ECU arms 7E8 to 7EF, and two extended), the eleventh ERR_EXCEEDED_LIMIT; the
     device's twelve; what they take of the CAN node's subscriber slots and of the
     internal RAM, and that all of it comes back
  F  a busy bus beside the conversation: 3 s of other traffic at 3000 frames a second
     (about four fifths of what 500 kbit/s carries), every answer whole meanwhile, the
     CAN node's loss counters unchanged, and the tester is told of no loss
  D  the driver DLL called like an application (--dll, the 32-bit Python):
     j2534_ext_addr_dll_check.py
  H  the stacks: the server's three tasks and the provider's kept 1 KB unused
  X  restore: settings as found, only the planned restarts, no new E lines

usage (PC, PCAN wired to the DUT's bus, the tunnel of ports 80 and 6809 to the DUT):
  python j2534_ext_addr_bench.py --dut localhost:18081 --j2534 localhost:18534
       [--pcan PCAN_USBBUS1] [--dll <built wican_j2534.dll>] [--py32 <32-bit python>]
       [--keep] (leave the settings)
       [--before] (a firmware from before the change, which refuses the flag: the two
                   checks of leg O in normal addressing alone)
"""
import argparse
import collections
import json
import os
import struct
import subprocess
import sys
import threading
import time

import can

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_argv = sys.argv
sys.argv = [sys.argv[0]]
import j2534_bench as b  # noqa: E402  (its module-level argv parsing wants no args)
import j2534_auto_baud_bench as ab  # noqa: E402  (Dut)
import j2534_tp20_ps_bench as tp  # noqa: E402  (ioctl, cfg, any_msg, pattern)
import j2534_session_bench as sb  # noqa: E402  (Tester with the EVENT frames kept)
sys.argv = _argv

START_FILTER, STOP_FILTER = 0x11, 0x12
IOCTL_SET_CONFIG = 0x02
FLOW_CONTROL_FILTER = 3
CFG_BS, CFG_STMIN = 0x1E, 0x1F
TX_ADDR, RX_ADDR = 0x80, 0x80                   # ISO15765_ADDR_TYPE, in TxFlags and in RxStatus
RX_IND = b.RX_TX_MSG_TYPE | b.RX_START_OF_MESSAGE | b.RX_TX_INDICATION
TXDONE = b.RX_TX_MSG_TYPE | b.RX_TX_INDICATION
# the return codes of wire version 2 (SAE J2534-1's numbers)
ERR_INVALID_MSG, ERR_EXCEEDED_LIMIT, ERR_INVALID_FILTER_ID, ERR_NO_FLOW_CONTROL = 0x0A, 0x0C, 0x16, 0x17
GW_REQ, GW_RSP = 0x6F5, 0x6FD                   # the gateway: requests, answers
N_REQ, N_RSP = 0x6F6, 0x6FE                     # an ECU in normal addressing
L_REQ = 0x6C0                                   # leg L: eight normal ECUs 0x6C0..0x6C7, answers on the id + 8
L_GW_REQ, L_GW_RSP = 0x6D0, 0x6D8               # leg L: two extended sessions
FLOOD_ID = 0x123
MAX_PDU = 4095
LOSS_COUNTERS = tp.LOSS_COUNTERS

fails, passes = [], []
hexs = tp.hexs


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


def answers_to(req):
    """What an ECU of this bench sends back: a list of messages."""
    if len(req) >= 4 and req[0] == 0x22:
        total = max(4, min(MAX_PDU, (req[1] << 8) | req[2]))
        return [b"\x62" + req[1:4] + tp.pattern(req[3], total - 4)]
    if len(req) >= 5 and req[:2] == b"\x31\x01":
        total = max(8, min(MAX_PDU, (req[2] << 8) | req[3]))
        return [b"\x71\x01", b"\x62" + req[2:5] + tp.pattern(req[4], total - 4)]
    return [bytes([(req[0] + 0x40) & 0xFF]) + req[1:]]


class Ecu:
    """One ISO 15765-2 node on the PCAN. tx_ae None: normal addressing. Else extended: tx_ae leads the frames
    the tester sends it, rx_ae the frames it sends back."""

    def __init__(self, bus, name, req_id, rsp_id, tx_ae=None, rx_ae=None):
        self.bus, self.name, self.req_id, self.rsp_id = bus, name, req_id, rsp_id
        self.tx_ae, self.rx_ae = tx_ae, rx_ae
        self.off = 0 if tx_ae is None else 1
        self.head = b"" if rx_ae is None else bytes([rx_ae])
        self.on = True
        self.rx = None                      # a request in several frames under way: [total, bytearray]
        self.out = collections.deque()      # messages waiting to go out
        self.cur = None                     # the message going out in several frames
        self.requests = []                  # whole requests taken
        self.fcs = []                       # the device's flow controls for this ECU: the 3 bytes behind the head
        self.done = 0                       # messages sent whole

    def idle(self):
        return self.cur is None and not self.out and self.rx is None

    def frame(self, d):
        """A frame for this ECU, from its PCI byte on."""
        kind = d[0] >> 4
        if kind == 0:
            n = d[0] & 0x0F
            if 1 <= n <= 7 - self.off and len(d) >= 1 + n:
                self.take(bytes(d[1:1 + n]))
        elif kind == 1 and len(d) >= 2:
            self.rx = [((d[0] & 0x0F) << 8) | d[1], bytearray(d[2:])]
            self.bus.send(self.rsp_id, self.head + b"\x30\x00\x00", self.name)
        elif kind == 2 and self.rx is not None:
            self.rx[1] += d[1:]
            if len(self.rx[1]) >= self.rx[0]:
                req, self.rx = bytes(self.rx[1][:self.rx[0]]), None
                self.take(req)
        elif kind == 3 and len(d) >= 3:
            self.fcs.append(bytes(d[:3]))
            c = self.cur
            if c is not None and c["wait_fc"]:
                status, bs, st = d[0] & 0x0F, d[1], d[2]
                if status == 0:
                    c["wait_fc"], c["left"] = False, bs
                    c["gap"] = st / 1000.0 if st <= 0x7F else (st - 0xF0) / 10000.0 if 0xF1 <= st <= 0xF9 else 0.127
                elif status != 1:
                    self.bus.errors.append("%s: flow control %s ends its answer" % (self.name, hexs(d[:3])))
                    self.cur = None

    def take(self, req):
        self.requests.append(req)
        self.out.extend(answers_to(req))

    def step(self, now):
        """Put at most one frame on the bus. Two ECUs stepped in turn send their frames in turn."""
        if self.cur is None:
            if not self.out:
                return
            ans = self.out.popleft()
            if len(ans) <= 7 - self.off:
                self.bus.send(self.rsp_id, self.head + bytes([len(ans)]) + ans, self.name)
                self.done += 1
                return
            first = 6 - self.off
            self.cur = {"ans": ans, "at": first, "sn": 1, "wait_fc": True, "left": 0, "gap": 0.0, "next": 0.0, "dead": now + 1.0}
            self.bus.send(self.rsp_id, self.head + bytes([0x10 | (len(ans) >> 8), len(ans) & 0xFF]) + ans[:first], self.name)
            return
        c = self.cur
        if c["wait_fc"]:
            if now > c["dead"]:
                self.bus.errors.append("%s: no flow control for an answer of %d bytes (at byte %d)" % (self.name, len(c["ans"]), c["at"]))
                self.cur = None
            return
        if now < c["next"]:
            return
        take = 7 - self.off
        if not self.bus.send(self.rsp_id, self.head + bytes([0x20 | c["sn"]]) + c["ans"][c["at"]:c["at"] + take], self.name):
            self.cur = None
            return
        c["at"], c["sn"], c["next"] = c["at"] + take, (c["sn"] + 1) & 0x0F, now + c["gap"]
        if c["at"] >= len(c["ans"]):
            self.cur = None
            self.done += 1
        elif c["left"]:
            c["left"] -= 1
            if c["left"] == 0:
                c["wait_fc"], c["dead"] = True, now + 1.0


class Bus(threading.Thread):
    """The PCAN: the ECUs, and the witness of every frame the device sends."""

    def __init__(self, channel):
        super().__init__(daemon=True)
        self.bus = can.Bus(interface="pcan", channel=channel, bitrate=500000, receive_own_messages=False)
        while self.bus.recv(timeout=0):
            pass
        self.halt = threading.Event()
        self.tx = threading.Lock()
        self.ecus = []
        self.seen = collections.defaultdict(list)   # can id -> the frames the device (or anyone else) sent, whole
        self.order = []                             # who sent each of our frames, in bus order
        self.errors = []
        self.hold = False                           # True: the ECUs take requests and answer nothing yet

    def add(self, *a, **kw):
        e = Ecu(self, *a, **kw)
        self.ecus.append(e)
        return e

    def run(self):
        while not self.halt.is_set():
            busy = not self.hold and any(e.on and (e.out or (e.cur is not None and not e.cur["wait_fc"])) for e in self.ecus)
            m = self.bus.recv(timeout=0 if busy else 0.02)
            while m is not None:
                self._take(m)
                m = self.bus.recv(timeout=0)
            now = time.perf_counter()
            for e in self.ecus:
                if e.on and not self.hold:
                    e.step(now)

    def _take(self, m):
        if m.is_error_frame or m.is_extended_id or m.is_remote_frame or not m.data:
            return
        cid, d = m.arbitration_id, bytes(m.data)
        self.seen[cid].append(d)
        for e in self.ecus:
            if not e.on or e.req_id != cid:
                continue
            if e.tx_ae is None:
                if d[0] >> 4 <= 3:
                    e.frame(d)
            elif d[0] == e.tx_ae and len(d) >= 2:
                e.frame(d[1:])

    def send(self, can_id, data, who="bench"):
        frame = can.Message(arbitration_id=can_id, is_extended_id=False, data=bytes(data).ljust(8, b"\x55"))
        end = time.perf_counter() + 1.0
        while True:
            try:
                with self.tx:
                    self.bus.send(frame, timeout=0.2)
                    self.order.append(who)
                return True
            except can.CanError as e:          # the adapter's transmit queue is full: it is sending
                if time.perf_counter() > end:
                    self.errors.append("%s could not send: %s" % (who, e))
                    return False
                time.sleep(0.001)

    def flood(self, can_id, secs, rate):
        """Frames of eight bytes with a counter, rate a second, for secs. Returns how many went out. Paced: handed
        over faster than the bus carries them they would pile up in the adapter and hold the ECUs' frames back."""
        n, t0 = 0, time.perf_counter()
        while True:
            now = time.perf_counter()
            if now - t0 >= secs:
                return n
            if n > (now - t0) * rate:
                continue
            try:
                with self.tx:
                    self.bus.send(can.Message(arbitration_id=can_id, is_extended_id=False,
                                              data=struct.pack(">I", n) + b"\xF1\x00\x0D\x00"), timeout=0.2)
                n += 1
            except can.CanError:
                time.sleep(0.0005)

    def settle(self, secs=3.0):
        """Wait until every ECU has sent what it had to send."""
        end = time.perf_counter() + secs
        while time.perf_counter() < end:
            if all(e.idle() for e in self.ecus):
                return True
            time.sleep(0.01)
        return False

    def close(self):
        self.halt.set()
        self.join(2.0)
        self.bus.shutdown()


# ---- the tester's side -----------------------------------------------------------------------------------

def iso(data, flags=b.TX_PAD):
    return tp.any_msg(b.PROT_ISO15765, bytes(data), flags)


def ext(can_id, ae, payload=b""):
    """The data of an extended-addressing message: [id][address extension][payload]."""
    return struct.pack(">I", can_id) + bytes([ae]) + bytes(payload)


def connect(t, proto=b.PROT_ISO15765):
    st, body = t.call(b.CONNECT, payload=struct.pack("<IIIII", proto, 0, 500000, 0, 0))
    return st, struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else 0


def start_filter(t, ch, mask, pattern, flow):
    """-> (status, the filter id)."""
    st, body = t.call(START_FILTER, ch, struct.pack("<I", FLOW_CONTROL_FILTER) + mask + pattern + flow)
    return st, struct.unpack_from("<I", body, 0)[0] if st == 0 and len(body) >= 4 else 0


def filter_ext(t, ch, e):
    return start_filter(t, ch, iso(b"\xFF" * 5, TX_ADDR), iso(ext(e.rsp_id, e.rx_ae), TX_ADDR),
                        iso(ext(e.req_id, e.tx_ae), TX_ADDR | b.TX_PAD))


def filter_normal(t, ch, req_id, rsp_id):
    return start_filter(t, ch, iso(b"\xFF" * 4), iso(struct.pack(">I", rsp_id)), iso(struct.pack(">I", req_id)))


def stop_filter(t, ch, fid):
    return t.call(STOP_FILTER, ch, struct.pack("<I", fid))[0]


def write(t, ch, e, payload, pad=True):
    """One message to an ECU, in its addressing. -> the status."""
    if e.tx_ae is None:
        m = iso(struct.pack(">I", e.req_id) + payload, b.TX_PAD if pad else 0)
    else:
        m = iso(ext(e.req_id, e.tx_ae, payload), TX_ADDR | (b.TX_PAD if pad else 0))
    return t.call(b.WRITE_MSGS, ch, struct.pack("<I", 1) + m)[0]


def from_ecu(r, ch, e):
    """Is the received entry r of ECU e on channel ch? (its id, and its address extension with 0x80)"""
    if r[1] != ch or r[3][:4] != struct.pack(">I", e.rsp_id):
        return False
    if e.rx_ae is None:
        return not r[2] & RX_ADDR
    return bool(r[2] & RX_ADDR) and len(r[3]) >= 5 and r[3][4] == e.rx_ae


def payload_of(r, e):
    return r[3][4 + e.off:]


def read_msg(t, ch, e, secs=3.0):
    return t.wait_rx(lambda r: from_ecu(r, ch, e) and not r[2] & RX_IND, secs)


def read_som(t, ch, e, secs=3.0):
    return t.wait_rx(lambda r: from_ecu(r, ch, e) and r[2] & RX_IND == b.RX_START_OF_MESSAGE, secs)


def read_txdone(t, ch, e, secs=2.0):
    """The TxDone of a write to e: [request id] and, extended, [the tester's address extension] with 0x80."""
    want = struct.pack(">I", e.req_id) + (b"" if e.tx_ae is None else bytes([e.tx_ae]))
    return t.wait_rx(lambda r: r[1] == ch and r[2] & RX_IND == TXDONE and r[3] == want
                     and bool(r[2] & RX_ADDR) == (e.tx_ae is not None), secs)


def drain(t):
    with t.cv:
        del t.rx[:]


def ask(t, bus, ch, e, req, secs=6.0):
    """A request written and its answer read. -> (the write's status, the answer whole and right, the entry)."""
    drain(t)
    st = write(t, ch, e, req)
    r = read_msg(t, ch, e, secs)
    bus.settle()
    return st, r is not None and payload_of(r, e) == answers_to(req)[-1], r


def provider(dut):
    return ((dut.get("/api/uds") or {}).get("provider_stats") or {})


def subscribers(dut):
    """-> (the names of the CAN node's queue subscribers, the table's size)."""
    c = dut.get("/api/can") or {}
    return [s.get("name") for s in c.get("subscribers") or []], c.get("subscribers_max")


def internal_free(dut):
    return (((dut.get("/api/status") or {}).get("memory") or {}).get("internal") or {}).get("free")


# ---- the legs --------------------------------------------------------------------------------------------

def leg_a(t, bus, dut, e1, e2):
    print("== A: two extended-addressing filters on one pair of ids", flush=True)
    p0, (s0, _cap) = provider(dut), subscribers(dut)
    st, ch = connect(t)
    st1, f1 = filter_ext(t, ch, e1)
    check("A1 CONNECT(ISO15765) and a FLOW_CONTROL filter with ISO15765_ADDR_TYPE: pattern %03X + %02X, flow control %03X + %02X"
          % (e1.rsp_id, e1.rx_ae, e1.req_id, e1.tx_ae), st == 0 and ch >= 1 and st1 == 0 and f1 >= 1, (hex(st), ch, hex(st1), f1))
    st2, f2 = filter_ext(t, ch, e2)
    check("A2 a second one on the same two ids with other address extensions (%02X / %02X) is its own filter" % (e2.rx_ae, e2.tx_ae),
          st2 == 0 and f2 not in (0, f1), (hex(st2), f2))
    p1, (s1, cap) = provider(dut), subscribers(dut)
    check("A3 two ISO-TP sessions opened, and they share ONE subscription of the CAN node (the channel's queue is the other)",
          (p1.get("sessions_open") or 0) - (p0.get("sessions_open") or 0) == 2 and s1.count("isotp") - s0.count("isotp") == 1
          and len(s1) - len(s0) == 2, {"sessions_open": (p0.get("sessions_open"), p1.get("sessions_open")), "subscribers": s1, "of": cap})
    bad4 = start_filter(t, ch, iso(b"\xFF" * 4, TX_ADDR), iso(struct.pack(">I", e1.rsp_id), TX_ADDR), iso(ext(e1.req_id, 0x43), TX_ADDR))[0]
    bad5 = start_filter(t, ch, iso(b"\xFF" * 5, TX_ADDR), iso(ext(e1.rsp_id, 0x44), TX_ADDR), iso(struct.pack(">I", e1.req_id), TX_ADDR))[0]
    check("A4 the flag with a pattern or a flow-control message of four bytes is ERR_INVALID_MSG",
          bad4 == ERR_INVALID_MSG and bad5 == ERR_INVALID_MSG, (hex(bad4), hex(bad5)))
    return ch, f1, f2


def leg_s(t, bus, ch, e1):
    print("== S: single frames", flush=True)
    drain(t)
    n0 = len(bus.seen[GW_REQ])
    st = write(t, ch, e1, b"\x10\x03")
    done, r = read_txdone(t, ch, e1), read_msg(t, ch, e1)
    bus.settle()
    sent = bus.seen[GW_REQ][n0:]
    check("S1 10 03 to %02X is ONE frame led by the address extension, padded: 41 02 10 03 AA AA AA AA" % e1.tx_ae,
          st == 0 and sent == [bytes([e1.tx_ae, 0x02, 0x10, 0x03]) + b"\xAA" * 4], (hex(st), [hexs(f) for f in sent]))
    check("S2 its TxDone: RxStatus 0x89 (TX_MSG_TYPE, TX_INDICATION, ISO15765_ADDR_TYPE), data = the id and the address extension",
          done is not None and done[2] == 0x89 and done[3] == ext(e1.req_id, e1.tx_ae), None if done is None else (hex(done[2]), hexs(done[3])))
    check("S3 the answer: RxStatus 0x80, data = %03X, %02X, 50 03" % (e1.rsp_id, e1.rx_ae),
          r is not None and r[2] == RX_ADDR and r[3] == ext(e1.rsp_id, e1.rx_ae, b"\x50\x03"), None if r is None else (hex(r[2]), hexs(r[3])))
    drain(t)
    n0 = len(bus.seen[GW_REQ])
    st = write(t, ch, e1, b"\x10\x03", pad=False)
    r = read_msg(t, ch, e1)
    sent = bus.seen[GW_REQ][n0:]
    check("S4 without ISO15765_FRAME_PAD the frame is four bytes: 41 02 10 03, and it is answered",
          st == 0 and sent == [bytes([e1.tx_ae, 0x02, 0x10, 0x03])] and r is not None, (hex(st), [hexs(f) for f in sent]))
    drain(t)
    n0 = len(bus.seen[GW_REQ])
    six = b"\x3E\x01\x02\x03\x04\x05"
    st = write(t, ch, e1, six)
    r = read_msg(t, ch, e1)
    sent = bus.seen[GW_REQ][n0:]
    check("S5 six bytes are the most one frame holds behind an address extension: 41 06 and the six",
          st == 0 and sent == [bytes([e1.tx_ae, 0x06]) + six] and r is not None and payload_of(r, e1) == b"\x7E" + six[1:],
          (hex(st), [hexs(f) for f in sent]))


def leg_m(t, bus, ch, e1):
    print("== M: more than one frame, both ways", flush=True)
    drain(t)
    n0, f0 = len(bus.seen[GW_REQ]), len(e1.fcs)
    seven = b"\x3E" + bytes(range(0x11, 0x17))
    st = write(t, ch, e1, seven)
    som, r = read_som(t, ch, e1), read_msg(t, ch, e1)
    bus.settle()
    sent, a = bus.seen[GW_REQ][n0:], bytes([e1.tx_ae])
    want = [a + b"\x10\x07" + seven[:5], a + b"\x21" + seven[5:] + b"\xAA" * 4, a + b"\x30\x00\x00" + b"\xAA" * 4]
    check("M1 seven bytes take two frames: a first frame with five of them, then, after the gateway's flow control, a consecutive frame "
          "with two; the device's own flow control for the answer follows",
          st == 0 and sent == want and e1.requests[-1:] == [seven], (hex(st), [hexs(f) for f in sent]))
    check("M2 the answer (seven bytes back: two frames from the gateway) drew ONE flow control, led by the tester's address extension: "
          "41 30 00 00", e1.fcs[f0:] == [b"\x30\x00\x00"], [hexs(f) for f in e1.fcs[f0:]])
    check("M3 START_OF_MESSAGE first (RxStatus 0x82, the id and %02X), then the message whole" % e1.rx_ae,
          som is not None and som[2] == 0x82 and som[3] == ext(e1.rsp_id, e1.rx_ae) and r is not None and som[0] < r[0]
          and payload_of(r, e1) == b"\x7E" + seven[1:], (None if som is None else (hex(som[2]), hexs(som[3])), None if r is None else hexs(r[3])))
    st = tp.ioctl(t, ch, IOCTL_SET_CONFIG, tp.cfg([(CFG_BS, 4), (CFG_STMIN, 2)]))[0]
    f0 = len(e1.fcs)
    st2, whole, _r = ask(t, bus, ch, e1, b"\x22\x00\x64\x05")
    check("M4 SET_CONFIG ISO15765_BS 4 / ISO15765_STMIN 2 reaches an extended session: a 100-byte answer (16 consecutive frames of six "
          "bytes) whole, 30 04 02 four times", st == 0 and st2 == 0 and whole and e1.fcs[f0:] == [b"\x30\x04\x02"] * 4,
          (hex(st), hex(st2), [hexs(f) for f in e1.fcs[f0:]][:6]))
    tp.ioctl(t, ch, IOCTL_SET_CONFIG, tp.cfg([(CFG_BS, 0), (CFG_STMIN, 0)]))
    big = b"\x22" + struct.pack(">H", MAX_PDU) + b"\x07" + tp.pattern(0x51, MAX_PDU - 4)
    n0, f0 = len(bus.seen[GW_REQ]), len(e1.fcs)
    st, whole, _r = ask(t, bus, ch, e1, big, 15.0)
    sent = bus.seen[GW_REQ][n0:]
    data_frames = [f for f in sent if len(f) >= 2 and f[1] >> 4 in (1, 2)]
    check("M5 %d bytes each way: the request in 683 frames, every one led by %02X, taken whole by the ECU; the answer whole, every byte; "
          "one flow control for it" % (MAX_PDU, e1.tx_ae),
          st == 0 and whole and e1.requests[-1:] == [big] and len(data_frames) == 683 and all(f[0] == e1.tx_ae for f in sent)
          and e1.fcs[f0:] == [b"\x30\x00\x00"],
          {"write": hex(st), "answer": whole, "request_frames": len(data_frames), "flow_controls": len(e1.fcs) - f0})


def leg_t(t, bus, ch, e1, e2, f1):
    print("== T: two ECUs behind the gateway at once", flush=True)
    drain(t)
    o0, fa, fb = len(bus.order), len(e1.fcs), len(e2.fcs)
    reqs = (b"\x22\x01\x2C\x21", b"\x22\x01\x2C\x22")
    body = struct.pack("<I", 2) + iso(ext(e1.req_id, e1.tx_ae, reqs[0]), TX_ADDR | b.TX_PAD) \
        + iso(ext(e2.req_id, e2.tx_ae, reqs[1]), TX_ADDR | b.TX_PAD)
    q1, q2 = len(e1.requests), len(e2.requests)
    bus.hold = True                     # both requests in before either ECU answers: the answers then overlap
    st = t.call(b.WRITE_MSGS, ch, body)[0]
    end = time.perf_counter() + 2.0
    while time.perf_counter() < end and (len(e1.requests) == q1 or len(e2.requests) == q2):
        time.sleep(0.005)
    bus.hold = False
    r1, r2 = read_msg(t, ch, e1, 6.0), read_msg(t, ch, e2, 6.0)
    bus.settle()
    order = [w for w in bus.order[o0:] if w in (e1.name, e2.name)]
    turns = sum(1 for x, y in zip(order, order[1:]) if x != y)
    check("T1 two 300-byte answers at once on the one id, the gateway sending their frames in turn (%d changes of ECU in %d frames): "
          "both arrive whole, each under its own address extension" % (turns, len(order)),
          st == 0 and turns >= 40 and r1 is not None and payload_of(r1, e1) == answers_to(reqs[0])[0]
          and r2 is not None and payload_of(r2, e2) == answers_to(reqs[1])[0],
          (hex(st), None if r1 is None else len(r1[3]), None if r2 is None else len(r2[3])))
    check("T2 one flow control each, led by 41 and by 42", e1.fcs[fa:] == [b"\x30\x00\x00"] and e2.fcs[fb:] == [b"\x30\x00\x00"],
          ([hexs(f) for f in e1.fcs[fa:]], [hexs(f) for f in e2.fcs[fb:]]))
    drain(t)
    n0 = len(bus.seen[GW_REQ])
    bus.send(GW_RSP, b"\x99\x02\x50\x03")
    bus.send(GW_RSP, b"\x99\x10\x14\x62\xF1\x90\x31\x32")
    time.sleep(0.4)
    with t.cv:
        got = [(hex(r[2]), hexs(r[3])) for r in t.rx if r[1] == ch]
    check("T3 frames on the answer id led by an address extension nobody listens to (99): no flow control, nothing for the tester",
          bus.seen[GW_REQ][n0:] == [] and got == [], ([hexs(f) for f in bus.seen[GW_REQ][n0:]], got))
    st = stop_filter(t, ch, f1)
    st2, whole, _r = ask(t, bus, ch, e2, b"\x22\x00\x64\x23")
    st3 = write(t, ch, e1, bytes(range(1, 8)))
    check("T4 the first filter stopped: the second keeps the id (a 100-byte answer whole), a seven-byte message to the first is "
          "ERR_NO_FLOW_CONTROL", st == 0 and st2 == 0 and whole and st3 == ERR_NO_FLOW_CONTROL, (hex(st), hex(st2), whole, hex(st3)))
    st, f1 = filter_ext(t, ch, e1)
    st2, whole, _r = ask(t, bus, ch, e1, b"\x22\x00\x64\x24")
    check("T5 armed again, it answers again", st == 0 and st2 == 0 and whole, (hex(st), hex(st2), whole))
    return f1


def leg_o(t, bus, ch, e, tag):
    kind = "extended" if e.tx_ae is not None else "normal"
    drain(t)
    reqs = [bytes([0x3E, i]) for i in range(5)]
    head = struct.pack(">I", e.req_id) + (b"" if e.tx_ae is None else bytes([e.tx_ae]))
    flags = b.TX_PAD | (TX_ADDR if e.tx_ae is not None else 0)
    ev0 = len(t.events)
    st = t.call(b.WRITE_MSGS, ch, struct.pack("<I", 5) + b"".join(iso(head + q, flags) for q in reqs))[0]
    dones = [read_txdone(t, ch, e, 2.0) for _ in reqs]
    got = [read_msg(t, ch, e, 2.0) for _ in reqs]
    bus.settle()
    check("%s1 five single-frame messages in one write (%s): five TxDone, five answers in order, no loss told" % (tag, kind),
          st == 0 and all(d is not None for d in dones) and [None if r is None else payload_of(r, e) for r in got] ==
          [bytes([0x7E, i]) for i in range(5)] and len(t.events) == ev0,
          (hex(st), sum(1 for d in dones if d is not None), [None if r is None else hexs(payload_of(r, e)) for r in got], t.events[ev0:]))
    drain(t)
    req = b"\x31\x01\x00\x64\x31"
    st = write(t, ch, e, req)
    ack = t.wait_rx(lambda r: from_ecu(r, ch, e) and not r[2] & RX_IND and payload_of(r, e) == b"\x71\x01", 3.0)
    som, big = read_som(t, ch, e), read_msg(t, ch, e, 4.0)
    bus.settle()
    check("%s2 an acknowledgement in one frame and at once an answer of 100 bytes on the same id (%s): read as on the bus, the "
          "acknowledgement, START_OF_MESSAGE, the answer" % (tag, kind),
          st == 0 and None not in (ack, som, big) and ack[0] < som[0] < big[0] and payload_of(big, e) == answers_to(req)[1],
          (hex(st), "ack" if ack else None, "som" if som else None, None if big is None else len(big[3]),
           None if None in (ack, som, big) else ["%.1f ms" % (1000 * (x[0] - ack[0])) for x in (som, big)]))


def leg_n(t, bus, dut, ch, e1, e2, en, plain, f1, f2):
    print("== N: normal addressing beside it, and a filter of the other kind on the gateway's ids", flush=True)
    st, _fn = filter_normal(t, ch, en.req_id, en.rsp_id)
    f0 = len(en.fcs)
    st2, whole, r = ask(t, bus, ch, en, b"\x22\x00\x64\x41")
    check("N1 a normal session on %03X / %03X beside the two: a 100-byte answer whole, RxStatus without 0x80, flow control 30 00 00"
          % (en.req_id, en.rsp_id), st == 0 and st2 == 0 and whole and r[2] == 0 and en.fcs[f0:] == [b"\x30\x00\x00"],
          (hex(st), hex(st2), whole, [hexs(f) for f in en.fcs[f0:]]))
    leg_o(t, bus, ch, en, "NO")         # NO1 and NO2: the ring and the order, in normal addressing
    p0 = provider(dut)
    st, fp = filter_normal(t, ch, GW_REQ, GW_RSP)
    p1 = provider(dut)
    check("N3 a NORMAL filter on the gateway's two ids is accepted and takes the place of both extended sessions (three open, two after)",
          st == 0 and (p0.get("sessions_open"), p1.get("sessions_open")) == (3, 2), (hex(st), p0.get("sessions_open"), p1.get("sessions_open")))
    s1, s2 = stop_filter(t, ch, f1), stop_filter(t, ch, f2)
    check("N4 and their two filter ids are gone with them: stopping either is ERR_INVALID_FILTER_ID",
          s1 == ERR_INVALID_FILTER_ID and s2 == ERR_INVALID_FILTER_ID, (hex(s1), hex(s2)))
    e1.on = e2.on = False
    plain.on = True
    st, whole, r = ask(t, bus, ch, plain, b"\x22\x00\x64\x42")
    check("N5 the ids speak normal addressing now: a 100-byte answer whole", st == 0 and whole and r[2] == 0, (hex(st), whole))
    plain.on = False
    e1.on = e2.on = True
    st, f1 = filter_ext(t, ch, e1)
    s3 = stop_filter(t, ch, fp)
    st2, whole, _r = ask(t, bus, ch, e1, b"\x22\x00\x64\x43")
    check("N6 an extended filter armed on them again takes the normal one's place (its id is gone) and answers",
          st == 0 and s3 == ERR_INVALID_FILTER_ID and st2 == 0 and whole, (hex(st), hex(s3), hex(st2), whole))
    return f1


def leg_w(t, bus, ch, e1):
    print("== W: without a filter", flush=True)
    drain(t)
    n0 = len(bus.seen[GW_REQ])
    st = t.call(b.WRITE_MSGS, ch, struct.pack("<I", 1) + iso(ext(GW_REQ, 0x77, b"\x3E\x00"), TX_ADDR | b.TX_PAD))[0]
    done = t.wait_rx(lambda r: r[1] == ch and r[2] & RX_IND == TXDONE and r[3] == ext(GW_REQ, 0x77), 2.0)
    sent = bus.seen[GW_REQ][n0:]
    check("W1 a single frame to an address extension no filter names (77) still goes out: 77 02 3E 00 AA AA AA AA, with its TxDone",
          st == 0 and sent == [b"\x77\x02\x3E\x00" + b"\xAA" * 4] and done is not None and done[2] == 0x89, (hex(st), [hexs(f) for f in sent]))
    st = t.call(b.WRITE_MSGS, ch, struct.pack("<I", 1) + iso(ext(GW_REQ, 0x77, bytes(range(1, 8))), TX_ADDR | b.TX_PAD))[0]
    st2 = t.call(b.WRITE_MSGS, ch, struct.pack("<I", 1) + iso(ext(GW_REQ, e1.tx_ae), TX_ADDR | b.TX_PAD))[0]
    check("W2 seven bytes to it are ERR_NO_FLOW_CONTROL; the id and the address extension with no payload are ERR_INVALID_MSG",
          st == ERR_NO_FLOW_CONTROL and st2 == ERR_INVALID_MSG, (hex(st), hex(st2)))


def leg_l(t, bus, dut, ch):
    print("== L: the limits and what the sessions cost", flush=True)
    time.sleep(1.0)
    p0, (s0, cap), m0 = provider(dut), subscribers(dut), internal_free(dut)
    st, ch2 = connect(t)
    sts = [filter_normal(t, ch2, L_REQ + i, L_REQ + 8 + i)[0] for i in range(8)]
    sts += [start_filter(t, ch2, iso(b"\xFF" * 5, TX_ADDR), iso(ext(L_GW_RSP, ae), TX_ADDR), iso(ext(L_GW_REQ, ae), TX_ADDR))[0]
            for ae in (0xE9, 0x5F)]
    time.sleep(0.5)
    p1, (s1, _c), m1 = provider(dut), subscribers(dut), internal_free(dut)
    over = filter_normal(t, ch2, L_REQ + 0x20, L_REQ + 0x28)[0]
    check("L1 ten sessions on one channel (eight normal ones, as a tool arms 7E8 to 7EF, and two extended on one id): all accepted, "
          "the eleventh ERR_EXCEEDED_LIMIT", st == 0 and sts == [0] * 10 and over == ERR_EXCEEDED_LIMIT, ([hex(s) for s in sts], hex(over)))
    opened = (p1.get("sessions_open") or 0) - (p0.get("sessions_open") or 0)
    slots = len(s1) - len(s0)
    cost = None if None in (m0, m1) else (m0 - m1) / 10.0
    print("   the ten sessions and their channel took %d of the CAN node's %s subscriber slots (%d in use now) and %s B of internal RAM "
          "each (%s B free before, %s after)" % (slots, cap, len(s1), None if cost is None else round(cost), m0, m1), flush=True)
    check("L2 they took ten subscriber slots (nine sessions' ids and the channel's queue: the two extended ones share), at least two "
          "of the %s stay free, and a session costs under 2 KB of internal RAM" % cap,
          opened == 10 and slots == 10 and cap is not None and cap - len(s1) >= 2 and cost is not None and cost < 2048,
          {"sessions_open": opened, "slots": slots, "in_use": len(s1), "internal_per_session": cost})
    # the device's table: ch holds two sessions (an extended one and the normal one), ch2 ten
    here = p1.get("sessions_open")
    st13 = start_filter(t, ch, iso(b"\xFF" * 5, TX_ADDR), iso(ext(GW_RSP, 0x33), TX_ADDR), iso(ext(GW_REQ, 0x34), TX_ADDR))[0]
    check("L3 with twelve sessions open the device's table is full: a thirteenth is ERR_EXCEEDED_LIMIT",
          here == 12 and st13 == ERR_EXCEEDED_LIMIT, (here, hex(st13)))
    t.call(b.DISCONNECT, channel=ch2)
    time.sleep(1.0)
    p2, (s2, _c), m2 = provider(dut), subscribers(dut), internal_free(dut)
    check("L4 the channel closed: its ten sessions, its subscriber slots and the internal RAM are back",
          p2.get("sessions_open") == p0.get("sessions_open") and sorted(s2) == sorted(s0) and None not in (m0, m2) and m0 - m2 < 2048,
          {"sessions_open": (p0.get("sessions_open"), p2.get("sessions_open")), "subscribers": (len(s0), len(s2)), "internal_free": (m0, m2)})


def leg_f(t, bus, dut, ch, e1, secs=3.0, rate=3000):
    print("== F: a busy bus beside the conversation", flush=True)
    c0, j0, ev0 = dut.can(*LOSS_COUNTERS, "rx"), dut.get("/api/j2534") or {}, len(t.events)
    sent = []
    th = threading.Thread(target=lambda: sent.append(bus.flood(FLOOD_ID, secs, rate)), daemon=True)
    th.start()
    n = ok = 0
    end = time.perf_counter() + secs
    while time.perf_counter() < end:
        _s, whole, _r = ask(t, bus, ch, e1, b"\x22\x00\x64" + bytes([n & 0xFF]))
        n, ok = n + 1, ok + (1 if whole else 0)
    th.join(5.0)
    time.sleep(0.5)
    c1, j1 = dut.can(*LOSS_COUNTERS, "rx"), dut.get("/api/j2534") or {}
    flood = sent[0] if sent else 0
    events = [e for e in t.events[ev0:] if e[2] == sb.EVENT_RX_LOST]
    print("   %d frames of id %03X in %.0f s (%.0f a second) beside %d answers of 100 bytes" % (flood, FLOOD_ID, secs, flood / secs, n), flush=True)
    check("F1 every answer whole meanwhile (%d)" % n, n >= 5 and ok == n, "%d of %d" % (ok, n))
    check("F2 the CAN node lost nothing: no loss counter moved, and it received the %d frames" % flood,
          all(c1.get(k) == c0.get(k) for k in LOSS_COUNTERS) and (c1.get("rx") or 0) - (c0.get("rx") or 0) >= flood > 0.8 * secs * rate,
          {k: (c0.get(k), c1.get(k)) for k in c0 if c0.get(k) != c1.get(k)})
    check("F3 the tester was told of no loss: no EVENT RX_LOST, rx_lost on /api/j2534 unchanged",
          events == [] and j1.get("rx_lost") == j0.get("rx_lost"), (events[:4], j0.get("rx_lost"), j1.get("rx_lost")))


def leg_d(py32, jh, jp, dll, bus, e1):
    print("== D: the driver DLL called like an application", flush=True)
    if not dll:
        print("SKIP: no --dll (pass the built wican_j2534.dll)", flush=True)
        return
    if not os.path.exists(dll) or not py32 or not os.path.exists(py32):
        check("D0 the DLL and the 32-bit Python are there", False, (dll, py32))
        return
    env = dict(os.environ, WICAN_J2534_HOST=jh if jh != "localhost" else "127.0.0.1", WICAN_J2534_PORT=str(jp), WICAN_J2534_DLL=dll,
               WICAN_EXT_REQ="%03X" % e1.req_id, WICAN_EXT_RSP="%03X" % e1.rsp_id, WICAN_EXT_TX_AE="%02X" % e1.tx_ae,
               WICAN_EXT_RX_AE="%02X" % e1.rx_ae)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "j2534_ext_addr_dll_check.py")
    e0, n0 = len(bus.errors), len(e1.requests)
    try:
        p = subprocess.run([py32, "-I", script], env=env, capture_output=True, text=True, timeout=90)
        out = p.stdout + p.stderr
    except Exception as e:
        out, p = "%s: %s" % (type(e).__name__, e), None
    for ln in out.rstrip().splitlines():
        print("   " + ln, flush=True)
    check("D1 every call through the DLL did what the standard says (%d steps)" % out.count("OK: "),
          p is not None and p.returncode == 0 and "DLL CHECK DONE" in out and "BAD: " not in out, "exit %s" % (p.returncode if p else "none"))
    bus.settle()
    check("D2 the ECU took the application's three requests and ended every answer", len(e1.requests) - n0 == 3 and bus.errors[e0:] == [],
          (len(e1.requests) - n0, bus.errors[e0:][:4]))


def leg_h(dut):
    print("== H: the stacks of the tasks on the path", flush=True)
    names = ("j2534", "j2534_rx", "j2534_per", "isotp")
    tasks = {x.get("name"): x.get("stack_hw") for x in ((dut.get("/api/status/tasks") or {}).get("tasks") or [])}
    got = {n: tasks.get(n) for n in names}
    check("H1 the J2534 server's three tasks and the ISO-TP provider's kept at least 1 KB of their stacks unused through the run",
          all(v is not None and v >= 1024 for v in got.values()), got)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--j2534", default="localhost:18534")
    ap.add_argument("--pcan", default=os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"))
    ap.add_argument("--py32", default=r"C:\Users\Ali\idf_tmp\py32\python.exe")
    ap.add_argument("--dll", default=None, help="the built wican_j2534.dll for leg D (the DLL called like an application)")
    ap.add_argument("--keep", action="store_true", help="leave the settings as the run set them (a second run follows)")
    ap.add_argument("--before", action="store_true", help="a firmware from before 2026-11-19, which refuses the flag: the two checks of "
                    "leg O in normal addressing alone (five TxDone for five messages, the order of the bus)")
    a = ap.parse_args()

    dut = ab.Dut(a.dut)
    jh, jp = a.j2534.split(":")[0], int(a.j2534.split(":")[1])
    t0 = time.time()
    planned = 0

    st = dut.get("/api/status")
    if not st:
        print("J2534 EXT ADDR FAIL: no DUT at", a.dut)
        sys.exit(1)
    boot0, unexp0 = st.get("boot_count"), st.get("unexpected_resets")
    err0 = (st.get("health") or {}).get("log_errors")
    print("dut: %s boot_count=%s unexpected=%s log_errors=%s" % (st.get("version"), boot0, unexp0, err0), flush=True)
    found = {c: dut.settings(c) for c in ("j2534_server",)}
    groups = (dut.get("/api/autopid") or {}).get("groups", [])
    group_on = [g["name"] for g in groups if g.get("enabled")]
    try:
        bus = Bus(a.pcan)
    except Exception as e:
        print("J2534 EXT ADDR FAIL: PCAN %s not usable: %s" % (a.pcan, e))
        sys.exit(1)
    e1 = bus.add("ecu1", GW_REQ, GW_RSP, 0x41, 0xE9)
    e2 = bus.add("ecu2", GW_REQ, GW_RSP, 0x42, 0x5F)
    en = bus.add("normal", N_REQ, N_RSP)
    plain = bus.add("plain", GW_REQ, GW_RSP)     # the gateway's ids in normal addressing: leg N alone
    plain.on = False
    bus.start()

    def submit(objs, why):
        nonlocal planned
        for comp, obj in objs.items():
            dut.api("/api/settings/" + comp, "PUT", obj)
        r = dut.api("/api/settings/submit", "POST")
        if r.get("reboot"):
            planned += 1
            print("restart %d (%s)..." % (planned, why), flush=True)
            time.sleep(6)
            if not dut.wait_up():
                print("J2534 EXT ADDR FAIL: the DUT did not come back (%s)" % why)
                sys.exit(1)
            time.sleep(2)

    def pause_autopid(on):
        for g in group_on:
            try:
                dut.api("/api/autopid/group", "POST", {"name": g, "enabled": on})
            except Exception:
                pass

    print("== C: configure", flush=True)
    j = json.loads(json.dumps(found["j2534_server"]))
    j["enabled"] = True
    j["allow_lan"] = True
    submit({"j2534_server": j}, "the configuration")
    pause_autopid(False)
    time.sleep(1.5)

    t = None
    try:
        t = sb.Tester(jh if jh != "localhost" else "127.0.0.1", jp)
        hst, ver = t.hello(2)
        ost = t.call(b.OPEN)[0]
        if hst != 0 or ver != 2 or ost != 0:
            raise RuntimeError("no session at wire version 2: HELLO 0x%X (version %s), OPEN 0x%X" % (hst, ver, ost))
        if a.before:
            print("== O (--before): every TxDone of a write, and the order of the bus, in normal addressing alone", flush=True)
            _st, ch = connect(t)
            filter_normal(t, ch, en.req_id, en.rsp_id)
            leg_o(t, bus, ch, en, "NO")
            raise StopIteration
        ch, f1, f2 = leg_a(t, bus, dut, e1, e2)
        leg_s(t, bus, ch, e1)
        leg_m(t, bus, ch, e1)
        f1 = leg_t(t, bus, ch, e1, e2, f1)
        print("== O: every TxDone of a write, and the order of the bus", flush=True)
        leg_o(t, bus, ch, e1, "O")
        f1 = leg_n(t, bus, dut, ch, e1, e2, en, plain, f1, f2)
        leg_w(t, bus, ch, e1)
        leg_l(t, bus, dut, ch)
        leg_f(t, bus, dut, ch, e1)
        t.call(b.DISCONNECT, channel=ch)
        t.call(b.CLOSE)
    except StopIteration:
        pass                                # --before: the one leg has run
    except Exception as e:
        check("the legs ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        if t is not None:
            t.close()
        time.sleep(1.0)
    if not a.before:
        leg_d(a.py32, jh, jp, a.dll, bus, e1)
        leg_h(dut)
    check("the ECUs ended every answer they began", not bus.errors, bus.errors[:4])

    print("== X: restore", flush=True)
    if a.keep:
        print("--keep: the settings stay as the run set them", flush=True)
    else:
        submit(found, "the settings as found")
        back = {k: dut.settings(k) for k in found}
        check("X1 settings back as found", back == found, {k: "same" if back[k] == found[k] else "DIFFERS" for k in found})
    pause_autopid(True)
    st = dut.get("/api/status") or {}
    check("X2 only the planned restarts (%d)" % planned, st.get("boot_count") == boot0 + planned and st.get("unexpected_resets") == unexp0,
          {"boot_count": (boot0, st.get("boot_count")), "unexpected": (unexp0, st.get("unexpected_resets"))})
    err1 = (st.get("health") or {}).get("log_errors")
    check("X3 no new E lines", err1 == err0 or err1 == 0, (err0, err1))
    bus.close()

    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t0), flush=True)
    if fails:
        print("J2534 EXT ADDR FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("J2534 EXT ADDR PASS")


if __name__ == "__main__":
    main()
