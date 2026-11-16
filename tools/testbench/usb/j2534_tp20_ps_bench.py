#!/usr/bin/env python3
"""TP2.0 as its own J2534 protocol against ECUs that follow SAE J2819  ->  J2534 TP20 PASS

The PassThru server serves TP2.0 (SAE J2534-2 section 19, protocol TP2_0_PS 0x800E):
the device sets the channel up, segments and reassembles, acknowledges blocks and
keeps the connection alive. This bench plays the other side of the bus the way SAE
J2819 writes it down, on the PCAN, and drives the device over the wire protocol the
Windows DLL speaks. The ECUs: identifier 0x200 + address, the first one (address
0x01) receives on 0x740 as a real engine ECU does.

Legs (one planned restart to configure, one to restore):
  C  configure: j2534 on with allow_lan, autopid's groups paused
  S  CONNECT(TP2_0_PS), J1962_PINS 0x060E taken, another pin pair refused
  I  GET_DEVICE_INFO with a parameter list (SAE J2534-2 section 25): TP2_0_SUPPORTED and
     TP2_0_SIMULTANEOUS 0x00000100, TP2_0_PS_J1962 answered in "supported" for the
     pin bitmap asked, an unknown parameter = not supported with NOERROR
  K  REQUEST_CONNECTION: the setup on 0x200, CONNECTION_ESTABLISHED (RX id, TX id),
     the connection setup with a block size of 1 to 15 and FF in the unused time bytes
  J  an exchange published from a real car (jazdw.net/tp20), byte for byte: 10 89
     answered 50 89, 21 01 answered by 26 bytes in four telegrams, every telegram of
     both sides as the page prints it
  D  data: a short request answered by a 42-byte message (6 telegrams), a 601-byte
     request (86 telegrams, an acknowledge wanted at every block end) arrives whole
  W  a message goes out as written and comes in as sent (SAE J2534-2 19.3.1; in J2819 a
     message ends at the end-of-message telegram and has no length field): the two
     length bytes a VW ECU wants in front are the application's, the device neither
     adds nor removes them and does not read them (a message whose length bytes say
     more, or less, than came is handed over as it came); 4093 bytes are refused on
     the way out and not handed over on the way in; the id alone = one empty
     end-of-message telegram
  T  the connection test (J2819 5.2, 6.2.9): 7.5 s without traffic, the ECU answers
     every test with a connection acknowledge; the connection stays, no disconnect,
     a request after it is answered
  M  four connections at once (J2819 5.2: four must be kept): three more ECUs, a
     fifth request refused with nothing sent, a request on each, each tested about
     once a second, the three torn down again
  N  the largest message at full speed, counted: 4092 bytes written N times to an
     ECU that asks for no gap between telegrams and read back N times with none,
     every byte compared, the CAN node's loss counters unchanged
  O  beside other protocols: an ISO15765 channel of the same tester talks UDS to the
     simulator (7E0 / 7E8) while the TP2.0 connection stays up and answers after
  G  what J2819 says about trouble (5.2, table 8, 6.2.4 to 6.2.7), injected by the
     ECU: a telegram lost inside a block (sent again from it), a block-end telegram
     lost (sent again after T1), receiver not ready (T_WAIT, then on), not ready with
     a telegram to send again, a telegram of the ECU's answer lost (the device names
     it), a break (the message is ended, the connection kept)
  B  a broadcast (J2819 5.1.2): five telegrams T_BR_INT apart, the two key bytes
     55 55, AA AA, 55 55, AA AA, 55 55; a broadcast flag on a non-broadcast address
     is ERR_INVALID_MSG
  R  the re-triggered broadcast (START_PERIODIC with the broadcast flag): the five,
     then ONE telegram per period, the key still alternating; STOP_PERIODIC ends it
  E  a long message to an id without a connection = ERR_NO_CONNECTION_ESTABLISHED
     (0x00010001, SAE J2534-2 table 123); a short one goes out as a CAN frame
  F  a connection request nobody answers: CONNECTION_LOST with the RX id of the
     request and reason 1 after the retries
  Q  (--soak S) S seconds without traffic: a test about every second all the way,
     the connection kept
  X  TEARDOWN_CONNECTION: the ECU sees the disconnect, CONNECTION_LOST reason 0
  L  the driver DLL called like an application (--dll, the 32-bit Python):
     j2534_tp20_ps_dll_check.py with the standard's own structures (an SPARAM_LIST
     for GET_DEVICE_INFO, SBYTE_ARRAYs for the connection), against the same ECU
  Z  restore: settings as found, only the planned restarts, no new E lines

usage (PC, PCAN wired to the DUT's bus, the tunnel of ports 80 and 6809 to the DUT):
  python j2534_tp20_ps_bench.py --dut localhost:18081 --j2534 localhost:18534
       [--pcan PCAN_USBBUS1] [--dll <built wican_j2534.dll>] [--py32 <32-bit python>]
       [--big 10] (messages each way in leg N) [--soak 60]
       [--keep] (leave the settings as the run set them)
"""
import argparse
import json
import os
import socket
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
sys.argv = _argv

TP20 = 0x800E
START_FILTER, START_PERIODIC, STOP_PERIODIC, IOCTL = 0x11, 0x13, 0x14, 0x15
IOCTL_GET_CONFIG, IOCTL_SET_CONFIG = 0x01, 0x02
IOCTL_REQUEST, IOCTL_TEARDOWN, IOCTL_DEVICE_INFO = 0x800A, 0x800B, 0x800C
CFG_J1962_PINS = 0x8001
DEV_ETHERNET_NDIS, DEV_TP20_SUPPORTED, DEV_TP20_PS_J1962, DEV_TP20_SIMULTANEOUS = 0x54, 0x13, 0x28, 0x46
RX_UP, RX_LOST = 0x00010000, 0x00020000
TX_BROADCAST = 0x00010000
FLOW_CONTROL_FILTER = 3
ERR_NOT_SUPPORTED, ERR_INVALID_MSG, ERR_NO_CONNECTION = 0x01, 0x0A, 0x00010001
TESTER_ID, ECU_ADDR, ECU_RX, TESTER_RX = 0x200, 0x01, 0x740, 0x300
MAX_MSG = 4092                  # the largest message behind the id: SAE J2534-2 table 80
BIG = MAX_MSG - 2               # what is left of it behind the two length bytes a VW ECU wants
LOSS_COUNTERS = ("tx_errors", "rx_errors", "rx_missed", "rx_overrun", "tx_lost", "dispatch_drops", "rx_bad", "bus_off")
# jazdw.net/tp20, the example exchange of a real engine ECU: the answer to 21 01
REAL_21_01 = bytes.fromhex("6101010000" "27000022 00801A".replace(" ", "") + "324B25027A2500" + "00250000250000")

fails, passes = [], []


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


def hexs(d):
    return bytes(d).hex(" ").upper()


def time_byte_s(wire):
    """A T1 / T3 byte (J2819 5.2.2): bits 7-6 the base, bits 5-0 the count."""
    return (0.0001, 0.001, 0.01, 0.1)[wire >> 6] * (wire & 0x3F)


def pattern(seed, n):
    return bytes((seed * 13 + i * 7 + (i >> 8)) & 0xFF for i in range(n))


