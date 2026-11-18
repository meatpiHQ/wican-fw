#!/usr/bin/env python3
"""One WebSocket client on a slcan channel of the unit (/ws/can), as the
slcan stage's leg W needs it. Runs on the Pi, straight at the unit, python3
standard library only.

  python3 ws_stall_client.py <unit> <path> <secs> <mode> [arg]

mode
  read          read all the time; count the generator's frames (id 123, a
                32-bit counter in the first four data bytes)
  write <hz>    read as above AND write a slcan line (id 100, a counter) <hz>
                times a second
  stall         finish the handshake, then take nothing (a small receive
                buffer, no read) until the unit closes or <secs> are over
  slow <bps>    take <bps> bytes a second and no more
  burst <n>     write <n> slcan lines (id 326, a counter) as fast as the
                socket takes them, a hundred lines (2.2 KB) a message, then
                read on
  oversize <n>  write one message of <n> bytes and wait for the unit's answer
  crowd <n>     no WebSocket: <n> plain HTTP connections, one after the other,
                each asks GET /api/info once and then stays open until <secs>
                are over (<path> is not used). Its result: opened, answered,
                closed_by_unit

Prints one line: RESULT {...}
  frames, first, last   generator frames seen and their first and last number
  lost_inside           numbers missing between first and last
  max_gap_ms            the longest time between two of them
  written               lines written (mode write)
  closed                "no", "close frame <code>" or "eof" and when (s)
"""
import base64
import json
import os
import select
import socket
import struct
import sys
import time

GEN_HEAD = b"t1238"
WRITE_ID = 0x100
BURST_ID = 0x326


def handshake(host, path, rcvbuf=None):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if rcvbuf:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)   # before the connect, or it does not count
    s.settimeout(8)
    s.connect((host, 80))
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, key)).encode())
    head = b""
    while b"\r\n\r\n" not in head:
        d = s.recv(1)
        if not d:
            raise RuntimeError("the unit closed during the handshake: %r" % head[:80])
        head += d
    if b" 101 " not in head.split(b"\r\n")[0]:
        raise RuntimeError("no upgrade: %r" % head.split(b"\r\n")[0])
    return s


def masked(opcode, payload):
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
    else:
        head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
    return head + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(payload))


class Reader:
    """Server frames out of a byte stream (never masked), slcan lines out of
    their payloads, the generator's counter out of the lines."""

    def __init__(self):
        self.raw = b""
        self.text = b""
        self.frames = 0
        self.first = None
        self.last = None
        self.seen = 0
        self.max_gap = 0.0
        self.last_at = None
        self.closed = "no"

    def feed(self, data, now):
        self.raw += data
        while True:
            if len(self.raw) < 2:
                return
            op = self.raw[0] & 0x0F
            n = self.raw[1] & 0x7F
            off = 2
            if n == 126:
                if len(self.raw) < 4:
                    return
                n = struct.unpack("!H", self.raw[2:4])[0]
                off = 4
            elif n == 127:
                if len(self.raw) < 10:
                    return
                n = struct.unpack("!Q", self.raw[2:10])[0]
                off = 10
            if len(self.raw) < off + n:
                return
            payload = self.raw[off:off + n]
            self.raw = self.raw[off + n:]
            if op == 8:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 0
                self.closed = "close frame %d" % code
                return
            if op in (1, 2, 0):
                self.lines(payload, now)

    def lines(self, payload, now):
        self.text += payload
        parts = self.text.split(b"\r")
        self.text = parts.pop()
        for ln in parts:
            if not ln.startswith(GEN_HEAD) or len(ln) < 13:
                continue
            try:
                c = int(ln[5:13], 16)
            except ValueError:
                continue
            if self.first is None:
                self.first = c
            self.last = c
            self.seen += 1
            if self.last_at is not None:
                self.max_gap = max(self.max_gap, now - self.last_at)
            self.last_at = now

    def result(self):
        span = (self.last - self.first + 1) if self.seen else 0
        return {"frames": self.seen, "first": self.first, "last": self.last,
                "lost_inside": span - self.seen, "max_gap_ms": round(self.max_gap * 1000)}


