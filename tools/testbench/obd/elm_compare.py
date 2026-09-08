#!/usr/bin/env python3
"""Side-by-side ELM327 responsiveness table: the WiCAN (TCP) against a
reference USB adapter (OBDLink SX/EX or any ELM327 USB clone) on the
SAME ECU simulator bus, plus the stored before/after rows of the
2026-09-08 fix. Both loops are the Car Scanner request pattern: the
hinted `010C 1` (fast mode) and the plain `010C`.

  python elm_compare.py [--tcp label=host[:port]]... [--serial label=COMx[:baud]]...
                        [--n 200] [--fixture tools/testbench/obd/fixtures/elm_latency_2026-09-08.json]
                        [--no-fixture] [--md FILE]

Examples (PC, the OBDLink plugged into the PC and into the simulator's
OBD socket; the WiCAN reached through the tunnel is NOT representative
for latency - run the TCP leg from the Pi or give the WiCAN's hotspot
address from a PC on the same WiFi):
  python elm_compare.py --serial obdlink=COM9:115200
  python elm_compare.py --tcp wican=10.42.1.194 --serial obdlink=COM9

Reference-adapter notes: OBDLink SX/EX default 115200 baud; FTDI ports
add their latency timer (16 ms default on Windows) to every response -
set it to 1 ms in Device Manager (port settings > advanced) first. The
adapter's own ELM timeout/adaptive timing rules apply the same way as on
the MIC3624, so compare the hinted rows for the transport floor and the
hint-less rows for the chip's multi-ECU wait.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from elm_latency_probe import INIT, Link, run_loop  # noqa: E402

COLS = ["label", "cmd", "rate_hz", "min", "p50", "p95", "max", "bad"]
HEAD = ["adapter / condition", "request", "req/s", "min ms", "p50 ms",
        "p95 ms", "max ms", "bad/200"]


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def table(rows):
    out = ["| " + " | ".join(HEAD) + " |",
           "|" + "|".join("---" for _ in HEAD) + "|"]
    for r in rows:
        bad = f"{r.get('bad', 0)}/{r.get('n', 200)}"
        cells = [r["label"], f"`{r['cmd']}`", fmt(r.get("rate_hz")),
                 fmt(r.get("min")), fmt(r.get("p50")), fmt(r.get("p95")),
                 fmt(r.get("max")), bad]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def measure(label, link, n):
    rows = []
    for cmd in ("010C 1", "010C"):
        print(f"-- {label} ({link.name}): {cmd} x{n}")
        s, _ = run_loop(link, cmd, n, quiet=False, init=INIT)
        s["label"] = label
        print("   " + json.dumps(s))
        rows.append(s)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tcp", action="append", default=[],
                    help="label=host[:port]")
    ap.add_argument("--serial", action="append", default=[],
                    help="label=COMx[:baud]")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--fixture", default=os.path.join(
        os.path.dirname(__file__), "fixtures", "elm_latency_2026-09-08.json"))
    ap.add_argument("--no-fixture", action="store_true")
    ap.add_argument("--md", default="", help="also write the table here")
    a = ap.parse_args()

    rows = []
    if not a.no_fixture and os.path.exists(a.fixture):
        with open(a.fixture) as f:
            rows += json.load(f)["rows"]

    for spec in a.tcp:
        label, _, target = spec.partition("=")
        host, _, port = target.partition(":")
        link = Link(host=host, port=int(port or 35000))
        rows += measure(label or host, link, a.n)
        link.close()

    for spec in a.serial:
        label, _, target = spec.partition("=")
        port, _, baud = target.partition(":")
        link = Link(serial_port=port, baud=int(baud or 115200))
        rows += measure(label or port, link, a.n)
        link.close()

    md = table(rows)
    print()
    print(md)
    if a.md:
        with open(a.md, "w") as f:
            f.write(md + "\n")


if __name__ == "__main__":
    main()
