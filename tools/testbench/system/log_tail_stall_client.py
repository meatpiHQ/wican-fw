#!/usr/bin/env python3
"""A client of the unit's TCP log tail that stalls like a phone radio does
(runs on the Pi; the sibling of can/slcan_stall_client.py).

  python3 log_tail_stall_client.py <dut_ip> <port> <secs> <gap_ms> <every_s> <mode>
          [quiet_end_s]

mode:
  none   read all the time
  delay  every <every_s> this client's packets to the unit are held back by
         <gap_ms> for <gap_ms> (tc netem): nothing is lost, the ACKs arrive
         late. To the unit that is <gap_ms> without an ACK.
  ack    every <every_s> this client's packets to the unit are dropped for
         <gap_ms> (nft, output hook): the case of a client that is gone.

Only this client's connection is touched: it connects from one of 64 local
ports (40384 to 40447) and the rule or the delay matches those ports.

The unit's `logsinks emit <n>` prints lines that end in `LSBENCH i/n`; the
client counts the numbers it saw per connection, so the lines lost inside a
connection are known. One EVENT line per connect / close and a last RESULT
line (JSON). `max_quiet_ms` in it is the longest time without a byte from
the unit between the first numbered line and the last: a unit whose ACKs
do not come stops sending once its own buffer is full, so this number says
that a stall was in force. Needs sudo -n for nft (mode ack) and tc (mode delay).
Used by log_tail_stall_bench.py.
"""
import json
import re
import socket
import subprocess
import sys
import threading
import time

dut = sys.argv[1]
port = int(sys.argv[2])
secs = float(sys.argv[3])
gap_ms = int(sys.argv[4])
every_s = float(sys.argv[5])
mode = sys.argv[6]
quiet_end_s = float(sys.argv[7]) if len(sys.argv) > 7 else 1.0

T0 = time.monotonic()
gaps = []
TABLE = "logtailrepro"
SPORT0 = 40384       # 64 local ports: one mask for tc, one range for nft
SPORTS = 64
PORT_FILE = "/tmp/log_tail_stall_port"
MARK = re.compile(rb"LSBENCH (\d+)/(\d+)")


def next_sport():
    """in turn, across runs: a pair reused within its TIME_WAIT is answered late"""
    try:
        n = int(open(PORT_FILE).read().strip())
    except Exception:
        n = 0
    try:
        open(PORT_FILE, "w").write(str((n + 1) % SPORTS))
    except Exception:
        pass
    return SPORT0 + n % SPORTS


def now():
    return time.monotonic() - T0


def nft(*args):
    subprocess.run(["sudo", "-n", "nft"] + list(args), check=False, capture_output=True)


def tc(*args):
    subprocess.run(["sudo", "-n", "tc"] + list(args), check=False, capture_output=True)


def iface():
    out = subprocess.run(["ip", "-o", "route", "get", dut], capture_output=True, text=True).stdout.split()
    return out[out.index("dev") + 1]


