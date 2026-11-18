#!/usr/bin/env python3
"""A J2534 session when something goes wrong beside it  ->  J2534 SESSION PASS

Five things a J2534 tool meets outside the happy path, each one a defect found by reading
the server's code on 2026-11-18 and shown here on the bench before it was fixed:

  the PC vanishes       a session whose peer disappears without closing (a pulled cable, a
                        laptop asleep) was kept until the unit restarted: no tool could
                        connect again. The unit now ends it by itself: TCP keepalive (idle
                        10 s, a probe every 5 s, 3 probes) and a send timeout of 5 s.
  messages are lost     a channel's receive queue that runs over dropped messages and the
                        tool was never told. A client that speaks wire version 2 gets an
                        EVENT frame (RX_LOST, the count) in front of the first message
                        after the loss, which the Windows driver turns into SAE J2534-1's
                        ERR_BUFFER_OVERFLOW.
  a long ISO 15765 write  held the one channel lock for seconds: nothing received on any
                        channel reached the tool meanwhile (and what did not fit the queue
                        was lost), and a periodic message waited inside the esp_timer task,
                        which serves every software timer of the unit.
  the return codes      from 0x14 up were one too low against SAE J2534-1 (and
                        ERR_DEVICE_IN_USE sat at 0x1A instead of 0x0E). A client that asks
                        for wire version 2 in its HELLO gets the standard's numbers; one
                        that does not gets the numbers it always got.

The PCAN plays an ECU that takes a long request slowly (its flow control asks for a gap),
counts the periodic messages on the bus and makes the traffic. The legs that need a client
next to the unit (a connection whose packets are dropped, a client with a small receive
buffer that stops reading) run on the Pi: j2534_session_pi.py, copied there and driven
through ssh.

Legs (one planned restart to configure, one to restore, when the J2534 server is off):
  C  configure: j2534 on with allow_lan, autopid's groups paused
  A  a second connection while a session is alive gets no session and disturbs nothing;
     when the first one closes, the next is served
  N  the return codes by wire version: version 1 (and a HELLO without a version) keeps
     0x18 / 0x16 / 0x15, version 2 answers SAE's 0x19 / 0x17 / 0x16. First (N0) the unit's
     CAN node must be at a proven 500 kbit/s: a CONNECT at another bit rate is one of the
     refusals, and a node on `auto` with nothing proven would take it instead
  L  a long ISO 15765 write (about 2 s, the ECU's STmin is 10 ms) with two periodic
     messages running and 500 frames arriving on a raw CAN channel meanwhile: every frame
     arrives, while the write runs; both periodic messages keep their period, and none
     of them piles up behind the write. A failed write prints how far the ECU got, the
     unit's CAN and ISO-TP counters that moved and its log lines
  P  a periodic message ends with its channel: DISCONNECT stops it, it does not come
     back on the next channel that is connected, and its id is gone (SAE J2534-1 7.3.6)
  O  overflow: a client stops reading for 2.5 s while 2400 frames arrive; it then reads
     what is left and must have been told where frames are missing and how many
  K  a session that is alive and silent for 32 s keeps its place (the keepalive probes are
     answered by the peer's TCP, the tool sends nothing)
  V  the peer vanishes: an idle session is given up after about 25 s (V1), one the unit is
     sending to within 15 s (V3, the send timeout); a new connection made at the moment
     the peer vanished is served then, and a fresh one after it (V2, V4)
  D  the driver DLL (--dll, the 32-bit Python): j2534_session_dll_check.py against fake
     devices of both wire versions (the numbers an application sees, ERR_BUFFER_OVERFLOW)
     and against the unit
  X  restore: settings as found, only the planned restarts, no new E lines

usage (PC, PCAN wired to the DUT's bus, the tunnel of ports 80 and 6809 to the DUT):
  python j2534_session_bench.py --dut localhost:18081 --j2534 localhost:18534
       --pi rpi001 --dut-ip 10.42.1.41 [--pcan PCAN_USBBUS1]
       [--dll <built wican_j2534.dll>] [--old-dll <a driver before 1.10>] [--py32 <32-bit python>]
       [--legs ANLPOKVD] (a subset) [--repeat N] (the chosen legs N times) [--keep] (leave the settings)
"""
import argparse
import json
import os
import queue
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
import j2534_tp20_ps_bench as tp  # noqa: E402  (Tester, any_msg, pattern)
sys.argv = _argv

