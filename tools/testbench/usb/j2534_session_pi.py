#!/usr/bin/env python3
"""The Pi's half of j2534_session_bench.py: a J2534 wire client that sits next to the unit.

Three things a client behind an ssh tunnel cannot do, so they run here, on the bench's
access point, one TCP hop from the unit:

  vanish  a session whose peer disappears without closing: the packets of that ONE
          connection are dropped both ways with nft (no FIN, no RST, no acknowledge), the
          way a pulled cable or a sleeping laptop looks from the unit. A second connection,
          made at that moment, measures how long the unit keeps the dead session.
  flood   a client that stops reading its socket for a moment while the bus is busy, with
          a small receive buffer: the unit's send path backs up, its channel's receive
          queue runs over, and the client then reads everything and says what it was told.
  idle    a session that is alive and silent: nothing either way for a while, then one
          request.

It reads one JSON command per line on stdin and writes JSON lines on stdout (the bench on
the PC drives it through ssh). nft needs root: `sudo -n nft` must work. The rules live in a
table of their own, removed in a `finally` and at start (a run that died).

usage (on the Pi):  python3 -u j2534_session_pi.py <unit ip> [port]
"""
import json
import select
import socket
import struct
import subprocess
import sys
import time

MAGIC = 0x4A35
HDR = struct.Struct("<HBBHHI")          # magic, version, type, seq, channel, length
MSG = struct.Struct("<IIIIII")          # protocol, rx_status, tx_flags, timestamp, extra, size
HELLO, OPEN, CLOSE, CONNECT, START_FILTER, IOCTL = 0x01, 0x02, 0x03, 0x04, 0x11, 0x15
ACK, RX_MSG, EVENT = 0x80, 0x81, 0x82
PROT_CAN = 5
PASS_FILTER = 1
READ_VBATT = 0x03
TABLE = "j2534_session_bench"

unit = sys.argv[1]
port = int(sys.argv[2]) if len(sys.argv) > 2 else 6809


def say(**kw):
    print(json.dumps(kw), flush=True)


