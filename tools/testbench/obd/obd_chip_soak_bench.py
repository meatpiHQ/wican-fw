#!/usr/bin/env python3
"""The OBD chip under a field configuration, accelerated  ->  OBD CHIP SOAK PASS / FAIL

A user's WiCAN PRO lost its OBD chip (issue #1023: the chip stopped answering
after a while on the Audi EV MEB profile). This bench puts the bench DUT on
that user's configuration (`internal/wican config_2026-10-28.json`: the same
car profile, sleep on at 13.1 V with the periodic wake, requests paused
under the sleep voltage, standard PIDs off), polls the profile at the
scheduler's floor instead of the user's 5 s, and then plays what a day in
the car does to the chip, over and over, until the chip fails or a stop
file appears:

  poll              two minutes of polling at 13.5 V: the counters move, the
                    SOC parameter has a fresh value, the simulator saw the
                    requests, no E line, no reset
  engine_off_sleep  12.5 V: the requests pause (pause_follow_sleep), the
                    device sleeps after the 1 min delay (supply current),
                    two minutes of naps with no spurious wake, 13.5 V: the
                    wake reboot (power_wake), the chip up, polling back
  periodic_checkin  12.5 V across one periodic wake (5 min): the check-in
                    reboot at low voltage, a chip bring-up there, the
                    re-entry, then the voltage wake; history reads
                    periodic_wake then power_wake, never internal_recovery
  power_cut         the supply off for 5 s: a cold boot with the chip awake
                    at power-on (the other boot state)
  crank_dip         9.0 V for 2 s (a starter motor): whatever the ESP does,
                    the chip must be up and polled afterwards
  sw_restart        POST /api/restart: the warm chip path (ATWS)
  app_session       an app over /ws/obd: ATZ, ATE0, ATSP6, 0100, 010C x5,
                    ATZ; AutoPID yields and resumes; the chip's reset and
                    memory counters step
  store_read        ATZ + ATDPN over /ws/obd: the chip's non-volatile
                    protocol store still reads as found (the profile flips
                    6 <-> 7 seven times per cycle; ATM0 must spare it)
  fast_hysteresis   12.5 V for 20 s and back, three times: pause and resume
                    without a sleep

After every scenario the chip's health is read: `ready` on /api/obd_chip,
the polling counters moving at 13.5 V, the log ring's new E lines, the
faults, the restart history. The run STOPS (verdict FAIL, the DUT left as
it is for a post-mortem, the supply kept on) when the chip is down: not
ready 90 s after a boot with a `bringup.wire` note or a `chip not
responding` / `bring-up failed` / `unresponsive` E line, no answered poll
in 60 s at 13.5 V while the chip claims ready, a `chip will not sleep`
recovery reboot, or the chip report answering 409 twice. Everything else
(a crash, an unexpected reset, a stray E line, a scenario assertion) is a
FINDING: logged, counted, the run goes on.

usage: obd_chip_soak_bench.py [--dut localhost:18081] [--sim http://192.168.8.1]
            [--psu auto|COMxx] [--console auto|COMxx|none]
            [--config "internal/wican config_2026-10-28.json"]
            [--out test-reports/logs/obd_chip_soak_<stamp>] [--stop-file <out>/STOP]
            [--rounds N] [--only a,b,c] [--period-ms 50] [--setup-only] [--no-restore]
            [--as-found <an earlier run's as_found.json>] [--seed N]
  run on the PC with the DUT through an ssh tunnel (`ssh -N -L 18081:<dut>:80
  rpi001`), the simulator at its NCM address (lib/sim_net.py), the PSU on its
  FTDI port, the console tailed on the CH344 B channel (opening it may reset
  the DUT once, before the baseline). Stop it with the stop file (touch it)
  or Ctrl-C: the DUT, the simulator and the supply are put back as found.
Needs: the branch firmware on the DUT, the chip up, the simulator on the bus
at 500 kbit/s, the /ws/obd bridge, the ssh tunnel.
"""
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import bench_bus    # noqa: E402  (the simulator; installs sim_net)
import bench_ports  # noqa: E402  (COM ports by role)
import wsmin        # noqa: E402  (the /ws/obd bridge)
from owon_psu import OwonPsu  # noqa: E402

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


STAMP = time.strftime("%Y-%m-%d_%H%M")
DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
SIM = arg("--sim", bench_bus.SIM_URL)
PSU_PORT = arg("--psu", "auto")
CONSOLE = arg("--console", "auto")
CONFIG = arg("--config", os.path.join(REPO, "internal", "wican config_2026-10-28.json"))
OUT = arg("--out", os.path.join(REPO, "test-reports", "logs", "obd_chip_soak_" + STAMP))
STOP_FILE = arg("--stop-file", os.path.join(OUT, "STOP"))
ROUNDS = int(arg("--rounds", "0"))
ONLY = [s for s in arg("--only", "").split(",") if s]
PERIOD_MS = int(arg("--period-ms", "50"))
SETUP_ONLY = "--setup-only" in ARGS
NO_RESTORE = "--no-restore" in ARGS
SEED = arg("--seed", "")
AS_FOUND = arg("--as-found", "")   # an earlier run's as_found.json: what the restore puts back
STORE_EACH = "--store-each" in ARGS  # read the chip's protocol store after every scenario

V_AWAKE = 13.5
V_SLEEPY = 12.5
V_CRANK = 9.0
SIM_PROFILE = "meb_soak.json"
MEB_CONVERT = os.path.join(HERE, "fixtures", "ecu_sim_meb_soak_profile.json")

CHIP_E = re.compile(r"chip not responding|bring-up failed|chip unresponsive|not ready for too long|"
                    r"will not sleep; recovery reboot|device degraded", re.I)


class ChipDown(Exception):
    """The stop condition: the OBD chip failed."""


class Finding(Exception):
    """Something the firmware should not have done; the run goes on."""


# ---- plumbing ---------------------------------------------------------------

def now():
    return time.strftime("%H:%M:%S")


