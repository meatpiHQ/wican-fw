#!/usr/bin/env python3
"""AutoPID vehicle profile end to end  ->  AUTOPID PROFILE E2E PASS / FAIL

One published vehicle profile, imported the way the web UI imports it,
polled against an ECU that is played FROM THE PROFILE ITSELF on the PCAN:
every PID the profile sends gets a reply whose bytes the bench chose, every
passive filter's frame is broadcast with bytes the bench chose, and the
values the device publishes are compared with what the profile's
expressions mean. The published expressions index the legacy firmware's
frame of reference: every byte after the CAN header, PCI bytes included
(`parse_elm327_response()` in the legacy main/autopid.c; a single frame is
`0L 41 05 xx`, a multi-frame reply `10 LL 61 01 .. 21 .. 22 ..`). That is
what the bench evaluates them against ("what the profile means"); the
device must publish the same numbers after the importer's conversion.

Legs:
  L0 preflight: the device answers and no app or diagnostic tool holds the
     chip; the tables and the settings are saved; the profile is loaded
     (local file or the published URL) and the car picked.
  L1 the ECU: reply bytes per request and frame bytes per filter are
     generated (seeded, inside every published min/max), the expected
     values computed, the actor `actors/pcan_profile_ecu.py` started on
     the PCAN.
  L2 the import: the table the UI would PUT (`autopid_profile_bench.convert`,
     the importer mirror: index shift, renames, filters) is applied; the
     settings get the profile's init as `specific_init`, its name as
     `vehicle`, and the poller enabled (submit-reboot when they changed).
  L3 the poll: /api/autopid watched for --watch seconds after the first
     profile value. Claims: every request of the profile reached the ECU
     and was answered (no flow-control timeout), every filter parameter
     equals its expected value, every PID parameter equals its expected
     value (a parameter past the 128-byte payload cap is reported, not
     failed), the rows keep being re-read, the standard rows found on the
     device keep reading the simulator beside the profile.
  L4 the one-shot: every PID through POST /api/autopid/test with the
     profile's init chain: the chip's raw reply lines go to the log (the
     byte view the device really gets), the decoded values are compared.
  L5 restore: the saved tables and settings (a submit-reboot when the
     settings had to change), the actor stopped, `0100` answered by the
     simulator again.

usage: autopid_profile_e2e_bench.py [--dut localhost:18081] [--pcan PCAN_USBBUS2]
          [--profiles FILE|URL] [--car "Leaf ZE1 (2018-)"] [--watch 60]
          [--seed 20261021] [--out DIR]
  Runs on the PC (python-can + PCAN); the DUT through an ssh tunnel
  (`ssh -N -L 18081:10.42.1.194:80 rpi001`) or by address. --profiles
  defaults to the published file on GitHub main; a local copy (a checkout's
  vehicle_profiles.json) tests a profile before it is pushed.
Needs: the main firmware on the DUT on the bench AP, PCAN_USBBUS2 on the
DUT bus, the ECU simulator on the bus (it ACKs the actor's frames and
answers the standard rows), the PSU above the sleep voltage. 11-bit CAN
profiles (ATSH/ATCRA/ATFCSH with 3 hex digits); ~4 min, two planned
restarts.
"""
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import autopid_profile_bench as apb  # noqa: E402  (the importer mirror)

ACTOR = os.path.join(HERE, "..", "actors", "pcan_profile_ecu.py")
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
PAYLOAD_CAP = 128          # AP_PAYLOAD_MAX in autopid_private.h
DEFAULT_CAR = "Leaf ZE1 (2018-)"


# ---------------------------------------------------------------- expressions
def expr_indexes(expr):
    """Every byte index an expression references."""
    out = []
    for m in re.finditer(r"\[[BS](\d+):[BS](\d+)\]", expr):
        out.extend(range(int(m.group(1)), int(m.group(2)) + 1))
    for m in re.finditer(r"(?<![\[:])[BS](\d+)", expr):
        out.append(int(m.group(1)))
    return out


