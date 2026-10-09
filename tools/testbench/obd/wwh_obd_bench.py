#!/usr/bin/env python3
"""WWH-OBD bench (TASK_j1939_wwh.md, phase 3): a vehicle that speaks OBD
over UDS (ISO 27145 / SAE J1979-2) through the OBD chip.

The claim under test: such a vehicle is detected, polled, its trouble codes
read and cleared with no step an OBD-II car would not need, and the device
tells the two kinds apart by itself when it is moved between them.

The vehicle is the PCAN actor (actors/pcan_wwh_ecu.py, the independent
reference): it logs every request it sees, so the judge is the wire as much
as the DUT's API. The ECU simulator stays on the bus as the ACK source with
its own ECU off (it would answer 0100), and is the OBD-II car of the last
leg. Every leg judges observed state: the vehicle store, the tables, the
values against what the actor was told to send, the DTC report, and the
requests the actor saw (to which ECU, functional or physical).

Legs:
  w1  a new UDS-dialect vehicle (29-bit, 500k, two ECUs) is found by first
      contact with no user action: protocol 7, dialect uds, VIN, both ECUs
      with their bitmaps, one row per supported PID addressed to its owner
  w2  polling: every value equals what the actor sends; each row is asked
      physically of the ECU that owns it, nobody else is asked
  w3  a custom row with no init of its own, between the standard rows, is
      sent with the FUNCTIONAL header (the standard rows give it back)
  w4  DTC scan: codes of both ECUs with status and severity, the lamp per
      ECU, permanent codes, `7F xx 78` answers ridden out; the clear is
      refused while `dtc_allow_clear` is off and nothing is sent
  w5  the clear: `14 FF FF 33` to every ECU, codes gone, the lamp off, the
      permanent code still there; a known vehicle is back after a restart
      (METRIC: seconds from boot to its first value)
  w6  the same vehicle reporting in the SAE J1939 DTC format (SPN + FMI)
  w7  an 11-bit vehicle with one ECU and no F810 (the SAE J1979-2 shape):
      protocol 6, dialect uds, rows addressed to 7E0
  w8  the Sprinter VS30 of the field report: three ECUs with its bitmaps,
      the engine speed from 58; ECU 5A turns up later with an unscaled
      copy and is never asked; no row for a PID that decodes nothing
  w9  a 250k bus that is LIVE at boot: the bus guard names the bitrate, the
      vehicle is found on protocol 9, and the adapter sees no bus error
  w10 back on the OBD-II simulator with no user action; then the same car
      on 29-bit ids: its ECU is told apart and a DTC scan reads it
  restore: the bench's vehicles forgotten, the store, the tables, every
  setting and the simulator as found.
  across the run (read after every job, before every restart, after every
  leg): no E line of the feature's tags; the stack headroom of the detection
  job, the vehicle writer, the DTC job and the poller, and the internal
  heap's floor (METRIC, judged against the stack audit's floors).

Verdict: `WWH OBD PASS`. Run on the PC (IDF venv python; python-can +
can-isotp):
  python wwh_obd_bench.py [host[:port]] [--pcan PCAN_USBBUS2]
                          [--only w1,w2,...] [--logdir DIR]
`--only` runs some legs and says `WWH OBD PARTIAL PASS` (w1 is always run:
the later legs need its vehicle).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import bench_bus  # noqa: E402
from pcbench import unplanned_boots  # noqa: E402

ACTOR = os.path.join(HERE, "..", "actors", "pcan_wwh_ecu.py")
PY = sys.executable

DUT = "localhost:8081"
PCAN = "PCAN_USBBUS2"
ONLY = []
LOGDIR = tempfile.gettempdir()
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--pcan":
        PCAN = args[i + 1]; i += 2
    elif args[i] == "--only":
        ONLY = [x.strip() for x in args[i + 1].split(",") if x.strip()]; i += 2
    elif args[i] == "--logdir":
        LOGDIR = args[i + 1]; i += 2
    else:
        DUT = args[i]; i += 1
BASE = "http://" + DUT
SIM = bench_bus.SIM_URL

ALL_LEGS = ["w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8", "w9", "w10"]

# the vehicles of this bench (their VINs are the store keys)
TRUCK = "1WCANWWH0TRUCK001"     # 29-bit, 500k, ECUs 00 and 3D
CAR11 = "1WCANWWH0CAR00002"     # 11-bit, one ECU, no F810
TRUCK250 = "1WCANWWH0TRK25003"  # 29-bit on a live 250k bus
SPRINTER = "W1V907633NP000001"  # the field report's van (actor --sprinter)
BENCH_VINS = (TRUCK, CAR11, TRUCK250, SPRINTER)

# what the truck's ECUs are told to send, and what the DUT must show for it
# (parameter name -> value; the PID encodings quantise some of them)
TRUCK_SET = ("rpm=1875,speed=91,coolant=77,load=55,throttle=33,fuel=42,"
             "volt=13.8,ambient=21,oil=101,cat_temp=410,intake=30,maf=22.5,"
             "runtime=4321,baro=98,fuel_rate=7.5,odo=54321.5")
TRUCK_VALUES = {
    "EngineRPM": 1875, "VehicleSpeed": 91, "EngineCoolantTemp": 77,
    "CalcEngineLoad": 55, "ThrottlePosition": 33, "FuelTankLevel": 42,
    "ControlModuleVolt": 13.8, "AmbientAirTemp": 21, "EngineOilTemp": 101,
    "CatTempBank1Sens1": 410, "IntakeAirTemperature": 30,
    "MAFAirFlowRate": 22.5, "TimeSinceEngStart": 4321, "AbsBaroPres": 98,
    "EngineFuelRate": 7.5, "Odometer": 54321.5,
}
TRUCK_ROWS = 17                 # 16 of ECU 00 (one shared), F43C of ECU 3D
TRUCK_DTCS = ["--dtcs", "P0420:08:02,P2463-1F:04:04", "--dtcs2",
              "P20EE:0C:02", "--permanent", "P0420", "--pending", "2"]
OWN_TAGS = ("autopid", "obd_chip", "uds_manager", "can_manager", "can_core")
# the ephemeral autopid jobs log their stack headroom when they end (log
# text -> name here); the poller is a live task. Floors: the stack audit's.
STACK_JOBS = {"std scan": "detection_job", "vehicle writer": "vehicle_writer",
              "dtc job": "dtc_job"}
POLLER_TASK = "autopid"
STACK_FAIL_B = 512
INT_MIN_FREE_B = 20 * 1024

fails = []
metrics = []
restarts = [0]
ring0 = set()                   # the log ring's lines as found
seen_e = set()                  # E lines of OWN_TAGS during the run
stack_min = {}                  # job / task -> least headroom seen, bytes
heap_min = [None]               # internal heap: least min_free seen, bytes


class Bench(Exception):
    """The rig did not do what a step needs: the run cannot go on."""


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail != "" else ""), flush=True)
    if not ok:
        fails.append(name)
    return ok


def metric(name, value, unit=""):
    metrics.append((name, value, unit))
    print(f"METRIC {name}={value}{(' ' + unit) if unit else ''}", flush=True)


# ---- the DUT over HTTP ----------------------------------------------------------

def api(path, method="GET", body=None, timeout=8):
    """(status, json or text); (0, reason) when the DUT does not answer."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, text = r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e)
    if text.strip().startswith(("{", "[")):
        try:
            return code, json.loads(text)
        except ValueError:
            pass
    return code, text


