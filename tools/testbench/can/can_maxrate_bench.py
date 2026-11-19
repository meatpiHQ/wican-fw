#!/usr/bin/env python3
"""The most frames a CAN bus can carry, through the unit -> CAN MAX RATE PASS.

A CAN bus is full when one frame follows the other with nothing but the three
bits of interframe space between them. How many frames a second that is
depends on the bit rate and on the length of the frame, and the shortest
frame (a standard identifier, no data, 48 bits with its one stuff bit) gives
the most: 10,416 a second at 500 kbit/s and 20,833 at 1 Mbit/s. A car's own traffic stays far below
(1,000 to 2,500 frames a second on a 500 kbit/s bus), so this bench is the
ceiling, not a drive.

A tier is a bit rate and a frame shape:
  std8  standard identifier, 8 data bytes  (a car's bus at its densest)
  std0  standard identifier, no data       (the most frames a bus can carry)
  ext8  29-bit identifier, 8 data bytes    (the longest classic frame)
(any data length can be asked for: std0 to std8, ext4 to ext8). By default
the three shapes at 500 kbit/s and the two long ones at 1000 kbit/s, which
the unit must take whole, and std0 at 1000 kbit/s, which is above what it
takes (see the end). For each tier the PCAN hands over exactly N frames back to back (N = the line rate times
--secs) while one slcan client reads the unit's TCP server, and the bench
asserts:
  - the bus was full: the rate the unit's own `rx` counter rose at is within
    5 % of the rate the frame's bit count allows (computed bit by bit, with
    the stuff bits);
  - the CAN driver took every frame: `rx` +N, `rx_missed`, `rx_overrun`,
    `rx_bad`, `dispatch_drops` and the error counters +0;
  - the TCP client got every frame, once and in order, and the server's
    `tx_drops` did not move;
  - the web API answered all through it (the slowest answer is printed);
  - the unit's flash was at rest all through it (see below).

The flash at rest: a flash erase holds every task of the unit for 23 ms and
more, and only the CAN receive interrupt runs meanwhile. The receive queue
holds 256 frames, which is 61 ms of a full 500 kbit/s bus of 8-byte frames
and 30 ms at 1000 kbit/s, so an erase at the line rate can cost frames. And
the unit does erase: about 25 s after a restart that changed a setting it
refreshes its configuration backup (28 writes, 4 erases), which is where
this bench's first tier ran until 2026-11-19. Nothing was lost in the six
runs of 2026-11-18; on 2026-11-19 three runs of eight lost 1 to 18 frames
in that tier. So the bench now waits, after each of its restarts, until the
unit has been up 40 s and its flash counters have stood still for 3 s, and
it reads the counters before and after every tier. --no-flash-wait runs the
tiers at once, as before, to see what a refresh inside a tier costs.

A tier marked with ! (std0 at 1000 kbit/s, 20,833 frames a second) is above
what the unit takes: frames are lost there, and the bench asserts instead
that every lost frame is in a counter with a name (`rx_missed`, `rx_overrun`
at the CAN driver, `dispatch_drops` and `tx_drops` behind it), that the
client kept its connection and the order of what it got, and that the unit
is itself again when the bus lets go.

The bus must be quiet and at one bit rate, so for the run the unit's AutoPID
is off (the OBD chip then leaves the bus alone), its CAN interface is set to
the tier's bit rate, the slcan server is on with a `can` connection row, and
ECU sim v2's HS CAN is moved to the same bit rate with its answers and its
broadcast off (it keeps acknowledging; it refuses 1000 kbit/s while its J1939
side is on). Everything goes back as found at the end, whatever happened.

Run on the PC (IDF venv python, python-can, the PCAN on the DUT's bus); the
stage `.\\test.ps1 canmax` opens the tunnel and passes the arguments:
  python can_maxrate_bench.py --dut localhost:18081 --slcan localhost:18333
         [--pcan PCAN_USBBUS2] [--tiers 500:std8,500:std0,1000:std0!]
         [--secs 10] [--keep-sim] [--no-flash-wait]
Expected final line: CAN MAX RATE PASS
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request

import can

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import bench_bus  # noqa: E402

STD_ID = 0x123
EXT_ID = 0x18DAF110
SLCAN_S = {125: 4, 250: 5, 500: 6, 1000: 8}
# What the stage runs. The first five must lose nothing. The last one is the
# most frames classic CAN can carry at all, 20,833 a second, which is above
# what the unit takes (measured 2026-11-18: nothing lost up to 10,400 frames a
# second, the first frames lost at 11,900): there the accounting is asserted.
DEFAULT_TIERS = "500:std8,500:std0,500:ext8,1000:std8,1000:ext8,1000:std0!"
# ECU sim v2's HS CAN for the run: at the tier's bit rate, every answer and
# broadcast off (the node stays up and acknowledges). Its J1939 side must be
# off for 1000 kbit/s at all: the simulator refuses that bit rate with it on.
SIM_HS_QUIET = ("hs_obd11", "hs_obd29", "hs_uds", "hs_j1939", "hs_broadcast")
CAN_COUNTERS = ("rx_missed", "rx_overrun", "rx_bad", "dispatch_drops", "rx_errors",
                "bus_errors", "bus_off", "rx_storms", "rx_deaf")

fails = []
passes = []


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


# ---- the length of a frame on the wire -----------------------------------------------

def crc15(bits):
    crc = 0
    for b in bits:
        top = ((crc >> 14) & 1) ^ b
        crc = (crc << 1) & 0x7FFF
        if top:
            crc ^= 0x4599
    return crc


def frame_bits(can_id, ext, data):
    """Bits one classic data frame takes on the wire, the stuff bits and the
    three bits of interframe space included."""
    bits = [0]                                             # start of frame
    if ext:
        bits += [(can_id >> i) & 1 for i in range(28, 17, -1)]   # base id
        bits += [1, 1]                                     # SRR, IDE
        bits += [(can_id >> i) & 1 for i in range(17, -1, -1)]   # id extension
        bits += [0, 0, 0]                                  # RTR, r1, r0
    else:
        bits += [(can_id >> i) & 1 for i in range(10, -1, -1)]
        bits += [0, 0, 0]                                  # RTR, IDE, r0
    bits += [(len(data) >> i) & 1 for i in range(3, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    crc = crc15(bits)
    bits += [(crc >> i) & 1 for i in range(14, -1, -1)]
    stuffed = 0
    run_bit, run = None, 0
    for b in bits:
        if b == run_bit:
            run += 1
        else:
            run_bit, run = b, 1
        if run == 5:                                       # a stuff bit follows,
            stuffed += 1                                   # of the other level,
            run_bit, run = 1 - b, 1                        # and starts a run itself
    # CRC delimiter, ACK slot, ACK delimiter, 7 bits end of frame, 3 interframe
    return len(bits) + stuffed + 13


# A frame with fewer than four data bytes has no room for a 32-bit counter, so
# it carries its number in the identifier: ROUND identifiers, round and round,
# each picked so that its frame is the shortest such a frame can be. For no
# data that is 48 bits: the seven zero bits that follow the identifier (RTR,
# IDE, r0 and a data length of 0) always cost one stuff bit, so the 47 bits the
# fields add up to are never seen on a wire.
ROUND = 512


def short_ids(dlc):
    data = b"\x55" * dlc
    sizes = {i: frame_bits(i, False, data) for i in range(0x7F0)}
    best = min(sizes.values())
    ids = [i for i in range(0x7F0) if sizes[i] == best][:ROUND]
    if len(ids) < 64:
        raise SystemExit("only %d identifiers give the shortest frame with %d data bytes" % (len(ids), dlc))
    return ids


class Shape:
    """One kind of frame, named std<N> or ext<N> after its identifier and its
    N data bytes: how the PCAN builds number i, how its slcan line is
    recognised and which number that line carries."""

    def __init__(self, name):
        self.name = name
        self.ext = name.startswith("ext")
        self.dlc = int(name[3:])
        self.by_id = self.dlc < 4
        if not name.startswith(("std", "ext")) or not 0 <= self.dlc <= 8 or (self.ext and self.by_id):
            raise SystemExit("no such shape: %s (std0..std8, ext4..ext8)" % name)
        if self.by_id:
            self.ids = short_ids(self.dlc)
            self.index = {v: k for k, v in enumerate(self.ids)}
            self.span = len(self.ids)
            self.tail = b"%d" % self.dlc + b"55" * self.dlc      # after the identifier in a slcan line
        else:
            self.span = 0
            self.head = (b"T%08X%d" % (EXT_ID, self.dlc)) if self.ext else (b"t%03X%d" % (STD_ID, self.dlc))

    def frame(self, i):
        if self.by_id:
            return self.ids[i % self.span], b"\x55" * self.dlc
        return (EXT_ID if self.ext else STD_ID), i.to_bytes(4, "big") + b"\xAA\x55\x00\x00"[:self.dlc - 4]

    def message(self, i):
        can_id, data = self.frame(i)
        return can.Message(arbitration_id=can_id, is_extended_id=self.ext, data=data)

    def mean_bits(self, n):
        span = min(n, 4096)
        frames = [self.frame(i) for i in range(span)]
        return sum(frame_bits(can_id, self.ext, data) for can_id, data in frames) / span

    def number(self, line):
        """The frame number a slcan line carries (its place in the identifier
        round for the short shapes), None for a line that is not one of ours."""
        if self.by_id:
            if len(line) != 4 + len(self.tail) or line[:1] != b"t" or line[4:] != self.tail:
                return None
            try:
                return self.index.get(int(line[1:4], 16))
            except ValueError:
                return None
        h = len(self.head)
        if not line.startswith(self.head) or len(line) < h + 8:
            return None
        try:
            return int(line[h:h + 8], 16)
        except ValueError:
            return None


# ---- the unit ---------------------------------------------------------------------------

class Dut:
    def __init__(self, base):
        self.base = "http://" + base

    def api(self, path, method="GET", body=None, timeout=10):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode(errors="replace")
        return json.loads(t) if t.strip().startswith(("{", "[")) else t

    def get(self, path, timeout=10):
        try:
            return self.api(path, timeout=timeout)
        except Exception:
            return None

    def wait_up(self, secs=150):
        end = time.time() + secs
        while time.time() < end:
            if self.get("/api/status", timeout=4):
                return True
            time.sleep(2)
        return False

    def flash(self):
        """The unit's flash counters since boot ({writes, erases, ..}) and its uptime in seconds."""
        st = self.get("/api/status") or {}
        up = 0
        for part in str(st.get("uptime") or "0").split(":"):
            up = up * 60 + (int(part) if part.isdigit() else 0)
        return dict((st.get("health") or {}).get("flash") or {}), up

    def wait_flash_at_rest(self, since_boot=40, still=3.0, limit=90.0):
        """A restart that changed a setting is followed by the configuration
        backup's refresh about 25 s after boot (28 writes, 4 erases of 4 KB).
        A flash erase holds every task of the unit for 23 ms and more, and at
        the line rate that is longer than its CAN receive queue lasts (256
        frames: 30 ms at 8,400 frames a second). Wait until the unit has been
        up `since_boot` seconds and its flash counters have stood still for
        `still` seconds: (counters, uptime, seconds waited)."""
        t0 = time.time()
        last, last_t, up = None, time.time(), 0
        while time.time() - t0 < limit:
            ops, up = self.flash()
            if ops != last:
                last, last_t = ops, time.time()
            if up >= since_boot and time.time() - last_t >= still:
                break
            time.sleep(1.0)
        return last or {}, up, time.time() - t0

    def settings(self, comp):
        s = self.api("/api/settings/" + comp)
        s.pop("degraded", None)
        s.pop("pending_reboot", None)
        return s

    def slcan_stats(self):
        sk = self.get("/api/sockets") or {}
        row = next((s for s in sk.get("servers", []) if s.get("name") == "slcan0"), None) or {}
        return dict(row.get("stats") or {}), sk.get("client_buffer_bytes")