def eval_expr(expr, data):
    """The firmware's expression semantics on a byte buffer: B<n> unsigned
    byte, S<n> signed byte, [Bx:By] unsigned big-endian span, [Sx:Sy] a
    signed span whose container follows legacy (1 B int8, 2 B int16, 3..4 B
    int32, 5..8 B int64). Returns None past the buffer or on an error."""
    try:
        def span(m):
            kind, a, b = m.group(1), int(m.group(2)), int(m.group(3))
            if b < a or b >= len(data):
                raise IndexError
            v = 0
            for j in range(a, b + 1):
                v = (v << 8) | data[j]
            if kind == "S":
                n = b - a + 1
                bits = 8 if n == 1 else 16 if n == 2 else 32 if n <= 4 else 64
                if v >= 1 << (bits - 1):
                    v -= 1 << bits
            return "(%d)" % v

        s = re.sub(r"\[([BS])(\d+):[BS](\d+)\]", span, expr)

        def single(m):
            kind, i = m.group(1), int(m.group(2))
            if i >= len(data):
                raise IndexError
            v = data[i]
            if kind == "S" and v >= 128:
                v -= 256
            return "(%d)" % v

        s = re.sub(r"([BS])(\d+)", single, s)
        if not re.fullmatch(r"[\d\.\s\+\-\*/\(\)]+", s):
            return None
        return float(eval(s, {"__builtins__": {}}, {}))  # noqa: S307 (arithmetic only)
    except (IndexError, ZeroDivisionError, SyntaxError, ValueError, TypeError):
        return None


def bound(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def in_bounds(value, prm):
    lo, hi = bound(prm.get("min")), bound(prm.get("max"))
    if value is None:
        return False
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def near(a, b):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))


# ---------------------------------------------------------------- the ECU from the profile
def chain_ids(chain, ids):
    """Update {req, resp, fc} from the ATSH / ATCRA / ATFCSH of an init
    chain (11-bit, 3 hex digits; a 29-bit ATCRA of 8 digits is taken)."""
    for cmd in str(chain or "").split(";"):
        c = cmd.strip().upper().replace(" ", "")
        m = re.fullmatch(r"ATSH([0-9A-F]{3})", c)
        if m:
            ids["req"] = int(m.group(1), 16)
        m = re.fullmatch(r"ATCRA([0-9A-F]{3}|[0-9A-F]{8})", c)
        if m:
            ids["resp"] = int(m.group(1), 16)
        m = re.fullmatch(r"ATFCSH([0-9A-F]{3}|[0-9A-F]{8})", c)
        if m:
            ids["fc"] = int(m.group(1), 16)
    return ids


def split_cmd(cmd):
    """'21018' -> (b'\\x21\\x01', 8): an odd trailing digit is the ELM
    response-count hint, never sent on the wire."""
    c = str(cmd).replace(" ", "").upper()
    hint = None
    if len(c) % 2 == 1:
        hint = int(c[-1], 16)
        c = c[:-1]
    return bytes.fromhex(c), hint


def raw_view(payload):
    """The legacy frame of reference of a reply: the CAN frames as the
    chip prints them with headers on, PCI bytes included, 8 bytes each."""
    n = len(payload)
    if n <= 7:
        return (bytes([n]) + payload).ljust(8, b"\x00")
    out = bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF]) + payload[:6]
    rest = payload[6:]
    sn = 1
    while rest:
        out += (bytes([0x20 | (sn & 0x0F)]) + rest[:7]).ljust(8, b"\x00")
        rest = rest[7:]
        sn = (sn + 1) & 0x0F
    return out


def wrapped_rows_payload(payload):
    """What a reader that orders the chip's ISO-TP rows by their PRINTED
    index gets: the ELM prints row indexes 0..F and wraps, so for a reply
    over 16 rows the rows 16.. interleave with 0.. (found 2026-10-21 on
    the Leaf's 28-row cell-voltage reply)."""
    if len(payload) <= 7:
        return payload
    rows = [payload[:6]]
    rest = payload[6:]
    while rest:
        rows.append(rest[:7])
        rest = rest[7:]
    out = b""
    for idx in range(len(rows)):
        for i, r in enumerate(rows):
            if i % 16 == idx:
                out += r
    return out