START_FILTER, STOP_FILTER, START_PERIODIC, STOP_PERIODIC, IOCTL, EVENT = 0x11, 0x12, 0x13, 0x14, 0x15, 0x82
PASS_FILTER, FLOW_CONTROL_FILTER = 1, 3
READ_VBATT = 0x03
EVENT_RX_LOST = 1
ECU_LONG = 0x6F2            # the ECU that takes a long request; it answers on the id + 8
FUNC_ID = 0x6F0             # leg L's periodic message on the ISO15765 channel: an id with no session
PER_ID = 0x555              # leg L's periodic message on the raw CAN channel
GONE_ID = 0x557             # leg P's periodic message, on a channel that is then disconnected
FLOOD_ID = 0x444            # frames with a 32-bit counter in bytes 0..3
NOISE_ID = 0x446            # leg V2's traffic
# what a refused request is answered with, by wire version: a CONNECT at a bit rate the bus does not run at
# (ERR_INVALID_BAUDRATE), a long ISO 15765 message to an id without a flow control filter
# (ERR_NO_FLOW_CONTROL), STOP_FILTER of an id that is not there (ERR_INVALID_FILTER_ID)
CODES = {1: (0x18, 0x16, 0x15), 2: (0x19, 0x17, 0x16)}
PI_TABLE = "j2534_session_bench"

fails, passes = [], []
hexs = tp.hexs


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


class Tester(tp.Tester):
    """The wire client of the other J2534 benches, with the EVENT frames kept."""

    def __init__(self, host, port):
        self.events = []        # (t, channel, event, value)
        super().__init__(host, port)

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
                    elif rt == EVENT and len(body) >= 8:
                        self.events.append((time.perf_counter(), rch) + struct.unpack_from("<II", body, 0))
                    self.cv.notify_all()
        except Exception as e:
            with self.cv:
                self.dead = e
                self.cv.notify_all()

    def hello(self, version=None):
        """HELLO, with the wire version asked for (None: no payload, as the first clients sent it)."""
        st, body = self.call(b.HELLO, payload=b"" if version is None else struct.pack("<I", version))
        return st, struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else None


class Bus(threading.Thread):
    """The PCAN: the ECU of the long request, the witness of the periodic messages, the traffic."""

    def __init__(self, channel):
        super().__init__(daemon=True)
        self.bus = can.Bus(interface="pcan", channel=channel, bitrate=500000, receive_own_messages=False)
        while self.bus.recv(timeout=0):
            pass
        self.halt = threading.Event()
        self.tx = threading.Lock()
        self.stmin = 10                 # the gap the ECU asks for in its flow control, ms
        self.seen = {}                  # can id -> [bus timestamp, ...]
        self.long = None                # the long request under way: {"total", "buf", "t0", "t1"}
        self.longs = []                 # finished ones
        self.errors = []

    def run(self):
        while not self.halt.is_set():
            m = self.bus.recv(timeout=0.05)
            if m is None or m.is_error_frame or m.is_extended_id or m.is_remote_frame:
                continue
            cid, d = m.arbitration_id, bytes(m.data)
            self.seen.setdefault(cid, []).append(m.timestamp)
            if cid != ECU_LONG or not d:
                continue
            kind = d[0] >> 4
            if kind == 1:
                self.long = {"total": ((d[0] & 0x0F) << 8) | d[1], "buf": bytearray(d[2:8]), "t0": m.timestamp, "t1": m.timestamp}
                self.send(ECU_LONG + 8, bytes([0x30, 0x00, self.stmin]))
            elif kind == 2 and self.long is not None:
                self.long["buf"] += d[1:8]
                self.long["t1"] = m.timestamp
                if len(self.long["buf"]) >= self.long["total"]:
                    self.long["buf"] = bytes(self.long["buf"][:self.long["total"]])
                    self.longs.append(self.long)
                    self.long = None

    def send(self, can_id, data):
        frame = can.Message(arbitration_id=can_id, is_extended_id=False, data=bytes(data).ljust(8, b"\x55"))
        end = time.perf_counter() + 1.0
        while True:
            try:
                with self.tx:
                    self.bus.send(frame, timeout=0.2)
                return True
            except can.CanError as e:          # the adapter's transmit queue is full: it is sending
                if time.perf_counter() > end:
                    self.errors.append("the PCAN could not send: %s" % e)
                    return False
                time.sleep(0.001)

    def flood(self, can_id, n, gap_s, sent_at=None):
        """n frames with a counter, gap_s apart. sent_at (a list) receives the time each one was handed over."""
        nxt = time.perf_counter()
        for i in range(n):
            while time.perf_counter() < nxt:
                if nxt - time.perf_counter() > 0.002:
                    time.sleep(0.001)
            if not self.send(can_id, struct.pack(">I", i) + b"\xF1\x00\x0D\x00"):
                return i
            if sent_at is not None:
                sent_at.append(time.perf_counter())
            nxt += gap_s
        return n

    def close(self):
        self.halt.set()
        self.join(2.0)
        self.bus.shutdown()