class Rig(threading.Thread):
    """The PCAN and the ECUs on it. Everything on the bus is kept."""

    def __init__(self, channel):
        super().__init__(daemon=True)
        self.pcan = channel
        self.bus = can.Bus(interface="pcan", channel=channel, bitrate=500000, receive_own_messages=False)
        while self.bus.recv(timeout=0):
            pass
        self.halt = threading.Event()
        self.ecus = {}          # address -> Ecu
        self.by_rx = {}         # an ECU's RX id -> Ecu
        self.frames = []        # (t, can id, data): every frame received
        self.trace = []         # (can id, data) in bus order, the ECUs' own frames included
        self.bcast = []         # (t, data): telegrams to a broadcast address
        self.errors = []        # what an ECU saw that J2819 does not allow

    def add(self, ecu):
        self.ecus[ecu.addr] = ecu
        self.by_rx[ecu.rx_p] = ecu

    def run(self):
        while not self.halt.is_set():
            self.pump(None, 0.05)

    def close(self):
        self.halt.set()
        self.join(1.0)
        self.bus.shutdown()

    def send(self, can_id, data):
        try:
            self.bus.send(can.Message(arbitration_id=can_id, is_extended_id=False, data=data), timeout=1.0)
            self.trace.append((can_id, bytes(data)))
        except can.CanError as e:
            self.errors.append("an ECU could not send %s: %s" % (hexs(data), e))

    def pump(self, sender, timeout):
        """One frame off the bus, handled. An acknowledge for the ECU that is sending is returned."""
        m = self.bus.recv(timeout=timeout)
        if m is None or m.is_error_frame or m.is_extended_id or m.is_remote_frame:
            return None
        t, cid, d = m.timestamp, m.arbitration_id, bytes(m.data)
        self.frames.append((t, cid, d))
        self.trace.append((cid, d))
        if 0x200 <= cid <= 0x2EF:
            if len(d) >= 2 and d[0] >= 0xF0:
                self.bcast.append((t, d))
            elif len(d) == 7 and d[1] == 0xC0 and d[0] in self.ecus:
                self.ecus[d[0]].channel(t, cid, d)
            return None
        ecu = self.by_rx.get(cid)
        if ecu is None or not d:
            return None
        op = ecu.telegram(t, d, sending=ecu is sender)
        return op if ecu is sender else None


class Ecu:
    """One ECU as SAE J2819 describes it: identifier 0x200 + addr, receives on rx_p."""

    def __init__(self, rig, addr=ECU_ADDR, rx_p=ECU_RX, bs=15, t3_wire=0x0A):
        self.rig, self.addr, self.rx_p, self.bs, self.t3_wire = rig, addr, rx_p, bs, t3_wire
        self.rx_a = None        # where the tester receives: the ECU sends there
        self.up = False
        self.tx_bs = bs
        self.t3_s = 0.001
        self.sn_rx = self.sn_tx = 0
        self.asm = b""
        self.setups = []        # (t, data): channel setups addressed to this ECU
        self.conn_setups = []   # the connection setups (0xA0 ..)
        self.tests = []         # t of every connection test (0xA3)
        self.disconnects = []   # t of every disconnect (0xA8)
        self.msgs = []          # the complete messages received, the length word taken off
        self.wire_msgs = []     # the same as they were on the wire, the ones with a wrong length word too
        self.lenient = False    # leg W: a wrong length word is kept in wire_msgs and not held against the tester
        self.ack_wanted = 0     # data telegrams that asked for an acknowledge
        self.replies = {}       # a request -> its answer, before the built-in ones
        self.raw_replies = {}   # a request -> its answer's bytes as they go on the wire (leg W)
        self.fault = None       # one injected fault for the next message (leg G): see data() and send_message()
        self.rx_i = 0           # data telegrams taken of the message being received
        self.broke = False      # a break left: the message ends at the next end-of-message telegram
        self.broken = 0         # messages given up after a break
        self.seen = []          # (t, first byte) of every data telegram that came, the dropped ones too
        self.errors = rig.errors
        self.frames = rig.frames
        self.bcast = rig.bcast
        rig.add(self)

    def send(self, data):
        self.rig.send(self.rx_a, data)

    def conn_ack(self):
        return bytes([0xA1, self.bs, 0x8A, 0xFF, self.t3_wire, 0xFF])

    def channel(self, t, cid, d):
        self.setups.append((t, d))
        if d[5] & 0x10:
            self.errors.append("a channel setup without an RX id: %s" % hexs(d))
            return
        self.rx_a = d[4] | ((d[5] & 0x07) << 8)
        self.up = False
        self.rig.send(0x200 + self.addr, bytes([cid & 0xFF, 0xD0, d[4], d[5] & 0x07, self.rx_p & 0xFF,
                                                (self.rx_p >> 8) & 0x07, d[6]]))

    def telegram(self, t, d, sending=False):
        """One telegram on this ECU's RX id. Returns an acknowledge's first byte, else None."""
        op = d[0]
        if op == 0xA0 and len(d) >= 6:
            self.conn_setups.append(d)
            self.tx_bs = min(self.bs, d[1]) if 1 <= d[1] <= 15 else self.bs
            self.t3_s = time_byte_s(d[4])
            self.sn_rx = self.sn_tx = 0
            self.asm = b""
            self.rx_i = 0
            self.broke = False
            self.up = True
            self.send(self.conn_ack())
        elif not self.up:
            self.errors.append("ECU %02X: a telegram before the connection setup: %s" % (self.addr, hexs(d)))
        elif op == 0xA3:
            self.tests.append(t)
            self.send(self.conn_ack())         # J2819 5.2: a test is answered with a connection acknowledge
        elif op == 0xA8:
            self.disconnects.append(t)
            self.send(b"\xA8")                 # J2819 6.2.11
            self.up = False
        elif op == 0xA1:
            pass
        elif (op >> 4) in (0x9, 0xB):
            return op                          # an acknowledge: send_message waits for it
        elif (op >> 4) <= 0x3:
            if sending:
                self.errors.append("ECU %02X: a data telegram while it was sending: %s" % (self.addr, hexs(d)))
            else:
                self.data(t, d)
        else:
            self.errors.append("ECU %02X: a telegram J2819 does not know: %s" % (self.addr, hexs(d)))
        return None

    def data(self, t, d):
        """One data telegram. The injected faults of leg G (self.fault, one per message):
        ("drop_rx", k[, "not_ready"])  telegram k is lost on its way here: the ones behind it are out of
                                        sequence and not taken, and the acknowledge request at the block end
                                        is answered with the sequence number that is missing (J2819 5.2, table 8;
                                        as "receiver not ready" with the third element, 6.2.6)
        ("not_ready",)                  the first block-end acknowledge says "receiver not ready" (6.2.4)
        ("break", k)                    a break leaves after telegram k (6.2.7): the message is given up"""
        op, sn = d[0] >> 4, d[0] & 0xF
        self.seen.append((t, d[0]))
        f = self.fault
        if f and f[0] == "drop_rx" and sn == self.sn_rx and self.rx_i == f[1]:
            self.fault = ("gap",) + f[2:]
            return
        if sn != self.sn_rx:
            if f and f[0] == "gap":
                if op in (0x0, 0x1):
                    self.send(bytes([(0x90 if len(f) > 1 else 0xB0) | self.sn_rx]))
                return
            self.errors.append("ECU %02X: a data telegram with sequence %X, expected %X" % (self.addr, sn, self.sn_rx))
            self.send(bytes([0xB0 | self.sn_rx]))    # J2819 table 8: answer with the expected one
            return
        if f and f[0] == "gap":
            self.fault = None                        # the missing telegram came again
        self.sn_rx = (sn + 1) & 0xF
        self.asm += d[1:]
        self.rx_i += 1
        if op in (0x0, 0x1):
            self.ack_wanted += 1
            if self.fault == ("not_ready",) and op == 0x0:
                self.fault = None
                self.send(bytes([0x90 | self.sn_rx]))
            else:
                self.send(bytes([0xB0 | self.sn_rx]))
        if self.fault and self.fault[0] == "break" and self.rx_i == self.fault[1]:
            self.fault = None
            self.broke = True
            self.send(b"\xA4")
        if op not in (0x1, 0x3):
            return
        wire, self.asm = self.asm, b""
        self.rx_i = 0
        if self.broke:
            self.broke = False
            self.broken += 1
            return
        self.wire_msgs.append(wire)
        if len(wire) < 2 or (struct.unpack(">H", wire[:2])[0] & 0x7FFF) != len(wire) - 2:
            if not self.lenient:
                self.errors.append("ECU %02X: a message with the length word %s over %d bytes" % (
                    self.addr, hexs(wire[:2]), len(wire) - 2))
            return
        kwp = wire[2:]
        self.msgs.append(kwp)
        if kwp in self.raw_replies:
            self.send_wire(self.raw_replies[kwp])
        else:
            self.send_message(self.answer(kwp))

    def answer(self, kwp):
        if kwp in self.replies:
            return self.replies[kwp]
        if kwp[:1] == b"\x1A":
            n = 40 if kwp[1:2] == b"\x9B" else 2
            return b"\x5A" + kwp[1:2] + bytes((i * 3 + 1) & 0xFF for i in range(n))
        if kwp[:1] == b"\x22" and len(kwp) == 2:
            return b"\x62" + kwp[1:2] + pattern(kwp[1], BIG - 2)     # the largest message
        if kwp[:1] == b"\x36":
            return b"\x76" + struct.pack(">H", len(kwp) - 1) + bytes([sum(kwp[1:]) & 0xFF])
        if kwp[:1] == b"\x3E":
            return b"\x7E"
        return b"\x7F" + kwp[:1] + b"\x11"

    def send_message(self, kwp):
        """An answer the way a VW ECU sends it: its length word in front."""
        self.send_wire(struct.pack(">H", len(kwp)) + kwp)

    def send_wire(self, wire):
        """These bytes as one message in telegrams, an acknowledge asked for at every block end. An
        acknowledge that names an earlier telegram of the block has the block sent again from it
        (J2819 5.2). The injected fault ("skip_tx", k): telegram k is lost on its way to the tester, once."""
        chunks = [wire[i:i + 7] for i in range(0, len(wire), 7)] or [b""]
        sn0 = self.sn_tx                     # the sequence number of the first telegram
        i = block0 = again = 0
        while i < len(chunks):
            last = i == len(chunks) - 1
            block_end = (i - block0 + 1) % self.tx_bs == 0
            sn = (sn0 + i) & 0xF
            if self.fault == ("skip_tx", i):
                self.fault = None
            else:
                self.send(bytes([(0x10 if last else (0x00 if block_end else 0x20)) | sn]) + chunks[i])
            if not (last or block_end):
                if self.t3_s > 0:
                    time.sleep(self.t3_s)
                i += 1
                continue
            expected = (sn + 1) & 0xF
            end = time.perf_counter() + 1.0
            got = None
            while got is None and time.perf_counter() < end:
                got = self.rig.pump(self, 0.05)
            if got == (0xB0 | expected):
                i += 1
                block0 = i
                continue
            back = None if got is None else (expected - (got & 0xF)) & 0xF
            if back is not None and 1 <= back <= i - block0 + 1 and again < 5:
                again += 1
                i = i + 1 - back             # the telegram the tester names: from there again
                continue
            self.errors.append("ECU %02X: the tester's acknowledge B%X did not come within 1 s (got %s)" % (
                self.addr, expected, "nothing" if got is None else "%02X" % got))
            self.sn_tx = expected
            return
        self.sn_tx = (sn0 + len(chunks)) & 0xF


