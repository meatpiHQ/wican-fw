#!/usr/bin/env python3
"""ISO 15765 flow control as a J2534 tool sets it  ->  J2534 FLOW CONTROL PASS

SAE J2534-1 gives an ISO15765 channel two receive parameters, ISO15765_BS (0x1E) and
ISO15765_STMIN (0x1F): the block size and the separation time the interface writes into
the flow control it sends when an ECU answers with more than one frame. Until 2026-11-17
the device sent BS 8 / STmin 1 ms whatever the tool set. It opens a channel with the
standard's 0 and 0 now, takes what the tool sets and answers GET_CONFIG with the values
in force. This bench plays the ECU on the PCAN the way
ISO 15765-2 writes it down (a first frame, then only the consecutive frames the flow
control allows, at the pace it asks for), reads every flow control off the bus and drives
the device over the wire protocol the Windows DLL speaks. The ECUs: request ids 0x6F2, 0x6F3, 0x6F4, answers on the id + 8 (ids the
bench simulator does not answer). A request 22 hh ll ss is answered by 0xhhll bytes:
62 hh ll ss, then a pattern seeded by ss.

Legs (one planned restart to configure, one to restore):
  C  configure: j2534 on with allow_lan, autopid's groups paused
  A  a channel as it opens: GET_CONFIG reads ISO15765_BS 0 and ISO15765_STMIN 0 (SAE
     J2534-1's defaults), a 20-byte answer draws one flow control, 30 00 00
  B  SET_CONFIG on a session that is open: BS 4 / STMIN 2 read back, a 100-byte answer
     draws 30 04 02 after the first frame and after every four consecutive frames
  T  the two values one by one (the other kept), STMIN in microseconds (0xF3 = 300 us),
     the largest values (BS 0xFF, STMIN 0x7F)
  S  the scope: a filter opened after SET_CONFIG has the channel's values, a second
     channel has its own, a channel opened again starts at 0 / 0
  Z  BS 0 means the rest of the message: 4095 bytes (585 consecutive frames, more than
     the 255 a block counter holds) draw exactly one flow control, every byte arrives
  R  what is refused and what is let through: BS 0x100 and a reserved STMIN (0x80,
     0xFA) are ERR_INVALID_IOCTL_VALUE and change nothing, a good pair in front of a
     refused one included; a body that is not a list (a count without its pairs, the
     bytes a driver build that was never released sent) is refused; DATA_RATE and
     LOOPBACK beside the two (the list udsoncan sends) are acknowledged; SET_CONFIG
     and GET_CONFIG without a body (drivers up to 1.8) stay NOERROR and change
     nothing; a raw CAN channel acknowledges the pair and answers none
  N  the line at full rate, counted: --big answers of 4095 bytes with BS 0 / STMIN 0,
     the ECU sending without a pause; every byte compared, one flow control each, the
     CAN node's loss counters unchanged, the ISO-TP provider fed exactly the frames
     the ECU sent and dropped none; two answers with BS 8 / STMIN 1 first, for the time.
     This is the run with the unit's flash at rest, and it says so when it was not: the
     configuration backup is written about 25 s after a restart that changed a setting
     (the stage's own), so the leg waits that write out. With the CAN node's receive
     queue at 64 frames that write cost 28 and 30 frames and one answer (2026-11-17)
  W  the same rate while the unit writes its flash: three files of 32 KB uploaded to
     /data while the answers run (the flash counters must have moved, else the leg
     tested nothing); every answer whole, no loss counter moved, the provider fed
     every frame. An erase stops every task for about 23 ms and leaves the receive
     interrupt alone, so the frames of that time wait in the CAN node's receive queue
     (256 frames since 2026-11-17) and then arrive at once in the queues behind it
     (the channel's own, at 32 frames, dropped 201 of them that day)
  U  the device's own UDS client (POST /api/uds/request) is not the tool's channel: it
     keeps BS 8 / STmin 1 ms
  D  the driver DLL called like an application (--dll, the 32-bit Python):
     j2534_flow_control_dll_check.py sets and reads the two values with SCONFIG_LISTs;
     the ECU sees the flow controls it set
  X  restore: settings as found, only the planned restarts, no new E lines

usage (PC, PCAN wired to the DUT's bus, the tunnel of ports 80 and 6809 to the DUT):
  python j2534_flow_control_bench.py --dut localhost:18081 --j2534 localhost:18534
       [--pcan PCAN_USBBUS1] [--dll <built wican_j2534.dll>] [--py32 <32-bit python>]
       [--big 100] (answers of 4095 bytes in leg N) [--keep] (leave the settings)
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import threading
import time
import urllib.request

import can

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_argv = sys.argv
sys.argv = [sys.argv[0]]
import j2534_bench as b  # noqa: E402  (its module-level argv parsing wants no args)
import j2534_auto_baud_bench as ab  # noqa: E402  (Dut)
import j2534_tp20_ps_bench as tp  # noqa: E402  (Tester, ioctl, cfg, any_msg, pattern)
sys.argv = _argv

START_FILTER, IOCTL_GET_CONFIG, IOCTL_SET_CONFIG = 0x11, 0x01, 0x02
FLOW_CONTROL_FILTER = 3
CFG_DATA_RATE, CFG_LOOPBACK, CFG_BS, CFG_STMIN = 0x01, 0x03, 0x1E, 0x1F
ERR_INVALID_IOCTL_VALUE = 0x05
RX_NOT_A_MESSAGE = b.RX_TX_MSG_TYPE | b.RX_START_OF_MESSAGE | b.RX_TX_INDICATION
ECU1, ECU2, ECU3 = 0x6F2, 0x6F3, 0x6F4          # request ids; an ECU answers on the id + 8
MAX_PDU = 4095                                  # the largest message ISO 15765-2 carries with a 12-bit length
OPENS_WITH = (0, 0)                             # ISO15765_BS, ISO15765_STMIN of a channel nobody has set
FC_OPENS_WITH = bytes([0x30, OPENS_WITH[0], OPENS_WITH[1]])
LOSS_COUNTERS = tp.LOSS_COUNTERS
FLASH_PROBE = "/data/j2534fc_flash_probe.bin"   # leg W writes it and takes it away again

fails, passes = [], []
hexs = tp.hexs


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


def answer_to(req):
    """What an ECU of this bench answers to a request."""
    if len(req) >= 4 and req[0] == 0x22:
        total = max(4, min(MAX_PDU, (req[1] << 8) | req[2]))
        return b"\x62" + req[1:4] + tp.pattern(req[3], total - 4)
    return bytes([(req[0] + 0x40) & 0xFF]) + req[1:]


class Ecus(threading.Thread):
    """The PCAN as ECUs that follow ISO 15765-2 when they answer. Every flow control is kept."""

    def __init__(self, channel, req_ids):
        super().__init__(daemon=True)
        self.bus = can.Bus(interface="pcan", channel=channel, bitrate=500000, receive_own_messages=False)
        while self.bus.recv(timeout=0):
            pass
        self.req_ids = set(req_ids)
        self.halt = threading.Event()
        self.fcs = []           # (t, request id, the 3 bytes): every flow control seen
        self.sent = {}          # answer id -> frames put on the bus
        self.pdus = []          # (answer id, bytes, seconds from the first frame to the last one handed to the PCAN)
        self.errors = []

    def run(self):
        while not self.halt.is_set():
            fr = self._recv(0.05)
            if fr is not None:
                self._handle(*fr)

    def close(self):
        self.halt.set()
        self.join(2.0)
        self.bus.shutdown()

    def flow_controls(self, since=0):
        return [d for _t, _i, d in self.fcs[since:]]

    def _recv(self, timeout):
        m = self.bus.recv(timeout=timeout)
        if m is None or m.is_error_frame or m.is_extended_id or m.is_remote_frame or not m.data:
            return None
        cid, d = m.arbitration_id, bytes(m.data)
        if cid in self.req_ids and d[0] >> 4 == 3:
            self.fcs.append((m.timestamp, cid, d[:3]))
        return cid, d

    def _send(self, can_id, data):
        frame = can.Message(arbitration_id=can_id, is_extended_id=False, data=bytes(data).ljust(8, b"\x55"))
        end = time.perf_counter() + 1.0
        while True:
            try:
                self.bus.send(frame, timeout=0.2)
                self.sent[can_id] = self.sent.get(can_id, 0) + 1
                return True
            except can.CanError as e:          # the adapter's transmit queue is full: it is sending
                if time.perf_counter() > end:
                    self.errors.append("an ECU could not send: %s" % e)
                    return False
                time.sleep(0.001)

    def _handle(self, cid, d):
        if cid not in self.req_ids:
            return
        kind = d[0] >> 4
        if kind == 0 and 1 <= (d[0] & 0x0F) <= 7:
            self._answer(cid, d[1:1 + (d[0] & 0x0F)])
        elif kind == 1:
            # a request in several frames: take it the way a fast ECU does (BS 0, no gap)
            total, buf = ((d[0] & 0x0F) << 8) | d[1], bytearray(d[2:8])
            self._send(cid + 8, b"\x30\x00\x00")
            end = time.perf_counter() + 2.0
            while len(buf) < total and time.perf_counter() < end:
                fr = self._recv(0.1)
                if fr is not None and fr[0] == cid and fr[1][0] >> 4 == 2:
                    buf += fr[1][1:8]
            if len(buf) >= total:
                self._answer(cid, bytes(buf[:total]))
            else:
                self.errors.append("a request of %d bytes to %03X stopped at %d" % (total, cid, len(buf)))

    def _wait_fc(self, cid, timeout):
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            fr = self._recv(0.05)
            if fr is not None and fr[0] == cid and fr[1][0] >> 4 == 3:
                return fr[1]
        return None

    def _answer(self, cid, req):
        ans, rsp = answer_to(req), cid + 8
        if len(ans) <= 7:
            self._send(rsp, bytes([len(ans)]) + ans)
            return
        t0 = time.perf_counter()
        self._send(rsp, bytes([0x10 | (len(ans) >> 8), len(ans) & 0xFF]) + ans[:6])
        off, sn = 6, 1
        while off < len(ans):
            fc = self._wait_fc(cid, 1.0)
            if fc is None:
                self.errors.append("no flow control from %03X for an answer of %d bytes (at byte %d)" % (cid, len(ans), off))
                return
            status, bs, st = fc[0] & 0x0F, fc[1], fc[2]
            if status == 1:
                continue                        # wait: the next flow control decides
            if status != 0:
                self.errors.append("flow control %s from %03X ends the answer" % (hexs(fc[:3]), cid))
                return
            gap = st / 1000.0 if st <= 0x7F else (st - 0xF0) / 10000.0 if 0xF1 <= st <= 0xF9 else 0.127
            n = 0
            while off < len(ans) and (bs == 0 or n < bs):
                if gap:
                    until = time.perf_counter() + gap
                    if gap >= 0.002:
                        time.sleep(gap - 0.001)
                    while time.perf_counter() < until:
                        pass
                if not self._send(rsp, bytes([0x20 | sn]) + ans[off:off + 7]):
                    return
                off, sn, n = off + 7, (sn + 1) & 0x0F, n + 1
        self.pdus.append((rsp, len(ans), time.perf_counter() - t0))


def iso_msg(data, tx_flags=b.TX_PAD):
    return tp.any_msg(b.PROT_ISO15765, data, tx_flags)


def connect(t, proto=b.PROT_ISO15765):
    st, body = t.call(b.CONNECT, payload=struct.pack("<IIIII", proto, 0, 500000, 0, 0))
    return st, struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else 0


def fc_filter(t, ch, req_id):
    mask, patt, flow = iso_msg(b"\xFF\xFF\xFF\xFF"), iso_msg(struct.pack(">I", req_id + 8)), iso_msg(struct.pack(">I", req_id))
    return t.call(START_FILTER, ch, struct.pack("<I", FLOW_CONTROL_FILTER) + mask + patt + flow)[0]


def set_cfg(t, ch, pairs):
    return tp.ioctl(t, ch, IOCTL_SET_CONFIG, tp.cfg(pairs))[0]


def get_cfg(t, ch, params=(CFG_BS, CFG_STMIN)):
    """GET_CONFIG for the parameters -> (status, {parameter: value}) of what the device answered."""
    st, body = tp.ioctl(t, ch, IOCTL_GET_CONFIG, tp.cfg([(p, 0xEEEEEEEE) for p in params]))
    out = {}
    if st == 0 and len(body) >= 4:
        for i in range(struct.unpack_from("<I", body, 0)[0]):
            if 4 + (i + 1) * 8 > len(body):
                break
            p, v = struct.unpack_from("<II", body, 4 + i * 8)
            out[p] = v
    return st, out


def flow(cfg_now):
    return (cfg_now.get(CFG_BS), cfg_now.get(CFG_STMIN))


def ask(t, ecus, ch, req_id, total, seed, secs=6.0):
    """22 hh ll ss written to an ECU. Returns (the write's status, whether the answer came whole and right,
    the flow controls the ECU saw for it)."""
    f0 = len(ecus.fcs)
    req = b"\x22" + struct.pack(">H", total) + bytes([seed])
    t0 = time.perf_counter()
    st = t.call(b.WRITE_MSGS, ch, struct.pack("<I", 1) + iso_msg(struct.pack(">I", req_id) + req))[0]
    r = t.wait_rx(lambda x: x[1] == ch and not (x[2] & RX_NOT_A_MESSAGE) and x[3][:4] == struct.pack(">I", req_id + 8), secs)
    ask.took = time.perf_counter() - t0
    time.sleep(0.05)            # a flow control that should not be there needs a moment to show
    return st, r is not None and r[3][4:] == answer_to(req), ecus.flow_controls(f0)


def fcs_hex(lst):
    return [hexs(d) for d in lst][:6] + (["... %d in all" % len(lst)] if len(lst) > 6 else [])


def leg_a(t, ecus):
    print("== A: a channel as it opens", flush=True)
    st, ch = connect(t)
    st2 = fc_filter(t, ch, ECU1)
    check("A1 CONNECT(ISO15765) and a FLOW_CONTROL filter %03X / %03X accepted" % (ECU1, ECU1 + 8), st == 0 and ch >= 1 and st2 == 0,
          (hex(st), ch, hex(st2)))
    st, got = get_cfg(t, ch)
    check("A2 GET_CONFIG reads ISO15765_BS 0 and ISO15765_STMIN 0, SAE J2534-1's defaults", st == 0 and flow(got) == OPENS_WITH,
          (hex(st), got))
    st, whole, fcs = ask(t, ecus, ch, ECU1, 20, 1)
    check("A3 a 20-byte answer arrives whole", st == 0 and whole, hex(st))
    check("A4 the ECU saw one flow control for it: 30 00 00", fcs == [FC_OPENS_WITH], fcs_hex(fcs))
    return ch


def leg_b(t, ecus, ch):
    print("== B: SET_CONFIG on an open session", flush=True)
    st = set_cfg(t, ch, [(CFG_BS, 4), (CFG_STMIN, 2)])
    check("B1 SET_CONFIG ISO15765_BS 4, ISO15765_STMIN 2 is NOERROR", st == 0, hex(st))
    st, got = get_cfg(t, ch)
    check("B2 GET_CONFIG reads them back", st == 0 and flow(got) == (4, 2), (hex(st), got))
    st, whole, fcs = ask(t, ecus, ch, ECU1, 100, 2)
    check("B3 a 100-byte answer (14 consecutive frames) arrives whole", st == 0 and whole, hex(st))
    check("B4 30 04 02 after the first frame and after every four consecutive frames (four in all)", fcs == [b"\x30\x04\x02"] * 4,
          fcs_hex(fcs))


def leg_t(t, ecus, ch):
    print("== T: the two values one by one, and the separation time byte", flush=True)
    st = set_cfg(t, ch, [(CFG_STMIN, 5)])
    _s, whole, fcs = ask(t, ecus, ch, ECU1, 20, 3)
    check("T1 ISO15765_STMIN 5 alone: 30 04 05 (the block size kept)", st == 0 and whole and fcs == [b"\x30\x04\x05"], (hex(st), fcs_hex(fcs)))
    st = set_cfg(t, ch, [(CFG_BS, 3)])
    _s, whole, fcs = ask(t, ecus, ch, ECU1, 20, 4)
    check("T2 ISO15765_BS 3 alone: 30 03 05 (the separation time kept)", st == 0 and whole and fcs == [b"\x30\x03\x05"], (hex(st), fcs_hex(fcs)))
    st = set_cfg(t, ch, [(CFG_STMIN, 0xF3)])
    _s2, got = get_cfg(t, ch)
    _s, whole, fcs = ask(t, ecus, ch, ECU1, 20, 5)
    check("T3 ISO15765_STMIN 0xF3 (300 us): 30 03 F3, and GET_CONFIG reads 0xF3", st == 0 and whole and fcs == [b"\x30\x03\xF3"]
          and flow(got) == (3, 0xF3), (hex(st), fcs_hex(fcs), got))
    st = set_cfg(t, ch, [(CFG_BS, 0xFF), (CFG_STMIN, 0x7F)])
    _s, whole, fcs = ask(t, ecus, ch, ECU1, 20, 6)
    check("T4 the largest values, BS 0xFF and STMIN 0x7F (127 ms): 30 FF 7F", st == 0 and whole and fcs == [b"\x30\xFF\x7F"],
          (hex(st), fcs_hex(fcs)))


def leg_s(t, ecus, ch):
    print("== S: the scope of the two values", flush=True)
    st = set_cfg(t, ch, [(CFG_BS, 2), (CFG_STMIN, 0)])
    st2 = fc_filter(t, ch, ECU2)
    _s, whole, fcs = ask(t, ecus, ch, ECU2, 30, 7)
    check("S1 a filter opened after SET_CONFIG has the channel's values: 30 02 00 for a second ECU, twice for 4 consecutive frames",
          st == 0 and st2 == 0 and whole and fcs == [b"\x30\x02\x00"] * 2, (hex(st), hex(st2), fcs_hex(fcs)))
    st, ch2 = connect(t)
    st2 = fc_filter(t, ch2, ECU3)
    _s3, got2 = get_cfg(t, ch2)
    _s, whole, fcs = ask(t, ecus, ch2, ECU3, 30, 8)
    check("S2 a second channel has its own: GET_CONFIG 0 / 0 and 30 00 00", st == 0 and ch2 not in (0, ch) and st2 == 0 and whole
          and flow(got2) == OPENS_WITH and fcs == [FC_OPENS_WITH], (hex(st), ch2, got2, fcs_hex(fcs)))
    st = set_cfg(t, ch2, [(CFG_BS, 5)])
    _s3, got = get_cfg(t, ch)
    _s, whole, fcs = ask(t, ecus, ch, ECU1, 30, 9)
    check("S3 a value set on the second channel leaves the first alone: still 2 / 0", st == 0 and flow(got) == (2, 0) and whole
          and fcs == [b"\x30\x02\x00"] * 2, (hex(st), got, fcs_hex(fcs)))
    t.call(b.DISCONNECT, channel=ch2)
    st, ch3 = connect(t)
    _s3, got = get_cfg(t, ch3)
    check("S4 a channel opened again starts at 0 / 0", st == 0 and flow(got) == OPENS_WITH, (hex(st), ch3, got))
    t.call(b.DISCONNECT, channel=ch3)


def leg_z(t, ecus, ch):
    print("== Z: BS 0 means the rest of the message", flush=True)
    st = set_cfg(t, ch, [(CFG_BS, 0), (CFG_STMIN, 0)])
    _s, whole, fcs = ask(t, ecus, ch, ECU1, MAX_PDU, 10, 10.0)
    check("Z1 a %d-byte answer (585 consecutive frames) arrives whole, every byte" % MAX_PDU, st == 0 and whole, hex(st))
    check("Z2 exactly one flow control for it: 30 00 00", fcs == [b"\x30\x00\x00"], fcs_hex(fcs))


def leg_r(t, ecus, ch):
    print("== R: what is refused and what is let through", flush=True)
    set_cfg(t, ch, [(CFG_BS, 8), (CFG_STMIN, 1)])
    st = set_cfg(t, ch, [(CFG_BS, 0x100)])
    check("R1 ISO15765_BS 0x100 is ERR_INVALID_IOCTL_VALUE", st == ERR_INVALID_IOCTL_VALUE, hex(st))
    st, st2 = set_cfg(t, ch, [(CFG_STMIN, 0x80)]), set_cfg(t, ch, [(CFG_STMIN, 0xFA)])
    check("R2 a reserved ISO15765_STMIN (0x80, 0xFA) is ERR_INVALID_IOCTL_VALUE", st == ERR_INVALID_IOCTL_VALUE and st2 == ERR_INVALID_IOCTL_VALUE,
          (hex(st), hex(st2)))
    st = set_cfg(t, ch, [(CFG_BS, 4), (CFG_STMIN, 0x80)])
    _s, got = get_cfg(t, ch)
    check("R3 a good pair in front of a refused one changes nothing: still 8 / 1", st == ERR_INVALID_IOCTL_VALUE and flow(got) == (8, 1),
          (hex(st), got))
    one_pair = struct.pack("<III", 2, CFG_BS, 0)
    st = tp.ioctl(t, ch, IOCTL_SET_CONFIG, one_pair)[0]
    old = struct.pack("<II", IOCTL_SET_CONFIG, 2) + struct.pack("<IIII", CFG_BS, 0, CFG_STMIN, 0)     # id, count, pairs
    st2 = t.call(tp.IOCTL, ch, old)[0]
    _s, got = get_cfg(t, ch)
    check("R4 a body that is not a list (a count of 2 with one pair; id, count, pairs without the length) is refused, nothing changes",
          st == ERR_INVALID_IOCTL_VALUE and st2 == ERR_INVALID_IOCTL_VALUE and flow(got) == (8, 1), (hex(st), hex(st2), got))
    st = set_cfg(t, ch, [(CFG_DATA_RATE, 500000), (CFG_LOOPBACK, 0), (CFG_BS, 0x20), (CFG_STMIN, 0)])
    _s, got = get_cfg(t, ch)
    check("R5 the list udsoncan sends (DATA_RATE, LOOPBACK, BS 0x20, STMIN 0) is NOERROR and sets 0x20 / 0", st == 0 and flow(got) == (0x20, 0),
          (hex(st), got))
    st = t.call(tp.IOCTL, ch, struct.pack("<I", IOCTL_SET_CONFIG))[0]
    st2, body = t.call(tp.IOCTL, ch, struct.pack("<I", IOCTL_GET_CONFIG))
    st3 = t.call(tp.IOCTL, ch, struct.pack("<II", IOCTL_SET_CONFIG, 0))[0]
    _s, got = get_cfg(t, ch)
    check("R6 SET_CONFIG and GET_CONFIG as their id alone (drivers up to 1.8) are NOERROR, answer nothing and change nothing",
          st == 0 and st2 == 0 and body == b"" and st3 == 0 and flow(got) == (0x20, 0), (hex(st), hex(st2), hexs(body), hex(st3), got))
    _s, whole, fcs = ask(t, ecus, ch, ECU1, 20, 11)
    check("R7 the next flow control is the last good set: 30 20 00", whole and fcs == [b"\x30\x20\x00"], fcs_hex(fcs))
    st, chc = connect(t, b.PROT_CAN)
    st2 = set_cfg(t, chc, [(CFG_BS, 4), (CFG_STMIN, 2)])
    st3, got = get_cfg(t, chc)
    check("R8 a raw CAN channel acknowledges the pair and answers neither", st == 0 and st2 == 0 and st3 == 0 and got == {},
          (hex(st), hex(st2), hex(st3), got))
    t.call(b.DISCONNECT, channel=chc)


def flash_ops(dut):
    """The unit's flash counters since boot and its uptime in seconds."""
    st = dut.get("/api/status") or {}
    up = 0
    for part in str(st.get("uptime") or "0").split(":"):
        up = up * 60 + (int(part) if part.isdigit() else 0)
    return ((st.get("health") or {}).get("flash") or {}), up


