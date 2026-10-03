#!/usr/bin/env python3
"""CAN auto-baud and bus-guard bench (TASK_j1939_wwh.md, phase 2).

The claim under test: the DUT never destroys the traffic of a bus it cannot
read, with its native CAN controller or through the OBD chip, and it finds
the bitrate of a live bus by listening.

A CAN node at the wrong bitrate answers every frame with an error frame; the
sender retransmits until it is bus-off (measured before the fix: 445 error
frames a second with the DUT at 500k on a 250k bus, listen-only included).
So the judge is the wire, not the DUT's word: the PCAN adapter keeps the bus
alive (29-bit id 18FF1021, --fps frames a second), the ECU simulator at the
same bitrate is the ACK source, and the adapter counts the error frames it
sees and the frames of its own that made it. Every leg stages settings on
the DUT, submits (the DUT restarts), and asserts the observed state in
/api/can and /api/autopid plus the error frames on the wire, across the
restart and in a steady window.

Legs (bus / DUT setting):
  native controller (can_manager), autopid off
   n1  500k live / 500 normal   running, bitrate proven by frames, receives
   n2  500k live / 250 normal   mismatch: listen-only, nothing destroyed; the
                                restart out of a running normal node is clean
   n3  500k live / 250 silent   mismatch, nothing destroyed
   n4  500k live / auto         detects 500, runs
   n5  the bus moves to 250k under the running auto node: demoted, detects
                                250 (METRIC: what the demotion cost)
   n6  250k live / 500 normal   mismatch, nothing destroyed
   n7  250k live / 500 silent   mismatch, nothing destroyed
   n8  250k live / 250 normal   running, bitrate proven by frames
   n9  250k silent / auto       stays listen-only without a bitrate; traffic
                                starts: detected (METRIC: detection time)
   n10 250k silent / 500 normal runs as configured (a gatewayed OBD port
                                is silent); 250k traffic starts: demoted
                                (METRIC: cost) and it stays listen-only when
                                the bus falls silent again
   n11 auto + silent, the adapter alone talks at every bitrate of the
                                setting in turn (125, 1000, 100, 83.3, 95.2,
                                33.3, 500, 250): each one is found and read
                                (the walk, the bit timing of every rate)
   n12 auto + silent, traffic at 800k (no candidate reads it): every
                                candidate is tried, round after round, the
                                node never claims a bitrate
  OBD chip (autopid's bus guard), can_manager off
   c0  500k live / protocol Automatic: polls answered, the vehicle store
                                holds protocol 6
   c1  250k live / protocol 6 (pinned): parked before the first request,
                                nothing written to the chip, chip jobs
                                refused with the guard's sentence
   c2  250k live / Automatic, the store says 6: the chip's search is used,
                                polls answered, the store learns 8
   c3  250k silent at boot / protocol 6, then traffic: parked at the first
                                look that reads frames (METRIC: cost)
   c4  silent at boot / protocol 6, then traffic at 800k (neither 500 nor
                                250 reads it): parked as unreadable although
                                the chip's own unanswered requests sound the
                                same; polling resumes when the traffic stops
  restore: the simulator, the vehicle store's protocol and every setting as
  found.

Run on the PC (IDF venv python: python-can + the PCAN on the DUT's bus, the
simulator's REST at 192.168.8.1, the DUT's HTTP through the tunnel):
  python can_autobaud_bench.py [dut host[:port]] [--sim http://192.168.8.1]
        [--pcan PCAN_USBBUS2] [--fps 200] [--window 8] [--only n1,c3,...]
Takes about 8 minutes (18 DUT restarts, 2 simulator restarts).

The simulator cannot leave the bus (its CAN controller survives a software
restart and keeps acknowledging at its last bitrate, "enabled": false or
not), so the legs at other bitrates run with the DUT listen-only: there the
adapter's frames go unacknowledged and it repeats them at line rate, which
a listener reads as traffic.
Expected final line: CAN AUTOBAUD PASS   (--only prints CAN AUTOBAUD PARTIAL)
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import can

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import bench_bus  # noqa: E402

DUT = "localhost:8081"
SIM = bench_bus.SIM_URL
PCAN = "PCAN_USBBUS2"
FPS = 200
WINDOW = 8.0
ONLY = None
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--sim":
        SIM = args[i + 1]; i += 2
    elif args[i] == "--pcan":
        PCAN = args[i + 1]; i += 2
    elif args[i] == "--fps":
        FPS = int(args[i + 1]); i += 2
    elif args[i] == "--window":
        WINDOW = float(args[i + 1]); i += 2
    elif args[i] == "--only":
        ONLY = [x.strip() for x in args[i + 1].split(",") if x.strip()]; i += 2
    else:
        DUT = args[i]; i += 1
BASE = "http://" + DUT

ALL_LEGS = ["c0", "n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9", "n10",
            "n11", "n12", "c1", "c2", "c3", "c4"]
LEG_BUS = {"c0": 500, "n1": 500, "n2": 500, "n3": 500, "n4": 500, "n5": 500,
           "n6": 250, "n7": 250, "n8": 250, "n9": 250, "n10": 250,
           "n11": 250, "n12": 250, "c1": 250, "c2": 250, "c3": 250,
           "c4": 250}
# n11: the adapter's bitrates in the order the node does NOT try them, the
# bus's own last (the simulator, at 250k, acknowledges there again)
WALK_RATES = [125, 1000, 100, 83, 95, 33, 500, 250]

# Limits. A node that already transmits (normal mode) and meets traffic it
# cannot read destroys frames until the link policy takes its TX pin away:
# CAN_AB_BAD_MIN (8) receive errors wake the RX task, the node is rebuilt
# listen-only. The cost on the wire is the frames destroyed until then.
DEMOTE_BOUND = 16     # measured 5 and 6 (2026-10-03)
# The chip's request that is on the wire at the moment a silent bus wakes up
# cannot be called back; every later one is preceded by a look at the bus.
CHIP_BOUND = 40       # one request's attempts; measured 0
# tags whose E lines are this feature's (a bench fails on its own tags only)
OWN_TAGS = ("can_manager", "can_core", "autopid", "obd_chip", "twai",
            "esp_twai")

fails = []
metrics = []


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
    """GET, modify, PUT, read back. True when something changed."""
    cur = settings(name)
    new = dict(cur)
    new.update(vals)
    if new == cur:
        return False
    code, r = api(f"/api/settings/{name}", "PUT", new)
    if code != 200:
        raise Bench(f"PUT {name}: HTTP {code} {r}")
    back = settings(name)
    bad = {k: back.get(k) for k in vals if back.get(k) != vals[k]}
    if bad:
        raise Bench(f"{name} did not stage {vals}: reads {bad}")
    return True


def boot_count():
    code, st = api("/api/status", timeout=4)
    return st.get("boot_count") if code == 200 and isinstance(st, dict) \
        else None


restarts = [0]


def apply(changes, fresh=False):
    """Stage `changes` ({component: {key: value}}) and submit: the DUT
    restarts. Returns (t_submit, t_back). When the settings already were
    what the leg needs: (now, now) and no restart, unless `fresh` asks for
    one (a leg that judges what happens from boot)."""
    changed = [n for n, v in changes.items() if stage(n, v)]
    if not changed and not fresh:
        now = time.time()
        return now, now
    b0 = boot_count()
    if b0 is None:
        raise Bench("no boot_count before a restart")
    t_submit = time.time()
    # the reply can lose to the restart
    api("/api/settings/submit" if changed else "/api/restart", "POST", {})
    time.sleep(3)
    while time.time() - t_submit < 150:
        b = boot_count()
        if b is not None and b != b0:
            restarts[0] += 1
            return t_submit, time.time()
        time.sleep(1)
    raise Bench("the DUT did not come back within 150 s of a restart")


def can_state():
    return get("/api/can")


def can_wait(pred, secs, step=0.25):
    """Poll /api/can until pred holds. (last answer, held, seconds)."""
    t0 = time.time()
    st = {}
    while True:
        code, r = api("/api/can", timeout=4)
        if code == 200 and isinstance(r, dict):
            st = r
            if pred(st):
                return st, True, time.time() - t0
        if time.time() - t0 >= secs:
            return st, False, time.time() - t0
        time.sleep(step)


def autopid():
    return get("/api/autopid")


def autopid_wait(pred, secs, step=0.5):
    t0 = time.time()
    ap = {}
    while True:
        code, r = api("/api/autopid", timeout=6)
        if code == 200 and isinstance(r, dict):
            ap = r
            if pred(ap):
                return ap, True, time.time() - t0
        if time.time() - t0 >= secs:
            return ap, False, time.time() - t0
        time.sleep(step)


def chip_tx():
    """Bytes written to the OBD chip since boot."""
    return get("/api/obd_chip")["uart"]["tx_bytes"]


def store_protocol():
    v = get("/api/autopid/vehicles")
    for e in v.get("vehicles", []):
        if e.get("current"):
            return e.get("protocol")
    return None


def link(st):
    return (f"state={st.get('state')} listen_only={st.get('listen_only')} "
            f"verified={st.get('verified')} baud={st.get('baud_kbps')} "
            f"detected={st.get('baud_detected')}")


def guard(ap):
    g = ap.get("bus_guard", {})
    s = ap.get("stats", {})
    return (f"bus={g.get('bus')}/{g.get('bus_kbps')} "
            f"verdict={g.get('verdict')} parked={g.get('parked')} "
            f"paused_bus={s.get('paused_bus')} polls_ok={s.get('polls_ok')} "
            f"failed={s.get('polls_failed')}")


def own_e_lines():
    """E lines of this feature's tags in the DUT's log ring."""
    code, t = api("/api/logs/ring", timeout=15)
    if code != 200 or not isinstance(t, str):
        return []
    out = []
    for ln in t.splitlines():
        ln = ln.replace("\x1b[0;31m", "").replace("\x1b[0m", "")
        if not ln.startswith("E ("):
            continue
        tag = ln.split(") ", 1)[1].split(":", 1)[0].strip() if ") " in ln \
            else ""
        if tag in OWN_TAGS:
            out.append(ln)
    return out


# ---- the wire: PCAN as traffic source and judge -------------------------------------

class Wire:
    """PCAN reports two things as error frames: a bus error it saw (the id
    is the kind: 1 bit, 2 form, 4 stuff, 8 other) and, with id 0, every
    later change of its error counters. One frame destroyed on the bus is
    one bus error and eight count-downs; only bus errors are counted."""
    ID = 0x18FF1021

    def __init__(self, channel):
        self.channel = channel
        self.bus = None
        self.kbit = 0
        self.fps = 0
        self.lock = threading.Lock()
        self.err = []       # arrival time of every bus error
        self.echo = []      # ... of every frame of ours that made it
        self.sent = []      # when each frame was handed to the adapter
        self.tx_fail = 0
        self._stop = None
        self._threads = []

    def open(self, kbit):
        self.close()
        self.bus = can.Bus(interface="pcan", channel=self.channel,
                           bitrate=kbit * 1000, auto_reset=True,
                           receive_own_messages=True)
        self.kbit = kbit
        self.fps = 0
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._send, daemon=True),
                         threading.Thread(target=self._recv, daemon=True)]
        for t in self._threads:
            t.start()

    def close(self):
        if self.bus is None:
            return
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        try:
            self.bus.shutdown()
        except can.CanError:
            pass
        self.bus = None
        self.fps = 0

    def traffic(self, fps):
        """Frames a second from now on; 0 = the adapter only listens."""
        self.fps = fps

    def _send(self):
        n = 0
        nxt = time.perf_counter()
        while not self._stop.is_set():
            fps = self.fps
            if fps <= 0:
                time.sleep(0.01)
                nxt = time.perf_counter()
                continue
            try:
                self.bus.send(can.Message(
                    arbitration_id=self.ID, is_extended_id=True,
                    data=n.to_bytes(4, "little") + b"\x00" * 4))
                n += 1
                with self.lock:
                    self.sent.append(time.time())
            except can.CanError:
                with self.lock:
                    self.tx_fail += 1
                time.sleep(0.002)
            nxt += 1.0 / fps
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)
            elif d < -0.05:
                nxt = time.perf_counter()   # far behind (a busy PC): no burst

    def _recv(self):
        while not self._stop.is_set():
            try:
                m = self.bus.recv(timeout=0.05)
            except can.CanError:
                continue
            if m is None:
                continue
            now = time.time()
            with self.lock:
                if m.is_error_frame:
                    if m.arbitration_id != 0:
                        self.err.append(now)
                elif m.arbitration_id == self.ID and m.is_extended_id:
                    self.echo.append(now)

    def errors(self, t0, t1=None):
        t1 = time.time() if t1 is None else t1
        with self.lock:
            return sum(1 for t in self.err if t0 <= t < t1)

    def error_span(self, t0, t1=None):
        """'first..last' offsets (s after t0) of the error frames in a
        window, for the detail of a failed check."""
        t1 = time.time() if t1 is None else t1
        with self.lock:
            ts = [t - t0 for t in self.err if t0 <= t < t1]
        return f"at +{ts[0]:.2f}..+{ts[-1]:.2f} s" if ts else ""

    def on_bus(self, t0, t1=None):
        t1 = time.time() if t1 is None else t1
        with self.lock:
            return sum(1 for t in self.echo if t0 <= t < t1)

    def given(self, t0, t1=None):
        """Frames handed to the adapter in a window."""
        t1 = time.time() if t1 is None else t1
        with self.lock:
            return sum(1 for t in self.sent if t0 <= t < t1)

    def flows(self, t0, t1=None):
        """(ok, text): the traffic of a window made it to the bus. Judged
        against what the adapter was given (a busy PC sends less than --fps)
        with a few frames of slack for the window's edges."""
        t1 = time.time() if t1 is None else t1
        k, n = self.on_bus(t0, t1), self.given(t0, t1)
        ok = n >= 0.5 * FPS * (t1 - t0) and k >= 0.97 * n - 5
        return ok, f"{k} of {n} frames on the bus in {t1 - t0:.0f} s"