class Tester:
    """The wire protocol the DLL speaks, with the indications kept instead of skipped."""

    def __init__(self, host, port):
        self.s = socket.create_connection((host, port), timeout=5)
        self.s.settimeout(None)
        self.seq = 0
        self.acks = {}
        self.rx = []            # (t, channel, rx_status, data)
        self.cv = threading.Condition()
        self.dead = None
        threading.Thread(target=self._reader, daemon=True).start()

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise EOFError("the device closed the link")
            buf += chunk
        return buf

    def _reader(self):
        try:
            while True:
                magic, _ver, rt, rseq, rch, rlen = b.HDR.unpack(self._recv(b.HDR.size))
                if magic != b.MAGIC:
                    raise ValueError("bad magic 0x%04X" % magic)
                body = self._recv(rlen)
                with self.cv:
                    if rt == b.ACK:
                        self.acks[rseq] = body
                    elif rt == b.RX_MSG:
                        f = b.MSG.unpack_from(body, 0)
                        self.rx.append((time.perf_counter(), rch, f[1], body[b.MSG.size:b.MSG.size + f[5]]))
                    self.cv.notify_all()
        except Exception as e:
            with self.cv:
                self.dead = e
                self.cv.notify_all()

    def call(self, mtype, channel=0, payload=b"", timeout=20.0):
        """One request. Returns (status, the ACK's bytes behind the status)."""
        self.seq = (self.seq + 1) & 0xFFFF
        seq = self.seq
        self.s.sendall(b.HDR.pack(b.MAGIC, 1, mtype, seq, channel, len(payload)) + payload)
        end = time.perf_counter() + timeout
        with self.cv:
            while seq not in self.acks:
                left = end - time.perf_counter()
                if left <= 0 or self.dead:
                    raise TimeoutError("no ACK for message type 0x%02X (%s)" % (mtype, self.dead or "timeout"))
                self.cv.wait(left)
            body = self.acks.pop(seq)
        return struct.unpack_from("<I", body, 0)[0], body[4:]

    def wait_rx(self, pred, timeout):
        """The first kept message pred accepts, taken out of the list; None after timeout."""
        end = time.perf_counter() + timeout
        with self.cv:
            i = 0
            while True:
                while i < len(self.rx):
                    if pred(self.rx[i]):
                        return self.rx.pop(i)
                    i += 1
                left = end - time.perf_counter()
                if left <= 0 or self.dead:
                    return None
                self.cv.wait(left)

    def lost(self):
        with self.cv:
            return [(hex(s), hexs(d)) for _t, _c, s, d in self.rx if s & RX_LOST]

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def ioctl(t, ch, ioctl_id, body=b""):
    return t.call(IOCTL, ch, struct.pack("<II", ioctl_id, len(body)) + body)


def cfg(pairs):
    return struct.pack("<I", len(pairs)) + b"".join(struct.pack("<II", p, v) for p, v in pairs)


def any_msg(proto, data, tx_flags=0):
    return b.MSG.pack(proto, 0, tx_flags, 0, 0, len(data)) + data


def write(t, ch, data, tx_flags=0):
    return t.call(b.WRITE_MSGS, ch, struct.pack("<I", 1) + any_msg(TP20, data, tx_flags))[0]


def device_info(t, pairs):
    """GET_DEVICE_INFO for (parameter, value) pairs -> status, {parameter: [(value, supported), ..]}."""
    st, body = ioctl(t, 0, IOCTL_DEVICE_INFO, cfg(pairs))
    out = {}
    if st == 0 and len(body) >= 4:
        n = struct.unpack_from("<I", body, 0)[0]
        for i in range(n):
            if 4 + (i + 1) * 12 > len(body):
                break
            p, v, s = struct.unpack_from("<III", body, 4 + i * 12)
            out.setdefault(p, []).append((v, s))
    return st, out


