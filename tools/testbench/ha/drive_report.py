#!/usr/bin/env python3
"""The judge of the drive bench: the truth, the unit's log and Home
Assistant held side by side, per parameter and per period.

Reads a run folder of `ha_drive_backfill_bench.py`:

  run.json            the plan, the phases (home / away / parked), the clocks,
                      the entities, every catch-up run the integration made
  truth.jsonl         every value set in the simulator, with the PC's stamps
  device_rows.jsonl   the unit's log as its export route gave it
  ha_stats_hour.json  Home Assistant's hourly statistics (what the import writes)
  ha_stats_5min.json  its five-minute statistics (made from live states only)
  ha_states.json      the states (what the History panel draws)

and writes `report.json`, `report.md` and `report.html` (a chart per
parameter). Every time is moved onto the UNIT's clock, which stamps the rows.

Per parameter:
  log     rows while at home and while away, the share of the samples that
          should be there, every hole in the rows, every row whose value the
          simulator did not hold at that moment, every value the simulator
          held that never shows; the rows from the logger's start to the
          drive against the values the run found in the simulator
  hours   for each hour the unit recorded: the mean, minimum and maximum of
          its rows against Home Assistant's statistics row of that hour:
          exact / differs / missing / pending (the hour had not ended)
  states  how many states Home Assistant has from the away time, and whether
          each of them is a value the unit logged at that moment (the
          integration writes the logged values of the away time as states
          since 2.0.0-beta2; before that the History panel had none)
  live    Home Assistant's live states against the unit's rows of the same
          moment: other numbers mean Home Assistant keeps the sensor in
          another unit than the unit logs

Units: the unit logs in its own unit (°C, km/h, kPa), Home Assistant keeps a
sensor in the unit it shows (°F, mph, psi on a US system). Every comparison
converts the unit's value into Home Assistant's unit first, so an hour that
went in as the raw number (the integration until 2.0.0-beta1) reads
"differs". The unit's own battery voltage (`device.batt_voltage` in the log
since firmware 6.0.0-beta.15) is judged like a parameter, against the
device's Battery Voltage sensor.

  python tools/testbench/ha/drive_report.py <logdir>
"""
import bisect
import calendar
import html
import json
import os
import statistics
import sys
import time

REL_TOL = 1e-6
CONV_TOL = 2e-5                 # a converted figure: the two sides' constants differ in the seventh digit

# ---- units ---------------------------------------------------------------------
# The unit's unit names as its table writes them, in Home Assistant's spelling.
UNIT_ALIASES = {"degC": "°C", "degF": "°F", "deg": "°", "grams/sec": "g/s", "seconds": "s", "sec": "s",
                "kph": "km/h", "km/hr": "km/h", "": None}
# Factors to a base unit, per kind (Home Assistant's own constants).
_LINEAR = (
    {"Pa": 1.0, "hPa": 100.0, "mbar": 100.0, "kPa": 1000.0, "bar": 1e5, "cbar": 1000.0, "MPa": 1e6,
     "psi": 6894.757, "inHg": 3386.389, "mmHg": 133.322},
    {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 1609.344 / 3600, "kn": 1852 / 3600, "ft/s": 0.3048},
    {"m": 1.0, "km": 1000.0, "mi": 1609.344, "ft": 0.3048, "yd": 0.9144, "in": 0.0254, "cm": 0.01, "mm": 0.001},
    {"L": 1.0, "mL": 0.001, "gal": 3.785411784, "fl. oz.": 0.0295735295625, "m³": 1000.0},
    {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "oz": 0.028349523125},
)


def unit_name(unit):
    return UNIT_ALIASES.get(unit, unit) if unit is not None else None


def unit_converter(logged, kept):
    """The function that turns the unit's value into Home Assistant's unit;
    the value as it is when the two are the same or one is not known; None
    when both are known and this table has no conversion between them."""
    a, b = unit_name(logged), unit_name(kept)
    if not a or not b or a == b:
        return lambda v: v
    temps = {"°C", "°F", "K"}
    if a in temps and b in temps:
        to_c = {"°C": lambda v: v, "°F": lambda v: (v - 32) / 1.8, "K": lambda v: v - 273.15}[a]
        from_c = {"°C": lambda v: v, "°F": lambda v: v * 1.8 + 32, "K": lambda v: v + 273.15}[b]
        return lambda v: from_c(to_c(v))
    for table in _LINEAR:
        if a in table and b in table:
            factor = table[a] / table[b]
            return lambda v: v * factor
    return None


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _iso_to_epoch(text):
    """An ISO stamp of Home Assistant ("2026-11-21T14:20:54.123456+00:00")."""
    try:
        head, _, frac = text.partition(".")
        base = head[:19]
        secs = calendar.timegm(time.strptime(base, "%Y-%m-%dT%H:%M:%S"))
        tz = text[19:]
        digits = ""
        for ch in frac:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            secs += float("0." + digits)
        sign = tz.rfind("+") if "+" in tz else tz.rfind("-")
        if sign >= 0 and ":" in tz[sign:]:
            hh, mm = tz[sign + 1:].split(":")[:2]
            delta = int(hh) * 3600 + int(mm[:2]) * 60
            secs -= delta if tz[sign] == "+" else -delta
        return secs
    except (ValueError, IndexError):
        return None


def hhmm(t, secs=False):
    return time.strftime("%H:%M:%S" if secs else "%H:%M", time.gmtime(t))


def load_rows(path):
    """{name: [(ts_s, value), ...]} sorted, from the export's lines."""
    out = {}
    try:
        f = open(path, encoding="utf-8")
    except OSError:
        return out
    with f:
        for line in f:
            if not line.startswith("{") or line.startswith('{"_'):
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            ts, name, value = doc.get("ts"), doc.get("param"), doc.get("value")
            if not isinstance(ts, (int, float)) or not isinstance(name, str) or isinstance(value, bool) \
                    or not isinstance(value, (int, float)):
                continue
            if ts > 1e11:
                ts /= 1000.0
            out.setdefault(name.rsplit(".", 1)[-1], []).append((ts, float(value)))
    for rows in out.values():
        rows.sort()
    return out


def load_truth(path, to_dut, params):
    """{name: [(start, end, expected value), ...]}: a value is in the
    simulator from its POST until the next one for that parameter landed."""
    sets = {}
    try:
        f = open(path, encoding="utf-8")
    except OSError:
        return {}
    with f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("ok"):
                sets.setdefault(d["name"], []).append((to_dut(d["t0"]), to_dut(d["t1"]), d["value"]))
    out = {}
    for name, items in sets.items():
        p = params.get(name, {})
        scale, offset = p.get("scale", 1), p.get("offset", 0)
        segs = []
        for i, (a, b, v) in enumerate(items):
            end = items[i + 1][1] if i + 1 < len(items) else float("inf")
            segs.append((a, end, v * scale + offset))
        out[name] = segs
    return out