def wait_flash_at_rest(dut, since_boot=40, still=3.0, limit=90.0):
    """A restart that changed a setting is followed by the configuration backup's refresh about 25 s after boot.
    Wait until the unit has been up since_boot seconds and its flash counters have not moved for still seconds."""
    end = time.time() + limit
    last, last_t = None, time.time()
    while time.time() < end:
        ops, up = flash_ops(dut)
        if ops != last:
            last, last_t = ops, time.time()
        if up >= since_boot and time.time() - last_t >= still:
            return ops
        time.sleep(1.0)
    return last


def leg_n(t, ecus, ch, dut, n):
    print("== N: the line at full rate, counted", flush=True)

    def provider():
        return ((dut.get("/api/uds") or {}).get("provider_stats") or {})

    f0 = wait_flash_at_rest(dut)
    c0, p0, sent0, pdu0 = dut.can(*LOSS_COUNTERS, "rx"), provider(), ecus.sent.get(ECU1 + 8, 0), len(ecus.pdus)
    set_cfg(t, ch, [(CFG_BS, 8), (CFG_STMIN, 1)])
    slow = []
    for i in range(2):
        _s, whole, _f = ask(t, ecus, ch, ECU1, MAX_PDU, 0x40 + i, 15.0)
        slow.append(ask.took if whole else None)
    st = set_cfg(t, ch, [(CFG_BS, 0), (CFG_STMIN, 0)])
    ok, one_fc, fast = 0, 0, []
    for i in range(n):
        _s, whole, fcs = ask(t, ecus, ch, ECU1, MAX_PDU, i & 0xFF, 15.0)
        fast.append(ask.took)
        ok += 1 if whole else 0
        one_fc += 1 if fcs == [b"\x30\x00\x00"] else 0
    bus = [s for _r, _l, s in ecus.pdus[pdu0 + 2:]]
    check("N1 %d answers of %d bytes with BS 0 / STMIN 0: every byte arrived" % (n, MAX_PDU), st == 0 and ok == n, "%d of %d" % (ok, n))
    check("N2 one flow control each, 30 00 00", one_fc == n, "%d of %d" % (one_fc, n))
    if bus and all(slow):
        # the ECU hands an answer's 586 frames to the adapter faster than the bus carries them (about 0.15 s of bus
        # time at 500 kbit/s), so they leave it back to back: that is the line rate
        t_fast, t_slow = sum(fast) / len(fast), sum(slow) / len(slow)
        print("   request to answer in hand: %.0f ms (%.0f B/s) with BS 0 / STMIN 0, %.0f ms (%.0f B/s) with BS 8 / STMIN 1; "
              "the ECU handed an answer's frames to the adapter in %.0f ms" % (
                  1000 * t_fast, MAX_PDU / t_fast, 1000 * t_slow, MAX_PDU / t_slow, 1000 * sum(bus) / len(bus)), flush=True)
    c1, p1, f1 = dut.can(*LOSS_COUNTERS, "rx"), provider(), flash_ops(dut)[0]
    frames = ecus.sent.get(ECU1 + 8, 0) - sent0
    check("N0 the unit's flash was at rest for the run (leg W is the run with the flash written)", f1 == f0,
          {k: (f0.get(k), f1.get(k)) for k in f1 if f0.get(k) != f1.get(k)})
    check("N3 the CAN node lost nothing: no loss counter moved and it received at least the %d frames the ECU sent" % frames,
          all(c1.get(k) == c0.get(k) for k in LOSS_COUNTERS) and (c1.get("rx") or 0) - (c0.get("rx") or 0) >= frames,
          {k: (c0.get(k), c1.get(k)) for k in c0 if c0.get(k) != c1.get(k)})
    fed = (p1.get("frames_fed") or 0) - (p0.get("frames_fed") or 0)
    pdus = (p1.get("pdus_rx") or 0) - (p0.get("pdus_rx") or 0)
    check("N4 the ISO-TP provider was fed exactly those %d frames, made %d messages of them and dropped none" % (frames, n + 2),
          fed == frames and pdus == n + 2 and p1.get("rx_dropped") == p0.get("rx_dropped"),
          {"frames_fed": fed, "pdus_rx": pdus, "rx_dropped": (p0.get("rx_dropped"), p1.get("rx_dropped"))})