def request(t, ch, dest, rx_a):
    """REQUEST_CONNECTION (SAE J2534-2 table 78): the 11 bytes behind their count."""
    arr = struct.pack(">I", TESTER_ID) + bytes([dest, 0xC0, 0x00, 0x10, rx_a & 0xFF, (rx_a >> 8) & 0x07, 0x01])
    return ioctl(t, ch, IOCTL_REQUEST, struct.pack("<I", len(arr)) + arr)[0]


def teardown(t, ch, rx_a):
    return ioctl(t, ch, IOCTL_TEARDOWN, struct.pack("<I", 4) + struct.pack(">I", rx_a))[0]


def gaps_ms(times):
    return [round((b2 - a2) * 1000.0, 1) for a2, b2 in zip(times, times[1:])]


def drop_lost(t):
    """Take the CONNECTION_LOST indications of an earlier leg out of the way."""
    while t.wait_rx(lambda r: r[2] & RX_LOST, 0) is not None:
        pass


def vw(kwp):
    """A message the way a VW ECU takes it: two length bytes, then the bytes. They are the application's
    to add: the device passes a message as written (SAE J2534-2 19.3.1)."""
    return struct.pack(">H", len(kwp)) + kwp


def read_msg(t, ch, secs, tester_rx=TESTER_RX):
    """The next message of a connection: its bytes behind the id, or None."""
    r = t.wait_rx(lambda x: x[1] == ch and not (x[2] & (RX_UP | RX_LOST | b.RX_TX_MSG_TYPE))
                  and x[3][:4] == struct.pack(">I", tester_rx), secs)
    return None if r is None else r[3][4:]


def ask_wire(t, ch, data, secs=3.0, ecu_rx=ECU_RX, tester_rx=TESTER_RX):
    """One message written as given; the device's status and the next message read, as it was handed over."""
    st = write(t, ch, struct.pack(">I", ecu_rx) + data)
    return st, read_msg(t, ch, secs, tester_rx)


def ask(t, ch, kwp, secs=3.0, ecu_rx=ECU_RX, tester_rx=TESTER_RX):
    """One request to an ECU with the length bytes an application adds; the device's status and the
    answer behind its own length bytes (None, and a line, when it does not begin with them)."""
    st, body = ask_wire(t, ch, vw(kwp), secs, ecu_rx, tester_rx)
    if body is None:
        return st, None
    if len(body) < 2 or struct.unpack(">H", body[:2])[0] != len(body) - 2:
        print("   the answer does not begin with its length bytes: %s (%d bytes)" % (hexs(body[:10]), len(body)), flush=True)
        return st, None
    return st, body[2:]


def leg_s(t):
    print("== S: the channel", flush=True)
    st, body = t.call(b.CONNECT, payload=struct.pack("<IIIII", TP20, 0, 500000, 0, 0))
    ch = struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else 0
    check("S1 CONNECT(TP2_0_PS 0x800E, 500000) accepted", st == 0 and ch >= 1, (hex(st), ch))
    st, _ = ioctl(t, ch, IOCTL_SET_CONFIG, cfg([(CFG_J1962_PINS, 0x060E)]))
    check("S2 SET_CONFIG J1962_PINS 0x060E (pins 6 and 14) taken", st == 0, hex(st))
    st, body = ioctl(t, ch, IOCTL_GET_CONFIG, cfg([(CFG_J1962_PINS, 0)]))
    val = struct.unpack_from("<III", body, 0)[2] if len(body) >= 12 else None
    check("S3 GET_CONFIG J1962_PINS reads 0x060E", st == 0 and val == 0x060E, (hex(st), val))
    st, _ = ioctl(t, ch, IOCTL_SET_CONFIG, cfg([(CFG_J1962_PINS, 0x030B)]))
    check("S4 pins 3 and 11 are refused with ERR_NOT_SUPPORTED", st == ERR_NOT_SUPPORTED, hex(st))
    return ch


def leg_i(t):
    print("== I: GET_DEVICE_INFO with a parameter list", flush=True)
    st, got = device_info(t, [(DEV_TP20_SUPPORTED, 0), (DEV_TP20_SIMULTANEOUS, 0), (DEV_TP20_PS_J1962, 0x00202000),
                              (DEV_TP20_PS_J1962, 0x00010002), (DEV_ETHERNET_NDIS, 0x1234)])
    check("I1 the list is answered with NOERROR", st == 0 and len(got) == 4, (hex(st), got))
    check("I2 TP2_0_SUPPORTED = 0x00000100 (one TP2_0_PS channel), supported", got.get(DEV_TP20_SUPPORTED) == [(0x100, 1)],
          got.get(DEV_TP20_SUPPORTED))
    check("I3 TP2_0_SIMULTANEOUS = 0x00000100, supported", got.get(DEV_TP20_SIMULTANEOUS) == [(0x100, 1)],
          got.get(DEV_TP20_SIMULTANEOUS))
    check("I4 TP2_0_PS_J1962: pins 6 and 14 supported, pins 1 and 2 not, the value left alone",
          got.get(DEV_TP20_PS_J1962) == [(0x00202000, 1), (0x00010002, 0)], got.get(DEV_TP20_PS_J1962))
    check("I5 a parameter the device does not have: not supported, the value left alone",
          got.get(DEV_ETHERNET_NDIS) == [(0x1234, 0)], got.get(DEV_ETHERNET_NDIS))


def leg_k(t, ecu, ch):
    print("== K: the connection", flush=True)
    st = request(t, ch, ECU_ADDR, TESTER_RX)
    check("K1 REQUEST_CONNECTION accepted at once", st == 0, hex(st))
    up = t.wait_rx(lambda r: r[2] & RX_UP, 2.0)
    check("K2 the setup went out on 0x200 as 01 C0 00 10 00 03 01",
          bool(ecu.setups) and ecu.setups[0][1] == bytes([0x01, 0xC0, 0x00, 0x10, 0x00, 0x03, 0x01]),
          [hexs(d) for _t, d in ecu.setups][:2])
    if not ecu.setups:
        print("   nothing of the unit on %s: is that the PCAN channel on the unit's bus (--pcan, WICAN_PCAN_DUT)?" % ecu.rig.pcan,
              flush=True)
    check("K3 CONNECTION_ESTABLISHED: data = the RX id 0x300, then the TX id 0x740",
          up is not None and up[3] == struct.pack(">II", TESTER_RX, ECU_RX), None if up is None else hexs(up[3]))
    a0 = ecu.conn_setups[0] if ecu.conn_setups else b""
    check("K4 the connection setup: A0, a block size of 1 to 15, FF in the two unused time bytes",
          len(a0) == 6 and 1 <= a0[1] <= 15 and a0[3] == 0xFF and a0[5] == 0xFF, hexs(a0))


def leg_j(t, rig, ecu, ch):
    print("== J: an exchange published from a real car, byte for byte", flush=True)
    ecu.replies[b"\x10\x89"] = b"\x50\x89"
    ecu.replies[b"\x21\x01"] = REAL_21_01
    n0 = len(rig.trace)
    st1, a1 = ask(t, ch, b"\x10\x89")
    st2, a2 = ask(t, ch, b"\x21\x01")
    time.sleep(0.2)
    # the data telegrams and their acknowledges alone: a connection test may fall in between
    seen = ["%03X %s" % (cid, hexs(d)) for cid, d in rig.trace[n0:]
            if cid in (ECU_RX, TESTER_RX) and d[0] not in (0xA1, 0xA3)]
    want = ["740 10 00 02 10 89", "300 B1", "300 10 00 02 50 89", "740 B1",
            "740 11 00 02 21 01", "300 B2", "300 21 00 1A 61 01 01 00 00", "300 22 27 00 00 22 00 80 1A",
            "300 23 32 4B 25 02 7A 25 00", "300 14 00 25 00 00 25 00 00", "740 B5"]
    check("J1 10 89 is answered 50 89 and 21 01 by the 26 bytes of the page", st1 == 0 and a1 == b"\x50\x89" and st2 == 0
          and a2 == REAL_21_01, (hex(st1), hex(st2), None if a2 is None else len(a2)))
    check("J2 the eleven telegrams of both sides are the page's, byte for byte", seen == want,
          [x for x in seen if x not in want][:3] or seen[:12])


