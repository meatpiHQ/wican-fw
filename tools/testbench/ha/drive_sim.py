#!/usr/bin/env python3
"""The car of the drive bench: a drive profile played on ECU sim v2.

A drive profile (JSON, `fixtures/drive_default.json`) names the parameters of
a drive: where each one lives in the simulator (`POST /api/ecu/pids/<mode>/
<pid>` with `{"<param>": value}`), the AutoPID row that reads it on the
WiCAN, and the PATTERN its value follows over the run. `DriveSim` is the
thread that plays the profile: every `tick` it works out each parameter's
value for the moment, sends the ones that changed and writes every send to
the TRUTH file, one JSON line each:

    {"name": "EngineRPM", "value": 2150, "t0": <PC epoch before the POST>,
     "t1": <PC epoch after it>, "ok": true}

The truth is what `drive_report.py` holds the WiCAN's log and Home
Assistant's statistics against.

Patterns (`t` = seconds since the run began; every value is rounded to the
parameter's `step` and clipped to `min`/`max`):

  {"kind": "const",   "value": 22}
  {"kind": "sine",    "min": 800, "max": 3600, "period_s": 300, "phase_s": 0}
  {"kind": "ramp",    "from": 20, "to": 90, "over_s": 600}             then held
  {"kind": "saw",     "min": 0, "max": 100, "period_s": 240}           ramp, again
  {"kind": "steps",   "values": [0, 30, 60, 90], "hold_s": 45}         in a ring
  {"kind": "random",  "min": 10, "max": 80, "hold_s": 20, "seed": 7}   a new draw per hold
  {"kind": "walk",    "min": 13.2, "max": 14.6, "max_delta": 0.2, "hold_s": 10, "seed": 3}
  {"kind": "counter", "per_s": 1, "wrap": 65535}                       t itself
  {"kind": "points",  "points": [[0, 0], [30, 50], [90, 50], [120, 0]], "repeat": true}

  python tools/testbench/ha/drive_sim.py --profile <file> --preview 600   # the values, no simulator
"""
import argparse
import json
import math
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROFILE = os.path.join(HERE, "fixtures", "drive_default.json")


class Param:
    """One parameter of a drive profile."""

    def __init__(self, doc):
        self.name = doc["name"]
        mode, pid, key = doc["sim"].split("/")
        self.mode, self.pid, self.key = mode.upper(), pid.upper(), key
        self.row = doc.get("row")
        self.pattern = doc.get("pattern") or {"kind": "const", "value": 0}
        self.step = float(doc.get("step", 1))
        self.tol = float(doc.get("tol", self.step / 2 if self.step else 0.5))
        self.every_s = float(doc.get("every_s", 0))     # 0 = the bench's --step-s
        self.lo = doc.get("min")
        self.hi = doc.get("max")
        self.scale = float(doc.get("scale", 1))         # logged value = set value * scale + offset
        self.offset = float(doc.get("offset", 0))
        self._walk = None

    @property
    def sim_path(self):
        return f"/api/ecu/pids/{self.mode}/{self.pid}"

    def same_pid(self, mode, pid):
        try:
            return int(self.mode, 16) == int(str(mode), 16) and int(self.pid, 16) == int(str(pid), 16)
        except ValueError:
            return False

    def expected(self, set_value):
        """What the WiCAN decodes for a value set in the simulator."""
        return set_value * self.scale + self.offset

    def quantize(self, v):
        if self.step:
            v = round(v / self.step) * self.step
        if self.lo is not None:
            v = max(float(self.lo), v)
        if self.hi is not None:
            v = min(float(self.hi), v)
        v = round(v, 6)
        return int(v) if float(v).is_integer() else v

    def value_at(self, t):
        return self.quantize(pattern_value(self.pattern, t, self))


