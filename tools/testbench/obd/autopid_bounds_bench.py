#!/usr/bin/env python3
"""AutoPID bounds bench  ->  AUTOPID BOUNDS PASS / FAIL   (issue #1004)

The report (2026-10-20, a VW T6.1 on v6.00p alfa-04): with the tank full
the ECU answers `41 2F FF`, the standard FuelTankLevel row decodes it as
B2*0.3921568692 = 100.000001646, a hair above the row's max of 100, and the
poller drops it: the dashboard goes stale, MQTT keeps the old value, the
Test button shows 100.000001646. The legacy firmware clamped a standard
PID to the table's bounds; v6 dropped every value outside min..max.

The bench plays it on the ECU simulator (its fuel level set to 100 %, raw
0xFF) with four rows:
  FuelTankLevel   std    012F  B2*0.3921568692  0..100  the alfa-04 row as
                                                         stored: must read 100
  CalcEngineLoad  std    0104  B2*0.3921568692  0..100  a control (25.1 %)
  FuelCustomNoise custom 012F  B2*0.3921568692  0..100  the same float noise
                                                         on a custom row: 100
  FuelWrongScale  custom 012F  B2*0.4           0..100  102: outside the
                                                         band, stays dropped
Then the generator: `GET /api/autopid/std_table` must hand out the fuel
row as `B2*100/255` (0xFF decodes to exactly 100, no float noise) and the
test route must decode it so.

Legs: L0 preflight (DUT, simulator, saves), L1 stage (the table, autopid
on with a submit-reboot when it was off, the simulator at 100 %), L2 the
poll (values after --watch seconds), L3 the generator, L4 restore (the
simulator's level, the tables and the settings).

usage: autopid_bounds_bench.py [--dut localhost:18081] [--watch 20] [--out DIR]
  Runs on the PC (the simulator at 192.168.8.1 through sim_net); the DUT
  through an ssh tunnel (`ssh -N -L 18081:10.42.1.194:80 rpi001`) or by
  address.
Needs: the main firmware on the DUT on the bench AP, the ECU simulator
answering 7DF (its car in the vehicle store), the PSU above the sleep
voltage. ~1.5 min, up to two planned restarts.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import sim_net  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SIM = "http://192.168.8.1"
NOISE = "B2*0.3921568692"       # the table's float scale as alfa-04 printed it


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


def sim_level():
    st, d = get_json(SIM, "/api/ecu/pids")
    for e in (d or {}).get("standard", []):
        if str(e.get("pid", "")).upper() == "2F":
            for p in e.get("params", []):
                if p.get("name") == "level":
                    return p.get("value")
    return None


def sim_set_level(v):
    st, body = http(SIM, "POST", "/api/ecu/pids/01/2F", {"level": v})
    return st == 200 and '"ok":true' in body.replace(" ", "")


def table():
    def row(name, typ, cmd, expr):
        return {"name": name, "type": typ, "cmd": cmd, "group": "bounds",
                "parameters": [{"name": name, "expression": expr, "unit": "%",
                                "min": 0, "max": 100}]}
    return {"groups": [{"name": "bounds", "enabled": True, "period_ms": 1000}],
            "pids": [row("FuelTankLevel", "std", "012F", NOISE),
                     row("CalcEngineLoad", "std", "0104", NOISE),
                     row("FuelCustomNoise", "custom", "012F", NOISE),
                     row("FuelWrongScale", "custom", "012F", "B2*0.4")],
            "filters": []}


def submit_and_wait(base, window=150):
    st, body = http(base, "POST", "/api/settings/submit", {})
    print("  submit -> %s %s" % (st, body.strip()[:80]))
    if not (st == 200 and '"reboot":true' in body.replace(" ", "")):
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
            time.sleep(8)
            return True
    print("  the DUT did not come back within %d s" % window)
    return False


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print("  [%s] %s%s" % ("ok" if ok else "FAIL", name,
                               (": " + detail) if detail else ""), flush=True)
        return bool(ok)

    def failed(self):
        return [n for n, ok, _ in self.rows if not ok]


def values(base):
    st, ap = get_json(base, "/api/autopid")
    out = {}
    for p in (ap or {}).get("params", []):
        out[p.get("name")] = p.get("value")
    return out, (ap or {}).get("stats", {})


def main():
    args = sys.argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    dut = opt("--dut", "localhost:18081")
    base = "http://" + dut.rstrip("/")
    watch = float(opt("--watch", 20))
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = opt("--out", os.path.join(REPO, "test-reports", "logs",
                                    "autopid_bounds_" + ts))
    os.makedirs(out, exist_ok=True)
    print("AUTOPID BOUNDS bench: dut=%s watch=%.0f s out=%s" % (dut, watch, out),
          flush=True)
    c = Checks()
    saved_cfg = saved_settings = None
    settings_changed = False
    level_before = None

    try:
        print("L0 preflight")
        st, info = get_json(base, "/api/info")
        if not c.add("dut answers", st == 200 and info,
                     "%s %s" % (st, (info or {}).get("fw_build"))):
            raise SystemExit(1)
        sim_net.install(SIM.split("//")[1])
        level_before = sim_level()
        c.add("the simulator answers and has a fuel level",
              level_before is not None, "level %s %%" % level_before)
        st, saved_cfg = get_json(base, "/api/autopid/config")
        c.add("tables saved", st == 200 and saved_cfg is not None)
        st, saved_settings = get_json(base, "/api/settings/autopid")
        c.add("settings saved", st == 200 and saved_settings is not None)
        with open(os.path.join(out, "saved_config.json"), "w") as f:
            json.dump(saved_cfg, f)
        with open(os.path.join(out, "saved_settings.json"), "w") as f:
            json.dump(saved_settings, f)
        if c.failed():
            raise SystemExit(1)

        print("L1 stage")
        c.add("the simulator's tank is full (raw FF)", sim_set_level(100) and
              sim_level() == 100)
        st, body = http(base, "PUT", "/api/autopid/config", table())
        c.add("table accepted", st == 200, "%s %s" % (st, body.strip()[:100]))
        new_settings = dict(saved_settings)
        for k in ("degraded", "pending_reboot"):
            new_settings.pop(k, None)
        want = {"enabled": True, "std_enabled": True, "custom_enabled": True}
        if any(new_settings.get(k) != v for k, v in want.items()):
            new_settings.update(want)
            st, body = http(base, "PUT", "/api/settings/autopid", new_settings)
            c.add("autopid switched on (settings PUT)", st == 200,
                  "%s %s" % (st, body.strip()[:100]))
            settings_changed = True
            c.add("the DUT rebooted and is back", submit_and_wait(base))
        if c.failed():
            raise SystemExit(1)

        print("L2 the poll: %.0f s" % watch)
        t_end = time.time() + watch
        vals, stats0 = values(base)
        samples = open(os.path.join(out, "samples.jsonl"), "w")
        while time.time() < t_end:
            time.sleep(1.0)
            vals, stats = values(base)
            samples.write(json.dumps({"t": time.time(), "values": vals,
                                      "stats": stats}) + "\n")
        samples.close()
        print("  values: " + json.dumps(vals))
        c.add("the control row reads the simulator's load (25.1 %)",
              vals.get("CalcEngineLoad") is not None and
              abs(vals["CalcEngineLoad"] - 25.098) < 0.05,
              "CalcEngineLoad %s" % vals.get("CalcEngineLoad"))
        c.add("the standard fuel row reads a full tank as exactly 100",
              vals.get("FuelTankLevel") == 100,
              "FuelTankLevel %s (the report: null, Test 100.000001646)"
              % vals.get("FuelTankLevel"))
        c.add("a custom row with the same float noise reads 100 too",
              vals.get("FuelCustomNoise") == 100,
              "FuelCustomNoise %s" % vals.get("FuelCustomNoise"))
        c.add("a custom row decoding 102 against a max of 100 stays dropped",
              vals.get("FuelWrongScale") is None,
              "FuelWrongScale %s" % vals.get("FuelWrongScale"))
        c.add("the poller is running", stats.get("running") is True and
              stats.get("polls_ok", 0) > stats0.get("polls_ok", 0),
              "polls_ok %s -> %s, failed %s -> %s" % (
                  stats0.get("polls_ok"), stats.get("polls_ok"),
                  stats0.get("polls_failed"), stats.get("polls_failed")))

        print("L3 the generator")
        st, tab = get_json(base, "/api/autopid/std_table")
        fuel = None
        for e in (tab or {}).get("entries", (tab or {}).get("pids", [])) \
                if isinstance(tab, dict) else (tab or []):
            if str(e.get("cmd", "")).upper() == "012F":
                fuel = e
        prm = (fuel or {}).get("parameters", [{}])[0]
        with open(os.path.join(out, "std_table_012F.json"), "w") as f:
            json.dump(fuel, f)
        c.add("the table hands out the fuel row as B2*100/255 with max 100",
              prm.get("expression") == "B2*100/255" and prm.get("max") == 100,
              "expression %r max %r" % (prm.get("expression"), prm.get("max")))
        st, body = http(base, "POST", "/api/autopid/test",
                        {"cmd": "012F", "type": "std",
                         "expressions": [prm.get("expression") or "B2*100/255",
                                         NOISE]}, timeout=40)
        with open(os.path.join(out, "test_012F.json"), "w") as f:
            f.write(body)
        try:
            t = json.loads(body)
        except ValueError:
            t = {}
        v = t.get("values") or []
        c.add("test-a-PID decodes the full tank as exactly 100 with the "
              "generated expression", st == 200 and t.get("ok") and
              str(t.get("raw", "")).replace(" ", "") == "412FFF" and
              len(v) >= 1 and v[0] == 100,
              "%s raw=%r values=%s" % (st, t.get("raw"), v))
    finally:
        print("L4 restore")
        if level_before is not None:
            c.add("the simulator's fuel level restored",
                  sim_set_level(level_before) and sim_level() == level_before,
                  "level %s %%" % level_before)
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

    failed = c.failed()
    print("checks: %d, failed: %d" % (len(c.rows), len(failed)))
    if failed:
        print("AUTOPID BOUNDS FAIL: " + "; ".join(failed))
        return 1
    print("AUTOPID BOUNDS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
