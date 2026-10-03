#!/usr/bin/env python3
"""What a bench that runs on the PC needs, in one place.

Such a bench drives the DUT over its HTTP API (through an ssh tunnel, e.g.
`localhost:8081`), plays the vehicle with the PCAN adapter (an actor from
tools/testbench/actors as a child process) and judges observed state. This
module holds the parts every one of them repeats:

  Run     checks and METRIC lines in the conventions test.ps1 scrapes, and
          the verdict line
  Dut     the HTTP API: GET with retries, settings staged by GET-modify-PUT,
          a restart that waits for the next boot, and `sweep()`: what the
          device only says in passing (E lines of the bench's own log tags,
          the stack headroom of named tasks, the internal heap's floor),
          read before every restart because a restart resets two of them
  Actor   one run of an actor script: started, asked to stop through its
          `--stop-file` (never killed while it transmits: the PEAK driver
          wedges), its log read back

Benches written before this module (can_autobaud_bench.py, wwh_obd_bench.py)
carry their own copies of these helpers.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

STACK_FAIL_B = 512              # the stack audit's floors
INT_MIN_FREE_B = 20 * 1024


class Bench(Exception):
    """The rig did not do what a step needs: the run cannot go on."""


class Run:
    """Checks, metrics and the verdict of one bench run."""

    def __init__(self, name):
        self.name = name        # e.g. "J1939": the verdict is "<name> PASS"
        self.fails = []
        self.metrics = []

    def check(self, name, ok, detail=""):
        print(("PASS" if ok else "FAIL") + ": " + name
              + (f"  ({detail})" if detail != "" else ""), flush=True)
        if not ok:
            self.fails.append(name)
        return ok

    def metric(self, name, value, unit=""):
        self.metrics.append((name, value, unit))
        print(f"METRIC {name}={value}{(' ' + unit) if unit else ''}",
              flush=True)

    def verdict(self, partial=""):
        """Print the metrics again and the verdict line. Returns the exit
        code. `partial` (e.g. "legs: j1 j4") makes it a PARTIAL verdict."""
        for name, value, unit in self.metrics:
            print(f"  {name} = {value}{(' ' + unit) if unit else ''}")
        word = self.name + (" PARTIAL" if partial else "")
        if self.fails:
            print(f"{word} FAIL: " + ", ".join(self.fails))
            return 1
        print(f"{word} PASS" + (f" ({partial})" if partial else ""))
        return 0


class Dut:
    """The DUT over HTTP."""

    def __init__(self, hostport, own_tags=(), tasks=()):
        self.base = "http://" + hostport
        self.own_tags = tuple(own_tags)   # log tags whose E lines fail a run
        self.tasks = tuple(tasks)         # tasks whose stack headroom is kept
        self.restarts = 0
        self.ring0 = set()                # the log ring as found
        self.seen_e = set()
        self.stack_min = {}               # task -> least headroom seen, bytes
        self.heap_min = None              # internal heap: least min_free seen

    # ---- plain HTTP -----------------------------------------------------------

    def api(self, path, method="GET", body=None, timeout=8):
        """(status, json or text); (0, reason) when the DUT does not answer."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code = r.status
                text = r.read().decode("utf-8", errors="replace")
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

    def get(self, path, tries=6):
        """GET that rides out a WiFi hiccup; Bench when the DUT stays away."""
        last = None
        for _ in range(tries):
            code, r = self.api(path)
            if code == 200:
                return r
            last = (code, r)
            time.sleep(1.5)
        raise Bench(f"GET {path}: {last}")

    # ---- settings and restarts --------------------------------------------------

    def settings(self, name):
        doc = self.get(f"/api/settings/{name}")
        return {k: v for k, v in doc.items()
                if k not in ("degraded", "pending_reboot")}

    def stage(self, name, vals):
        """GET, modify, PUT. True when something changed."""
        cur = self.settings(name)
        new = dict(cur)
        new.update(vals)
        if new == cur:
            return False
        code, r = self.api(f"/api/settings/{name}", "PUT", new)
        if code != 200:
            raise Bench(f"PUT {name}: HTTP {code} {r}")
        return True

    def boot_count(self):
        code, st = self.api("/api/status", timeout=4)
        return st.get("boot_count") if code == 200 and isinstance(st, dict) \
            else None

    def restart(self, changes=None):
        """Stage `changes` ({component: {key: value}}) and restart (submit
        when something changed, a plain restart otherwise). Returns the time
        the DUT answered again."""
        self.sweep()            # this boot's task list and heap floor end here
        changed = [n for n, v in (changes or {}).items() if self.stage(n, v)]
        b0 = self.boot_count()
        if b0 is None:
            raise Bench("no boot_count before a restart")
        t0 = time.time()
        # the reply can lose to the restart
        self.api("/api/settings/submit" if changed else "/api/restart",
                 "POST", {})
        time.sleep(3)
        while time.time() - t0 < 150:
            b = self.boot_count()
            if b is not None and b != b0:
                self.restarts += 1
                return time.time()
            time.sleep(0.5)
        raise Bench("the DUT did not come back within 150 s of a restart")

    # ---- what the device only says in passing ------------------------------------

    def ring_lines(self):
        """The log ring as plain lines (16 KB: it rolls; it survives a
        restart)."""
        code, t = self.api("/api/logs/ring", timeout=15)
        if code != 200 or not isinstance(t, str):
            return []
        return [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in t.splitlines()]

    def as_found(self):
        """Remember the log ring as it is: older lines are not this run's."""
        self.ring0.update(self.ring_lines())

    def sweep(self):
        """Keep the E lines of the own tags, the stack headroom of the named
        tasks and the internal heap's floor. Quiet when the DUT is away."""
        for ln in self.ring_lines():
            if ln in self.ring0 or not ln.startswith("E (") or ") " not in ln:
                continue
            if ln.split(") ", 1)[1].split(":", 1)[0].strip() in self.own_tags:
                self.seen_e.add(ln)
        code, t = self.api("/api/status/tasks")
        if code == 200 and isinstance(t, dict):
            for x in t.get("tasks", []):
                hw = x.get("stack_hw")
                if x.get("name") in self.tasks and isinstance(hw, int):
                    old = self.stack_min.get(x["name"])
                    if old is None or hw < old:
                        self.stack_min[x["name"]] = hw
        code, st = self.api("/api/status")
        if code == 200 and isinstance(st, dict):
            low = st.get("memory", {}).get("internal", {}).get("min_free")
            if isinstance(low, int) and \
                    (self.heap_min is None or low < self.heap_min):
                self.heap_min = low

    def passing_checks(self, run):
        """The closing checks on what sweep() collected."""
        self.sweep()
        new_e = sorted(self.seen_e)
        run.check("no_own_E_lines", not new_e, " | ".join(new_e)[:400])
        for name in self.tasks:
            if self.stack_min.get(name) is not None:
                run.metric(f"stack_{name}_headroom_b", self.stack_min[name])
        low = [n for n in self.tasks if self.stack_min.get(n) is None
               or self.stack_min[n] < STACK_FAIL_B]
        if self.tasks:
            run.check(f"stack_headroom_at_least_{STACK_FAIL_B}_B", not low,
                      ", ".join(f"{n} {self.stack_min.get(n)}"
                                for n in self.tasks))
        if self.heap_min is not None:
            run.metric("internal_heap_floor_b", self.heap_min)
        run.check(f"internal_heap_floor_at_least_{INT_MIN_FREE_B}_B",
                  self.heap_min is not None
                  and self.heap_min >= INT_MIN_FREE_B, f"{self.heap_min}")