class Pi:
    """j2534_session_pi.py on the Pi, driven through ssh: one JSON line per command and per event."""

    def __init__(self, host, dut_ip, port):
        self.host = host
        here = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", os.path.join(here, "j2534_session_pi.py"),
                        "%s:/tmp/j2534_session_pi.py" % host], check=True, timeout=40)
        self.p = subprocess.Popen(["ssh", "-o", "BatchMode=yes", host, "python3", "-u", "/tmp/j2534_session_pi.py", dut_ip, str(port)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.q = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        if self.wait("ready", 20) is None:
            raise RuntimeError("the helper on %s did not start" % host)

    def _read(self):
        for line in self.p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.q.put(json.loads(line))
            except ValueError:
                self.q.put({"ev": "text", "text": line})
        self.q.put({"ev": "eof"})

    def cmd(self, **c):
        self.p.stdin.write(json.dumps(c) + "\n")
        self.p.stdin.flush()

    def wait(self, ev, timeout):
        """The next event named ev (others are printed), None after timeout or when the helper ended."""
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                return None
            try:
                e = self.q.get(timeout=left)
            except queue.Empty:
                return None
            if e.get("ev") == ev:
                return e
            if e.get("ev") == "eof":
                return None
            print("   pi: %s" % json.dumps(e), flush=True)

    def close(self):
        try:
            self.cmd(cmd="quit")
            self.p.wait(8)
        except Exception:
            self.p.kill()
        subprocess.run(["ssh", "-o", "BatchMode=yes", self.host,
                        "sudo -n nft delete table inet %s 2>/dev/null; rm -f /tmp/j2534_session_pi.py" % PI_TABLE], timeout=30)


def iso_msg(data, tx_flags=b.TX_PAD):
    return tp.any_msg(b.PROT_ISO15765, data, tx_flags)


def connect(t, proto, baud=500000):
    st, body = t.call(b.CONNECT, payload=struct.pack("<IIIII", proto, 0, baud, 0, 0))
    return st, struct.unpack_from("<I", body, 0)[0] if st == 0 and len(body) >= 4 else 0


def pass_filter(t, ch, proto, can_id):
    m = tp.any_msg(proto, b"\xFF\xFF\xFF\xFF"), tp.any_msg(proto, struct.pack(">I", can_id))
    return t.call(START_FILTER, ch, struct.pack("<I", PASS_FILTER) + m[0] + m[1])[0]


def fc_filter(t, ch, req_id):
    mask, patt, flow = iso_msg(b"\xFF\xFF\xFF\xFF"), iso_msg(struct.pack(">I", req_id + 8)), iso_msg(struct.pack(">I", req_id))
    return t.call(START_FILTER, ch, struct.pack("<I", FLOW_CONTROL_FILTER) + mask + patt + flow)[0]


def periodic(t, ch, interval_ms, msg):
    st, body = t.call(START_PERIODIC, ch, struct.pack("<I", interval_ms) + msg)
    return st, struct.unpack_from("<I", body, 0)[0] if st == 0 and len(body) >= 4 else 0


def session(host, port, version=1):
    t = Tester(host, port)
    st, ver = t.hello(version)
    st2 = t.call(b.OPEN)[0]
    if st != 0 or st2 != 0:
        t.close()
        raise RuntimeError("no session: HELLO 0x%X, OPEN 0x%X" % (st, st2))
    return t, ver


def served_within(host, port, secs):
    """A new connection's HELLO answered within secs -> the seconds it took, else None."""
    t0 = time.time()
    try:
        t = Tester(host, port)
    except OSError:
        return None
    try:
        t.call(b.HELLO, payload=struct.pack("<I", 1), timeout=secs)
        took = time.time() - t0
        try:
            t.call(b.CLOSE, timeout=3)
        except Exception:
            pass
        return took
    except Exception:
        return None
    finally:
        t.close()


def leg_a(dut, host, port):
    print("== A: a second connection while a session is alive", flush=True)
    t, _v = session(host, port)
    st, ch = connect(t, b.PROT_CAN)
    s2 = socket.create_connection((host, port), timeout=5)
    try:
        s2.sendall(b.HDR.pack(b.MAGIC, 1, b.HELLO, 1, 0, 4) + struct.pack("<I", 1))
        s2.settimeout(3.0)
        try:
            got = s2.recv(64)
        except (socket.timeout, TimeoutError):
            got = None
        refused = not got or (len(got) >= 16 and got[3] == b.ACK and struct.unpack_from("<I", got, 12)[0] != 0)
        check("A1 a second connection gets no session while the first is alive (its HELLO is not answered with NOERROR)",
              refused, "nothing in 3 s" if got is None else "closed" if not got else hexs(got[:20]))
        st2, body = t.call(IOCTL, 0, struct.pack("<I", READ_VBATT))
        j = dut.get("/api/j2534") or {}
        check("A2 the first session is undisturbed: its request is answered, its channel is still open",
              st == 0 and st2 == 0 and len(body) >= 4 and j.get("client_connected") is True and j.get("channels") == 1,
              {"read_vbatt": hex(st2), "client_connected": j.get("client_connected"), "channels": j.get("channels")})
    finally:
        s2.close()
    t.call(b.CLOSE)
    t.close()
    took = served_within(host, port, 8.0)
    check("A3 when the first session closes, the next connection is served", took is not None,
          "no HELLO answer in 8 s" if took is None else "%.1f s" % took)


def bus_proven(dut, bus):
    """True when a CONNECT at another bit rate will be REFUSED: the unit's CAN node runs at a fixed 500 kbit/s, or
    it has read frames at 500. With `baud = auto` and nothing read yet the unit would TAKE the tester's bit rate
    on a silent bus (and then sit at 250 kbit/s on this bench's 500 kbit/s bus), so the PCAN talks first."""
    for _ in range(3):
        c = dut.can("running", "baud_auto", "baud_kbps", "verified", "adopted", "state")
        if c.get("running") and c.get("baud_kbps") == 500 and (not c.get("baud_auto") or c.get("verified")):
            return True
        for i in range(20):
            bus.send(NOISE_ID, struct.pack(">I", i) + b"\x00\x00\x00\x00")
            time.sleep(0.01)
        time.sleep(0.5)
    print("   the CAN node: %s" % c, flush=True)
    return False


def codes_of(host, port, version):
    """One session at a wire version -> (the version the HELLO answered, the three refusals)."""
    t = Tester(host, port)
    try:
        _st, ver = t.hello(version)
        t.call(b.OPEN)
        baud = connect(t, b.PROT_CAN, 250000)[0]
        _st, ch = connect(t, b.PROT_ISO15765)
        no_fc = t.call(b.WRITE_MSGS, ch, struct.pack("<I", 1) + iso_msg(struct.pack(">I", 0x6F5) + bytes(20)))[0]
        filt = t.call(STOP_FILTER, ch, struct.pack("<I", 77))[0]
        t.call(b.DISCONNECT, channel=ch)
        t.call(b.CLOSE)
        return ver, (baud, no_fc, filt)
    finally:
        t.close()
        time.sleep(0.3)


def leg_n(dut, bus, host, port):
    print("== N: the return codes by wire version", flush=True)
    if not check("N0 the unit's CAN node is at a proven 500 kbit/s (else a CONNECT at 250000 would be taken, not refused)",
                 bus_proven(dut, bus)):
        return

    def hx(c):
        return "ERR_INVALID_BAUDRATE 0x%02X, ERR_NO_FLOW_CONTROL 0x%02X, ERR_INVALID_FILTER_ID 0x%02X" % c

    ver, got = codes_of(host, port, 1)
    check("N1 a client that asks for wire version 1 is answered 1 and keeps the numbers it always got (%s)" % hx(CODES[1]),
          ver == 1 and got == CODES[1], (ver, hx(got)))
    ver, got = codes_of(host, port, None)
    check("N2 a HELLO without a version (the first clients) is version 1 too", ver == 1 and got == CODES[1], (ver, hx(got)))
    ver, got = codes_of(host, port, 2)
    check("N3 a client that asks for wire version 2 is answered 2", ver == 2, ver)
    check("N4 and gets SAE J2534-1's numbers (%s)" % hx(CODES[2]), got == CODES[2], hx(got))
    ver, got = codes_of(host, port, 9)
    check("N5 a client that asks for a later version gets the unit's own, 2", ver == 2 and got == CODES[2], (ver, hx(got)))


def worst_gap(stamps, t0, t1):
    """The longest silence of a periodic message inside [t0, t1] (the edges count), and how many frames fell in it."""
    inside = [s for s in stamps if t0 <= s <= t1]
    edges = [t0] + inside + [t1]
    return max(b_ - a_ for a_, b_ in zip(edges, edges[1:])), len(inside)


def unit_counters(dut):
    """The unit's CAN counters and its ISO-TP provider's, for the note a failed transfer gets."""
    c = dut.can() or {}
    c.pop("subscribers", None)
    c.pop("probe", None)
    return c, ((dut.get("/api/uds") or {}).get("provider_stats") or {})


def moved(a, b):
    return {k: (a.get(k), b.get(k)) for k in b if a.get(k) != b.get(k) and not isinstance(b.get(k), dict)}


def leg_l(bus, dut, host, port):
    print("== L: a long ISO 15765 write, two periodic messages and traffic on a second channel", flush=True)
    n_req, n_flood, flood_gap = 1400, 500, 0.003
    t, _v = session(host, port)
    try:
        st, chi = connect(t, b.PROT_ISO15765)
        st2 = fc_filter(t, chi, ECU_LONG)
        st3, chc = connect(t, b.PROT_CAN)
        st4 = pass_filter(t, chc, b.PROT_CAN, FLOOD_ID)
        n1, n2 = len(bus.seen.get(FUNC_ID, [])), len(bus.seen.get(PER_ID, []))
        st5, p1 = periodic(t, chi, 50, iso_msg(struct.pack(">I", FUNC_ID) + b"\x3E\x80"))
        st6, p2 = periodic(t, chc, 20, tp.any_msg(b.PROT_CAN, struct.pack(">I", PER_ID) + b"\x01\x02\x03\x04\x05\x06\x07\x08"))
        time.sleep(1.2)
        n1, n2 = len(bus.seen.get(FUNC_ID, [])) - n1, len(bus.seen.get(PER_ID, [])) - n2
        check("L0 two channels, a flow control filter and two periodic messages (50 ms on the ISO15765 channel to an id "
              "without a session, 20 ms on the raw CAN channel) run", (st, st2, st3, st4, st5, st6) == (0,) * 6
              and 18 <= n1 <= 30 and 45 <= n2 <= 75, {"statuses": [hex(s) for s in (st, st2, st3, st4, st5, st6)],
                                                        "frames in 1.2 s": (n1, n2)})
        bus.stmin = 10
        sent_at, done = [], []
        fl = threading.Thread(target=lambda: (time.sleep(0.3), done.append(bus.flood(FLOOD_ID, n_flood, flood_gap, sent_at))), daemon=True)
        req = b"\x36" + tp.pattern(0x5A, n_req - 1)
        c0, p0 = unit_counters(dut)
        del t.rx[:]
        del bus.longs[:]
        w0 = time.perf_counter()
        fl.start()
        wst = t.call(b.WRITE_MSGS, chi, struct.pack("<I", 1) + iso_msg(struct.pack(">I", ECU_LONG) + req), timeout=20)[0]
        w1 = time.perf_counter()
        fl.join(10)
        time.sleep(1.5)
        lg = bus.longs[-1] if bus.longs else None
        if not check("L1 a request of %d bytes to an ECU whose flow control asks for 10 ms between frames is written whole "
                     "(the write lasts about 2 s)" % n_req, wst == 0 and lg is not None and lg["buf"] == req and w1 - w0 >= 1.5,
                     {"write": hex(wst), "write_s": round(w1 - w0, 2), "ecu_bytes": None if lg is None else len(lg["buf"])}):
            # what a failed transfer looked like from both ends
            part = bus.long
            c1, p1 = unit_counters(dut)
            print("   the ECU: %s" % ("never saw a first frame" if part is None and lg is None else "complete" if part is None
                                      else "%d of %d bytes, the last frame %.2f s after the first" % (
                                          len(part["buf"]), part["total"], part["t1"] - part["t0"])), flush=True)
            print("   the unit's CAN counters that moved: %s" % moved(c0, c1), flush=True)
            print("   the ISO-TP provider's counters that moved: %s" % moved(p0, p1), flush=True)
            for ln in [x for x in (dut.api("/api/logs/ring", raw=True) or "").splitlines()
                       if "isotp" in x or "j2534" in x or "can_core" in x][-10:]:
                print("   log: %s" % ln.strip()[:200], flush=True)
        with t.cv:
            got = [(at, struct.unpack_from(">I", d, 4)[0]) for at, c, s, d in t.rx
                   if c == chc and not s and len(d) >= 8 and d[:4] == struct.pack(">I", FLOOD_ID)]
        counters = [c for _a, c in got]
        gone = sorted(set(range(n_flood)) - set(counters))
        check("L2 the %d frames the bus carried meanwhile all arrived on the raw CAN channel, in order" % n_flood,
              done == [n_flood] and counters == list(range(n_flood)),
              {"sent": done, "arrived": len(counters), "missing": len(gone), "which": gone[:8], "in_order": counters == sorted(counters)})
        late = [at - sent_at[c] for at, c in got if c < len(sent_at)]
        in_write = sum(1 for at, _c in got if at <= w1)
        check("L3 and they arrived while the write ran: the slowest took under 0.5 s from the bus to the client",
              bool(late) and max(late) < 0.5, {"slowest_s": round(max(late), 2) if late else None,
                                                "arrived_before_the_write_ended": in_write, "of": len(got)})
        if lg is not None:
            g1, k1 = worst_gap(bus.seen.get(FUNC_ID, []), lg["t0"], lg["t1"])
            g2, k2 = worst_gap(bus.seen.get(PER_ID, []), lg["t0"], lg["t1"])
            span = lg["t1"] - lg["t0"]
        else:
            g1 = g2 = span = 99.0
            k1 = k2 = 0
        check("L4 the periodic message on the ISO15765 channel kept its 50 ms during the write (it goes to another id "
              "than the transfer)", g1 < 0.15, {"longest_silence_s": round(g1, 3), "frames": k1, "transfer_s": round(span, 2)})
        check("L5 the periodic message on the raw CAN channel kept its 20 ms during the write", g2 < 0.10,
              {"longest_silence_s": round(g2, 3), "frames": k2, "transfer_s": round(span, 2)})
        # A timer whose callbacks could not run fires them all at once when it can again: periodic messages that
        # were held back in the unit's timer task come out in one burst behind the write (SAE J2534-1: copies of a
        # periodic message must not stack up). 0.25 s at 50 ms and 20 ms is 5 and 12 or 13 frames.
        after = (lg["t1"], lg["t1"] + 0.25) if lg is not None else (0.0, 0.0)
        b1 = sum(1 for s in bus.seen.get(FUNC_ID, []) if after[0] < s <= after[1])
        b2 = sum(1 for s in bus.seen.get(PER_ID, []) if after[0] < s <= after[1])
        check("L7 nothing piled up behind the write: the 0.25 s after it carry the 5 and the 12 or 13 frames the two "
              "periods give, not the ones held back", lg is not None and 3 <= b1 <= 8 and 9 <= b2 <= 17,
              {"frames_in_0.25_s": (b1, b2)})
        s1 = t.call(STOP_PERIODIC, chi, struct.pack("<I", p1))[0]
        s2 = t.call(STOP_PERIODIC, chc, struct.pack("<I", p2))[0]
        time.sleep(0.3)
        a1, a2 = len(bus.seen.get(FUNC_ID, [])), len(bus.seen.get(PER_ID, []))
        time.sleep(0.5)
        check("L6 both periodic messages stop when asked: nothing more on the bus", s1 == 0 and s2 == 0
              and len(bus.seen.get(FUNC_ID, [])) == a1 and len(bus.seen.get(PER_ID, [])) == a2,
              {"stop": (hex(s1), hex(s2)), "frames_after": (len(bus.seen.get(FUNC_ID, [])) - a1, len(bus.seen.get(PER_ID, [])) - a2)})
        t.call(b.DISCONNECT, channel=chi)
        t.call(b.DISCONNECT, channel=chc)
        t.call(b.CLOSE)
    finally:
        t.close()
        time.sleep(0.5)


def leg_p(bus, host, port):
    print("== P: a periodic message ends with its channel", flush=True)

    def frames():
        return len(bus.seen.get(GONE_ID, []))

    t, _v = session(host, port)
    try:
        n0 = frames()
        st, ch = connect(t, b.PROT_CAN)
        st2, pid = periodic(t, ch, 20, tp.any_msg(b.PROT_CAN, struct.pack(">I", GONE_ID) + b"\x11\x22\x33\x44\x55\x66\x77\x88"))
        time.sleep(0.6)
        n1 = frames()
        check("P0 a periodic message every 20 ms on a raw CAN channel runs", st == 0 and st2 == 0 and 20 <= n1 - n0 <= 40,
              {"statuses": (hex(st), hex(st2)), "frames in 0.6 s": n1 - n0})
        st3 = t.call(b.DISCONNECT, channel=ch)[0]
        time.sleep(0.3)
        n2 = frames()
        time.sleep(0.5)
        n3 = frames()
        check("P1 DISCONNECT ends it: nothing more on the bus", st3 == 0 and n3 == n2, {"disconnect": hex(st3), "frames_after": n3 - n2})
        # the next channel takes the slot the first one had: a message left in the table would go out on it
        st4, ch2 = connect(t, b.PROT_CAN)
        time.sleep(0.8)
        n4 = frames()
        check("P2 and it does not come back on the next channel that is connected (SAE J2534-1 7.3.6: a channel's "
              "periodic messages are cleared with it)", st4 == 0 and n4 == n3,
              {"connect": hex(st4), "channel ids": (ch, ch2), "frames in 0.8 s on the new channel": n4 - n3})
        st5 = t.call(STOP_PERIODIC, ch2, struct.pack("<I", pid))[0]
        check("P3 its id is gone with it: STOP_PERIODIC of that id is refused", st5 != 0, hex(st5))
        t.call(b.DISCONNECT, channel=ch2)
        t.call(b.CLOSE)
    finally:
        t.close()
        time.sleep(0.5)


def leg_o(bus, dut, pi):
    print("== O: a channel's receive queue runs over", flush=True)
    n = 2400
    c0, j0 = (dut.can("dispatch_drops") or {}).get("dispatch_drops"), dut.get("/api/j2534") or {}
    pi.cmd(cmd="flood", stall=2.5, rcvbuf=4096, can_id=FLOOD_ID, version=2, quiet=1.5)
    if pi.wait("stalled", 15) is None:
        check("O0 the client on the Pi opened its session", False, "no word from the helper")
        return
    time.sleep(0.15)
    sent = bus.flood(FLOOD_ID, n, 0.00075)
    r = pi.wait("result", 25) or {"error": "no result from the helper"}
    time.sleep(0.5)
    c1, j1 = (dut.can("dispatch_drops") or {}).get("dispatch_drops"), dut.get("/api/j2534") or {}
    if r.get("error"):
        check("O0 the client on the Pi ran the leg", False, r)
        return
    missing = sent - r["frames"]
    check("O1 the flood ran the channel's queue over: %d frames on the bus while the client did not read, fewer arrived, "
          "and the CAN node counted what the queue dropped" % n, sent == n and 0 < missing and c1 is not None and c0 is not None
          and c1 - c0 >= missing, {"sent": sent, "arrived": r["frames"], "missing": missing, "dispatch_drops": (c0, c1)})
    check("O2 the session runs at wire version 2", r.get("version") == 2, r.get("version"))
    gaps, evs = r.get("gaps") or [], [e for e in (r.get("events") or []) if e[1] == r.get("channel") and e[2] == EVENT_RX_LOST]
    check("O3 the unit told the client: an EVENT RX_LOST on the channel in front of the first message after each loss",
          r.get("n_gaps", 0) >= 1 and [e[0] for e in evs] == [g[0] for g in gaps],
          {"gaps (index, missing)": gaps[:5], "events (index, channel, event, value)": (r.get("events") or [])[:5]})
    check("O4 and how many: the events' counts are the frames that are missing", bool(evs) and sum(e[3] for e in evs) == missing
          and [e[3] for e in evs] == [g[1] for g in gaps], {"events": [e[3] for e in evs][:5], "missing": missing})
    lost0, lost1 = j0.get("rx_lost"), j1.get("rx_lost")
    check("O5 /api/j2534 counts them (rx_lost)", lost1 is not None and lost1 - (lost0 or 0) == missing, (lost0, lost1))


def leg_k(pi, secs=32):
    print("== K: a session that is alive and silent", flush=True)
    pi.cmd(cmd="idle", secs=secs)
    r = pi.wait("result", secs + 20) or {"error": "no result from the helper"}
    check("K1 %d s without a frame either way, then a request: the session is still there and answers" % secs,
          r.get("status") == 0 and r.get("millivolts") is not None and not r.get("error"), r)


def leg_v(bus, dut, pi, host, port):
    print("== V: the peer vanishes", flush=True)
    j0 = dut.get("/api/j2534") or {}
    pi.cmd(cmd="vanish", limit=45, channel=False)
    v = pi.wait("vanished", 20)
    r = pi.wait("result", 60) or {"error": "no result from the helper"}
    pi.wait("cleanup", 15)
    freed = r.get("freed_s")
    check("V1 an idle session whose peer vanished is given up by the unit, and a connection made at that moment is served "
          "(keepalive: 10 s idle, 3 probes 5 s apart, so about 25 s)", v is not None and freed is not None and 15 <= freed <= 40,
          "not within %s s" % r.get("limit") if freed is None and not r.get("error") else r)
    time.sleep(1.5)
    took = served_within(host, port, 10.0)
    check("V2 the server is free after it: a new session is served", took is not None, "no HELLO answer in 10 s" if took is None
          else "%.1f s" % took)

    halt = threading.Event()

    def noise():
        i = 0
        while not halt.is_set():
            bus.send(NOISE_ID, struct.pack(">I", i) + b"\x00\x00\x00\x00")
            i += 1
            time.sleep(0.002)

    th = threading.Thread(target=noise, daemon=True)
    th.start()
    try:
        pi.cmd(cmd="vanish", limit=30, channel=True, settle=0.8)
        v = pi.wait("vanished", 20)
        r = pi.wait("result", 45) or {"error": "no result from the helper"}
        pi.wait("cleanup", 15)
    finally:
        halt.set()
        th.join(2)
    freed = r.get("freed_s")
    check("V3 a session the unit is sending to (a busy raw CAN channel) is given up within 15 s of the peer vanishing: "
          "the send timeout of 5 s", v is not None and freed is not None and freed <= 15,
          "not within %s s" % r.get("limit") if freed is None and not r.get("error") else r)
    time.sleep(1.5)
    took = served_within(host, port, 10.0)
    j1 = dut.get("/api/j2534") or {}
    check("V4 the server is free after it, and /api/j2534 counted the send that timed out (tx_timeouts)",
          took is not None and j1.get("tx_timeouts") is not None and j1.get("tx_timeouts") - (j0.get("tx_timeouts") or 0) >= 1,
          {"served_s": None if took is None else round(took, 1), "tx_timeouts": (j0.get("tx_timeouts"), j1.get("tx_timeouts"))})


def leg_d(dut, bus, py32, jh, jp, dll, old_dll):
    print("== D: the driver DLL", flush=True)
    if not dll:
        print("SKIP: no --dll (pass the built wican_j2534.dll, driver 1.10 or later)", flush=True)
        return
    if not os.path.exists(dll) or not py32 or not os.path.exists(py32):
        check("D0 the DLL and the 32-bit Python are there", False, (dll, py32))
        return
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "j2534_session_dll_check.py")
    proven = bus_proven(dut, bus)
    for name, args, what in (
            ("D1", ["--dll", dll, "--fake"], "against fake devices of wire version 1 and 2: the numbers an application sees, "
                                             "the last-error texts, ERR_BUFFER_OVERFLOW after a loss"),
            ("D2", ["--dll", dll, "--unit", "%s:%d" % (jh, jp)], "against the unit: SAE's numbers through the DLL"),
            ("D3", ["--dll", old_dll, "--unit", "%s:%d" % (jh, jp), "--old"] if old_dll else None,
             "a driver before 1.10 against the unit: the numbers it always returned")):
        if args is None:
            print("SKIP: %s, no --old-dll" % name, flush=True)
            continue
        if "--unit" in args and not proven:
            check("%s the unit's CAN node is at a proven 500 kbit/s" % name, False)
            continue
        try:
            p = subprocess.run([sys.executable, "-u", script, "--py32", py32] + args, capture_output=True, text=True, timeout=180)
            out = p.stdout + p.stderr
        except Exception as e:
            out, p = "%s: %s" % (type(e).__name__, e), None
        for ln in out.rstrip().splitlines():
            print("   " + ln, flush=True)
        check("%s %s (%d steps)" % (name, what, out.count("OK: ")), p is not None and p.returncode == 0
              and "DLL SESSION CHECK DONE" in out and "BAD: " not in out, "exit %s" % (p.returncode if p else "none"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--j2534", default="localhost:18534")
    ap.add_argument("--pi", default="rpi001", help="the ssh name of the Pi next to the unit")
    ap.add_argument("--dut-ip", default="10.42.1.41", help="the unit's address as the Pi sees it")
    ap.add_argument("--pcan", default=os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"))
    ap.add_argument("--py32", default=r"C:\Users\Ali\idf_tmp\py32\python.exe")
    ap.add_argument("--dll", default=None, help="the built wican_j2534.dll (1.10 or later) for leg D")
    ap.add_argument("--old-dll", default=None, help="a wican_j2534.dll before 1.10: leg D3")
    ap.add_argument("--legs", default="ANLPOKVD", help="the legs to run (C and X always run)")
    ap.add_argument("--repeat", type=int, default=1, help="run the chosen legs this many times (a soak)")
    ap.add_argument("--keep", action="store_true", help="leave the settings as the run set them (a second run follows)")
    a = ap.parse_args()

    dut = ab.Dut(a.dut)
    jh, jp = a.j2534.split(":")[0], int(a.j2534.split(":")[1])
    host = jh if jh != "localhost" else "127.0.0.1"
    t0 = time.time()
    planned = 0

    st = dut.get("/api/status")
    if not st:
        print("J2534 SESSION FAIL: no DUT at", a.dut)
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
        print("J2534 SESSION FAIL: PCAN %s not usable: %s" % (a.pcan, e))
        sys.exit(1)
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
                print("J2534 SESSION FAIL: the DUT did not come back (%s)" % why)
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

    pi = None
    try:
        for round_no in range(a.repeat):
            if a.repeat > 1:
                print("== round %d of %d" % (round_no + 1, a.repeat), flush=True)
            for leg, run in (("A", lambda: leg_a(dut, host, jp)), ("N", lambda: leg_n(dut, bus, host, jp)),
                             ("L", lambda: leg_l(bus, dut, host, jp)), ("P", lambda: leg_p(bus, host, jp))):
                if leg in a.legs:
                    try:
                        run()
                    except Exception as e:
                        check("leg %s ran to the end" % leg, False, "%s: %s" % (type(e).__name__, e))
                        time.sleep(2.0)
            if set("OKV") & set(a.legs):
                pi = pi or Pi(a.pi, a.dut_ip, 6809)
                for leg, run in (("O", lambda: leg_o(bus, dut, pi)), ("K", lambda: leg_k(pi)),
                                 ("V", lambda: leg_v(bus, dut, pi, host, jp))):
                    if leg in a.legs:
                        try:
                            run()
                        except Exception as e:
                            check("leg %s ran to the end" % leg, False, "%s: %s" % (type(e).__name__, e))
                            time.sleep(2.0)
    except Exception as e:
        check("the legs ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        if pi is not None:
            pi.close()
    if "D" in a.legs:
        leg_d(dut, bus, a.py32, jh, jp, a.dll, a.old_dll)
    check("the PCAN sent every frame it was asked to", not bus.errors, bus.errors[:4])

    # the server's tasks after the legs drove them (their stacks are in PSRAM, where an overflow is silent)
    rows = {x.get("name"): x.get("stack_hw") for x in ((dut.get("/api/status/tasks") or {}).get("tasks") or [])
            if str(x.get("name", "")).startswith("j2534")}
    check("S1 the J2534 tasks kept at least 1 KB of their stacks unused (the session's, the RX pump's, the periodic "
          "messages')", all(rows.get(n) is not None and rows[n] >= 1024 for n in ("j2534", "j2534_rx", "j2534_per")), rows)

    print("== X: restore", flush=True)
    j = dut.get("/api/j2534") or {}
    check("X0 no session is left behind", j.get("client_connected") is False, {"client_connected": j.get("client_connected"),
                                                                                "transport": j.get("transport")})
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
        print("J2534 SESSION FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("J2534 SESSION PASS")


if __name__ == "__main__":
    main()