def bus_healthy(wire, tag):
    """The precondition of a live-bus leg: the traffic flows, nothing on the
    bus objects (the simulator ACKs, the DUT is harmless)."""
    t0 = time.time()
    time.sleep(3)
    e = wire.errors(t0)
    ok, text = wire.flows(t0)
    return check(f"{tag}_bus_healthy", e == 0 and ok,
                 f"{wire.kbit}k: {text}, {e} error frames")


def steady(wire, tag, t_submit, extra=""):
    """A steady window on a live bus: (before, after) of /api/can, with the
    two wire checks every live-bus leg makes."""
    a = can_state()
    t0 = time.time()
    time.sleep(WINDOW)
    t1 = time.time()
    b = can_state()
    e_restart = wire.errors(t_submit, t0)
    e_steady = wire.errors(t0, t1)
    k = wire.on_bus(t0, t1)
    ok, text = wire.flows(t0, t1)
    check(f"{tag}_wire_restart", e_restart == 0,
          f"{e_restart} error frames from the submit to the steady window "
          f"{wire.error_span(t_submit, t0)}")
    check(f"{tag}_wire_steady", e_steady == 0 and ok,
          f"{e_steady} error frames, {text}{extra}")
    return a, b, k


# ---- native controller legs ----------------------------------------------------------