def gapper():
    dev = iface() if mode == "delay" else None
    if mode == "delay":
        # everything to band 2; only this client's packets to band 1 (netem)
        tc("qdisc", "replace", "dev", dev, "root", "handle", "1:", "prio", "bands", "3",
           "priomap", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1")
        tc("qdisc", "add", "dev", dev, "parent", "1:1", "handle", "10:", "netem", "delay", "0ms")
        tc("filter", "add", "dev", dev, "parent", "1:", "protocol", "ip", "prio", "1", "u32",
           "match", "ip", "dport", str(port), "0xffff",
           "match", "ip", "sport", str(SPORT0), "0x%04x" % (0xFFFF & ~(SPORTS - 1)),
           "flowid", "1:1")
    if mode == "ack":
        nft("add", "table", "ip", TABLE)
        nft("add", "chain", "ip", TABLE, "out", "{ type filter hook output priority 0 ; }")
        nft("flush", "chain", "ip", TABLE, "out")
    t = every_s
    # a gap ends inside the run: one cut short would leave the rule in place
    while max(t, now()) + gap_ms / 1000.0 < secs - quiet_end_s:
        time.sleep(max(0.0, t - now()))
        a = now()
        if mode == "ack":
            nft("add", "rule", "ip", TABLE, "out", "ip", "daddr", dut, "tcp", "dport", str(port),
                "tcp", "sport", "%d-%d" % (SPORT0, SPORT0 + SPORTS - 1), "drop")
            b = now()
            time.sleep(gap_ms / 1000.0)
            nft("flush", "chain", "ip", TABLE, "out")
        else:
            tc("qdisc", "change", "dev", dev, "parent", "1:1", "handle", "10:", "netem", "delay", "%dms" % gap_ms)
            b = now()
            time.sleep(gap_ms / 1000.0)
            tc("qdisc", "change", "dev", dev, "parent", "1:1", "handle", "10:", "netem", "delay", "0ms")
        gaps.append((a, b, now()))
        t = max(t + every_s, now() + 0.5)
    if mode == "ack":
        nft("delete", "table", "ip", TABLE)
    if mode == "delay":
        # the packets of the last gap still wait in netem: let them leave
        time.sleep(gap_ms / 1000.0 + 0.3)
        tc("qdisc", "del", "dev", dev, "root")


def main():
    th = threading.Thread(target=gapper, daemon=True)
    if mode in ("ack", "delay") and gap_ms > 0:
        th.start()
        time.sleep(0.4)             # the qdisc or the table stands before the connect
    conns = []
    while now() < secs:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(5)
        try:
            s.bind(("", next_sport()))
            s.connect((dut, port))
        except Exception as e:
            print("EVENT connect_fail t=%.3f %s" % (now(), e), flush=True)
            s.close()
            time.sleep(0.5)
            continue
        tc_ = now()
        print("EVENT connect t=%.3f" % tc_, flush=True)
        s.settimeout(0.5)
        buf = b""
        seen = set()
        first = last = None
        nbytes = 0
        kind = "run_end"
        t_data = None           # when the last bytes came
        quiet = 0.0             # the longest wait for bytes while lines ran
        said = [0, 0]           # the unit's own lines: "is behind", "caught up"
        while now() < secs:
            try:
                d = s.recv(65536)
            except socket.timeout:
                continue
            except ConnectionResetError:
                kind = "reset"
                break
            except OSError as e:
                kind = "error:%s" % e
                break
            if not d:
                kind = "eof"
                break
            nbytes += len(d)
            buf += d
            cut = buf.rfind(b"\n")
            if cut < 0:
                continue
            said[0] += buf[:cut].count(b"tail client is behind")
            said[1] += buf[:cut].count(b"tail client caught up")
            marks = [int(m.group(1)) for m in MARK.finditer(buf[:cut])]
            if marks:
                if t_data is not None and first is not None:
                    quiet = max(quiet, now() - t_data)
                t_data = now()
            for i in marks:
                first = i if first is None else first
                last = i
                seen.add(i)
            buf = buf[cut + 1:]
        te = now()
        lost = (last - first + 1 - len(seen)) if seen else 0
        if kind != "run_end":
            before = [g for g in gaps if g[0] <= te]
            print("EVENT close kind=%s t=%.3f alive_s=%.2f since_gap_ms=%s lines=%d lost_inside=%d" % (
                kind, te, te - tc_, round((te - before[-1][0]) * 1000) if before else None, len(seen), lost),
                flush=True)
        conns.append({"kind": kind, "lines": len(seen), "lost_inside": lost, "bytes": nbytes,
                      "first": first, "last": last, "quiet": quiet, "said": said})
        s.close()
    if th.is_alive():
        th.join(timeout=2 * gap_ms / 1000.0 + 5)
    closes = [c for c in conns if c["kind"] != "run_end"]
    print("RESULT " + json.dumps({
        "mode": mode, "gap_ms": gap_ms, "connections": len(conns), "closes": len(closes),
        "kinds": sorted(set(c["kind"] for c in closes)),
        "lines": sum(c["lines"] for c in conns), "lost_inside": sum(c["lost_inside"] for c in conns),
        "first": conns[0]["first"] if conns else None, "last": conns[-1]["last"] if conns else None,
        "bytes": sum(c["bytes"] for c in conns), "gaps": len(gaps),
        "max_quiet_ms": round(1000 * max([c["quiet"] for c in conns] or [0])),
        "said_behind": sum(c["said"][0] for c in conns),
        "said_caught_up": sum(c["said"][1] for c in conns),
        "gap_real_ms": [round((g[2] - g[1]) * 1000) for g in gaps]}), flush=True)


main()
