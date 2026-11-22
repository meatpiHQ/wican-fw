#!/usr/bin/env python3
"""A drive, start to finish: home WiFi, the road, home again, the backfill.

The users' report (2026-11-21): after a drive away from the home WiFi the
history in Home Assistant has holes, some parameters come back and others do
not. This bench plays that drive in real time and keeps three records side by
side, so the place where a value is lost can be named:

  1. the TRUTH: every value set in ECU sim v2, stamped (`truth.jsonl`);
  2. what the WiCAN RECORDED on its SD card while it was away
     (`device_rows.jsonl`, read back through `GET /api/logger/export`);
  3. what HOME ASSISTANT holds afterwards: the hourly statistics the
     integration imports, the five-minute statistics and the plain states
     the History panel draws (`ha_stats_hour.json`, `ha_stats_5min.json`,
     `ha_states.json`).

`drive_report.py` compares them per parameter and per period and writes
`report.md`, `report.html` (a chart per parameter) and `report.json`; it runs
alone over a finished run's folder too (`python drive_report.py <logdir>`).

The scenario, every part of it a parameter:

  home     --home-min        at home on the WiFi, pushes live
  away     --drive-min       the bench AP goes off (`--away ap`, a real loss
                             of the network) or the pushes are switched off
                             (`--away push`); the values keep changing by
                             the drive profile (`--profile`, `--step-s`)
  return   the AP comes back; the first push ends the gap and the
           integration pulls the log (`--pull-wait-s`)
  park     --park-min        optional: the supply drops to `--park-v` after
                             `--park-delay-min` (engine off, the unit sleeps
                             by its own settings), then comes back
  again    --trips N         the whole thing N times
  settle   --settle          followup = wait for the run the integration
                             books past the top of the hour (the hour still
                             running is imported only once it has ended),
                             button = press Catch up history then,
                             none = look now
  compare  the three records, the verdict, everything put back
  units    --ha-units        Home Assistant's unit system for the run (a US
                             system keeps temperatures, speeds, distances and
                             pressures in other units than the unit logs)

  python tools/testbench/ha/ha_drive_backfill_bench.py --dut localhost:18089 \
      --dut-ip 10.42.1.41 --drive-min 30

Legs: d0 the rig, d1 configure, d2 the trips, d3 settle, d4 collect and
compare, d5 restore. Verdict line: HA DRIVE PASS / FAIL. The folder of a run
keeps every record; a file named STOP in it ends the drive early (the rest
runs as usual). Page: testing/hadrive.md.
"""
import argparse
import calendar
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import ha_history_backfill_bench as hb  # noqa: E402  (Dut, Ha, pi, check, log)
import drive_report  # noqa: E402
import drive_sim  # noqa: E402
import sim_net  # noqa: E402

check, log, metric = hb.check, hb.log, hb.metric
REPO = hb.REPO
SETTINGS = ("data_logger", "autopid", "sleep_manager", "ha_webhooks")
HA_INSTALLS = {
    # the bench Home Assistant (2026-09) and the fresh one beside it (a
    # current release with the integration's public release: what a user runs)
    "bench": {"unit": "ha-bench", "config": "/home/meatpi/.homeassistant", "bin": "/home/meatpi/ha/bin/hass",
              "login": None, "pi_ip": "10.42.1.1"},
    "fresh": {"unit": "ha-fresh", "config": "/home/meatpi/ha_fresh/config",
              "bin": "/home/meatpi/ha_fresh/venv/bin/hass", "login": "~/pushreply/fresh/login.json", "pi_ip": None},
}
AP_CLI = "cd ~/wican/tools/testbench/pi && timeout 20 python3 bench_ap_cli.py"
# A sensor of the bench's own in Home Assistant (a state every 20 s): its
# hourly statistics are Home Assistant's work alone. An hour of it missing
# means Home Assistant's hourly job did not get through (it writes every
# sensor's row of an hour in one go and gives up as a whole when one row is
# there already: an import that came before it).
CONTROL_ENTITY = "sensor.hadrive_control"


class HaLong(hb.Ha):
    """hb.Ha for a run of hours: an access token lives 30 minutes."""

    def __init__(self, base, user=None, pw=None):
        super().__init__(base)
        self.user, self.pw = user, pw
        self._lock = threading.Lock()

    def relogin(self):
        with self._lock:
            return self.login(self.user, self.pw)

    def call(self, path, body=None, method=None, timeout=60):
        code, r = super().call(path, body, method, timeout)
        if code == 401 and self.user:
            self.relogin()
            code, r = super().call(path, body, method, timeout)
        return code, r

    def ws_call(self, message):
        try:
            return super().ws_call(message)
        except RuntimeError as e:
            if "auth" not in str(e) or not self.user:
                raise
            self.relogin()
            return super().ws_call(message)

    def registry(self, entry_id):
        """{parameter name: {entity_id, disabled}} of the entry's PID sensors,
        and every entity id of the entry."""
        pids, every = {}, []
        prefix = f"{entry_id}_pid_"
        for e in self.ws_call({"type": "config/entity_registry/list"}):
            if e.get("config_entry_id") != entry_id:
                continue
            every.append(e.get("entity_id"))
            uid = str(e.get("unique_id") or "")
            if uid.startswith(prefix):
                pids[uid[len(prefix):]] = {"entity_id": e.get("entity_id"), "disabled": bool(e.get("disabled_by"))}
        return pids, every

    def device_sensor(self, entry_id, key):
        """The entity id of the entry's status sensor of that key (None = none)."""
        for e in self.ws_call({"type": "config/entity_registry/list"}):
            if e.get("config_entry_id") == entry_id and e.get("unique_id") == f"{entry_id}_{key}":
                return None if e.get("disabled_by") else e.get("entity_id")
        return None

    def stats(self, ids, start_epoch, period):
        if not ids:
            return {}
        start = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(start_epoch))
        return self.ws_call({"type": "recorder/statistics_during_period", "start_time": start,
                             "statistic_ids": list(ids), "period": period, "types": ["mean", "min", "max"]})

    def units(self, ids):
        """{entity_id: {"state": the unit its state carries, "statistics": the
        unit Home Assistant keeps its statistics in}}."""
        out = {i: {"state": None, "statistics": None} for i in ids}
        try:
            for m in self.ws_call({"type": "recorder/list_statistic_ids"}) or []:
                if m.get("statistic_id") in out:
                    out[m["statistic_id"]]["statistics"] = m.get("statistics_unit_of_measurement")
        except RuntimeError as e:
            log(f"the statistics' units could not be read: {e}")
        for i in ids:
            code, st = self.call(f"/api/states/{i}")
            if code == 200 and isinstance(st, dict):
                out[i]["state"] = (st.get("attributes") or {}).get("unit_of_measurement")
        return out

    def states(self, ids, start_epoch, end_epoch):
        if not ids:
            return []
        iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(s))  # noqa: E731
        q = urllib.parse.urlencode({"end_time": iso(end_epoch), "filter_entity_id": ",".join(ids), "no_attributes": ""})
        code, r = self.call(f"/api/history/period/{urllib.parse.quote(iso(start_epoch))}?{q}", timeout=120)
        return r if code == 200 and isinstance(r, list) else []


