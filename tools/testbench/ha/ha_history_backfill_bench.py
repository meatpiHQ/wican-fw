#!/usr/bin/env python3
"""Store and forward: the drive recorded on the SD card reaches Home Assistant.

The scenario (Ali, 2026-11-06): the car is on the road, WiCAN reads and
records the vehicle parameters; back home it joins the home WiFi, the Home
Assistant integration pulls what was recorded and imports it into each
sensor's long-term statistics before the device goes to sleep. Against the
bench HA on rpi001 (`ha-bench.service`, the integration copied from the
`meatpi-multi-device` branch on rpi002) and the bench WiCAN PRO on the
bench AP, with the ECU simulator answering the custom row the bench adds.

Legs (every check prints PASS/FAIL):
  b0  the rig: the DUT answers with the export block of this build, the bench
      HA is up (started here when it is not; stopped at the end then), an
      access token through the owner login, the config entry for this device
      (added through the config flow when missing), the webhook registered on
      the device with the bench AP's address and a push delivered, the DUT's
      and the Pi's clocks within 60 s
  b1  configure: the logger on as JSON lines with every AutoPID sample, one
      custom AutoPID row (EngineRPM, 010C, the simulator answers it), the
      /ws/cli channel for the seeding command; one planned restart; the push
      then carries EngineRPM and Home Assistant has its sensor
  b2  on the road: a known series is recorded two hours back through
      `logger test` (explicit stamps, i * 0.5 one second apart, 1200 rows
      inside one complete hour), then the device's pushes are switched off
      (POST /api/webhook enabled:false, applied live) for longer than the
      integration's staleness window
  b3  back home: the pushes are switched on again; the first push ends the
      gap and the integration pulls: its diagnostics say so (source export,
      the rows, the hours, the cursor, no errors), the recorder holds the
      seeded hour's mean/min/max exactly, the device's export counters moved,
      and the live rows of the hour in progress are deferred with a follow-up
      booked
  b4  idempotent: the Catch up history button runs the pull again; the
      cursor made it cheap (only the deferred rows are read again), nothing
      is imported twice, the statistics do not change
  b5  sleep: the supply at 12.5 V starts the 5 min countdown; with less than
      70 s left a 3000-row series (three pages) is seeded in the hour before
      this one and the button pressed: the device reports the extension
      (`activity_s`) and the entry moved out, the hour is imported, the
      supply back at 13.5 V ends the countdown
  b6  restore: the settings as found (one planned restart), the custom row
      gone, the log files the run wrote deleted, the webhook as found, the
      supply at 13.5 V, the HA entry left in place (reloaded), ha-bench
      stopped when this run started it; no unexpected reset, no E line of
      the components involved

  python tools/testbench/ha/ha_history_backfill_bench.py --dut localhost:18086 --dut-ip 10.42.1.75

Needs: the DUT on the bench AP through rpi001 (the tunnel), the ECU
simulator on the bus answering 010C, the OWON on its port for b5 (`--psu
none` skips the voltage part of b5), `internal/bench_ha_credentials.json`
(or --ha-user/--ha-pass/--ha-token). Verdict line: HA BACKFILL PASS / FAIL.
"""
import argparse
import calendar
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from wsmin import OP_TEXT, WS  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
CRED_FILE = os.path.join(REPO, "internal", "bench_ha_credentials.json")

V_AWAKE = 13.5
V_SLEEPY = 12.5
SEED_NAME = "EngineRPM"
ROW_CMD = "010C"
ROW_EXPR = "(B3*256+B4)/4"      # the 2026-10-24 frame of reference: B0 = the length byte
GROUP = "habackfill"
PUSH_INTERVAL = 15               # the integration's default post interval
STALE_S = max(PUSH_INTERVAL * 5, 120) + 30
TAGS = ("ha_webhooks", "data_logger", "sleep_manager", "autopid")

fails = []
notes = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        fails.append(name)


def metric(name, value):
    print(f"METRIC: {name} = {value}", flush=True)


def log(msg):
    print("  " + msg, flush=True)


# ---- HTTP -------------------------------------------------------------------

