#!/usr/bin/env python3
"""An slcan TCP client that stalls like a phone radio does (runs on the Pi).

  python3 slcan_stall_client.py <dut_ip> <port> <secs> <gap_ms> <every_s> <mode>
          [quiet_end_s]

mode:
  none  read all the time (the laptop case)
  delay every <every_s> this client's packets to the DUT are held back by
        <gap_ms> for <gap_ms> (tc netem): nothing is lost, the ACKs arrive
        late. To the DUT that is <gap_ms> without an ACK, the case of a
        radio that was away while the access point kept its frames.
  ack   every <every_s> this client's packets to the DUT are dropped for
        <gap_ms> (nft, output hook): the DUT's data still arrives, its ACKs
        never leave. The DUT then waits for its own retransmit timer, so the
        stall it sees is longer than the gap: the case of a client that is
        gone.
  app   a small receive buffer and a reader that sleeps <gap_ms> every
        <every_s>: an app that stops reading for a moment.

Only this client's connection is touched: it connects from one of 64 local
ports (40320 to 40383) and the rule or the delay matches those ports, so another client on
the same Pi (a tunnel from the PC) and the web API keep their timing.

The last <quiet_end_s> seconds (default 1) carry no stall, so the stream has
caught up when the run ends and the count of lost frames is whole.

The generator on the bus sends id 0x123, DLC 8, a 32-bit counter in bytes
0..3 (big endian), so frames lost inside one connection are counted.
Prints one EVENT line per connect / close and a last RESULT line (JSON).
Needs sudo -n for nft in mode ack and for tc in mode delay.
Used by slcan_bridge_test.py (leg S of the stage slcan).
"""
import json
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
gaps = []            # (asked_at, in_force_from, in_force_until)
pause_until = [0.0]
TABLE = "slcanrepro"
SPORT0 = 40320       # 64 local ports, 40320..40383: one mask for tc, one range for nft
SPORTS = 64
PORT_FILE = "/tmp/slcan_stall_port"   # the next port to use, kept across runs


def next_sport():
    """The ports are used in turn, across runs: an address pair comes back
    after 64 connections, long after the DUT has forgotten the last one (a
    pair reused within its two minutes of TIME_WAIT is answered late)."""
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
        # a prio qdisc whose map sends everything to band 2; only this
        # client's packets (the filter) go to band 1, where netem sits
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
    # a gap must end inside the run: one that is cut short would leave the
    # rule or the delay in place for the next run
    while max(t, now()) + gap_ms / 1000.0 < secs - quiet_end_s:
        time.sleep(max(0.0, t - now()))
        if mode == "ack":
            a = now()
            nft("add", "rule", "ip", TABLE, "out", "ip", "daddr", dut, "tcp", "dport", str(port),
                "tcp", "sport", "%d-%d" % (SPORT0, SPORT0 + SPORTS - 1), "drop")
            b = now()
            time.sleep(gap_ms / 1000.0)
            nft("flush", "chain", "ip", TABLE, "out")
            c = now()
            gaps.append((a, b, c))
        elif mode == "delay":
            a = now()
            tc("qdisc", "change", "dev", dev, "parent", "1:1", "handle", "10:", "netem", "delay", "%dms" % gap_ms)
            b = now()
            time.sleep(gap_ms / 1000.0)
            tc("qdisc", "change", "dev", dev, "parent", "1:1", "handle", "10:", "netem", "delay", "0ms")
            c = now()
            gaps.append((a, b, c))
        elif mode == "app":
            a = now()
            pause_until[0] = a + gap_ms / 1000.0
            gaps.append((a, a, a + gap_ms / 1000.0))
        t = max(t + every_s, now() + 0.5)
    if mode == "ack":
        nft("delete", "table", "ip", TABLE)
    if mode == "delay":
        # the packets of the last gap are still waiting in netem: taking
        # the qdisc away now would throw them away (a stall of seconds that
        # is the tool's, found 2026-11-17)
        time.sleep(gap_ms / 1000.0 + 0.3)
        tc("qdisc", "del", "dev", dev, "root")


def since_gap(t):
    """ms from the start of the latest gap before t, or None"""
    before = [g for g in gaps if g[0] <= t]
    return round((t - before[-1][0]) * 1000) if before else None


def main():
    th = threading.Thread(target=gapper, daemon=True)
    if mode in ("ack", "delay", "app") and gap_ms > 0:
        th.start()
        time.sleep(0.4 if mode != "app" else 0.0)   # the qdisc or the table stands before the connect
    conns = []
    while now() < secs:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if mode == "app":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2304)
        s.settimeout(5)
        t_try = now()
        try:
            s.bind(("", next_sport()))
            s.connect((dut, port))
        except Exception as e:
            print("EVENT connect_fail t=%.3f %s" % (now(), e), flush=True)
            s.close()
            time.sleep(0.2)
            continue
        tc_ = now()
        print("EVENT connect t=%.3f took_ms=%d" % (tc_, round((tc_ - t_try) * 1000)), flush=True)
        s.sendall(b"C\rS6\rO\r")
        s.settimeout(0.5)
        buf = b""
        first = last = None
        cnt = 0
        nbytes = 0
        kind = "run_end"
        while now() < secs:
            if mode == "app" and now() < pause_until[0]:
                time.sleep(0.005)
                continue
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
            parts = buf.split(b"\r")
            buf = parts.pop()
            for line in parts:
                if len(line) == 21 and line.startswith(b"t1238"):
                    try:
                        c = int(line[5:13], 16)
                    except ValueError:
                        continue
                    if first is None:
                        first = c
                    last = c
                    cnt += 1
        te = now()
        lost = (last - first + 1 - cnt) if cnt else 0
        if kind != "run_end":
            print("EVENT close kind=%s t=%.3f alive_s=%.2f since_gap_ms=%s frames=%d lost_inside=%d"
                  % (kind, te, te - tc_, since_gap(te), cnt, lost), flush=True)
        conns.append({"start": round(tc_, 3), "end": round(te, 3), "kind": kind, "frames": cnt,
                      "lost_inside": lost, "bytes": nbytes, "since_gap_ms": since_gap(te)})
        try:
            s.close()
        except Exception:
            pass
    if th.is_alive():
        th.join(timeout=2 * gap_ms / 1000.0 + 5)
    closes = [c for c in conns if c["kind"] != "run_end"]
    print("RESULT " + json.dumps({
        "mode": mode, "gap_ms": gap_ms, "every_s": every_s, "secs": secs,
        "connections": len(conns), "closes": len(closes),
        "kinds": sorted(set(c["kind"] for c in closes)),
        "frames": sum(c["frames"] for c in conns),
        "lost_inside": sum(c["lost_inside"] for c in conns),
        "bytes": sum(c["bytes"] for c in conns),
        "gaps": len(gaps),
        "gap_real_ms": [round((g[2] - g[1]) * 1000) for g in gaps],
        "gap_cmd_ms": [round((g[1] - g[0]) * 1000) for g in gaps],
    }), flush=True)


main()