def get(path, tries=6):
    """GET that rides out a WiFi hiccup; Bench when the DUT stays away."""
    last = None
    for _ in range(tries):
        code, r = api(path)
        if code == 200:
            return r
        last = (code, r)
        time.sleep(1.5)
    raise Bench(f"GET {path}: {last}")


def settings(name):
    doc = get(f"/api/settings/{name}")
    return {k: v for k, v in doc.items()
            if k not in ("degraded", "pending_reboot")}


def stage(name, vals):
    """GET, modify, PUT. True when something changed."""
    cur = settings(name)
    new = dict(cur)
    new.update(vals)
    if new == cur:
        return False
    code, r = api(f"/api/settings/{name}", "PUT", new)
    if code != 200:
        raise Bench(f"PUT {name}: HTTP {code} {r}")
    return True


def boot_count():
    code, st = api("/api/status", timeout=4)
    return st.get("boot_count") if code == 200 and isinstance(st, dict) \
        else None


def restart(changes=None):
    """Stage `changes` ({component: {key: value}}) and restart the DUT
    (submit when something changed, a plain restart otherwise). Returns the
    time the DUT answered again."""
    sweep()                     # this boot's task list and heap floor end here
    changed = [n for n, v in (changes or {}).items() if stage(n, v)]
    b0 = boot_count()
    if b0 is None:
        raise Bench("no boot_count before a restart")
    t0 = time.time()
    # the reply can lose to the restart
    api("/api/settings/submit" if changed else "/api/restart", "POST", {})
    time.sleep(3)
    while time.time() - t0 < 150:
        b = boot_count()
        if b is not None and b != b0:
            restarts[0] += 1
            return time.time()
        time.sleep(0.5)
    raise Bench("the DUT did not come back within 150 s of a restart")


def uptime_s():
    """Seconds since the DUT booted, by its own clock."""
    ap = get("/api/autopid")
    return ap.get("stats", {}).get("now_us", 0) / 1e6


def vehicles():
    return get("/api/autopid/vehicles")


def current_car():
    for e in vehicles().get("vehicles", []):
        if e.get("current"):
            return e
    return {}


def wait_car(key, secs):
    """Until `key` is the current car. (entry, held)."""
    end = time.time() + secs
    e = {}
    while time.time() < end:
        e = current_car()
        if e.get("key") == key:
            return e, True
        time.sleep(1)
    return e, False


def values():
    ap = get("/api/autopid")
    return ({p["name"]: p.get("value") for p in ap.get("params", [])},
            ap.get("stats", {}))


def wait_value(name, secs):
    """Until parameter `name` has a value. (value, DUT uptime then)."""
    end = time.time() + secs
    while time.time() < end:
        vals, st = values()
        if vals.get(name) is not None:
            return vals[name], st.get("now_us", 0) / 1e6
        time.sleep(0.5)
    return None, None


def close(a, b):
    return a is not None and abs(a - b) <= max(0.6, abs(b) * 0.01)


def scan_result():
    code, r = api("/api/autopid/std_scan/result")
    return r if code == 200 and isinstance(r, dict) else {}


def wait_detection(secs):
    """Until no detection job runs. The status document."""
    end = time.time() + secs
    st = {}
    while time.time() < end:
        code, st = api("/api/autopid/std_scan")
        if code == 200 and st.get("status") != "running":
            time.sleep(0.5)     # the job's last log lines follow its status
            sweep()
            return st
        time.sleep(0.5)
    return st


def dtc_scan(secs=40):
    code, r = api("/api/autopid/dtc/scan", "POST", {})
    if code != 202:
        raise Bench(f"dtc scan: HTTP {code} {r}")
    end = time.time() + secs
    doc = {}
    while time.time() < end:
        code, doc = api("/api/autopid/dtc")
        if code == 200 and not doc.get("scanning"):
            sweep()
            return doc
        time.sleep(0.3)
    raise Bench("dtc scan did not finish")