def running_at(kbit):
    return lambda s: (s.get("state") == "running" and s.get("verified")
                      and not s.get("listen_only")
                      and s.get("baud_detected") == kbit)


def mismatched(s):
    return (s.get("state") == "mismatch" and s.get("listen_only")
            and not s.get("verified"))


def native(tag, wire, baud, silent, want_run):
    print(f"--- {tag}: bus {wire.kbit}k live, can_manager baud={baud} "
          f"silent={silent}", flush=True)
    t_submit, _ = apply({"autopid": {"enabled": False},
                         "can_manager": {"enabled": True, "baud": str(baud),
                                         "silent": silent}})
    pred = running_at(wire.kbit) if want_run else mismatched
    st, ok, took = can_wait(pred, 15)
    check(f"{tag}_state", ok, f"{link(st)} after {took:.1f} s")
    a, b, k = steady(wire, tag, t_submit)
    drx = b.get("rx", 0) - a.get("rx", 0)
    dbad = b.get("rx_bad", 0) - a.get("rx_bad", 0)
    if want_run:
        check(f"{tag}_receives", 0.9 * k <= drx <= 1.1 * k + 20,
              f"rx +{drx} for {k} frames on the bus, rx_bad +{dbad}")
    else:
        check(f"{tag}_reads_nothing", drx == 0 and dbad > 0 and mismatched(b),
              f"rx +{drx}, rx_bad +{dbad}, {link(b)}")
    return b