def http(url, method="GET", body=None, headers=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"Content-Type": "application/json"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode("utf-8", "replace")
    if text.strip().startswith(("{", "[")):
        try:
            return code, json.loads(text)
        except ValueError:
            pass
    return code, text


class Dut:
    def __init__(self, hostport):
        self.base = "http://" + hostport
        host, _, port = hostport.partition(":")
        self.host, self.port = host, int(port or 80)
        self.restarts = 0

    def api(self, path, method="GET", body=None, timeout=15, tries=3):
        last = None
        for _ in range(tries):
            try:
                return http(self.base + path, method, body, timeout=timeout)
            except (urllib.error.URLError, OSError, ValueError) as e:
                last = e
                time.sleep(2)
        return 0, str(last)

    def get(self, path, tries=6):
        for _ in range(tries):
            code, r = self.api(path)
            if code == 200:
                return r
            time.sleep(2)
        raise RuntimeError(f"GET {path}: {code} {str(r)[:120]}")

    def settings(self, name):
        doc = self.get(f"/api/settings/{name}")
        return {k: v for k, v in doc.items() if k not in ("degraded", "pending_reboot")}

    def put_settings(self, name, doc):
        code, r = self.api(f"/api/settings/{name}", "PUT", doc)
        if code != 200:
            raise RuntimeError(f"PUT {name}: {code} {str(r)[:160]}")

    def submit_and_wait(self, window=150):
        code, r = self.api("/api/settings/submit", "POST", {})
        if code != 200:
            raise RuntimeError(f"submit: {code} {r}")
        if not (isinstance(r, dict) and r.get("reboot")):
            log("submit: nothing to apply, no restart")
            return True
        self.restarts += 1
        t0 = time.time()
        time.sleep(6)
        while time.time() - t0 < window:
            code, _ = self.api("/api/info", timeout=5, tries=1)
            if code == 200:
                time.sleep(5)
                log(f"the DUT is back {time.time() - t0:.0f} s after the submit")
                return True
            time.sleep(2)
        return False

    def now_s(self):
        rtc = self.get("/api/rtc")
        return calendar.timegm(time.strptime(rtc["time"], "%Y-%m-%dT%H:%M:%SZ"))

    def ring_errors(self):
        code, ring = self.api("/api/logs/ring", timeout=10)
        if code != 200:
            return None
        lines = ring.get("lines") if isinstance(ring, dict) else ring
        if not isinstance(lines, list):
            lines = str(ring).splitlines()
        out = []
        for ln in lines:
            text = ln if isinstance(ln, str) else (ln.get("text") or ln.get("line") or str(ln))
            if "E (" in text and any(t + ":" in text for t in TAGS):
                out.append(text.strip())
        return out

    def cli(self, cmd, marker, timeout=60):
        """One command over /ws/cli (text channel): the output up to `marker`."""
        ws = WS(self.host, self.port, "/ws/cli", timeout=10)
        try:
            ws.send((cmd + "\n").encode(), opcode=OP_TEXT)
            out = ""
            end = time.time() + timeout
            while time.time() < end:
                try:
                    op, payload = ws.recv_frame()
                except Exception:
                    continue
                if op in (OP_TEXT, 2):
                    out += payload.decode("utf-8", "replace")
                    if marker in out:
                        return out
            raise TimeoutError(f"cli {cmd!r}: no {marker!r} in {out[-200:]!r}")
        finally:
            try:
                ws.s.close()
            except Exception:
                pass

    def log_files(self):
        code, r = self.api("/api/fs/list?path=/sd/logs")
        if code != 200 or not isinstance(r, dict):
            return []
        return sorted(e["name"] for e in r.get("entries", []) if isinstance(e, dict)
                      and str(e.get("name", "")).startswith("dl_") and not e.get("dir"))


class Ha:
    def __init__(self, base, token=None):
        self.base = base.rstrip("/")
        self.token = token
        u = urllib.parse.urlparse(self.base)
        self.host, self.port = u.hostname, u.port or 8123

    def login(self, user, pw):
        client_id = self.base + "/"
        code, flow = http(self.base + "/auth/login_flow",
                          "POST", {"client_id": client_id, "handler": ["homeassistant", None],
                                   "redirect_uri": client_id}, timeout=30)
        code, res = http(self.base + "/auth/login_flow/" + flow["flow_id"], "POST",
                         {"username": user, "password": pw, "client_id": client_id}, timeout=30)
        if res.get("type") != "create_entry":
            raise RuntimeError("HA login did not finish: " + json.dumps(res)[:200])
        data = urllib.parse.urlencode({"grant_type": "authorization_code", "code": res["result"],
                                       "client_id": client_id}).encode()
        req = urllib.request.Request(self.base + "/auth/token", data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            self.token = json.loads(r.read().decode())["access_token"]
        return self.token

    def call(self, path, body=None, method=None, timeout=60):
        return http(self.base + path, method or ("POST" if body is not None else "GET"), body,
                    headers={"Authorization": "Bearer " + self.token}, timeout=timeout)

    def up(self):
        try:
            code, _ = http(self.base + "/api/", timeout=5)
            return code in (200, 401)
        except Exception:
            return False

    def entries(self):
        code, r = self.call("/api/config/config_entries/entry")
        return [e for e in r if e.get("domain") == "wican"] if code == 200 else []

    def add_entry(self, address):
        code, flow = self.call("/api/config/config_entries/flow",
                               {"handler": "wican", "show_advanced_options": False})
        code, res = self.call("/api/config/config_entries/flow/" + flow["flow_id"],
                              {"mdns": address, "host": address})
        if res.get("type") != "create_entry":
            raise RuntimeError("config flow: " + json.dumps(res)[:300])
        return res["result"]["entry_id"]

    def reload(self, entry_id):
        return self.call(f"/api/config/config_entries/entry/{entry_id}/reload", {})

    def diagnostics(self, entry_id):
        code, r = self.call(f"/api/diagnostics/config_entry/{entry_id}")
        return r.get("data", r) if code == 200 and isinstance(r, dict) else None

    def press(self, entity_id):
        return self.call("/api/services/button/press", {"entity_id": entity_id})

    def state(self, entity_id):
        code, r = self.call(f"/api/states/{entity_id}")
        return r if code == 200 else None

    def ws_call(self, message):
        """One command over the websocket API (auth first); its result."""
        ws = WS(self.host, self.port, "/api/websocket", timeout=20)
        try:
            def recv():
                while True:
                    op, payload = ws.recv_frame()
                    if op == OP_TEXT:
                        return json.loads(payload.decode())
            msg = recv()
            if msg.get("type") != "auth_required":
                raise RuntimeError("ws: " + str(msg)[:100])
            ws.send(json.dumps({"type": "auth", "access_token": self.token}).encode(), opcode=OP_TEXT)
            msg = recv()
            if msg.get("type") != "auth_ok":
                raise RuntimeError("ws auth: " + str(msg)[:100])
            ws.send(json.dumps({"id": 1, **message}).encode(), opcode=OP_TEXT)
            while True:
                msg = recv()
                if msg.get("id") == 1:
                    if not msg.get("success"):
                        raise RuntimeError(message.get("type", "ws") + ": " + str(msg)[:200])
                    return msg["result"]
        finally:
            try:
                ws.s.close()
            except Exception:
                pass

    def statistics(self, entity_id, start_epoch):
        """recorder/statistics_during_period: hourly mean/min/max rows for
        one entity from `start_epoch` on."""
        start = datetime.datetime.fromtimestamp(start_epoch, datetime.timezone.utc).isoformat()
        result = self.ws_call({"type": "recorder/statistics_during_period", "start_time": start,
                               "statistic_ids": [entity_id], "period": "hour",
                               "types": ["mean", "min", "max"]})
        out = {}
        for row in result.get(entity_id, []):
            st = row.get("start")
            st_s = int(st / 1000) if isinstance(st, (int, float)) and st > 1e11 else int(st)
            out[st_s] = {"mean": row.get("mean"), "min": row.get("min"), "max": row.get("max")}
        return out

    def device_id_for(self, entry_id):
        """The HA device registry id of the entry's device (the service target)."""
        for dev in self.ws_call({"type": "config/device_registry/list"}):
            if entry_id in (dev.get("config_entries") or []):
                return dev.get("id")
        return None

    def call_service(self, domain, service, data):
        return self.call(f"/api/services/{domain}/{service}?return_response", data, timeout=120)


def pi(host, cmd, timeout=60):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


# ---- the known series ---------------------------------------------------------

def expected_buckets(t0_s, n, step_s):
    """hour start -> (mean, min, max) of the rows i*0.5 at t0 + i*step."""
    hours = {}
    for i in range(n):
        h = ((t0_s + i * step_s) // 3600) * 3600
        hours.setdefault(h, []).append(i * 0.5)
    return {h: (sum(v) / len(v), min(v), max(v)) for h, v in hours.items()}


def seed(dut, n, age_s, step_ms=1000):
    out = dut.cli(f"logger test {n} autopid {SEED_NAME} {age_s} {step_ms}", "records", timeout=120)
    return "queued %d/%d" % (n, n) in out, out.strip().splitlines()[-1][:120]


def wait_written(dut, target, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        code, lg = dut.api("/api/logger")
        if code == 200 and lg.get("written", 0) >= target:
            return lg.get("written", 0)
        time.sleep(2)
    code, lg = dut.api("/api/logger")
    return lg.get("written", 0) if code == 200 else -1


def last_finished(ha, entry_id):
    """`finished_at` of the integration's last run (HA's clock), 0 when none."""
    d = ha.diagnostics(entry_id) or {}
    return ((d.get("history_sync") or {}).get("finished_at")) or 0


def stored_watermark(pi_host, entry_id):
    """The integration's TIME watermark as stored (HA's .storage), 0 when none:
    the diagnostics carry the last RUN's fields, not the stored marks."""
    rc, out = pi(pi_host, f"cat ~/.homeassistant/.storage/wican.history_{entry_id} 2>/dev/null")
    try:
        return float((json.loads(out).get("data") or {}).get("watermark") or 0)
    except (ValueError, AttributeError):
        return 0.0


def wait_sync(ha, entry_id, after_fin, secs, label):
    """The next catch-up run whose `finished_at` is past `after_fin`, the
    previous run's (both on HA's clock: the PC's runs seconds behind the
    Pi's, and a PC-clock baseline took the previous run for the new one in
    run 4)."""
    t0 = time.time()
    last = None
    while time.time() - t0 < secs:
        d = ha.diagnostics(entry_id)
        hs = (d or {}).get("history_sync")
        if hs and hs.get("finished_at") and hs["finished_at"] > after_fin and hs.get("ran"):
            log(f"{label}: catch-up done {time.time() - t0:.0f} s in: {json.dumps({k: hs.get(k) for k in ('source', 'pages', 'rows_fetched', 'rows_used', 'rows_deferred', 'rows_rejected', 'hours_imported', 'pids_imported', 'watermark', 'cursor', 'followup_in', 'errors')})}")
            return hs
        last = hs
        time.sleep(3)
    log(f"{label}: no catch-up within {secs} s; last: {json.dumps(last)[:300]}")
    return None


def entity_for(ha, entry_id, suffix):
    d = ha.diagnostics(entry_id) or {}
    for eid in (d.get("entities") or {}):
        if eid.startswith("sensor.") and eid.endswith(suffix):
            return eid
    return None


# ---- main -------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dut", default="localhost:18086", help="the DUT as reached from here (an ssh tunnel through the Pi)")
    ap.add_argument("--dut-ip", default="10.42.1.75", help="the DUT's address on the bench AP (what Home Assistant uses)")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--pi-ip", default="10.42.1.1", help="the Pi's address on the bench AP (the webhook's host)")
    ap.add_argument("--ha", default=None, help="the bench HA's base URL from here (default: http://<pi LAN ip>:8123)")
    ap.add_argument("--ha-user")
    ap.add_argument("--ha-pass")
    ap.add_argument("--ha-token")
    ap.add_argument("--psu", default="auto", help="the OWON's port, or none to skip the voltage part of b5")
    ap.add_argument("--only", default="", help="comma list of legs (b0..b6); b0 and b6 always run")
    ap.add_argument("--keep-ha", action="store_true", help="leave ha-bench running even when this run started it")
    ap.add_argument("--logdir", default=None)
    args = ap.parse_args()
    only = set(x.strip() for x in args.only.split(",") if x.strip())
    want = lambda leg: not only or leg in only  # noqa: E731

    logdir = args.logdir or os.path.join(REPO, "test-reports", "logs",
                                         "habackfill_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(logdir, exist_ok=True)
    dut = Dut(args.dut)
    t_start = time.time()
    started_ha = False
    found = {}
    saved_cfg = None
    files_before = []
    psu = None
    reg_before = None
    ha = None
    entry_id = None

    try:
        # ---- b0 rig --------------------------------------------------------------
        print("== b0 the rig")
        info = dut.get("/api/info")
        check("[b0] the DUT answers /api/info", info.get("device_id") is not None, str(info)[:100])
        device_id = info.get("device_id")
        lg = dut.get("/api/logger")
        check("[b0] this build has the export counters on /api/logger", "export" in lg and "format" in lg,
              json.dumps(lg.get("export")))
        sl = dut.get("/api/sleep")
        check("[b0] this build has activity_s on /api/sleep", "activity_s" in sl, str(sl.get("activity_s")))
        hist0 = dut.get("/api/restart/history")
        unexpected0 = hist0.get("unexpected_resets", 0)
        ring0 = dut.ring_errors() or []

        rc, pi_ip = pi(args.pi, "hostname -I | awk '{print $1}'")
        ha_base = args.ha or f"http://{pi_ip}:8123"
        ha = Ha(ha_base)
        rc, active = pi(args.pi, "systemctl is-active ha-bench")
        if active.strip() != "active":
            log("ha-bench is not running: starting it for this run")
            pi(args.pi, "sudo -n systemd-run --unit=ha-bench --collect -p User=meatpi -p WorkingDirectory=/home/meatpi /home/meatpi/ha/bin/hass -c /home/meatpi/.homeassistant")
            started_ha = True
        t0 = time.time()
        while time.time() - t0 < 180 and not ha.up():
            time.sleep(5)
        check("[b0] the bench HA answers", ha.up(), ha_base)
        if not ha.up():
            raise RuntimeError("no HA")
        if args.ha_token:
            ha.token = args.ha_token
        else:
            user, pw = args.ha_user, args.ha_pass
            if not (user and pw) and os.path.exists(CRED_FILE):
                with open(CRED_FILE) as f:
                    cred = json.load(f)
                user, pw = cred.get("user"), cred.get("password")
            if not (user and pw):
                raise RuntimeError("no HA credentials: --ha-user/--ha-pass or internal/bench_ha_credentials.json")
            ha.login(user, pw)
        check("[b0] an access token through the owner login", bool(ha.token))

        entries = ha.entries()
        mine = [e for e in entries if e.get("title") == args.dut_ip]
        if not mine:
            log(f"no entry for {args.dut_ip}: adding it through the config flow")
            entry_id = ha.add_entry(args.dut_ip)
            time.sleep(5)
        else:
            entry_id = mine[0]["entry_id"]
        check("[b0] a config entry for this device", bool(entry_id), entry_id)
        # a fresh start for the integration's marks (the time watermark and the
        # device cursor of the LAST run would make this run's seeded hours "older
        # than what was imported" and reject them: run 3, 2026-11-06); the reload
        # re-registers the webhook and runs the probe's pull
        rc, out = pi(args.pi, f"rm -fv ~/.homeassistant/.storage/wican.history_{entry_id}")
        log(f"the entry's history marks cleared ({out.strip() or 'none stored'}); reloading the entry")
        ha.reload(entry_id)
        time.sleep(8)
        # the entry's device id must be ours (the device_id lands with the first push)
        t0 = time.time()
        reg = None
        while time.time() - t0 < 90:
            code, reg = dut.api("/api/webhook")
            if code == 200 and reg.get("url") and args.pi_ip in reg["url"] and reg.get("success_count", 0) > 0:
                break
            time.sleep(5)
        reg_before = found["webhook"] = reg if isinstance(reg, dict) else None
        check("[b0] the webhook registered on the device with the bench AP's address and a push delivered",
              isinstance(reg, dict) and bool(reg.get("url")) and args.pi_ip in reg.get("url", "") and reg.get("success_count", 0) > 0,
              json.dumps({k: reg.get(k) for k in ("url", "status", "success_count", "fail_count")}) if isinstance(reg, dict) else str(reg))
        d = ha.diagnostics(entry_id) or {}
        check("[b0] the entry knows this device", (d.get("device_info") or {}).get("device_id") == device_id,
              str((d.get("device_info") or {}).get("device_id")))
        rc, pi_now = pi(args.pi, "date -u +%s")
        dut_now = dut.now_s()
        check("[b0] the DUT's and the Pi's clocks within 60 s", abs(int(pi_now) - dut_now) < 60, f"{dut_now - int(pi_now):+d} s")

        # ---- b1 configure --------------------------------------------------------
        print("== b1 configure")
        for name in ("data_logger", "autopid", "websocket_manager", "ha_webhooks", "bridge_manager"):
            found[name] = dut.settings(name)
        saved_cfg = dut.get("/api/autopid/config")
        found["vehicles_current"] = dut.get("/api/autopid/vehicles").get("current", "")
        files_before = dut.log_files()
        if want("b1") or True:
            dl = dict(found["data_logger"])
            dl.update({"enabled": True, "format": "jsonl", "autopid_log": "all"})
            dut.put_settings("data_logger", dl)
            apd = dict(found["autopid"])
            apd["enabled"] = True
            apd["pause_follow_sleep"] = False  # b5 holds the supply under the sleep voltage
            dut.put_settings("autopid", apd)
            wsm = json.loads(json.dumps(found["websocket_manager"]))
            for ch in wsm.get("channels", []):
                if ch.get("name") == "ws_cli":
                    ch["enabled"] = True
            dut.put_settings("websocket_manager", wsm)
            # the console reaches /ws/cli through a bridge (the canonical bench
            # settings carry one; the fresh unit does not: run 1, 2026-11-06)
            brm = json.loads(json.dumps(found["bridge_manager"]))
            if not any(b.get("a") == "cli" and b.get("b") == "ws_cli" for b in brm.get("bridges", [])):
                brm.setdefault("bridges", []).append(
                    {"name": "br_cli_ws", "a": "cli", "b": "ws_cli", "translator": "raw", "enabled": True})
                dut.put_settings("bridge_manager", brm)
            check("[b1] the settings staged (logger jsonl + every sample, autopid on, ws_cli on + its bridge)",
                  dut.settings("data_logger").get("format") == "jsonl" and dut.settings("autopid").get("enabled") is True)
            check("[b1] submit and the planned restart", dut.submit_and_wait())
            t_back = time.time()
            # a unit that has not met this car runs its vehicle detection at the
            # first start with autopid on (the std scan learns the simulator's car
            # and REWRITES the table ~20 s after the boot): the custom row goes in
            # once that is over, or it is dropped (run 1, 2026-11-06)
            t0 = time.time()
            while time.time() - t0 < 120:
                code, scan = dut.api("/api/autopid/std_scan")
                if code == 200 and scan.get("status") == "idle" and time.time() - t_back >= 30:
                    break
                time.sleep(3)
            log(f"detection idle {time.time() - t_back:.0f} s after the restart; bridges: "
                + json.dumps([(b.get('name'), b.get('up')) for b in (dut.get('/api/bridges').get('bridges') or []) if b.get('name') == 'br_cli_ws']))
            cfg = {"groups": [{"name": GROUP, "enabled": True, "period_ms": 1000}],
                   "pids": [{"name": SEED_NAME, "type": "custom", "cmd": ROW_CMD, "group": GROUP,
                             "parameters": [{"name": SEED_NAME, "expression": ROW_EXPR, "unit": "rpm",
                                             "min": 0, "max": 16384}]}],
                   "filters": []}
            code, r = dut.api("/api/autopid/config", "PUT", cfg)
            check("[b1] the custom row EngineRPM (010C) applied", code == 200, f"{code} {str(r)[:80]}")
            lg = dut.get("/api/logger")
            check("[b1] the logger runs as jsonl on the card", lg.get("enabled") and lg.get("storage_ok") and lg.get("format") == "jsonl",
                  json.dumps({k: lg.get(k) for k in ("enabled", "running", "storage_ok", "format", "autopid_log", "file")}))
            written = wait_written(dut, 5, 60)
            apst = dut.get("/api/autopid")
            check("[b1] live AutoPID rows reach the card (the simulator answers 010C)", written >= 5,
                  f"written {written}; autopid stats {json.dumps({k: (apst.get('stats') or {}).get(k) for k in ('running', 'polls_ok', 'polls_failed', 'paused_voltage', 'paused_bus', 'pids')})}")
            # HA: the pushes resume after the restart; the sensor for the row appears with the first value
            t0 = time.time()
            eid = None
            while time.time() - t0 < 120 and not eid:
                time.sleep(5)
                eid = entity_for(ha, entry_id, "_enginerpm")
            check("[b1] Home Assistant has the EngineRPM sensor from the push", bool(eid), str(eid))
            if not eid:
                raise RuntimeError("no entity for the seeded parameter: nothing to import into")
            found["entity"] = eid

        # ---- b2 on the road ------------------------------------------------------
        print("== b2 on the road")
        exp2 = {}
        if want("b2"):
            now = dut.now_s()
            hour = (now // 3600) * 3600
            t0_s = hour - 2 * 3600 + 300          # 5 min into the hour before last
            age = now - t0_s
            w0 = dut.get("/api/logger").get("written", 0)
            ok, line = seed(dut, 1200, age)
            check("[b2] 1200 rows recorded two hours back (logger test)", ok, line)
            exp2 = expected_buckets(t0_s, 1200, 1)
            metric("b2_expected", json.dumps({str(k): v for k, v in exp2.items()}))
            written = wait_written(dut, w0 + 1200, 90)
            check("[b2] the rows written to the card", written >= w0 + 1200, f"written {written} (was {w0})")
            reg = dut.get("/api/webhook")
            code, r = dut.api("/api/webhook", "POST", {"url": reg["url"], "enabled": False,
                                                        "interval": reg.get("interval", PUSH_INTERVAL),
                                                        "manual_override": False})
            check("[b2] the pushes switched off (the car drove away)", code in (200, 201), f"{code}")
            cnt0 = dut.get("/api/webhook").get("success_count", 0)
            log(f"silence for {STALE_S} s (the integration's staleness window)")
            time.sleep(STALE_S)
            cnt1 = dut.get("/api/webhook").get("success_count", 0)
            check("[b2] no push left the device meanwhile", cnt1 == cnt0, f"{cnt0} -> {cnt1}")

        # ---- b3 back home --------------------------------------------------------
        print("== b3 back home")
        if want("b3"):
            lg_before = dut.get("/api/logger").get("export", {})
            # the integration runs an automatic pull at most every 5 min (a
            # gap end inside that window is booked for its end): the bench
            # says how far the last run is so the wait below can be read
            d0 = ha.diagnostics(entry_id) or {}
            fin0 = ((d0.get("history_sync") or {}).get("finished_at")) or 0
            log(f"the integration's last run finished {time.time() - fin0:.0f} s ago" if fin0 else "no run yet")
            t_home = time.time()
            reg = dut.get("/api/webhook")
            code, r = dut.api("/api/webhook", "POST", {"url": reg["url"], "enabled": True,
                                                        "interval": reg.get("interval", PUSH_INTERVAL),
                                                        "manual_override": False})
            check("[b3] the pushes switched on (home WiFi)", code in (200, 201), f"{code}")
            hs = wait_sync(ha, entry_id, fin0, 360, "b3")
            check("[b3] the first push ended the gap and the integration pulled the log", hs is not None)
            if hs:
                check("[b3] through the export route, no errors", hs.get("source") == "export" and not hs.get("errors"),
                      json.dumps(hs.get("errors")))
                check("[b3] the seeded rows were fetched and the hour imported",
                      hs.get("rows_fetched", 0) >= 1200 and hs.get("hours_imported", 0) >= 1 and hs.get("pids_imported", 0) >= 1,
                      json.dumps({k: hs.get(k) for k in ("rows_fetched", "rows_used", "hours_imported", "pids_imported")}))
                check("[b3] the device cursor is kept", bool(hs.get("cursor")), str(hs.get("cursor")))
                check("[b3] the device logger state travels in the diagnostics",
                      (hs.get("device") or {}).get("format") == "jsonl" and (hs.get("device") or {}).get("logger") is True,
                      json.dumps(hs.get("device")))
                check("[b3] the live rows of the hour in progress are deferred with a follow-up booked",
                      hs.get("rows_deferred", 0) > 0 and hs.get("followup_in") and hs.get("followup_due"),
                      json.dumps({k: hs.get(k) for k in ("rows_deferred", "followup_in", "followup_due")}))
                metric("b3_rows_fetched", hs.get("rows_fetched"))
                metric("b3_pages", hs.get("pages"))
            lg_after = dut.get("/api/logger").get("export", {})
            check("[b3] the device counted the reads", lg_after.get("reads", 0) > lg_before.get("reads", 0) and lg_after.get("rows", 0) >= 1200,
                  json.dumps(lg_after))
            # the statistics in the recorder
            stats = ha.statistics(found["entity"], min(exp2) - 3600) if exp2 else {}
            for h, (mean, mn, mx) in sorted(exp2.items()):
                got = stats.get(h)
                ok = bool(got) and abs(got["mean"] - mean) < 1e-6 and abs(got["min"] - mn) < 1e-9 and abs(got["max"] - mx) < 1e-9
                check(f"[b3] the recorder holds hour {time.strftime('%H:%M', time.gmtime(h))}Z exactly (mean {mean:.3f}, min {mn}, max {mx})",
                      ok, json.dumps(got))
            found["stats_b3"] = stats

        # ---- b4 idempotent ------------------------------------------------------------
        print("== b4 the button, idempotent")
        if want("b4"):
            fin_before = last_finished(ha, entry_id)
            btn = found["entity"].replace("sensor.", "button.").rsplit("_", 1)[0] + "_catch_up_history"
            code, r = ha.press(btn)
            check("[b4] the Catch up history button pressed", code == 200, f"{btn} {code}")
            hs = wait_sync(ha, entry_id, fin_before, 120, "b4")
            check("[b4] the second pull ran", hs is not None)
            rc, marks4 = pi(args.pi, f"cat ~/.homeassistant/.storage/wican.history_{entry_id} 2>/dev/null")
            log(f"b4: marks stored {' '.join(marks4.split())[:160]}; device export {json.dumps(dut.get('/api/logger').get('export'))}")
            if hs:
                # the live rows of the hour in progress sit among the seeded rows
                # in this file (the bench writes the past after the present), so
                # the cursor rolls back to the file's start and the button reads
                # it all again; every row read again is older than the watermark
                # or deferred, none is imported twice (a real file is in time
                # order: the rollback costs one page there)
                check("[b4] nothing imported twice (every row read again was older than the watermark or deferred)",
                      hs.get("hours_imported", 0) == 0 and hs.get("rows_fetched", 0) > 0
                      and hs.get("rows_rejected", 0) + hs.get("rows_deferred", 0) == hs.get("rows_fetched", 0),
                      json.dumps({k: hs.get(k) for k in ("rows_fetched", "rows_rejected", "rows_deferred", "hours_imported")}))
            if exp2:
                stats = ha.statistics(found["entity"], min(exp2) - 3600)
                same = all(stats.get(h) == found["stats_b3"].get(h) for h in exp2)
                check("[b4] the statistics did not change", same,
                      "" if same else json.dumps({str(h): [found["stats_b3"].get(h), stats.get(h)] for h in exp2}))

        # ---- b4g the GPS track (2026-11-06, "the GPX GPS time series") -----------------
        print("== b4g the GPS track")
        if want("b4g"):
            # two series under the fix's own names on the SAME stamps (the
            # absolute start `@<epoch_s>`: an age is taken from the device's now
            # at each command, which put the two series a second apart in run
            # 4): the device gathers them into points (lat = lon = i * 0.5 at
            # t0 + i s)
            now = dut.now_s()
            t0_s = now - 7200 + 2400                 # inside the hour before last, after the b2 series
            wg = dut.get("/api/logger").get("written", 0)
            ok_lat = "queued 100/100" in dut.cli(f"logger test 100 autopid gps_latitude @{t0_s} 1000", "records", timeout=60)
            ok_lon = "queued 100/100" in dut.cli(f"logger test 100 autopid gps_longitude @{t0_s} 1000", "records", timeout=60)
            check("[b4g] 100 fixes recorded (latitude and longitude series on the same stamps)", ok_lat and ok_lon)
            wait_written(dut, wg + 200, 60)
            wait_written(dut, dut.get("/api/logger").get("written", 0), 5)
            time.sleep(12)  # the writer's last batch of the two series
            code, gpx = dut.api(f"/api/logger/gpx?since={t0_s - 5}&limit=4000", timeout=60)
            check("[b4g] GET /api/logger/gpx answers a GPX document", code == 200 and isinstance(gpx, str) and gpx.startswith("<?xml") and "<gpx" in gpx,
                  f"{code} {str(gpx)[:60]!r}")
            if code == 200 and isinstance(gpx, str):
                import re as _re
                pts = _re.findall(r'<trkpt lat="([-\d.]+)" lon="([-\d.]+)"><time>([^<]+)</time>', gpx)
                trailer = _re.search(r'<!-- cursor="([^"]+)" more="(true|false)" points="(\d+)" -->', gpx)
                lats = [float(p[0]) for p in pts]
                lons = [float(p[1]) for p in pts]
                times = [p[2] for p in pts]
                expect = [i * 0.5 for i in range(100)]
                check("[b4g] 100 points, the latitude and longitude series paired by stamp, in order",
                      len(pts) == 100 and all(abs(a - b) < 1e-5 for a, b in zip(lats, expect)) and all(abs(a - b) < 1e-5 for a, b in zip(lons, expect)),
                      f"{len(pts)} points; first {pts[:1]}")
                check("[b4g] the points' times ascend one second apart", times == sorted(times) and len(set(times)) == len(times) and times[0].endswith("Z"), f"{times[:2]}")
                check("[b4g] the document names the point count and no more", bool(trailer) and trailer.group(3) == "100" and trailer.group(2) == "false",
                      trailer.group(0) if trailer else "no trailer")
                metric("b4g_gpx_bytes", len(gpx))
            # the integration's service saves the same track under www/wican
            dev_id = ha.device_id_for(entry_id)
            check("[b4g] the HA device of the entry found", bool(dev_id), str(dev_id))
            if dev_id:
                code, resp = ha.call_service("wican", "save_gpx", {"device_id": dev_id, "hours": 6})
                body = resp.get("service_response", resp) if isinstance(resp, dict) else resp
                check("[b4g] wican.save_gpx saved the track", code == 200 and isinstance(body, dict) and body.get("points", 0) >= 100 and str(body.get("url", "")).startswith("/local/wican/"),
                      f"{code} {str(body)[:160]}")
                if isinstance(body, dict) and body.get("path"):
                    rc, out = pi(args.pi, f"grep -c '<trkpt ' '{body['path']}'; rm -f '{body['path']}'")
                    check("[b4g] the file on the Pi holds the points (then removed)", out.strip().splitlines()[0].isdigit() and int(out.strip().splitlines()[0]) >= 100, out.strip()[:80])

        # ---- b5 sleep ---------------------------------------------------------------------
        print("== b5 the countdown and the log read")
        if want("b5"):
            if args.psu != "none":
                from bench_psu import open_psu  # noqa: E402  (ECU sim v2's OBD pin 16)
                psu = open_psu(args.psu)
                psu.meas_voltage()
                psu.set_voltage(V_SLEEPY)
                t0 = time.time()
                pend = None
                while time.time() - t0 < 40:
                    sl = dut.get("/api/sleep")
                    if sl.get("pending") == "delay":
                        pend = sl
                        break
                    time.sleep(2)
                check("[b5] the countdown runs at 12.5 V", pend is not None, json.dumps({k: (pend or {}).get(k) for k in ("pending", "sleep_in_s", "voltage")}))
                # the hold only shows once the entry is nearer than a minute
                t0 = time.time()
                while time.time() - t0 < 320:
                    sl = dut.get("/api/sleep")
                    if sl.get("pending") != "delay" or sl.get("sleep_in_s", 999) <= 70:
                        break
                    time.sleep(5)
                sl = dut.get("/api/sleep")
                check("[b5] the entry is less than 70 s away", sl.get("pending") == "delay" and sl.get("sleep_in_s", 999) <= 70,
                      json.dumps({k: sl.get(k) for k in ("pending", "sleep_in_s")}))
                left_before = sl.get("sleep_in_s", 0)
            now = dut.now_s()
            hour = (now // 3600) * 3600
            t0_s = hour - 3600 + 60
            # importable only when the integration's watermark has not passed
            # that hour (it has when the hour rolled over during the run and
            # the live rows of the hour before were imported meanwhile)
            wm5 = stored_watermark(args.pi, entry_id)
            importable = wm5 <= hour - 3600
            log(f"b5: the watermark is {wm5:.0f} ({'the hour before this one is still open to an import' if importable else 'past the hour before this one: the pages are read, nothing new is imported'})")
            w0 = dut.get("/api/logger").get("written", 0)
            ok, line = seed(dut, 3000, now - t0_s)
            check("[b5] 3000 rows recorded in the hour before this one (three pages)", ok, line)
            exp5 = expected_buckets(t0_s, 3000, 1) if importable else {}
            written = wait_written(dut, w0 + 3000, 120)
            check("[b5] the rows written to the card", written >= w0 + 3000, f"written {written} (was {w0})")
            # where this pull starts and what the device holds (run 4 left the
            # partial import of this hour unexplained)
            rc, marks5 = pi(args.pi, f"cat ~/.homeassistant/.storage/wican.history_{entry_id} 2>/dev/null")
            code, fsl = dut.api("/api/fs/list?path=/sd/logs")
            log(f"b5: before the press: marks {' '.join(marks5.split())[:160]}; device export {json.dumps(dut.get('/api/logger').get('export'))}; "
                f"files {[(e.get('name'), e.get('size')) for e in (fsl.get('entries') or []) if isinstance(e, dict) and str(e.get('name', '')).startswith('dl_')] if isinstance(fsl, dict) else fsl}")
            fin_before = last_finished(ha, entry_id)
            btn = found["entity"].replace("sensor.", "button.").rsplit("_", 1)[0] + "_catch_up_history"
            code, r = ha.press(btn)
            seen_activity = 0
            hs = None
            t0 = time.time()
            while time.time() - t0 < 120:
                sl = dut.get("/api/sleep")
                seen_activity = max(seen_activity, sl.get("activity_s", 0))
                d = ha.diagnostics(entry_id)
                cur = (d or {}).get("history_sync")
                if cur and cur.get("finished_at") and cur["finished_at"] > fin_before:
                    hs = cur
                    break
                time.sleep(1)
            check("[b5] the pull ran", hs is not None)
            if hs:
                log(f"b5: {json.dumps({k: hs.get(k) for k in ('pages', 'rows_fetched', 'rows_used', 'rows_rejected', 'rows_deferred', 'hours_imported', 'watermark', 'cursor', 'followup_in', 'errors')})}; "
                    f"device export after {json.dumps(dut.get('/api/logger').get('export'))}")
                check("[b5] three pages or more" + (", the hour imported" if importable else ""),
                      hs.get("pages", 0) >= 3 and (hs.get("hours_imported", 0) >= 1 or not importable),
                      json.dumps({k: hs.get(k) for k in ("pages", "rows_fetched", "hours_imported")}))
            if psu is not None:
                sl = dut.get("/api/sleep")
                seen_activity = max(seen_activity, sl.get("activity_s", 0))
                check("[b5] the device reports the extension a log read gave (activity_s)", seen_activity > 0, f"activity_s up to {seen_activity}")
                check("[b5] the entry moved out past the read (sleep_in_s)", sl.get("pending") == "delay" and sl.get("sleep_in_s", 0) >= 45,
                      f"{left_before} s before the pull, {sl.get('sleep_in_s')} s after")
                metric("b5_sleep_in_s_after", sl.get("sleep_in_s"))
                ring = dut.ring_errors()
                psu.set_voltage(V_AWAKE)
                t0 = time.time()
                while time.time() - t0 < 40:
                    sl = dut.get("/api/sleep")
                    if sl.get("pending") == "none":
                        break
                    time.sleep(2)
                check("[b5] the countdown ends at 13.5 V", sl.get("pending") == "none", json.dumps({k: sl.get(k) for k in ("pending", "voltage")}))
            stats = ha.statistics(found["entity"], min(exp5) - 3600) if exp5 else {}
            for h, (mean, mn, mx) in sorted(exp5.items()):
                got = stats.get(h)
                ok = bool(got) and abs(got["mean"] - mean) < 1e-6 and abs(got["min"] - mn) < 1e-9 and abs(got["max"] - mx) < 1e-9
                check(f"[b5] the recorder holds hour {time.strftime('%H:%M', time.gmtime(h))}Z exactly (mean {mean:.3f}, min {mn}, max {mx})", ok, json.dumps(got))

    except Exception as e:
        check("[run] no exception", False, f"{type(e).__name__}: {e}")
    finally:
        # ---- b6 restore ---------------------------------------------------------------------
        print("== b6 restore")
        try:
            if psu is not None:
                psu.set_voltage(V_AWAKE)
            ring_end = dut.ring_errors()
            new_e = [ln for ln in (ring_end or []) if ln not in (ring0 if 'ring0' in dir() else [])]
            check("[b6] no E line from the components involved during the run", ring_end is not None and not new_e, "; ".join(new_e[:4]))
            if saved_cfg is not None:
                code, r = dut.api("/api/autopid/config", "PUT", saved_cfg)
                check("[b6] the AutoPID table as found", code == 200, str(code))
            if reg_before is not None and reg_before.get("url"):
                dut.api("/api/webhook", "POST", {"url": reg_before["url"], "enabled": bool(reg_before.get("enabled", True)),
                                                "interval": reg_before.get("interval", PUSH_INTERVAL), "manual_override": False})
            elif entry_id:
                code, _ = dut.api("/api/webhook", "DELETE")
                log(f"the webhook registration deleted (none was there before): {code}")
            changed = False
            for name in ("data_logger", "autopid", "websocket_manager", "ha_webhooks", "bridge_manager"):
                if name in found and found[name] != dut.settings(name):
                    dut.put_settings(name, found[name])
                    changed = True
            if changed:
                check("[b6] the settings as found and the planned restart", dut.submit_and_wait())
            # a car the run's detection learned is forgotten again (the store as found)
            if "vehicles_current" in found and not found["vehicles_current"]:
                cur = dut.get("/api/autopid/vehicles").get("current", "")
                if cur:
                    code, _ = dut.api("/api/autopid/vehicles/" + urllib.parse.quote(cur), "DELETE")
                    log(f"the car {cur} the detection learned forgotten: {code}")
            # the log files this run wrote (the logger is off again: nothing is locked)
            gone = 0
            for name in dut.log_files():
                if name not in files_before:
                    code, _ = dut.api("/api/fs/file?path=" + urllib.parse.quote("/sd/logs/" + name), "DELETE")
                    gone += code in (200, 204)
            log(f"{gone} log file(s) of this run deleted")
            hist1 = dut.get("/api/restart/history")
            check("[b6] no unexpected reset", hist1.get("unexpected_resets", 0) == unexpected0,
                  f"{unexpected0} -> {hist1.get('unexpected_resets')}, planned restarts by this run: {dut.restarts}")
            if ha is not None and ha.token and entry_id:
                ha.reload(entry_id)
            if started_ha and not args.keep_ha:
                pi(args.pi, "sudo -n systemctl stop ha-bench")
                log("ha-bench stopped (this run started it)")
        except Exception as e:
            check("[b6] restore", False, f"{type(e).__name__}: {e}")
        with open(os.path.join(logdir, "summary.json"), "w") as f:
            json.dump({"fails": fails, "secs": round(time.time() - t_start), "found": {k: v for k, v in found.items() if k != "stats_b3"}}, f, indent=1)
        print(f"log dir: {logdir}")
        if fails:
            print(f"HA BACKFILL FAIL: {len(fails)} failed: " + "; ".join(fails[:6]))
        else:
            print("HA BACKFILL PASS")
        sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