class Client(threading.Thread):
    """One slcan client on the unit's TCP server: counts the lines of the
    tier's shape and checks that each carries the number after the last."""

    def __init__(self, host, port, shape, kbit):
        super().__init__(daemon=True)
        self.shape = shape
        self.s = socket.create_connection((host, port), timeout=5)
        self.s.sendall(b"C\rS%d\r\rO\r" % SLCAN_S.get(kbit, 6))
        self.got = 0
        self.out_of_order = 0
        self.missing = 0
        self.first_at = None
        self.last_at = None
        self.closed_by_peer = False
        self._next = None
        self._halt = threading.Event()
        self.start()

    def run(self):
        buf = b""
        by_id, span = self.shape.by_id, self.shape.span
        self.s.settimeout(0.5)
        while not self._halt.is_set():
            try:
                d = self.s.recv(262144)
            except socket.timeout:
                continue
            except OSError:
                break
            if not d:
                self.closed_by_peer = True
                break
            now = time.time()
            buf += d
            parts = buf.split(b"\r")
            buf = parts.pop()
            for ln in parts:
                n = self.shape.number(ln)
                if n is None:
                    continue
                if self.first_at is None:
                    self.first_at = now
                self.last_at = now
                self.got += 1
                if self._next is not None and n != self._next:
                    gap = (n - self._next) % span if by_id else n - self._next
                    if gap > 0:
                        self.missing += gap
                    else:
                        self.out_of_order += 1
                self._next = (n + 1) % span if by_id else n + 1

    def close(self):
        self._halt.set()
        self.join(timeout=3)
        try:
            self.s.close()
        except OSError:
            pass


