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
  restore_bus_and_store()
          (2026-11-19) the restore of a bench that made the bench bus
          another vehicle's: ECU sim v2 as found first, the chip's protocol
          store, the unit's settings and one restart, then the vehicle store
          judged car by car with each car's protocol (Dut.off_the_bus,
          Dut.store_view, Dut.chip_store, Dut.chip_store_write)

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
GONE_HINT = (" [no answer: if the supply reads the sleep-level current (`python "
             "tools/testbench/lib/bench_psu.py`) and the LED breathes red, the "
             "DUT PARKED itself after three crashes in a row: the console log "
             "has `WICAN PARK`, a power cycle (`bench_psu.py cycle`) brings it back, and `GET "
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


def restore_bus_and_store(dut, run, found, found_sim, found_precan, found_view, found_chip, settle_s=25):
    """The restore of a bench that made the bench bus another vehicle's
    (bench_bus.sim_ack_only): ECU sim v2 as found FIRST, with nothing of the
    unit on the bus while the bit rate changes; then the chip's protocol
    store as found (one ATSP through the wire probe, when a detection of the
    bench's vehicle saved another); then the unit's settings `found` and ONE
    restart, and `settle_s` seconds for that boot's first contact. Judged:
    `restored_settings`, `restored_vehicle_store` (each car with its
    protocol, Dut.store_view(); `found_view` from before the run) and
    `restored_chip_store` (`found_chip`, Dut.chip_store() from before).

    Why this order (2026-11-19): restarted first with Automate on, beside a
    simulator that still only acknowledged on CAN and played the car on its
    K-line and J1850 pins, the unit's first contact ran the chip's protocol
    search, met the simulator's J1850 VPW ECU and stored protocol 2 for the
    bench car; a restore that compared the store by its keys passed that."""
    import bench_bus
    if bench_bus.sim_get("ecu_sim") != found_sim:
        dut.off_the_bus()
    bench_bus.sim_as_found(found_sim, found_precan)
    chip_moved = found_chip is not None and dut.chip_store() != found_chip
    chip_written = chip_moved and dut.chip_store_write(found_chip)
    now = {n: dut.settings(n) for n in found}
    if now != found or chip_moved:
        dut.restart(found)
        time.sleep(settle_s)
    now = {n: dut.settings(n) for n in found}
    run.check("restored_settings", now == found, "" if now == found else json.dumps(now)[:300])
    sv = dut.store_view()
    run.check("restored_vehicle_store", sv is not None and sv == found_view,
              json.dumps(sv)[:300] if sv != found_view
              else f"{len(sv['cars'])} cars, each on the protocol it had")
    chip_now = dut.chip_store()
    run.check("restored_chip_store", chip_now == found_chip,
              f"{found_chip} as found, {chip_now} now"
              + ("; put back with one ATSP through the wire probe" if chip_written else "")
              + ("; the wire probe's ATSP was not answered OK" if chip_moved and not chip_written else ""))


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

    # ---- the vehicle bus and what the unit holds about its cars ---------------------

    #: nothing of the unit on the vehicle bus
    OFF_THE_BUS = {"can_manager": {"enabled": False}, "j1939": {"enabled": False},
                   "autopid": {"enabled": False}}

    def off_the_bus(self):
        """Take the unit off the vehicle bus (Automate, its own CAN node and
        the J1939 listener off, one restart) when it polls or drives the bus.
        For the moment a bench turns the bench bus into another vehicle's
        bus at another bit rate: a unit left polling its car as found goes on
        transmitting meanwhile, and the adapter that plays the other vehicle
        can go bus-off in it before the unit ever restarts (2026-11-19, the
        EU truck bench: 3 of 3 runs with the unit as found, 0 of 2 with it
        off the bus first). Returns True when the unit was restarted."""
        if not (self.settings("autopid").get("enabled") or self.settings("can_manager").get("enabled")):
            return False
        self.restart(self.OFF_THE_BUS)
        return True

    def store_view(self):
        """The vehicle store as a bench compares it before and after: who is
        current, and every car with its name, its protocol and the protocol
        the unit believes the chip holds for it. The protocol belongs in it:
        a car's key and name stay when first contact re-learns its protocol
        from another bus (2026-11-19: two stages left the bench car on
        protocol 2 and compared keys only)."""
        code, v = self.api("/api/autopid/vehicles")
        if code != 200 or not isinstance(v, dict):
            return None
        return {"current": v.get("current"),
                "cars": sorted([e.get("key"), e.get("name"), e.get("protocol"), e.get("chip_protocol")]
                               for e in v.get("vehicles", []))}

    def chip_store(self):
        """The protocol the OBD chip itself has stored, as its driver read it
        back at the bring-up (None when the unit does not say)."""
        code, oc = self.api("/api/obd_chip")
        return (oc.get("eeprom_guard") or {}).get("stored_protocol") \
            if code == 200 and isinstance(oc, dict) else None

    def chip_store_write(self, proto):
        """Put `proto` into the chip's protocol store: ONE real `ATSP<proto>`
        through the wire probe (POST /api/obd_chip/probe), the way the store
        was put back by hand on 2026-11-16. The caller restarts the unit
        afterwards: the bring-up reads the store back (chip_store()) and
        sends ATE0 again, which the probe's raw word leaves off. For a bench
        whose own detection made the unit save another protocol: a truck's
        detection stores its protocol in the chip, and nothing of the unit
        puts the car's back, because the car's record still says the chip
        holds it (a firmware matter, told to Ali 2026-11-19). True when the
        chip answered OK."""
        code, r = self.api("/api/obd_chip/probe", "POST",
                           {"kind": "probe", "baud": 2000000, "seconds": 0, "cmd": f"ATSP{proto}"})
        if code != 202:
            return False
        pr = {}
        t0 = time.time()
        while time.time() - t0 < 30:
            time.sleep(0.5)
            code, pr = self.api("/api/obd_chip/probe")
            if code == 200 and isinstance(pr, dict) and pr.get("done") and not pr.get("running"):
                break
        said = " ".join(str(x.get("text") or "") for x in (pr or {}).get("lines", [])
                        if x.get("cmd") == f"ATSP{proto}")
        return "OK" in said.upper()

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