class Monitor(threading.Thread):
    """Every 10 s: the unit's logger, webhook and sleep figures when it can
    be reached, and every catch-up run the integration finishes."""

    def __init__(self, run):
        super().__init__(daemon=True, name="monitor")
        self.run_ = run
        self.halt = threading.Event()
        self.syncs = []
        self.reachable = True
        self.kept_seen = False
        self.ticks = 0
        self.t_start = time.time()
        self._seen = set()

    def note_sync(self, hs):
        key = hs.get("finished_at")
        if not key or key in self._seen:
            return
        self._seen.add(key)
        self.syncs.append(hs)
        st = hs.get("states") or {}
        log("integration run: " + json.dumps({k: hs.get(k) for k in (
            "ran", "source", "pages", "rows_fetched", "rows_used", "rows_deferred", "rows_rejected",
            "hours_imported", "hours_from_kept_copy", "pids_imported", "pids_skipped", "followup_in", "errors")
            if k in hs} | ({"states_written": st.get("states_written"), "states_note": st.get("note")} if st else {})))
        if hs.get("hours_from_kept_copy") and not self.kept_seen:
            # hours imported from the rows an earlier pull kept (the unit does
            # not answer): what the statistics hold at this moment is the proof
            self.kept_seen = True
            r = self.run_
            r.event("hours imported from the kept copy", hours=hs.get("hours_from_kept_copy"), ran=hs.get("ran"))
            try:
                if r.stat_ids:
                    with open(os.path.join(r.logdir, "ha_stats_hour_kept.json"), "w", encoding="utf-8") as f:
                        json.dump({"taken_at_ha": key, "stats": r.ha.stats(r.stat_ids, r.stat_start, "hour")}, f)
            except Exception as e:  # noqa: BLE001  (the monitor never ends a run)
                log(f"the statistics at the kept-copy import could not be read: {type(e).__name__}: {e}")

    def run(self):
        r = self.run_
        with open(os.path.join(r.logdir, "monitor.jsonl"), "a", encoding="utf-8") as f:
            while not self.halt.wait(10):
                rec = {"t": round(time.time(), 1)}
                if self.reachable:
                    for key, path in (("logger", "/api/logger"), ("webhook", "/api/webhook"), ("sleep", "/api/sleep")):
                        code, doc = r.dut.api(path, timeout=4, tries=1)
                        if code == 200 and isinstance(doc, dict):
                            keep = {"logger": ("written", "dropped", "errors", "queued", "file", "export"),
                                    "webhook": ("status", "success_count", "fail_count", "last_error"),
                                    "sleep": ("state", "voltage", "pending", "sleep_in_s", "activity_s")}[key]
                            rec[key] = {k: doc.get(k) for k in keep}
                try:
                    # a sensor that is not the unit's, kept moving: Home Assistant's
                    # own hourly statistics of it tell whether its hourly job ran whole
                    if r.ha is not None and self.ticks % 2 == 0:
                        r.ha.call(f"/api/states/{CONTROL_ENTITY}", {
                            "state": str(round(12.0 + (self.ticks % 30) * 0.1, 1)),
                            "attributes": {"state_class": "measurement", "unit_of_measurement": "V",
                                           "friendly_name": "hadrive control"}})
                    self.ticks += 1
                except Exception as e:  # noqa: BLE001  (the monitor never ends a run)
                    rec["control_error"] = f"{type(e).__name__}: {e}"[:120]
                try:
                    if r.ha is not None and r.entry_id:
                        hs = (r.ha.diagnostics(r.entry_id) or {}).get("history_sync")
                        if hs:
                            rec["sync_finished_at"] = hs.get("finished_at")
                            self.note_sync(hs)
                except Exception as e:  # the monitor never ends a run
                    rec["ha_error"] = f"{type(e).__name__}: {e}"[:120]
                f.write(json.dumps(rec) + "\n")
                f.flush()


class Run:
    def __init__(self, args):
        self.args = args
        self.logdir = args.logdir
        self.dut = hb.Dut(args.dut)
        self.ha = None
        self.entry_id = None
        self.phases = []                # {"kind": home|away|parked, "trip", "t0", "t1"} on the PC's clock
        self.events = []                # {"t", "what"}
        self.found = {}
        self.sim = None
        self.mon = None
        self.stat_ids = []              # the sensors whose statistics the run reads
        self.stat_start = 0

    def event(self, what, **more):
        self.events.append({"t": round(time.time(), 2), "what": what, **more})
        log(f"[{time.strftime('%H:%M:%S', time.gmtime())}Z] {what}" + (f" {json.dumps(more)}" if more else ""))

    def stop_asked(self):
        return os.path.exists(os.path.join(self.logdir, "STOP"))

    def wait(self, secs, what, stoppable=False):
        """Sleep `secs`, a line a minute; True when a STOP file cut it short."""
        t0 = time.time()
        nxt = t0 + 60
        while time.time() - t0 < secs:
            if stoppable and self.stop_asked():
                log(f"{what}: the STOP file ends it after {time.time() - t0:.0f} s")
                return True
            time.sleep(min(2.0, max(0.05, secs - (time.time() - t0))))
            if time.time() >= nxt:
                nxt += 60
                sim = self.sim
                log(f"{what}: {(time.time() - t0) / 60:.0f} of {secs / 60:.0f} min"
                    + (f"; simulator: {sim.sent} values set, {sim.failed} refused" if sim else ""))
        return False