def main():
    host, path, secs, mode = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
    arg = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
    t0 = time.time()
    res = {"mode": mode}

    if mode == "crowd":
        socks, answered, gone = [], 0, 0
        for _ in range(int(arg)):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(8)
            try:
                s.connect((host, 80))
                s.sendall(("GET /api/info HTTP/1.1\r\nHost: %s\r\nConnection: keep-alive\r\n\r\n" % host).encode())
                if b" 200 " in s.recv(4096).split(b"\r\n")[0]:
                    answered += 1
            except OSError:
                pass
            socks.append(s)
            time.sleep(0.3)
        while time.time() - t0 < secs:
            time.sleep(0.2)
        for s in socks:
            s.settimeout(0.05)
            try:
                while s.recv(4096):
                    pass
                gone += 1                       # the unit's FIN
            except socket.timeout:
                pass                            # still open
            except OSError:
                gone += 1                       # the unit's RST
            s.close()
        res.update({"opened": len(socks), "answered": answered, "closed_by_unit": gone})
        print("RESULT " + json.dumps(res))
        return

    if mode in ("stall", "slow"):
        s = handshake(host, path, rcvbuf=2048)
        took = 0
        closed = "no"
        s.settimeout(0.2)
        hup = select.poll()
        hup.register(s, select.POLLRDHUP | select.POLLERR | select.POLLHUP)
        while time.time() - t0 < secs:
            if mode == "stall":
                # no read at all: the unit's FIN or RST is seen by poll
                ev = hup.poll(200)
                if ev:
                    closed = "closed by the unit after %.1f s (poll 0x%x)" % (time.time() - t0, ev[0][1])
                    break
                continue
            budget = int(arg * (time.time() - t0)) - took
            if budget <= 0:
                time.sleep(0.05)
                continue
            try:
                d = s.recv(min(budget, 4096))
            except socket.timeout:
                continue
            except OSError as e:
                closed = "error %s after %.1f s" % (e.errno, time.time() - t0)
                break
            if not d:
                closed = "eof after %.1f s" % (time.time() - t0)
                break
            took += len(d)
        if mode == "stall" and closed == "no":
            # what did the unit do meanwhile? read what is there now
            s.settimeout(1.0)
            try:
                rest = b""
                while True:
                    d = s.recv(65536)
                    if not d:
                        closed = "eof found at the end"
                        break
                    rest += d
                    if len(rest) > 4_000_000:
                        break
            except (socket.timeout, OSError):
                pass
            r = Reader()
            r.feed(rest, time.time())
            if r.closed != "no":
                closed = r.closed + " found at the end"
        res.update({"took": took, "closed": closed})
        print("RESULT " + json.dumps(res), flush=True)
        s.close()
        return

    s = handshake(host, path)
    r = Reader()
    written = 0
    if mode == "burst":
        s.settimeout(30)
        for i in range(0, int(arg), 100):
            lines = b"".join(b"t%03X8%08XAA550000\r" % (BURST_ID, k) for k in range(i, min(i + 100, int(arg))))
            s.sendall(masked(1, lines))
            written += min(100, int(arg) - i)
        res["burst_s"] = round(time.time() - t0, 2)
    elif mode == "oversize":
        s.settimeout(10)
        s.sendall(masked(1, b"t1230\r" * (int(arg) // 6)))
    s.settimeout(0.05)
    next_write = time.time() + 1.0
    eof = "no"
    while time.time() - t0 < secs:
        now = time.time()
        if mode == "write" and now >= next_write:
            line = b"t%03X8%08XAA550000\r" % (WRITE_ID, written)
            try:
                s.sendall(masked(1, line))
                written += 1
            except OSError as e:
                eof = "write error %s after %.1f s" % (e.errno, now - t0)
                break
            next_write += 1.0 / arg
        try:
            d = s.recv(65536)
        except socket.timeout:
            continue
        except OSError as e:
            eof = "error %s after %.1f s" % (e.errno, time.time() - t0)
            break
        if not d:
            eof = "eof after %.1f s" % (time.time() - t0)
            break
        r.feed(d, time.time())
        if r.closed != "no":
            eof = r.closed + " after %.1f s" % (time.time() - t0)
            break
    res.update(r.result())
    res.update({"written": written, "closed": eof})
    print("RESULT " + json.dumps(res), flush=True)
    try:
        s.sendall(masked(8, struct.pack("!H", 1000)))
    except OSError:
        pass
    s.close()


if __name__ == "__main__":
    main()