def leg_n5(wire):
    print("--- n5: the bus moves to 250k under the running auto node",
          flush=True)
    a = can_state()
    if not check("n5_precondition", running_at(500)(a) and a.get("baud_auto"),
                 link(a)):
        return
    wire.traffic(0)
    time.sleep(0.5)
    wire.close()
    bench_bus.sim_set(bitrate=250, base=SIM)
    wire.open(250)
    time.sleep(1)
    quiet = can_state()
    check("n5_silence_keeps_the_node", running_at(500)(quiet), link(quiet))
    t_live = time.time()
    wire.traffic(FPS)
    st, ok, took = can_wait(running_at(250), 15, step=0.1)
    time.sleep(0.5)
    cost = wire.errors(t_live)
    check("n5_redetected", ok, f"{link(st)} after {took:.1f} s")
    metric("n5_demotion_cost", cost, "error frames")
    metric("n5_redetect_ms", int(took * 1000))
    check("n5_cost_bounded", cost <= DEMOTE_BOUND,
          f"{cost} error frames {wire.error_span(t_live)}, "
          f"limit {DEMOTE_BOUND}")
    check("n5_demoted_once",
          st.get("link_demotions", 0) - a.get("link_demotions", 0) == 1,
          f"link_demotions {a.get('link_demotions')} -> "
          f"{st.get('link_demotions')}")
    t0 = time.time()
    time.sleep(WINDOW)
    e = wire.errors(t0)
    ok, text = wire.flows(t0)
    check("n5_wire_steady", e == 0 and ok, f"{e} error frames, {text}")


def leg_n9(wire):
    print("--- n9: silent 250k bus, can_manager baud=auto; then traffic",
          flush=True)
    wire.traffic(0)
    time.sleep(0.5)
    apply({"autopid": {"enabled": False},
           "can_manager": {"enabled": True, "baud": "auto", "silent": False}},
          fresh=True)
    time.sleep(4)
    st = can_state()
    check("n9_silent_stays_listening",
          st.get("state") == "detecting" and st.get("listen_only")
          and not st.get("verified") and st.get("baud_detected") == 0,
          link(st))
    t_live = time.time()
    wire.traffic(FPS)
    st, ok, took = can_wait(running_at(250), 15, step=0.1)
    check("n9_detected", ok, f"{link(st)} after {took:.2f} s")
    metric("n9_detect_ms", int(took * 1000))
    time.sleep(WINDOW)
    e = wire.errors(t_live)
    check("n9_wire", e == 0, f"{e} error frames since the traffic started "
          f"{wire.error_span(t_live)}")


def leg_n10(wire):
    print("--- n10: silent 250k bus, can_manager baud=500 normal; then "
          "250k traffic", flush=True)
    wire.traffic(0)
    time.sleep(0.5)
    apply({"autopid": {"enabled": False},
           "can_manager": {"enabled": True, "baud": "500", "silent": False}},
          fresh=True)
    st, ok, took = can_wait(
        lambda s: s.get("state") == "running" and not s.get("listen_only"), 10)
    check("n10_silent_runs_as_configured", ok and not st.get("verified"),
          f"{link(st)} after {took:.1f} s")
    a = st
    t_live = time.time()
    wire.traffic(FPS)
    st, ok, took = can_wait(lambda s: s.get("listen_only"), 10, step=0.1)
    time.sleep(0.5)
    cost = wire.errors(t_live)
    check("n10_demoted", ok and mismatched(st),
          f"{link(st)} after {took:.2f} s")
    metric("n10_demotion_cost", cost, "error frames")
    check("n10_cost_bounded", cost <= DEMOTE_BOUND,
          f"{cost} error frames {wire.error_span(t_live)}, "
          f"limit {DEMOTE_BOUND}")
    t0 = time.time()
    time.sleep(WINDOW)
    e = wire.errors(t0)
    ok, text = wire.flows(t0)
    check("n10_wire_steady", e == 0 and ok, f"{e} error frames, {text}")
    # the bus falls silent again: a node that was bitten keeps listening
    wire.traffic(0)
    time.sleep(3)
    st = can_state()
    check("n10_stays_listen_only_after_silence", st.get("listen_only"),
          link(st))
    t1 = time.time()
    wire.traffic(FPS)
    time.sleep(3)
    e = wire.errors(t1)
    st = can_state()
    check("n10_second_wave_untouched",
          e == 0 and st.get("link_demotions", 0)
          - a.get("link_demotions", 0) == 1,
          f"{e} error frames, link_demotions "
          f"{a.get('link_demotions')} -> {st.get('link_demotions')}")