def leg_d(t, ecu, ch):
    print("== D: data both ways", flush=True)
    st, ans = ask(t, ch, b"\x1A\x9B")
    want = b"\x5A\x9B" + bytes((i * 3 + 1) & 0xFF for i in range(40))
    check("D1 1A 9B reached the ECU as one message", st == 0 and ecu.msgs[-1:] == [b"\x1A\x9B"], (hex(st), [hexs(m) for m in ecu.msgs[-1:]]))
    check("D2 the 42-byte answer (6 telegrams) came back whole, the RX id first", ans == want, None if ans is None else hexs(ans[:12]))
    big = b"\x36" + bytes((i * 7 + 3) & 0xFF for i in range(600))
    wanted0 = ecu.ack_wanted
    st, ans = ask(t, ch, big, 8.0)
    check("D3 a 601-byte request arrived whole", st == 0 and ecu.msgs[-1:] == [big], (hex(st), len(ecu.msgs[-1]) if ecu.msgs else 0))
    check("D4 the ECU acknowledged it: 76, the length, the checksum", ans == b"\x76" + struct.pack(">H", 600) + bytes([sum(big[1:]) & 0xFF]),
          None if ans is None else hexs(ans))
    check("D5 the device asked for an acknowledge at every block end (86 telegrams, 6 or more asked)", ecu.ack_wanted - wanted0 >= 6,
          ecu.ack_wanted - wanted0)


def leg_w(t, rig, ecu, ch):
    print("== W: a message as written (the two length bytes are the application's)", flush=True)

    def data_out(n0):
        return [hexs(d) for cid, d in rig.trace[n0:] if cid == ECU_RX and (d[0] >> 4) <= 0x3]

    n0 = len(rig.trace)
    st, ans = ask_wire(t, ch, b"\x00\x02\x1A\x9B")
    time.sleep(0.1)
    out = data_out(n0)
    check("W1 00 02 1A 9B written goes out as one telegram with those four bytes, nothing added",
          st == 0 and len(out) == 1 and out[0][0] == "1" and out[0][3:] == "00 02 1A 9B", (hex(st), out[:3]))
    want = vw(b"\x5A\x9B" + bytes((i * 3 + 1) & 0xFF for i in range(40)))
    check("W2 the answer is read as the ECU sent it: its two length bytes 00 2A, then the 42 bytes", ans == want,
          None if ans is None else (len(ans), hexs(ans[:8])))

    ecu.lenient = True
    w0 = len(ecu.wire_msgs)
    st = write(t, ch, struct.pack(">I", ECU_RX) + b"\x1A\x9B\x77")
    time.sleep(0.3)
    check("W3 a message written without length bytes goes out as written (1A 9B 77): the device adds none",
          st == 0 and ecu.wire_msgs[w0:] == [b"\x1A\x9B\x77"], (hex(st), [hexs(m) for m in ecu.wire_msgs[w0:]]))
    n0, w0 = len(rig.trace), len(ecu.wire_msgs)
    st = write(t, ch, struct.pack(">I", ECU_RX))
    time.sleep(0.3)
    out = data_out(n0)
    check("W4 a write of the id alone is one end-of-message telegram without data",
          st == 0 and len(out) == 1 and len(out[0]) == 2 and out[0][0] == "1" and ecu.wire_msgs[w0:] == [b""],
          (hex(st), out[:3]))
    ecu.lenient = False
    while read_msg(t, ch, 0.2) is not None:          # whatever an older device's telegrams were answered with
        pass

    short = b"\x00\x10\x61\x01\x02"                           # the word says 16 bytes, 3 came
    more = b"\x00\x02\x61\x01" + bytes(range(0xA0, 0xA8))     # the word says 2 bytes, 10 came (two telegrams)
    over = struct.pack(">H", MAX_MSG - 1) + pattern(3, MAX_MSG - 1)   # 4093 bytes: one more than the largest
    ecu.raw_replies.update({b"\x21\x10": short, b"\x21\x11": more, b"\x21\x12": over})
    st, ans = ask_wire(t, ch, vw(b"\x21\x10"))
    check("W5 a message whose length bytes say more than came is handed over as it came (it ends at the "
          "end-of-message telegram)", st == 0 and ans == short, (hex(st), None if ans is None else hexs(ans)))
    st, ans = ask_wire(t, ch, vw(b"\x21\x11"))
    extra = read_msg(t, ch, 0.3)
    check("W6 one whose length bytes say less than came is handed over whole, as one message",
          st == 0 and ans == more and extra is None,
          (hex(st), None if ans is None else hexs(ans), None if extra is None else hexs(extra[:8])))

    n0 = len(rig.frames)
    st = write(t, ch, struct.pack(">I", ECU_RX) + bytes(MAX_MSG + 1))
    time.sleep(0.2)
    sent = [d for _t, cid, d in rig.frames[n0:] if cid == ECU_RX and (d[0] >> 4) <= 0x3]
    check("W7 a message of 4093 bytes behind the id is refused with ERR_INVALID_MSG and nothing goes out",
          st == ERR_INVALID_MSG and not sent, (hex(st), len(sent)))
    n_err = len(rig.errors)
    st, ans = ask_wire(t, ch, vw(b"\x21\x12"), 6.0)
    check("W8 a message of 4093 bytes from the ECU is not handed over, and every block of it is acknowledged",
          st == 0 and ans is None and rig.errors[n_err:] == [],
          (hex(st), None if ans is None else len(ans), rig.errors[n_err:][:2]))
    st, ans = ask(t, ch, b"\x3E")
    check("W9 the connection is kept through all of it", st == 0 and ans == b"\x7E" and not t.lost(), (hex(st), t.lost()))


def leg_t(t, ecu, ch):
    print("== T: the connection test (7.5 s without traffic)", flush=True)
    n0, d0 = len(ecu.tests), len(ecu.disconnects)
    time.sleep(7.5)
    tests = ecu.tests[n0:]
    check("T1 the device tested the connection about once a second (5 to 9 tests)", 5 <= len(tests) <= 9, len(tests))
    g = gaps_ms(tests)
    check("T2 the tests are 0.8 to 1.3 s apart", bool(g) and all(800 <= x <= 1300 for x in g), g)
    check("T3 the connection is not reported lost", not t.lost(), t.lost())
    check("T4 the device sent no disconnect", len(ecu.disconnects) == d0, len(ecu.disconnects) - d0)
    st, ans = ask(t, ch, b"\x3E")
    check("T5 a request after the pause is answered", st == 0 and ans == b"\x7E", (hex(st), None if ans is None else hexs(ans)))