def phase_at(phases, t):
    for ph in phases:
        if ph["t0"] <= t < ph["t1"]:
            return ph
    return None


def overlaps(phases, kind, a, b):
    return any(ph["kind"] == kind and ph["t0"] < b and a < ph["t1"] for ph in phases)


def rows_between(rows, a, b):
    lo = bisect.bisect_left(rows, (a, float("-inf")))
    hi = bisect.bisect_left(rows, (b, float("-inf")))
    return rows[lo:hi]


def analyze_param(name, rows, truth, phases, run, x0, x1, judge):
    """The log part of one parameter's record."""
    table_period = run.get("table", {}).get("period_ms", 1000) / 1000.0
    in_run = rows_between(rows, x0, x1)
    out = {"rows": len(in_run), "phases": [], "gaps": [], "mismatches": 0, "mismatch_examples": [],
           "unseen": 0, "unseen_examples": [], "checked": 0,
           "before": {"checked": 0, "mismatches": 0, "examples": []}}
    live = [r for r in in_run if not overlaps(phases, "parked", r[0], r[0] + 0.001)]
    # a parameter's period is the median step of its own rows, holes left out.
    # What counts as a hole is not tied to a fast table alone: the unit's own
    # values (the battery voltage, a row every 10 s) keep their pace whatever
    # the table's period is, and with a table of 100 ms a limit of 30 periods
    # threw every one of their steps away (the period then fell back to the
    # table's and the coverage read 1 %).
    diffs = [b[0] - a[0] for a, b in zip(live, live[1:]) if 0 < b[0] - a[0] < max(30 * table_period, 60.0)]
    period = statistics.median(diffs) if len(diffs) >= 5 else table_period
    out["period_s"] = round(period, 3)
    changed_only = judge.get("log_mode") == "changed"
    gap_thr = judge.get("gap_s") or max(10.0, 5 * period)
    for ph in phases:
        got = rows_between(rows, ph["t0"], ph["t1"])
        dur = ph["t1"] - ph["t0"]
        expect = dur / period if period > 0 else 0
        rec = {"kind": ph["kind"], "trip": ph.get("trip"), "rows": len(got), "duration_s": round(dur),
               "coverage_pct": None if changed_only or ph["kind"] == "parked" or expect < 1
               else round(min(100.0, 100.0 * len(got) / expect), 1)}
        out["phases"].append(rec)
        if changed_only or ph["kind"] == "parked":
            continue
        edges = [ph["t0"]] + [r[0] for r in got] + [ph["t1"]]
        for a, b in zip(edges, edges[1:]):
            if b - a > gap_thr:
                out["gaps"].append({"from": round(a, 1), "to": round(b, 1), "seconds": round(b - a, 1),
                                    "phase": ph["kind"], "trip": ph.get("trip")})
    if truth:
        starts = [s[0] for s in truth]
        slack = judge.get("slack_s", 2.5)
        tol = run["_params"].get(name, {}).get("tol", 0.5) + 1e-9
        # Before the first value was set the simulator held what the run found
        # in it: every row from the logger's start to the drive is judged
        # against that (only when this run switched the logger on itself, or
        # the rows could be another run's).
        found = (run.get("simulator_as_found") or {}).get(name)
        if found is not None and ((run.get("as_found") or {}).get("data_logger") or {}).get("enabled") is False:
            par = run["_params"].get(name, {})
            want = found * par.get("scale", 1) + par.get("offset", 0)
            for ts, value in rows:
                if ts >= truth[0][0] + slack:
                    break
                out["before"]["checked"] += 1
                # around the first set value a row may carry any value the
                # simulator held by then, as in the drive itself: with
                # --step-s under the slack the second and third value are
                # there before the slack is over (a row "1" 0.4 s after the
                # simulator went from 1 to 2 was called wrong)
                held = [s[2] for s in truth[:bisect.bisect_right(starts, ts + slack)] if s[1] + slack >= ts]
                if abs(value - want) > tol and not any(abs(value - a) <= tol for a in held):
                    out["before"]["mismatches"] += 1
                    if len(out["before"]["examples"]) < 5:
                        out["before"]["examples"].append({"at": hhmm(ts, True), "logged": value, "simulator": want})
        for ts, value in in_run:
            if ts < truth[0][0] + slack:
                continue                # before the first value was set
            out["checked"] += 1
            hi = bisect.bisect_right(starts, ts + slack)
            allowed = [s[2] for s in truth[max(0, hi - 40):hi] if s[1] + slack >= ts]
            if not any(abs(value - a) <= tol for a in allowed):
                out["mismatches"] += 1
                if len(out["mismatch_examples"]) < 5:
                    out["mismatch_examples"].append({"at": hhmm(ts, True), "logged": value,
                                                     "simulator": sorted(set(allowed))[:4]})
        if not changed_only:
            need = max(3 * period, 2 * slack + period)
            for a, b, v in truth:
                b = min(b, x1)
                if b - a < need or a < x0 or overlaps(phases, "parked", a, b):
                    continue
                if any(g["from"] < b and a < g["to"] for g in out["gaps"]):
                    continue            # inside a hole already reported as one
                if not any(abs(r[1] - v) <= tol for r in rows_between(rows, a - slack, b + slack)):
                    out["unseen"] += 1
                    if len(out["unseen_examples"]) < 5:
                        out["unseen_examples"].append({"from": hhmm(a, True), "to": hhmm(b, True), "value": v})
    return out


def stats_by_hour(stat_rows):
    by_hour = {}
    for r in stat_rows or []:
        st = r.get("start")
        if isinstance(st, (int, float)):
            by_hour[int(st / 1000 if st > 1e11 else st)] = r
    return by_hour