def ring_lines():
    """The DUT's log ring as plain lines (16 KB: it rolls; it survives a
    restart)."""
    code, t = api("/api/logs/ring", timeout=15)
    if code != 200 or not isinstance(t, str):
        return []
    return [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in t.splitlines()]


def keep_min(name, value):
    if isinstance(value, int) and \
            (stack_min.get(name) is None or value < stack_min[name]):
        stack_min[name] = value


def sweep():
    """Keep what the DUT only says in passing: the E lines of this feature's
    tags, the stack headroom its jobs log when they end, the poller task's
    headroom and the internal heap's floor. Called after every job, before
    every restart and after every leg: the ring rolls, and a restart resets
    the task list and the heap floor. Quiet when the DUT does not answer."""
    for ln in ring_lines():
        if ln in ring0:
            continue
        if ln.startswith("E (") and ") " in ln and \
                ln.split(") ", 1)[1].split(":", 1)[0].strip() in OWN_TAGS:
            seen_e.add(ln)
        m = re.search(r"(std scan|vehicle writer|dtc job) stack_hw=(\d+)", ln)
        if m:
            keep_min(STACK_JOBS[m.group(1)], int(m.group(2)))
    code, t = api("/api/status/tasks")
    if code == 200 and isinstance(t, dict):
        for x in t.get("tasks", []):
            if x.get("name") == POLLER_TASK:
                keep_min("poller", x.get("stack_hw"))
    code, st = api("/api/status")
    if code == 200 and isinstance(st, dict):
        low = st.get("memory", {}).get("internal", {}).get("min_free")
        if isinstance(low, int) and \
                (heap_min[0] is None or low < heap_min[0]):
            heap_min[0] = low


# ---- the vehicle: the PCAN actor ---------------------------------------------------

class Actor:
    """One run of pcan_wwh_ecu.py. Its log is the wire's side of the story:
    one `ECU {json}` line per request (which ECU, functional or physical,
    what it answered)."""

    def __init__(self):
        self.p = None
        self.log = None
        self.stop_file = None

    def start(self, tag, actor_args, secs=900):
        self.stop()
        self.log = os.path.join(LOGDIR, f"wwh_bench_actor_{tag}.log")
        self.stop_file = os.path.join(LOGDIR, f"wwh_bench_actor_{tag}.stop")
        if os.path.exists(self.stop_file):
            os.remove(self.stop_file)
        out = open(self.log, "w")
        self.p = subprocess.Popen(
            [PY, "-u", ACTOR, str(secs), "--pcan", PCAN,
             "--stop-file", self.stop_file] + actor_args,
            stdout=out, stderr=subprocess.STDOUT)
        end = time.time() + 15
        while time.time() < end:
            if self.p.poll() is not None:
                raise Bench(f"the actor died at start: see {self.log}")
            if "ECU READY" in self.text():
                return
            time.sleep(0.3)
        raise Bench(f"the actor did not come up: see {self.log}")

    def stop(self):
        """Ask for a clean stop (never kill mid-frame: it wedges the PEAK
        driver). Returns the ECU DONE statistics, {} without."""
        if self.p is None:
            return {}
        open(self.stop_file, "w").close()
        try:
            self.p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.p.terminate()
            self.p.wait(timeout=10)
        self.p = None
        time.sleep(0.5)
        if os.path.exists(self.stop_file):
            os.remove(self.stop_file)
        for ln in self.text().splitlines():
            if ln.startswith("ECU DONE "):
                return json.loads(ln[9:])
        return {}

    def text(self):
        try:
            return open(self.log, encoding="utf-8", errors="replace").read()
        except OSError:
            return ""

    def requests(self, start=0):
        """The requests seen so far, from line index `start` on:
        [(req hex, ecu, functional, [responses hex])]."""
        out = []
        for ln in self.text().splitlines()[start:]:
            if ln.startswith("ECU {"):
                try:
                    d = json.loads(ln[4:])
                except ValueError:
                    continue
                out.append((d["req"], d["ecu"], d["func"], d["resp"]))
        return out

    def mark(self):
        return len(self.text().splitlines())

    def bus_errors(self):
        """The newest bus-error count of a --traffic actor."""
        n = None
        for ln in self.text().splitlines():
            if ln.startswith("ECU STAT "):
                n = json.loads(ln[9:]).get("bus_errors")
        return n


def forget(key):
    code, r = api("/api/autopid/vehicles/" + key, "DELETE")
    return code


def sim(**kw):
    """Move the simulator (it reboots when something changes)."""
    bench_bus.sim_set(base=SIM, **kw)


# ---- legs --------------------------------------------------------------------------

def rows_by_cmd(doc):
    return {r.get("cmd"): r for r in doc.get("supported", [])}