def raw_to_payload_index(i, multi):
    """Where a legacy raw index lands in the PCI-free payload (None for a
    PCI byte)."""
    if not multi:
        return None if i == 0 else i - 1
    if i < 2:
        return None
    if i < 8:
        return i - 2
    if i % 8 == 0:
        return None
    return i - 2 - i // 8


def build_reply(pid, seed):
    """Reply payload (PCI-free) for one published PID: the echo, then data
    bytes drawn from a seeded generator and nudged until every parameter
    with a published range decodes inside it. Returns a dict."""
    req, hint = split_cmd(pid["pid"])
    echo = bytes([req[0] + 0x40]) + req[1:]
    params = pid.get("parameters", [])
    idx = sorted(set(i for p in params for i in expr_indexes(p["expression"])))
    max_idx = max(idx) if idx else 0
    frames = max(max_idx // 8 + 1, hint or 1)
    multi = frames > 1
    if multi:
        length = 6 + 7 * (frames - 1)
    else:
        length = max(len(echo), min(7, max_idx))
    rng = random.Random("%s:%s" % (seed, pid["pid"]))
    data = bytearray(rng.randrange(256) for _ in range(length - len(echo)))

    def payload():
        return echo + bytes(data)

    # nudge the bytes a ranged parameter reads until it decodes in range
    for _ in range(40):
        clean = True
        for p in params:
            raw = raw_view(payload())
            if in_bounds(eval_expr(p["expression"], raw), p) or \
                    (bound(p.get("min")) is None and bound(p.get("max")) is None):
                continue
            clean = False
            cells = [raw_to_payload_index(i, multi) for i in expr_indexes(p["expression"])]
            cells = [c - len(echo) for c in cells
                     if c is not None and len(echo) <= c < length]
            for _try in range(400):
                for c in cells:
                    data[c] = rng.randrange(256)
                if in_bounds(eval_expr(p["expression"], raw_view(payload())), p):
                    break
        if clean:
            break
    raw = raw_view(payload())
    rows = []
    for p in params:
        shifted = apb.shift_expr(p["expression"])
        rows.append({
            "name": p["name"], "expression": p["expression"], "v6_expression": shifted,
            "expected": eval_expr(p["expression"], raw),
            "shift_on_raw": eval_expr(shifted, raw),
            "shift_on_pcifree": eval_expr(shifted, payload()),
            "shift_on_wrapped_rows": eval_expr(shifted, wrapped_rows_payload(payload())),
            "max_raw_index": max(expr_indexes(p["expression"]) or [0]),
            "in_range": in_bounds(eval_expr(p["expression"], raw), p),
        })
    return {"pid": pid["pid"], "key": req.hex().upper(), "hint": hint,
            "frames": frames, "multi": multi, "payload": payload().hex().upper(),
            "raw_view": raw.hex().upper(), "params": rows}


def build_filter(flt, seed):
    rng = random.Random("%s:filter:%s" % (seed, flt.get("frame_id")))
    data = bytearray(rng.randrange(256) for _ in range(8))
    params = flt.get("parameters", [])
    for _ in range(40):
        clean = True
        for p in params:
            if in_bounds(eval_expr(p["expression"], bytes(data)), p) or \
                    (bound(p.get("min")) is None and bound(p.get("max")) is None):
                continue
            clean = False
            cells = [i for i in expr_indexes(p["expression"]) if i < 8]
            for _try in range(400):
                for c in cells:
                    data[c] = rng.randrange(256)
                if in_bounds(eval_expr(p["expression"], bytes(data)), p):
                    break
        if clean:
            break
    rows = [{"name": p["name"], "expression": p["expression"],
             "expected": eval_expr(p["expression"], bytes(data)),
             "in_range": in_bounds(eval_expr(p["expression"], bytes(data)), p)}
            for p in params]
    return {"frame_id": apb.frame_id_number(flt.get("frame_id")),
            "monitor_ms": flt.get("monitor_ms") or 1000, "data": bytes(data).hex().upper(),
            "params": rows}


def build_ecu(car, seed):
    """The actor script + the expectation table for one car."""
    ids = chain_ids(car.get("init", ""), {"req": None, "resp": None, "fc": None})
    ecus = {}
    replies = []
    skipped = []
    for pid in car.get("pids", []):
        ids = chain_ids(pid.get("pid_init", ""), dict(ids))
        if ids["req"] is None or ids["resp"] is None:
            skipped.append("%s: no ATSH/ATCRA in its init chain (11-bit only)" % pid["pid"])
            continue
        rep = build_reply(pid, seed)
        rep["req_id"], rep["resp_id"] = ids["req"], ids["resp"]
        replies.append(rep)
        e = ecus.setdefault(ids["req"], {"req_id": "0x%03X" % ids["req"],
                                          "resp_id": "0x%03X" % ids["resp"],
                                          "fc_from": "0x%03X" % (ids["fc"] or ids["req"]),
                                          "responses": {}})
        e["responses"][rep["key"]] = rep["payload"]
    filters = [build_filter(f, seed) for f in car.get("filters", []) or []]
    script = {"ecus": list(ecus.values()),
              "broadcasts": [{"id": "0x%03X" % f["frame_id"], "data": f["data"],
                              "gap_ms": 50} for f in filters if f["frame_id"]],
              "cf_gap_ms": 1}
    return script, replies, filters, skipped


# ---------------------------------------------------------------- HTTP
def http(base, method, path, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e)


def get_json(base, path):
    st, body = http(base, "GET", path)
    try:
        return st, json.loads(body)
    except ValueError:
        return st, None


def submit_and_wait(base, window=150):
    st, body = http(base, "POST", "/api/settings/submit", {})
    print("  submit -> %s %s" % (st, body.strip()[:80]))
    reboot = st == 200 and '"reboot":true' in body.replace(" ", "")
    if not reboot:
        return st == 200
    t0 = time.time()
    seen_down = False
    while time.time() - t0 < window:
        time.sleep(2)
        st, _ = http(base, "GET", "/api/info", timeout=5)
        if st != 200:
            seen_down = True
        elif seen_down:
            print("  back after %.0f s" % (time.time() - t0))
            time.sleep(10)      # the chip provisions ~15 s after a boot
            return True
    print("  the DUT did not come back within %d s" % window)
    return False


def load_cars(src):
    if src.startswith("http://") or src.startswith("https://"):
        with urllib.request.urlopen(src, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))["cars"]
    with open(src, encoding="utf-8") as f:
        return json.load(f)["cars"]


