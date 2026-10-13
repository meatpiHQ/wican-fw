#!/usr/bin/env python3
"""The WiCAN data logger's clock journal: show it, or re-base a log file
with it (issue #984, 2026-10-13).

The device writes `/sd/logs/clock.jsonl`, one JSON object per line:

  {"ev":"boot","boot":N,"ts":..,"up_ms":..,"valid":false,"source":"none"}
  {"ev":"valid","boot":N,"ts_before":..,"ts_after":..,"delta_ms":..,
   "source":"sntp","files":["dl_0000000042.jsonl","can_0000000042.mf4"]}
  {"ev":"step",...}          the same shape, every later set of the clock
  {"ev":"rotate","boot":N,"ts":..,"stream":"params","from":"..","to":"..",
   "why":"clock"}

A row written BEFORE a step was stamped on the old clock. This tool adds
every such step's delta_ms to those rows (the device's own record, so no
guessing per boot) and writes `<name>.rebased.<ext>`. Formats: jsonl, csv
(params), candump (.log) and the binary .wdl (needs tools/wdl_dump.py next
to this file). A row is "before" a step when it precedes the step's
in-band marker in the file (jsonl, csv, wdl have one) or, without a marker,
when its stamp is at or before the step's `ts_before`.

  python tools/log_clock.py --show clock.jsonl
  python tools/log_clock.py --journal clock.jsonl --rebase dl_0000000042.jsonl [--out FILE]

Rows the device already corrected (queued before a step, written after
it) are after the marker and are not touched. mf4, blf and asc carry
their time relative to the file's start and take it from the device's
monotonic clock: nothing to re-base there, their header start is the
device's corrected one (asc keeps the date it was opened with).
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CLOCK_SOURCES = {"none": 0, "rtc": 1, "sntp": 2, "manual": 3}


def read_journal(path):
    events = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                events.append(json.loads(ln))
            except ValueError:
                continue
    return events


def iso(ms):
    import datetime
    if ms is None:
        return "-"
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S") + "Z"


def show(events):
    print(f"{'when (UTC)':20} {'event':7} {'boot':>4} {'delta':>12} {'source':6} files")
    for e in events:
        ev = e.get("ev")
        if ev == "boot":
            print(f"{iso(e.get('ts')):20} {ev:7} {e.get('boot', ''):>4} {'':>12} "
                  f"{e.get('source', ''):6} valid={e.get('valid')} up_ms={e.get('up_ms')}")
        elif ev in ("valid", "step"):
            d = e.get("delta_ms", 0)
            print(f"{iso(e.get('ts_after')):20} {ev:7} {e.get('boot', ''):>4} "
                  f"{d:+12d} {e.get('source', ''):6} {' '.join(e.get('files', []))}")
        elif ev == "rotate":
            print(f"{iso(e.get('ts')):20} {ev:7} {e.get('boot', ''):>4} {'':>12} "
                  f"{e.get('stream', ''):6} {e.get('from')} -> {e.get('to')}")
        else:
            print(f"{'':20} {str(ev):7} {json.dumps(e)}")


def steps_for(events, fname):
    """The steps that list `fname` among the files open at the time, in
    journal order, each with the wall-clock origin of its boot (the boot
    line's ts minus its up_ms): a row older than that was written by an
    earlier boot into a resumed file and is not that step's business."""
    out = []
    origin = None
    for e in events:
        ev = e.get("ev")
        if ev == "boot":
            try:
                origin = int(e.get("ts", 0)) - int(e.get("up_ms", 0))
            except (TypeError, ValueError):
                origin = None
        elif ev in ("valid", "step"):
            if fname in e.get("files", []):
                s = dict(e)
                s["_origin"] = origin
                out.append(s)
            # the boot's origin is expressed in the clock as it was at the
            # boot line: every later step of the boot moves it along
            if origin is not None:
                origin += int(e.get("delta_ms", 0) or 0)
    return out


# ---- rows per format ------------------------------------------------------------
# a row = (kind, ts_ms, payload); kind "marker" carries (ts_after, delta)

def rows_jsonl(path):
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.rstrip("\n")
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except ValueError:
                out.append(("raw", None, ln))
                continue
            if o.get("event") == "clock_step":
                out.append(("marker", o.get("ts"), o.get("delta_ms")))
            else:
                out.append(("row", o.get("ts"), o))
    return out


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for kind, ts, payload in rows:
            if kind == "marker":
                continue
            if kind == "raw":
                f.write(payload + "\n")
                continue
            payload["ts"] = ts
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")


def rows_csv(path):
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, ln in enumerate(f):
            ln = ln.rstrip("\n")
            c = ln.split(",")
            if i == 0 and c and c[0] == "ts_ms":
                out.append(("raw", None, ln))
                continue
            if len(c) >= 3 and c[1] == "logger.clock_step":
                out.append(("marker", int(c[0]), int(c[2])))
                continue
            try:
                out.append(("row", int(c[0]), c))
            except ValueError:
                out.append(("raw", None, ln))
    return out


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for kind, ts, payload in rows:
            if kind == "marker":
                continue
            if kind == "raw":
                f.write(payload + "\n")
                continue
            payload[0] = str(ts)
            f.write(",".join(payload) + "\n")


def rows_candump(path):
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.rstrip("\n")
            if ln.startswith("(") and ")" in ln:
                stamp, rest = ln[1:].split(")", 1)
                try:
                    sec, usec = stamp.split(".")
                    out.append(("row", int(sec) * 1000 + int(usec) // 1000, rest))
                    continue
                except ValueError:
                    pass
            out.append(("raw", None, ln))
    return out


def write_candump(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for kind, ts, payload in rows:
            if kind == "raw":
                f.write(payload + "\n")
            else:
                f.write(f"({ts // 1000}.{(ts % 1000) * 1000:06d}){payload}\n")


def rows_wdl(path):
    import wdl_dump
    out = []
    for rec in wdl_dump.decode(path):
        if rec[0] == "clock":
            out.append(("marker", rec[1], rec[2]))
        else:
            out.append(("row", rec[1], rec))
    return out


def write_wdl(path, rows):
    names = {}
    with open(path, "wb") as f:
        f.write(b"WDL1")
        for kind, ts, payload in rows:
            if kind != "row":
                continue
            if payload[0] == "param":
                _, _, name, value = payload
                if name not in names:
                    names[name] = len(names) + 1
                    nb = name.encode("utf-8")
                    f.write(b"\x01" + struct.pack("<HB", names[name], len(nb)) + nb)
                f.write(b"\x02" + struct.pack("<qHd", ts, names[name], value))
            else:
                _, _, fid, ext, rtr, data = payload
                idw = (fid & 0x1FFFFFFF) | (0x80000000 if ext else 0) | (0x40000000 if rtr else 0)
                f.write(b"\x03" + struct.pack("<qIB", ts, idw, len(data)) + data)


FORMATS = {
    ".jsonl": (rows_jsonl, write_jsonl),
    ".csv": (rows_csv, write_csv),
    ".log": (rows_candump, write_candump),
    ".wdl": (rows_wdl, write_wdl),
}


def rebase(events, path, out_path=None):
    fname = os.path.basename(path)
    ext = os.path.splitext(fname)[1]
    if ext not in FORMATS:
        raise SystemExit(f"{fname}: no re-base for {ext} (jsonl, csv, log, wdl)")
    steps = steps_for(events, fname)
    if not steps:
        raise SystemExit(f"{fname}: the journal lists no clock step while this file was open")
    reader, writer = FORMATS[ext]
    rows = reader(path)
    # the markers in the file, by position
    markers = [(i, ts, delta) for i, (kind, ts, delta) in enumerate(rows) if kind == "marker"]
    shifted = 0
    for s in steps:
        delta = int(s.get("delta_ms", 0))
        after = s.get("ts_after")
        before = s.get("ts_before")
        origin = s.get("_origin")
        pos = None
        for i, ts, d in markers:
            if ts == after and d == delta:
                pos = i
                break
        for i, (kind, ts, payload) in enumerate(rows):
            if kind != "row" or ts is None:
                continue
            is_before = (i < pos) if pos is not None else (ts <= before)
            # a row from an earlier boot (a resumed file) keeps its stamp
            if origin is not None and ts < origin - 5000:
                is_before = False
            if is_before:
                rows[i] = (kind, ts + delta, payload)
                shifted += 1
    if out_path is None:
        base, e = os.path.splitext(path)
        out_path = base + ".rebased" + e
    writer(out_path, rows)
    n_rows = sum(1 for k, _, _ in rows if k == "row")
    print(f"{fname}: {len(steps)} step(s) applied, {shifted} of {n_rows} row-shifts, "
          f"{len(markers)} marker(s) dropped -> {out_path}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--show", metavar="CLOCK_JSONL", help="print the journal as a table")
    ap.add_argument("--journal", metavar="CLOCK_JSONL", help="the device's clock journal")
    ap.add_argument("--rebase", metavar="FILE", help="a log file to re-base with --journal")
    ap.add_argument("--out", help="where to write the re-based file")
    a = ap.parse_args()
    if a.show:
        show(read_journal(a.show))
        return
    if a.journal and a.rebase:
        rebase(read_journal(a.journal), a.rebase, a.out)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