def leg_w1(actor):
    """A vehicle the store has never seen, found with nobody asking."""
    actor.start("truck", ["--id-format", "29", "--ecu", "0x00", "--ecu2",
                          "0x3D", "--set", TRUCK_SET] + TRUCK_DTCS)
    restart({"autopid": {"enabled": True, "std_protocol": "0",
                         "dtc_enabled": True, "dtc_allow_clear": False,
                         "dtc_pending": True, "dtc_permanent": True,
                         "dtc_protocol": "obd", "dtc_scan_period_min": 0}})
    e, held = wait_car(TRUCK, 90)
    check("w1_found_by_itself", held, f"current car {e.get('key')}")
    st = wait_detection(60)
    check("w1_detection_done", st.get("status") == "done", json.dumps(st))
    e = current_car()
    check("w1_store_entry",
          e.get("protocol") == "7" and e.get("dialect") == "uds"
          and e.get("vin") == TRUCK and e.get("pending_profile") is True,
          json.dumps({k: e.get(k) for k in ("protocol", "dialect", "vin",
                                            "pending_profile")}))
    ecus = dict(x.split(":") for x in e.get("ecus", "").split(",") if ":" in x)
    check("w1_both_ecus_with_bitmaps",
          ecus == {"18DAF100": "981B8003", "18DAF13D": "80000001"},
          e.get("ecus"))
    r = scan_result()
    check("w1_result_document",
          r.get("dialect") == "uds" and r.get("protocol_detected") == "7"
          and r.get("vin") == TRUCK and r.get("uds_protocol_id") == 1
          and r.get("found") == TRUCK_ROWS,
          json.dumps({k: r.get(k) for k in ("dialect", "protocol_detected",
                                            "vin", "uds_protocol_id",
                                            "found")}))
    rows = rows_by_cmd(r)
    owners = {c: x.get("init") for c, x in rows.items()}
    wrong = {c: o for c, o in owners.items()
             if o != ("ATSH18DA3DF1" if c == "22F43C" else "ATSH18DA00F1")}
    check("w1_rows_addressed_to_their_owner",
          len(rows) == TRUCK_ROWS and not wrong, json.dumps(wrong)[:200])
    rpm = rows.get("22F40C", {}).get("parameters", [{}])[0]
    check("w1_expressions_shifted", rpm.get("expression") == "[B3:B4]*0.25",
          str(rpm.get("expression")))
    # the rows a detection stores are OFF until the user ticks them (2026-10-09,
    # Ali): the user's tick, as the wizard's picker or Automate would
    cfg = get("/api/autopid/config")
    stored = [p for p in (cfg.get("pids") or []) if p.get("type") == "std"]
    check("w1_rows_stored_off_until_ticked",
          bool(stored) and all(p.get("enabled") is False for p in stored),
          f"{sum(1 for p in stored if p.get('enabled') is False)} of {len(stored)} off")
    for p in stored:
        p.pop("enabled", None)
    code, r = api("/api/autopid/config", "PUT", cfg)
    check("w1_rows_ticked_on", code == 200, f"HTTP {code} {json.dumps(r)[:80]}")
    v, up = wait_value("EngineRPM", 30)
    check("w1_values_flow", close(v, TRUCK_VALUES["EngineRPM"]), str(v))
    if up is not None:
        metric("w1_new_vehicle_first_value_s", round(up, 1), "s after boot")


def leg_w2(actor):
    time.sleep(3)
    vals, st0 = values()
    bad = {n: vals.get(n) for n, want in TRUCK_VALUES.items()
           if not close(vals.get(n), want)}
    check("w2_every_value_is_the_actors", not bad, json.dumps(bad))
    mark = actor.mark()
    time.sleep(10)
    vals, st1 = values()
    ok = st1.get("polls_ok", 0) - st0.get("polls_ok", 0)
    failed = st1.get("polls_failed", 0) - st0.get("polls_failed", 0)
    dt = (st1.get("now_us", 0) - st0.get("now_us", 0)) / 1e6
    check("w2_no_failed_poll", ok > 50 and failed == 0,
          f"{ok} answered, {failed} failed in {dt:.1f} s")
    metric("w2_polls_per_s", round(ok / dt, 1) if dt > 0 else 0)
    reqs = [x for x in actor.requests(mark) if x[0].startswith("22f4")]
    functional = [x for x in reqs if x[2]]
    stray = [x for x in reqs
             if x[1] != ("18DAF13D" if x[0] == "22f43c" else "18DAF100")]
    unanswered = [x for x in reqs if not x[3] or x[3][0][:2] != "62"]
    check("w2_asked_physically_of_the_owner_only",
          len(reqs) > 50 and not functional and not stray and not unanswered,
          f"{len(reqs)} requests, {len(functional)} functional, "
          f"{len(stray)} to another ECU, {len(unanswered)} unanswered")


def leg_w3(actor):
    """A custom row with no init between rows that set their own header."""
    cfg = get("/api/autopid/config")
    probe = dict(cfg)
    probe["pids"] = list(cfg.get("pids", [])) + [{
        "name": "wwh_probe", "type": "custom", "cmd": "22F810",
        "group": "default", "period_ms": 1000,
        "parameters": [{"name": "WWH_PROTOCOL_ID", "expression": "B3",
                        "unit": ""}]}]
    code, r = api("/api/autopid/config", "PUT", probe)
    if code != 200:
        raise Bench(f"PUT config: {code} {r}")
    try:
        mark = actor.mark()
        _, st0 = values()
        v, _ = wait_value("WWH_PROTOCOL_ID", 15)
        time.sleep(6)
        vals, st1 = values()
        check("w3_custom_row_answered", vals.get("WWH_PROTOCOL_ID") == 1,
              str(vals.get("WWH_PROTOCOL_ID")))
        reqs = actor.requests(mark)
        f810 = [x for x in reqs if x[0] == "22f810"]
        # a functional request reaches both ECUs; a physical one (the header
        # a standard row left behind) would reach one
        check("w3_sent_with_the_functional_header",
              len(f810) >= 6 and all(x[2] for x in f810)
              and {x[1] for x in f810} == {"18DAF100", "18DAF13D"},
              f"{len(f810)} requests, "
              f"{sum(1 for x in f810 if not x[2])} physical, ECUs "
              f"{sorted({x[1] for x in f810})}")
        data = [x for x in reqs if x[0].startswith("22f4")]
        check("w3_standard_rows_still_physical",
              len(data) > 30 and not [x for x in data if x[2]],
              f"{len(data)} requests, "
              f"{sum(1 for x in data if x[2])} functional")
        check("w3_no_failed_poll",
              st1.get("polls_failed", 0) == st0.get("polls_failed", 0),
              f"+{st1.get('polls_failed', 0) - st0.get('polls_failed', 0)}")
    finally:
        code, r = api("/api/autopid/config", "PUT", cfg)
        if code != 200:
            raise Bench(f"PUT config back: {code} {r}")


