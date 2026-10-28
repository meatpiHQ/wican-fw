#!/usr/bin/env python3
"""AutoPID init-leak bench  ->  AUTOPID INIT LEAK PASS / FAIL   (issue #964)

The report (2026-09-29, a 2018 BMW 640i GT): nine Mode 01 rows and one
custom UDS odometer row whose init moves the chip to the gateway
(`ATSH6F1;ATCEA60;ATCRA660;ATFCSH6F1;ATFCSD60300000;ATFCSM1`). Those
settings stayed in effect, every Mode 01 request that followed went to the
gateway, which answered `7F 01 11`, and the values froze (RPM 68.25 =
0x0111 / 4, speed 1, coolant -39 on the legacy firmware; on v6 the rows
fail and the values stop moving). The bench plays that vehicle: the ECU
simulator is the OBD-II car on 7DF/7E8, `actors/pcan_ext_addr_ecu.py` on
the PCAN is the extended-addressed ECU (0x6F1 + address 0x60, answers on
0x660: `62 D1 07` for the odometer, `7F 01 11` for service 01).

Legs:
  L0 preflight: the DUT answers and is not yielding the chip to an app or
     a diagnostic tool; the tables and the settings are saved; the actor
     opens the PCAN.
  L1 stage: the report's table is PUT (nine standard rows the simulator
     supports + the ODOMETER custom row, one group every --period-ms);
     autopid enabled with a submit-reboot when it was off.
  L2 the poll: /api/autopid watched for --cycles periods after the first
     odometer read. Claims: every standard value keeps being read after the
     custom row ran (its ts_us moves on every cycle), the odometer reads
     123456, polls_failed does not grow, and the actor saw NO service-01
     request on 0x6F1 while 0x7DF carried them.
  L3 the one-shot: the custom row through POST /api/autopid/test, then a
     standard row the same way: its transcript must carry the restoring
     prelude (ATSH7DF, ATCRA, ATCEA, ATFCSM0) and a `41 0C` answer.
  L4 restore: the saved tables and settings (a submit-reboot when the
     settings had to change).

usage: autopid_init_leak_bench.py [--dut localhost:18081] [--pcan PCAN_USBBUS2]
                                  [--cycles 4] [--period-ms 5000] [--out DIR]
  Runs on the PC (python-can + PCAN); the DUT through an ssh tunnel
  (`ssh -N -L 18081:10.42.1.194:80 rpi001`) or by address.
Needs: the main firmware on the DUT on the bench AP, the ECU simulator
answering 7DF (its car in the vehicle store), PCAN_USBBUS2 on the DUT bus,
the bench PSU above the sleep voltage (pause_follow_sleep). ~2 min, up to
two planned restarts.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ACTOR = os.path.join(HERE, "..", "actors", "pcan_ext_addr_ecu.py")
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

ODO_INIT = "ATSH6F1;ATCEA60;ATCRA660;ATFCSH6F1;ATFCSD60300000;ATFCSM1"
ODO_KM = 123456

# the report's nine Mode 01 rows, on PIDs the bench simulator answers
# expressions in the frame of reference of 2026-10-24 (the reply as the
# chip prints it with headers on: `03 41 05 5A` reads B3 for the data byte;
# the extended-addressed reply `F1 06 62 D1 07 01 E2 40` its odometer at
# [B5:B7]); the bench had last run on 2026-10-20 with B2 and [B3:B5]
STD_ROWS = [
    ("EngineCoolantTemp", "0105", "B3-40", "degC"),
    ("EngineRPM", "010C", "[B3:B4]*0.25", "rpm"),
    ("VehicleSpeed", "010D", "B3", "km/h"),
    ("IntakeAirTemperature", "010F", "B3-40", "degC"),
    ("MAFAirFlowRate", "0110", "[B3:B4]*0.01", "grams/sec"),
    ("ThrottlePosition", "0111", "B3*0.3921568692", "%"),
    ("FuelTankLevel", "012F", "B3*0.3921568692", "%"),
    ("CalcEngineLoad", "0104", "B3*0.3921568692", "%"),
    ("ControlModuleVolt", "0142", "[B3:B4]*0.001", "V"),
]


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


def table(period_ms):
    pids = []
    for name, cmd, expr, unit in STD_ROWS:
        pids.append({"name": name, "type": "std", "cmd": cmd,
                     "group": "report",
                     "parameters": [{"name": name, "expression": expr,
                                     "unit": unit}]})
    pids.append({"name": "ODOMETER", "type": "custom", "cmd": "22D107",
                 "init": ODO_INIT, "group": "report",
                 "parameters": [{"name": "ODOMETER",
                                 "expression": "[B5:B7]", "unit": "km"}]})
    return {"groups": [{"name": "report", "enabled": True,
                        "period_ms": period_ms}],
            "pids": pids, "filters": []}


def submit_and_wait(base, window=150):
    """POST /api/settings/submit, then wait for the DUT to go away and
    come back (the device reboots when a PUT changed something)."""
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
            time.sleep(8)       # the chip provisions ~15 s after a boot
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


def main():
    args = sys.argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    dut = opt("--dut", "localhost:18081")
    base = "http://" + dut.rstrip("/")
    pcan = opt("--pcan", "PCAN_USBBUS2")
    cycles = int(opt("--cycles", 4))
    period_ms = int(opt("--period-ms", 5000))
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = opt("--out", os.path.join(REPO, "test-reports", "logs",
                                    "autopid_init_leak_" + ts))
    os.makedirs(out, exist_ok=True)
    print("AUTOPID INIT LEAK bench: dut=%s pcan=%s cycles=%d period=%d ms "
          "out=%s" % (dut, pcan, cycles, period_ms, out), flush=True)
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
                     "%s %s" % (st, (info or {}).get("fw_build"))):
            raise SystemExit(1)
        st, ap = get_json(base, "/api/autopid")
        stats = (ap or {}).get("stats", {})
        c.add("no app or diagnostic hold on the chip",
              not stats.get("paused_client") and not stats.get("paused_diag"),
              json.dumps({k: stats.get(k) for k in
                          ("paused_client", "paused_diag", "paused_voltage",
                           "paused_bus")}))
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

        actor_log = open(os.path.join(out, "actor.log"), "w")
        actor = subprocess.Popen(
            [sys.executable, "-u", ACTOR, str(cycles * period_ms / 1000 + 400),
             "--pcan", pcan, "--odometer", str(ODO_KM),
             "--summary", summary_path],
            stdout=actor_log, stderr=subprocess.STDOUT)
        time.sleep(2)
        c.add("the extended-addressed ECU is on the PCAN",
              actor.poll() is None, "actor pid %d" % actor.pid)
        if c.failed():
            raise SystemExit(1)

        # ---- L1 stage ----------------------------------------------------
        print("L1 stage: the report's table")
        st, body = http(base, "PUT", "/api/autopid/config", table(period_ms))
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

        # ---- L2 the poll -------------------------------------------------
        print("L2 the poll: %d cycles of %d ms" % (cycles, period_ms))
        samples = open(os.path.join(out, "samples.jsonl"), "w")
        names = [r[0] for r in STD_ROWS]
        last_ts = {}
        advances = {n: 0 for n in names + ["ODOMETER"]}
        values = {}
        first_odo_at = None
        stats0 = None
        statsN = None
        deadline = time.time() + 90      # wait for the first odometer read
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
                        continue
                    t_us = p.get("ts_us")
                    if t_us is not None and t_us != last_ts.get(n):
                        if n in last_ts:
                            advances[n] += 1
                        last_ts[n] = t_us
                        values[n] = p.get("value")
                if first_odo_at is None and "ODOMETER" in last_ts:
                    first_odo_at = now
                    stats0 = dict(stats)
                    # count from here: the next cycles must keep the
                    # standard rows alive
                    for n in advances:
                        advances[n] = 0
                    t_end = now + cycles * period_ms / 1000.0 + 2.0
                    print("  the custom row was read (odometer %s); watching "
                          "%d more cycles" % (values.get("ODOMETER"), cycles),
                          flush=True)
                if t_end is not None and now >= t_end:
                    statsN = dict(stats)
                    break
            if first_odo_at is None and now > deadline:
                break
            time.sleep(1.0)
        samples.close()
        c.add("the custom row was read at least once", first_odo_at is not None,
              "polls_ok %s failed %s" % (
                  (ap or {}).get("stats", {}).get("polls_ok"),
                  (ap or {}).get("stats", {}).get("polls_failed")))
        if first_odo_at is not None:
            frozen = [n for n in names if advances[n] < cycles - 1]
            c.add("every standard row keeps being read after the custom row "
                  "(%d of %d cycles each)" % (cycles - 1, cycles),
                  not frozen,
                  "advances " + json.dumps({n: advances[n] for n in names}))
            c.add("the odometer reads %d" % ODO_KM,
                  values.get("ODOMETER") == ODO_KM,
                  "value %s" % values.get("ODOMETER"))
            c.add("the standard values are the simulator's (RPM 800, coolant "
                  "90, speed 0)",
                  values.get("EngineRPM") == 800 and
                  values.get("EngineCoolantTemp") == 90 and
                  values.get("VehicleSpeed") == 0,
                  json.dumps({n: values.get(n) for n in names}))
            d_fail = statsN.get("polls_failed", 0) - stats0.get("polls_failed", 0)
            d_ok = statsN.get("polls_ok", 0) - stats0.get("polls_ok", 0)
            c.add("no poll fails while the two row types alternate",
                  d_fail == 0, "polls_failed +%d, polls_ok +%d" % (d_fail, d_ok))
        time.sleep(1.5)
        summ = {}
        try:
            with open(summary_path) as f:
                summ = json.load(f)
        except (OSError, ValueError):
            pass
        c.add("the extended-addressed ECU answered the odometer requests",
              summ.get("did_at_ecu", 0) >= 1,
              "did_at_ecu %s" % summ.get("did_at_ecu"))
        c.add("NO service-01 request reached the extended-addressed ECU",
              summ.get("svc01_at_ecu", 0) == 0,
              "svc01_at_ecu %s (the leak), svc01 on 7DF %s" % (
                  summ.get("svc01_at_ecu"), summ.get("svc01_7df")))
        c.add("the standard rows went out functionally on 7DF",
              summ.get("svc01_7df", 0) >= len(STD_ROWS) * max(1, cycles - 1),
              "svc01_7df %s" % summ.get("svc01_7df"))

        # ---- L3 the one-shot ---------------------------------------------
        print("L3 the one-shot: test-a-PID custom, then standard")
        st, body = http(base, "POST", "/api/autopid/test",
                        {"cmd": "22D107", "type": "custom", "init": ODO_INIT,
                         "expression": "[B5:B7]"}, timeout=40)
        with open(os.path.join(out, "test_custom.json"), "w") as f:
            f.write(body)
        try:
            t1 = json.loads(body)
        except ValueError:
            t1 = {}
        c.add("test-a-PID of the custom row answers the odometer",
              st == 200 and t1.get("ok") and t1.get("value") == ODO_KM,
              "%s raw=%r value=%s" % (st, t1.get("raw"), t1.get("value")))
        time.sleep(1.0)
        st, body = http(base, "POST", "/api/autopid/test",
                        {"cmd": "010C", "type": "std",
                         "expression": "[B3:B4]*0.25"}, timeout=40)
        with open(os.path.join(out, "test_std.json"), "w") as f:
            f.write(body)
        try:
            t2 = json.loads(body)
        except ValueError:
            t2 = {}
        tr = t2.get("transcript", "")
        print("  standard row transcript:\n    " +
              tr.replace("\n", "\n    ").rstrip())
        c.add("the standard row's transcript restores the baseline first",
              all(x in tr for x in ("ATSH7DF", "ATCRA", "ATCEA", "ATFCSM0")),
              "looked for ATSH7DF, ATCRA, ATCEA, ATFCSM0")
        c.add("the standard row answers 41 0C (RPM 800) after the custom one",
              st == 200 and t2.get("ok") and
              "410C" in str(t2.get("raw", "")).replace(" ", "") and
              t2.get("value") == 800,
              "%s raw=%r value=%s" % (st, t2.get("raw"), t2.get("value")))
    finally:
        # ---- L4 restore --------------------------------------------------
        print("L4 restore")
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

    failed = c.failed()
    print("checks: %d, failed: %d" % (len(c.rows), len(failed)))
    if failed:
        print("AUTOPID INIT LEAK FAIL: " + "; ".join(failed))
        return 1
    print("AUTOPID INIT LEAK PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