class Log:
    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8")

    def __call__(self, *parts):
        line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(str(p) for p in parts))
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def http(base, path, method="GET", body=None, timeout=8, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(text)
            except ValueError:
                return r.status, text
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            return exc.code, None
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def get(path, timeout=8):
    st, d = http(DUT, path, timeout=timeout)
    return d if st == 200 else None


def mA(amps):
    return "%.0f mA" % (amps * 1000)


class ElmWs:
    """The chip through the /ws/obd bridge: one command, the bytes to the prompt."""

    def __init__(self):
        host, _, port = DUT_HP.partition(":")
        self.ws = wsmin.WS(host, int(port or 80), "/ws/obd", timeout=4)
        self.ws.settimeout(0.2)

    def cmd(self, text, wait_s=2.0):
        self.ws.pump()
        self.ws.send((text + "\r").encode(), wsmin.OP_BIN)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < wait_s:
            frames = self.ws.pump()
            for _, payload in frames:
                buf += payload
            if b">" in buf:
                break
            if not frames:
                time.sleep(0.02)
        return buf.decode(errors="replace").replace("\r", " ").replace("\n", " ").strip()

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def reply_of(text):
    return text.split(">")[0].strip()


def proto_digit(dpn):
    s = reply_of(dpn).replace("ATDPN", "").strip()
    s = re.sub(r"\s*\[.*\]$", "", s).strip()
    if len(s) == 2 and s[0] == "A":
        s = s[1:]
    return s


# ---- the rig ---------------------------------------------------------------

class Rig:
    def __init__(self, log):
        self.log = log
        self.psu = OwonPsu(bench_ports.resolve(PSU_PORT, "psu", "COM50"))
        try:
            self.psu.meas_current()  # a cold OWON's first answer is not one
        except (AssertionError, ValueError):
            pass
        self.con = None
        self.con_stop = None
        self.ring_seen = set()
        self.ring_e_new = []
        self.seq = -1
        self.unexpected = 0
        self.boot_count = 0
        self.store_found = ""
        self.store_last = ""
        self.findings = []
        self.state = {"rounds": 0, "scenarios": {}, "findings": [], "start": STAMP}

    # -- the console beside the run (forensics; may reset the DUT once at open)
    def console_start(self):
        if CONSOLE == "none":
            return
        port = bench_ports.resolve(CONSOLE, ("wican_console", "ch342_console"), "COM254")
        log = os.path.join(OUT, "console.log")
        self.con_stop = log + ".stop"
        if os.path.exists(self.con_stop):
            os.remove(self.con_stop)  # a stale stop file ends the tail at once (run 2, 02:37: 0 lines)
        self.con = subprocess.Popen(
            [sys.executable, "-u", os.path.join(HERE, "..", "lib", "console_tail.py"), port, log,
             "--secs", "2000000", "--stop-file", self.con_stop],
            stdout=open(log + ".keys", "a"), stderr=subprocess.STDOUT)
        self.log("console tailed on %s -> %s (an open may reset the DUT once)" % (port, log))

    def console_stop(self):
        if self.con is None:
            return
        try:
            open(self.con_stop, "w").close()
            self.con.wait(8)
        except Exception:  # noqa: BLE001
            self.con.kill()
        self.con = None

    # -- the DUT
    def wait_up(self, limit):
        t0 = time.time()
        while time.time() - t0 < limit:
            st, d = http(DUT, "/api/info", timeout=4)
            if st == 200:
                return True
            time.sleep(2)
        return False

    def chip(self):
        return get("/api/obd_chip") or {}

    def wait_ready(self, limit):
        t0 = time.time()
        oc = {}
        while time.time() - t0 < limit:
            oc = self.chip()
            if oc.get("ready"):
                return oc
            time.sleep(1)
        return oc

    def autopid(self):
        return get("/api/autopid") or {}

    def stats(self):
        return self.autopid().get("stats", {})

    def sleep_api(self):
        return get("/api/sleep") or {}

    def history(self):
        return get("/api/restart/history") or {}

    def mark_history(self):
        h = self.history()
        recs = h.get("records") or [{}]
        self.seq = recs[0].get("seq", -1)
        self.unexpected = h.get("unexpected_resets", 0)
        self.boot_count = h.get("boot_count", 0)

    def ring_lines(self):
        st, text = http(DUT, "/api/logs/ring", timeout=10)
        if st != 200 or not isinstance(text, str):
            return []
        return [re.sub(r"\x1b\[[0-9;]*m", "", ln).strip() for ln in text.splitlines()]

    def ring_mark(self):
        self.ring_seen = set(self.ring_lines())

    def ring_new_e(self, where=""):
        """New E lines since the mark (the mark moves). Every new line (W and
        I too) goes to ring.log under `where`: the ring is wiped by a power
        cut and the console capture is not always there (run 2)."""
        lines = self.ring_lines()
        new_all = [ln for ln in lines if ln not in self.ring_seen]
        self.ring_seen = set(lines)
        if new_all:
            with open(os.path.join(OUT, "ring.log"), "a", encoding="utf-8") as f:
                f.write("===== %s %s: %d new line(s)\n" % (time.strftime("%H:%M:%S"), where, len(new_all)))
                f.write("\n".join(new_all) + "\n")
        return [ln for ln in new_all if re.match(r"^E ", ln)]

    def put_settings(self, comp, patch):
        cur = get("/api/settings/" + comp)
        assert cur, comp + " settings unreachable"
        cur.pop("degraded", None)
        cur.pop("pending_reboot", None)
        cur.update(patch)
        st, r = http(DUT, "/api/settings/" + comp, "PUT", cur)
        assert st == 200 and isinstance(r, dict) and "error" not in r, "%s PUT: %s %s" % (comp, st, r)
        return bool(r.get("changed"))

    def submit(self):
        st, r = http(DUT, "/api/settings/submit", "POST", {}, timeout=15)
        assert st == 200 and isinstance(r, dict) and r.get("reboot"), "submit: %s %s" % (st, r)
        time.sleep(8)
        assert self.wait_up(150), "DUT lost after the submit restart"

    # -- the supply
    def current(self, n=4):
        return statistics.mean(self.psu.sample_current(n))

    def wait_sleep_entry(self, gate, limit):
        t0 = time.time()
        hit = self.psu.wait_current(lambda a: a < gate, timeout_s=limit)
        return hit, time.time() - t0

    def soak_asleep(self, sleep_a, secs, http_check=True):
        """No sustained current rise, no HTTP answer, for `secs`."""
        thresh = sleep_a + 0.025
        high = 0
        t0 = time.time()
        next_http = t0 + 40
        while time.time() - t0 < secs:
            a = self.psu.meas_current()
            high = high + 1 if a > thresh else 0
            if high >= 4:
                raise Finding("spurious wake during the sleep soak (%s sustained at t+%.0f s)"
                              % (mA(a), time.time() - t0))
            if http_check and time.time() >= next_http:
                next_http += 60
                if get("/api/sleep", timeout=3) is not None:
                    raise Finding("the DUT answered HTTP while it should sleep")
            time.sleep(2)

    def wake_burst(self, sleep_a, secs):
        """True when the device stays awake (3 samples) within secs."""
        high = 0
        t0 = time.time()
        while time.time() - t0 < secs:
            if self.psu.meas_current() > sleep_a + 0.025:
                high += 1
                if high >= 3:
                    return True
            else:
                high = 0
            time.sleep(2)
        return False


# ---- health: the stop condition and the findings ---------------------------

def health(rig, where, after_boot=False, expect_polling=True, allow_resets=0):
    """Read the chip and the device after a scenario. Raises ChipDown (stop)
    or Finding (logged, the run goes on)."""
    oc = rig.wait_ready(90 if after_boot else 10)
    e_lines = rig.ring_new_e(where)
    for ln in e_lines:
        rig.log("  E line:", ln[:200])
    chip_e = [ln for ln in e_lines if CHIP_E.search(ln) or "obd_chip" in ln]
    if not oc.get("ready"):
        raise ChipDown("%s: the chip is not ready (bringup.wire %s; E lines %s)"
                       % (where, oc.get("bringup", {}).get("wire"), chip_e[:3]))
    if chip_e:
        raise ChipDown("%s: the chip driver logged an error: %s" % (where, chip_e[:3]))
    h = rig.history()
    recs = h.get("records") or []
    for r in recs:
        if isinstance(r.get("seq"), int) and r["seq"] > rig.seq:
            if r.get("planned_reason") == "internal_recovery":
                raise ChipDown("%s: a recovery reboot (the chip would not sleep): %s" % (where, r))
            if r.get("crash"):
                rig.findings.append("%s: a crash: %s" % (where, r["crash"].get("summary")))
                rig.log("  FINDING crash:", r["crash"].get("summary"))
    grew = h.get("unexpected_resets", 0) - rig.unexpected
    if grew > allow_resets:
        rig.findings.append("%s: unexpected resets +%d" % (where, grew))
        rig.log("  FINDING unexpected resets +%d: %s" % (grew, [(r.get("seq"), r.get("reason")) for r in recs[:3]]))
    rig.mark_history()
    if e_lines:
        rig.findings.append("%s: %d E line(s): %s" % (where, len(e_lines), e_lines[0][:120]))
    faults = (get("/api/faults") or {}).get("faults")
    if faults:
        rig.findings.append("%s: faults %s" % (where, faults))
        rig.log("  FINDING faults:", faults)
    if expect_polling:
        polling_alive(rig, where)
    if STORE_EACH and expect_polling:
        # localise a store write to the scenario that did it (run 2, round 7:
        # the store read 7 after 6 rounds at 6, the writer unknown)
        got = read_store(rig)
        rig.log("  store after %s: %s (previous %s, as found %s)" % (where, got, rig.store_last, rig.store_found))
        if got != rig.store_last:
            rig.findings.append("%s: the chip's protocol store moved %s -> %s" % (where, rig.store_last, got))
            rig.log("  FINDING: the store moved %s -> %s during %s" % (rig.store_last, got, where))
            rig.store_last = got
        time.sleep(12)
    return oc


def read_store(rig):
    """ATZ + ATE0 + ATDPN over /ws/obd: the protocol the chip comes back on
    after a reset is its stored one. The ATZ goes to the chip as ATWS with an
    ATM0 hidden behind it (the driver's app-reset cover), AutoPID yields for
    10 s after the app's last byte."""
    e = ElmWs()
    try:
        # the first bytes land on a chip busy with a poll: it answers STOPPED
        # and DISCARDS the word, so an ATZ sent first is lost and the ATDPN
        # then reports the CURRENT protocol (run 2, 05:46 and run 3: three
        # "store moves" that were the row in flight). A CR first, then the
        # ATZ with its banner verified; no banner = no reading.
        e.cmd("", wait_s=2.0)
        banner = e.cmd("ATZ", wait_s=3.0)
        if "ELM" not in banner:
            banner = e.cmd("ATZ", wait_s=3.0)
        if "ELM" not in banner:
            rig.log("  store read: the ATZ got no banner (%r), no reading" % banner[:40])
            return "?"
        e.cmd("ATE0")
        return proto_digit(e.cmd("ATDPN"))
    finally:
        e.close()


def polling_alive(rig, where, secs=30, need=5):
    """At 13.5 V the poller must answer: polls_ok grows by `need` in `secs`."""
    s0 = rig.stats()
    t0 = time.time()
    while time.time() - t0 < secs:
        time.sleep(3)
        s1 = rig.stats()
        if s1.get("polls_ok", 0) - s0.get("polls_ok", 0) >= need:
            return s1
    s1 = rig.stats()
    oc = rig.chip()
    msg = ("%s: no answered poll in %d s (ok %s -> %s, failed %s -> %s, running %s, paused v/c/d/b %s/%s/%s/%s, chip ready %s, claim %s)"
           % (where, secs, s0.get("polls_ok"), s1.get("polls_ok"), s0.get("polls_failed"), s1.get("polls_failed"),
              s1.get("running"), s1.get("paused_voltage"), s1.get("paused_client"), s1.get("paused_diag"),
              s1.get("paused_bus"), oc.get("ready"), oc.get("claim")))
    if s1.get("paused_voltage") or s1.get("paused_client") or s1.get("paused_diag") or not s1.get("running"):
        raise Finding(msg)  # the poller is held off for a reason that is not the chip
    # the chip claims ready and answers nothing: ask it directly
    st, rep = http(DUT, "/api/obd_chip/report", timeout=12)
    if st == 409:
        time.sleep(2)
        st, rep = http(DUT, "/api/obd_chip/report", timeout=12)
    answered = st == 200 and isinstance(rep, dict) and any(
        (it.get("resp") or "").strip() for it in rep.get("items", []) if it.get("cmd") == "ATI")
    raise ChipDown(msg + "; report %s %s" % (st, "answered" if answered else "silent"))


def sim_counts():
    st, d = http(SIM, "/api/ecu/status", timeout=6)
    return (d.get("requests", 0), d.get("responses", 0)) if st == 200 and isinstance(d, dict) else (0, 0)


# ---- the scenarios ---------------------------------------------------------

def s_poll(rig):
    """Two minutes of polling at 13.5 V."""
    rig.psu.set_voltage(V_AWAKE)
    s0, (q0, r0) = rig.stats(), sim_counts()
    mo0 = rig.chip().get("eeprom_guard", {}).get("memory_off", 0)
    time.sleep(120)
    s1, (q1, r1) = rig.stats(), sim_counts()
    oc = rig.chip()
    mo1 = oc.get("eeprom_guard", {}).get("memory_off", 0)
    ok, failed = s1.get("polls_ok", 0) - s0.get("polls_ok", 0), s1.get("polls_failed", 0) - s0.get("polls_failed", 0)
    soc = [p for p in rig.autopid().get("params", []) if p.get("name") == "SOC"]
    soc_v = soc[0].get("value") if soc else None
    rig.log("  120 s: polls ok +%d failed +%d (%.1f ok/s), simulator requests +%d responses +%d, memory_off +%d, SOC %s, rx_overflows %s"
            % (ok, failed, ok / 120.0, q1 - q0, r1 - r0, mo1 - mo0, soc_v, oc.get("uart", {}).get("rx_overflows")))
    rig.state.setdefault("poll_rate", []).append(round(ok / 120.0, 2))
    if ok < 100:
        raise Finding("only %d answered polls in 120 s" % ok)
    if r1 - r0 < 100:
        raise Finding("the simulator answered only %d requests in 120 s" % (r1 - r0))
    if soc_v is None:
        raise Finding("SOC has no value after 120 s of polling")


def drop_and_sleep(rig, where, entry_limit=160):
    """12.5 V: the requests pause, the device sleeps; returns (awake_a, sleep_a, took)."""
    awake_a = rig.current(4)
    gate = awake_a - 0.030
    rig.log("  awake %s at %.1f V; dropping to %.1f V" % (mA(awake_a), V_AWAKE, V_SLEEPY))
    rig.psu.set_voltage(V_SLEEPY)
    time.sleep(12)
    st = rig.stats()
    sl = rig.sleep_api()
    rig.log("  after 12 s: paused_voltage %s, sleep state %s pending %s in %s s, chip ready %s"
            % (st.get("paused_voltage"), sl.get("state"), sl.get("pending"), sl.get("sleep_in_s"), rig.chip().get("ready")))
    if not st.get("paused_voltage"):
        rig.findings.append("%s: the requests did not pause under the sleep voltage" % where)
        rig.log("  FINDING: requests not paused under the sleep voltage")
    hit, took = rig.wait_sleep_entry(gate, entry_limit)
    if hit is None:
        raise Finding("%s: no sleep entry (never below %s in %.0f s)" % (where, mA(gate), took))
    sleep_a = rig.current(6)
    rig.log("  asleep after %.0f s: %s (delta %s)" % (took + 12, mA(sleep_a), mA(awake_a - sleep_a)))
    return awake_a, sleep_a, took


def wake_and_check(rig, where, sleep_a, want_reasons):
    """13.5 V: the wake reboot, the records, the chip, the polling."""
    rig.log("  waking at %.1f V" % V_AWAKE)
    rig.psu.set_voltage(V_AWAKE)
    awake = rig.psu.wait_current(lambda a: a >= sleep_a + 0.030, timeout_s=60)
    if awake is None:
        raise Finding("%s: no wake current on the voltage recovery" % where)
    if not rig.wait_up(150):
        raise Finding("%s: the DUT does not answer HTTP 150 s after the wake" % where)
    recs = (rig.history().get("records") or [])
    new = [r for r in recs if isinstance(r.get("seq"), int) and r["seq"] > rig.seq]
    reasons = [r.get("planned_reason") for r in sorted(new, key=lambda r: r["seq"])]
    rig.log("  back: records since the drop %s (seq %s -> %s)" % (reasons, rig.seq, recs[0].get("seq") if recs else None))
    if reasons != want_reasons:
        rig.findings.append("%s: wake records %s, expected %s" % (where, reasons, want_reasons))
        rig.log("  FINDING: wake records %s, expected %s" % (reasons, want_reasons))
    health(rig, where, after_boot=True)


def s_engine_off_sleep(rig):
    awake_a, sleep_a, _ = drop_and_sleep(rig, "engine_off_sleep")
    rig.log("  soaking 120 s asleep")
    rig.soak_asleep(sleep_a, 120)
    naps_note = "(naps are read after the wake)"
    rig.log("  soak clean %s" % naps_note)
    wake_and_check(rig, "engine_off_sleep", sleep_a, ["power_wake"])


def s_periodic_checkin(rig):
    """One periodic wake while the supply stays at 12.5 V."""
    interval_s = 5 * 60
    awake_a, sleep_a, _ = drop_and_sleep(rig, "periodic_checkin")
    t_entry = time.time()
    rig.log("  waiting for the periodic check-in (%d s interval)" % interval_s)
    if not rig.wake_burst(sleep_a, interval_s + 120):
        raise Finding("periodic_checkin: no periodic wake within %d s" % (interval_s + 120))
    t_burst = time.time() - t_entry
    rig.log("  check-in reboot %.0f s after the entry; reading the device at %.1f V" % (t_burst, V_SLEEPY))
    if not rig.wait_up(90):
        raise Finding("periodic_checkin: no HTTP after the check-in reboot")
    oc = rig.wait_ready(60)
    sl = rig.sleep_api()
    st = rig.stats()
    rig.log("  at the check-in: chip ready %s, sleep state %s pending %s in %s s, paused_voltage %s, chip %s"
            % (oc.get("ready"), sl.get("state"), sl.get("pending"), sl.get("sleep_in_s"), st.get("paused_voltage"),
               oc.get("fw", {}).get("chip")))
    if not oc.get("ready"):
        raise ChipDown("periodic_checkin: the chip did not come up at the check-in boot (bringup.wire %s)"
                       % oc.get("bringup", {}).get("wire"))
    hit, took = rig.wait_sleep_entry(sleep_a + 0.030, 180)
    if hit is None:
        raise Finding("periodic_checkin: no re-entry after the check-in in 180 s")
    sleep_b = rig.current(6)
    rig.log("  back asleep %.0f s after the check-in: %s; soaking 60 s" % (took, mA(sleep_b)))
    rig.soak_asleep(sleep_b, 60)
    wake_and_check(rig, "periodic_checkin", sleep_b, ["periodic_wake", "power_wake"])


def s_power_cut(rig):
    rig.psu.set_voltage(V_AWAKE)
    rig.ring_new_e("before power_cut")  # the cut wipes the PSRAM ring
    rig.log("  supply off 5 s")
    rig.psu.output(False)
    time.sleep(5)
    rig.psu.output(True)
    if not rig.wait_up(150):
        raise Finding("power_cut: no HTTP 150 s after the power-on")
    recs = rig.history().get("records") or []
    rig.log("  back: boot reason %s" % (recs[0].get("reason") if recs else None))
    health(rig, "power_cut", after_boot=True, allow_resets=1)


def s_crank_dip(rig):
    rig.psu.set_voltage(V_AWAKE)
    bc0 = rig.history().get("boot_count", 0)
    rig.ring_new_e("before crank_dip")  # a brown-out reset would wipe the ring
    rig.log("  dip to %.1f V for 2 s" % V_CRANK)
    rig.psu.set_voltage(V_CRANK)
    time.sleep(2)
    rig.psu.set_voltage(V_AWAKE)
    time.sleep(3)
    if not rig.wait_up(150):
        raise Finding("crank_dip: no HTTP 150 s after the dip")
    h = rig.history()
    rebooted = h.get("boot_count", 0) != bc0
    recs = h.get("records") or []
    rig.log("  %s (boot %s -> %s, reason %s)" % ("the ESP reset in the dip" if rebooted else "no reset in the dip",
                                                   bc0, h.get("boot_count"), recs[0].get("reason") if recs else None))
    rig.state.setdefault("crank_resets", 0)
    if rebooted:
        rig.state["crank_resets"] += 1
        time.sleep(10)  # the sleep manager's pause under 13.1 V clears with the voltage back
    else:
        time.sleep(12)  # the pause under the sleep voltage lifts (+0.3 V hysteresis, a few samples)
    health(rig, "crank_dip", after_boot=rebooted, allow_resets=1 if rebooted else 0)


def s_sw_restart(rig):
    rig.psu.set_voltage(V_AWAKE)
    st, r = http(DUT, "/api/restart", "POST", {}, timeout=10)
    rig.log("  POST /api/restart -> %s %s" % (st, r))
    time.sleep(6)
    if not rig.wait_up(150):
        raise Finding("sw_restart: no HTTP 150 s after the restart")
    recs = rig.history().get("records") or []
    if recs and recs[0].get("planned_reason") != "user_request":
        rig.findings.append("sw_restart: the boot record reads %s" % recs[0].get("planned_reason"))
    health(rig, "sw_restart", after_boot=True)


def s_app_session(rig):
    rig.psu.set_voltage(V_AWAKE)
    g0 = rig.chip().get("eeprom_guard", {})
    e = ElmWs()
    try:
        # an app's first bytes land while a poll is in flight: the chip aborts
        # it with STOPPED and DISCARDS the app's command (seen on run 1: the
        # ATZ answered 'STOPPED >', the session then ran on the headers the
        # profile's last row had left). Real apps send a CR first and wait for
        # the prompt; counted, not a finding.
        first = e.cmd("", wait_s=2.0)
        if "STOPPED" in first:
            rig.state["app_first_stopped"] = rig.state.get("app_first_stopped", 0) + 1
        banner = e.cmd("ATZ", wait_s=3.0)
        if "ELM" not in banner:
            banner = e.cmd("ATZ", wait_s=3.0)
        e.cmd("ATE0")
        e.cmd("ATH1")
        sp = e.cmd("ATSP6")
        r0100 = e.cmd("0100", wait_s=3.0)
        rpm = [reply_of(e.cmd("010C", wait_s=2.0)) for _ in range(5)]
        time.sleep(1)
        st_in = rig.stats()
        e.cmd("ATZ", wait_s=3.0)
    finally:
        e.close()
    g1 = rig.chip().get("eeprom_guard", {})
    rig.log("  app: banner %r, ATSP6 %r, 0100 %r, 010C %r; during the session running %s paused_client %s; app_resets %s -> %s, memory_off %s -> %s"
            % (reply_of(banner)[:40], reply_of(sp), reply_of(r0100)[:40], rpm[-1][:30], st_in.get("running"),
               st_in.get("paused_client"), g0.get("app_resets"), g1.get("app_resets"), g0.get("memory_off"), g1.get("memory_off")))
    if "ELM" not in banner:
        raise Finding("app_session: the app's ATZ got no banner (%r)" % banner[:60])
    if "41 00" not in r0100.replace("4100", "41 00"):
        raise Finding("app_session: 0100 not answered for the app (%r)" % r0100[:60])
    if not any("41 0C" in x.replace("410C", "41 0C") for x in rpm):
        raise Finding("app_session: 010C not answered for the app (%r)" % rpm)
    if g1.get("app_resets", 0) - g0.get("app_resets", 0) < 2:
        rig.findings.append("app_session: app_resets stepped %s -> %s for two ATZ" % (g0.get("app_resets"), g1.get("app_resets")))
    time.sleep(14)  # the poller yields to an app for 10 s after its last byte
    health(rig, "app_session")


def s_store_read(rig):
    rig.psu.set_voltage(V_AWAKE)
    got = read_store(rig)
    rig.log("  the chip's stored protocol after ATZ: %s (previous read %s, as found %s)" % (got, rig.store_last, rig.store_found))
    rig.state.setdefault("store_reads", []).append(got)
    time.sleep(14)
    moved = got != rig.store_last
    rig.store_last = got
    if moved:
        raise Finding("store_read: the chip's protocol store moved from %s to %s since the previous read (a protocol switch was written)"
                      % (rig.state["store_reads"][-2] if len(rig.state["store_reads"]) > 1 else rig.store_found, got))
    health(rig, "store_read")


def s_fast_hysteresis(rig):
    for i in range(3):
        rig.psu.set_voltage(V_SLEEPY)
        time.sleep(20)
        paused = rig.stats().get("paused_voltage")
        rig.psu.set_voltage(V_AWAKE)
        time.sleep(15)
        st = rig.stats()
        rig.log("  dip %d: paused under %.1f V %s; back: running %s paused_voltage %s" % (i + 1, V_SLEEPY, paused, st.get("running"), st.get("paused_voltage")))
        if not paused:
            rig.findings.append("fast_hysteresis: the requests did not pause in 20 s under the sleep voltage")
    health(rig, "fast_hysteresis")


SCENARIOS = [
    ("poll", s_poll), ("engine_off_sleep", s_engine_off_sleep), ("poll", s_poll),
    ("sw_restart", s_sw_restart), ("app_session", s_app_session), ("poll", s_poll),
    ("periodic_checkin", s_periodic_checkin), ("poll", s_poll), ("power_cut", s_power_cut),
    ("store_read", s_store_read), ("fast_hysteresis", s_fast_hysteresis), ("poll", s_poll),
    ("crank_dip", s_crank_dip), ("poll", s_poll),
]


# ---- setup and restore -----------------------------------------------------

def user_rows(cfg):
    """The user's profile rows as v6 `specific` rows (the web UI's import)."""
    car = cfg["auto_pid_car_data"]["cars"][0]
    rows = []
    for pp in car["pids"]:
        prm = []
        for x in pp.get("parameters", []):
            p = {"name": x["name"], "expression": str(x.get("expression") or ""), "unit": x.get("unit", "")}
            if x.get("class"):
                p["class"] = x["class"]
            for k in ("min", "max"):
                if str(x.get(k, "")).strip() != "":
                    p[k] = float(x[k])
            prm.append(p)
        rows.append({"name": prm[0]["name"] if len(prm) == 1 else pp["pid"], "cmd": pp["pid"],
                     "init": pp.get("pid_init", ""), "group": "default", "period_ms": 0,
                     "type": "specific", "parameters": prm})
    return car, rows


def setup(rig, cfg):
    """The user's configuration on the DUT, the profile on the simulator."""
    car, rows = user_rows(cfg)
    u = cfg["config"]
    rig.log("the user's file: car %r, %d rows, sleep %s at %s V after %s min, periodic %s/%s min, autopid pause %s, std %s"
            % (car["car_model"][:30], len(rows), u.get("sleep_status"), u.get("sleep_volt"), u.get("sleep_time"),
               u.get("periodic_wakeup"), u.get("wakeup_interval"), cfg["auto_pid"].get("disable_on_sleep_voltage"),
               cfg["auto_pid"].get("standard_pids")))
    # the simulator plays the car
    st, body = http(SIM, "/api/ecu/profiles/" + SIM_PROFILE, "POST", raw=open(MEB_CONVERT, "rb").read(), timeout=60)
    assert st == 200, "simulator profile upload: %s %s" % (st, body)
    st, body = http(SIM, "/api/ecu/profiles/" + SIM_PROFILE + "/reload", "POST", raw=b"", timeout=40)
    assert st == 200, "simulator profile reload: %s %s" % (st, body)
    time.sleep(1.5)
    st, s = http(SIM, "/api/ecu/status")
    rig.log("simulator: %s" % json.dumps(s)[:200])
    assert isinstance(s, dict) and "MEB" in (s.get("profile") or ""), "the simulator is not on the MEB profile"
    # the DUT: the car's rows at the floor period, the car's init on its store entry
    veh = get("/api/autopid/vehicles") or {}
    key = veh.get("current")
    if not key:
        # a fresh device: the detection job meets the simulator's car first
        # (the Quick Setup's path), the profile is attached to it below
        rig.wait_ready(90)
        st, r = http(DUT, "/api/autopid/vehicles/detect", "POST", {}, timeout=15)
        assert st == 202, "detect: %s %s" % (st, r)
        t0 = time.time()
        js = {}
        while time.time() - t0 < 180:
            time.sleep(3)
            js = get("/api/autopid/std_scan") or {}
            if js.get("status") in ("done", "failed"):
                break
        rig.log("detection: %s" % json.dumps(js)[:200])
        assert js.get("status") == "done", "the detection did not finish: %s" % js
        veh = get("/api/autopid/vehicles") or {}
        key = veh.get("current")
        assert key, "the vehicle store has no current car after the detection: %s" % json.dumps(veh)[:200]
        rig.log("the store's current car after the detection: %s" % key)
    st, r = http(DUT, "/api/autopid/vehicles/" + key, "PUT",
                 {"profile": car["car_model"][:63], "specific_init": car.get("init", "")})
    assert st == 200, "vehicle PUT: %s %s" % (st, r)
    table = {"groups": [{"name": "default", "enabled": True, "period_ms": PERIOD_MS}], "pids": rows, "filters": []}
    st, r = http(DUT, "/api/autopid/config", "PUT", table, timeout=30)
    assert st == 200 and isinstance(r, dict) and r.get("ok"), "config PUT: %s %s" % (st, r)
    rig.log("config: %d specific rows in group default at %d ms (the floor %s ms), car %s with init %r"
            % (len(rows), PERIOD_MS, rig.stats().get("period_floor_ms"), key, car.get("init", "")))
    changed = rig.put_settings("autopid", {
        "enabled": True, "std_enabled": False, "custom_enabled": True, "specific_enabled": True,
        "pause_follow_sleep": cfg["auto_pid"].get("disable_on_sleep_voltage") == "automate_threshold",
        "pause_mode": "requests_only", "pause_below_mv": 0})
    changed |= rig.put_settings("sleep_manager", {
        "enabled": u.get("sleep_status") == "enable", "sleep_mv": int(round(float(u.get("sleep_volt", "13.1")) * 1000)),
        "wake_mv": 13200, "sleep_delay_min": 1, "periodic_wakeup": u.get("periodic_wakeup") == "enable",
        "wakeup_interval_min": 5})
    if changed:
        rig.log("settings staged; the planned restart")
        rig.submit()
    rig.psu.set_voltage(V_AWAKE)
    rig.psu.output(True)
    oc = rig.wait_ready(90)
    assert oc.get("ready"), "the chip is not ready after the setup: %s" % json.dumps(oc)[:300]
    rig.log("chip: %s at %s baud, fw %s, eeprom_guard %s" % (oc.get("fw", {}).get("chip"), oc.get("baud"), oc.get("fw", {}).get("current"), oc.get("eeprom_guard")))
    sl = get("/api/settings/sleep_manager") or {}
    ap = get("/api/settings/autopid") or {}
    rig.log("settings now: sleep %s %s/%s mV delay %s min periodic %s/%s min; autopid enabled %s specific %s std %s pause_follow_sleep %s"
            % (sl.get("enabled"), sl.get("sleep_mv"), sl.get("wake_mv"), sl.get("sleep_delay_min"), sl.get("periodic_wakeup"),
               sl.get("wakeup_interval_min"), ap.get("enabled"), ap.get("specific_enabled"), ap.get("std_enabled"), ap.get("pause_follow_sleep")))
    # the store as found: the protocol the chip comes back on after a reset
    rig.store_found = read_store(rig)
    rig.store_last = rig.store_found
    rig.log("the chip's protocol store as found: %s" % rig.store_found)
    time.sleep(14)
    s = polling_alive(rig, "setup", secs=40, need=10)
    rig.log("polling: ok %s failed %s running %s" % (s.get("polls_ok"), s.get("polls_failed"), s.get("running")))


def snapshot(rig):
    if AS_FOUND:
        # a run after `--setup-only`: the DUT is already in the test state, the
        # restore must put back what that earlier run found
        found = json.load(open(AS_FOUND, encoding="utf-8"))
        json.dump(found, open(os.path.join(OUT, "as_found.json"), "w"), indent=1)
        rig.log("as found taken from %s" % AS_FOUND)
        return found
    found = {
        "autopid": get("/api/settings/autopid"), "sleep_manager": get("/api/settings/sleep_manager"),
        "config": get("/api/autopid/config"), "vehicles": get("/api/autopid/vehicles"),
        "sim": bench_bus.sim_get(base=SIM), "sim_status": (http(SIM, "/api/ecu/status")[1]),
        "psu_v": rig.psu.voltage_setpoint(), "history": rig.history(), "info": get("/api/info"),
    }
    json.dump(found, open(os.path.join(OUT, "as_found.json"), "w"), indent=1)
    return found


def restore(rig, found):
    rig.log("restoring the DUT, the simulator and the supply as found")
    rig.psu.set_voltage(V_AWAKE)
    rig.psu.output(True)
    rig.wait_up(150)
    try:
        cur = (found.get("vehicles") or {}).get("current")
        ent = [v for v in (found.get("vehicles") or {}).get("vehicles", []) if v.get("key") == cur]
        if cur and ent:
            http(DUT, "/api/autopid/vehicles/" + cur, "PUT",
                 {"profile": ent[0].get("profile", ""), "specific_init": ent[0].get("specific_init", "")})
        elif not cur:
            # a fresh device had no car: the one the setup's detection made
            # goes (its profile and init with it), the store empty as found
            now_cur = (get("/api/autopid/vehicles") or {}).get("current")
            if now_cur:
                st, _ = http(DUT, "/api/autopid/vehicles/" + now_cur, "DELETE")
                rig.log("  the setup's car %s forgotten: %s" % (now_cur, st))
        if found.get("config"):
            st, r = http(DUT, "/api/autopid/config", "PUT", found["config"], timeout=30)
            rig.log("  config as found: %s" % st)
        changed = False
        for comp in ("autopid", "sleep_manager"):
            if found.get(comp):
                vals = {k: v for k, v in found[comp].items() if k not in ("degraded", "pending_reboot")}
                changed |= rig.put_settings(comp, vals)
        if changed:
            rig.submit()
            rig.log("  settings as found (a planned restart)")
    except Exception as exc:  # noqa: BLE001
        rig.log("  (DUT restore incomplete: %s)" % exc)
    try:
        active = (found.get("sim") or {}).get("active_profile") or "default.json"
        http(SIM, "/api/ecu/profiles/" + active + "/reload", "POST", raw=b"", timeout=40)
        time.sleep(1.5)
        st, _ = http(SIM, "/api/ecu/profiles/" + SIM_PROFILE, "DELETE")
        if st == 409:
            # the warm reload plays the found profile but the simulator still
            # counts the bench profile as the active one until it reboots
            # (run 4's stop, 22:15): the saved setting re-submitted = a reboot
            st2, cur = http(SIM, "/api/settings/ecu_sim")
            if st2 == 200 and isinstance(cur, dict):
                cur.pop("degraded", None)
                cur.pop("pending_reboot", None)
                cur["active_profile"] = active
                http(SIM, "/api/settings/ecu_sim", "PUT", cur)
                http(SIM, "/api/settings/submit", "POST", {}, timeout=20)
                time.sleep(25)
                for _ in range(20):
                    st3, _ = http(SIM, "/api/ecu/status", timeout=5)
                    if st3 == 200:
                        break
                    time.sleep(3)
                st, _ = http(SIM, "/api/ecu/profiles/" + SIM_PROFILE, "DELETE")
        st4, s = http(SIM, "/api/ecu/status", timeout=6)
        rig.log("  simulator back on %s, playing %r (bench profile deleted: %s)"
                % (active, (s or {}).get("profile") if isinstance(s, dict) else s, st))
    except Exception as exc:  # noqa: BLE001
        rig.log("  (simulator restore incomplete: %s)" % exc)
    try:
        if found.get("psu_v"):
            rig.psu.set_voltage(found["psu_v"])
    except Exception:  # noqa: BLE001
        pass


def post_mortem(rig, why):
    """The chip failed: collect everything, touch nothing that would heal it."""
    rig.log("OBD CHIP FAILURE:", why)
    oc = rig.chip()
    dump = {"why": why, "obd_chip": oc, "autopid": rig.autopid(), "sleep": rig.sleep_api(),
            "history": rig.history(), "faults": get("/api/faults"), "status": get("/api/status"),
            "psu": {"v": rig.psu.meas_voltage(), "mA": rig.current(4) * 1000}}
    st, rep = http(DUT, "/api/obd_chip/report", timeout=15)
    dump["report"] = {"status": st, "body": rep}
    try:
        st, r = http(DUT, "/api/obd_chip/probe", "POST", {"kind": "scan"}, timeout=10)
        pr = None
        for _ in range(120):
            time.sleep(1)
            st, pr = http(DUT, "/api/obd_chip/probe")
            if st == 200 and isinstance(pr, dict) and pr.get("done") and not pr.get("running"):
                break
        dump["probe_scan"] = pr
    except Exception as exc:  # noqa: BLE001
        dump["probe_scan"] = str(exc)
    with open(os.path.join(OUT, "ring_at_failure.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(rig.ring_lines()))
    json.dump(dump, open(os.path.join(OUT, "chip_failure.json"), "w"), indent=1, default=str)
    rig.log("post-mortem written: chip_failure.json, ring_at_failure.txt, console.log in", OUT)
    rig.log("the DUT is left as it is (supply on at %.1f V); do not power cycle it before looking" % rig.psu.meas_voltage())


# ---- main ------------------------------------------------------------------

def main():
    os.makedirs(OUT, exist_ok=True)
    log = Log(os.path.join(OUT, "soak.log"))
    log("OBD chip soak: DUT %s, simulator %s, out %s, stop file %s" % (DUT, SIM, OUT, STOP_FILE))
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    rig = Rig(log)
    if SEED:
        random.seed(int(SEED))
    verdict = None
    found = None
    try:
        rig.console_start()
        time.sleep(3)
        assert rig.wait_up(150), "the DUT does not answer on " + DUT
        info = get("/api/info") or {}
        log("DUT %s fw %s (%s) on %s" % (info.get("device_id"), info.get("fw_version"), info.get("fw_build"), info.get("partition")))
        found = snapshot(rig)
        setup(rig, cfg)
        rig.ring_mark()
        rig.mark_history()
        health(rig, "setup")
        json.dump(rig.state, open(os.path.join(OUT, "state.json"), "w"), indent=1)
        if SETUP_ONLY:
            log("setup only: done")
            verdict = "OBD CHIP SOAK SETUP OK"
            return 0
        rounds = 0
        while True:
            rounds += 1
            order = [s for s in SCENARIOS if not ONLY or s[0] in ONLY]
            if SEED:
                random.shuffle(order)
            log("===== round %d: %s" % (rounds, " ".join(k for k, _ in order)))
            for key, fn in order:
                if os.path.exists(STOP_FILE):
                    log("stop file found: finishing after %d rounds" % (rounds - 1))
                    verdict = "OBD CHIP SOAK PASS (stopped by request after %d round(s), %d finding(s))" % (rounds - 1, len(rig.findings))
                    return 0
                n = rig.state["scenarios"].setdefault(key, {"pass": 0, "finding": 0})
                log("--- %s (run %d)" % (key, n["pass"] + n["finding"] + 1))
                t0 = time.time()
                try:
                    fn(rig)
                    n["pass"] += 1
                    log("PASS: %s #%d (%.0f s)" % (key, n["pass"], time.time() - t0))
                except Finding as exc:
                    n["finding"] += 1
                    rig.findings.append("%s: %s" % (key, exc))
                    log("FINDING: %s #%d: %s (%.0f s)" % (key, n["pass"] + n["finding"], exc, time.time() - t0))
                    rig.psu.set_voltage(V_AWAKE)
                    rig.psu.output(True)
                    if not rig.wait_up(150):
                        log("  the DUT is away after the finding; a power cycle")
                        rig.psu.output(False)
                        time.sleep(4)
                        rig.psu.output(True)
                        rig.wait_up(150)
                    try:
                        health(rig, key + "/after-finding", after_boot=True, allow_resets=1)
                    except Finding as exc2:
                        log("  still: %s" % exc2)
                        rig.findings.append("%s/after: %s" % (key, exc2))
                except AssertionError as exc:
                    n["finding"] += 1
                    rig.findings.append("%s: assertion: %s" % (key, exc))
                    log("FINDING: %s #%d: assertion %s" % (key, n["pass"] + n["finding"], exc))
                    rig.psu.set_voltage(V_AWAKE)
                    rig.psu.output(True)
                    rig.wait_up(150)
                rig.state["rounds"] = rounds
                rig.state["findings"] = rig.findings
                json.dump(rig.state, open(os.path.join(OUT, "state.json"), "w"), indent=1)
            summary = ", ".join("%s %d/%d" % (k, v["pass"], v["pass"] + v["finding"]) for k, v in rig.state["scenarios"].items())
            log("round %d done: %s; findings %d; poll rate %s ok/s; store reads %s" % (
                rounds, summary, len(rig.findings), rig.state.get("poll_rate", [])[-3:], rig.state.get("store_reads", [])[-3:]))
            if ROUNDS and rounds >= ROUNDS:
                verdict = "OBD CHIP SOAK PASS (%d round(s), %d finding(s))" % (rounds, len(rig.findings))
                return 0
    except ChipDown as exc:
        post_mortem(rig, str(exc))
        verdict = "OBD CHIP SOAK FAIL: the OBD chip failed: %s" % exc
        return 2
    except KeyboardInterrupt:
        verdict = "OBD CHIP SOAK PASS (interrupted after %d round(s), %d finding(s))" % (rig.state["rounds"], len(rig.findings))
        return 0
    finally:
        rig.console_stop()
        if verdict and verdict.startswith("OBD CHIP SOAK FAIL"):
            log("no restore: the DUT keeps its state for the post-mortem")
        elif found is not None and not NO_RESTORE:
            restore(rig, found)
        for f in rig.findings:
            log("finding:", f)
        if verdict:
            log(verdict)
        rig.psu.close()


if __name__ == "__main__":
    sys.exit(main())
