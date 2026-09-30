#!/usr/bin/env python3
"""Side-by-side table for a recorded app session (elm_tcp_tap.py log) and
its replays (elm_latency_probe.py --replay ... --raw X.json) on other
adapters: per poll command p50/p95 RTT, bad answers, and the app's own
pacing gap from the tap log.

usage: elm_replay_compare.py TAP_LOG [label=replay.json ...]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from elm_tcp_tap import parse_log, analyse, pct, is_at  # noqa: E402


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    init, polls, bad, out = analyse(parse_log(sys.argv[1]))
    cols = {"recorded (app)": {}}
    by = {}
    for tc, c, tf, tp, n, txt in polls:
        if tp is not None:
            by.setdefault(c, []).append(tp - tc)
    for c, v in by.items():
        cols["recorded (app)"][c] = (len(v), pct(v, 50), pct(v, 95),
                                     sum(1 for p in polls if p[1] == c and p in bad))
    for arg in sys.argv[2:]:
        label, path = arg.split("=", 1)
        d = json.load(open(path))
        col = {}
        byc = {}
        for x in d["samples"]:
            if is_at(x["cmd"]):
                continue
            byc.setdefault(x["cmd"], []).append(x)
        for c, xs in byc.items():
            v = [x["rtt"] for x in xs if x["rtt"] is not None]
            badn = sum(1 for x in xs if x["rtt"] is None or "NO DATA" in x["text"]
                       or "STOPPED" in x["text"] or "?" in x["text"])
            col[c] = (len(xs), pct(v, 50), pct(v, 95), badn)
        cols[label] = col
    cmds = sorted({c for col in cols.values() for c in col},
                  key=lambda c: -sum(col.get(c, (0,))[0] for col in cols.values()))
    labels = list(cols)
    print("| command | " + " | ".join(f"{l} n / p50 / p95 / bad" for l in labels) + " |")
    print("|---|" + "---|" * len(labels))
    for c in cmds[:12]:
        cells = []
        for l in labels:
            v = cols[l].get(c)
            cells.append("-" if v is None else f"{v[0]} / {v[1]} / {v[2]} / {v[3]}")
        print(f"| `{c}` | " + " | ".join(cells) + " |")
    print()
    print("recorded session: app gap prompt->next request p50/p95 ms =",
          out["app_gap_ms"]["p50"], "/", out["app_gap_ms"]["p95"],
          "| WiCAN RTT p50/p95 =", out["rtt_ms"]["p50"], "/", out["rtt_ms"]["p95"],
          "| pipelined", out["pipelined_requests"], "| bad", out["bad_responses"])


if __name__ == "__main__":
    main()