def _hold_index(t, hold_s):
    return int(max(0.0, t) // max(0.001, float(hold_s)))


def pattern_value(pat, t, state=None):
    """The value of a pattern `t` seconds into the run."""
    kind = pat.get("kind", "const")
    if kind == "const":
        return float(pat.get("value", 0))
    if kind == "sine":
        lo, hi = float(pat["min"]), float(pat["max"])
        ph = (t + float(pat.get("phase_s", 0))) / float(pat.get("period_s", 300))
        return lo + (hi - lo) * (0.5 - 0.5 * math.cos(2 * math.pi * ph))
    if kind == "ramp":
        a, b, over = float(pat["from"]), float(pat["to"]), float(pat.get("over_s", 600))
        return a + (b - a) * min(1.0, max(0.0, t / over))
    if kind == "saw":
        lo, hi = float(pat["min"]), float(pat["max"])
        return lo + (hi - lo) * ((t % float(pat.get("period_s", 240))) / float(pat.get("period_s", 240)))
    if kind == "steps":
        vals = pat["values"]
        return float(vals[_hold_index(t, pat.get("hold_s", 30)) % len(vals)])
    if kind == "random":
        i = _hold_index(t, pat.get("hold_s", 20))
        rnd = random.Random(int(pat.get("seed", 1)) * 1000003 + i)
        return rnd.uniform(float(pat["min"]), float(pat["max"]))
    if kind == "walk":
        # a walk is a function of the hold index: replayed from the start so
        # the same profile gives the same drive, whenever it is asked
        lo, hi = float(pat["min"]), float(pat["max"])
        i = _hold_index(t, pat.get("hold_s", 10))
        cache = getattr(state, "_walk", None) if state is not None else None
        if cache is None or cache[0] > i:
            cache = [-1, float(pat.get("start", (lo + hi) / 2)), random.Random(int(pat.get("seed", 1)))]
        while cache[0] < i:
            cache[1] = min(hi, max(lo, cache[1] + cache[2].uniform(-1, 1) * float(pat.get("max_delta", 1))))
            cache[0] += 1
        if state is not None:
            state._walk = cache
        return cache[1]
    if kind == "counter":
        v = t * float(pat.get("per_s", 1)) + float(pat.get("start", 0))
        wrap = pat.get("wrap")
        return v % float(wrap) if wrap else v
    if kind == "points":
        pts = [(float(a), float(b)) for a, b in pat["points"]]
        span = pts[-1][0]
        if pat.get("repeat", True) and span > 0:
            t = t % span
        if t <= pts[0][0]:
            return pts[0][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t <= t1:
                return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return pts[-1][1]
    raise ValueError(f"unknown pattern kind {kind!r}")


def load_profile(path=None, only=None, limit=0):
    """The profile document and its parameters (`only` = names to keep)."""
    with open(path or DEFAULT_PROFILE, encoding="utf-8") as f:
        doc = json.load(f)
    params = [Param(p) for p in doc.get("parameters", [])]
    if only:
        params = [p for p in params if p.name in only]
    if limit:
        params = params[:limit]
    names = [p.name for p in params]
    if len(set(names)) != len(names):
        raise ValueError("drive profile: a parameter name is used twice")
    return doc, params


def sim_http(base, path, method="GET", body=None, timeout=6):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode("utf-8", "replace")
    try:
        return code, json.loads(text)
    except ValueError:
        return code, text


def sim_values(base):
    """{(mode int, pid int): {param name: {value, min, max, unit}}} as the
    simulator holds them now."""
    code, doc = sim_http(base, "/api/ecu/pids", timeout=10)
    if code != 200 or not isinstance(doc, dict):
        raise RuntimeError(f"the simulator's /api/ecu/pids: {code} {str(doc)[:120]}")
    out = {}
    for group in doc.values():
        if not isinstance(group, list):
            continue
        for e in group:
            try:
                key = (int(str(e.get("mode")), 16), int(str(e.get("pid")), 16))
            except ValueError:
                continue
            out[key] = {p["name"]: p for p in e.get("params", []) if isinstance(p, dict) and "name" in p}
    return out


def sim_lookup(values, param):
    return (values.get((int(param.mode, 16), int(param.pid, 16))) or {}).get(param.key)


class DriveSim(threading.Thread):
    """Plays a profile on the simulator and writes the truth file."""

    def __init__(self, base, params, truth_path, step_s=5.0, tick_s=0.5, t0=None):
        super().__init__(daemon=True, name="drive_sim")
        self.base = base
        self.params = params
        self.truth_path = truth_path
        self.step_s = float(step_s)
        self.tick_s = float(tick_s)
        self.t0 = t0 if t0 is not None else time.time()
        self.sent = 0
        self.failed = 0
        self.last_error = ""
        self.last = {}                  # name -> the value in the simulator now
        self._due = {p.name: 0.0 for p in params}
        self._halt = threading.Event()
        self._lock = threading.Lock()
        self._frozen = False

    def freeze(self, on=True):
        """Hold every value where it is (a parked car)."""
        self._frozen = bool(on)

    def stop(self):
        self._halt.set()

    def _send(self, f, p, value):
        t_before = time.time()
        ok, why = False, ""
        try:
            code, r = sim_http(self.base, p.sim_path, "POST", {p.key: value}, timeout=5)
            ok = code == 200
            if not ok:
                why = f"{code} {str(r)[:80]}"
        except (urllib.error.URLError, OSError) as e:
            why = f"{type(e).__name__}: {e}"
        t_after = time.time()
        rec = {"name": p.name, "value": value, "t0": round(t_before, 3), "t1": round(t_after, 3), "ok": ok}
        if not ok:
            rec["error"] = why
            self.failed += 1
            self.last_error = f"{p.name}: {why}"
        else:
            self.sent += 1
            self.last[p.name] = value
        f.write(json.dumps(rec) + "\n")
        f.flush()
        return ok

    def run(self):
        with open(self.truth_path, "a", encoding="utf-8") as f:
            while not self._halt.is_set():
                now = time.time()
                t = now - self.t0
                if not self._frozen:
                    for p in self.params:
                        if now < self._due[p.name]:
                            continue
                        value = p.value_at(t)
                        if p.name in self.last and self.last[p.name] == value:
                            self._due[p.name] = now + (p.every_s or self.step_s)
                            continue
                        if self._send(f, p, value):
                            self._due[p.name] = now + (p.every_s or self.step_s)
                        else:
                            self._due[p.name] = now + 2.0      # the simulator did not take it: again soon
                self._halt.wait(self.tick_s)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--preview", type=float, default=600, help="print the values over this many seconds")
    ap.add_argument("--step-s", type=float, default=30)
    a = ap.parse_args()
    doc, params = load_profile(a.profile)
    print(f"{doc.get('name', os.path.basename(a.profile))}: {len(params)} parameter(s)")
    t = 0.0
    print("t_s".rjust(6) + "".join(p.name[:13].rjust(14) for p in params))
    while t <= a.preview:
        print(f"{t:6.0f}" + "".join(str(p.value_at(t)).rjust(14) for p in params))
        t += a.step_s
    return 0


if __name__ == "__main__":
    sys.exit(main())