def leg_m(t, rig, ch, first, more, fifth):
    print("== M: four connections at once", flush=True)
    pairs = [(e, 0x301 + i) for i, e in enumerate(more)]
    ups = []
    for e, rx in pairs:
        st = request(t, ch, e.addr, rx)
        up = t.wait_rx(lambda r: r[2] & RX_UP, 2.0)
        ups.append((hex(st), None if up is None else hexs(up[3])))
    check("M1 three more connections come up, each with its own two ids",
          ups == [("0x0", hexs(struct.pack(">II", rx, e.rx_p))) for e, rx in pairs], ups)
    n0 = len(rig.frames)
    st = request(t, ch, fifth.addr, 0x30F)
    time.sleep(0.4)
    sent = [d for _t, cid, d in rig.frames[n0:] if cid == TESTER_ID and d[:2] == bytes([fifth.addr, 0xC0])]
    check("M2 a fifth is refused at once and nothing goes out for it", st != 0 and not sent, (hex(st), len(sent)))
    got = []
    for e, rx in [(first, TESTER_RX)] + pairs:
        st, ans = ask(t, ch, b"\x1A\x9B", 3.0, e.rx_p, rx)
        got.append(st == 0 and ans is not None and ans[:2] == b"\x5A\x9B" and len(ans) == 42)
    check("M3 a request to each of the four is answered on its own ids", all(got), got)
    n0 = [len(e.tests) for e in [first] + more]
    time.sleep(3.4)
    counts = [len(e.tests) - n for e, n in zip([first] + more, n0)]
    check("M4 3.4 s without traffic: every connection is tested about once a second (2 to 4 each), none lost",
          all(2 <= c <= 4 for c in counts) and not t.lost(), (counts, t.lost()))
    d0 = [len(e.disconnects) for e in more]
    lost = []
    for e, rx in pairs:
        st = teardown(t, ch, rx)
        r = t.wait_rx(lambda x: x[2] & RX_LOST, 2.0)
        lost.append((hex(st), None if r is None else hexs(r[3])))
    check("M5 the three are torn down: CONNECTION_LOST with each RX id and reason 0, each ECU saw its disconnect",
          lost == [("0x0", hexs(struct.pack(">I", rx) + b"\x00")) for _e, rx in pairs]
          and [len(e.disconnects) - n for e, n in zip(more, d0)] == [1, 1, 1], lost)
    st, ans = ask(t, ch, b"\x3E")
    check("M6 the first connection is still there", st == 0 and ans == b"\x7E", (hex(st), None if ans is None else hexs(ans)))