def leg_n11(wire):
    print("--- n11: can_manager baud=auto silent; the adapter alone at every "
          "bitrate of the setting", flush=True)
    wire.traffic(0)
    time.sleep(0.5)
    apply({"autopid": {"enabled": False},
           "can_manager": {"enabled": True, "baud": "auto", "silent": True}})
    slowest = 0

    for kbit in WALK_RATES:
        wire.open(kbit)
        a = can_state()
        wire.traffic(40)
        st, ok, took = can_wait(
            lambda s, k=kbit: s.get("verified") and s.get("listen_only")
            and s.get("baud_detected") == k, 15, step=0.2)
        slowest = max(slowest, took)
        time.sleep(2)
        b = can_state()
        wire.traffic(0)
        time.sleep(0.2)
        drx = b.get("rx", 0) - a.get("rx", 0)
        dbad = b.get("rx_bad", 0) - a.get("rx_bad", 0)
        check(f"n11_reads_{kbit}k", ok and drx >= 50 and b.get("listen_only"),
              f"{link(st)} after {took:.1f} s, rx +{drx}, rx_bad +{dbad}, "
              f"switches {a.get('link_switches')} -> "
              f"{b.get('link_switches')}")

    metric("n11_slowest_detect_ms", int(slowest * 1000))
    # the walk's 1000 kbit/s candidate on a slow bus hears a bit error every
    # few bit times: a receive-error storm that starved the tick and reset
    # the chip (interrupt watchdog) until 2026-10-03; the driver now masks the
    # controller's interrupts past 20 000 errors/s and the policy bounces the
    # deaf node. The storms must be counted (the throttle engaged) and the
    # DUT must not have rebooted (the closing no_unexpected_reset check)
    st = can_state()
    metric("n11_error_storms", st.get("rx_storms", 0))
    check("n11_error_storms_throttled", st.get("rx_storms", 0) >= 1,
          f"rx_storms {st.get('rx_storms')} (absent = older firmware)")
    wire.close()


def leg_n12(wire):
    print("--- n12: can_manager baud=auto silent; traffic at 800k, which no "
          "candidate reads", flush=True)
    apply({"autopid": {"enabled": False},
           "can_manager": {"enabled": True, "baud": "auto", "silent": True}})
    wire.open(800)
    a = can_state()
    wire.traffic(40)
    # the node may still run at the last bitrate it proved (n11 ends at 250k)
    st, gone, took = can_wait(lambda s: not s.get("verified"), 5, step=0.1)
    check("n12_lets_go_of_the_old_bitrate", gone,
          f"{link(st)} after {took:.1f} s")
    claimed = []
    states = set()
    end = time.time() + 10

    while time.time() < end:
        st = can_state()
        states.add(st.get("state"))

        if st.get("verified") or not st.get("listen_only"):
            claimed.append(link(st))

        time.sleep(0.25)

    b = can_state()
    wire.traffic(0)
    time.sleep(0.2)
    wire.close()
    dsw = b.get("link_switches", 0) - a.get("link_switches", 0)
    check("n12_never_claims_a_bitrate", not claimed and
          b.get("baud_detected") in (0, 250),
          claimed[0] if claimed else link(b))
    check("n12_tries_every_candidate", dsw >= 8,
          f"link_switches +{dsw} in 10 s, rx +"
          f"{b.get('rx', 0) - a.get('rx', 0)}, rx_bad +"
          f"{b.get('rx_bad', 0) - a.get('rx_bad', 0)}, rx_deaf +"
          f"{b.get('rx_deaf', 0) - a.get('rx_deaf', 0)}")
    check("n12_says_unreadable", "mismatch" in states,
          f"states seen: {sorted(x for x in states if x)}")
    wire.open(250)      # the bus as the next leg expects it


# ---- chip legs (autopid's bus guard) -----------------------------------------------------

CAN_OFF = {"enabled": False}


def polls(ap):
    s = ap.get("stats", {})
    return s.get("polls_ok", 0), s.get("polls_failed", 0)


def leg_c0(wire):
    print("--- c0: bus 500k live, autopid on, protocol Automatic", flush=True)
    t_submit, _ = apply({"can_manager": CAN_OFF,
                         "autopid": {"enabled": True, "std_protocol": "0"}})
    # a store left at a 250k protocol is re-learned here (the chip's search)
    ap, ok, took = autopid_wait(lambda a: polls(a)[0] >= 20, 90)
    check("c0_polls_answered", ok, f"{guard(ap)} after {took:.0f} s")
    end = time.time() + 60
    proto = store_protocol()
    while proto != "6" and time.time() < end:
        time.sleep(2)
        proto = store_protocol()
    check("c0_store_protocol_6", proto == "6", f"store protocol {proto}")
    a = autopid()
    t0 = time.time()
    time.sleep(WINDOW)
    b = autopid()
    g = b.get("bus_guard", {})
    check("c0_guard_allows",
          not g.get("parked") and not b["stats"].get("paused_bus")
          and g.get("bus") == "live" and g.get("bus_kbps") == 500, guard(b))
    check("c0_polling", polls(b)[0] > polls(a)[0]
          and polls(b)[1] == polls(a)[1],
          f"polls_ok +{polls(b)[0] - polls(a)[0]}, "
          f"failed +{polls(b)[1] - polls(a)[1]} in {WINDOW:.0f} s")
    e_restart, e_steady = wire.errors(t_submit, t0), wire.errors(t0)
    check("c0_wire", e_restart == 0 and e_steady == 0,
          f"{e_restart} error frames across the restart "
          f"{wire.error_span(t_submit, t0)}, {e_steady} in the window")


