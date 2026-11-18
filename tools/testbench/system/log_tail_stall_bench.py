#!/usr/bin/env python3
"""The TCP log tail and a client whose link stalls -> LOG TAIL STALL PASS.

The log tail (`nc <unit> 5515`, the `tcp` sink of log_sinks) wrote to its
clients with a blocking send and closed a client whose send did not finish:
the defect the TCP servers had until 2026-11-17. lwIP holds 20 KB
unacknowledged; behind that the sink's flusher stood in the send for up to
a second (the 200 ms limit is only looked at when an ACK arrives or once a
second), the ring of 8 KB ran over meanwhile, a second client and the other
network sinks got nothing, and a stall of a few seconds closed the client
with a D line only.

The unit's `logsinks emit <n> <gap_ms>` is the generator: n lines, each
ending in `LSBENCH i/n`. log_tail_stall_client.py on the Pi is the client;
it holds its ACKs back (tc netem) or drops them (nft).

Legs (each check prints PASS/FAIL/SKIP; the verdict is the last line):
  G  gates: the tail port is closed as found (the sink ships off).
  L1 no stall: the client holds and gets every line.
  L2 ACKs late by --short-ms (2 s) every 4 s: no close, no line lost,
     the sink's `dropped` unchanged.
  L3 ACKs late by --long-ms (8 s), more than the client's backlog holds:
     the client stays, the lines it lost are counted in `dropped`.
  L4 a client that keeps up (on this PC, through --tail) beside one that is
     behind loses nothing.
  L5 a client that takes nothing for 10 s is closed, with a W line in the
     log; the next client receives.
  R  settings back as found (the tail port closed again), planned restarts
     only, no reset of its own, no new E line.

  python log_tail_stall_bench.py --dut localhost:18081 --tail localhost:18515
         --dut-ip 10.42.1.41 --pi rpi001 [--gap-ms 2]
The generator writes a line every 2 ms (about 27 KB a second). At a line a
millisecond the log pipeline itself (64 lines deep) loses lines whenever a
task of its priority is busy, which says nothing about the sink. It can at
a line every 2 ms too: while the unit writes its flash (the configuration
copies, 20 s after a boot) and during that component's check every two
minutes (2026-11-18: 25 of 9,000 lines, missing on the console as well).
Those lines never reach a sink and are in the pipeline's own counter
(`dropped` on /api/logs/status), so "no line lost" in L1, L2 and L4 is:
none beyond what that counter moved by.
The tunnel is the caller's (ssh -N -L 18081:<unit>:80 -L 18515:<unit>:5515
rpi001). Needs sudo -n, tc (netem) and nft on the Pi.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from dp_bench import Checks, Dut, Staged  # noqa: E402

CLIENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log_tail_stall_client.py")
PORT = 5515
MARK = re.compile(rb"LSBENCH (\d+)/(\d+)")
ck = Checks("LOG TAIL STALL")


def sink(dut):
    """the tcp line of the console command `logsinks`"""
    out = dut.cli("logsinks", marker="file ", quiet_s=2)
    m = re.search(r"^tcp\s+(\d)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+clients=(\d+)", out, re.M)
    if not m:
        return {}
    st = dict(zip(("enabled", "in", "out", "dropped", "buffered", "clients"), (int(x) for x in m.groups())))
    # what the log pipeline itself dropped before any sink (its queue was full)
    st["pipeline"] = (dut.get("/api/logs/status") or {}).get("dropped", 0)
    return st


def delta(s0, s1, key):
    return (s1.get(key) or 0) - (s0.get(key) or 0)


def free_places(dut):
    """Both places of the tail free before a leg. The sink reads nothing from
    its clients: one that left is noticed when a line to it fails (the second
    line after it left), so on a unit that logs nothing the clients of the leg
    before still hold the two places and the next client is turned away
    (2026-11-18: three connections of leg L5 ended after 0.1 s). A few lines
    make the sink look."""
    left = -1
    for _ in range(5):
        left = sink(dut).get("clients", 0)
        if not left:
            return 0
        dut.cli("logsinks emit 3 100", marker="emitted", quiet_s=3)
        time.sleep(0.5)
    return left


def stall_ready(pi):
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", pi,
                            "sudo -n true && sudo -n tc -V >/dev/null && sudo -n nft -v >/dev/null && echo STALL_OK"],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:
        return "ssh to the Pi failed: %s" % e
    return "" if "STALL_OK" in r.stdout else "sudo, tc (netem) or nft missing on the Pi"


def stall_clean(pi, dut_ip):
    """no rule and no delay left on the Pi, whatever a run left behind"""
    subprocess.run(["ssh", "-o", "BatchMode=yes", pi,
                    "sudo -n nft delete table ip logtailrepro 2>/dev/null; "
                    "sudo -n tc qdisc del dev $(ip -o route get %s | sed -n 's/.* dev \\([^ ]*\\).*/\\1/p') root "
                    "2>/dev/null; true" % dut_ip], capture_output=True, timeout=30)


class Emit(threading.Thread):
    """`logsinks emit n gap` on the unit's console, for as long as it takes"""

    def __init__(self, dut, n, gap_ms):
        super().__init__(daemon=True)
        self.dut, self.n, self.gap = dut, n, gap_ms
        self.out = ""
        self.start()

    def run(self):
        secs = self.n * max(self.gap, 1) / 1000.0
        try:
            self.out = self.dut.cli("logsinks emit %d %d" % (self.n, self.gap), marker="emitted",
                                    quiet_s=secs + 15, total_s=secs + 30)
        except Exception as e:
            self.out = "the generator did not run: %s: %s" % (type(e).__name__, e)

    def ran(self):
        return "emitted %d lines" % self.n in self.out


