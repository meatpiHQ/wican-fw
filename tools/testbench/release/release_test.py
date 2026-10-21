#!/usr/bin/env python3
"""The Release Test: the fast pre-release gate on the exact release image.

Born 2026-10-21 from the beta.1 field crash (internal/TASK_release_test.md):
the gate that would have caught it existed, nobody ran it on the bytes that
shipped. Three rules make this a gate and not another bench:

  1. The bytes under test are the bytes published: the test flashes the
     build's flash set, checks the running image's version and ELF id
     against the image's own descriptor, and the record it writes is keyed
     by the image's sha256. release.py refuses to publish an image that has
     no fresh PASS record.
  2. A hard time cap (--cap-s, 900): a gate that grows stops being run.
     Every step has a budget; a step over it fails.
  3. Coverage by task: MATRIX maps every task with a PSRAM stack to the
     step that drives its main path (the beta.1 class, flash touched from a
     PSRAM stack, only shows when that task runs the path). A PSRAM-stack
     task without a step is reported by the preflight.

Steps (about 9 min): r0 preflight, r1 flash + boot, r2 boot health, r3 UI
and API, r4 a settings round trip, r5 OBD (autopid values, ELM over
ws_obd), r6 CAN frames flow, r7 MQTT, r8 the HA webhook sta_ip bench (the
PSRAM-stack canary), r9 the updater's check, r10 an OTA upload of the same
image, r11 the writers (backup copy, log ring, a script), r12 BLE, r13 the
closing audit (stack watermarks, heap floors, resets, faults, E lines, the
console, the supply current) and the restore.

Rig: the DUT on the bench AP reached through an ssh tunnel (--dut
localhost:18081; test.ps1 opens it, --tunnel opens one here), the Pi
(--pi) for the Pi-side scripts, the receiver and the broker, the DUT's
console port (--console, by role when omitted) for the flash and the tail,
the OWON PSU for the supply current, the ECU simulator answering on the
bus. The DUT holds the canonical bench settings (TESTING.md).

  python release_test.py [--dut localhost:18081] [--dut-ip 10.42.1.194]
      [--pi rpi001] [--pi-ip 10.42.1.1] [--port 8197] [--image build/x.bin]
      [--console COMx] [--no-flash] [--tunnel] [--only r1,r5] [--cap-s 900]
      [--logdir DIR] [--record-dir test-reports/release]

Verdict line: RELEASE TEST PASS / FAIL (PARTIAL with --only or --no-flash:
the publisher refuses those records).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, os.path.join(REPO, "tools"))
import bench_ports  # noqa: E402
import fw_image  # noqa: E402
import pcbench  # noqa: E402

PROJECT = "wican-fw_obd_pro"
ELF_SHA_OFF = fw_image.DESC_OFF + 144          # the descriptor's elf_sha256
VERDICT = "RELEASE TEST"
STEPS = [  # id, title, budget_s
    ("r0", "preflight: rig, image, the canonical settings, the matrix", 60),
    ("r1", "flash the release set and boot", 150),
    ("r2", "boot health", 20),
    ("r3", "web UI and API", 20),
    ("r4", "settings round trip (submit, reboot, read back)", 90),
    ("r5", "OBD: autopid values and ELM over ws_obd", 120),
    ("r6", "CAN frames flow", 30),
    ("r7", "MQTT at the broker", 40),
    ("r8", "HA webhook push (the PSRAM-stack canary)", 150),
    ("r9", "the updater's check", 60),
    ("r10", "OTA upload of the same image", 150),
    ("r11", "the writers: backup copy, log ring, a script", 45),
    ("r12", "BLE", 30),
    ("r13", "closing audit and restore", 300),
]
# PSRAM-stack tasks and the step that drives them (section 5 of the design)
MATRIX = {
    "autopid": "r5", "apid_dtc": "r13", "ha_webhook": "r8",
    "mqtt_pub": "r7", "mqtt_link": "r7", "em_dispatch": "r7",
    "em_worker": "r7", "fw_update": "r9", "cb_copies": "r11",
    "dl_writer": "r11", "dl_can": "r11", "dl_fault": "r11",
    "log_sinks": "r11", "log_manager": "r2", "script_run": "r11",
    "se_load": "r11", "socket_net": "r6", "wm_reconnect": "r4",
    "ble_http": "r12", "ble_tx": "r12", "ble_j2534": "r12",
    "rtc_sntp": "r2", "batt_sampler": "r2", "batt_events": "r2",
    "imu_events": "r2", "imu_detector": "r2", "sd_detect": "r2",
    "usb_presence": "r2", "usb_net_dev": "r2", "can_bridge": "r6",
    "j2534_cdc": "reviewed", "config_mode": "reviewed",
    "data_dest": "r7", "vpn_state": "reviewed", "can_core_rx": "r6",
    "isotp": "reviewed", "j1939": "reviewed", "j2534": "reviewed",
    "j2534_rx": "reviewed", "espnl_link": "reviewed",
    "usb_acm_rx": "reviewed", "usb_acm_gps": "reviewed",
}
# "reviewed": no step drives the task on this rig (no dongle, no J2534 tester,
# no J1939 bus, no button): its flash touches are reviewed by hand (section 5
# of the design) until a step exists
OWN_TAGS = ("fw_update", "ha_webhooks", "autopid", "mqtt_manager",
            "can_manager", "config_backup", "script_engine", "ota_manager",
            "restart_tracker", "settings_manager", "wifi_manager", "api_http",
            "event_manager", "socket_manager", "bridge_manager", "park")
OTA_WORDS = ("new", "pending_verify", "valid", "invalid", "aborted",
             "undefined", "unknown")
CRASH = re.compile(r"Guru Meditation|wdt timeout|Task watchdog|Backtrace:|"
                   r"abort\(\) was called|assert failed|stack overflow|"
                   r"CORRUPT HEAP|WICAN PARK|crash-loop brake", re.I)


class Run:
    """PASS/FAIL lines, metrics, the per-step ledger."""

    def __init__(self):
        self.checks = []          # (step, name, ok, detail)
        self.metrics = {}
        self.step = "r0"

    def check(self, name, ok, detail=""):
        ok = bool(ok)
        self.checks.append((self.step, name, ok, str(detail)))
        print(("PASS: " if ok else "FAIL: ") + name
              + ("  (%s)" % str(detail)[:300] if detail else ""), flush=True)
        return ok

    def metric(self, name, value, unit=""):
        self.metrics[name] = value
        print("METRIC %s=%s%s" % (name, value, unit), flush=True)

    def failed_in(self, step):
        return [c for c in self.checks if c[0] == step and not c[2]]


class Skip(Exception):
    """A step whose instrument is absent: SKIP with the reason."""


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def sh(cmd, timeout, cwd=None):
    """A subprocess: (exit code, combined text)."""
    try:
        p = subprocess.run(cmd, cwd=cwd, timeout=timeout, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        return -1, "timeout after %s s: %s" % (timeout, (e.stdout or b"")[-2000:])


def ssh(pi, cmd, timeout):
    return sh(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", pi, cmd], timeout)


def raw_post(base, path, data, ctype, timeout):
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def head(base, path, timeout=8):
    """(status, headers) of a GET whose body is thrown away."""
    req = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(4096)
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": str(e)}


def image_facts(path):
    data = open(path, "rb").read()
    info = fw_image.info(data)
    info["path"] = os.path.abspath(path)
    info["elf_id"] = data[ELF_SHA_OFF:ELF_SHA_OFF + 32].hex()[:16]
    return data, info


def current_image():
    code, out = sh([sys.executable, os.path.join(REPO, "tools", "fw_bin.py")], 30)
    if code != 0:
        raise pcbench.Bench("tools/fw_bin.py: " + out.strip()[:200])
    return out.strip().splitlines()[-1]


class ReleaseTest:
    def __init__(self, a):
        self.a = a
        self.run = Run()
        self.dut = pcbench.Dut(a.dut, own_tags=OWN_TAGS, tasks=("fw_update", "ha_webhook", "autopid"))
        self.base = "http://" + a.dut
        self.ledger = []
        self.skips = []
        self.found = {}             # settings as found, for the restore
        self.console = None
        self.console_log = os.path.join(a.logdir, "console.log")
        self.console_stop = self.console_log + ".stop"
        self.tunnel = None
        self.t_start = time.time()
        self.st0 = None
        self.image = None
        self.info = {}
        self.device_id = ""

    # ---- the frame -------------------------------------------------------------------

    def selected(self, sid):
        return not self.a.only or sid in self.a.only

    def do(self, sid, title, budget, fn):
        self.run.step = sid
        print("== %s %s (budget %d s) ==" % (sid, title, budget), flush=True)
        t0 = time.time()
        verdict, note = "PASS", ""
        if not self.selected(sid):
            verdict, note = "SKIP", "not selected"
        else:
            try:
                fn()
            except Skip as e:
                verdict, note = "SKIP", str(e)
            except pcbench.Bench as e:
                self.run.check("%s_no_bench_error" % sid, False, str(e))
            except Exception as e:  # a bug in the bench is a FAIL with its trace
                self.run.check("%s_no_exception" % sid, False, "%s: %s" % (type(e).__name__, e))
                traceback.print_exc()
        secs = round(time.time() - t0, 1)
        if verdict == "PASS" and self.run.failed_in(sid):
            verdict = "FAIL"
            note = "; ".join(c[1] for c in self.run.failed_in(sid))[:200]
        if verdict != "SKIP" and secs > budget:
            verdict = "FAIL"
            note = (note + "; " if note else "") + "over budget: %.0f s > %d s" % (secs, budget)
            self.run.check("%s_within_budget" % sid, False, "%.0f s > %d s" % (secs, budget))
        if verdict == "SKIP":
            self.skips.append({"step": sid, "reason": note})
        self.ledger.append({"id": sid, "title": title, "verdict": verdict,
                            "secs": secs, "budget_s": budget, "note": note})
        print("[%s] %s %s (%.1f s)%s" % (verdict, sid, title, secs, "  " + note if note else ""),
              flush=True)

    def wait_up(self, deadline_s=150):
        t0 = time.time()
        while time.time() - t0 < deadline_s:
            code, _ = self.dut.api("/api/info", timeout=4)
            if code == 200:
                return time.time() - t0
            time.sleep(2)
        return None

    def wait_reboot(self, b0, deadline_s=150):
        t0 = time.time()
        time.sleep(3)
        while time.time() - t0 < deadline_s:
            b = self.dut.boot_count()
            if b is not None and b != b0:
                self.dut.restarts += 1
                return time.time() - t0
            time.sleep(1)
        return None

    def counting_reboots(self, fn):
        """Run fn; the boots it caused (a bench that submits settings) join
        the ledger the closing reset check compares against."""
        b0 = self.dut.boot_count()
        try:
            return fn()
        finally:
            b1 = self.dut.boot_count()
            if b0 is not None and b1 is not None and b1 > b0:
                self.dut.restarts += b1 - b0

    def start_console(self):
        port = self.a.console
        if not port:
            return
        if os.path.exists(self.console_stop):
            os.remove(self.console_stop)
        tail = os.path.join(HERE, "..", "lib", "console_tail.py")
        self.console = subprocess.Popen(
            [sys.executable, "-u", tail, port, self.console_log, "--secs",
             str(self.a.cap_s + 300), "--stop-file", self.console_stop],
            stdout=open(self.console_log + ".keys", "w"), stderr=subprocess.STDOUT)
        time.sleep(1.5)

    def stop_console(self):
        if self.console is None:
            return
        open(self.console_stop, "w").close()
        try:
            self.console.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.console.kill()
        self.console = None

    # ---- the steps -------------------------------------------------------------------

    def r0_preflight(self):
        a, run = self.a, self.run
        path = a.image or current_image()
        self.image, self.info = image_facts(path)
        info = self.info
        run.metric("image_path", info["path"])
        run.metric("image_sha256", info["sha256"])
        run.metric("image_version", info["version"])
        run.metric("image_elf_id", info["elf_id"])
        run.check("image_is_a_wican_pro_image",
                  info["has_descriptor"] and info["project_name"] == PROJECT
                  and info["checksum_ok"] and info["digest_ok"] is not False,
                  "%s %s" % (info["project_name"], info["version"]))
        vtxt = open(os.path.join(REPO, "version.txt"), encoding="utf-8").read().strip()
        run.check("image_version_is_version_txt", info["version"] == vtxt,
                  "%s vs %s" % (info["version"], vtxt))
        code, out = ssh(a.pi, "true", 20)
        run.check("pi_answers", code == 0, out.strip()[:120])
        code, inf = self.dut.api("/api/info", timeout=6)
        run.check("dut_answers", code == 200 and isinstance(inf, dict),
                  "%s %s" % (code, (inf or {}).get("fw_build") if isinstance(inf, dict) else inf))
        if code == 200:
            self.device_id = inf.get("device_id", "")
        if not a.no_flash:
            run.check("console_port_known", bool(a.console), a.console or "none by role")
        psu = os.path.join(HERE, "..", "lib", "owon_psu.py")
        code, out = sh([sys.executable, psu, "status"], 40)
        m = re.search(r"(\d+) mA", out)
        run.check("psu_answers", code == 0 and m is not None, out.strip()[-120:])
        if m:
            run.metric("supply_ma_before", int(m.group(1)))
        # the canonical settings this test relies on
        mq = self.dut.settings("mqtt_manager")
        run.check("canonical_mqtt_enabled", mq.get("enabled") is True, json.dumps(mq)[:120])
        can = self.dut.get("/api/can")
        run.check("canonical_can_enabled", can.get("enabled") is True, can.get("state"))
        self.found["autopid"] = self.dut.settings("autopid")
        self.found["ha_webhooks"] = self.dut.settings("ha_webhooks")
        self.found["script_engine"] = self.dut.settings("script_engine")
        # the coverage matrix against the audit's task table
        code, out = sh([sys.executable, os.path.join(REPO, "tools", "stack_audit.py"),
                        "--tasks-json"], 120)
        unmapped = []
        if code == 0:
            try:
                tasks = json.loads(out[out.index("["):])
                psram = [t["name"] for t in tasks if t.get("psram") is True]
                unmapped = sorted(set(n for n in psram if n not in MATRIX))
                run.metric("psram_tasks", len(psram))
            except (ValueError, KeyError) as e:
                run.metric("matrix_note", "task table unreadable: %s" % e)
        else:
            run.metric("matrix_note", "stack_audit.py --tasks-json failed")
        run.metric("psram_tasks_unmapped", ",".join(unmapped) or "none")
        if unmapped:
            print("WARN: PSRAM-stack tasks with no release-test step: %s" % ", ".join(unmapped))
        self.dut.as_found()
        self.st0 = self.dut.get("/api/status")

    def r1_flash(self):
        a, run = self.a, self.run
        if a.no_flash:
            self.start_console()
            raise Skip("--no-flash: the image already on the DUT is tested, the record says so")
        build = os.path.dirname(self.info["path"])
        cmd = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "-p", a.console,
               "-b", "460800", "--before", "default-reset", "--after", "hard-reset",
               "write-flash", "@flash_args"]
        t0 = time.time()
        code, out = sh(cmd, 240, cwd=build)
        open(os.path.join(a.logdir, "flash.log"), "w", encoding="utf-8").write(out)
        run.metric("flash_s", round(time.time() - t0, 1))
        run.check("flash_wrote_and_verified", code == 0 and "Hash of data verified" in out,
                  out.strip().splitlines()[-1][:160] if out.strip() else "no output")
        self.start_console()
        up = self.wait_up(150)
        run.check("dut_up_after_flash", up is not None, "%s s" % (round(up, 1) if up else "never"))
        if up is None:
            raise pcbench.Bench("the DUT did not come back after the flash" + pcbench.GONE_HINT)
        time.sleep(5)
        inf = self.dut.get("/api/info")
        hist = self.dut.get("/api/restart/history")
        run.metric("fw_build", inf.get("fw_build"))
        run.check("running_version_is_the_image", inf.get("fw_version") == self.info["version"],
                  "%s vs %s" % (inf.get("fw_version"), self.info["version"]))
        run.check("running_elf_is_the_image", hist.get("elf_sha") == self.info["elf_id"],
                  "%s vs %s" % (hist.get("elf_sha"), self.info["elf_id"]))
        run.check("fresh_boot_count_1", hist.get("boot_count") == 1, hist.get("boot_count"))
        self.dut.as_found()
        self.st0 = self.dut.get("/api/status")

    def r2_boot_health(self):
        run = self.run
        st = self.dut.get("/api/status")
        hist = self.dut.get("/api/restart/history")
        faults = self.dut.get("/api/faults")
        h = st.get("health", {})
        run.check("no_unexpected_reset_at_boot", st.get("unexpected_resets") == 0, st.get("unexpected_resets"))
        br = hist.get("brake", {})
        run.check("brake_normal", br.get("verdict") == "normal" and br.get("streak") == 0, json.dumps(br)[:120])
        run.check("no_latched_faults", faults.get("faults") == [], json.dumps(faults)[:120])
        # the bench is air-gapped: the updater's scheduled check fails there
        # with IDF's connect-failure E lines, so the count is a metric and
        # the assertion is on our own tags (console_verdict, passing_checks)
        run.metric("log_errors_at_boot", h.get("log_errors"))
        caps = h.get("caps", {})
        tight = {k: v for k, v in caps.items() if isinstance(v, dict) and v.get("cap", 0) - v.get("used", 0) < 2}
        run.check("registry_headroom_at_least_2", not tight, json.dumps(tight) if tight else json.dumps(caps)[:200])
        fl = h.get("flash", {})
        run.metric("boot_flash_writes", fl.get("writes"))
        run.metric("boot_flash_erases", fl.get("erases"))
        if not self.a.no_flash:
            run.check("boot_flash_within_budget",
                      isinstance(fl.get("writes"), int) and fl["writes"] <= 64 and fl.get("erases", 0) <= 16,
                      json.dumps(fl))
        mem = st.get("memory", {}).get("internal", {})
        run.metric("internal_free_at_boot_b", mem.get("free"))
        run.metric("internal_largest_block_b", mem.get("largest_block"))
        run.check("internal_largest_block_at_least_16k", (mem.get("largest_block") or 0) >= 16384, mem.get("largest_block"))

    def r3_ui_api(self):
        run = self.run
        code, hdr = head(self.base, "/")
        etag = {k.lower(): v for k, v in hdr.items()}.get("etag", "")
        run.check("index_200_with_etag", code == 200 and bool(etag), "%s %s" % (code, etag))
        s = self.dut.get("/api/settings")
        comps = s.get("components") if isinstance(s, dict) and "components" in s else s
        n = len(comps) if isinstance(comps, (dict, list)) else 0
        run.metric("settings_components", n)
        run.check("settings_lists_every_component", n >= 40, n)
        code, sch = self.dut.api("/api/settings/wifi_manager/schema")
        run.check("schema_route_answers", code == 200 and isinstance(sch, dict), code)
        code, tasks = self.dut.api("/api/status/tasks")
        run.check("tasks_route_answers", code == 200 and isinstance(tasks, dict) and tasks.get("tasks"),
                  "%s tasks" % len((tasks or {}).get("tasks", [])) if isinstance(tasks, dict) else code)

    def r4_settings_round_trip(self):
        run = self.run
        cli_found = self.found["ha_webhooks"].get("cli", True)
        # autopid on for r5/r6, the script engine on for r11 (off in the
        # canonical bench set), one harmless flip that always makes the
        # submit a real one; the restore puts all three back as found
        changes = {"autopid": {"enabled": True}, "ha_webhooks": {"cli": not cli_found},
                   "script_engine": {"enabled": True}}
        b0 = self.dut.boot_count()
        t0 = time.time()
        self.dut.restart(changes)
        run.metric("submit_reboot_s", round(time.time() - t0, 1))
        time.sleep(4)
        hist = self.dut.get("/api/restart/history")
        rec = (hist.get("records") or [{}])[0]
        run.check("planned_config_apply_recorded",
                  rec.get("planned") is True and rec.get("planned_reason") == "config_apply",
                  "%s/%s" % (rec.get("reason"), rec.get("planned_reason")))
        ap = self.dut.settings("autopid")
        hw = self.dut.settings("ha_webhooks")
        run.check("settings_read_back_after_reboot",
                  ap.get("enabled") is True and hw.get("cli") == (not cli_found),
                  "autopid.enabled %s, ha_webhooks.cli %s" % (ap.get("enabled"), hw.get("cli")))
        run.check("boot_count_advanced_once", self.dut.boot_count() == (b0 or 0) + 1, self.dut.boot_count())

    def r5_obd(self):
        a, run = self.a, self.run
        t0 = time.time()
        got = 0
        while time.time() - t0 < 30:
            code, ap = self.dut.api("/api/autopid")
            if code == 200 and isinstance(ap, dict):
                got = sum(1 for p in ap.get("params", []) if p.get("value") is not None)
                if got >= 3:
                    break
            time.sleep(2)
        run.metric("autopid_params_with_values", got)
        run.metric("autopid_first_values_s", round(time.time() - t0, 1))
        run.check("autopid_polls_the_simulator", got >= 3, "%d params with values in %.0f s" % (got, time.time() - t0))
        # the WS test pauses the autopid groups itself, but its first command
        # then races the poll it interrupted and the chip answers STOPPED
        # (first run, 2026-10-21): pause here, let the chip settle, then run
        cfg = self.dut.get("/api/autopid/config")
        groups = [g["name"] for g in cfg.get("groups", []) if g.get("enabled")]
        for g in groups:
            self.dut.api("/api/autopid/group", "POST", {"name": g, "enabled": False})
        time.sleep(2.5)
        code, out = ssh(a.pi, "cd ~/wican && python3 -u tools/testbench/wifi/ws_live_test.py %s" % a.dut_ip, 100)
        open(os.path.join(a.logdir, "ws_live.log"), "w", encoding="utf-8").write(out)
        run.check("elm_over_ws_obd", "WS LIVE PASS" in out, out.strip().splitlines()[-1][:160] if out.strip() else "no output")
        for g in groups:
            self.dut.api("/api/autopid/group", "POST", {"name": g, "enabled": True})

    def r6_can(self):
        run = self.run
        # after the WS session the chip hands the bus back to autopid with a
        # grace period (the single-master rule): wait for the frames to flow
        # before the window is measured (6 frames in 10 s on the first run)
        t0 = time.time()
        c0 = self.dut.get("/api/can")
        while time.time() - t0 < 30:
            time.sleep(2)
            c = self.dut.get("/api/can")
            if (c.get("rx") or 0) - (c0.get("rx") or 0) >= 5:
                break
            c0 = c
        run.metric("can_traffic_wait_s", round(time.time() - t0, 1))
        c0 = self.dut.get("/api/can")
        time.sleep(10)
        c1 = self.dut.get("/api/can")
        rx = (c1.get("rx") or 0) - (c0.get("rx") or 0)
        errs = sum((c1.get(k) or 0) - (c0.get(k) or 0) for k in ("bus_errors", "rx_missed", "rx_overrun", "tx_errors"))
        run.metric("can_rx_in_10s", rx)
        run.metric("can_error_delta", errs)
        run.check("can_running", c1.get("state") == "running", c1.get("state"))
        run.check("can_frames_flow", rx >= 20, "%d frames in 10 s" % rx)
        run.check("can_no_errors", errs == 0, "errors +%d" % errs)

    def r7_mqtt(self):
        a, run = self.a, self.run
        st = self.dut.get("/api/status")
        bits = st.get("bits", {})
        connected = bits.get("mqtt_connected") if isinstance(bits, dict) else None
        if connected is None and isinstance(bits, dict):
            connected = any(k.startswith("mqtt") and v for k, v in bits.items())
        run.check("status_says_mqtt_connected", bool(connected), json.dumps(bits)[:200])
        topic = "wican/%s/#" % self.device_id
        code, out = ssh(a.pi, "timeout 20 mosquitto_sub -h 127.0.0.1 -v -t '%s'" % topic, 40)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        open(os.path.join(a.logdir, "mqtt.log"), "w", encoding="utf-8").write(out)
        run.metric("mqtt_messages_in_20s", len(lines))
        run.check("mqtt_messages_at_the_broker", len(lines) >= 1, (lines[0][:120] if lines else "none on " + topic))
        st_line = [ln for ln in lines if ln.split(" ", 1)[0].endswith("/status")]
        run.check("mqtt_status_topic_online", any('"online"' in ln for ln in st_line), st_line[0][:120] if st_line else "no status message")

    def r8_ha_webhook(self):
        a, run = self.a, self.run
        bench = os.path.join(HERE, "..", "ha", "ha_webhook_sta_ip_bench.py")
        code, out = self.counting_reboots(lambda: sh(
            [sys.executable, "-u", bench, "--dut", a.dut, "--pi", a.pi,
             "--pi-ip", a.pi_ip, "--port", str(a.port)], 170))
        open(os.path.join(a.logdir, "ha_sta_ip.log"), "w", encoding="utf-8").write(out)
        fails = [ln for ln in out.splitlines() if ln.startswith("FAIL:")]
        run.check("ha_webhook_sta_ip_bench", "HA WEBHOOK STA IP PASS" in out,
                  "; ".join(fails)[:200] if fails else out.strip().splitlines()[-1][:120] if out.strip() else "no output")
        run.check("ha_push_carries_the_updater_keys", "fw_update_available" in out and "fw_latest_version" in out,
                  "the three keys of 2026-10-18 on the first push")

    def r9_updater(self):
        run = self.run
        st = self.dut.get("/api/fw_update")
        rb = st.get("rollback", {})
        run.metric("ota_state", rb.get("ota_state"))
        run.check("updater_snapshot_sane",
                  rb.get("running") in ("ota_0", "ota_1") and rb.get("ota_state") in OTA_WORDS
                  and (rb.get("bootloader_version") or 0) >= 1, json.dumps(rb)[:200])
        code, r = self.dut.api("/api/fw_update/check", "POST", {})
        if code in (403, 409, 503):
            run.metric("updater_check_note", "refused %s %s" % (code, json.dumps(r)[:120]))
            run.check("updater_check_refusal_is_a_known_one", True, "the checker is off or busy: %s" % code)
            return
        run.check("updater_check_accepted", code in (200, 202), "%s %s" % (code, json.dumps(r)[:120]))
        t0 = time.time()
        last = None
        while time.time() - t0 < 45:
            st = self.dut.get("/api/fw_update")
            job = st.get("job") or {}
            last = st.get("last_check")
            if not job.get("active") and last:
                break
            time.sleep(2)
        run.metric("updater_check_s", round(time.time() - t0, 1))
        run.metric("updater_check_result", json.dumps(last)[:120] if last else "none")
        run.check("updater_check_concluded", last is not None and (last.get("ok") or last.get("error")),
                  json.dumps(last)[:160] if last else "the check never concluded")
        code, tasks = self.dut.api("/api/status/tasks")
        names = [t.get("name") for t in (tasks or {}).get("tasks", [])] if isinstance(tasks, dict) else []
        run.check("fw_update_task_alive", "fw_update" in names, "%d tasks" % len(names))

    def r10_ota_upload(self):
        a, run = self.a, self.run
        inf0 = self.dut.get("/api/info")
        b0 = self.dut.boot_count()
        t0 = time.time()
        code, txt = raw_post(self.base, "/api/ota/upload", self.image, "application/octet-stream", 300)
        run.metric("ota_upload_s", round(time.time() - t0, 1))
        run.check("ota_upload_accepted", code == 200, "%s %s" % (code, txt[:120]))
        if code != 200:
            return
        back = self.wait_reboot(b0, 150)
        run.check("dut_back_after_ota", back is not None, "%s s" % (round(back, 1) if back else "never"))
        if back is None:
            raise pcbench.Bench("the DUT did not come back after the OTA upload" + pcbench.GONE_HINT)
        time.sleep(5)
        inf1 = self.dut.get("/api/info")
        hist = self.dut.get("/api/restart/history")
        rec = (hist.get("records") or [{}])[0]
        run.check("ota_apply_restart_recorded", rec.get("planned_reason") == "ota_apply",
                  "%s/%s" % (rec.get("reason"), rec.get("planned_reason")))
        run.check("other_slot_runs_the_same_image",
                  inf1.get("partition") != inf0.get("partition") and inf1.get("fw_build") == inf0.get("fw_build"),
                  "%s -> %s, %s" % (inf0.get("partition"), inf1.get("partition"), inf1.get("fw_build")))
        run.check("running_elf_still_the_image", hist.get("elf_sha") == self.info["elf_id"], hist.get("elf_sha"))
        st = self.dut.get("/api/status")
        run.check("no_unexpected_reset_on_the_other_slot",
                  st.get("unexpected_resets") == self.st0.get("unexpected_resets"),
                  "unexpected_resets %s" % st.get("unexpected_resets"))
        run.metric("log_errors_after_ota", st.get("health", {}).get("log_errors"))
        self.dut.sweep()

    def r11_writers(self):
        run = self.run
        code, r = self.dut.api("/api/backup/copies/refresh", "POST", {}, timeout=30)
        run.check("backup_copy_written", code == 200 and "internal" in (r.get("written") or []) if isinstance(r, dict) else False,
                  "%s %s" % (code, json.dumps(r)[:120]))
        code, s = self.dut.api("/api/backup/status", timeout=20)
        run.check("backup_status_answers", code == 200 and isinstance(s, dict), code)
        ring = self.dut.ring_lines()
        run.check("log_ring_exports", len(ring) >= 10, "%d lines" % len(ring))
        se = self.dut.settings("script_engine")
        if se.get("enabled"):
            code, r = self.dut.api("/api/scripts/run", "POST", {"src": 'print("release test ok")'}, timeout=20)
            run.check("script_runs", code == 200 and isinstance(r, dict) and r.get("ok")
                      and "release test ok" in str(r.get("output", "")), "%s %s" % (code, json.dumps(r)[:120]))
        else:
            run.metric("script_leg", "skipped: the script engine is off on this device")

    def r12_ble(self):
        run = self.run
        code, b = self.dut.api("/api/ble")
        if code != 200 or not isinstance(b, dict):
            raise Skip("no /api/ble (%s)" % code)
        if not b.get("enabled"):
            raise Skip("BLE is disabled in the DUT's settings (the canonical bench set)")
        run.check("ble_advertising", bool(b.get("advertising")), json.dumps(b)[:160])

    def r13_closing(self):
        a, run = self.a, self.run
        # the Pi's audit submits a setting and restores it: two planned reboots
        code, out = self.counting_reboots(lambda: ssh(
            a.pi, "cd ~/wican && python3 -u tools/testbench/system/stack_audit_test.py %s" % a.dut_ip, 200))
        open(os.path.join(a.logdir, "stack_audit.log"), "w", encoding="utf-8").write(out)
        run.check("runtime_stack_audit", "STACK AUDIT PASS" in out,
                  out.strip().splitlines()[-1][:160] if out.strip() else "no output")
        time.sleep(3)
        self.dut.sweep()
        self.restore()
        self.dut.reset_check(run, self.st0)
        faults = self.dut.get("/api/faults")
        run.check("close_no_latched_faults", faults.get("faults") == [], json.dumps(faults)[:120])
        st = self.dut.get("/api/status")
        run.metric("log_errors_at_close", st.get("health", {}).get("log_errors"))
        self.dut.passing_checks(run)
        self.stop_console()
        self.console_verdict()
        psu = os.path.join(HERE, "..", "lib", "owon_psu.py")
        code, out = sh([sys.executable, psu, "status"], 40)
        m = re.search(r"(\d+) mA", out)
        if m:
            ma = int(m.group(1))
            run.metric("supply_ma_after", ma)
            run.check("supply_current_awake_band", 90 <= ma <= 300, "%d mA" % ma)

    def restore(self):
        """The settings as found, one submit when anything differs."""
        changes = {}
        ap = self.found.get("autopid", {})
        hw = self.found.get("ha_webhooks", {})
        se = self.found.get("script_engine", {})
        if "enabled" in ap:
            changes["autopid"] = {"enabled": ap["enabled"]}
        if "cli" in hw:
            changes["ha_webhooks"] = {"cli": hw["cli"]}
        if "enabled" in se:
            changes["script_engine"] = {"enabled": se["enabled"]}
        try:
            cur_ap = self.dut.settings("autopid")
            cur_hw = self.dut.settings("ha_webhooks")
            cur_se = self.dut.settings("script_engine")
        except pcbench.Bench:
            return
        if changes and (cur_ap.get("enabled") != ap.get("enabled") or cur_hw.get("cli") != hw.get("cli")
                        or cur_se.get("enabled") != se.get("enabled")):
            t0 = time.time()
            self.dut.restart(changes)
            self.run.metric("restore_reboot_s", round(time.time() - t0, 1))
            time.sleep(3)

    def console_verdict(self):
        run = self.run
        if not os.path.isfile(self.console_log):
            run.metric("console_note", "no console capture")
            return
        lines = open(self.console_log, encoding="utf-8", errors="replace").read().splitlines()
        crash = [ln for ln in lines if CRASH.search(ln)]
        run.metric("console_lines", len(lines))
        run.check("console_no_crash_line", not crash, " | ".join(crash)[:300])
        # our own tags fail the run; IDF's (esp-tls, mqtt_client, ...) are a
        # metric: the air-gapped bench and the reboots make them log
        own, other = {}, {}
        for ln in lines:
            m = re.search(r"\bE \(\d+\) ([A-Za-z0-9_.-]+):", ln)
            if not m:
                continue
            tag = m.group(1)
            bucket = own if tag in OWN_TAGS else other
            bucket[tag] = bucket.get(tag, 0) + 1
        run.metric("console_e_lines_other_tags", json.dumps(other))
        run.check("console_no_E_lines_of_own_tags", not own, json.dumps(own))

    # ---- the whole run --------------------------------------------------------------

    def record(self, verdict):
        rec = {
            "verdict": verdict,
            "complete": not self.a.only and not self.a.no_flash,
            "flashed": not self.a.no_flash,
            "image_sha256": self.info.get("sha256"),
            "elf_id": self.info.get("elf_id"),
            "version": self.info.get("version"),
            "project": self.info.get("project_name"),
            "fw_build": self.run.metrics.get("fw_build"),
            "date": utc_now(),
            "host": socket.gethostname(),
            "duration_s": round(time.time() - self.t_start, 1),
            "cap_s": self.a.cap_s,
            "steps": self.ledger,
            "skips": self.skips,
            "checks": {"passed": sum(1 for c in self.run.checks if c[2]),
                       "failed": sum(1 for c in self.run.checks if not c[2])},
            "metrics": self.run.metrics,
            "logdir": os.path.abspath(self.a.logdir),
        }
        os.makedirs(self.a.record_dir, exist_ok=True)
        sha = self.info.get("sha256") or "unknown"
        path = os.path.join(self.a.record_dir, sha + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2)
        with open(os.path.join(self.a.logdir, "record.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2)
        print("record: " + path)
        return rec

    def main(self):
        a = self.a
        os.makedirs(a.logdir, exist_ok=True)
        if a.tunnel:
            self.tunnel = subprocess.Popen(["ssh", "-N", "-o", "BatchMode=yes", "-o",
                                            "ExitOnForwardFailure=yes", "-L",
                                            "18081:%s:80" % a.dut_ip, a.pi])
            time.sleep(3)
        fns = {"r0": self.r0_preflight, "r1": self.r1_flash, "r2": self.r2_boot_health,
               "r3": self.r3_ui_api, "r4": self.r4_settings_round_trip, "r5": self.r5_obd,
               "r6": self.r6_can, "r7": self.r7_mqtt, "r8": self.r8_ha_webhook,
               "r9": self.r9_updater, "r10": self.r10_ota_upload, "r11": self.r11_writers,
               "r12": self.r12_ble, "r13": self.r13_closing}
        try:
            for sid, title, budget in STEPS:
                if sid == "r1" and (not self.selected("r0") or self.run.failed_in("r0")):
                    self.run.check("r0_passed_before_the_flash", False, "the preflight failed: no flash")
                    break
                self.do(sid, title, budget, fns[sid])
                if sid == "r1" and self.run.failed_in("r1"):
                    break
                if time.time() - self.t_start > a.cap_s:
                    self.run.check("whole_run_within_cap", False,
                                   "%.0f s > %d s" % (time.time() - self.t_start, a.cap_s))
                    break
        finally:
            if "r13" not in [s["id"] for s in self.ledger]:
                try:
                    self.restore()
                except Exception as e:
                    print("restore: " + str(e))
            self.stop_console()
            if self.tunnel is not None:
                self.tunnel.kill()
        total = time.time() - self.t_start
        self.run.metric("total_s", round(total, 1))
        if total > a.cap_s:
            self.run.check("whole_run_within_cap", False, "%.0f s > %d s" % (total, a.cap_s))
        failed = [c for c in self.run.checks if not c[2]]
        partial = bool(a.only) or a.no_flash
        verdict = "FAIL" if failed else ("PARTIAL PASS" if partial else "PASS")
        self.record("PASS" if verdict == "PASS" else ("PARTIAL" if verdict == "PARTIAL PASS" else "FAIL"))
        print("== summary ==")
        for s in self.ledger:
            print("  %-4s %-6s %6.1f s  %s%s" % (s["id"], s["verdict"], s["secs"], s["title"],
                                                 "  " + s["note"] if s["note"] else ""))
        print("  checks: %d passed, %d failed, %d skipped step(s), %.0f s of %d"
              % (len(self.run.checks) - len(failed), len(failed), len(self.skips), total, a.cap_s))
        if failed:
            print("  failed: " + "; ".join("%s %s" % (c[0], c[1]) for c in failed)[:600])
        print("%s %s" % (VERDICT, verdict))
        return 0 if verdict == "PASS" else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081", help="host:port of the DUT's API (a tunnel)")
    ap.add_argument("--dut-ip", default="10.42.1.194", help="the DUT as the Pi reaches it")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--pi-ip", default="10.42.1.1", help="the Pi as the DUT reaches it")
    ap.add_argument("--port", type=int, default=8197, help="the HA receiver's port on the Pi")
    ap.add_argument("--image", help="the image to test (default: tools/fw_bin.py, the current build)")
    ap.add_argument("--console", help="the DUT's console port (default: by role)")
    ap.add_argument("--no-flash", action="store_true", help="test the image already on the DUT (a PARTIAL record)")
    ap.add_argument("--tunnel", action="store_true", help="open the ssh tunnel here")
    ap.add_argument("--only", help="comma list of steps (a PARTIAL record)")
    ap.add_argument("--cap-s", type=int, default=900)
    ap.add_argument("--logdir", help="default test-reports/logs/release_<stamp>")
    ap.add_argument("--record-dir", default=os.path.join(REPO, "test-reports", "release"))
    a = ap.parse_args()
    a.only = set(x.strip() for x in a.only.split(",")) if a.only else None
    if a.only:
        a.only.add("r0")
    if not a.logdir:
        a.logdir = os.path.join(REPO, "test-reports", "logs",
                                "release_" + time.strftime("%Y%m%d_%H%M%S"))
    if not a.console:
        try:
            a.console = bench_ports.port(("wican_console", "ch342_console"))
        except Exception:
            a.console = None
    print("release test: dut %s (%s via %s), console %s, cap %d s, logs %s"
          % (a.dut, a.dut_ip, a.pi, a.console or "none", a.cap_s, a.logdir))
    return ReleaseTest(a).main()


if __name__ == "__main__":
    sys.exit(main())