class Checks:
    def __init__(self):
        self.rows = []
        self.warns = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print("  [%s] %s%s" % ("ok" if ok else "FAIL", name,
                               (": " + detail) if detail else ""), flush=True)
        return bool(ok)

    def warn(self, text):
        self.warns.append(text)
        print("  [WARN] " + text, flush=True)

    def failed(self):
        return [n for n, ok, _ in self.rows if not ok]


def fmt(v):
    if v is None:
        return "null"
    return ("%.6g" % v) if isinstance(v, float) else str(v)


# ---------------------------------------------------------------- main
def main():
    args = sys.argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    dut = opt("--dut", "localhost:18081")
    base = "http://" + dut.rstrip("/")
    pcan = opt("--pcan", "PCAN_USBBUS2")
    profiles = opt("--profiles", apb.PROFILES_URL)
    car_sel = opt("--car", DEFAULT_CAR)
    watch_s = float(opt("--watch", 60))
    seed = opt("--seed", "20261021")
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = opt("--out", os.path.join(REPO, "test-reports", "logs",
                                    "autopid_profile_e2e_" + ts))
    os.makedirs(out, exist_ok=True)
    print("AUTOPID PROFILE E2E bench: dut=%s pcan=%s car=%r watch=%.0f s seed=%s out=%s"
          % (dut, pcan, car_sel, watch_s, seed, out), flush=True)
    c = Checks()
    actor = None
    saved_cfg = None
    saved_settings = None
    settings_changed = False
    summary_path = os.path.join(out, "actor_summary.json")

    try:
        # ---- L0 preflight ------------------------------------------------
        print("L0 preflight")
        st, info = get_json(base, "/api/info")
        if not c.add("dut answers", st == 200 and info,
                     "%s %s %s" % (st, (info or {}).get("fw_version"),
                                   (info or {}).get("fw_build"))):
            raise SystemExit(1)
        st, ap = get_json(base, "/api/autopid")
        stats = (ap or {}).get("stats", {})
        c.add("no app or diagnostic hold on the chip",
              not stats.get("paused_client") and not stats.get("paused_diag"),
              json.dumps({k: stats.get(k) for k in
                          ("paused_client", "paused_diag", "paused_voltage",
                           "paused_bus")}))
        st, saved_cfg = get_json(base, "/api/autopid/config")
        c.add("tables saved", st == 200 and saved_cfg is not None,
              "%d pids, %d filters" % (len((saved_cfg or {}).get("pids", [])),
                                       len((saved_cfg or {}).get("filters", []))))
        st, saved_settings = get_json(base, "/api/settings/autopid")
        c.add("settings saved", st == 200 and saved_settings is not None)
        with open(os.path.join(out, "saved_config.json"), "w") as f:
            json.dump(saved_cfg, f)
        with open(os.path.join(out, "saved_settings.json"), "w") as f:
            json.dump(saved_settings, f)
        cars = load_cars(profiles)
        picked = [x for x in cars if car_sel.lower() in x["car_model"].lower()]
        if not c.add("one profile matches --car", len(picked) == 1,
                     "%d of %d match %r in %s" % (len(picked), len(cars), car_sel,
                                                  profiles)):
            raise SystemExit(1)
        car = picked[0]
        print("  profile: %s: %d PIDs, %d filters, init %r"
              % (car["car_model"], len(car.get("pids", [])),
                 len(car.get("filters", []) or []), car.get("init", "")))
        if c.failed():
            raise SystemExit(1)

        # ---- L1 the ECU --------------------------------------------------
        print("L1 the ECU from the profile")
        script, replies, filters, skipped = build_ecu(car, seed)
        with open(os.path.join(out, "ecu_script.json"), "w") as f:
            json.dump(script, f, indent=1)
        with open(os.path.join(out, "expected.json"), "w") as f:
            json.dump({"replies": replies, "filters": filters}, f, indent=1)
        for s in skipped:
            c.warn("skipped " + s)
        c.add("every PID of the profile has an ECU to answer it",
              bool(replies) and not skipped,
              "%d replies on %s" % (len(replies), ", ".join(
                  "0x%03X -> 0x%03X" % (int(e["req_id"], 0), int(e["resp_id"], 0))
                  for e in script["ecus"])))
        for rep in replies:
            print("    %-8s %s %2d frame%s %3d bytes  %s"
                  % (rep["pid"], "multi " if rep["multi"] else "single",
                     rep["frames"], "s" if rep["frames"] != 1 else " ",
                     len(rep["payload"]) // 2,
                     ", ".join("%s=%s" % (p["name"], fmt(p["expected"]))
                               for p in rep["params"][:4])
                     + (" ..." if len(rep["params"]) > 4 else "")))
        for flt in filters:
            print("    filter 0x%03X %s  %s" % (
                flt["frame_id"], flt["data"],
                ", ".join("%s=%s" % (p["name"], fmt(p["expected"])) for p in flt["params"])))
        out_of_range = [p["name"] for rep in replies for p in rep["params"]
                        if not p["in_range"] and
                        (bound(p.get("min")) is not None or bound(p.get("max")) is not None)]
        c.add("the generated replies decode inside every published range",
              not out_of_range, ", ".join(out_of_range[:8]))
        beyond = [p["name"] for rep in replies for p in rep["params"]
                  if p["max_raw_index"] >= PAYLOAD_CAP]
        if beyond:
            c.warn("%d parameter(s) index past the %d-byte payload cap "
                   "(AP_PAYLOAD_MAX) and cannot decode on v6: %s%s"
                   % (len(beyond), PAYLOAD_CAP, ", ".join(beyond[:6]),
                      " ..." if len(beyond) > 6 else ""))
        actor_log = open(os.path.join(out, "actor.log"), "w")
        actor = subprocess.Popen(
            [sys.executable, "-u", ACTOR, os.path.join(out, "ecu_script.json"),
             str(watch_s + 600), "--pcan", pcan, "--summary", summary_path],
            stdout=actor_log, stderr=subprocess.STDOUT)
        time.sleep(2.5)
        c.add("the profile's ECU is on the PCAN", actor.poll() is None,
              "actor pid %d" % actor.pid)
        if c.failed():
            raise SystemExit(1)

        # ---- L2 the import -----------------------------------------------
        print("L2 the import: the table the UI would PUT, the settings it would stage")
        cfg, renamed = apb.convert(saved_cfg, car)
        if renamed:
            print("  importer renames: " + ", ".join(renamed[:5]))
        with open(os.path.join(out, "put_config.json"), "w") as f:
            json.dump(cfg, f, indent=1)
        st, body = http(base, "PUT", "/api/autopid/config", cfg)
        c.add("table accepted", st == 200, "%s %s" % (st, body.strip()[:100]))
        new_settings = dict(saved_settings)
        for k in ("degraded", "pending_reboot"):
            new_settings.pop(k, None)
        want = {"enabled": True, "specific_enabled": True,
                "specific_init": car.get("init", "") or "",
                "vehicle": str(car["car_model"])[:63]}
        if any(new_settings.get(k) != v for k, v in want.items()):
            new_settings.update(want)
            st, body = http(base, "PUT", "/api/settings/autopid", new_settings)
            c.add("settings staged (enabled, specific_init, vehicle)", st == 200,
                  "%s %s" % (st, body.strip()[:100]))
            settings_changed = True
            c.add("the DUT rebooted and is back", submit_and_wait(base))
        if c.failed():
            raise SystemExit(1)
        st, ap = get_json(base, "/api/autopid")
        groups = {g["name"]: g for g in (ap or {}).get("groups", [])}
        print("  groups after the import: %s" % json.dumps(groups))
        c.add("poller running", (ap or {}).get("stats", {}).get("running") is True,
              json.dumps({k: (ap or {}).get("stats", {}).get(k) for k in
                          ("running", "paused_voltage", "paused_bus", "paused_client")}))

        # ---- L3 the poll -------------------------------------------------
        print("L3 the poll: %.0f s after the first profile value" % watch_s)
        exp_pid = {p["name"]: p for rep in replies for p in rep["params"]}
        exp_flt = {p["name"]: p for flt in filters for p in flt["params"]}
        names = list(exp_pid) + list(exp_flt)
        samples = open(os.path.join(out, "samples.jsonl"), "w")
        last_ts = {}
        advances = {n: 0 for n in names}
        values = {}
        std_values = {}
        first_at = None
        stats0 = None
        statsN = None
        deadline = time.time() + 150
        t_end = None
        while True:
            now = time.time()
            st, ap = get_json(base, "/api/autopid")
            if st == 200 and ap:
                stats = ap.get("stats", {})
                samples.write(json.dumps({"t": now, "params": ap.get("params"),
                                          "stats": stats}) + "\n")
                samples.flush()
                for p in ap.get("params", []):
                    n = p.get("name")
                    if n not in advances:
                        if p.get("value") is not None:
                            std_values[n] = p.get("value")
                        continue
                    t_us = p.get("ts_us")
                    if t_us is not None and t_us != last_ts.get(n):
                        if n in last_ts:
                            advances[n] += 1
                        last_ts[n] = t_us
                        values[n] = p.get("value")
                if first_at is None and any(values.get(n) is not None for n in names):
                    first_at = now
                    stats0 = dict(stats)
                    for n in advances:
                        advances[n] = 0
                    t_end = now + watch_s
                    print("  first profile value at +%.0f s; watching %.0f s"
                          % (now - deadline + 150, watch_s), flush=True)
                if t_end is not None and now >= t_end:
                    statsN = dict(stats)
                    break
            if first_at is None and now > deadline:
                break
            time.sleep(1.0)
        samples.close()
        c.add("a profile value was published", first_at is not None,
              "polls_ok %s failed %s" % (
                  (ap or {}).get("stats", {}).get("polls_ok"),
                  (ap or {}).get("stats", {}).get("polls_failed")))
        # the actor's view
        time.sleep(1.5)
        summ = {}
        try:
            with open(summary_path) as f:
                summ = json.load(f)
        except (OSError, ValueError):
            pass
        unanswered = [rep["pid"] for rep in replies
                      if summ.get("answered", {}).get(rep["key"], 0) < 1]
        c.add("every request of the profile reached the ECU and was answered",
              not unanswered,
              "requests %s" % json.dumps(summ.get("requests")) +
              ("; unanswered: " + ", ".join(unanswered) if unanswered else ""))
        c.add("no flow-control timeout on the multi-frame replies",
              summ.get("fc_timeout", 1) == 0,
              "fc_ok %s fc_timeout %s" % (summ.get("fc_ok"), summ.get("fc_timeout")))
        c.add("the filter frames were broadcast",
              not filters or all(v > 0 for v in summ.get("broadcast_sent", {}).values()),
              json.dumps(summ.get("broadcast_sent")))
        # filters
        for n, p in exp_flt.items():
            c.add("filter %s == %s" % (n, fmt(p["expected"])),
                  near(values.get(n), p["expected"]), "got %s" % fmt(values.get(n)))
        # PIDs
        for rep in replies:
            good, bad, capped = [], [], []
            for p in rep["params"]:
                got = values.get(p["name"])
                if near(got, p["expected"]):
                    good.append(p["name"])
                elif got is None and p["max_raw_index"] >= PAYLOAD_CAP:
                    capped.append(p["name"])
                else:
                    bad.append((p, got))
            detail = "%d/%d parameters" % (len(good), len(rep["params"]))
            if capped:
                detail += ", %d past the payload cap (null)" % len(capped)
            if bad:
                n_pf = sum(1 for p, got in bad if near(got, p["shift_on_pcifree"]))
                n_raw = sum(1 for p, got in bad if near(got, p["shift_on_raw"]))
                n_wrap = sum(1 for p, got in bad if near(got, p["shift_on_wrapped_rows"]))
                n_null = sum(1 for p, got in bad if got is None)
                detail += "; wrong: " + "; ".join(
                    "%s got %s want %s" % (p["name"], fmt(got), fmt(p["expected"]))
                    for p, got in bad[:5]) + (" ..." if len(bad) > 5 else "")
                detail += (" | of %d wrong: %d null, %d equal the shifted expression on the"
                           " PCI-free payload, %d equal it on the raw frames, %d equal it on"
                           " the rows re-ordered by their printed index (0..F wraps)" % (
                               len(bad), n_null, n_pf, n_raw, n_wrap))
            c.add("pid %s decodes as the profile means (%s)" % (
                rep["pid"], "multi-frame, %d frames" % rep["frames"] if rep["multi"] else "single frame"),
                not bad, detail)
            if capped:
                c.warn("pid %s: %s past the payload cap: %s" % (
                    rep["pid"], len(capped), ", ".join(capped[:5]) + (" ..." if len(capped) > 5 else "")))
        # re-reads and the rows around
        if first_at is not None:
            moving = {rep["pid"]: min(advances[p["name"]] for p in rep["params"]
                                      if values.get(p["name"]) is not None) if any(
                values.get(p["name"]) is not None for p in rep["params"]) else 0
                for rep in replies}
            c.add("the profile rows keep being re-read during the watch",
                  all(v >= 2 for v in moving.values()), json.dumps(moving))
            d_fail = statsN.get("polls_failed", 0) - stats0.get("polls_failed", 0)
            d_ok = statsN.get("polls_ok", 0) - stats0.get("polls_ok", 0)
            print("  polls during the watch: ok +%d failed +%d" % (d_ok, d_fail))
            if not beyond:
                c.add("no poll fails during the watch", d_fail == 0,
                      "polls_failed +%d" % d_fail)
            std_rows = [p for p in saved_cfg.get("pids", [])
                        if p.get("type") == "std" and p.get("enabled", True)]
            rpm_rows = [p for p in std_rows if str(p.get("cmd", "")).startswith("010C")]
            if rpm_rows and new_settings.get("std_enabled", True):
                n = rpm_rows[0]["parameters"][0]["name"]
                c.add("the standard rows found on the device keep reading the "
                      "simulator beside the profile (%s == 800)" % n,
                      near(std_values.get(n), 800.0), "got %s" % fmt(std_values.get(n)))
        with open(os.path.join(out, "values.json"), "w") as f:
            json.dump({"values": values, "advances": advances, "std": std_values,
                       "actor": summ}, f, indent=1)

        # ---- L4 the one-shot ---------------------------------------------
        print("L4 the one-shot: every PID through the test route (raw reply lines to the log)")
        pid_by_cmd = {p["cmd"]: p for p in cfg["pids"] if p.get("type") == "specific"}
        shots = {}
        for rep in replies:
            row = pid_by_cmd.get(rep["pid"])
            if row is None:
                continue
            st, body = http(base, "POST", "/api/autopid/test",
                            {"cmd": row["cmd"], "type": "specific",
                             "init": row.get("init", ""),
                             "expressions": [p["expression"] for p in row["parameters"]]},
                            timeout=60)
            try:
                t = json.loads(body)
            except ValueError:
                t = {"error": body[:120]}
            shots[rep["pid"]] = t
            raw = str(t.get("raw", "")).replace("\r", "|").replace("\n", "|")
            print("  %s: ok=%s raw=%s%s" % (rep["pid"], t.get("ok"), raw[:150],
                                           " ..." if len(raw) > 150 else ""))
            vals = t.get("values") or []
            good = sum(1 for p, v in zip(rep["params"], vals) if near(v, p["expected"]))
            capped = sum(1 for p, v in zip(rep["params"], vals)
                         if v is None and p["max_raw_index"] >= PAYLOAD_CAP)
            c.add("test route %s answers and decodes as the profile means" % rep["pid"],
                  st == 200 and t.get("ok") and good + capped == len(rep["params"]),
                  "%s ok=%s %d/%d right%s%s" % (
                      st, t.get("ok"), good, len(rep["params"]),
                      ", %d past the cap" % capped if capped else "",
                      (" error=%s" % t.get("error")) if t.get("error") else ""))
            time.sleep(0.8)
        with open(os.path.join(out, "test_route.json"), "w") as f:
            json.dump(shots, f, indent=1)
    finally:
        # ---- L5 restore --------------------------------------------------
        print("L5 restore")
        if actor is not None and actor.poll() is None:
            actor.terminate()
            try:
                actor.wait(5)
            except subprocess.TimeoutExpired:
                actor.kill()
        if saved_cfg is not None:
            st, body = http(base, "PUT", "/api/autopid/config", saved_cfg)
            c.add("tables restored", st == 200, "%s %s" % (st, body.strip()[:80]))
        if settings_changed and saved_settings is not None:
            restore = dict(saved_settings)
            for k in ("degraded", "pending_reboot"):
                restore.pop(k, None)
            st, body = http(base, "PUT", "/api/settings/autopid", restore)
            c.add("settings restored", st == 200, "%s %s" % (st, body.strip()[:80]))
            c.add("the DUT rebooted and is back (restore)", submit_and_wait(base))
        if saved_cfg is not None:
            st, body = http(base, "POST", "/api/autopid/test", {"cmd": "0100"},
                            timeout=40)
            try:
                t = json.loads(body)
            except ValueError:
                t = {}
            c.add("the chip is back on the baseline (0100 answered by the simulator)",
                  st == 200 and t.get("ok") and
                  str(t.get("raw", "")).replace(" ", "").startswith("4100"),
                  "raw=%r" % str(t.get("raw", ""))[:40])

    failed = c.failed()
    print("checks: %d, failed: %d, warnings: %d" % (len(c.rows), len(failed), len(c.warns)))
    for w in c.warns:
        print("WARN: " + w)
    if failed:
        print("AUTOPID PROFILE E2E FAIL: " + "; ".join(failed))
        return 1
    print("AUTOPID PROFILE E2E PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