def nft(*args, check=True):
    p = subprocess.run(["sudo", "-n", "nft"] + list(args), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError("nft %s: %s" % (" ".join(args), p.stderr.strip()))
    return p.returncode == 0


def blackhole(sport):
    """Drop every packet of the connection from our port `sport` to the unit, both ways."""
    nft("delete", "table", "inet", TABLE, check=False)
    nft("add", "table", "inet", TABLE)
    nft("add", "chain", "inet", TABLE, "out", "{ type filter hook output priority 0 ; }")
    nft("add", "chain", "inet", TABLE, "in", "{ type filter hook input priority 0 ; }")
    nft("add", "rule", "inet", TABLE, "out", "ip", "daddr", unit, "tcp", "sport", str(sport),
        "tcp", "dport", str(port), "drop")
    nft("add", "rule", "inet", TABLE, "in", "ip", "saddr", unit, "tcp", "sport", str(port),
        "tcp", "dport", str(sport), "drop")


def unhole():
    return nft("delete", "table", "inet", TABLE, check=False)


class Link:
    """One connection to the unit's J2534 port."""

    def __init__(self, rcvbuf=0, timeout=5.0):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if rcvbuf:
            self.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.s.settimeout(timeout)
        self.s.connect((unit, port))
        self.sport = self.s.getsockname()[1]
        self.seq = 0
        self.buf = b""
        self.frames = []            # what arrived that was not the ACK waited for: (type, channel, body)

    def send(self, mtype, channel=0, payload=b""):
        self.seq = (self.seq + 1) & 0xFFFF
        self.s.sendall(HDR.pack(MAGIC, 1, mtype, self.seq, channel, len(payload)) + payload)
        return self.seq

    def _fill(self, until):
        left = until - time.monotonic()
        if left <= 0:
            return False
        r, _w, _x = select.select([self.s], [], [], left)
        if not r:
            return False
        chunk = self.s.recv(65536)
        if not chunk:
            raise EOFError("the unit closed the link")
        self.buf += chunk
        return True

    def frame(self, timeout):
        """The next frame (type, seq, channel, body), or None after timeout."""
        until = time.monotonic() + timeout
        while True:
            if len(self.buf) >= HDR.size:
                magic, _v, rt, rseq, rch, rlen = HDR.unpack_from(self.buf, 0)
                if magic != MAGIC:
                    raise ValueError("bad magic 0x%04X" % magic)
                if len(self.buf) >= HDR.size + rlen:
                    body = self.buf[HDR.size:HDR.size + rlen]
                    self.buf = self.buf[HDR.size + rlen:]
                    return rt, rseq, rch, body
            if not self._fill(until):
                return None

    def ack(self, seq, timeout=5.0):
        """The ACK for seq: (status, the bytes behind it), or (None, b'') after timeout."""
        until = time.monotonic() + timeout
        while True:
            left = until - time.monotonic()
            f = self.frame(left) if left > 0 else None
            if f is None:
                return None, b""
            rt, rseq, rch, body = f
            if rt == ACK and rseq == seq:
                return struct.unpack_from("<I", body, 0)[0], body[4:]
            self.frames.append((rt, rch, body))

    def call(self, mtype, channel=0, payload=b"", timeout=5.0):
        return self.ack(self.send(mtype, channel, payload), timeout)

    def hello(self, version, timeout=5.0):
        st, body = self.call(HELLO, payload=struct.pack("<I", version), timeout=timeout)
        return st, (struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else None)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def can_channel(link, pass_id=None):
    st, body = link.call(CONNECT, payload=struct.pack("<IIIII", PROT_CAN, 0, 500000, 0, 0))
    ch = struct.unpack_from("<I", body, 0)[0] if st == 0 and len(body) >= 4 else 0
    if st == 0 and pass_id is not None:
        m = MSG.pack(PROT_CAN, 0, 0, 0, 0, 4)
        st, _b = link.call(START_FILTER, ch, struct.pack("<I", PASS_FILTER) + m + b"\xFF\xFF\xFF\xFF" +
                           m + struct.pack(">I", pass_id))
    return st, ch


def cmd_idle(c):
    """Alive and silent for c['secs'], then one request."""
    link = Link()
    try:
        st, ver = link.hello(c.get("version", 1))
        st2, _b = link.call(OPEN)
        t0 = time.monotonic()
        time.sleep(c["secs"])
        try:
            st3, body = link.call(IOCTL, 0, struct.pack("<I", READ_VBATT))
            err = ""
        except Exception as e:
            st3, body, err = None, b"", "%s: %s" % (type(e).__name__, e)
        say(ev="result", hello=st, version=ver, open=st2, waited=round(time.monotonic() - t0, 1), status=st3,
            millivolts=struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else None, error=err)
        link.call(CLOSE, timeout=2.0) if st3 is not None else None
    finally:
        link.close()


def cmd_vanish(c):
    """A session, then its packets dropped; how long until a NEW connection is served."""
    link, probe, freed, pst = Link(), None, None, None
    try:
        st, _v = link.hello(1)
        st2, _b = link.call(OPEN)
        ch = 0
        if c.get("channel"):
            st2, ch = can_channel(link)
        if st != 0 or st2 != 0:
            say(ev="result", error="the session did not open: hello %s, then %s" % (st, st2))
            return
        if c.get("settle"):
            time.sleep(c["settle"])                    # the bench's traffic is flowing to this client
        blackhole(link.sport)
        t0 = time.monotonic()
        say(ev="vanished", sport=link.sport, channel=ch)
        # the one probe: connected at once (it waits in the unit's listen queue), its HELLO already sent
        try:
            probe = Link(timeout=10.0)
            seq = probe.send(HELLO, payload=struct.pack("<I", 1))
            pst, _b = probe.ack(seq, timeout=c["limit"])
            if pst is not None:
                freed = time.monotonic() - t0
        except Exception as e:
            say(ev="note", probe_error="%s: %s" % (type(e).__name__, e))
        say(ev="result", freed_s=None if freed is None else round(freed, 1), probe_status=pst, limit=c["limit"])
    finally:
        ok = unhole()
        if probe is not None:
            probe.close()
        link.close()
        say(ev="cleanup", rules_removed=ok)


def cmd_flood(c):
    """Stop reading while the bus is busy, then read everything."""
    link = Link(rcvbuf=c.get("rcvbuf", 4096))
    try:
        st, ver = link.hello(c.get("version", 2))
        st2, _b = link.call(OPEN)
        st3, ch = can_channel(link, c["can_id"])
        if st != 0 or st2 != 0 or st3 != 0:
            say(ev="result", error="the session did not open: hello %s, open %s, channel %s" % (st, st2, st3))
            return
        del link.frames[:]
        say(ev="stalled", channel=ch, version=ver)
        time.sleep(c["stall"])
        counters, events, other = [], [], 0
        while True:
            f = link.frame(c.get("quiet", 1.5))
            if f is None:
                break
            rt, _seq, rch, body = f
            if rt == RX_MSG and len(body) >= MSG.size + 8:
                size = MSG.unpack_from(body, 0)[5]
                data = body[MSG.size:MSG.size + size]
                if struct.unpack_from(">I", data, 0)[0] == c["can_id"]:
                    counters.append(struct.unpack_from(">I", data, 4)[0])
                else:
                    other += 1
            elif rt == EVENT:
                ev, val = struct.unpack_from("<II", body, 0) if len(body) >= 8 else (None, None)
                events.append([len(counters), rch, ev, val])
            else:
                other += 1
        gaps = [[i, counters[i] - counters[i - 1] - 1] for i in range(1, len(counters)) if counters[i] != counters[i - 1] + 1]
        st4, _b = link.call(CLOSE, timeout=3.0)
        say(ev="result", version=ver, channel=ch, frames=len(counters), first=counters[0] if counters else None,
            last=counters[-1] if counters else None, gaps=gaps[:40], n_gaps=len(gaps), events=events[:40],
            n_events=len(events), other=other, close=st4)
    finally:
        link.close()


def main():
    unhole()
    say(ev="ready", unit=unit, port=port)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        c = json.loads(line)
        try:
            if c["cmd"] == "quit":
                break
            {"idle": cmd_idle, "vanish": cmd_vanish, "flood": cmd_flood}[c["cmd"]](c)
        except Exception as e:
            unhole()
            say(ev="result", error="%s: %s" % (type(e).__name__, e))
    unhole()


if __name__ == "__main__":
    main()