def report_checks(tag, rep, stored, pending, permanent, lamp):
    check(f"{tag}_report",
          rep.get("valid") is True and rep.get("protocol") == "wwh"
          and sorted(rep.get("stored", [])) == sorted(stored)
          and sorted(rep.get("pending", [])) == sorted(pending)
          and sorted(rep.get("permanent", [])) == sorted(permanent)
          and rep.get("mil") is lamp and not rep.get("error"),
          json.dumps({k: rep.get(k) for k in ("valid", "protocol", "mil",
                                              "stored", "pending",
                                              "permanent", "error")}))


def leg_w4(actor):
    mark = actor.mark()
    _, st0 = values()
    doc = dtc_scan()
    rep = doc.get("report", {})
    check("w4_path_is_wwh", doc.get("path") == "wwh", str(doc.get("path")))
    report_checks("w4", rep, ["P0420", "P20EE"], ["P2463-1F", "P20EE"],
                  ["P0420"], True)
    src = {s.get("ecu"): (s.get("mil"), s.get("count"))
           for s in rep.get("sources", [])}
    check("w4_lamp_per_ecu",
          src == {"18DAF100": (True, 1), "18DAF13D": (True, 1)}
          and rep.get("mil_count") == 2 and rep.get("ecus") == 2,
          json.dumps(rep.get("sources")))
    items = {(it.get("code"), it.get("kind"), it.get("ecu")):
             (it.get("status"), it.get("severity"))
             for it in rep.get("items", [])}
    check("w4_who_reported_what",
          items.get(("P0420", "stored", "18DAF100")) == (8, 2)
          and items.get(("P20EE", "stored", "18DAF13D")) == (12, 2)
          and items.get(("P2463-1F", "pending", "18DAF100")) == (4, 4)
          and ("P0420", "permanent", "18DAF100") in items,
          json.dumps(rep.get("items"))[:300])
    reqs = actor.requests(mark)
    dtc = [x for x in reqs if x[0][:2] == "19"]
    check("w4_asked_functionally_and_pending_ridden_out",
          {x[0] for x in dtc} == {"194233081e", "194233041e", "195533"}
          and all(x[2] for x in dtc)
          and all(x[3][:2] == ["7f1978", "7f1978"] for x in dtc),
          json.dumps(sorted({x[0] for x in dtc})))

    # the gate: dtc_allow_clear is off
    mark = actor.mark()
    code, r = api("/api/autopid/dtc/clear", "POST", {"confirm": True})
    time.sleep(1.5)
    sent = [x for x in actor.requests(mark) if x[0][:2] == "14"]
    check("w4_clear_refused_and_nothing_sent", code == 403 and not sent,
          f"HTTP {code} {json.dumps(r)[:80]}, {len(sent)} clear requests")

    # polling went on: the second ECU's row too (a scan leaves no filter)
    time.sleep(5)
    vals, st1 = values()
    check("w4_polling_undisturbed",
          st1.get("polls_failed", 0) == st0.get("polls_failed", 0)
          and close(vals.get("CatTempBank1Sens1"), 410)
          and st1.get("polls_ok", 0) - st0.get("polls_ok", 0) > 40,
          f"+{st1.get('polls_ok', 0) - st0.get('polls_ok', 0)} answered, "
          f"+{st1.get('polls_failed', 0) - st0.get('polls_failed', 0)} "
          f"failed")


def leg_w5(actor):
    restart({"autopid": {"dtc_allow_clear": True}})
    e, held = wait_car(TRUCK, 30)
    v, up = wait_value("EngineRPM", 40)
    check("w5_known_vehicle_back", held and close(v, 1875),
          f"car {e.get('key')}, rpm {v}")
    if up is not None:
        metric("w5_known_vehicle_first_value_s", round(up, 1), "s after boot")
    st = get("/api/autopid/std_scan")
    check("w5_no_new_detection", st.get("status") != "running"
          and current_car().get("pending_profile") is True
          and len([x for x in vehicles().get("vehicles", [])
                   if x.get("key") == TRUCK]) == 1, json.dumps(st))

    mark = actor.mark()
    code, r = api("/api/autopid/dtc/clear", "POST", {"confirm": True},
                  timeout=30)
    check("w5_cleared", code == 200 and r.get("cleared") is True
          and r.get("before") == 2 and r.get("after") == 0,
          f"HTTP {code} {json.dumps(r)}")
    clr = [x for x in actor.requests(mark) if x[0][:2] == "14"]
    check("w5_clear_on_the_wire",
          len(clr) == 2 and all(x[0] == "14ffff33" and x[2]
                                and x[3] == ["54"] for x in clr)
          and {x[1] for x in clr} == {"18DAF100", "18DAF13D"},
          json.dumps(clr))
    rep = dtc_scan().get("report", {})
    report_checks("w5_after", rep, [], [], ["P0420"], False)