class Poller(threading.Thread):
    """The unit's CAN counters twice a second. What the controller saw is
    `rx` plus `rx_missed` (a frame it received and had no room for), so the
    slope of that sum is the rate on the wire even when the unit is losing
    frames; the slowest answer says how the web API fared meanwhile."""

    def __init__(self, dut):
        super().__init__(daemon=True)
        self.dut = dut
        self.samples = []        # (time, rx)
        self.slowest = 0.0
        self.unanswered = 0
        self._halt = threading.Event()
        self.start()

    def run(self):
        while not self._halt.is_set():
            t0 = time.time()
            c = self.dut.get("/api/can", timeout=8)
            t1 = time.time()
            if c is None:
                self.unanswered += 1
            else:
                self.slowest = max(self.slowest, t1 - t0)
                self.samples.append(((t0 + t1) / 2, c.get("rx", 0) + c.get("rx_missed", 0)))
            self._halt.wait(max(0.0, 0.5 - (t1 - t0)))

    def close(self):
        self._halt.set()
        self.join(timeout=8)

    def wire_rate(self, seen0, n):
        """Frames a second between the first and the last sample taken while
        the count was rising (the ramps at both ends left out when there are
        samples enough). 0 when fewer than two samples fell inside the burst:
        the web API did not answer often enough to tell."""
        for lo, hi in ((0.05, 0.95), (0.0, 1.0)):
            mid = [(t, c) for t, c in self.samples if seen0 + n * lo < c < seen0 + n * hi]
            if len(mid) >= 4 or (hi == 1.0 and len(mid) >= 2):
                return (mid[-1][1] - mid[0][1]) / (mid[-1][0] - mid[0][0])
        return 0.0