def hour_figures(got, conv):
    """Mean, minimum and maximum of rows, in Home Assistant's unit."""
    vals = [conv(v) for _, v in got]
    return {"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals)}


def figures_match(ha, exp, tol):
    return all(abs(ha.get(k) - exp[k]) <= tol * max(1.0, abs(exp[k])) for k in ("mean", "min", "max")
               if ha.get(k) is not None)


def analyze_hours(rows, stat_rows, x0, x1, t_validate, conv=None, converted=False):
    """The unit's rows per hour against Home Assistant's statistics rows.
    `conv` turns a logged value into the unit of the statistics (None: this
    table has no conversion between the two units, nothing can be judged)."""
    by_hour = stats_by_hour(stat_rows)
    tol = CONV_TOL if converted else REL_TOL
    out = []
    h = int(x0 // 3600) * 3600
    while h <= x1:
        got = rows_between(rows, h, h + 3600)
        ha = by_hour.get(h)
        rec = {"hour": h, "label": hhmm(h) + "Z", "rows": len(got), "ha": None, "expected": None}
        if ha:
            rec["ha"] = {k: ha.get(k) for k in ("mean", "min", "max")}
        if got:
            exp = rec["expected"] = hour_figures(got, conv) if conv else None
            if converted:
                rec["logged"] = hour_figures(got, lambda v: v)
            if h + 3600 > t_validate:
                rec["status"] = "pending"
            elif not ha or ha.get("mean") is None:
                rec["status"] = "missing"
            elif exp is None:
                rec["status"] = "differs"             # two units this judge cannot convert between
            else:
                rec["status"] = "exact" if figures_match(ha, exp, tol) else "differs"
                if rec["status"] == "differs" and converted and figures_match(ha, rec["logged"], REL_TOL):
                    rec["raw_number"] = True          # the logged number went in as it was
        else:
            rec["status"] = "live only" if ha else "no rows"
        out.append(rec)
        h += 3600
    return out


def analyze_kept(rows, snapshot_rows, hours, t_pull, conv, converted):
    """An hour imported from the rows the pull at the return kept, while the
    unit slept: the statistics as they were at that import against the unit's
    rows of that hour up to the pull (the pull reads what the unit had written
    to its card by then: its last few seconds may or may not be in it)."""
    by_hour = stats_by_hour(snapshot_rows)
    out = []
    for h in hours:
        got = rows_between(rows, h, h + 3600)
        ha = by_hour.get(h)
        if not got:
            continue
        rec = {"hour": h, "label": hhmm(h) + "Z", "ha": {k: ha.get(k) for k in ("mean", "min", "max")} if ha else None,
               "status": "missing"}
        if ha and ha.get("mean") is not None and conv is not None:
            rec["status"] = "differs"
            cuts = [i + 1 for i, (t, _) in enumerate(got) if t_pull - 20 <= t <= t_pull + 3]
            first = cuts[0] - 1 if cuts else sum(1 for t, _ in got if t <= t_pull)
            for n in sorted(set([first] + cuts)):
                if n > 0 and figures_match(ha, hour_figures(got[:n], conv), CONV_TOL if converted else REL_TOL):
                    rec["status"] = "exact"
                    rec["rows"] = n
                    break
        out.append(rec)
    return out


def analyze_states(items, phases, ha_to_dut, x0, x1):
    """The states as (moment on the unit's clock, value, decimals the state
    is written with)."""
    pts = []
    for it in items or []:
        t = _iso_to_epoch(str(it.get("last_changed") or it.get("last_updated") or ""))
        if t is None:
            continue
        text = str(it.get("state"))
        try:
            v = float(text)
        except (TypeError, ValueError):
            v = None
        pts.append((ha_to_dut(t), v, len(text.partition(".")[2]) if v is not None else 0))
    pts.sort(key=lambda p: p[0])
    in_run = [p for p in pts if x0 <= p[0] <= x1]
    away = sum(1 for t, v, _ in in_run if v is not None and (phase_at(phases, t) or {}).get("kind") == "away")
    home = sum(1 for t, v, _ in in_run if v is not None and (phase_at(phases, t) or {}).get("kind") == "home")
    return {"points": pts, "numeric_home": home, "numeric_away": away,
            "unavailable": sum(1 for _, v, _d in in_run if v is None)}


def _state_tol(v, dec, conv):
    """How far a state may be from the logged value it shows: half of its
    last digit (Home Assistant rounds what it shows), the three places the
    integration keeps of the unit's value, taken through the conversion."""
    slope = abs(conv(1.0) - conv(0.0))
    return 0.5 * 10 ** (-dec) + 0.0006 * max(1.0, slope) + 1e-6 * abs(v)


def match_states(points, rows, x0, x1, conv=None, back=30.0, fwd=3.0):
    """Home Assistant's live states against the unit's rows of the same
    moment: a state is a value a push carried, so a row with that number (in
    Home Assistant's unit: `conv`) lies a few seconds before it."""
    conv = conv or (lambda v: v)
    ts = [t for t, _ in rows]
    checked, off, examples = 0, 0, []
    for t, v, dec in points:
        if v is None or t < x0 or t > x1:
            continue
        a, b = bisect.bisect_left(ts, t - back), bisect.bisect_right(ts, t + fwd)
        if a == b:
            continue
        near = min(range(a, b), key=lambda i: abs(conv(rows[i][1]) - v))
        if v == 0 and rows[near][1] == 0:
            continue                                    # zero is zero in every unit: it tells nothing
        checked += 1
        if abs(conv(rows[near][1]) - v) > _state_tol(v, dec, conv):
            off += 1
            if len(examples) < 3:
                examples.append({"at": hhmm(t, True), "state": v, "logged": rows[b - 1][1],
                                 "in_ha_unit": round(conv(rows[b - 1][1]), 6)})
    scale = None
    if checked >= 2:                                    # a parameter that never changes has two states in a run
        scale = "other" if off > 0.5 * checked else "same"
    return {"checked": checked, "off": off, "examples": examples, "scale": scale}


def match_away(points, rows, phases, conv=None, reach=2.5):
    """The states Home Assistant has from the away time: each is a logged
    value written with the row's own stamp (the integration's History fill),
    so a row with that value lies within a second or two of it. Also: did
    the logged value change at all while away (a value that stood still is
    one state at most, there is nothing to draw)."""
    conv = conv or (lambda v: v)
    ts = [t for t, _ in rows]
    away = [ph for ph in phases if ph["kind"] == "away"]
    states, matched, examples = 0, 0, []
    for t, v, dec in points:
        if v is None or not any(ph["t0"] < t < ph["t1"] for ph in away):
            continue
        states += 1
        a, b = bisect.bisect_left(ts, t - reach), bisect.bisect_right(ts, t + reach)
        if a < b and min(abs(conv(rows[i][1]) - v) for i in range(a, b)) <= _state_tol(v, dec, conv):
            matched += 1
        elif len(examples) < 3:
            examples.append({"at": hhmm(t, True), "state": v,
                             "logged_near": [round(conv(rows[i][1]), 4) for i in range(a, min(b, a + 3))]})
    distinct = set()
    span = 0.0
    for ph in away:
        got = rows_between(rows, ph["t0"] + 30, ph["t1"] - 5)   # the first seconds: the last push may still carry them
        distinct.update(round(conv(v), 3) for _, v in got)
        span += max(0.0, ph["t1"] - ph["t0"])
    return {"states": states, "matched": matched, "examples": examples, "changed": len(distinct) > 1,
            "distinct_values": len(distinct), "away_s": round(span)}


def match_spacing(points, rows, phases, steps, conv=None, to_ha=None, margin=20.0):
    """The away time's states against the integration's option History detail
    (`steps`: seconds per trip, 0 = every change). The rule as the option's
    text gives it: a state when the logged value, as the sensor shows it,
    is another than the last one written, and not sooner than the spacing
    after it. A state of the fill carries its row's own stamp, so here the
    states are taken on Home Assistant's raw time (`to_ha` undoes the judge's
    clock mapping) and compared with the rows to the millisecond.

    Per away phase, inside it by `margin` seconds at both ends (the first
    and the last state of a gap follow other rules): the states found, how
    many sit on a logged row's stamp, how many come sooner than the spacing
    after the one before, and the number the rule gives when it is started
    at the first state found."""
    conv = conv or (lambda v: v)
    to_ha = to_ha or (lambda t: t)
    dec = max((d for _, v, d in points if v is not None), default=0)
    stamps = [t for t, _ in rows]
    out = []
    for i, ph in enumerate(p for p in phases if p["kind"] == "away"):
        step = float(steps[min(i, len(steps) - 1)])
        a, b = ph["t0"] + margin, ph["t1"] - margin
        got = sorted((to_ha(t), v) for t, v, _ in points if v is not None and a < to_ha(t) < b)
        rec = {"trip": ph.get("trip"), "step": step, "states": len(got), "on_a_row": 0, "too_soon": 0,
               "rule": None, "rows": len(rows_between(rows, a, b))}
        for k, (t, _) in enumerate(got):
            j = bisect.bisect_left(stamps, t - 0.002)
            rec["on_a_row"] += j < len(stamps) and abs(stamps[j] - t) <= 0.002
            rec["too_soon"] += k > 0 and t - got[k - 1][0] < step - 0.002
        if got:
            last_t, last_v, count = got[0][0], round(got[0][1], dec), 1
            for t, v in rows_between(rows, got[0][0] + 0.002, b):
                shown = round(conv(v), dec)
                if shown != last_v and t - last_t >= step - 1e-6:
                    last_t, last_v, count = t, shown, count + 1
            rec["rule"] = count
        out.append(rec)
    return out


def decimate(rows, x0, x1, gap_thr, columns=900):
    """At most four points a column (first, low, high, last) and a None
    where the rows have a hole, for the chart."""
    out, last_t = [], None
    width = max(1e-6, (x1 - x0) / columns)
    bucket, cur = None, []

    def flush():
        if not cur:
            return
        keep = {0, len(cur) - 1, min(range(len(cur)), key=lambda i: cur[i][1]), max(range(len(cur)), key=lambda i: cur[i][1])}
        out.extend([round(cur[i][0], 1), cur[i][1]] for i in sorted(keep))

    for t, v in rows:
        if t < x0 or t > x1:
            continue
        if last_t is not None and t - last_t > gap_thr:
            flush()
            cur = []
            out.append(None)
        b = int((t - x0) / width)
        if b != bucket:
            flush()
            cur = []
            bucket = b
        cur.append((t, v))
        last_t = t
    flush()
    return out


def analyze(logdir):
    run = _read_json(os.path.join(logdir, "run.json"))
    if not run:
        raise SystemExit(f"no run.json in {logdir}")
    clock = run.get("clock", {})
    off_dut = (clock.get("dut_minus_pc_start", 0) + clock.get("dut_minus_pc_end", clock.get("dut_minus_pc_start", 0))) / 2
    off_pi = (clock.get("pi_minus_pc_start", 0) + clock.get("pi_minus_pc_end", clock.get("pi_minus_pc_start", 0))) / 2
    to_dut = lambda t: t + off_dut                          # noqa: E731
    ha_to_dut = lambda t: t - off_pi + off_dut              # noqa: E731
    judge = run.get("judge", {})
    params = run["_params"] = {p["name"]: p for p in run.get("params", [])}
    x0 = to_dut(run.get("t_drive0", 0))
    x1 = to_dut(run.get("t_end", time.time()))
    t_validate = ha_to_dut(run.get("t_validate_ha", run.get("t_end", time.time()) + off_pi))
    phases = [dict(ph, t0=to_dut(ph["t0"]), t1=to_dut(ph["t1"])) for ph in run.get("phases", [])]
    if phases and phases[-1]["kind"] != "parked" and phases[-1]["t1"] < x1 - 1:
        phases.append({"kind": "home", "trip": phases[-1].get("trip"), "t0": phases[-1]["t1"], "t1": x1})
    rows = load_rows(os.path.join(logdir, "device_rows.jsonl"))
    truth = load_truth(os.path.join(logdir, "truth.jsonl"), to_dut, params)
    stats_h = _read_json(os.path.join(logdir, "ha_stats_hour.json"), {}) or {}
    stats_5 = _read_json(os.path.join(logdir, "ha_stats_5min.json"), {}) or {}
    states_raw = _read_json(os.path.join(logdir, "ha_states.json"), []) or []
    states_by = {}
    for series in states_raw:
        if isinstance(series, list) and series:
            states_by[series[0].get("entity_id")] = series
    entities = dict(run.get("entities", {}))
    for key, eid in (run.get("device_entities") or {}).items():
        if eid and key not in entities:
            entities[key] = {"entity_id": eid, "disabled": False, "device": True}
    kept_doc = _read_json(os.path.join(logdir, "ha_stats_hour_kept.json"), {}) or {}
    kept_stats = kept_doc.get("stats") or {}
    # the hours an import from the kept copy can have brought: those that had
    # ended when it ran, from the hour of the first pull after a return on
    t_kept = ha_to_dut(kept_doc["taken_at_ha"]) if kept_doc.get("taken_at_ha") else None
    # the pull whose rows were kept: the last one before that import that read rows and put some off
    pulls = [ha_to_dut(s["finished_at"]) for s in run.get("syncs") or []
             if s.get("ran") and s.get("rows_deferred") and s.get("finished_at")]
    t_pull = max((t for t in pulls if t_kept is None or t < t_kept), default=None)
    kept_hours = [int(t_pull // 3600) * 3600] if (t_kept and t_pull and int(t_pull // 3600) * 3600 + 3600 <= t_kept) else []
    fill_active = any((s.get("states") or {}).get("active") for s in run.get("syncs") or [])
    # the integration's option History detail as the bench set it, per trip (--history-step)
    hist_steps = [float(s) for s in (run.get("ha") or {}).get("history_step") or []]
    logged = run.get("table", {}).get("parameters") or []
    names = list(dict.fromkeys(list(params) + [n for n in logged if n in rows or n in entities]
                               + [n for n in rows if rows_between(rows[n], x0, x1)]))
    away = [ph for ph in phases if ph["kind"] == "away"]
    gap_thr_default = judge.get("gap_s") or 10.0
    per, charts = {}, []
    for name in names:
        r = rows.get(name, [])
        ent = entities.get(name) or {}
        eid = ent.get("entity_id")
        rec = {"driven": name in params, "entity": eid, "disabled": ent.get("disabled", False),
               "device": bool(ent.get("device"))}
        u = rec["units"] = (run.get("units") or {}).get(name) or {}
        kept_unit = u.get("statistics") or u.get("state")
        conv_stat = unit_converter(u.get("wican"), kept_unit)
        conv_state = unit_converter(u.get("wican"), u.get("state")) or (lambda v: v)
        converted = bool(unit_name(u.get("wican")) and unit_name(kept_unit) and unit_name(u.get("wican")) != unit_name(kept_unit))
        rec["converted"] = converted
        rec["log"] = analyze_param(name, r, truth.get(name), phases, run, x0, x1, judge)
        rec["hours"] = analyze_hours(r, stats_h.get(eid) if eid else None, x0, x1, t_validate, conv_stat, converted)
        rec["kept"] = analyze_kept(r, kept_stats.get(eid), kept_hours, t_pull, conv_stat, converted) if eid and kept_hours else []
        st = analyze_states(states_by.get(eid), phases, ha_to_dut, x0, x1) if eid else \
            {"points": [], "numeric_home": 0, "numeric_away": 0, "unavailable": 0}
        five = 0
        for row in (stats_5.get(eid) or []) if eid else []:
            s = row.get("start")
            if isinstance(s, (int, float)):
                s = ha_to_dut(s / 1000 if s > 1e11 else s)
                five += any(ph["t0"] <= s and s + 300 <= ph["t1"] for ph in away)
        rec["states"] = {k: v for k, v in st.items() if k != "points"}
        home_pts = [p for p in st["points"] if (phase_at(phases, p[0]) or {}).get("kind") == "home"]
        rec["live"] = match_states(home_pts, r, x0, x1, conv_state)
        rec["away"] = match_away(st["points"], r, phases, conv_state)
        rec["spacing"] = (match_spacing(st["points"], r, phases, hist_steps, conv_state, lambda t: t + off_pi - off_dut)
                          if hist_steps and eid else [])
        rec["five_min_rows_away"] = five
        done = [h for h in rec["hours"] if h["status"] in ("exact", "differs", "missing")]
        exact = [h for h in done if h["status"] == "exact"]
        rec["backfill"] = ("nothing to import yet" if not done else "every hour" if len(exact) == len(done)
                           else "no hour" if not exact else "some hours")
        per[name] = rec
        gap_thr = judge.get("gap_s") or max(10.0, 5 * rec["log"]["period_s"])
        charts.append({
            "name": name, "entity": eid or "", "driven": rec["driven"],
            "truth": [[round(max(a, x0 - 60), 1), v] for a, _, v in truth.get(name, [])],
            "dev": decimate(r, x0 - 60, x1 + 60, gap_thr),
            # the chart's axis is the unit's unit: a state of a sensor kept in
            # another unit is not drawn as a number of the wrong scale
            "states": [[round(t, 1), v] for t, v, _ in st["points"] if x0 - 60 <= t <= x1 + 60] if not converted else [],
            "hours": [{"h": h["hour"], "status": h["status"], "ha": h["ha"], "exp": h["expected"]} for h in rec["hours"]]
                     if not converted else
                     [{"h": h["hour"], "status": h["status"], "ha": h.get("logged") if h["status"] == "exact" else None,
                       "exp": h.get("logged")} for h in rec["hours"]],
            "backfill": rec["backfill"], "scale": "other" if converted else rec["live"]["scale"],
        })

    # ---- the verdict ---------------------------------------------------------------
    checks, notes = [], []
    changed_only = judge.get("log_mode") == "changed"
    minc = judge.get("min_coverage", 95)
    if away and not changed_only:
        bad, strays = [], []
        for name, rec in per.items():
            if name not in params and name not in logged and not rec["device"]:
                # rows of a parameter the run's table does not carry: the last
                # lap of the table the unit had before the run's own took over
                # (--table profile). Nothing says it should be there while away.
                strays.append(name)
                continue
            cov = [p["coverage_pct"] for p in rec["log"]["phases"] if p["kind"] == "away" and p["coverage_pct"] is not None]
            holes = [g for g in rec["log"]["gaps"] if g["phase"] == "away"]
            if not cov or min(cov) < minc or holes:
                why = f"{min(cov):.0f} %" if cov else "no rows"
                if holes:
                    why += f", {len(holes)} hole(s) up to {max(g['seconds'] for g in holes):.0f} s"
                bad.append(f"{name} ({why})")
        judged = len(per) - len(strays)
        checks.append((f"[log] every parameter recorded through the away time ({judged - len(bad)} of {judged}; "
                       f"{minc:.0f} % of the samples, no hole)", not bad, "; ".join(bad[:6])))
        if strays:
            notes.append(f"{len(strays)} parameter(s) have rows in the run and are not in the run's table (the unit's table from "
                         f"before the run, its last lap): {', '.join(strays[:8])}; not judged for the away time")
    driven = {n: r for n, r in per.items() if r["driven"]}
    if driven:
        bad = [f"{n}: {r['log']['mismatches']} of {r['log']['checked']} rows" for n, r in driven.items()
               if r["log"]["mismatches"] > max(2, 0.002 * r["log"]["checked"])]
        total = sum(r["log"]["checked"] for r in driven.values())
        checks.append((f"[log] the recorded values are the ones the simulator held ({total} rows of {len(driven)} driven parameters)",
                       not bad and total > 0, "; ".join(bad[:6]) if bad else ("no row to check" if not total else "")))
        before = sum(r["log"]["before"]["checked"] for r in driven.values())
        if before:
            bad = [f"{n}: at {ex['at']} the log says {ex['logged']:g}, the simulator held {ex['simulator']:g}"
                   for n, r in driven.items() for ex in r["log"]["before"]["examples"]]
            checks.append((f"[log] from the logger's start to the drive every row carries the value the simulator held ({before} rows)",
                           not bad, "; ".join(bad[:6])))
        if not changed_only:
            bad = [f"{n}: {r['log']['unseen']}" for n, r in driven.items() if r["log"]["unseen"]]
            checks.append(("[log] every value the simulator held long enough shows in the log", not bad, "; ".join(bad[:6])))
    recorded = [n for n in per if per[n]["log"]["rows"]]
    nosensor = [n for n in recorded if not per[n]["entity"]]
    checks.append((f"[ha] every recorded parameter has a sensor ({len(recorded) - len(nosensor)} of {len(recorded)})",
                   not nosensor and bool(recorded), ", ".join(nosensor[:8]) if nosensor else ("nothing recorded" if not recorded else "")))
    counts = {"exact": 0, "differs": 0, "missing": 0, "pending": 0}
    bad, raw = [], []
    for name, rec in per.items():
        if not rec["entity"]:
            continue
        u = rec.get("units") or {}
        for h in rec["hours"]:
            if h["status"] in counts:
                counts[h["status"]] += 1
            if h["status"] in ("differs", "missing"):
                more = ""
                if h["status"] == "differs" and rec["converted"] and h.get("ha") and h.get("expected"):
                    more = (f" (the statistics say a mean of {h['ha']['mean']:.5g} {u.get('statistics') or u.get('state')}, "
                            f"the unit recorded {h['logged']['mean']:.5g} {u.get('wican')} = {h['expected']['mean']:.5g})")
                bad.append(f"{name} {h['label']} {h['status']}{more}")
            if h.get("raw_number") and name not in raw:
                raw.append(name)
    done = counts["exact"] + counts["differs"] + counts["missing"]
    checks.append((f"[ha] every finished hour is in the statistics as recorded, in the unit Home Assistant keeps the sensor in "
                   f"({counts['exact']} of {done} parameter-hours exact)",
                   done > 0 and not bad, ("; ".join(bad[:4]) + (f" and {len(bad) - 4} more" if len(bad) > 4 else "")) if bad
                   else ("no finished hour to compare: nothing proven" if not done else "")))
    if raw:
        notes.append(f"{len(raw)} sensor(s) hold hours with the unit's number as it was logged, in a series Home Assistant keeps in "
                     f"another unit ({', '.join(raw[:12])}): the integration until 2.0.0-beta1 imported without converting")
    kept_recs = [(n, k) for n, r in per.items() for k in r.get("kept") or []]
    if kept_recs:
        kbad = [f"{n} {k['label']} {k['status']}" for n, k in kept_recs if k["status"] != "exact"]
        checks.append((f"[ha] the hour of the return was in the statistics while the unit slept, from the rows the pull at the "
                       f"return had read ({len(kept_recs) - len(kbad)} of {len(kept_recs)} parameter-hours)", not kbad,
                       "; ".join(kbad[:6]) + (f" and {len(kbad) - 6} more" if len(kbad) > 6 else "")))
    judged = {n: r for n, r in per.items() if r["live"]["scale"]}
    other = {n: r for n, r in judged.items() if r["live"]["scale"] == "other"}
    if judged:
        def how(n, r):
            ex = (r["live"]["examples"] or [{}])[0]
            u = r.get("units") or {}
            return (f"{n}: the state {ex.get('state', 0):.6g} {u.get('state') or ''} where the log has "
                    f"{ex.get('logged', 0):.6g} {u.get('wican') or ''} = {ex.get('in_ha_unit', 0):.6g}")
        checks.append((f"[ha] a sensor's live states are the values the unit logged, in the unit Home Assistant shows "
                       f"({len(judged) - len(other)} of {len(judged)} sensors)", not other,
                       "; ".join(how(n, r) for n, r in list(other.items())[:6]) + (f" and {len(other) - 6} more" if len(other) > 6 else "")))
    conv_names = [n for n, r in per.items() if r.get("converted")]
    if conv_names:
        notes.append(f"{len(conv_names)} sensor(s) are kept by Home Assistant in another unit than the unit logs "
                     f"({', '.join(conv_names[:14])}): every comparison converts the unit's value first")
    kinds = {}
    for rec in per.values():
        kinds[rec["backfill"]] = kinds.get(rec["backfill"], 0) + 1
    notes.append("parameters by what reached the statistics: " + ", ".join(f"{k}: {v}" for k, v in sorted(kinds.items())))
    if counts["pending"]:
        notes.append(f"{counts['pending']} parameter-hour(s) lie in an hour that had not ended when the run looked: "
                     "the integration imports an hour once it is over")
    if away:
        st_away = sum(r["states"]["numeric_away"] for r in per.values())
        st_home = sum(r["states"]["numeric_home"] for r in per.values())
        five = sum(r["five_min_rows_away"] for r in per.values())
        states_note = (f"Home Assistant's states (what the History panel draws): {st_home} from the time at home, {st_away} from "
                       f"the away time; five-minute statistics rows inside the away time: {five}.")
        if fill_active:
            with_sensor = {n: r for n, r in per.items() if r["entity"] and r["log"]["rows"]}
            changed = {n: r for n, r in with_sensor.items() if r["away"]["changed"]}
            # one state inside the away time is the least a change gives: the
            # value before it is the gap's first point, and the gap begins at
            # the last push before the leave, seconds before the away time
            none = [n for n, r in changed.items() if r["away"]["states"] < 1]
            total = sum(r["away"]["states"] for r in with_sensor.values())
            matched = sum(r["away"]["matched"] for r in with_sensor.values())
            wrong = [f"{n}: {r['away']['states'] - r['away']['matched']} of {r['away']['states']} states are no value the unit "
                     f"logged then (e.g. {json.dumps(r['away']['examples'][:1])})"
                     for n, r in with_sensor.items() if r["away"]["matched"] < r["away"]["states"]]
            detail = "; ".join(([f"no states of the away time for: {', '.join(none[:8])}"] if none else []) + wrong[:4])
            checks.append((f"[ha] the away time shows in History: every parameter whose value changed while away has states of "
                           f"that time ({len(changed) - len(none)} of {len(changed)}), each state a value the unit logged at "
                           f"that moment ({matched} of {total})", bool(changed) and not none and not wrong,
                           detail if detail else ("no parameter changed while away: nothing proven" if not changed else "")))
            notes.append(states_note + " The integration wrote the logged values of the away time as states (its History fill).")
            if hist_steps:
                bad, judged = [], 0
                by_trip = {}
                for name, r in with_sensor.items():
                    for sp in r.get("spacing") or []:
                        if not sp["states"] and not sp["rule"]:
                            continue
                        judged += 1
                        rule = sp["rule"] or 0
                        t = by_trip.setdefault(sp["trip"], {"step": sp["step"], "rows": 0, "states": 0, "rule": 0})
                        t["rows"] += sp["rows"]
                        t["states"] += sp["states"]
                        t["rule"] += rule
                        why = []
                        if sp["on_a_row"] < sp["states"]:
                            why.append(f"{sp['states'] - sp['on_a_row']} of {sp['states']} states on no logged row's stamp")
                        if sp["too_soon"]:
                            why.append(f"{sp['too_soon']} sooner than {sp['step']:g} s after the one before")
                        if abs(sp["states"] - rule) > max(2, 0.02 * rule):
                            why.append(f"{sp['states']} states where the spacing gives {rule}")
                        if why:
                            bad.append(f"{name}, trip {sp['trip']}: " + ", ".join(why))
                label = "; ".join(f"trip {k}: {t['step']:g} s{' = every change' if t['step'] == 0 else ''}, {t['rows']} logged rows, "
                                  f"{t['states']} states, the spacing gives {t['rule']}" for k, t in sorted(by_trip.items(), key=lambda kv: str(kv[0])))
                checks.append(("[ha] History detail as set: the away time's states are the ones the spacing gives, each on a logged "
                               f"row's own stamp, none sooner than the spacing after the one before ({label})",
                               judged > 0 and not bad,
                               "; ".join(bad[:4]) if bad else ("no sensor has states inside the away time: nothing proven" if not judged else "")))
                told = sorted({(s.get("states") or {}).get("step") for s in run.get("syncs") or []
                               if (s.get("states") or {}).get("states_written")} - {None})
                widened = [s["states"] for s in run.get("syncs") or []
                           if (s.get("states") or {}).get("step_used") not in (None, (s.get("states") or {}).get("step"))]
                notes.append(f"History detail: the catch-up runs that wrote states report a spacing of {', '.join(f'{v:g} s' for v in told) or '-'}"
                             + (f"; {len(widened)} run(s) widened it for their cap: "
                                + ", ".join(f"{w.get('step'):g} to {w.get('step_used'):g} s ({w.get('states_at_step')} points asked)" for w in widened)
                                if widened else "; no run was over its cap"))
        else:
            notes.append(states_note + " This integration imports hourly statistics only, so the History panel keeps a gap "
                         "or a flat line across a drive whatever was imported")
    report = {"logdir": logdir, "device": run.get("device"), "ha": run.get("ha"), "profile": run.get("profile"),
              "window": {"from": x0, "to": x1, "validated_at": t_validate},
              "phases": phases, "events": run.get("events"), "syncs": run.get("syncs"), "judge": judge,
              "table": run.get("table"), "push": run.get("push"), "settle": run.get("settle"),
              "hour_counts": counts, "parameters": per,
              "checks": [list(c) for c in checks], "notes": notes}
    with open(os.path.join(logdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    with open(os.path.join(logdir, "report.md"), "w", encoding="utf-8") as f:
        f.write(render_md(report, off_pi - off_dut))
    with open(os.path.join(logdir, "report.html"), "w", encoding="utf-8") as f:
        f.write(render_html(report, charts, x0, x1, off_pi - off_dut))
    report["checks"] = checks
    return report


# ---- the written report --------------------------------------------------------------

def _summary_rows(report):
    out = []
    for name, rec in report["parameters"].items():
        lg = rec["log"]
        home = sum(p["rows"] for p in lg["phases"] if p["kind"] == "home")
        away = sum(p["rows"] for p in lg["phases"] if p["kind"] == "away")
        cov = [p["coverage_pct"] for p in lg["phases"] if p["kind"] == "away" and p["coverage_pct"] is not None]
        holes = [g for g in lg["gaps"] if g["phase"] == "away"]
        hours = rec["hours"]
        n = lambda s: sum(1 for h in hours if h["status"] == s)  # noqa: E731
        out.append({
            "name": name, "driven": "yes" if rec["driven"] else "-", "sensor": rec["entity"] or "NONE",
            "home": home, "away": away, "coverage": f"{min(cov):.0f} %" if cov else "-",
            "holes": f"{len(holes)} (max {max(g['seconds'] for g in holes):.0f} s)" if holes else "0",
            "wrong": f"{lg['mismatches']} of {lg['checked']}" if rec["driven"] else "-",
            "unseen": str(lg["unseen"]) if rec["driven"] else "-",
            "exact": n("exact"), "differs": n("differs"), "missing": n("missing"), "pending": n("pending"),
            "states_away": f"{rec['away']['states']} ({rec['away']['matched']})" if rec.get("away") else rec["states"]["numeric_away"],
            "backfill": rec["backfill"],
            "live": "-" if not rec["live"]["checked"] else f"{rec['live']['off']} of {rec['live']['checked']}",
            "units": "-" if not rec.get("units") else
                     f"{rec['units'].get('wican') or '-'} / {rec['units'].get('statistics') or rec['units'].get('state') or '-'}",
        })
    return out


COLUMNS = [("name", "Parameter"), ("driven", "Driven"), ("home", "Rows at home"), ("away", "Rows away"),
           ("coverage", "Away coverage"), ("holes", "Holes away"), ("wrong", "Wrong values"), ("unseen", "Values never logged"),
           ("exact", "Hours exact"), ("differs", "Hours differ"), ("missing", "Hours missing"), ("pending", "Hours pending"),
           ("states_away", "States from away (of them a logged value)"), ("backfill", "Reached the statistics"),
           ("live", "Live states that are not the logged value"), ("units", "Unit: WiCAN / Home Assistant")]


def _sync_rows(report, ha_minus_dut):
    out = []
    for s in report.get("syncs") or []:
        fin = s.get("finished_at")
        out.append({"at": hhmm(fin - ha_minus_dut, True) if fin else "-", "ran": s.get("ran"), "pages": s.get("pages"),
                    "fetched": s.get("rows_fetched"), "used": s.get("rows_used"), "deferred": s.get("rows_deferred"),
                    "rejected": s.get("rows_rejected"), "hours": s.get("hours_imported"), "pids": s.get("pids_imported"),
                    "kept": s.get("hours_from_kept_copy", "-"),
                    "states": (s.get("states") or {}).get("states_written", "-"),
                    "skipped": s.get("pids_skipped"),
                    "followup": f"{s['followup_in']:.0f} s" if s.get("followup_in") else "-",
                    "errors": "; ".join(s.get("errors") or []) or "-"})
    return out


SYNC_COLUMNS = [("at", "Finished (UTC)"), ("ran", "Ran"), ("pages", "Pages"), ("fetched", "Rows read"), ("used", "Rows used"),
                ("deferred", "Rows deferred"), ("rejected", "Rows rejected"), ("hours", "Hours imported"),
                ("kept", "Of them from the kept copy"), ("pids", "Sensors imported"), ("states", "States written"),
                ("skipped", "Names without a sensor"), ("followup", "Follow-up in"), ("errors", "Errors")]


def render_md(report, ha_minus_dut):
    dev, ha = report.get("device") or {}, report.get("ha") or {}
    w = report["window"]
    lines = [f"# Drive report {time.strftime('%Y-%m-%d', time.gmtime(w['from']))}", "",
             f"Unit `{dev.get('device_id')}` on `{dev.get('fw_build')}`; Home Assistant {ha.get('version')} "
             f"({ha.get('install')}), integration {ha.get('integration')}. Profile: {report.get('profile')}.",
             f"Run {hhmm(w['from'], True)} to {hhmm(w['to'], True)} UTC, looked at {hhmm(w['validated_at'], True)}. "
             "Every time is UTC on the unit's clock.", "", "## Verdict", ""]
    for name, ok, detail in report["checks"]:
        lines.append(f"- {'PASS' if ok else 'FAIL'}: {name}" + (f" ({detail})" if detail else ""))
    lines += [""] + [f"- NOTE: {n}" for n in report["notes"]] + ["", "## Phases", "", "| Phase | Trip | From | To | Minutes |", "|---|---|---|---|---|"]
    for ph in report["phases"]:
        lines.append(f"| {ph['kind']} | {ph.get('trip') or '-'} | {hhmm(ph['t0'], True)} | {hhmm(ph['t1'], True)} | {(ph['t1'] - ph['t0']) / 60:.1f} |")
    syncs = _sync_rows(report, ha_minus_dut)
    lines += ["", "## The integration's catch-up runs", ""]
    if syncs:
        lines += ["| " + " | ".join(t for _, t in SYNC_COLUMNS) + " |", "|" + "---|" * len(SYNC_COLUMNS)]
        lines += ["| " + " | ".join(str(s[k]) for k, _ in SYNC_COLUMNS) + " |" for s in syncs]
    else:
        lines.append("None seen.")
    lines += ["", "## Per parameter", "", "| " + " | ".join(t for _, t in COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join(str(r[k]) for k, _ in COLUMNS) + " |" for r in _summary_rows(report)]
    lines += ["", "## Hours that differ or are missing in Home Assistant", ""]
    any_hour = False
    for name, rec in report["parameters"].items():
        odd = [h for h in rec["hours"] if h["status"] in ("differs", "missing")]
        if not odd:
            continue
        any_hour = True
        lines.append(f"**{name}** (`{rec['entity']}`)")
        u = rec.get("units") or {}
        kept_unit = u.get("statistics") or u.get("state") or ""
        for h in odd:
            e, a = h["expected"], h["ha"]
            want = (f"mean {e['mean']:.4f}, min {e['min']:.6g}, max {e['max']:.6g}" + (f" {kept_unit}" if rec.get("converted") else "")
                    if e else f"values in {u.get('wican')} this judge cannot convert into {kept_unit}")
            lines.append(f"- {h['label']} {h['status']}: the unit's {h['rows']} rows give {want}; "
                         + (f"Home Assistant holds mean {a['mean']}, min {a['min']}, max {a['max']}" if a else "Home Assistant holds no row")
                         + (" (the logged number, not converted)" if h.get("raw_number") else ""))
        lines.append("")
    if not any_hour:
        lines += ["None.", ""]
    lines += ["## Holes and wrong values in the unit's log", ""]
    any_ = False
    for name, rec in report["parameters"].items():
        lg = rec["log"]
        if not (lg["gaps"] or lg["mismatch_examples"] or lg["unseen_examples"] or lg["before"]["examples"]):
            continue
        any_ = True
        lines.append(f"**{name}** (a row every {lg['period_s']} s)")
        for g in lg["gaps"][:12]:
            lines.append(f"- no row from {hhmm(g['from'], True)} to {hhmm(g['to'], True)} ({g['seconds']:.0f} s, {g['phase']})")
        for m in lg["before"]["examples"]:
            lines.append(f"- before the drive, at {m['at']}, the log says {m['logged']:g}: the simulator held {m['simulator']:g}")
        for m in lg["mismatch_examples"]:
            lines.append(f"- at {m['at']} the log says {m['logged']}, the simulator held {m['simulator']}")
        for u in lg["unseen_examples"]:
            lines.append(f"- the simulator held {u['value']} from {u['from']} to {u['to']}: never in the log")
        lines.append("")
    if not any_:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _table(columns, rows):
    head = "".join(f"<th>{html.escape(t)}</th>" for _, t in columns)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(r[k]))}</td>" for k, _ in columns) + "</tr>" for r in rows)
    return f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def render_html(report, charts, x0, x1, ha_minus_dut):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "drive_report.html"), encoding="utf-8") as f:
        page = f.read()
    dev, ha = report.get("device") or {}, report.get("ha") or {}
    w = report["window"]
    head = (f"Unit {html.escape(str(dev.get('device_id')))} on {html.escape(str(dev.get('fw_build')))}; Home Assistant "
            f"{html.escape(str(ha.get('version')))} ({html.escape(str(ha.get('install')))}), integration "
            f"{html.escape(str(ha.get('integration')))}. Run {hhmm(w['from'], True)} to {hhmm(w['to'], True)} UTC, looked at "
            f"{hhmm(w['validated_at'], True)}. Times are UTC on the unit's clock.")
    verdict = "".join(
        f"<li class='{'pass' if ok else 'fail'}'><b>{'PASS' if ok else 'FAIL'}</b> {html.escape(name)}"
        + (f" <span class='muted'>({html.escape(str(detail))})</span>" if detail else "") + "</li>"
        for name, ok, detail in report["checks"])
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in report["notes"])
    phases = [{"kind": ph["kind"], "t0": round(ph["t0"], 1), "t1": round(ph["t1"], 1)} for ph in report["phases"]]
    data = {"x0": round(x0 - 60, 1), "x1": round(x1 + 60, 1), "phases": phases, "charts": charts,
            "validated": round(w["validated_at"], 1)}
    return (page.replace("__TITLE__", f"Drive report {time.strftime('%Y-%m-%d %H:%M', time.gmtime(w['from']))}Z")
            .replace("__HEAD__", head).replace("__VERDICT__", verdict).replace("__NOTES__", notes)
            .replace("__SUMMARY__", _table(COLUMNS, _summary_rows(report)))
            .replace("__SYNCS__", _table(SYNC_COLUMNS, _sync_rows(report, ha_minus_dut)) if report.get("syncs") else "<p>None seen.</p>")
            .replace("__DATA__", json.dumps(data).replace("</", "<\\/")))


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    rep = analyze(sys.argv[1])
    fails = 0
    for name, ok, detail in rep["checks"]:
        print(("PASS" if ok else "FAIL") + ": " + name + (f"  ({detail})" if detail else ""))
        fails += not ok
    for n in rep["notes"]:
        print("  NOTE: " + n)
    print(f"report: {os.path.join(sys.argv[1], 'report.html')}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