def leg_w6(actor):
    """The same vehicle, its codes in the SAE J1939 format."""
    actor.start("truck_spn", ["--id-format", "29", "--ecu", "0x00", "--ecu2",
                              "0x3D", "--set", TRUCK_SET, "--dtc-format",
                              "02", "--dtcs", "3226-4:08:02,110-0:04:04",
                              "--dtcs2", "4364-18:08:02", "--permanent",
                              "3226-4"])
    time.sleep(2)
    rep = dtc_scan().get("report", {})
    report_checks("w6", rep, ["SPN3226-4", "SPN4364-18"], ["SPN110-0"],
                  ["SPN3226-4"], True)
    v, _ = wait_value("EngineRPM", 15)
    check("w6_values_flow", close(v, 1875), str(v))


def leg_w7(actor):
    """11-bit ids, one ECU, no F810: what an SAE J1979-2 car looks like."""
    actor.start("car11", ["--id-format", "11", "--ecu", "0x00", "--vin",
                          CAR11, "--no-f810", "--set", "rpm=2100,speed=50",
                          "--dtcs", ""])
    restart()
    e, held = wait_car(CAR11, 90)
    st = wait_detection(60)
    e = current_car()
    r = scan_result()
    check("w7_found_by_itself",
          held and st.get("status") == "done" and e.get("protocol") == "6"
          and e.get("dialect") == "uds" and e.get("ecus") == "7E8:981B8003",
          json.dumps({k: e.get(k) for k in ("key", "protocol", "dialect",
                                            "ecus")}))
    rows = rows_by_cmd(r)
    check("w7_rows_addressed_to_7E0_no_f810_needed",
          len(rows) == 16 and all(x.get("init") == "ATSH7E0"
                                  for x in rows.values())
          and "uds_protocol_id" not in r,
          f"{len(rows)} rows, inits "
          f"{sorted({str(x.get('init')) for x in rows.values()})}, "
          f"uds_protocol_id {r.get('uds_protocol_id')}")
    v, up = wait_value("EngineRPM", 30)
    time.sleep(4)
    vals, _ = values()
    check("w7_values", close(vals.get("EngineRPM"), 2100)
          and close(vals.get("VehicleSpeed"), 50),
          f"rpm {vals.get('EngineRPM')} speed {vals.get('VehicleSpeed')}")
    rep = dtc_scan().get("report", {})
    check("w7_dtc_scan_no_codes",
          rep.get("valid") is True and rep.get("protocol") == "wwh"
          and rep.get("stored") == [] and rep.get("mil") is False
          and [s.get("ecu") for s in rep.get("sources", [])] == ["7E8"],
          json.dumps({k: rep.get(k) for k in ("valid", "protocol", "stored",
                                              "mil", "sources", "error")}))


def leg_w8(actor):
    """The Sprinter VS30 of the field report."""
    # 5A turns up well after the detection (about 35 s after the actor start)
    actor.start("sprinter", ["--sprinter", "--late-5a", "70", "--dtcs", ""])
    t_actor = time.time()
    restart()
    e, held = wait_car(SPRINTER, 90)
    st = wait_detection(60)
    e = current_car()
    ecus = dict(x.split(":") for x in e.get("ecus", "").split(",") if ":" in x)
    check("w8_found_with_the_reports_bitmaps",
          held and st.get("status") == "done" and e.get("protocol") == "7"
          and e.get("dialect") == "uds"
          and ecus == {"18DAF158": "9818A013", "18DAF159": "98180001",
                       "18DAF15D": "98180001"},
          json.dumps({k: e.get(k) for k in ("key", "protocol", "dialect",
                                            "ecus")}))
    r = scan_result()
    rows = rows_by_cmd(r)
    empty = [c for c, x in rows.items() if not x.get("parameters")]
    check("w8_no_row_that_decodes_nothing",
          not empty and "22F413" not in rows and len(rows) == r.get("found"),
          f"{len(rows)} rows, without parameters: {empty}")
    check("w8_engine_rows_on_58",
          rows.get("22F40C", {}).get("init") == "ATSH18DA58F1"
          and all(x.get("init") in ("ATSH18DA58F1", "ATSH18DA5DF1")
                  for x in rows.values()),
          json.dumps(sorted({str(x.get('init')) for x in rows.values()})))
    v, _ = wait_value("EngineRPM", 30)
    check("w8_engine_speed_is_58s", close(v, 763.25), str(v))

    # the engine "starts": ECU 5A turns up with its own, unscaled copy
    while time.time() - t_actor < 76 and "addr=0x5A" not in actor.text():
        time.sleep(1)
    check("w8_5A_turned_up", "addr=0x5A" in actor.text())
    mark = actor.mark()
    _, st0 = values()
    time.sleep(8)
    vals, st1 = values()
    to_5a = [x for x in actor.requests(mark) if x[1] == "18DAF15A"]
    check("w8_5A_is_never_asked_and_the_value_stays",
          close(vals.get("EngineRPM"), 763.25) and not to_5a
          and st1.get("polls_failed", 0) == st0.get("polls_failed", 0),
          f"rpm {vals.get('EngineRPM')}, {len(to_5a)} requests reached 5A")

    # the next boot meets four ECUs: the same van, nothing detected anew
    restart()
    e, held = wait_car(SPRINTER, 40)
    v, _ = wait_value("EngineRPM", 40)
    time.sleep(3)
    e = current_car()
    n = len([x for x in vehicles().get("vehicles", [])
             if x.get("key") == SPRINTER])
    check("w8_same_van_with_a_fourth_ecu",
          held and n == 1 and close(v, 763.25) and "18DAF15A" in e.get("ecus", "")
          and get("/api/autopid/std_scan").get("status") != "running",
          f"rpm {v}, ecus {e.get('ecus')}")