class Actor:
    """One run of an actor script on the PCAN adapter. Its log is the wire's
    side of the story."""

    def __init__(self, script, logdir, prefix, ready, done):
        self.script = script
        self.logdir = logdir
        self.prefix = prefix    # log file name: <prefix>_<tag>.log
        self.ready = ready      # the line that says it is up
        self.done = done        # prefix of its closing JSON line
        self.p = None
        self.log = None
        self.stop_file = None

    def start(self, tag, actor_args, secs=900):
        """Start it (a running one is stopped first) and wait for READY."""
        self.stop()
        self.log = os.path.join(self.logdir, f"{self.prefix}_{tag}.log")
        self.stop_file = os.path.join(self.logdir, f"{self.prefix}_{tag}.stop")
        if os.path.exists(self.stop_file):
            os.remove(self.stop_file)
        out = open(self.log, "w")
        self.p = subprocess.Popen(
            [sys.executable, "-u", self.script, str(secs),
             "--stop-file", self.stop_file] + list(actor_args),
            stdout=out, stderr=subprocess.STDOUT)
        end = time.time() + 15
        while time.time() < end:
            if self.p.poll() is not None:
                raise Bench(f"the actor died at start: see {self.log}")
            if self.ready in self.text():
                return
            time.sleep(0.2)
        raise Bench(f"the actor did not come up: see {self.log}")

    def stop(self):
        """Ask for a clean stop. Returns the closing statistics, {} without."""
        if self.p is None:
            return {}
        open(self.stop_file, "w").close()
        try:
            self.p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.p.terminate()
            self.p.wait(timeout=10)
        self.p = None
        time.sleep(0.3)
        if os.path.exists(self.stop_file):
            os.remove(self.stop_file)
        for ln in self.text().splitlines():
            if ln.startswith(self.done):
                return json.loads(ln[len(self.done):])
        return {}

    def running(self):
        return self.p is not None and self.p.poll() is None

    def text(self):
        try:
            return open(self.log, encoding="utf-8", errors="replace").read()
        except (OSError, TypeError):
            return ""

    def events(self, prefix, start=0):
        """The JSON of every line that begins with `prefix`, from log line
        `start` on."""
        out = []
        for ln in self.text().splitlines()[start:]:
            if ln.startswith(prefix):
                try:
                    out.append(json.loads(ln[len(prefix):]))
                except ValueError:
                    continue
        return out

    def mark(self):
        return len(self.text().splitlines())