def upload(dut, n_bytes):
    """One file of n_bytes written to the unit's /data: erases and writes on its flash."""
    req = urllib.request.Request(dut.base + "/api/fs/upload?path=" + FLASH_PROBE, data=os.urandom(n_bytes), method="POST",
                                 headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode(errors="replace")


def leg_w(t, ecus, ch, dut, uploads=3, size=32768):
    print("== W: the line at full rate while the unit writes its flash", flush=True)

    def provider():
        return ((dut.get("/api/uds") or {}).get("provider_stats") or {})

    st = set_cfg(t, ch, [(CFG_BS, 0), (CFG_STMIN, 0)])
    f0, c0, p0, sent0 = flash_ops(dut)[0], dut.can(*LOSS_COUNTERS, "rx"), provider(), ecus.sent.get(ECU1 + 8, 0)
    done, over = [], threading.Event()

    def writer():
        time.sleep(0.5)
        for _ in range(uploads):
            try:
                done.append(upload(dut, size))
            except Exception as e:
                done.append("%s: %s" % (type(e).__name__, e))
            time.sleep(1.0)
        over.set()

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    n = ok = one_fc = 0
    while (not over.is_set() or n < 20) and n < 400:
        _s, whole, fcs = ask(t, ecus, ch, ECU1, MAX_PDU, n & 0xFF, 15.0)
        n, ok, one_fc = n + 1, ok + (1 if whole else 0), one_fc + (1 if fcs == [b"\x30\x00\x00"] else 0)
    th.join(5)
    f1, c1, p1 = flash_ops(dut)[0], dut.can(*LOSS_COUNTERS, "rx"), provider()
    try:
        dut.api("/api/fs/file?path=" + FLASH_PROBE, "DELETE")
    except Exception:
        pass
    frames = ecus.sent.get(ECU1 + 8, 0) - sent0
    erases = (f1.get("erases") or 0) - (f0.get("erases") or 0)
    check("W0 the unit wrote its flash during the run: %d uploads of %d bytes taken, the erase counter up" % (uploads, size),
          st == 0 and len(done) == uploads and all('"ok":true' in r for r in done) and erases > 0,
          {"erases": erases, "writes": (f1.get("writes") or 0) - (f0.get("writes") or 0), "answers": [r[:40] for r in done]})
    check("W1 %d answers of %d bytes meanwhile: every byte arrived, one flow control each" % (n, MAX_PDU), ok == n and one_fc == n,
          "%d whole, %d with one flow control, of %d" % (ok, one_fc, n))
    check("W2 the CAN node lost nothing: no loss counter moved and it received at least the %d frames the ECU sent" % frames,
          all(c1.get(k) == c0.get(k) for k in LOSS_COUNTERS) and (c1.get("rx") or 0) - (c0.get("rx") or 0) >= frames,
          {k: (c0.get(k), c1.get(k)) for k in c0 if c0.get(k) != c1.get(k)})
    fed = (p1.get("frames_fed") or 0) - (p0.get("frames_fed") or 0)
    pdus = (p1.get("pdus_rx") or 0) - (p0.get("pdus_rx") or 0)
    check("W3 the ISO-TP provider was fed exactly those %d frames, made %d messages of them and dropped none" % (frames, n),
          fed == frames and pdus == n and p1.get("rx_dropped") == p0.get("rx_dropped"),
          {"frames_fed": fed, "pdus_rx": pdus, "rx_dropped": (p0.get("rx_dropped"), p1.get("rx_dropped"))})


def leg_u(dut, ecus):
    print("== U: the device's own UDS client", flush=True)
    f0 = len(ecus.fcs)
    req = b"\x22\x00\x64\x21"
    try:
        r = dut.api("/api/uds/request", "POST", {"tx_id": "%03X" % ECU2, "rx_id": "%03X" % (ECU2 + 8), "data": hexs(req)}, timeout=15)
    except Exception as e:
        r = {"error": "%s: %s" % (type(e).__name__, e)}
    got = bytes.fromhex((r.get("response") or "").replace(" ", "")) if isinstance(r, dict) else b""
    check("U1 POST /api/uds/request 22 00 64 21 to %03X is answered whole (100 bytes)" % ECU2, isinstance(r, dict) and r.get("ok") is True
          and got == answer_to(req), r if not isinstance(r, dict) or not r.get("ok") else len(got))
    fcs = ecus.flow_controls(f0)
    check("U2 its flow control stays 30 08 01, sent again after eight consecutive frames", fcs == [b"\x30\x08\x01"] * 2, fcs_hex(fcs))


def leg_d(py32, jh, jp, dll, ecus):
    print("== D: the driver DLL called like an application", flush=True)
    if not dll:
        print("SKIP: no --dll (a driver before 1.9 does not send the two values: pass the built wican_j2534.dll)", flush=True)
        return
    if not os.path.exists(dll) or not py32 or not os.path.exists(py32):
        check("D0 the DLL and the 32-bit Python are there", False, (dll, py32))
        return
    env = dict(os.environ, WICAN_J2534_HOST=jh if jh != "localhost" else "127.0.0.1", WICAN_J2534_PORT=str(jp),
               WICAN_J2534_DLL=dll, WICAN_FC_REQ="%03X" % ECU1)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "j2534_flow_control_dll_check.py")
    f0, e0 = len(ecus.fcs), len(ecus.errors)
    try:
        p = subprocess.run([py32, "-I", script], env=env, capture_output=True, text=True, timeout=90)
        out = p.stdout + p.stderr
    except Exception as e:
        out, p = "%s: %s" % (type(e).__name__, e), None
    for ln in out.rstrip().splitlines():
        print("   " + ln, flush=True)
    check("D1 every call through the DLL did what the standard says (%d steps)" % out.count("OK: "),
          p is not None and p.returncode == 0 and "DLL CHECK DONE" in out and "BAD: " not in out,
          "exit %s" % (p.returncode if p else "none"))
    fcs = ecus.flow_controls(f0)
    check("D2 the ECU saw what the application set: 30 00 00 as the channel opened, then 30 04 02 four times for 14 consecutive "
          "frames, then 30 00 F2", fcs == [FC_OPENS_WITH] + [b"\x30\x04\x02"] * 4 + [b"\x30\x00\xF2"], fcs_hex(fcs))
    check("D3 the ECU ended every answer", ecus.errors[e0:] == [], ecus.errors[e0:][:4])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--j2534", default="localhost:18534")
    ap.add_argument("--pcan", default=os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"))
    ap.add_argument("--py32", default=r"C:\Users\Ali\idf_tmp\py32\python.exe")
    ap.add_argument("--dll", default=None, help="the built wican_j2534.dll for leg D (the DLL called like an application)")
    ap.add_argument("--big", type=int, default=100, help="answers of 4095 bytes at full rate in leg N")
    ap.add_argument("--keep", action="store_true", help="leave the settings as the run set them (a second run follows)")
    a = ap.parse_args()

    dut = ab.Dut(a.dut)
    jh, jp = a.j2534.split(":")[0], int(a.j2534.split(":")[1])
    t0 = time.time()
    planned = 0

    st = dut.get("/api/status")
    if not st:
        print("J2534 FLOW CONTROL FAIL: no DUT at", a.dut)
        sys.exit(1)
    boot0, unexp0 = st.get("boot_count"), st.get("unexpected_resets")
    err0 = (st.get("health") or {}).get("log_errors")
    print("dut: %s boot_count=%s unexpected=%s log_errors=%s" % (st.get("version"), boot0, unexp0, err0), flush=True)
    found = {c: dut.settings(c) for c in ("j2534_server",)}
    groups = (dut.get("/api/autopid") or {}).get("groups", [])
    group_on = [g["name"] for g in groups if g.get("enabled")]
    try:
        ecus = Ecus(a.pcan, (ECU1, ECU2, ECU3))
    except Exception as e:
        print("J2534 FLOW CONTROL FAIL: PCAN %s not usable: %s" % (a.pcan, e))
        sys.exit(1)
    ecus.start()

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
                print("J2534 FLOW CONTROL FAIL: the DUT did not come back (%s)" % why)
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
        t = tp.Tester(jh if jh != "localhost" else "127.0.0.1", jp)
        t.call(b.HELLO, payload=struct.pack("<I", 1))
        t.call(b.OPEN)
        ch = leg_a(t, ecus)
        leg_b(t, ecus, ch)
        leg_t(t, ecus, ch)
        leg_s(t, ecus, ch)
        leg_z(t, ecus, ch)
        leg_r(t, ecus, ch)
        leg_n(t, ecus, ch, dut, a.big)
        leg_w(t, ecus, ch, dut)
        t.call(b.DISCONNECT, channel=ch)
        t.call(b.CLOSE)
    except Exception as e:
        check("the legs ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        if t is not None:
            t.close()
        time.sleep(1.0)
    try:
        leg_u(dut, ecus)
    except Exception as e:
        check("leg U ran to the end", False, "%s: %s" % (type(e).__name__, e))
    leg_d(a.py32, jh, jp, a.dll, ecus)
    check("the ECUs ended every answer they began", not ecus.errors, ecus.errors[:4])

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
    ecus.close()

    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t0), flush=True)
    if fails:
        print("J2534 FLOW CONTROL FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("J2534 FLOW CONTROL PASS")


if __name__ == "__main__":
    main()