def leg_n(t, rig, ch, fast, dut, n):
    print("== N: the largest message at full speed, counted", flush=True)
    rx = 0x301
    st = request(t, ch, fast.addr, rx)
    up = t.wait_rx(lambda r: r[2] & RX_UP, 2.0)
    if not check("N0 a connection to an ECU that asks for no gap between telegrams (T3 = 0)", st == 0 and up is not None, hex(st)):
        return
    c0 = dut.can(*LOSS_COUNTERS, "tx", "rx")
    f0 = len(rig.frames)
    ok, t0 = 0, time.perf_counter()
    for i in range(n):
        big = b"\x36" + pattern(i, BIG - 1)
        st, ans = ask(t, ch, big, 15.0, fast.rx_p, rx)
        if st == 0 and fast.msgs[-1:] == [big] and ans == b"\x76" + struct.pack(">H", BIG - 1) + bytes([sum(big[1:]) & 0xFF]):
            ok += 1
    dt_w = time.perf_counter() - t0
    f_w = len([1 for _t, cid, _d in rig.frames[f0:] if cid == fast.rx_p])
    check("N1 %d messages of %d bytes written: every byte arrived (%.0f B/s, %d telegrams, %.0f per second)" % (
        n, MAX_MSG, n * MAX_MSG / dt_w, f_w, f_w / dt_w), ok == n, "%d of %d" % (ok, n))
    ok, t0 = 0, time.perf_counter()
    for i in range(n):
        st, ans = ask(t, ch, bytes([0x22, i]), 15.0, fast.rx_p, rx)
        if st == 0 and ans == b"\x62" + bytes([i]) + pattern(i, BIG - 2):
            ok += 1
    dt_r = time.perf_counter() - t0
    check("N2 %d messages of %d bytes read: every byte arrived (%.0f B/s)" % (n, MAX_MSG, n * MAX_MSG / dt_r), ok == n,
          "%d of %d" % (ok, n))
    c1 = dut.can(*LOSS_COUNTERS, "tx", "rx")
    frames = n * ((MAX_MSG + 6) // 7)       # the data telegrams of one direction
    check("N3 the CAN node lost nothing: no loss counter moved, and it sent and received at least the %d data telegrams" % frames,
          all(c1.get(k) == c0.get(k) for k in LOSS_COUNTERS) and (c1.get("tx") or 0) - (c0.get("tx") or 0) >= frames
          and (c1.get("rx") or 0) - (c0.get("rx") or 0) >= frames,
          {k: (c0.get(k), c1.get(k)) for k in c0 if c0.get(k) != c1.get(k)})
    st = teardown(t, ch, rx)
    r = t.wait_rx(lambda x: x[2] & RX_LOST, 2.0)
    check("N4 the connection is torn down after it", st == 0 and r is not None, hex(st))


def leg_o(t, ch):
    print("== O: beside an ISO15765 channel", flush=True)
    st, body = t.call(b.CONNECT, payload=struct.pack("<IIIII", b.PROT_ISO15765, 0, 500000, 0, 0))
    ch2 = struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else 0
    mask = any_msg(b.PROT_ISO15765, b"\xFF\xFF\xFF\xFF", b.TX_PAD)
    patt = any_msg(b.PROT_ISO15765, struct.pack(">I", 0x7E8), b.TX_PAD)
    flow = any_msg(b.PROT_ISO15765, struct.pack(">I", 0x7E0), b.TX_PAD)
    st2, _ = t.call(START_FILTER, ch2, struct.pack("<I", FLOW_CONTROL_FILTER) + mask + patt + flow)
    check("O1 an ISO15765 channel opens beside the TP2.0 one, with its flow control filter", st == 0 and ch2 not in (0, ch)
          and st2 == 0, (hex(st), ch2, hex(st2)))
    st = t.call(b.WRITE_MSGS, ch2, struct.pack("<I", 1) + any_msg(b.PROT_ISO15765, struct.pack(">I", 0x7E0) + b"\x10\x03", b.TX_PAD))[0]
    r = t.wait_rx(lambda x: x[1] == ch2 and x[3][:6] == b"\x00\x00\x07\xE8\x50\x03", 2.0)
    check("O2 UDS 10 03 on it is answered by the simulator (50 03 behind 7E8)", st == 0 and r is not None, hex(st))
    st, ans = ask(t, ch, b"\x1A\x9B")
    check("O3 the TP2.0 connection answers with the other channel open", st == 0 and ans is not None and len(ans) == 42, hex(st))
    st, _ = t.call(b.DISCONNECT, channel=ch2)
    st2, ans = ask(t, ch, b"\x3E")
    check("O4 the ISO15765 channel closes, the TP2.0 connection stays", st == 0 and st2 == 0 and ans == b"\x7E" and not t.lost(),
          (hex(st), hex(st2), t.lost()))


def leg_g(t, rig, ch, e):
    print("== G: a lost telegram, a receiver that is not ready, a break", flush=True)
    rx = 0x302
    big = b"\x36" + bytes((i * 5 + 9) & 0xFF for i in range(600))        # 86 telegrams, six blocks
    good = b"\x76" + struct.pack(">H", 600) + bytes([sum(big[1:]) & 0xFF])

    def fresh():
        teardown(t, ch, rx)                  # whatever the case before left behind
        t.wait_rx(lambda r: r[2] & RX_LOST, 0.3)
        drop_lost(t)
        st = request(t, ch, e.addr, rx)
        return st == 0 and t.wait_rx(lambda r: r[2] & RX_UP, 2.0) is not None

    def case(name, fault, kwp=big, want=good, secs=8.0):
        if not fresh():
            check(name, False, "no connection")
            return False
        e.fault, e.seen = fault, []
        n_err = len(rig.errors)
        st, ans = ask(t, ch, kwp, secs, e.rx_p, rx)
        return check(name, st == 0 and ans == want and not t.lost() and rig.errors[n_err:] == [] and e.fault is None,
                     (hex(st), None if ans is None else len(ans), t.lost(), rig.errors[n_err:][:2], e.fault))

    case("G1 a telegram lost inside a block: the ECU names it at the block end, the device sends again from it "
         "and the message arrives whole", ("drop_rx", 3))
    case("G2 the block-end telegram lost: no acknowledge comes, the device sends it again after T1", ("drop_rx", 14))
    if case("G3 receiver not ready at a block end (J2819 6.2.4): the message arrives whole", ("not_ready",)):
        ends = [i for i, (_x, b0) in enumerate(e.seen) if (b0 >> 4) == 0x0]
        gap = (e.seen[ends[0] + 1][0] - e.seen[ends[0]][0]) * 1000.0 if ends and ends[0] + 1 < len(e.seen) else 0
        check("G4 the device waited T_WAIT (100 ms) before it went on", 90 <= gap <= 250, round(gap, 1))
    case("G5 not ready and a telegram to send again (J2819 6.2.6)", ("drop_rx", 5, "not_ready"))
    case("G6 a telegram of the ECU's answer lost: the device names it in its acknowledge, the ECU sends the block "
         "again and the 4092 bytes arrive whole", ("skip_tx", 20), bytes([0x22, 7]), b"\x62\x07" + pattern(7, BIG - 2), 15.0)
    if fresh():
        e.fault, e.seen = ("break", 20), []
        n_err, b0 = len(rig.errors), e.broken
        st, ans = ask(t, ch, big, 4.0, e.rx_p, rx)
        tail = e.seen[-1][1] >> 4 if e.seen else None
        check("G7 a break from the ECU (J2819 6.2.7): the device ends the message with an end-of-message telegram that "
              "asks for an acknowledge, and the write returns an error",
              st != 0 and ans is None and e.broken == b0 + 1 and tail == 0x1 and len(e.seen) < 60 and rig.errors[n_err:] == [],
              (hex(st), ans is None, e.broken - b0, tail, len(e.seen), rig.errors[n_err:][:2]))
        st, ans = ask(t, ch, b"\x3E", 3.0, e.rx_p, rx)
        check("G8 the connection is kept after the break", st == 0 and ans == b"\x7E" and not t.lost(), (hex(st), t.lost()))
    else:
        check("G7 a break from the ECU", False, "no connection")
    teardown(t, ch, rx)
    t.wait_rx(lambda r: r[2] & RX_LOST, 0.5)
    drop_lost(t)
    st, ans = ask(t, ch, b"\x3E")
    check("G9 the first connection went through all of it", st == 0 and ans == b"\x7E", hex(st))


def leg_b(t, ecu, ch):
    print("== B: a broadcast", flush=True)
    n0 = len(ecu.bcast)
    tel = bytes([0xF0, 0x23, 0x31, 0xB8, 0x00, 0x00, 0x00])
    st = write(t, ch, struct.pack(">I", TESTER_ID) + tel, TX_BROADCAST)
    time.sleep(0.3)
    got = ecu.bcast[n0:]
    check("B1 the write is accepted and five telegrams went out on 0x200", st == 0 and len(got) == 5, (hex(st), len(got)))
    check("B2 the request bytes are kept (F0 23 31 B8 00)", bool(got) and all(d[:5] == tel[:5] and len(d) == 7 for _t, d in got),
          [hexs(d) for _t, d in got][:2])
    keys = [hexs(d[5:7]) for _t, d in got]
    check("B3 the key: 55 55, AA AA, 55 55, AA AA, 55 55", keys == ["55 55", "AA AA", "55 55", "AA AA", "55 55"], keys)
    g = gaps_ms([x for x, _d in got])
    check("B4 the telegrams are T_BR_INT (20 ms) apart", len(g) == 4 and all(15 <= x <= 30 for x in g), g)
    st = write(t, ch, struct.pack(">I", TESTER_ID) + bytes([0x01, 0x23, 0x31, 0xB8, 0x00, 0x00, 0x00]), TX_BROADCAST)
    check("B5 the broadcast flag on address 0x01 is ERR_INVALID_MSG", st == ERR_INVALID_MSG, hex(st))


def leg_r(t, ecu, ch):
    print("== R: the re-triggered broadcast", flush=True)
    n0 = len(ecu.bcast)
    tel = bytes([0xF1, 0x23, 0x31, 0xB9, 0x01, 0x00, 0x00])
    st, body = t.call(START_PERIODIC, ch, struct.pack("<I", 300) + any_msg(TP20, struct.pack(">I", TESTER_ID) + tel, TX_BROADCAST))
    pid = struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else 0
    time.sleep(1.45)
    st2, _ = t.call(STOP_PERIODIC, ch, struct.pack("<I", pid))
    n_stop = len(ecu.bcast)
    time.sleep(0.7)
    got = [(x, d) for x, d in ecu.bcast[n0:] if d[0] == 0xF1]
    check("R1 START_PERIODIC and STOP_PERIODIC accepted", st == 0 and st2 == 0 and pid != 0, (hex(st), hex(st2), pid))
    g = gaps_ms([x for x, _d in got])
    check("R2 the five first (20 ms apart), then ONE telegram per 300 ms period (8 to 10 in 1.45 s)",
          8 <= len(got) <= 10 and all(15 <= x <= 30 for x in g[:4]) and all(250 <= x <= 350 for x in g[5:]), (len(got), g))
    keys = [hexs(d[5:7]) for _t, d in got]
    check("R3 the key keeps alternating over the whole run, 55 55 first",
          bool(keys) and keys == [("55 55", "AA AA")[i & 1] for i in range(len(keys))], keys)
    check("R4 nothing more after STOP_PERIODIC", len(ecu.bcast) == n_stop, len(ecu.bcast) - n_stop)


def leg_e(t, ecu, ch):
    print("== E: an id without a connection", flush=True)
    st = write(t, ch, struct.pack(">I", 0x7AA) + bytes(range(9)))
    check("E1 a 9-byte message to 0x7AA: ERR_NO_CONNECTION_ESTABLISHED 0x00010001", st == ERR_NO_CONNECTION, hex(st))
    n0 = len(ecu.frames)
    st = write(t, ch, struct.pack(">I", 0x7AA) + b"\x01\x02\x03")
    time.sleep(0.2)
    seen = [d for _t, cid, d in ecu.frames[n0:] if cid == 0x7AA]
    check("E2 a 3-byte message to 0x7AA goes out as one CAN frame", st == 0 and seen == [b"\x01\x02\x03"], (hex(st), [hexs(d) for d in seen]))


def leg_f(t, ecu, ch):
    print("== F: a request nobody answers", flush=True)
    drop_lost(t)
    n0 = len(ecu.frames)
    st = request(t, ch, 0x05, 0x310)
    lost = t.wait_rx(lambda r: r[2] & RX_LOST, 3.0)
    time.sleep(0.3)
    tries = [x for x, cid, d in ecu.frames[n0:] if cid == TESTER_ID and d[:2] == b"\x05\xC0"]
    check("F1 the request is accepted and repeated (T_E 100 ms apart, 9 to 11 times)", st == 0 and 9 <= len(tries) <= 11 and
          all(80 <= x <= 130 for x in gaps_ms(tries)), (hex(st), len(tries), gaps_ms(tries)[:4]))
    check("F2 CONNECTION_LOST carries the RX id of the request (0x310) and reason 1",
          lost is not None and lost[3] == struct.pack(">I", 0x310) + b"\x01", None if lost is None else hexs(lost[3]))


def leg_q(t, ecu, ch, secs):
    print("== Q: %d s without traffic" % secs, flush=True)
    n0, d0 = len(ecu.tests), len(ecu.disconnects)
    time.sleep(secs)
    tests = ecu.tests[n0:]
    g = gaps_ms(tests)
    check("Q1 a test about every second all the way (%d to %d tests, none more than 1.3 s after the one before)" % (secs - 2, secs + 2),
          secs - 2 <= len(tests) <= secs + 2 and all(x <= 1300 for x in g), (len(tests), max(g) if g else None))
    st, ans = ask(t, ch, b"\x3E")
    check("Q2 the connection is kept and answers", not t.lost() and len(ecu.disconnects) == d0 and st == 0 and ans == b"\x7E",
          (t.lost(), hex(st)))


def leg_x(t, ecu, ch):
    print("== X: the teardown", flush=True)
    drop_lost(t)
    d0 = len(ecu.disconnects)
    st = teardown(t, ch, TESTER_RX)
    lost = t.wait_rx(lambda r: r[2] & RX_LOST, 2.0)
    check("X1 TEARDOWN_CONNECTION accepted, the ECU saw the disconnect (A8)", st == 0 and len(ecu.disconnects) == d0 + 1,
          (hex(st), len(ecu.disconnects) - d0))
    check("X2 CONNECTION_LOST with the RX id 0x300 and reason 0", lost is not None and lost[3] == struct.pack(">I", TESTER_RX) + b"\x00",
          None if lost is None else hexs(lost[3]))
    check("X3 the ECUs saw nothing J2819 does not allow", not ecu.errors, ecu.errors[:4])


def leg_l(py32, jh, jp, dll, ecu):
    print("== L: the driver DLL called like an application", flush=True)
    if not dll:
        print("SKIP: no --dll (a driver before 1.8 does not serve TP2_0_PS: pass the built wican_j2534.dll)", flush=True)
        return
    if not os.path.exists(dll) or not py32 or not os.path.exists(py32):
        check("L0 the DLL and the 32-bit Python are there", False, (dll, py32))
        return
    env = dict(os.environ, WICAN_J2534_HOST=jh if jh != "localhost" else "127.0.0.1", WICAN_J2534_PORT=str(jp),
               WICAN_J2534_DLL=dll)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "j2534_tp20_ps_dll_check.py")
    n_err = len(ecu.errors)
    try:
        p = subprocess.run([py32, "-I", script], env=env, capture_output=True, text=True, timeout=60)
        out = p.stdout + p.stderr
    except Exception as e:
        out, p = "%s: %s" % (type(e).__name__, e), None
    for ln in out.rstrip().splitlines():
        print("   " + ln, flush=True)
    check("L1 every call through the DLL did what the standard says (%d steps)" % out.count("OK: "),
          p is not None and p.returncode == 0 and "DLL CHECK DONE" in out and "BAD: " not in out,
          "exit %s" % (p.returncode if p else "none"))
    check("L2 the ECU saw nothing J2819 does not allow", ecu.errors[n_err:] == [], ecu.errors[n_err:][:4])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--j2534", default="localhost:18534")
    ap.add_argument("--pcan", default=os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"))
    ap.add_argument("--py32", default=r"C:\Users\Ali\idf_tmp\py32\python.exe")
    ap.add_argument("--dll", default=None, help="the built wican_j2534.dll for leg L (the DLL called like an application)")
    ap.add_argument("--big", type=int, default=10, help="messages of 4092 bytes each way in leg N")
    ap.add_argument("--soak", type=int, default=0, help="seconds without traffic in leg Q (0 = the leg is left out)")
    ap.add_argument("--keep", action="store_true", help="leave the settings as the run set them (a second run follows)")
    a = ap.parse_args()

    dut = ab.Dut(a.dut)
    jh, jp = a.j2534.split(":")[0], int(a.j2534.split(":")[1])
    t0 = time.time()
    planned = 0

    st = dut.get("/api/status")
    if not st:
        print("J2534 TP20 FAIL: no DUT at", a.dut)
        sys.exit(1)
    boot0, unexp0 = st.get("boot_count"), st.get("unexpected_resets")
    err0 = (st.get("health") or {}).get("log_errors")
    print("dut: %s boot_count=%s unexpected=%s log_errors=%s" % (st.get("version"), boot0, unexp0, err0), flush=True)
    found = {c: dut.settings(c) for c in ("j2534_server",)}
    groups = (dut.get("/api/autopid") or {}).get("groups", [])
    group_on = [g["name"] for g in groups if g.get("enabled")]
    try:
        rig = Rig(a.pcan)
    except Exception as e:
        print("J2534 TP20 FAIL: PCAN %s not usable: %s" % (a.pcan, e))
        sys.exit(1)
    ecu = Ecu(rig)                                   # the engine ECU: 0x201, receives on 0x740
    fast = Ecu(rig, 0x02, 0x741, t3_wire=0x00)       # asks for no gap between telegrams
    more = [fast, Ecu(rig, 0x03, 0x742), Ecu(rig, 0x04, 0x743)]
    fifth = Ecu(rig, 0x06, 0x745)
    rig.start()

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
                print("J2534 TP20 FAIL: the DUT did not come back (%s)" % why)
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
        t = Tester(jh if jh != "localhost" else "127.0.0.1", jp)
        t.call(b.HELLO, payload=struct.pack("<I", 1))
        t.call(b.OPEN)
        ch = leg_s(t)
        leg_i(t)
        leg_k(t, ecu, ch)
        leg_j(t, rig, ecu, ch)
        leg_d(t, ecu, ch)
        leg_w(t, rig, ecu, ch)
        leg_t(t, ecu, ch)
        leg_m(t, rig, ch, ecu, more, fifth)
        leg_n(t, rig, ch, fast, dut, a.big)
        leg_o(t, ch)
        leg_g(t, rig, ch, more[1])
        leg_b(t, ecu, ch)
        leg_r(t, ecu, ch)
        leg_e(t, ecu, ch)
        leg_f(t, ecu, ch)
        if a.soak > 0:
            leg_q(t, ecu, ch, a.soak)
        leg_x(t, ecu, ch)
        t.call(b.DISCONNECT, channel=ch)
        t.call(b.CLOSE)
    except Exception as e:
        check("the legs ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        if t is not None:
            t.close()
        time.sleep(0.5)
    leg_l(a.py32, jh, jp, a.dll, ecu)

    print("== Z: restore", flush=True)
    if a.keep:
        print("--keep: the settings stay as the run set them", flush=True)
    else:
        submit(found, "the settings as found")
        back = {k: dut.settings(k) for k in found}
        check("Z1 settings back as found", back == found, {k: "same" if back[k] == found[k] else "DIFFERS" for k in found})
    pause_autopid(True)
    st = dut.get("/api/status") or {}
    check("Z2 only the planned restarts (%d)" % planned, st.get("boot_count") == boot0 + planned and st.get("unexpected_resets") == unexp0,
          {"boot_count": (boot0, st.get("boot_count")), "unexpected": (unexp0, st.get("unexpected_resets"))})
    err1 = (st.get("health") or {}).get("log_errors")
    check("Z3 no new E lines", err1 == err0 or err1 == 0, (err0, err1))
    rig.close()

    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t0), flush=True)
    if fails:
        print("J2534 TP20 FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("J2534 TP20 PASS")


if __name__ == "__main__":
    main()