def leg_w9(actor):
    """A truck's bus: 250 kbit/s and talking when the device boots."""
    actor.stop()
    # nothing of the DUT may sit on the bus at the old bitrate while it moves
    restart({"autopid": {"enabled": False}})
    # The simulator is the ACK source and must not answer 0100. Its CAN
    # controller survives its restart: disabled, it keeps ACKing at the
    # bitrate it LAST ran at. So: run it at 250k once, then switch its ECU
    # off (straight to "250k, off" it would sit on this bus at 500k).
    sim(bitrate=250, enabled=True)
    sim(enabled=False)
    actor.start("truck250", ["--bitrate", "250", "--id-format", "29", "--ecu",
                             "0x00", "--ecu2", "0x3D", "--vin", TRUCK250,
                             "--traffic", "100", "--set", "rpm=1400",
                             "--dtcs", ""])
    time.sleep(2.5)
    e0 = actor.bus_errors()
    restart({"autopid": {"enabled": True}})
    e, held = wait_car(TRUCK250, 90)
    st = wait_detection(60)
    e = current_car()
    check("w9_found_on_protocol_9",
          held and st.get("status") == "done" and e.get("protocol") == "9"
          and e.get("dialect") == "uds",
          json.dumps({k: e.get(k) for k in ("key", "protocol", "dialect")}))
    g = get("/api/autopid").get("bus_guard", {})
    check("w9_guard_named_the_bitrate",
          g.get("bus") == "live" and g.get("bus_kbps") == 250
          and g.get("parked") is False, json.dumps(g))
    v, up = wait_value("EngineRPM", 30)
    time.sleep(5)
    vals, _ = values()
    check("w9_values", close(vals.get("EngineRPM"), 1400),
          str(vals.get("EngineRPM")))
    if up is not None:
        metric("w9_live_250k_first_value_s", round(up, 1), "s after boot")
    e1 = actor.bus_errors()
    check("w9_no_bus_error_on_the_live_bus",
          e0 is not None and e1 is not None and e1 == e0,
          f"bus errors seen by the adapter: {e0} before the DUT's boot, "
          f"{e1} now")
    # ... and off the bus again before it moves back
    restart({"autopid": {"enabled": False}})
    done = actor.stop()
    check("w9_actor_clean", done.get("bus_errors") == e0
          and not done.get("isotp_errors"), json.dumps(done))
    # back to 500k, its ECU off again (through "on": see above)
    sim(bitrate=500, enabled=True)
    sim(enabled=False)


def leg_w10(actor, found_car):
    """Back on the OBD-II car with nobody asking; then that car on 29-bit
    ids (the chip prints such an id as four byte tokens)."""
    actor.stop()
    sim(id_format="11bit", enabled=True, bitrate=500)
    restart({"autopid": {"enabled": True}})
    e, held = wait_car(found_car, 60)
    v, up = wait_value("EngineRPM", 40)
    check("w10_back_on_the_obd2_car",
          held and e.get("dialect") == "obd2" and e.get("protocol") == "6"
          and v is not None,
          json.dumps({k: e.get(k) for k in ("key", "protocol", "dialect")})
          + f" rpm {v}")
    if up is not None:
        metric("w10_switch_first_value_s", round(up, 1), "s after boot")
    rep = dtc_scan().get("report", {})
    check("w10_obd2_dtc_scan_names_the_ecu",
          rep.get("valid") is True and rep.get("protocol") == "obd"
          and not rep.get("error") and len(rep.get("stored", [])) > 0
          and all(it.get("ecu") == "7E8" for it in rep.get("items", [])),
          json.dumps({k: rep.get(k) for k in ("valid", "protocol", "stored",
                                              "error")}))

    sim(id_format="29bit")
    restart()
    e, held = wait_car(found_car, 60)
    v, _ = wait_value("EngineRPM", 40)
    time.sleep(3)
    e = current_car()
    check("w10_29bit_ecu_told_apart",
          held and e.get("protocol") == "7" and v is not None
          and e.get("ecus", "").startswith("18DAF1"),
          json.dumps({k: e.get(k) for k in ("key", "protocol", "ecus")}))
    rep = dtc_scan().get("report", {})
    check("w10_29bit_dtc_scan_reads",
          rep.get("valid") is True and not rep.get("error")
          and len(rep.get("stored", [])) > 0
          and all(str(it.get("ecu", "")).startswith("18DAF1")
                  for it in rep.get("items", [])),
          json.dumps({k: rep.get(k) for k in ("valid", "stored", "error",
                                              "sources")}))
    sim(id_format="11bit")
    restart()
    e, held = wait_car(found_car, 60)
    wait_value("EngineRPM", 40)
    time.sleep(3)
    check("w10_back_on_11bit", current_car().get("protocol") == "6",
          str(current_car().get("protocol")))


def store_view(doc):
    """What must be the same before and after: who is in the store, who is
    current, and what the device learned about each car."""
    return {"current": doc.get("current"),
            "cars": sorted((e.get("key"), e.get("protocol"), e.get("dialect"),
                            e.get("ecus"), e.get("profile"), e.get("name"))
                           for e in doc.get("vehicles", []))}