def leg_c1(wire):
    print("--- c1: bus 250k live, autopid on, protocol 6 pinned", flush=True)
    t_submit, _ = apply({"can_manager": CAN_OFF,
                         "autopid": {"enabled": True, "std_protocol": "6",
                                     "dtc_enabled": True,
                                     "dtc_allow_clear": True}},
                        fresh=True)
    ap, ok, took = autopid_wait(
        lambda a: a.get("bus_guard", {}).get("parked"), 20)
    g = ap.get("bus_guard", {})
    check("c1_parked", ok and g.get("verdict") == "park"
          and g.get("bus") == "live" and g.get("bus_kbps") == 250
          and ap["stats"].get("paused_bus"),
          f"{guard(ap)} after {took:.1f} s")
    check("c1_reason", "250" in g.get("reason", "")
          and "Automatic" in g.get("reason", ""), g.get("reason", ""))
    check("c1_never_polled", polls(ap) == (0, 0),
          f"polls_ok {polls(ap)[0]}, failed {polls(ap)[1]} since boot")
    tx0 = chip_tx()
    t0 = time.time()
    time.sleep(WINDOW)
    b = autopid()
    tx1 = chip_tx()
    check("c1_chip_untouched", tx1 == tx0 and polls(b) == (0, 0),
          f"chip tx_bytes +{tx1 - tx0}, polls {polls(b)}")
    # one-shot chip jobs ask the guard too
    jobs = [("detect", "/api/autopid/vehicles/detect", {}),
            ("std_scan", "/api/autopid/std_scan", {}),
            ("test_pid", "/api/autopid/test", {"cmd": "010C"}),
            ("dtc_scan", "/api/autopid/dtc/scan", {}),
            ("dtc_clear", "/api/autopid/dtc/clear", {"confirm": True})]
    for name, path, body in jobs:
        code, r = api(path, "POST", body)
        err = r.get("error", "") if isinstance(r, dict) else str(r)
        check(f"c1_job_refused_{name}", code == 409 and "250" in err,
              f"HTTP {code}: {err}")
    time.sleep(2)
    tx2 = chip_tx()
    check("c1_jobs_wrote_nothing", tx2 == tx1, f"chip tx_bytes +{tx2 - tx1}")
    e_restart, e_steady = wire.errors(t_submit, t0), wire.errors(t0)
    k = wire.on_bus(t0)
    check("c1_wire", e_restart == 0 and e_steady == 0,
          f"{e_restart} error frames across the restart "
          f"{wire.error_span(t_submit, t0)}, {e_steady} in the window "
          f"({k} frames on the bus)")


def leg_c2(wire):
    print("--- c2: bus 250k live, autopid on, protocol Automatic, the store "
          "says 6", flush=True)
    before = store_protocol()
    check("c2_precondition_store_6", before == "6", f"store protocol {before}")
    t_submit, _ = apply({"can_manager": CAN_OFF,
                         "autopid": {"enabled": True, "std_protocol": "0",
                                     "dtc_enabled": False,
                                     "dtc_allow_clear": False}})
    ap, ok, took = autopid_wait(lambda a: polls(a)[0] >= 20, 90)
    check("c2_polls_answered", ok, f"{guard(ap)} after {took:.0f} s")
    end = time.time() + 60
    proto = store_protocol()
    while proto != "8" and time.time() < end:
        time.sleep(2)
        proto = store_protocol()
    check("c2_store_learns_8", proto == "8", f"store protocol {proto}")
    a = autopid()
    t0 = time.time()
    time.sleep(WINDOW)
    b = autopid()
    g = b.get("bus_guard", {})
    check("c2_guard_allows",
          not g.get("parked") and not b["stats"].get("paused_bus")
          and g.get("bus") == "live" and g.get("bus_kbps") == 250, guard(b))
    check("c2_polling", polls(b)[0] > polls(a)[0]
          and polls(b)[1] == polls(a)[1],
          f"polls_ok +{polls(b)[0] - polls(a)[0]}, "
          f"failed +{polls(b)[1] - polls(a)[1]} in {WINDOW:.0f} s")
    metric("c2_failed_polls_until_contact", polls(b)[1])
    e_restart, e_steady = wire.errors(t_submit, t0), wire.errors(t0)
    check("c2_wire", e_restart == 0 and e_steady == 0,
          f"{e_restart} error frames across the restart "
          f"{wire.error_span(t_submit, t0)}, {e_steady} in the window")


