#!/usr/bin/env python3
"""ELM327 TCP tap: a transparent proxy between an OBD app (Car Scanner,
Torque, ...) and a WiCAN's TCP ELM server that records EXACTLY what the
app sends and what comes back, with timestamps per socket read.

Point the app at THIS machine's address (same port) instead of the
WiCAN. Every byte is relayed unchanged; nothing is buffered or
re-framed, so the app sees the WiCAN's own framing.

usage: elm_tcp_tap.py WICAN_HOST [--upstream-port 35000] [--listen 0.0.0.0]
                      [--port 35000] [--log tap.log] [--quiet] [--once]

Writes `--log` (default tap_<timestamp>.log) with one line per read:
    <t_ms> <A|D> <n> <repr(bytes)>        A = app->WiCAN, D = WiCAN->app
and, when the app disconnects (or on Ctrl-C), prints a SUMMARY: the init
commands, the polling commands with their RTT (command CR -> '>' prompt),
reads per response, gap between the two reads (data line vs prompt),
requests the app sent BEFORE the previous prompt arrived (pipelining),
commands answered with NO DATA / STOPPED / '?', and the app's own idle
gaps (time from a prompt to the app's next command > 1 s; the autopid
yield window is 10 s). Replay the recorded command sequence against
another adapter with `elm_latency_probe.py --replay tap.log`.
"""
import argparse
import datetime
import json
import select
import socket
import sys
import time
from collections import Counter


def now_ms():
    return time.perf_counter() * 1000.0


def pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return round(s[int(round(p / 100 * (len(s) - 1)))], 1)


def is_at(cmd):
    u = cmd.upper()
    return u.startswith("AT") or u.startswith("ST") or u.startswith("VT")


