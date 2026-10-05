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

# A DUT that stops answering: since 2026-10-05 the first suspect is the crash
# park (three runs in a row that crash within ten minutes of their start, and
# the firmware sleeps instead of starting a fourth time). A bench over HTTP
# cannot see it; the supply current and the console can.
GONE_HINT = (" [no answer: if the supply reads about 47 mA (`python "
             "tools/testbench/lib/owon_psu.py`) and the LED breathes red, the "
             "DUT PARKED itself after three crashes in a row: the console log "
             "has `WICAN PARK`, a PSU power cycle brings it back, and `GET "
             "/api/restart/report` then says what crashed. TESTING.md, \"When "
             "the DUT disappears in the middle of a test\"]")


class Bench(Exception):
    """The rig did not do what a step needs: the run cannot go on."""


def unplanned_boots(history, since_boot):
    """From a `/api/restart/history` reply: the boots after boot number
    `since_boot` that nobody asked for, as text for a failed reset check
    ('' when the tracker's 8 records hold none)."""
    recs = history.get("records", []) if isinstance(history, dict) else []
    out = [reset_text(r) for r in recs
           if not r.get("planned") and isinstance(r.get("seq"), int)
           and r["seq"] > since_boot]
    return ("; " + "; ".join(sorted(out))) if out else ""


def reset_text(rec):
    """One record of the restart history for a failed reset check: the reset
    reason and, since 2026-10-05, the crash note the boot filed (where it
    crashed: kind, PC, task, backtrace), with the backtrace as function
    names when the ELF of that very image is in build/ (lib/crash_note.py)."""
    try:
        import crash_note
        text = crash_note.one_line(rec)
        if isinstance(rec.get("crash"), dict):
            text += crash_note.decoded_line(rec["crash"])
        return text
    except Exception:  # noqa: BLE001  (a report must not die on its footnote)
        return f"boot {rec.get('seq')}: reset reason '{rec.get('reason')}'"


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
        self.unexpected = None            # unexpected_resets at the last look
        self.old_resets = set()           # unplanned boots older than the run
        self.reset_notes = []             # the tracker's word on this run's

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
        raise Bench(f"GET {path}: {last}"
                    + (GONE_HINT if last and last[0] == 0 else ""))

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
        raise Bench("the DUT did not come back within 150 s of a restart"
                    + GONE_HINT)

    # ---- what the device only says in passing ------------------------------------

    def ring_lines(self):
        """The log ring as plain lines (16 KB: it rolls; it survives a
        restart)."""
        code, t = self.api("/api/logs/ring", timeout=15)
        if code != 200 or not isinstance(t, str):
            return []
        return [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in t.splitlines()]

    def as_found(self):
        """Remember the log ring as it is: older lines are not this run's.
        The same for resets nobody asked for."""
        self.ring0.update(self.ring_lines())
        code, st = self.api("/api/status")
        if code == 200 and isinstance(st, dict):
            self.note_resets(st)

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
            self.note_resets(st)

    def note_resets(self, st):
        """A reset nobody asked for: keep what the restart tracker says about
        it NOW (its history holds 8 boots; a bench restarts more often than
        that, and the reason is gone by the closing check otherwise; the EU
        truck bench's first run, 2026-10-03). `st` = a /api/status reply."""
        n = st.get("unexpected_resets")
        if not isinstance(n, int) or n == self.unexpected:
            return
        code, h = self.api("/api/restart/history", timeout=15)
        recs = h.get("records", []) if code == 200 and isinstance(h, dict) \
            else []
        first = self.unexpected is None
        self.unexpected = n
        for r in recs:
            seq = r.get("seq")
            if r.get("planned") or seq in self.old_resets:
                continue
            self.old_resets.add(seq)
            if not first:               # the first look is the run's baseline
                self.reset_notes.append(reset_text(r))

    def reset_check(self, run, st0):
        """The closing check: no boot but the ones the bench asked for since
        `st0` (the /api/status of the start). A failure says what the restart
        tracker recorded."""
        st1 = self.get("/api/status")
        self.note_resets(st1)
        boots = st1.get("boot_count") - st0.get("boot_count")
        detail = (f"unexpected_resets {st0.get('unexpected_resets')} -> "
                  f"{st1.get('unexpected_resets')}, boot_count +{boots} for "
                  f"{self.restarts} restarts")
        if self.reset_notes:
            detail += "; " + "; ".join(self.reset_notes)
        return run.check("no_unexpected_reset",
                         st1.get("unexpected_resets")
                         == st0.get("unexpected_resets")
                         and boots == self.restarts, detail)

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
        """Start it (a running one is stopped first) and wait for READY. An
        actor that dies at once is started again, twice: the PEAK driver now
        and then refuses the first open after a pause of the adapter
        (`PcanCanInitializationError: ... irregularities were registered`)
        and takes the next one."""
        self.stop()
        self.log = os.path.join(self.logdir, f"{self.prefix}_{tag}.log")
        self.stop_file = os.path.join(self.logdir, f"{self.prefix}_{tag}.stop")
        if os.path.exists(self.stop_file):
            os.remove(self.stop_file)
        for attempt in range(3):
            out = open(self.log, "w")
            self.p = subprocess.Popen(
                [sys.executable, "-u", self.script, str(secs),
                 "--stop-file", self.stop_file] + list(actor_args),
                stdout=out, stderr=subprocess.STDOUT)
            end = time.time() + 15
            died = False
            while time.time() < end:
                if self.p.poll() is not None:
                    died = True
                    break
                if self.ready in self.text():
                    return
                time.sleep(0.2)
            out.close()
            if not died:
                raise Bench(f"the actor did not come up: see {self.log}")
            last = self.text().strip().splitlines()[-1:] or ["(no output)"]
            print(f"actor died at start (attempt {attempt + 1}): {last[0][:160]}",
                  flush=True)
            self.p = None
            time.sleep(2)
        raise Bench(f"the actor died at start: see {self.log}")

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