def leg_c3(wire):
    print("--- c3: bus 250k silent at boot, autopid on, protocol 6 pinned; "
          "then traffic", flush=True)
    wire.traffic(0)
    time.sleep(0.5)
    apply({"can_manager": CAN_OFF,
           "autopid": {"enabled": True, "std_protocol": "6",
                       "dtc_enabled": False}}, fresh=True)
    # a silent bus is polled as configured (a gatewayed port answers only
    # when asked); here nobody answers at 500k
    ap, ok, took = autopid_wait(lambda a: polls(a)[1] >= 3, 30)
    check("c3_silent_bus_is_polled",
          ok and not ap.get("bus_guard", {}).get("parked"),
          f"{guard(ap)} after {took:.0f} s")
    t_live = time.time()
    wire.traffic(FPS)
    ap, ok, took = autopid_wait(
        lambda a: a.get("bus_guard", {}).get("parked"), 10, step=0.1)
    time.sleep(0.5)
    cost = wire.errors(t_live)
    g = ap.get("bus_guard", {})
    check("c3_parked", ok and g.get("bus") == "live"
          and g.get("bus_kbps") == 250, f"{guard(ap)} after {took:.2f} s")
    metric("c3_park_ms", int(took * 1000))
    metric("c3_cost", cost, "error frames")
    check("c3_cost_bounded", cost <= CHIP_BOUND,
          f"{cost} error frames {wire.error_span(t_live)}, "
          f"limit {CHIP_BOUND}")
    a = autopid()
    tx0 = chip_tx()
    t0 = time.time()
    time.sleep(WINDOW)
    b = autopid()
    tx1 = chip_tx()
    e = wire.errors(t0)
    flows, text = wire.flows(t0)
    check("c3_wire_steady", e == 0 and flows, f"{e} error frames, {text}")
    check("c3_chip_untouched", tx1 == tx0 and polls(b) == polls(a),
          f"chip tx_bytes +{tx1 - tx0}, polls {polls(a)} -> {polls(b)}")


def leg_c4(wire):
    print("--- c4: bus silent at boot, autopid on, protocol 6 pinned; then "
          "traffic at 800k", flush=True)
    wire.traffic(0)
    time.sleep(0.5)
    apply({"can_manager": CAN_OFF,
           "autopid": {"enabled": True, "std_protocol": "6",
                       "dtc_enabled": False}}, fresh=True)
    ap, ok, took = autopid_wait(lambda a: polls(a)[1] >= 3, 30)
    check("c4_silent_bus_is_polled",
          ok and not ap.get("bus_guard", {}).get("parked"),
          f"{guard(ap)} after {took:.0f} s")
    # the adapter alone at 800k: nobody acknowledges, its frame repeats at
    # line rate, and neither listener bitrate reads it
    wire.open(800)
    wire.traffic(40)
    ap, ok, took = autopid_wait(
        lambda a: a.get("bus_guard", {}).get("parked"), 15, step=0.2)
    g = ap.get("bus_guard", {})
    check("c4_parked_on_unreadable", ok and g.get("bus") == "unreadable"
          and "could not be read" in g.get("reason", ""),
          f"{guard(ap)} after {took:.1f} s: {g.get('reason', '')}")
    metric("c4_park_ms", int(took * 1000))
    a = autopid()
    tx0 = chip_tx()
    time.sleep(WINDOW)
    b = autopid()
    tx1 = chip_tx()
    check("c4_chip_untouched", tx1 == tx0 and polls(b) == polls(a)
          and b.get("bus_guard", {}).get("parked"),
          f"chip tx_bytes +{tx1 - tx0}, polls {polls(a)} -> {polls(b)}, "
          f"{guard(b)}")
    # the traffic stops: a silent bus is polled again
    wire.traffic(0)
    time.sleep(0.2)
    wire.close()
    ap, ok, took = autopid_wait(
        lambda x: not x.get("bus_guard", {}).get("parked")
        and polls(x)[1] > polls(b)[1], 20)
    check("c4_resumes_when_the_traffic_stops", ok,
          f"{guard(ap)} after {took:.1f} s")
    wire.open(250)


# ---- the run -----------------------------------------------------------------------------

def to_bus(wire, kbit, live):
    """Bring the whole bench bus to `kbit`: simulator (the ACK source),
    PCAN, traffic. The DUT's chip is idle whenever this is called (autopid
    off or parked, and every DUT restart resets the chip)."""
    st = bench_bus.sim_status(SIM)
    if st is None or st.get("bitrate") != kbit * 1000 or not st.get("running"):
        wire.close()
        bench_bus.sim_set(bitrate=kbit, enabled=True, base=SIM)
    if wire.bus is None or wire.kbit != kbit:
        wire.open(kbit)
    wire.traffic(FPS if live else 0)