def fetch_text(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def dut_clock_offset(dut):
    """The unit's clock minus the PC's, in seconds: the turn of a second of
    /api/rtc caught by asking in a row (a tenth of a second or so)."""
    last, best = None, None
    end = time.time() + 8
    while time.time() < end:
        a = time.time()
        code, r = dut.api("/api/rtc", timeout=4, tries=1)
        b = time.time()
        if code != 200 or not isinstance(r, dict):
            time.sleep(0.3)
            continue
        s = calendar.timegm(time.strptime(r["time"], "%Y-%m-%dT%H:%M:%SZ"))
        best = s + 0.5 - (a + b) / 2
        if last is not None and s == last + 1 and b - a < 0.5:
            return round(s - (a + b) / 2, 2)
        last = s
        time.sleep(0.02)
    if best is None:
        raise RuntimeError("the unit's clock could not be read")
    return round(best, 2)


def pi_clock_offset(host):
    a = time.time()
    rc, out = hb.pi(host, "date -u +%s.%N")
    b = time.time()
    return round(float(out.strip().splitlines()[-1]) - (a + b) / 2, 2)


def ap_cmd(args, cmd):
    return hb.pi(args.pi, f"{AP_CLI} '{cmd}'", timeout=40)


def wait_reachable(dut, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        code, _ = dut.api("/api/info", timeout=4, tries=1)
        if code == 200:
            return time.time() - t0
        time.sleep(2)
    return None


def export_device_log(dut, since_s, path):
    """Every row the unit holds from `since_s` on, through the export route,
    written as received. Returns (rows, pages, files)."""
    cursor, rows, pages, files, cut_pages = str(int(since_s)), 0, 0, [], 0
    with open(path, "w", encoding="utf-8") as f:
        while pages < 5000:
            # the cursor as it came, its colon not encoded: the route does not
            # decode %3A, reads such a cursor as a plain epoch and answers the
            # file's first page again (2026-11-22, this bench's first run)
            url = f"{dut.base}/api/logger/export?stream=params&limit=2000&since={cursor}"
            text = None
            for _ in range(4):
                try:
                    code, text = fetch_text(url, timeout=60)
                    if code == 200:
                        break
                except (urllib.error.URLError, OSError) as e:
                    text = None
                    log(f"export page: {type(e).__name__}: {e}; again")
                time.sleep(3)
            if text is None:
                raise RuntimeError("the unit's log could not be read (export route)")
            pages += 1
            nxt, more = None, False
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith('{"_cursor"'):
                    doc = json.loads(line)
                    nxt, more = doc.get("_cursor"), bool(doc.get("more"))
                    continue
                if line.startswith('{"_file"'):
                    name = json.loads(line).get("_file")      # every page names its file again
                    if name not in files:
                        files.append(name)
                elif line.startswith("{"):
                    rows += 1
                f.write(line + "\n")
            if nxt is None:
                cut_pages += 1
                if cut_pages > 5:
                    raise RuntimeError("the export route keeps answering without a cursor line")
                log(f"export page {pages}: no cursor line, read again")
                pages -= 1
                time.sleep(2)
                continue
            if not more:
                break
            if nxt == cursor:
                raise RuntimeError(f"the export cursor did not advance past {cursor}")
            cursor = nxt
    return rows, pages, files


def ring_e_tags(dut):
    """{tag: count} of the E lines the unit's log ring holds, any component
    (the verdict looks at four tags; a library's E lines while the network is
    away are a note)."""
    code, ring = dut.api("/api/logs/ring", timeout=10)
    if code != 200:
        return {}
    lines = ring.get("lines") if isinstance(ring, dict) else ring
    if not isinstance(lines, list):
        lines = str(ring).splitlines()
    out = {}
    for ln in lines:
        text = ln if isinstance(ln, str) else (ln.get("text") or ln.get("line") or str(ln))
        at = text.find("E (")
        if at < 0:
            continue
        tag = text[at:].split(")", 1)[-1].strip().split(":", 1)[0].strip()
        if tag and len(tag) < 32:
            out[tag] = out.get(tag, 0) + 1
    return out


def parse_set(items):
    out = {}
    for item in items or []:
        key, _, raw = item.partition("=")
        comp, _, name = key.partition(".")
        if not (comp and name and _):
            raise SystemExit(f"--set wants component.key=value, got {item!r}")
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        out.setdefault(comp, {})[name] = value
    return out


def build_table(params, period_ms):
    return {"groups": [{"name": "drive", "enabled": True, "period_ms": period_ms}],
            "pids": [{"name": p.name, "type": "custom", "cmd": p.row["cmd"], "group": "drive",
                      "parameters": [dict({"name": p.name}, **{k: v for k, v in p.row.items() if k != "cmd"})]}
                     for p in params],
            "filters": []}


def table_names(table):
    return [par.get("name") for pid in table.get("pids", []) for par in pid.get("parameters", [])]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_argument_group("the rig")
    g.add_argument("--dut", default="localhost:18089", help="the unit as reached from here (an ssh tunnel through the Pi)")
    g.add_argument("--dut-ip", default="10.42.1.41", help="the unit's address on the bench AP (what Home Assistant uses)")
    g.add_argument("--pi", default="rpi001")
    g.add_argument("--sim", default="auto", help="ECU sim v2's base URL (default: found)")
    g.add_argument("--psu", default="auto", help="the unit's supply (ECU sim v2's OBD pin 16); used by --park-min")
    g.add_argument("--ha-install", choices=sorted(HA_INSTALLS), default="fresh",
                   help="which Home Assistant on the Pi: fresh = a current release with the integration's public release (default), bench = the 2026-09 one")
    g.add_argument("--ha", default=None, help="that Home Assistant's base URL from here (default: http://<pi LAN ip>:8123)")
    g.add_argument("--ha-user")
    g.add_argument("--ha-pass")
    g.add_argument("--keep-ha", action="store_true", help="leave Home Assistant running and the entry in place, to look at the result")
    g.add_argument("--clear-marks", action="store_true", help="forget the integration's stored watermark and cursor first")
    g.add_argument("--ha-units", choices=("as-found", "metric", "us_customary"), default="as-found",
                   help="Home Assistant's unit system for the run, put back at the end (a sensor takes its unit when it is created: "
                        "the entry must be one this run adds)")
    g.add_argument("--clear-stats", action="store_true",
                   help="delete the statistics Home Assistant holds for the entry's sensors before the drive (an earlier run's hours)")
    g.add_argument("--integration", default=None, metavar="DIR",
                   help="run this copy of the integration (a custom_components/wican folder on this PC) instead of the one the "
                        "Home Assistant has installed: copied to the Pi for the run, the installed one put back at the end "
                        "(the Home Assistant must be one this run starts)")
    g = ap.add_argument_group("the drive")
    g.add_argument("--profile", default=drive_sim.DEFAULT_PROFILE, help="the drive profile (JSON; drive_sim.py lists the patterns)")
    g.add_argument("--params", default="", help="comma list: only these parameters of the profile")
    g.add_argument("--trips", type=int, default=1)
    g.add_argument("--home-min", default="3", help="at home before each drive (a comma list = per trip, the last value repeats)")
    g.add_argument("--drive-min", default="20", help="away from the home network (a comma list = per trip)")
    g.add_argument("--back-min", type=float, default=2, help="at home after the last return, before the settle")
    g.add_argument("--leave-at-minute", type=int, default=-1,
                   help="the first drive starts when the clock's minute is this (after --home-min): 50 with --drive-min 20 crosses the hour")
    g.add_argument("--away", choices=("ap", "push"), default="ap",
                   help="ap = the bench AP off (the P4's lever), push = the unit's pushes switched off")
    g.add_argument("--step-s", type=float, default=5, help="how often a value is worked out again (a parameter's every_s wins)")
    g.add_argument("--park-min", default="0", help="after each return: the supply at --park-v for this long (0 = no park; a comma list = per trip)")
    g.add_argument("--park-v", type=float, default=12.5)
    g.add_argument("--park-delay-min", type=float, default=1, help="from the return to the engine going off")
    g.add_argument("--freeze-parked", action="store_true", help="hold the simulator's values while parked")
    g = ap.add_argument_group("the unit")
    g.add_argument("--table", choices=("auto", "keep", "profile"), default="auto",
                   help="keep = the unit's own AutoPID table, profile = the profile's rows, auto = keep when it carries every profile parameter")
    g.add_argument("--period-ms", type=int, default=0, help="the AutoPID group period for the run (0 = as found, or the profile's)")
    g.add_argument("--log-mode", choices=("all", "changed"), default="all", help="data_logger.autopid_log for the run")
    g.add_argument("--set", action="append", default=[], metavar="component.key=value",
                   help="any other setting for the run, put back at the end (repeatable)")
    g.add_argument("--keep-logs", action="store_true", help="leave the run's log files on the card")
    g = ap.add_argument_group("the judge")
    g.add_argument("--settle", choices=("followup", "button", "none"), default="followup")
    g.add_argument("--press-at-s", type=float, default=100,
                   help="--settle button: how many seconds past the end of the hour of the return the button is pressed "
                        "(2 = before Home Assistant has written its own rows of that hour, which it does at second 10)")
    g.add_argument("--pull-wait-s", type=float, default=420, help="how long the pull may take after a return")
    g.add_argument("--min-coverage", type=float, default=95, help="percent of the expected samples a parameter must have while away")
    g.add_argument("--gap-s", type=float, default=0, help="a hole in a parameter's rows longer than this is reported (0 = 5 periods, 10 s at least)")
    g.add_argument("--slack-s", type=float, default=2.5, help="how far a row's stamp may be from the moment its value was set")
    g.add_argument("--report-only", default=None, metavar="LOGDIR", help="compare again over a finished run's folder")
    g.add_argument("--logdir", default=None)
    args = ap.parse_args()

    if args.report_only:
        rep = drive_report.analyze(args.report_only)
        for name, ok, detail in rep["checks"]:
            check(name, ok, detail)
        print(f"log dir: {args.report_only}")
        print("HA DRIVE PASS" if not hb.fails else f"HA DRIVE FAIL: {len(hb.fails)} failed: " + "; ".join(hb.fails[:6]))
        return 1 if hb.fails else 0

    def per_trip(text, what):
        try:
            vals = [float(x) for x in str(text).split(",") if x.strip() != ""]
        except ValueError:
            raise SystemExit(f"{what} wants minutes, or a comma list of them: {text!r}")
        return lambda trip: vals[min(trip, len(vals)) - 1]
    home_min, drive_min, park_min = (per_trip(args.home_min, "--home-min"), per_trip(args.drive_min, "--drive-min"),
                                     per_trip(args.park_min, "--park-min"))

    args.logdir = args.logdir or os.path.join(REPO, "test-reports", "logs", "hadrive_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(args.logdir, exist_ok=True)
    inst = HA_INSTALLS[args.ha_install]
    overrides = parse_set(args.set)
    only = [x.strip() for x in args.params.split(",") if x.strip()]
    profile, params = drive_sim.load_profile(args.profile, only=only)
    run = Run(args)
    dut = run.dut
    found = run.found
    t_run0 = time.time()
    started_ha = added_entry = swapped = False
    units_found, units_set, stat_ids = None, False, []
    table_found = table_set = reg_found = None
    files_before = None
    sim_found = {}
    psu = None
    psu_found_v = None
    unexpected0 = None
    ring0 = []
    doc = {"args": {k: v for k, v in vars(args).items() if k not in ("ha_pass",)}, "profile": profile.get("name"),
           "phases": run.phases, "events": run.events, "as_found": found}

    try:
        # ---- d0 the rig ----------------------------------------------------------
        print("== d0 the rig")
        up = wait_reachable(dut, 45)
        check("[d0] the unit answers", up is not None, args.dut)
        if up is None:
            raise RuntimeError("no unit behind --dut (the tunnel?)")
        info = dut.get("/api/info")
        doc["device"] = {k: info.get(k) for k in ("device_id", "fw_version", "fw_build")}
        device_id = info.get("device_id")
        lg = dut.get("/api/logger")
        sd = dut.get("/api/sdcard")
        check("[d0] an SD card the logger can use, a build with the export counters",
              bool(sd.get("mounted")) and "export" in lg, f"sd {sd.get('state')}, fw {info.get('fw_build')}")
        hist0 = dut.get("/api/restart/history")
        unexpected0 = hist0.get("unexpected_resets", 0)
        ring0 = dut.ring_errors() or []
        # as found, before Home Assistant's entry registers its webhook on the unit
        for name in sorted(set(SETTINGS) | set(overrides)):
            found[name] = dut.settings(name)
        reg_found = dut.get("/api/webhook")
        doc["webhook_as_found"] = {k: reg_found.get(k) for k in ("url", "enabled", "interval", "data_mode")}
        table_found = dut.get("/api/autopid/config")
        files_before = dut.log_files()

        sim_net.install()
        sim_base = sim_net.sim_url(args.sim)
        values = drive_sim.sim_values(sim_base)
        absent = [p.name for p in params if drive_sim.sim_lookup(values, p) is None]
        params = [p for p in params if p.name not in absent]
        code, prof = drive_sim.sim_http(sim_base, "/api/ecu/profiles")
        check(f"[d0] the simulator carries the profile's parameters ({len(params)})", bool(params) and not absent,
              f"{sim_base}, its profile {prof.get('active') if isinstance(prof, dict) else '?'}"
              + (f"; NOT in it: {', '.join(absent)}" if absent else ""))
        if not params:
            raise RuntimeError("nothing to drive")
        sim_found = {p.name: drive_sim.sim_lookup(values, p).get("value") for p in params}
        doc["simulator_as_found"] = dict(sim_found)

        if args.away == "ap":
            rc, out = ap_cmd(args, "ap")
            if rc != 0 or "not reachable" in out:
                log("the bench AP's lever daemon is down: starting it (the AP restarts once)")
                hb.pi(args.pi, "bash ~/wican/tools/testbench/pi/bench_ap_net.sh up", timeout=90)
                time.sleep(15)
                # an AP that restarted has an empty station table and the unit
                # keeps an association the AP no longer knows (2026-10-02): the
                # AP off and on again makes it join anew
                ap_cmd(args, "ap off")
                time.sleep(9)
                ap_cmd(args, "ap on")
                back = wait_reachable(dut, 240)
                log(f"the unit is back on the AP after {back:.0f} s" if back is not None else "the unit did NOT come back")
                rc, out = ap_cmd(args, "ap")
            check("[d0] the bench AP's lever answers (ap off / ap on)", rc == 0 and "not reachable" not in out,
                  " ".join(out.split())[:160])
            if rc != 0:
                raise RuntimeError("no lever for the home network: --away push works without it")

        rc, pi_lan = hb.pi(args.pi, "hostname -I | awk '{print $1}'")
        pi_lan = pi_lan.strip().splitlines()[-1]
        user, pw = args.ha_user, args.ha_pass
        if not (user and pw):
            if inst["login"]:
                rc, out = hb.pi(args.pi, f"cat {inst['login']}")
                cred = json.loads(out)
                user, pw = cred.get("username") or cred.get("user"), cred.get("password")
            elif os.path.exists(hb.CRED_FILE):
                with open(hb.CRED_FILE) as f:
                    cred = json.load(f)
                user, pw = cred.get("user"), cred.get("password")
        if not (user and pw):
            raise RuntimeError("no Home Assistant login: --ha-user / --ha-pass")
        ha = run.ha = HaLong(args.ha or f"http://{pi_lan}:8123", user, pw)
        rc, active = hb.pi(args.pi, f"systemctl is-active {inst['unit']}")
        # a copy of the integration for this run: in before the Home Assistant starts
        cc = f"{inst['config']}/custom_components"
        rc2, left = hb.pi(args.pi, f"test -d {cc}/wican.installed && echo left")
        if "left" in left and active.strip() != "active":
            hb.pi(args.pi, f"rm -rf {cc}/wican && mv {cc}/wican.installed {cc}/wican")
            log("an earlier run left its copy of the integration in place: the installed one is back")
        if args.integration:
            src = os.path.abspath(args.integration)
            if not os.path.isfile(os.path.join(src, "manifest.json")):
                raise RuntimeError(f"--integration: no manifest.json in {src}")
            if active.strip() == "active":
                raise RuntimeError(f"--integration: {inst['unit']} is running; this run must be the one that starts it")
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                tar.add(src, arcname="wican", filter=lambda ti: None if "__pycache__" in ti.name else ti)
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", args.pi,
                                f"rm -rf {cc}/wican.run && mkdir -p {cc}/wican.run && tar xzf - -C {cc}/wican.run && "
                                f"mv {cc}/wican {cc}/wican.installed && mv {cc}/wican.run/wican {cc}/wican && rmdir {cc}/wican.run"],
                               input=buf.getvalue(), capture_output=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"--integration: the copy to the Pi failed: {(r.stdout + r.stderr).decode(errors='replace')[:200]}")
            swapped = True
            log(f"the integration for this run: {src} ({len(buf.getvalue()) // 1024} KB), the installed one set aside")
        if active.strip() != "active":
            log(f"{inst['unit']} is not running: starting it for this run")
            hb.pi(args.pi, f"sudo -n systemd-run --unit={inst['unit']} --collect -p User=meatpi "
                           f"-p WorkingDirectory=/home/meatpi {inst['bin']} -c {inst['config']}")
            started_ha = True
        t0 = time.time()
        while time.time() - t0 < 240 and not ha.up():
            time.sleep(5)
        check("[d0] Home Assistant answers", ha.up(), f"{ha.base} ({args.ha_install}: {inst['unit']})")
        if not ha.up():
            raise RuntimeError("no Home Assistant")
        if started_ha:
            time.sleep(20)              # the integrations finish loading behind the first answer
        ha.relogin()
        code, cfg = ha.call("/api/config")
        doc["ha"] = {"install": args.ha_install, "version": cfg.get("version") if isinstance(cfg, dict) else None,
                     "base": ha.base, "integration_from": os.path.abspath(args.integration) if args.integration else "installed"}
        rc, manifest = hb.pi(args.pi, f"cat {inst['config']}/custom_components/wican/manifest.json")
        try:
            doc["ha"]["integration"] = json.loads(manifest).get("version")
        except ValueError:
            pass
        if isinstance(cfg, dict) and isinstance(cfg.get("unit_system"), dict):
            units_found = "metric" if cfg["unit_system"].get("temperature") == "°C" else "us_customary"
            doc["ha"]["units"] = units_found
        titles = (args.dut_ip, f"wican_{device_id}.local")
        mine = [e for e in ha.entries() if e.get("title") in titles]
        if args.ha_units != "as-found" and units_found and args.ha_units != units_found:
            ha.ws_call({"type": "config/core/update", "unit_system": args.ha_units})
            units_set = True
            doc["ha"]["units"] = args.ha_units
            log(f"Home Assistant's unit system for the run: {args.ha_units} (found: {units_found})"
                + ("; the entry was there already, its sensors keep the units they were created with" if mine else ""))
        if mine:
            run.entry_id = mine[0]["entry_id"]
        else:
            log(f"no entry for {args.dut_ip}: adding it through the config flow")
            run.entry_id = ha.add_entry(args.dut_ip)
            added_entry = True
            time.sleep(8)
        entry_id = run.entry_id
        doc["ha"]["entry_id"] = entry_id
        check("[d0] a config entry for this unit", bool(entry_id), f"{entry_id}{' (added by this run)' if added_entry else ''}")
        if args.clear_marks and not added_entry:
            rc, out = hb.pi(args.pi, f"rm -fv {inst['config']}/.storage/wican.history_{entry_id}")
            log(f"the integration's marks cleared ({out.strip() or 'none stored'}); the entry reloaded")
            ha.reload(entry_id)
            time.sleep(8)
        t0 = time.time()
        reg = None
        reloaded = False
        while time.time() - t0 < 150:
            code, reg = dut.api("/api/webhook")
            if code == 200 and reg.get("url") and reg.get("success_count", 0) > 0:
                break
            if time.time() - t0 > 60 and not reloaded and not added_entry:
                log("no push yet: the entry reloaded (it registers its webhook on the unit again)")
                ha.reload(entry_id)
                reloaded = True
            time.sleep(5)
        ok = isinstance(reg, dict) and bool(reg.get("url")) and reg.get("success_count", 0) > 0
        check("[d0] the webhook registered on the unit and a push delivered", ok,
              json.dumps({k: reg.get(k) for k in ("url", "interval", "data_mode", "status", "success_count", "fail_count")})
              if isinstance(reg, dict) else str(reg))
        if not ok:
            raise RuntimeError("no push reaches Home Assistant")
        interval = int(reg.get("interval") or 15)
        stale_s = max(interval * 5, 120)
        doc["push"] = {"interval_s": interval, "stale_s": stale_s, "url": reg.get("url"), "data_mode": reg.get("data_mode")}
        clock = doc["clock"] = {"dut_minus_pc_start": dut_clock_offset(dut), "pi_minus_pc_start": pi_clock_offset(args.pi)}
        check("[d0] the unit's and the Pi's clocks within 60 s",
              abs(clock["dut_minus_pc_start"] - clock["pi_minus_pc_start"]) < 60,
              f"unit {clock['dut_minus_pc_start']:+.2f} s, Pi {clock['pi_minus_pc_start']:+.2f} s against this PC")

        # ---- d1 configure --------------------------------------------------------
        print("== d1 configure")
        want = {}
        dl = dict(found["data_logger"])
        dl.update({"enabled": True, "format": "jsonl", "autopid_log": args.log_mode})
        want["data_logger"] = dl
        apd = dict(found["autopid"])
        apd["enabled"] = True
        want["autopid"] = apd
        for comp, kv in overrides.items():
            base = dict(want.get(comp, found[comp]))
            unknown = [k for k in kv if k not in base]
            if unknown:
                raise RuntimeError(f"--set: {comp} has no key {', '.join(unknown)}")
            base.update(kv)
            want[comp] = base
        staged = [c for c, d in want.items() if d != found[c]]
        for comp in staged:
            dut.put_settings(comp, want[comp])
        doc["settings_for_the_run"] = {c: {k: v for k, v in want[c].items() if found[c].get(k) != v} for c in staged}
        log("settings for the run: " + (json.dumps(doc["settings_for_the_run"]) if staged else "none to change"))
        if staged:
            check("[d1] the settings applied and the planned restart", dut.submit_and_wait())
            t_back = time.time()
            t0 = time.time()
            while time.time() - t0 < 120:
                code, scan = dut.api("/api/autopid/std_scan")
                if code == 200 and scan.get("status") == "idle" and time.time() - t_back >= 25:
                    break
                time.sleep(3)
        have = set(table_names(table_found))
        mode = args.table
        if mode == "auto":
            mode = "keep" if all(p.name in have for p in params) else "profile"
        if mode == "keep":
            outside = [p.name for p in params if p.name not in have]
            params = [p for p in params if p.name in have]
            if outside:
                log(f"not in the unit's table, not driven: {', '.join(outside)}")
            if args.period_ms:
                table_set = json.loads(json.dumps(table_found))
                for grp in table_set.get("groups", []):
                    grp["period_ms"] = args.period_ms
        else:
            norow = [p.name for p in params if not p.row]
            if norow:
                raise RuntimeError(f"the profile has no row for {', '.join(norow)} (--table keep?)")
            table_set = build_table(params, args.period_ms or int(profile.get("group_period_ms", 1000)))
        if table_set is not None:
            code, r = dut.api("/api/autopid/config", "PUT", table_set)
            check(f"[d1] the AutoPID table for the run ({mode})", code == 200, f"{code} {str(r)[:100]}")
        table_now = dut.get("/api/autopid/config")
        period_ms = max([int(grp.get("period_ms") or 1000) for grp in table_now.get("groups", [])] or [1000])
        logged = table_names(table_now)
        rows_per_hour = int(len(logged) * 3600 * 1000 / max(1, period_ms)) if args.log_mode == "all" else None
        doc["table"] = {"mode": mode, "period_ms": period_ms, "parameters": logged, "rows_per_hour": rows_per_hour}
        log(f"the table ({mode}): {len(logged)} parameter(s) at {period_ms} ms, {len(params)} of them driven"
            + (f"; at most {rows_per_hour} rows an hour by that period (a table that takes longer for a lap gives fewer)"
               if rows_per_hour else ""))
        if rows_per_hour and rows_per_hour > 200000:
            log("NOTE: that can be more than 200,000 rows an hour: the integration reads at most that many per run")
        lg = dut.get("/api/logger")
        check("[d1] the logger runs as JSON lines on the card",
              bool(lg.get("enabled")) and bool(lg.get("storage_ok")) and lg.get("format") == "jsonl",
              json.dumps({k: lg.get(k) for k in ("enabled", "running", "storage_ok", "format", "autopid_log", "file")}))
        doc["logger_start"] = {k: lg.get(k) for k in ("written", "dropped", "errors", "file", "export")}

        # the drive begins: the values move from here to the end of the run
        t_drive0 = time.time()
        sim = run.sim = drive_sim.DriveSim(sim_base, params, os.path.join(args.logdir, "truth.jsonl"),
                                           step_s=args.step_s, t0=t_drive0)
        sim.start()
        doc["params"] = [{"name": p.name, "sim": f"{p.mode}/{p.pid}/{p.key}", "tol": p.tol, "step": p.step,
                          "scale": p.scale, "offset": p.offset, "pattern": p.pattern} for p in params]
        doc["t_drive0"] = round(t_drive0, 2)
        # each driven parameter read back through the unit once: the row decodes the value set
        t0 = time.time()
        good, fast = set(), {}
        while time.time() - t0 < 45 and len(good) < len(params):
            time.sleep(3)
            before = dict(sim.last)
            code, apst = dut.api("/api/autopid")
            now_vals = {x.get("name"): x.get("value") for x in (apst.get("params") or [])} if code == 200 else {}
            for p in params:
                # a value set while the answer was on its way may be either one
                got = now_vals.get(p.name)
                wants = {before.get(p.name), sim.last.get(p.name)} - {None}
                if got is not None and any(abs(got - p.expected(w)) <= p.tol + 1e-9 for w in wants):
                    good.add(p.name)
                    fast.pop(p.name, None)
                elif p.name not in good:
                    fast[p.name] = (sim.last.get(p.name), got)
        check(f"[d1] the unit reads every driven parameter as the simulator holds it ({len(params)})", not fast,
              "; ".join(f"{n}: set {v[0]}, read {v[1]}" for n, v in list(fast.items())[:5]))
        doc["read_back_failed"] = sorted(fast)
        # Home Assistant has a sensor for every parameter once a push carried it
        t0 = time.time()
        pids = {}
        while time.time() - t0 < max(90, interval * 4):
            pids, _ = ha.registry(entry_id)
            if all(n in pids for n in logged):
                break
            time.sleep(6)
        nosensor = [n for n in logged if n not in pids]
        check(f"[d1] Home Assistant has a sensor for each of the {len(logged)} parameters", not nosensor,
              ("none for: " + ", ".join(nosensor[:8])) if nosensor else "")
        stat_ids = sorted(v["entity_id"] for v in pids.values() if v.get("entity_id"))
        batt_entity = ha.device_sensor(entry_id, "batt_voltage")
        if batt_entity:
            stat_ids.append(batt_entity)        # the unit's own battery voltage (in its log since 6.0.0-beta.15)
        if args.clear_stats and stat_ids:
            ha.ws_call({"type": "recorder/clear_statistics", "statistic_ids": stat_ids})
            log(f"the statistics Home Assistant held for {len(stat_ids)} sensor(s) deleted")
        run.stat_ids = list(stat_ids)
        run.stat_start = (int((t_drive0 + clock["dut_minus_pc_start"]) // 3600)) * 3600 - 3600
        run.mon = Monitor(run)
        run.mon.start()

        # ---- d2 the trips ----------------------------------------------------------
        scenario_error = None
        pi_off = clock["pi_minus_pc_start"]
        home_since = t_drive0
        try:
            cut = False
            home_since = t_drive0
            t_last_return = None
            for trip in range(1, args.trips + 1):
                print(f"== d2 trip {trip} of {args.trips}")
                run.event("at home", trip=trip)
                cut = run.wait(home_min(trip) * 60, f"trip {trip}, at home", stoppable=True)
                if trip == 1 and args.leave_at_minute >= 0 and not cut:
                    off = clock["dut_minus_pc_start"]
                    while time.gmtime(time.time() + off).tm_min != args.leave_at_minute % 60 and not cut:
                        cut = run.wait(5, f"trip {trip}, at home until minute {args.leave_at_minute:02d}", stoppable=True)
                if cut:
                    break
                cnt_home = dut.get("/api/webhook").get("success_count", 0)
                fin_before = hb.last_finished(ha, entry_id)
                lg_leave = dut.get("/api/logger")
                # away
                run.mon.reachable = args.away != "ap"
                if args.away == "ap":
                    rc, out = ap_cmd(args, "ap off")
                else:
                    wreg = dut.get("/api/webhook")
                    code, out = dut.api("/api/webhook", "POST", {"url": wreg["url"], "enabled": False,
                                                                  "interval": wreg.get("interval", interval),
                                                                  "manual_override": False})
                    rc = 0 if code in (200, 201) else 1
                t_leave = time.time()
                run.phases.append({"kind": "home", "trip": trip, "t0": home_since, "t1": t_leave})
                run.event("left home", trip=trip, how=args.away)
                time.sleep(8)
                if args.away == "ap":
                    code, _ = dut.api("/api/info", timeout=5, tries=1)
                    check(f"[trip {trip}] the home network is gone for the unit", rc == 0 and code != 200,
                          f"lever rc {rc}, the unit's answer {code}")
                else:
                    check(f"[trip {trip}] the pushes switched off", rc == 0, str(out)[:80])
                if drive_min(trip) * 60 <= stale_s + 30:
                    log(f"NOTE: the drive is shorter than the integration's silence window ({stale_s} s): it will see no gap")
                cut = run.wait(drive_min(trip) * 60 - 8, f"trip {trip}, away", stoppable=True)
                # home again
                if args.away == "ap":
                    rc, out = ap_cmd(args, "ap on")
                else:
                    wreg = dut.get("/api/webhook")
                    code, out = dut.api("/api/webhook", "POST", {"url": wreg["url"], "enabled": True,
                                                                  "interval": wreg.get("interval", interval),
                                                                  "manual_override": False})
                    rc = 0 if code in (200, 201) else 1
                t_return = time.time()
                run.phases.append({"kind": "away", "trip": trip, "t0": t_leave, "t1": t_return})
                home_since = t_last_return = t_return
                run.event("home again", trip=trip)
                back = wait_reachable(dut, 300)
                run.mon.reachable = back is not None
                check(f"[trip {trip}] the unit is back on the home network", back is not None,
                      f"{back:.0f} s after the network returned" if back is not None else "not within 300 s")
                if back is None:
                    raise RuntimeError("the unit did not come home")
                run.event("the unit answers again", trip=trip, after_s=round(back))
                lg_back = dut.get("/api/logger")
                grew = lg_back.get("written", 0) - lg_leave.get("written", 0)
                log(f"while away the logger wrote {grew} rows (dropped {lg_back.get('dropped')}, errors {lg_back.get('errors')})")
                t0 = time.time()
                pushed = None
                while time.time() - t0 < 120:
                    wreg = dut.get("/api/webhook")
                    if wreg.get("success_count", 0) > cnt_home:
                        pushed = time.time() - t_return
                        break
                    time.sleep(3)
                check(f"[trip {trip}] a push reached Home Assistant after the return", pushed is not None,
                      f"within {pushed:.0f} s" if pushed is not None else json.dumps({k: wreg.get(k) for k in ("status", "success_count", "fail_count", "last_error")}))
                # a park: the engine goes off a fixed time after the return, whether
                # the pull has run by then or not; the unit sleeps by its own
                # settings; the supply comes back later (the next start)
                parking = park_min(trip) > 0 and not cut
                t_park = None
                pull_wait = args.pull_wait_s
                if parking:
                    run.wait(max(0.0, args.park_delay_min * 60 - (time.time() - t_return)), f"trip {trip}, engine still on")
                    if psu is None:
                        from bench_psu import open_psu  # noqa: E402
                        psu = open_psu(args.psu)
                        psu_found_v = round(float(psu.meas_voltage()), 1)
                        code, pw_doc = drive_sim.sim_http(sim_base, "/api/power")
                        if code == 200 and isinstance(pw_doc, dict) and pw_doc.get("set_mv"):
                            psu_found_v = pw_doc["set_mv"] / 1000.0
                    psu.set_voltage(args.park_v)
                    if args.freeze_parked:
                        sim.freeze(True)
                    t_park = time.time()
                    run.phases.append({"kind": "home", "trip": trip, "t0": home_since, "t1": t_park})
                    run.event("parked: the supply down", trip=trip, volts=args.park_v)
                    pull_wait = min(pull_wait, park_min(trip) * 60)
                hs = hb.wait_sync(ha, entry_id, fin_before, pull_wait, f"trip {trip}")
                if hs:
                    run.mon.note_sync(hs)
                check(f"[trip {trip}] the integration pulled the log after the return" + (" (before the park ended)" if parking else ""),
                      hs is not None,
                      f"finished {hs['finished_at'] - (t_return + clock['pi_minus_pc_start']):.0f} s after the return: "
                      + json.dumps({k: hs.get(k) for k in ("pages", "rows_fetched", "rows_used", "rows_deferred", "hours_imported", "pids_imported", "errors")})
                      if hs else f"no run within {pull_wait:.0f} s")
                run.event("the pull after the return", trip=trip, ran=hs is not None,
                          **({k: hs.get(k) for k in ("rows_fetched", "rows_deferred", "hours_imported", "followup_in")} if hs else {}))
                if hs:
                    check(f"[trip {trip}] that pull ended without an error", not hs.get("errors"), json.dumps(hs.get("errors")))
                if parking:
                    run.wait(max(0.0, park_min(trip) * 60 - (time.time() - t_park)), f"trip {trip}, parked")
                    code, sl = dut.api("/api/sleep", timeout=4, tries=1)
                    asleep = code != 200
                    log("at the end of the park the unit " + ("does not answer (asleep)" if asleep else
                                                              f"is awake: {json.dumps({k: sl.get(k) for k in ('state', 'pending', 'sleep_in_s', 'voltage')})}"))
                    # the hour of the return ended while the unit slept: an
                    # integration that keeps the pulled rows (2.0.0-beta2) has
                    # imported it from them by now, without the unit
                    keeps = any("hours_from_kept_copy" in s for s in run.mon.syncs)
                    hour_end_ha = (int((t_return + pi_off) // 3600) + 1) * 3600
                    if keeps and asleep and hs and hs.get("rows_deferred") and time.time() + pi_off > hour_end_ha + 150:
                        check(f"[trip {trip}] the hour of the return was imported while the unit slept, from the rows the pull had read",
                              run.mon.kept_seen, json.dumps([{k: s.get(k) for k in ("ran", "hours_imported", "hours_from_kept_copy", "errors")}
                                                             for s in run.mon.syncs[-2:]]))
                    fin_before = hb.last_finished(ha, entry_id)
                    psu.set_voltage(psu_found_v)
                    sim.freeze(False)
                    t_wake = time.time()
                    run.phases.append({"kind": "parked", "trip": trip, "t0": t_park, "t1": t_wake, "slept": asleep})
                    home_since = t_wake
                    run.event("the next start: the supply back", trip=trip, volts=psu_found_v, was_asleep=asleep)
                    back = wait_reachable(dut, 240)
                    check(f"[trip {trip}] the unit answers after the park", back is not None,
                          f"{back:.0f} s after the supply came back" if back is not None else "not within 240 s")
                    if asleep and back is not None:
                        hs = hb.wait_sync(ha, entry_id, fin_before, args.pull_wait_s, f"trip {trip} wake")
                        if hs:
                            run.mon.note_sync(hs)
                        check(f"[trip {trip}] the integration pulled the log after the wake", hs is not None,
                              json.dumps({k: hs.get(k) for k in ("rows_fetched", "rows_deferred", "hours_imported", "errors")}) if hs else "no run")
                if cut:
                    break
            t_back0 = t_last_return if t_last_return is not None else time.time()
            run.wait(max(0.0, args.back_min * 60 - (time.time() - home_since)), "at home after the last return")

            # ---- d3 settle ---------------------------------------------------------------
            print(f"== d3 settle ({args.settle})")
            if args.settle != "none":
                # the hour the last return fell in is imported once it has ended
                ha_now = time.time() + pi_off
                hour_end = (int((t_back0 + pi_off) // 3600) + 1) * 3600
                if args.settle == "followup":
                    due = hour_end + 90
                    log(f"the hour of the return ends in {max(0, hour_end - ha_now) / 60:.1f} min; the integration's follow-up is due 90 s later")
                    run.wait(max(0.0, due - 20 - (time.time() + pi_off)), "the hour of the return")
                    t0 = time.time()
                    came = None
                    while time.time() - t0 < 300 and came is None:
                        hs = (ha.diagnostics(entry_id) or {}).get("history_sync") or {}
                        if hs.get("finished_at") and hs["finished_at"] >= hour_end:
                            came = hs
                            run.mon.note_sync(hs)
                        else:
                            time.sleep(5)
                    check("[d3] the follow-up for the hour of the return ran by itself", came is not None,
                          json.dumps({k: came.get(k) for k in ("rows_fetched", "rows_used", "rows_deferred", "hours_imported", "pids_imported", "errors")})
                          if came else "no run within 5 min of its due time")
                    run.event("the follow-up past the hour", ran=came is not None)
                    doc["settle"] = {"mode": "followup", "ran_by_itself": came is not None}
                    if came is None:
                        log("the button is pressed instead, so the comparison can tell data that cannot be imported from a run that did not come")
                else:
                    run.wait(max(0.0, hour_end + args.press_at_s - (time.time() + pi_off)), "the hour of the return")
                    came = None
                    doc["settle"] = {"mode": "button", "press_at_s": args.press_at_s}
                if came is None:
                    fin_before = hb.last_finished(ha, entry_id)
                    btn = next((e for e in ha.registry(entry_id)[1] if e and e.startswith("button.") and e.endswith("_catch_up_history")), None)
                    code, _ = ha.press(btn) if btn else (0, None)
                    hs = hb.wait_sync(ha, entry_id, fin_before, 180, "button") if code == 200 else None
                    if hs:
                        run.mon.note_sync(hs)
                    check("[d3] the Catch up history button ran a pull", hs is not None, f"{btn} {code}")
                    run.event("the button's pull", ran=hs is not None)
                # the recorder's statistics job: Home Assistant writes an hour's
                # rows at its second 10, a five-minute period's 10 s after it ends
                # (and an integration that waits for that itself imports the hour
                # with its follow-up, 90 s past the hour: a press before that is
                # compared after it)
                time.sleep(max(15.0, hour_end + (170 if args.press_at_s < 90 else 40) - (time.time() + pi_off)))
            else:
                doc["settle"] = {"mode": "none"}
        except Exception as e:
            # what was recorded until here is still compared
            scenario_error = e
            check("[run] the scenario ran to its end", False, f"{type(e).__name__}: {e}")
            if args.away == "ap":
                ap_cmd(args, "ap on")
            if psu is not None and psu_found_v:
                psu.set_voltage(psu_found_v)
            if wait_reachable(dut, 300) is None:
                raise RuntimeError("the unit does not answer: nothing to compare")
            run.mon.reachable = True

        # ---- d4 collect and compare ----------------------------------------------------
        print("== d4 collect and compare")
        t_end = time.time()
        run.phases.append({"kind": "home", "trip": args.trips, "t0": home_since, "t1": t_end})
        sim.stop()
        sim.join(10)
        run.mon.halt.set()
        doc["t_end"] = round(t_end, 2)
        doc["sim"] = {"sent": sim.sent, "failed": sim.failed, "last_error": sim.last_error}
        check("[d4] the simulator took every value", sim.failed == 0 and sim.sent > 0,
              f"{sim.sent} set, {sim.failed} refused {sim.last_error}")
        clock["dut_minus_pc_end"] = dut_clock_offset(dut)
        clock["pi_minus_pc_end"] = pi_clock_offset(args.pi)
        lg = dut.get("/api/logger")
        doc["logger_end"] = {k: lg.get(k) for k in ("written", "dropped", "errors", "file", "export")}
        check("[d4] the logger dropped no row and met no error",
              lg.get("dropped", 0) == doc["logger_start"].get("dropped", 0) and lg.get("errors", 0) == doc["logger_start"].get("errors", 0),
              json.dumps({k: lg.get(k) for k in ("written", "dropped", "errors")}))
        since = (int((t_drive0 + clock["dut_minus_pc_start"]) // 3600)) * 3600
        rows, pages, files = export_device_log(dut, since, os.path.join(args.logdir, "device_rows.jsonl"))
        log(f"the unit's log: {rows} rows in {pages} page(s) from {len(files)} file(s)")
        # the route reads its query values as a browser or a script library
        # writes them (6.0.0-beta.15): a cursor whose colon comes as %3A is the
        # same cursor, an encoded name the same name
        try:
            exp = f"{dut.base}/api/logger/export?stream=params&limit=5"
            code, page1 = fetch_text(f"{exp}&since={since}", timeout=30)
            cur = next((json.loads(ln).get("_cursor") for ln in page1.splitlines() if ln.startswith('{"_cursor"')), None)
            code_a, raw_page = fetch_text(f"{exp}&since={cur}", timeout=30)
            code_b, enc_page = fetch_text(f"{exp}&since={urllib.parse.quote(str(cur), safe='')}", timeout=30)
            check("[d4] the export route takes a cursor whose colon is written %3A (the next rows, not the first page again)",
                  bool(cur) and ":" in str(cur) and code_a == code_b == 200 and raw_page == enc_page and raw_page != page1,
                  f"cursor {cur}; raw and encoded pages {'the same' if raw_page == enc_page else 'DIFFER'}"
                  + ("; the encoded cursor gave the first page again" if enc_page == page1 else ""))
            some = (logged or ["EngineRPM"])[0]
            code_a, raw_page = fetch_text(f"{exp}&since={since}&name={some}", timeout=30)
            code_b, enc_page = fetch_text(f"{exp}&since={since}&name=" + "".join(f"%{ord(ch):02X}" for ch in some), timeout=30)
            check("[d4] the export route takes an encoded parameter name",
                  code_a == code_b == 200 and raw_page == enc_page and f'.{some}"' in raw_page, f"name {some}")
        except Exception as e:  # noqa: BLE001
            check("[d4] the export route's query values could be probed", False, f"{type(e).__name__}: {e}")
        pids, every = ha.registry(entry_id)
        doc["entities"] = pids
        ids = sorted(v["entity_id"] for v in pids.values() if v.get("entity_id"))
        batt_entity = ha.device_sensor(entry_id, "batt_voltage")
        doc["device_entities"] = {"batt_voltage": batt_entity} if batt_entity else {}
        if batt_entity:
            ids.append(batt_entity)
        start = since - 3600
        for period, name in (("hour", "ha_stats_hour.json"), ("5minute", "ha_stats_5min.json")):
            with open(os.path.join(args.logdir, name), "w", encoding="utf-8") as f:
                json.dump(ha.stats(ids, start, period), f)
        states = []
        for i in range(0, len(ids), 12):
            states.extend(ha.states(ids[i:i + 12], t_drive0 + pi_off - 120, t_end + pi_off + 60))
        with open(os.path.join(args.logdir, "ha_states.json"), "w", encoding="utf-8") as f:
            json.dump(states, f)
        table_units = {par.get("name"): par for pid in table_now.get("pids", []) for par in pid.get("parameters", [])}
        ha_units = ha.units(ids)
        doc["units"] = {n: {"wican": (table_units.get(n) or {}).get("unit"), "class": (table_units.get(n) or {}).get("class"),
                            "state": (ha_units.get(v.get("entity_id")) or {}).get("state"),
                            "statistics": (ha_units.get(v.get("entity_id")) or {}).get("statistics")}
                        for n, v in pids.items()}
        if batt_entity:
            doc["units"]["batt_voltage"] = {"wican": "V", "class": "voltage",
                                            "state": (ha_units.get(batt_entity) or {}).get("state"),
                                            "statistics": (ha_units.get(batt_entity) or {}).get("statistics")}
        doc["syncs"] = run.mon.syncs
        # Home Assistant's own hourly statistics, of a sensor that is not the unit's
        ha_now = time.time() + clock["pi_minus_pc_end"]
        h0 = int((run.mon.t_start + 30 + pi_off) // 3600) * 3600
        full = list(range(h0, int(ha_now - 45) // 3600 * 3600, 3600))    # the hours it had states in that are over for 45 s
        got = {int(r["start"] / 1000 if r["start"] > 1e11 else r["start"])
               for r in (ha.stats([CONTROL_ENTITY], h0 - 3600, "hour") or {}).get(CONTROL_ENTITY, [])
               if isinstance(r.get("start"), (int, float))}
        missing = [h for h in full if h not in got]
        rc, blocked = hb.pi(args.pi, f"grep -c 'Blocked attempt to insert duplicated statistic' {inst['config']}/home-assistant.log")
        blocked = int(blocked.strip().splitlines()[-1]) if blocked.strip().splitlines()[-1:] and blocked.strip().splitlines()[-1].isdigit() else -1
        doc["control"] = {"entity": CONTROL_ENTITY, "whole_hours": full, "missing": missing, "blocked_lines": blocked}
        if full:
            check(f"[d4] Home Assistant's own hourly statistics are whole: a sensor that is not the unit's has its row for "
                  f"every hour of the run that is over ({len(full) - len(missing)} of {len(full)})", not missing,
                  "no row for " + ", ".join(time.strftime("%H:%MZ", time.gmtime(h)) for h in missing) if missing else "")
        check("[d4] Home Assistant's log has no 'Blocked attempt to insert duplicated statistic rows'", blocked == 0,
              f"{blocked} line(s)" if blocked > 0 else ("the log could not be read" if blocked < 0 else ""))
        doc["t_validate_ha"] = round(time.time() + clock["pi_minus_pc_end"], 1)
        doc["judge"] = {"min_coverage": args.min_coverage, "gap_s": args.gap_s, "slack_s": args.slack_s,
                        "log_mode": args.log_mode}
        with open(os.path.join(args.logdir, "run.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        rep = drive_report.analyze(args.logdir)
        for name, ok, detail in rep["checks"]:
            check(name, ok, detail)
        for note in rep.get("notes", []):
            log("NOTE: " + note)

    except Exception as e:
        check("[run] no exception", False, f"{type(e).__name__}: {e}")
    finally:
        # ---- d5 restore -------------------------------------------------------------------
        print("== d5 restore")
        try:
            if run.sim is not None:
                run.sim.stop()
            if run.mon is not None:
                run.mon.halt.set()
            if psu is not None and psu_found_v:
                psu.set_voltage(psu_found_v)
            if args.away == "ap":
                ap_cmd(args, "ap on")
            if not os.path.exists(os.path.join(args.logdir, "run.json")):
                doc["syncs"] = run.mon.syncs if run.mon else []
                with open(os.path.join(args.logdir, "run.json"), "w", encoding="utf-8") as f:
                    json.dump(doc, f, indent=1)
            back = wait_reachable(dut, 300)
            if back is None:
                raise RuntimeError("the unit does not answer: NOTHING was restored on it")
            if sim_found:
                left = 0
                for p in params:
                    if sim_found.get(p.name) is not None:
                        code, _ = drive_sim.sim_http(sim_net.sim_url(args.sim), p.sim_path, "POST", {p.key: sim_found[p.name]})
                        left += code != 200
                log(f"the simulator's values as found ({len(params) - left} of {len(params)})")
            ring_end = dut.ring_errors()
            census = ring_e_tags(dut)
            if census:
                log("E lines in the log ring now, by tag: " + ", ".join(f"{t} {n}" for t, n in sorted(census.items())))
            new_e = [ln for ln in (ring_end or []) if ln not in ring0]
            check("[d5] no E line from the logger, the pushes, AutoPID or the sleep manager", ring_end is not None and not new_e,
                  "; ".join(new_e[:4]))
            if table_set is not None and table_found is not None:
                code, r = dut.api("/api/autopid/config", "PUT", table_found)
                check("[d5] the AutoPID table as found", code == 200, str(code))
            if run.ha is not None and not args.keep_ha:
                try:
                    run.ha.call(f"/api/states/{CONTROL_ENTITY}", method="DELETE")
                    run.ha.ws_call({"type": "recorder/clear_statistics", "statistic_ids": [CONTROL_ENTITY]})
                except Exception as e:  # noqa: BLE001
                    log(f"the bench's control sensor was not removed: {type(e).__name__}: {e}")
            if run.ha is not None and run.entry_id and added_entry and not args.keep_ha:
                code, r = run.ha.call(f"/api/config/config_entries/entry/{run.entry_id}", method="DELETE")
                log(f"the entry this run added is removed again: {code}")
                time.sleep(5)
            if units_set and run.ha is not None and not args.keep_ha:
                if stat_ids:
                    run.ha.ws_call({"type": "recorder/clear_statistics", "statistic_ids": stat_ids})
                run.ha.ws_call({"type": "config/core/update", "unit_system": units_found})
                log(f"Home Assistant's unit system as found ({units_found}); the statistics of the run's sensors deleted "
                    "(kept in the run's units, they would bend the next run)")
            elif units_set:
                log(f"Home Assistant's unit system left at {args.ha_units} (--keep-ha): found {units_found}")
            if reg_found is not None and not reg_found.get("url") and not args.keep_ha:
                code, wreg = dut.api("/api/webhook")
                if code == 200 and wreg.get("url"):
                    code, _ = dut.api("/api/webhook", "DELETE")
                    log(f"the webhook registration deleted (none was there before): {code}")
            changed = False
            for name, was in found.items():
                if name == "ha_webhooks" and args.keep_ha:
                    continue
                if was != dut.settings(name):
                    dut.put_settings(name, was)
                    changed = True
            if changed:
                check("[d5] the settings as found and the planned restart", dut.submit_and_wait())
            if files_before is not None and not args.keep_logs:
                gone = 0
                for name in dut.log_files():
                    if name not in files_before:
                        code, _ = dut.api("/api/fs/file?path=" + urllib.parse.quote("/sd/logs/" + name), "DELETE")
                        gone += code in (200, 204)
                log(f"{gone} log file(s) of this run deleted from the card")
            if unexpected0 is not None:
                hist1 = dut.get("/api/restart/history")
                check("[d5] no unexpected reset", hist1.get("unexpected_resets", 0) == unexpected0,
                      f"{unexpected0} -> {hist1.get('unexpected_resets')}, planned restarts by this run: {dut.restarts}")
            if started_ha and not args.keep_ha:
                hb.pi(args.pi, f"sudo -n systemctl stop {inst['unit']}")
                log(f"{inst['unit']} stopped (this run started it)")
            elif args.keep_ha and run.ha is not None:
                log(f"Home Assistant left running at {run.ha.base} with the entry in place (--keep-ha)")
            if swapped:
                cc = f"{inst['config']}/custom_components"
                if args.keep_ha:
                    log(f"the run's copy of the integration stays in place (--keep-ha): the installed one is {cc}/wican.installed, "
                        "the next run puts it back")
                else:
                    time.sleep(3)
                    rc, out = hb.pi(args.pi, f"test -d {cc}/wican.installed && rm -rf {cc}/wican && mv {cc}/wican.installed {cc}/wican "
                                             f"&& cat {cc}/wican/manifest.json | grep -c version")
                    check("[d5] the installed integration is back in place", rc == 0, out.strip()[:80])
        except Exception as e:
            check("[d5] restore", False, f"{type(e).__name__}: {e}")
        with open(os.path.join(args.logdir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump({"fails": hb.fails, "secs": round(time.time() - t_run0)}, f, indent=1)
        print(f"log dir: {args.logdir}")
        if hb.fails:
            print(f"HA DRIVE FAIL: {len(hb.fails)} failed: " + "; ".join(hb.fails[:6]))
        else:
            print("HA DRIVE PASS")
    return 1 if hb.fails else 0


if __name__ == "__main__":
    sys.exit(main())