def main():
    legs = ALL_LEGS if not ONLY else ["w1"] + [x for x in ONLY if x != "w1"]
    unknown = [x for x in legs if x not in ALL_LEGS]
    if unknown:
        print(f"unknown leg(s) {unknown}; legs: {' '.join(ALL_LEGS)}")
        return 2

    # ---- as found ----------------------------------------------------------------
    st0 = get("/api/status")
    found = {"autopid": settings("autopid")}
    found_sim = bench_bus.sim_get("ecu_sim", SIM)
    for key in BENCH_VINS:      # left behind by an aborted run
        forget(key)
    found_store = store_view(vehicles())
    found_car = found_store["current"]
    found_proto = current_car().get("protocol")
    found_cfg = get("/api/autopid/config")
    code, f0 = api("/api/faults")
    print(f"DUT {DUT}: boot_count {st0.get('boot_count')}, unexpected_resets "
          f"{st0.get('unexpected_resets')}, version {st0.get('version')}")
    print(f"as found: autopid enabled={found['autopid'].get('enabled')} "
          f"dtc_enabled={found['autopid'].get('dtc_enabled')}, store "
          f"{json.dumps(found_store)}")
    print(f"as found: simulator {json.dumps(found_sim)}")
    if not found_car:
        print("the bench needs the OBD-II car in the store as found "
              "(leg w10 returns to it)")
        return 2
    ring0.update(ring_lines())

    actor = Actor()
    done = 0
    try:
        sim(enabled=False)      # it would answer 0100; it keeps ACKing
        for leg in legs:
            print(f"--- {leg}", flush=True)
            if leg == "w10":
                leg_w10(actor, found_car)
            else:
                globals()["leg_" + leg](actor)
            sweep()
            done += 1
            print(f"PROGRESS {done}/{len(legs)}", flush=True)
    except Bench as e:
        check("bench_ran_to_the_end", False, str(e))
    finally:
        # ---- restore, as found ------------------------------------------------
        print("--- restore", flush=True)
        try:
            actor.stop()
            if bench_bus.sim_get("ecu_sim", SIM) != found_sim:
                # the bus may move: nothing of the DUT on it meanwhile
                restart({"autopid": {"enabled": False}})
                sim(bitrate=found_sim["bitrate"],
                    id_format=found_sim["id_format"],
                    enabled=found_sim["enabled"])
            cur = current_car()
            if cur.get("key") != found_car or \
                    cur.get("protocol") != found_proto:
                # the store learns from the car: let it meet the car as found
                restart({"autopid": {"enabled": True}})
                wait_car(found_car, 60)
                end = time.time() + 40
                while current_car().get("protocol") != found_proto and \
                        time.time() < end:
                    time.sleep(1)
            for key in BENCH_VINS:
                forget(key)
            if get("/api/autopid/config") != found_cfg:
                code, r = api("/api/autopid/config", "PUT", found_cfg)
                if code != 200:
                    raise Bench(f"PUT config back: {code} {r}")
            if settings("autopid") != found["autopid"]:
                restart({"autopid": found["autopid"]})
            now = {"autopid": settings("autopid")}
            check("restored_settings", now == found,
                  "" if now == found else json.dumps(now))
            sv = store_view(vehicles())
            check("restored_vehicle_store", sv == found_store,
                  "" if sv == found_store else json.dumps(sv))
            cfg = get("/api/autopid/config")
            check("restored_tables", cfg == found_cfg,
                  "" if cfg == found_cfg
                  else f"{len(cfg.get('pids', []))} pids now")
            sim_now = bench_bus.sim_get("ecu_sim", SIM)
            check("restored_simulator", sim_now == found_sim,
                  "" if sim_now == found_sim else json.dumps(sim_now))
            code, f1 = api("/api/faults")
            check("no_new_fault", f1 == f0, json.dumps(f1)[:300])
            st1 = get("/api/status")
            check("no_unexpected_reset",
                  st1.get("unexpected_resets") == st0.get("unexpected_resets")
                  and st1.get("boot_count") - st0.get("boot_count")
                  == restarts[0],
                  f"unexpected_resets {st0.get('unexpected_resets')} -> "
                  f"{st1.get('unexpected_resets')}, boot_count +"
                  f"{st1.get('boot_count') - st0.get('boot_count')} for "
                  f"{restarts[0]} restarts"
                  + unplanned_boots(api("/api/restart/history", timeout=15)[1],
                                    st0.get("boot_count", 0)))
            sweep()
            new_e = sorted(seen_e)
            check("no_own_E_lines", not new_e, " | ".join(new_e)[:400])
            # every new stack user of the feature ran in w1 (the detection
            # job, the vehicle writer, the poller); the DTC job in its legs
            want = ["detection_job", "vehicle_writer", "poller"]
            if set(legs) & {"w4", "w5", "w6", "w7", "w10"}:
                want.append("dtc_job")
            for name in want:
                if stack_min.get(name) is not None:
                    metric(f"stack_{name}_headroom_b", stack_min[name])
            low = [n for n in want if stack_min.get(n) is None
                   or stack_min[n] < STACK_FAIL_B]
            check(f"stack_headroom_at_least_{STACK_FAIL_B}_B", not low,
                  ", ".join(f"{n} {stack_min.get(n)}" for n in want))
            if heap_min[0] is not None:
                metric("internal_heap_floor_b", heap_min[0])
            check(f"internal_heap_floor_at_least_{INT_MIN_FREE_B}_B",
                  heap_min[0] is not None and heap_min[0] >= INT_MIN_FREE_B,
                  f"{heap_min[0]}")
        except Bench as e:
            check("restore", False, str(e))

    for name, value, unit in metrics:
        print(f"  {name} = {value}{(' ' + unit) if unit else ''}")
    if fails:
        print("WWH OBD " + ("PARTIAL " if ONLY else "") + "FAIL: "
              + ", ".join(fails))
        return 1
    print("WWH OBD PARTIAL PASS (legs: " + " ".join(legs) + ")" if ONLY
          else "WWH OBD PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