def main():
    legs = ONLY if ONLY else ALL_LEGS
    unknown = [x for x in legs if x not in ALL_LEGS]
    if unknown:
        print(f"unknown leg(s) {unknown}; legs: {' '.join(ALL_LEGS)}")
        return 2

    # ---- as found ----------------------------------------------------------------
    st0 = get("/api/status")
    found = {"can_manager": settings("can_manager"),
             "autopid": settings("autopid")}
    found_sim = bench_bus.sim_get("ecu_sim", SIM)
    found_proto = store_protocol()
    print(f"DUT {DUT}: boot_count {st0.get('boot_count')}, unexpected_resets "
          f"{st0.get('unexpected_resets')}, version {st0.get('version')}")
    print(f"as found: can_manager {json.dumps(found['can_manager'])}")
    print(f"as found: autopid enabled={found['autopid'].get('enabled')} "
          f"std_protocol={found['autopid'].get('std_protocol')!r}, vehicle "
          f"store protocol {found_proto}")
    print(f"as found: simulator {json.dumps(found_sim)}")
    e_lines0 = set(own_e_lines())

    wire = Wire(PCAN)
    seen_e = set()
    done = 0
    try:
        for leg in legs:
            # n5 moves the bus itself; n9, n10 and c3 start on a silent bus
            if leg == "n5":
                to_bus(wire, 500, True)
            else:
                live = leg not in ("n9", "n10", "n11", "n12", "c3", "c4")
                moved = wire.bus is None or wire.kbit != LEG_BUS[leg]
                if moved:
                    # nothing of the DUT may sit on the bus at the old
                    # bitrate while it moves
                    apply({"autopid": {"enabled": False},
                           "can_manager": CAN_OFF})
                to_bus(wire, LEG_BUS[leg], live)
                if moved and live:
                    bus_healthy(wire, leg)
            if leg == "c0":
                leg_c0(wire)
            elif leg == "n1":
                native("n1", wire, 500, False, True)
            elif leg == "n2":
                native("n2", wire, 250, False, False)
            elif leg == "n3":
                native("n3", wire, 250, True, False)
            elif leg == "n4":
                st = native("n4", wire, "auto", False, True)
                check("n4_auto", st.get("baud_auto") is True, link(st))
            elif leg == "n5":
                if ONLY:
                    native("n5_setup", wire, "auto", False, True)
                leg_n5(wire)
            elif leg == "n6":
                native("n6", wire, 500, False, False)
            elif leg == "n7":
                native("n7", wire, 500, True, False)
            elif leg == "n8":
                native("n8", wire, 250, False, True)
            elif leg == "n9":
                leg_n9(wire)
            elif leg == "n10":
                leg_n10(wire)
            elif leg == "n11":
                leg_n11(wire)
                wire.open(250)
            elif leg == "n12":
                leg_n12(wire)
            elif leg == "c1":
                leg_c1(wire)
            elif leg == "c2":
                if ONLY and store_protocol() != "6":
                    print("c2 alone: the store must say 6 first (run c0)")
                leg_c2(wire)
            elif leg == "c3":
                leg_c3(wire)
            elif leg == "c4":
                leg_c4(wire)
            seen_e |= set(own_e_lines())
            done += 1
            print(f"PROGRESS {done}/{len(legs)}", flush=True)
    except Bench as e:
        check("bench_ran_to_the_end", False, str(e))
    finally:
        # ---- restore, as found ------------------------------------------------
        print("--- restore", flush=True)
        try:
            wire.traffic(0)
            time.sleep(0.3)
            wire.close()
            if bench_bus.sim_get("ecu_sim", SIM) != found_sim:
                # the bus moves back: nothing of the DUT may sit on it at
                # the old bitrate meanwhile
                apply({"autopid": {"enabled": False}, "can_manager": CAN_OFF})
                bench_bus.sim_set(bitrate=found_sim["bitrate"],
                                  id_format=found_sim["id_format"],
                                  enabled=found_sim["enabled"], base=SIM)
            if found_proto is not None and store_protocol() != found_proto:
                # the store learns from the car: let it meet the bus as
                # found (live, so the chip's search is quick)
                wire.open(int(found_sim["bitrate"]))
                wire.traffic(FPS)
                apply({"autopid": {"enabled": True, "std_protocol": "0"}})
                end = time.time() + 120
                while store_protocol() != found_proto and time.time() < end:
                    time.sleep(3)
                wire.traffic(0)
                time.sleep(0.3)
                wire.close()
            apply(found)
            now = {"can_manager": settings("can_manager"),
                   "autopid": settings("autopid")}
            check("restored_settings", now == found,
                  "" if now == found else json.dumps(now))
            check("restored_vehicle_store", store_protocol() == found_proto,
                  f"protocol {store_protocol()}, found {found_proto}")
            sim_now = bench_bus.sim_get("ecu_sim", SIM)
            check("restored_simulator", sim_now == found_sim,
                  "" if sim_now == found_sim else json.dumps(sim_now))
            st1 = get("/api/status")
            check("no_unexpected_reset",
                  st1.get("unexpected_resets") == st0.get("unexpected_resets")
                  and st1.get("boot_count") - st0.get("boot_count")
                  == restarts[0],
                  f"unexpected_resets {st0.get('unexpected_resets')} -> "
                  f"{st1.get('unexpected_resets')}, boot_count +"
                  f"{st1.get('boot_count') - st0.get('boot_count')} for "
                  f"{restarts[0]} submits")
            seen_e |= set(own_e_lines())
            new_e = sorted(seen_e - e_lines0)
            check("no_own_E_lines", not new_e, " | ".join(new_e)[:400])
        except Bench as e:
            check("restore", False, str(e))

    for name, value, unit in metrics:
        print(f"  {name} = {value}{(' ' + unit) if unit else ''}")
    word = "PARTIAL" if ONLY else ""
    if fails:
        print(f"CAN AUTOBAUD {word + ' ' if word else ''}FAIL: "
              + ", ".join(fails))
        return 1
    print("CAN AUTOBAUD PARTIAL PASS (legs: " + " ".join(legs) + ")" if ONLY
          else "CAN AUTOBAUD PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