def send_exactly(bus, shape, n):
    """n frames handed to the adapter, each retried until it was taken. An
    adapter that takes nothing for 5 s is not getting its frames out (the
    wrong channel, a bus at another bit rate, nothing that acknowledges):
    that ends the run instead of hanging it."""
    i = 0
    taken_at = time.time()
    while i < n:
        try:
            bus.send(shape.message(i), timeout=0.2)
            i += 1
            taken_at = time.time()
        except can.CanError as e:
            if time.time() - taken_at > 5.0:
                raise RuntimeError("the PCAN took no frame for 5 s after %d of %d (%s)" % (i, n, e))


def api_wait(dut, path, secs=30.0):
    """An answer of the web API, asked again for up to `secs` (after an
    overload the unit may need a moment): (answer or None, seconds it took)."""
    t0 = time.time()
    while time.time() - t0 < secs:
        r = dut.get(path, timeout=5)
        if r is not None:
            return r, time.time() - t0
        time.sleep(0.5)
    return None, time.time() - t0


def run_tier(a, dut, bus, kbit, shape, overload, sl_host, sl_port):
    theory = kbit * 1000.0 / shape.mean_bits(100000)
    n = int(theory * a.secs)
    tag = "%dk %s" % (kbit, shape.name)
    print("-- %s%s: %d frames back to back, the line rate is %.0f frames/s (%.1f bits a frame)"
          % (tag, " (above what the unit takes: the accounting is what is asserted)" if overload else "",
             n, theory, kbit * 1000.0 / theory), flush=True)
    cl = Client(sl_host, sl_port, shape, kbit)
    time.sleep(1.0)
    c0 = dut.api("/api/can")
    s0, _ = dut.slcan_stats()
    f0, _ = dut.flash()
    poll = Poller(dut)
    t0 = time.time()
    send_exactly(bus, shape, n)
    handed = time.time() - t0
    # the adapter trails the hand-over: let the wire empty (the line rate says
    # when), then wait until the client's count stands still
    time.sleep(max(0.0, n / theory - handed) + 1.0)
    poll.close()
    last, still = -1, time.time()
    while time.time() - still < 2.0 and time.time() - t0 < a.secs * 4 + 60:
        if cl.got != last:
            last, still = cl.got, time.time()
        time.sleep(0.2)
    c1, back_s = api_wait(dut, "/api/can")
    if c1 is None:
        cl.close()
        check("%s the web API answers again after it" % tag, False, "nothing for %.0f s" % back_s)
        return {"tier": tag, "line_rate": round(theory), "frames": n, "client": cl.got, "api": "silent"}
    s1, _ = dut.slcan_stats()
    f1, _ = dut.flash()
    cl.close()
    flash = {k: f1.get(k, 0) - f0.get(k, 0) for k in ("writes", "erases") if f1.get(k, 0) != f0.get(k, 0)}

    rate = poll.wire_rate(c0.get("rx", 0) + c0.get("rx_missed", 0), n)
    took = (cl.last_at - cl.first_at) if cl.got > 1 else 0.0
    d_rx = c1.get("rx", 0) - c0.get("rx", 0)
    moved = {k: c1.get(k, 0) - c0.get(k, 0) for k in CAN_COUNTERS if c1.get(k, 0) != c0.get(k, 0)}
    d_drop = s1.get("tx_drops", 0) - s0.get("tx_drops", 0)
    print("   handed over in %.1f s; on the wire %s; the client's %d lines came in %.1f s (%.0f a second); "
          "the API: %d answers, %d unanswered, slowest %.2f s; the flash: %s"
          % (handed, ("%.0f frames/s (%.1f %% of the line rate)" % (rate, 100.0 * rate / theory)) if rate
             else "not measured (too few answers of the API)",
             cl.got, took, cl.got / took if took else 0.0, len(poll.samples), poll.unanswered, poll.slowest,
             ("WRITTEN during the tier, " + ", ".join("%s +%d" % kv for kv in sorted(flash.items()))) if flash
             else "at rest"),
          flush=True)
    res = {"tier": tag, "line_rate": round(theory), "wire_rate": round(rate), "frames": n, "rx": d_rx,
           "client": cl.got, "missing": cl.missing, "tx_drops": d_drop, "moved": moved, "flash": flash,
           "api_answers": len(poll.samples), "api_unanswered": poll.unanswered,
           "api_slowest_s": round(poll.slowest, 2)}

    if not overload:
        # what the tier asserts holds with the flash at rest: a write inside it
        # is the bench's own trouble (it did not wait) or a write nobody knew of
        check("%s the unit's flash was at rest all through it" % tag, not flash,
              ", ".join("%s +%d" % kv for kv in sorted(flash.items())) if flash else "no write, no erase")
        check("%s the bus was full" % tag, rate >= theory * 0.95, "%.0f of %.0f frames/s" % (rate, theory))
        check("%s the CAN driver took every frame" % tag, d_rx == n and not moved,
              "rx +%d of %d%s" % (d_rx, n, (", " + str(moved)) if moved else ""))
        check("%s the TCP client got every frame, once and in order" % tag,
              cl.got == n and cl.missing == 0 and cl.out_of_order == 0 and d_drop == 0 and not cl.closed_by_peer,
              "%d of %d, missing %d, out of order %d, tx_drops +%d%s"
              % (cl.got, n, cl.missing, cl.out_of_order, d_drop, ", CLOSED by the unit" if cl.closed_by_peer else ""))
        check("%s the web API answered all through it" % tag, poll.unanswered == 0 and len(poll.samples) >= 4,
              "%d answers, %d unanswered, slowest %.2f s" % (len(poll.samples), poll.unanswered, poll.slowest))
        return res

    # Above what the unit takes, frames are lost. What must hold: each one is
    # in a counter with a name, the client keeps its connection and its order,
    # and the unit is itself again when the bus lets go.
    missed = moved.get("rx_missed", 0)
    over = moved.get("rx_overrun", 0)
    fanout = moved.get("dispatch_drops", 0)
    other = {k: v for k, v in moved.items() if k not in ("rx_missed", "rx_overrun", "dispatch_drops")}
    fifo = n - d_rx - missed          # what the controller's own FIFO lost: one frame or two an overrun
    check("%s every frame the CAN driver lost is counted (rx_missed, rx_overrun)" % tag,
          0 <= n - d_rx and over <= fifo <= 2 * over and not other,
          "%d of %d taken, rx_missed +%d, rx_overrun +%d for %d frames%s"
          % (d_rx, n, missed, over, fifo, (", ALSO " + str(other)) if other else ""))
    check("%s every frame the bridge lost is counted (dispatch_drops, tx_drops)" % tag,
          abs((d_rx - cl.got) - (fanout + d_drop)) <= 2,
          "the client got %d of the %d taken, dispatch_drops +%d, tx_drops +%d" % (cl.got, d_rx, fanout, d_drop))
    check("%s the client kept its connection and the order of what it got" % tag,
          not cl.closed_by_peer and cl.out_of_order == 0 and cl.got > 0,
          "%d lines, out of order %d%s" % (cl.got, cl.out_of_order, ", CLOSED by the unit" if cl.closed_by_peer else ""))
    check("%s the unit is itself again after it" % tag,
          back_s < 10.0 and c1.get("state") == "running" and c1.get("bus_off", 0) == c0.get("bus_off", 0),
          "the API answered after %.1f s, CAN %s" % (back_s, c1.get("state")))
    res["lost_pct"] = round(100.0 * (n - cl.got) / n, 2)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081", help="the DUT's web API (host:port, a tunnel is fine)")
    ap.add_argument("--slcan", default="localhost:18333", help="the DUT's slcan server (host:port)")
    ap.add_argument("--pcan", default=os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"))
    ap.add_argument("--tiers", default=DEFAULT_TIERS,
                    help="kbit/s:shape, comma separated; a ! after a shape marks a tier above what the unit takes")
    ap.add_argument("--secs", type=float, default=10.0, help="how long each tier keeps the bus full")
    ap.add_argument("--keep-sim", action="store_true", help="leave ECU sim v2 alone (a bus without it)")
    ap.add_argument("--no-flash-wait", action="store_true",
                    help="start the tiers as soon as the unit is up, as the bench did until 2026-11-19: the first "
                         "tier then runs through the configuration backup's refresh (to see what that costs)")
    ap.add_argument("--log-level", default="",
                    help="the unit's log levels during the tiers, tag=level[,tag=level] ('*' = every tag), "
                         "to see what the console costs: '*=none'")
    a = ap.parse_args()

    dut = Dut(a.dut)
    sl_host, sl_port = a.slcan.split(":")[0], int(a.slcan.split(":")[1])
    tiers = {}                       # kbit/s -> [(shape, overload)], in the order given
    for t in [x for x in a.tiers.split(",") if x]:
        kbit, name = t.split(":")
        tiers.setdefault(int(kbit), []).append((Shape(name.rstrip("!")), name.endswith("!")))
    t_start = time.time()
    planned = 0

    # asked again for up to 40 s: the stage's console capture opens the console
    # port just before this, which can reset the unit, and a request through
    # the tunnel to a unit that is restarting fails at once (2026-11-18)
    st, _ = api_wait(dut, "/api/status", 40.0)
    if not st:
        print("CAN MAX RATE FAIL: no DUT at", a.dut)
        sys.exit(1)
    boot0, unexp0 = st.get("boot_count"), st.get("unexpected_resets")
    err0 = (st.get("health") or {}).get("log_errors")
    info = dut.get("/api/info") or {}
    print("dut: %s (build %s, %s) boot_count=%s unexpected=%s log_errors=%s"
          % (st.get("version"), info.get("fw_build"), info.get("partition"), boot0, unexp0, err0), flush=True)
    comps = ("can_manager", "autopid", "socket_manager", "bridge_manager")
    found = {c: dut.settings(c) for c in comps}
    print("as found: can=%s autopid=%s bridges=%s" % (
        {k: found["can_manager"].get(k) for k in ("enabled", "baud", "silent")},
        found["autopid"].get("enabled"),
        [(b["name"], b["a"], b["b"], b.get("enabled")) for b in found["bridge_manager"]["bridges"]]), flush=True)
    sim_found = None
    results = []

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
                raise RuntimeError("the DUT did not come back after the restart (%s)" % why)
            time.sleep(3)
        set_log_levels()

    def set_log_levels():
        # --log-level: a log level lasts until the unit's next restart, so it
        # is set again after each one (the last restart, back to the settings
        # as found, also ends it)
        for item in [x for x in a.log_level.split(",") if x]:
            tag, _, level = item.partition("=")
            dut.api("/api/logs/level", "PUT", {"tag": tag, "level": level})
            print("log level for the run: %s = %s" % (tag, level), flush=True)

    try:
        for kbit, shapes in tiers.items():
            print("== %d kbit/s" % kbit, flush=True)
            if not a.keep_sim:
                if sim_found is None:
                    cfg = bench_bus.sim_get()
                    sim_found = {k: cfg[k] for k in SIM_HS_QUIET + ("hs_bitrate_kbps",) if k in cfg}
                    print("the simulator's HS CAN as found: %s" % sim_found, flush=True)
                quiet = {k: False for k in sim_found if k != "hs_bitrate_kbps"}
                quiet["hs_bitrate_kbps"] = kbit
                bench_bus.sim_patch({"ecu_sim": quiet})
            want = {c: json.loads(json.dumps(found[c])) for c in comps}
            want["can_manager"].update({"enabled": True, "baud": str(kbit), "silent": False})
            want["autopid"]["enabled"] = False
            srv = next((s for s in want["socket_manager"]["servers"] if s["name"] == "slcan0"), None)
            if srv is None:
                srv = {"name": "slcan0", "proto": "tcp", "port": 3333}
                want["socket_manager"]["servers"].append(srv)
            srv.update({"enabled": True, "proto": "tcp", "port": 3333})
            rows = [b for b in want["bridge_manager"]["bridges"]
                    if b["name"] != "br_slcan" and "slcan0" not in (b.get("a"), b.get("b"))]
            rows.append({"name": "br_slcan", "a": "can", "b": "slcan0", "translator": "slcan", "enabled": True})
            while len(rows) > 6:      # six rows at most: one is lent for the run
                spare = next(b for b in reversed(rows) if b["name"] != "br_slcan" and "can" not in (b.get("a"), b.get("b")))
                print("no free connection row: %s is left out for the run" % spare["name"], flush=True)
                rows.remove(spare)
            want["bridge_manager"]["bridges"] = rows
            submit({c: want[c] for c in comps if want[c] != dut.settings(c)}, "%d kbit/s, AutoPID off, br_slcan" % kbit)
            c = dut.api("/api/can")
            up = {b["name"]: b.get("up") for b in (dut.get("/api/bridges") or {}).get("bridges", [])}
            _, buf = dut.slcan_stats()
            print("the unit: CAN %s kbit/s %s, br_slcan up=%s, client buffer %s bytes"
                  % (c.get("baud_kbps"), c.get("state"), up.get("br_slcan"), buf), flush=True)
            if not check("%dk the unit's CAN interface runs at the bit rate with br_slcan up" % kbit,
                         c.get("baud_kbps") == kbit and c.get("state") == "running" and up.get("br_slcan") is True,
                         {"baud_kbps": c.get("baud_kbps"), "state": c.get("state"), "br_slcan": up.get("br_slcan")}):
                continue
            if a.no_flash_wait:
                ops, up = dut.flash()
                print("the unit's flash: NOT waited for (--no-flash-wait), up %d s, %s" % (up, ops), flush=True)
            else:
                ops, up, waited = dut.wait_flash_at_rest()
                print("the unit's flash at rest after %.0f s (up %d s): writes %s, erases %s since boot"
                      % (waited, up, ops.get("writes"), ops.get("erases")), flush=True)
            bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=kbit * 1000)
            try:
                # a quiet bus, or the counts mean nothing
                rx_a = dut.api("/api/can").get("rx", 0)
                time.sleep(3.0)
                rx_b = dut.api("/api/can").get("rx", 0)
                if not check("%dk the bus is quiet before the tiers" % kbit, rx_b == rx_a, "rx +%d in 3 s" % (rx_b - rx_a)):
                    continue
                # and the PCAN is on it: 20 frames must arrive, or every tier
                # would only measure the wrong plug
                probe = Shape("std8")
                for i in range(20):
                    try:
                        bus.send(probe.message(i), timeout=0.5)
                    except can.CanError:
                        pass
                    time.sleep(0.01)
                time.sleep(1.0)
                rx_c = dut.api("/api/can").get("rx", 0)
                if not check("%dk the PCAN (%s) reaches the unit's bus" % (kbit, a.pcan), rx_c - rx_b == 20,
                             "20 frames sent, rx +%d" % (rx_c - rx_b)):
                    continue
                for shape, overload in shapes:
                    results.append(run_tier(a, dut, bus, kbit, shape, overload, sl_host, sl_port))
                    time.sleep(3.0)
            finally:
                bus.shutdown()
    except Exception as e:
        check("the tiers ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        print("== restore", flush=True)
        try:
            if sim_found is not None:
                bench_bus.sim_patch({"ecu_sim": sim_found})
            submit({c: found[c] for c in comps if found[c] != dut.settings(c)}, "the settings as found")
            back = {c: dut.settings(c) for c in comps}
            check("settings back as found", back == found, {c: "same" if back[c] == found[c] else "DIFFERS" for c in comps})
            st = dut.get("/api/status") or {}
            check("only the planned restarts (%d)" % planned,
                  st.get("boot_count") == boot0 + planned and st.get("unexpected_resets") == unexp0,
                  {"boot_count": (boot0, st.get("boot_count")), "unexpected": (unexp0, st.get("unexpected_resets"))})
            err1 = (st.get("health") or {}).get("log_errors")
            check("no new E lines", err1 == err0, (err0, err1))
        except Exception as e:
            check("the restore ran to the end", False, "%s: %s" % (type(e).__name__, e))

    print("RESULTS " + json.dumps(results), flush=True)
    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t_start), flush=True)
    if fails:
        print("CAN MAX RATE FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("CAN MAX RATE PASS")


if __name__ == "__main__":
    main()