def run(a, dut, n, secs, gap_ms, every_s, mode, quiet_end_s=3.0, connected=None):
    """one run of the Pi client and, from the moment it is connected, one emit
    of n lines: (RESULT, EVENT close lines). @p connected is called at that
    moment.

    The emit waits for the client's own `EVENT connect` line: an ssh to the
    Pi does not begin at once. On 2026-11-18 the lookup of the Pi's name took
    7 s on the PC, the emit (started first, as this bench did then) was over
    before the client was there, and the control leg counted 0 lines."""
    cmd = "python3 /tmp/log_tail_stall_client.py %s %d %g %d %g %s %g" % (
        a.dut_ip, PORT, secs, gap_ms, every_s, mode, quiet_end_s)
    p = subprocess.Popen(["ssh", "-o", "BatchMode=yes", a.pi, cmd], stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    lines = []
    up = threading.Event()

    def read():
        for ln in p.stdout:
            lines.append(ln.rstrip("\n"))
            if ln.startswith("EVENT connect t="):
                up.set()

    th = threading.Thread(target=read, daemon=True)
    th.start()
    if not up.wait(40):
        print("   the client on the Pi did not connect in 40 s: %s" % (lines[-2:] or "no word from it"), flush=True)
        p.kill()
        return None, []
    if connected is not None:
        connected()
    time.sleep(0.3)                 # the sink looks at its listener every 100 ms
    em = Emit(dut, n, a.gap_ms)
    try:
        p.wait(timeout=secs + 90)
    except subprocess.TimeoutExpired:
        p.kill()
    th.join(timeout=5)
    em.join(timeout=60)
    res = None
    for line in lines:
        if line.startswith("RESULT "):
            res = json.loads(line[7:])
    if not em.ran():
        print("   the generator: %s" % (em.out.strip()[-160:] or "no answer"), flush=True)
        res = None
    return res, [ln for ln in lines if ln.startswith("EVENT close")]


class Reader(threading.Thread):
    """a client on this PC that reads all the time and counts the lines"""

    def __init__(self, host, port):
        super().__init__(daemon=True)
        self.s = socket.create_connection((host, port), timeout=5)
        self.s.settimeout(0.5)
        self.seen = set()
        self.first = self.last = None
        self.closed = False
        self.halt = threading.Event()
        self.start()

    def run(self):
        buf = b""
        while not self.halt.is_set():
            try:
                d = self.s.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                self.closed = True
                break
            if not d:
                self.closed = True
                break
            buf += d
            cut = buf.rfind(b"\n")
            if cut < 0:
                continue
            for m in MARK.finditer(buf[:cut]):
                i = int(m.group(1))
                self.first = i if self.first is None else self.first
                self.last = i
                self.seen.add(i)
            buf = buf[cut + 1:]

    def lost(self):
        return (self.last - self.first + 1 - len(self.seen)) if self.seen else 0

    def close(self):
        self.halt.set()
        self.join(timeout=2)
        self.s.close()


def port_open(host, port):
    try:
        socket.create_connection((host, port), timeout=3).close()
        return True
    except Exception:
        return False


def legs(a, dut):
    rate = 1000 // max(a.gap_ms, 1)     # lines a second, at most

    # L1: the control. Not in the unit's first half minute: it writes its
    # flash then, and a line lost to that is not the sink's
    up = dut.settled(30)
    free_places(dut)
    s0 = sink(dut)
    res, _ = run(a, dut, 6 * rate, 10, 0, 3, "none")
    s1 = sink(dut)
    ck.check("L1 no stall: the client holds and gets every line",
             bool(res) and res["closes"] == 0 and res["lost_inside"] <= delta(s0, s1, "pipeline")
             and res["last"] == 6 * rate and s1.get("dropped") == s0.get("dropped"),
             res and {"closes": res["closes"], "lines": res["lines"], "lost": res["lost_inside"],
                      "first": res["first"], "last": res["last"],
                      "dropped": (s0.get("dropped"), s1.get("dropped")),
                      "pipeline_dropped": delta(s0, s1, "pipeline"), "unit_up_s_at_start": up})

    # L2: ACKs late by a second (a firmware before the fix closed at 200 ms
    # once 20 KB were not acknowledged)
    n = 18 * rate
    free_places(dut)
    s0 = sink(dut)
    res, closes = run(a, dut, n, 26, a.short_ms, 4, "delay", 5)
    s1 = sink(dut)
    # the stall was in force when the unit stopped sending for a part of it:
    # its own 20 KB are full after a second or so of this generator
    ck.check("L2 ACKs late by %d ms: no close, no line lost, nothing dropped" % a.short_ms,
             bool(res) and res["gaps"] >= 3 and res["closes"] == 0
             and res["lost_inside"] <= delta(s0, s1, "pipeline")
             and res["last"] == n and s1.get("dropped") == s0.get("dropped")
             and res["max_quiet_ms"] >= a.short_ms / 6,
             res and {"gaps": res["gaps"], "closes": res["closes"], "kinds": res["kinds"], "lines": res["lines"],
                      "unit_silent_ms": res["max_quiet_ms"],
                      "lost": res["lost_inside"], "last": res["last"], "dropped": delta(s0, s1, "dropped"),
                      "pipeline_dropped": delta(s0, s1, "pipeline"), "events": closes[:2]})

    # L3: a stall longer than the backlog holds
    free_places(dut)
    s0 = sink(dut)
    res, closes = run(a, dut, n, 36, a.long_ms, 8, "delay", 8)
    s1 = sink(dut)
    drops = delta(s0, s1, "dropped") + delta(s0, s1, "pipeline")
    ck.check("L3 ACKs late by %.1f s: the client stays" % (a.long_ms / 1000.0),
             bool(res) and res["gaps"] >= 2 and res["closes"] == 0 and res["max_quiet_ms"] >= a.long_ms / 2,
             res and {"gaps": res["gaps"], "closes": res["closes"], "kinds": res["kinds"],
                      "unit_silent_ms": res["max_quiet_ms"], "events": closes[:2]})
    ck.check("L3 the lines it lost are counted (`dropped`) and said in the log",
             # `dropped` counts every line of the unit, the generator's and the rest
             # the unit's line is read from the tail itself (its log ring has
             # rolled over by the end of the leg). Only "caught up" can be:
             # "is behind" is one of the lines this client does not get
             bool(res) and res["lost_inside"] > 0 and res["lost_inside"] <= drops <= res["lost_inside"] * 1.2 + 50
             and res["said_caught_up"] >= 1,
             res and {"lost": res["lost_inside"], "dropped": delta(s0, s1, "dropped"),
                      "pipeline_dropped": delta(s0, s1, "pipeline"),
                      "the_unit_said": {"is behind": res["said_behind"], "caught up": res["said_caught_up"]}})

    # L4: a client that keeps up beside one that is behind
    host, port = a.tail.split(":")[0], int(a.tail.split(":")[1])
    free_places(dut)
    s0 = sink(dut)
    good = Reader(host, port)
    time.sleep(0.5)
    res, _ = run(a, dut, n, 36, a.long_ms, 8, "delay", 8)
    time.sleep(1.0)
    good.close()
    s1 = sink(dut)
    ck.check("L4 a client that keeps up loses nothing beside one that is behind",
             bool(res) and res["closes"] == 0 and not good.closed and good.last == n
             and good.lost() <= delta(s0, s1, "pipeline"),
             {"keeps_up": {"lines": len(good.seen), "lost": good.lost(), "last": good.last, "closed": good.closed,
                          "pipeline_dropped": delta(s0, s1, "pipeline")},
              "behind": res and {"gaps": res["gaps"], "lost": res["lost_inside"], "closes": res["closes"]}})

    # L5: a client that is gone. The generator stops before the close, so the
    # line that says it is still in the unit's log ring when it is read
    free_places(dut)
    g0 = dut.ring_count("took no data for")
    mid = {}
    there = threading.Event()
    t0 = [0.0]

    def look():
        # what the sink holds 13 s and 18 s after the client connected: the
        # generator is done by then and the console is free (the client is
        # away from second 4 to second 19 of its run, the unit lets it go
        # 10 s into that)
        if not there.wait(60):
            return
        for at in (13, 18):
            time.sleep(max(0.0, at - (time.time() - t0[0])))
            try:
                st = sink(dut)
                mid[at] = {k: st.get(k) for k in ("clients", "buffered", "dropped")}
            except Exception as e:
                mid[at] = "%s" % e

    def connected():
        t0[0] = time.time()
        there.set()

    th = threading.Thread(target=look, daemon=True)
    th.start()
    res, closes = run(a, dut, 8 * rate, 30, 15000, 4, "ack", 1, connected)
    th.join(timeout=5)
    ck.check("L5 a client that takes nothing for 10 s is closed, with the reason in the log",
             bool(res) and res["closes"] == 1 and dut.ring_count("took no data for") == g0 + 1
             and isinstance(mid.get(18), dict) and mid[18]["clients"] == 0,
             {"client": res and {k: res[k] for k in ("closes", "kinds", "lines", "lost_inside", "last",
                                                      "gaps", "gap_real_ms")},
              "log": dut.ring_count("took no data for") - g0, "sink_at_13s_and_18s": mid,
              "events": closes[:2]})
    free_places(dut)
    res, _ = run(a, dut, 4 * rate, 8, 0, 3, "none")
    ck.check("L5 the next client receives again",
             bool(res) and res["closes"] == 0 and res["lost_inside"] == 0 and res["last"] == 4 * rate,
             res and {"lines": res["lines"], "lost": res["lost_inside"]})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--tail", default="localhost:18515", help="the unit's log tail port through the tunnel")
    ap.add_argument("--dut-ip", default="10.42.1.41", help="the unit's address as the Pi sees it")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--gap-ms", type=int, default=2, help="between two lines of the generator")
    ap.add_argument("--short-ms", type=int, default=2000)
    ap.add_argument("--long-ms", type=int, default=8000)
    a = ap.parse_args()

    dut = Dut(a.dut)
    h = dut.health(wait_s=40)
    if h["version"] is None:
        print("LOG TAIL STALL FAIL: no unit at", a.dut)
        return 1
    print("unit: %s boot_count=%s unexpected=%s log_errors=%s" % (
        h["version"], h["boot_count"], h["unexpected_resets"], h["log_errors"]), flush=True)
    why = stall_ready(a.pi)
    if why:
        ck.skip("the stall legs", why)
        return ck.finish()
    st = Staged(dut, ("log_sinks",), "LOG TAIL STALL")
    print("as found: log_sinks %s" % {k: st.found["log_sinks"].get(k) for k in ("tcp_enabled", "tcp_port")},
          flush=True)
    host, port = a.tail.split(":")[0], int(a.tail.split(":")[1])
    if not st.found["log_sinks"].get("tcp_enabled"):
        # through a tunnel a closed port still accepts and then closes: read to tell
        try:
            s = socket.create_connection((host, port), timeout=3)
            s.settimeout(2)
            try:
                shut = s.recv(1) == b""
            except socket.timeout:
                shut = False
            s.close()
        except Exception:
            shut = True
        ck.check("G1 the tail port is closed while the sink is off", shut)
    subprocess.run(["scp", "-q", CLIENT, a.pi + ":/tmp/log_tail_stall_client.py"], check=True, timeout=30)
    ls = st.copy("log_sinks")
    ls.update({"tcp_enabled": True, "tcp_port": PORT})
    st.submit({"log_sinks": ls}, "the TCP log tail on")
    try:
        legs(a, dut)
    except Exception as e:
        ck.check("the legs ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        stall_clean(a.pi, a.dut_ip)
    print("== R: restore", flush=True)
    st.restore(ck)
    return ck.finish()


if __name__ == "__main__":
    sys.exit(main())