def parse_log(path):
    """Read a tap log back into [(t_ms, dir, bytes)]."""
    reads = []
    with open(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            t, d, n, rest = line.split(None, 3)
            reads.append((float(t), d, eval(rest.strip())))  # repr(bytes)
    return reads


def commands(reads):
    """The app's command stream in order: [(t_ms, cmd)] split on CR/LF."""
    cmds = []
    abuf = b""
    for t, d, data in reads:
        if d != "A":
            continue
        abuf += data
        while True:
            cuts = [i for i in (abuf.find(b"\r"), abuf.find(b"\n")) if i >= 0]
            if not cuts:
                break
            i = min(cuts)
            cmd = abuf[:i].decode(errors="replace").strip()
            abuf = abuf[i + 1:]
            if cmd:
                cmds.append((t, cmd))
    return cmds


def analyse(reads):
    """Pair the app's commands with the device's prompt-terminated
    responses. Returns (init_cmds, polls, bad, summary)."""
    cmds = commands(reads)

    # device responses: reads grouped until one ends with the '>' prompt
    resps = []
    cur = []
    for t, d, data in reads:
        if d != "D":
            continue
        cur.append((t, data))
        joined = b"".join(x[1] for x in cur)
        if joined.rstrip().endswith(b">"):
            resps.append((cur[0][0], t, len(cur), joined.decode(errors="replace")))
            cur = []
    if cur:
        joined = b"".join(x[1] for x in cur)
        resps.append((cur[0][0], None, len(cur), joined.decode(errors="replace")))

    # Pair in TIME order: a prompt answers the OLDEST command still waiting.
    # A command that waits longer than UNANSWERED_MS with nothing back is
    # dropped as unanswered (the chip swallows commands sent during an ATZ
    # reset, for instance) so it cannot shift every later pairing. A
    # command sent while another one is still waiting is pipelined.
    UNANSWERED_MS = 1500.0
    events = ([(tc, 0, ("C", tc, cmd)) for tc, cmd in cmds] +
              [(tp if tp is not None else tf, 1, ("R", r)) for r in resps
               for tf, tp, _, _ in [r]])
    events.sort(key=lambda e: (e[0], e[1]))
    pending = []          # [(tc, cmd)]
    pairs = []            # (tc, cmd, tf, tp, nreads, text) in command order
    unanswered = []
    pipelined = 0
    last_prompt = None
    gaps = []             # prompt -> next command (the app's own pacing)
    for t, _, ev in events:
        if ev[0] == "C":
            _, tc, cmd = ev
            while pending and tc - pending[0][0] > UNANSWERED_MS:
                unanswered.append(pending.pop(0))
            if pending:
                pipelined += 1
            elif last_prompt is not None:
                gaps.append(tc - last_prompt)
            pending.append((tc, cmd))
        else:
            tf, tp, nreads, text = ev[1]
            if pending:
                tc, cmd = pending.pop(0)
                pairs.append((tc, cmd, tf, tp, nreads, text))
            last_prompt = tp if tp is not None else tf
    unanswered.extend(pending)
    for tc, cmd in unanswered:
        pairs.append((tc, cmd, None, None, 0, ""))
    pairs.sort(key=lambda p: p[0])

    init = [c for _, c, *_ in pairs if is_at(c)]
    polls = [p for p in pairs if not is_at(p[1])]
    rtts = [tp - tc for tc, _, _, tp, _, _ in polls if tp is not None]
    first = [tf - tc for tc, _, tf, _, _, _ in polls if tf is not None]
    two = [tp - tf for _, _, tf, tp, n, _ in polls if tp is not None and n > 1]
    bad = [(c, txt.strip()) for _, c, _, _, _, txt in polls
           if any(k in txt for k in ("NO DATA", "STOPPED", "?", "UNABLE", "ERROR"))]
    idle = [(round(a[3] / 1000, 1), round((b[0] - a[3]) / 1000, 1))
            for a, b in zip(pairs, pairs[1:])
            if a[3] is not None and b[0] - a[3] > 1000]
    hist = Counter(c.split(" ")[0][:4] for _, c, *_ in polls)
    span = (pairs[-1][0] - pairs[0][0]) / 1000 if len(pairs) > 1 else 0
    out = {
        "commands": len(cmds), "at_commands": len(init), "polls": len(polls),
        "unanswered": len(unanswered),
        "span_s": round(span, 1),
        "poll_rate_hz": round(len(polls) / span, 1) if span else None,
        "rtt_ms": {"p50": pct(rtts, 50), "p90": pct(rtts, 90),
                   "p95": pct(rtts, 95), "max": pct(rtts, 100)},
        "app_gap_ms": {"p50": pct(gaps, 50), "p90": pct(gaps, 90),
                       "p95": pct(gaps, 95), "max": pct(gaps, 100)},
        "first_read_ms": {"p50": pct(first, 50), "p95": pct(first, 95)},
        "data_to_prompt_gap_ms": {"n": len(two), "p50": pct(two, 50),
                                  "p95": pct(two, 95)},
        "multi_read_responses": sum(1 for p in polls if p[4] > 1),
        "pipelined_requests": pipelined,
        "bad_responses": len(bad),
        "stalls_gt150": sum(1 for r in rtts if r > 150),
        "app_idle_gaps_gt1s": idle[:20],
        "poll_hist": dict(hist.most_common(20)),
    }
    return init, polls, bad, out


def report(reads):
    init, polls, bad, out = analyse(reads)
    print("INIT", init)
    print("FIRST POLLS", [(round(tc), c) for tc, c, *_ in polls[:12]])
    if bad:
        print("BAD (first 10)", bad[:10])
    print("SUMMARY", json.dumps(out))


class Tap:
    def __init__(self, log, quiet):
        self.log = log
        self.quiet = quiet
        self.t0 = now_ms()
        self.reads = []

    def record(self, d, data):
        t = now_ms() - self.t0
        self.reads.append((t, d, data))
        line = f"{t:10.1f} {d} {len(data):4d} {data!r}\n"
        self.log.write(line)
        self.log.flush()
        if not self.quiet:
            sys.stdout.write(line)


def serve(args):
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((args.listen, args.port))
    ls.listen(1)
    print(f"tap: listening on {args.listen}:{args.port} -> "
          f"{args.host}:{args.upstream_port}; point the app here")
    while True:
        app, peer = ls.accept()
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = args.log or f"tap_{stamp}.log"
        print(f"tap: app connected from {peer[0]}:{peer[1]}, log {path}")
        up = socket.create_connection((args.host, args.upstream_port), timeout=10)
        for s in (app, up):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.setblocking(False)
        with open(path, "w") as log:
            log.write(f"# tap {stamp} app={peer[0]} wican={args.host}:"
                      f"{args.upstream_port}\n")
            tap = Tap(log, args.quiet)
            try:
                while True:
                    r, _, _ = select.select([app, up], [], [], 1.0)
                    if app in r:
                        data = app.recv(4096)
                        if not data:
                            break
                        tap.record("A", data)
                        up.sendall(data)
                    if up in r:
                        data = up.recv(4096)
                        if not data:
                            print("tap: WiCAN closed the connection")
                            break
                        tap.record("D", data)
                        app.sendall(data)
            except KeyboardInterrupt:
                pass
            except OSError as e:
                print(f"tap: socket error {e}")
            finally:
                app.close()
                up.close()
        report(tap.reads)
        if args.once:
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", help="the WiCAN's address")
    ap.add_argument("--upstream-port", type=int, default=35000)
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=35000)
    ap.add_argument("--log", default="")
    ap.add_argument("--quiet", action="store_true", help="don't echo every read")
    ap.add_argument("--once", action="store_true", help="exit after one app session")
    ap.add_argument("--analyse", default="", metavar="TAP_LOG",
                    help="re-print the summary of a recorded log and exit")
    args = ap.parse_args()
    if args.analyse:
        report(parse_log(args.analyse))
        return
    if not args.host:
        ap.error("WICAN_HOST is required unless --analyse is given")
    serve(args)


if __name__ == "__main__":
    main()
