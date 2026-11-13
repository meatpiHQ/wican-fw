#!/usr/bin/env python3
"""CAN filters bench  ->  CAN FILTERS PASS / FAIL   (2026-11-14)

The web UI got a CAN filters list on Automate > Parameters > Vehicle
Specific and Custom PIDs (Ali: "allow users to add filters in vehicle
specific and in Custom pids"). Two things of it are the firmware's:

  - a filter follows the switch of its tab: one tagged `"profile": true`
    (the vehicle's set) rests while `specific_enabled` is off, every other
    one while `custom_enabled` is off. Before, a filter ran whatever the
    switches said;
  - Test for a filter: `POST /api/autopid/test {"filter": {...}}` opens one
    monitor window for the frame, as a poll of the filter does, and answers
    with the frame, its data and the values.

The frames come from the PC's PCAN on the unit's bus, so every value is
known: 0x284 (the vehicle's filter, speed 30 then 60 km/h), 0x3B3 (a custom
filter, 5 then 7), 0x4D0 (a custom filter on a multiplexed message, page 1
reads 40, page 2 reads 80), each every 50 ms; 0x6F1 is sent by nobody.

Legs:
  f0  preflight: the unit, the tables and the autopid settings saved, the
      PCAN channel on the unit's bus found by listening (the adapter's two
      plugs may be swapped), Automate on with its three switches on
  f1  a table of filters ONLY, across a restart: the four values arrive
      (nothing but windows on the chip from the first second), the ID
      nobody sends stays empty. The frames start with 02, 05, 01 and 02:
      until 2026-11-14 the window monitored with the chip's CAN auto
      formatting on, which reads such a first byte as a length, and the
      speed frame arrived as `02 1E 02` (no B4 for its filter)
  f2  Test for a filter: the frame whole, its data and the values; a
      multiplexed message read page by page; a filter without parameters
      shows the frame; an ID nobody sends says so after the listen time;
      the four refusals (no ID, an ID beyond 29 bits, a listen time out of
      range, an expression that does not parse)
  f3  a standard PID beside the filters: it is read between the windows,
      and the filters' values follow the bus (30 -> 60, 5 -> 7)
  f3p the unit's own page, in Chrome (can_filters_page.mjs, skipped with
      --no-page): the CAN filters list on Custom PIDs with its live
      values, Add CAN filter, the fields, Test (the frame and 60 km/h),
      Apply Configuration (the unit's table and its reading), and a filter
      added on Vehicle Specific that arrives tagged `profile`
  f4  the switches: `specific_enabled` off = the vehicle's filters rest
      (the page's one too), the custom ones and the PID go on;
      `custom_enabled` off = the opposite; Test still reads a resting
      filter's frame
  f5  restore: the tables and the settings as found, no new E line in the
      unit's log ring (it spans the run's restarts), no new fault, no
      unexpected restart

The run puts its own tables in before anything is listened for: a window
sees only IDs of the width of the protocol the chip is on (the autopid
README's width rule), and a row of the unit's own tables that sets a 29-bit
protocol would leave the 11-bit IDs of this bench unseen.

usage: can_filters_bench.py [--dut localhost:18081] [--pcan PCAN_USBBUS1]
                            [--no-page] [--out DIR]
  Runs on the PC (the PCAN, the simulator through sim_net); the unit
  through an ssh tunnel (`ssh -N -L 18081:<its address>:80 rpi001`) or by
  address.
Needs: the main firmware on the unit on the bench AP, ECU sim v2 as the car
on its OBD socket (it acknowledges the PCAN's frames and answers the
standard PID), the PCAN on the unit's bus at 500 kbit/s, the supply above
the sleep voltage; for the page leg node, Chrome and the Playwright
devDependency of tools/webui_preview. About 5 min, four planned restarts
(five when Automate was off at the start).
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import can

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import bench_bus  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BITRATE = 500000
SPEED_30 = bytes.fromhex("021E021F0B7C4E20")    # [B4:B5]/98 = 30
SPEED_60 = bytes.fromhex("021E021F16F84E20")    # [B4:B5]/98 = 60
EXT_ID = 0x18DAF110                             # the frame with a 29-bit ID
FUNC_29 = 0x18DB33F1                            # a request on the 29-bit protocol
PLANNED = [0]


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


def get_json(base, path, timeout=30):
    st, body = http(base, "GET", path, timeout=timeout)
    try:
        return st, json.loads(body)
    except ValueError:
        return st, None


def post_json(base, path, body, timeout=40):
    st, text = http(base, "POST", path, body, timeout=timeout)
    try:
        return st, json.loads(text)
    except ValueError:
        return st, {"_text": text}


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print("  [%s] %s%s" % ("ok" if ok else "FAIL", name,
                               (": " + str(detail)) if detail != "" else ""),
              flush=True)
        return bool(ok)

    def failed(self):
        return [n for n, ok, _ in self.rows if not ok]


class Generator(threading.Thread):
    """The frames of the run, each every 50 ms; the multiplexed message
    alternates its two pages; one frame has a 29-bit ID. What the unit
    sends is kept by ID (`heard`)."""

    def __init__(self, channel):
        super().__init__(daemon=True)
        self.bus = can.Bus(interface="pcan", channel=channel, bitrate=BITRATE)
        self.frames = {0x284: SPEED_30, 0x3B3: bytes([5, 0, 0, 0])}
        self.pages = [bytes([0x01, 40, 0, 0]), bytes([0x02, 80, 0, 0])]
        self.ext = bytes([0x11, 0x22, 0x33, 0x44])      # B1 = 34
        self.heard = set()
        self.sent = 0
        self.errors = 0
        self.halt = threading.Event()

    def _send(self, fid, data, extended=False):
        try:
            self.bus.send(can.Message(arbitration_id=fid, data=data,
                                      is_extended_id=extended), timeout=0.05)
            self.sent += 1
        except can.CanError:
            self.errors += 1

    def run(self):
        k = 0
        while not self.halt.is_set():
            for fid, data in list(self.frames.items()):
                self._send(fid, data)
            self._send(0x4D0, self.pages[k % 2])
            self._send(EXT_ID, self.ext, extended=True)
            k += 1
            t_end = time.time() + 0.05
            while time.time() < t_end:
                m = self.bus.recv(0.01)
                if m is not None and not m.is_error_frame:
                    self.heard.add(m.arbitration_id)

    def stop(self):
        self.halt.set()
        self.join(2)
        self.bus.shutdown()


def channel_on_dut_bus(channel, base):
    """Is this PCAN channel on the unit's bus? Listening only, while the
    unit asks the car one standard request."""
    try:
        bus = can.Bus(interface="pcan", channel=channel, bitrate=BITRATE,
                      state=can.BusState.PASSIVE)
    except Exception as e:  # noqa: BLE001  (the driver's own error types)
        return False, "cannot open: %s" % e
    seen = set()
    try:
        threading.Thread(target=lambda: http(base, "POST", "/api/autopid/test",
                                             {"cmd": "0100", "type": "std"},
                                             timeout=25), daemon=True).start()
        t_end = time.time() + 8
        while time.time() < t_end:
            m = bus.recv(0.2)
            if m is not None and not m.is_error_frame:
                seen.add(m.arbitration_id)
            if 0x7DF in seen and any(0x7E8 <= i <= 0x7EF for i in seen):
                break
    finally:
        bus.shutdown()
    ok = 0x7DF in seen and any(0x7E8 <= i <= 0x7EF for i in seen)
    return ok, "ids " + " ".join("%X" % i for i in sorted(seen)[:12])


def table(with_pid, row29=False):
    pid = {"name": "EngineRPM", "type": "std", "cmd": "010C", "group": "default",
           "parameters": [{"name": "EngineRPM", "expression": "[B3:B4]/4",
                           "unit": "rpm"}]}
    # a row that sets the 29-bit protocol itself, as the rows of some vehicle
    # profiles do: nobody answers it here, and the chip stays on protocol 7
    row = {"name": "Row29", "type": "custom", "cmd": "0100", "init": "ATSP7;",
           "group": "default",
           "parameters": [{"name": "ROW29", "expression": "B3"}]}
    return {
        "groups": [{"name": "default", "enabled": True, "period_ms": 1000}],
        "pids": [row] if row29 else [pid] if with_pid else [],
        "filters": [
            # the vehicle's set: the tag and the page's own key `name`
            {"name": "Road speed", "frame_id": 0x284, "monitor_ms": 300,
             "period_ms": 1000, "group": "default", "profile": True,
             "parameters": [{"name": "FLT_SPEED", "expression": "[B4:B5]/98",
                             "unit": "km/h"}]},
            {"frame_id": 0x3B3, "monitor_ms": 300, "period_ms": 1000,
             "group": "default",
             "parameters": [{"name": "FLT_DOOR", "expression": "B0"}]},
            {"frame_id": 0x4D0, "monitor_ms": 400, "period_ms": 1000,
             "group": "default",
             "parameters": [
                 {"name": "FLT_MUX_A", "expression": "B1", "mux_expr": "B0&15",
                  "mux_val": 1},
                 {"name": "FLT_MUX_B", "expression": "B1", "mux_expr": "B0&15",
                  "mux_val": 2}]},
            {"frame_id": 0x6F1, "monitor_ms": 200, "period_ms": 5000,
             "group": "default",
             "parameters": [{"name": "FLT_NOBODY", "expression": "B0"}]},
            # a 29-bit ID on a car whose own protocol is the 11-bit one
            {"frame_id": EXT_ID, "monitor_ms": 300, "period_ms": 1000,
             "group": "default",
             "parameters": [{"name": "FLT_EXT", "expression": "B1"}]},
        ]}


def values(base):
    st, ap = get_json(base, "/api/autopid", timeout=8)
    out = {}
    for p in (ap or {}).get("params", []):
        out[p.get("name")] = p.get("value")
    return out, (ap or {}).get("stats", {})


def wait_values(base, want, secs):
    """Poll until every name in `want` reads its value (None = must stay
    empty for the whole time). Returns (ok, the last values, seconds)."""
    t0 = time.time()
    vals = {}
    while time.time() - t0 < secs:
        vals, _ = values(base)
        hit = all(vals.get(k) is not None and abs(vals[k] - v) < 0.01
                  for k, v in want.items() if v is not None)
        if hit and not any(v is None for v in want.values()):
            return True, vals, time.time() - t0
        time.sleep(1.0)
    ok = all((vals.get(k) is None) if v is None
             else (vals.get(k) is not None and abs(vals[k] - v) < 0.01)
             for k, v in want.items())
    return ok, vals, time.time() - t0


def e_lines(base):
    """The E lines of the unit's log ring (plain text, it spans restarts)."""
    st, ring = http(base, "GET", "/api/logs/ring", timeout=30)
    return set(ln.strip()[-150:] for ln in ring.splitlines()
               if ln.lstrip("\x1b[0;31m").startswith("E ("))


def restart(base, action):
    """A planned restart: `action` (the restart route, or a settings
    submit), then the wait for the next boot with the OBD chip ready."""
    st, h = get_json(base, "/api/restart/history")
    b0 = (h or {}).get("boot_count")
    if not action():
        return False
    PLANNED[0] += 1
    time.sleep(6)
    t0 = time.time()
    while time.time() - t0 < 150:
        st, h = get_json(base, "/api/restart/history", timeout=4)
        st2, oc = get_json(base, "/api/obd_chip", timeout=4)
        if isinstance(h, dict) and h.get("boot_count") not in (None, b0) and \
                isinstance(oc, dict) and oc.get("ready"):
            print("  back after %.0f s" % (time.time() - t0 + 6))
            return True
        time.sleep(1)
    print("  the unit did not come back within 150 s")
    return False


def set_switches(base, saved, **want):
    """The autopid switches (a settings PUT, a submit, the restart)."""
    st, cur = get_json(base, "/api/settings/autopid")
    new = {k: v for k, v in (cur or saved).items()
           if k not in ("degraded", "pending_reboot")}
    if all(new.get(k) == v for k, v in want.items()):
        return True
    new.update(want)
    st, body = http(base, "PUT", "/api/settings/autopid", new)
    if st != 200:
        print("  settings PUT -> %s %s" % (st, body.strip()[:120]))
        return False

    def submit():
        st2, body2 = http(base, "POST", "/api/settings/submit", {})
        return st2 == 200 and '"reboot":true' in body2.replace(" ", "")
    return restart(base, submit)


def flt_test(base, out, tag, flt):
    t0 = time.time()
    st, r = post_json(base, "/api/autopid/test", {"filter": flt})
    r["_wall_s"] = round(time.time() - t0, 2)
    with open(os.path.join(out, "test_%s.json" % tag), "w") as f:
        json.dump(r, f, indent=1)
    return st, r


def main():
    args = sys.argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d  # noqa: E731
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    dut = opt("--dut", "localhost:18081")
    base = "http://" + dut.rstrip("/")
    pcan = opt("--pcan", bench_bus.PCAN_DUT_BUS)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = opt("--out", os.path.join(REPO, "test-reports", "logs",
                                    "can_filters_" + ts))
    os.makedirs(out, exist_ok=True)
    print("CAN FILTERS bench: dut=%s pcan=%s out=%s" % (dut, pcan, out), flush=True)
    c = Checks()
    saved_cfg = saved_settings = None
    gen = None
    e0 = set()
    h0 = faults0 = None

    try:
        print("f0 preflight")
        st, info = get_json(base, "/api/info")
        if not c.add("the unit answers", st == 200 and info,
                     "%s %s %s" % (st, (info or {}).get("device_id"),
                                   (info or {}).get("fw_build"))):
            raise SystemExit(1)
        st, saved_cfg = get_json(base, "/api/autopid/config")
        c.add("tables saved", st == 200 and saved_cfg is not None)
        st, saved_settings = get_json(base, "/api/settings/autopid")
        c.add("settings saved", st == 200 and saved_settings is not None)
        for name, obj in (("saved_config.json", saved_cfg),
                          ("saved_settings.json", saved_settings)):
            with open(os.path.join(out, name), "w") as f:
                json.dump(obj, f)
        st, h0 = get_json(base, "/api/restart/history")
        st, faults0 = get_json(base, "/api/faults")
        e0 = e_lines(base)      # what the ring held before the run
        hs =bench_bus._hs(bench_bus.sim_status() or {})
        c.add("the simulator's HS CAN runs at 500 kbit/s and answers",
              hs.get("running") and hs.get("bitrate") == BITRATE and hs.get("answers"),
              json.dumps(hs))
        if c.failed():
            raise SystemExit(1)
        c.add("Automate on with its three switches on",
              set_switches(base, saved_settings, enabled=True,
                           std_enabled=True, custom_enabled=True,
                           specific_enabled=True))
        # the adapter's two plugs may be swapped: find the unit's bus by ear
        other = "PCAN_USBBUS2" if pcan.endswith("1") else "PCAN_USBBUS1"
        ok, heard = channel_on_dut_bus(pcan, base)
        if not ok:
            print("  %s does not hear the unit ask the car (%s): trying %s"
                  % (pcan, heard, other))
            time.sleep(2)
            ok2, heard2 = channel_on_dut_bus(other, base)
            if ok2:
                pcan, ok, heard = other, True, heard2 + " (the plugs are swapped)"
        if not c.add("a PCAN channel on the unit's bus", ok, "%s: %s" % (pcan, heard)):
            raise SystemExit(1)
        gen = Generator(pcan)
        gen.start()
        time.sleep(1.0)
        c.add("the generator's frames are acknowledged", gen.sent > 20 and gen.errors == 0,
              "sent %d, errors %d" % (gen.sent, gen.errors))

        print("f1 a table of filters only, across a restart")
        st, body = http(base, "PUT", "/api/autopid/config", table(False))
        c.add("the table of five filters is accepted", st == 200, "%s %s" % (st, body.strip()[:100]))
        st, back = get_json(base, "/api/autopid/config")
        c.add("it is kept as sent (the tag and the page's name on a filter too)",
              back == table(False), json.dumps((back or {}).get("filters", [{}])[0])[:160])
        c.add("the unit restarted and is back",
              restart(base, lambda: http(base, "POST", "/api/restart", {})[0] == 200))
        ok, vals, secs = wait_values(base, {"FLT_SPEED": 30, "FLT_DOOR": 5,
                                            "FLT_MUX_A": 40, "FLT_MUX_B": 80}, 45)
        c.add("the four values of the 11-bit IDs arrive with nothing but filters in the "
              "table (the frames start with 02, 05, 01 and 02: none is cut short)",
              ok, "%s after %.0f s" % (json.dumps({k: v for k, v in vals.items() if k.startswith("FLT_")}), secs))
        ok, vals, secs = wait_values(base, {"FLT_EXT": 34}, 20)
        c.add("the 29-bit ID is read too, on a car whose own protocol is the 11-bit one "
              "(the window opens on the protocol of the ID's width, as v4.50 did)",
              ok, "FLT_EXT %s after %.0f s" % (vals.get("FLT_EXT"), secs))
        time.sleep(6)   # the ID nobody sends had its windows too
        vals, stats = values(base)
        c.add("the ID nobody sends stays empty, the poller counts five filters",
              vals.get("FLT_NOBODY") is None and stats.get("filters") == 5 and stats.get("running") is True,
              "FLT_NOBODY %s, filters %s, polls ok %s failed %s" % (
                  vals.get("FLT_NOBODY"), stats.get("filters"), stats.get("polls_ok"), stats.get("polls_failed")))

        print("f2 Test for a filter")
        st, r = flt_test(base, out, "speed", {"frame_id": 0x284, "monitor_ms": 500,
                                              "parameters": [{"expression": "[B4:B5]/98"},
                                                             {"expression": "B0"}]})
        tr = r.get("transcript", "")
        c.add("the frame is read: ok, its data, the two values",
              st == 200 and r.get("ok") is True and
              r.get("payload") == "02 1E 02 1F 0B 7C 4E 20" and r.get("values") == [30, 2],
              "%s ok=%s payload=%r values=%s" % (st, r.get("ok"), r.get("payload"), r.get("values")))
        c.add("the exchange: headers on, the ID's receive filter, formatting off, "
              "the monitor, the whole frame, formatting on and receive-all again",
              "> ATH1" in tr and "> ATCRA284" in tr and "> ATCAF0" in tr and "> ATMA" in tr and
              "< 284 02 1E 02 1F 0B 7C 4E 20" in tr and
              tr.rstrip().endswith("> ATCAF1\n< OK\n> ATCRA\n< OK"),
              tr.replace("\n", " | ")[:300])
        c.add("the frame came well inside the listen time",
              0 <= r.get("elapsed_ms", -1) < 400, "%s ms" % r.get("elapsed_ms"))
        st, r = flt_test(base, out, "mux", {"frame_id": 0x4D0, "monitor_ms": 600, "parameters": [
            {"expression": "B1", "mux_expr": "B0&15", "mux_val": 1},
            {"expression": "B1", "mux_expr": "B0&15", "mux_val": 2},
            {"expression": "B1", "mux_expr": "B0&15", "mux_val": 9}]})
        tr = r.get("transcript", "")
        c.add("a multiplexed message: each parameter from its own page, none "
              "for a page that never comes",
              st == 200 and r.get("ok") is True and r.get("values") == [40, 80, None] and
              "< 4D0 01 28 00 00" in tr and "< 4D0 02 50 00 00" in tr,
              "%s values=%s | %s" % (st, r.get("values"), tr.replace("\n", " | ")[-160:]))
        st, r = flt_test(base, out, "ext", {"frame_id": EXT_ID, "monitor_ms": 500,
                                            "parameters": [{"expression": "B1"}]})
        tr = r.get("transcript", "")
        c.add("a 29-bit ID: the chip to the 29-bit protocol for the window and back, "
              "the frame with eight digits, the value",
              st == 200 and r.get("ok") is True and r.get("values") == [34] and
              "> ATTP7" in tr and "> ATCRA18DAF110" in tr and "< 18DAF110 11 22 33 44" in tr and
              tr.rstrip().endswith("> ATTP6\n< OK") and tr.index("> ATTP7") < tr.index("> ATCRA18DAF110"),
              "%s values=%s | %s" % (st, r.get("values"), tr.replace("\n", " | ")[:330]))
        st, r = flt_test(base, out, "bare", {"frame_id": 0x3B3, "monitor_ms": 300})
        c.add("a filter without parameters shows the frame",
              st == 200 and r.get("ok") is True and r.get("payload") == "05 00 00 00" and
              r.get("values") == [], "%s payload=%r values=%s" % (st, r.get("payload"), r.get("values")))
        st, r = flt_test(base, out, "nobody", {"frame_id": 0x6F1, "monitor_ms": 400,
                                               "parameters": [{"expression": "B0"}]})
        tr = r.get("transcript", "")
        c.add("an ID nobody sends: not ok, the reason, no value, after the listen time",
              st == 200 and r.get("ok") is False and "payload" not in r and
              r.get("values") == [None] and
              r.get("error") == "no frame with this CAN ID in the listen time" and
              "! no frame with CAN ID 6F1 in 400 ms" in tr and r["_wall_s"] >= 0.4,
              "%s ok=%s error=%r wall=%s s | %s" % (st, r.get("ok"), r.get("error"), r["_wall_s"],
                                                    tr.replace("\n", " | ")[-120:]))
        bad = [("no ID", {"monitor_ms": 300}),
               ("an ID beyond 29 bits", {"frame_id": 0x20000000}),
               ("a listen time of 10 ms", {"frame_id": 0x284, "monitor_ms": 10}),
               ("an expression that does not parse",
                {"frame_id": 0x284, "parameters": [{"expression": "B0+*"}]})]
        got = []
        for name, flt in bad:
            st, r = post_json(base, "/api/autopid/test", {"filter": flt})
            got.append((name, st, r.get("error")))
        c.add("the four refusals are 400 with a reason", all(st == 400 and err for _, st, err in got), got)

        print("f3 a standard PID beside the filters")
        st, body = http(base, "PUT", "/api/autopid/config", table(True))
        c.add("a standard PID beside the filters is accepted (live)", st == 200, "%s %s" % (st, body.strip()[:100]))
        gen.frames[0x284] = SPEED_60
        gen.frames[0x3B3] = bytes([7, 0, 0, 0])
        ok, vals, secs = wait_values(base, {"FLT_SPEED": 60, "FLT_DOOR": 7,
                                            "FLT_MUX_A": 40, "FLT_MUX_B": 80}, 30)
        c.add("the values follow the bus (30 -> 60, 5 -> 7)", ok,
              "%s after %.0f s" % (json.dumps({k: v for k, v in vals.items() if k.startswith("FLT_")}), secs))
        c.add("the standard PID is read between the windows", vals.get("EngineRPM") is not None,
              "EngineRPM %s" % vals.get("EngineRPM"))

        print("f3b behind a row that leaves the chip on the 29-bit protocol")
        gen.heard.clear()
        st, body = http(base, "PUT", "/api/autopid/config", table(False, row29=True))
        c.add("a custom row whose init sets ATSP7, and no other PID, is accepted (live)",
              st == 200, "%s %s" % (st, body.strip()[:100]))
        time.sleep(8)       # the row went out: the chip sits on protocol 7 from here on
        c.add("that row was asked on the 29-bit protocol", FUNC_29 in gen.heard,
              "heard " + " ".join("%X" % i for i in sorted(gen.heard) if i > 0x7FF and i != EXT_ID))
        gen.frames[0x284] = SPEED_30
        gen.frames[0x3B3] = bytes([5, 0, 0, 0])
        ok, vals, secs = wait_values(base, {"FLT_SPEED": 30, "FLT_DOOR": 5, "FLT_MUX_A": 40,
                                            "FLT_MUX_B": 80, "FLT_EXT": 34}, 30)
        c.add("the 11-bit filters go on reading behind it (60 -> 30, 7 -> 5), the 29-bit one too",
              ok, "%s after %.0f s" % (json.dumps({k: v for k, v in vals.items() if k.startswith("FLT_")}), secs))
        st, body = http(base, "PUT", "/api/autopid/config", table(True))
        gen.frames[0x284] = SPEED_60
        gen.frames[0x3B3] = bytes([7, 0, 0, 0])
        ok, vals, secs = wait_values(base, {"FLT_SPEED": 60, "FLT_DOOR": 7, "FLT_EXT": 34}, 30)
        c.add("back on the table with the standard PID: the filters follow the bus again and "
              "the PID is read", st == 200 and ok and vals.get("EngineRPM") is not None,
              "%s EngineRPM %s after %.0f s" % (
                  json.dumps({k: v for k, v in vals.items() if k.startswith("FLT_")}),
                  vals.get("EngineRPM"), secs))

        page_ok = False
        if "--no-page" in args:
            print("f3p the unit's own page: skipped (--no-page)")
        else:
            print("f3p the unit's own page, in Chrome")
            node = shutil.which("node")
            if c.add("node is on the PATH (the page leg is can_filters_page.mjs)", bool(node)):
                pr = subprocess.run([node, os.path.join(HERE, "can_filters_page.mjs"),
                                     "--base", base, "--out", out],
                                    capture_output=True, text=True, timeout=300)
                for ln in pr.stdout.splitlines():
                    if ln.startswith(("PASS ", "FAIL ")):
                        name, _, detail = ln[5:].partition("  [")
                        c.add(name, ln.startswith("PASS "), detail[:-1][:300])
                page_ok = c.add("the page leg's verdict", "PAGE LEG PASS" in pr.stdout,
                                (pr.stderr or "").strip()[-200:])
        # the two filters the page added: a custom one on 0x284, one of the
        # vehicle's on 0x3B3
        added_off_specific = {"NewFilter": 60, "PageDoor": None} if page_ok else {}
        added_off_custom = {"NewFilter": None, "PageDoor": 7} if page_ok else {}

        print("f4 the switches")
        c.add("specific_enabled off (a planned restart)",
              set_switches(base, saved_settings, specific_enabled=False))
        ok, vals, secs = wait_values(base, {"FLT_DOOR": 7, "FLT_MUX_A": 40, "FLT_MUX_B": 80,
                                            "FLT_EXT": 34, "FLT_SPEED": None,
                                            **added_off_specific}, 25)
        vals2, stats = values(base)
        c.add("the vehicle's filters rest, the custom filters go on", ok,
              "%s after %.0f s" % (json.dumps({k: v for k, v in vals.items() if not k.startswith("Engine")}), secs))
        c.add("and the standard PID with them", vals2.get("EngineRPM") is not None,
              "EngineRPM %s" % vals2.get("EngineRPM"))
        st, r = flt_test(base, out, "resting", {"frame_id": 0x284, "monitor_ms": 500,
                                                "parameters": [{"expression": "[B4:B5]/98"}]})
        c.add("Test still reads the resting filter's frame", st == 200 and r.get("values") == [60],
              "%s values=%s" % (st, r.get("values")))
        c.add("specific_enabled on, custom_enabled off (a planned restart)",
              set_switches(base, saved_settings, specific_enabled=True, custom_enabled=False))
        ok, vals, secs = wait_values(base, {"FLT_SPEED": 60, "FLT_DOOR": None, "FLT_MUX_A": None,
                                            "FLT_MUX_B": None, "FLT_EXT": None,
                                            **added_off_custom}, 25)
        c.add("the custom filters rest, the vehicle's filters go on", ok,
              "%s after %.0f s" % (json.dumps({k: v for k, v in vals.items() if not k.startswith("Engine")}), secs))
    finally:
        print("f5 restore")
        if gen is not None:
            gen.stop()
            c.add("the generator sent its frames to the end", gen.errors == 0,
                  "sent %d, errors %d" % (gen.sent, gen.errors))
        if saved_cfg is not None:
            st, body = http(base, "PUT", "/api/autopid/config", saved_cfg)
            c.add("tables restored", st == 200, "%s %s" % (st, body.strip()[:80]))
            st, back = get_json(base, "/api/autopid/config")
            c.add("and read back as found", back == saved_cfg)
        if saved_settings is not None:
            restore = {k: v for k, v in saved_settings.items()
                       if k not in ("degraded", "pending_reboot")}
            st, cur = get_json(base, "/api/settings/autopid")
            cur = {k: v for k, v in (cur or {}).items() if k not in ("degraded", "pending_reboot")}
            if cur != restore:
                c.add("settings restored (a planned restart)",
                      set_switches(base, saved_settings, **restore))
            st, cur = get_json(base, "/api/settings/autopid")
            cur = {k: v for k, v in (cur or {}).items() if k not in ("degraded", "pending_reboot")}
            c.add("and read back as found", cur == restore)
        new_e = sorted(e_lines(base) - e0)
        c.add("no new E line in the unit's log (the ring spans the run's restarts)",
              not new_e, new_e[:3])
        st, h1 = get_json(base, "/api/restart/history")
        if isinstance(h0, dict) and isinstance(h1, dict):
            c.add("the unit rode through: only the planned restarts",
                  h1.get("boot_count") == h0.get("boot_count") + PLANNED[0] and
                  h1.get("unexpected_resets") == h0.get("unexpected_resets"),
                  "boot %s -> %s with %d planned, unexpected %s -> %s" % (
                      h0.get("boot_count"), h1.get("boot_count"), PLANNED[0],
                      h0.get("unexpected_resets"), h1.get("unexpected_resets")))
        st, faults1 = get_json(base, "/api/faults")
        c.add("no new fault", faults1 == faults0 or
              len((faults1 or {}).get("faults", [])) <= len((faults0 or {}).get("faults", [])),
              json.dumps(faults1)[:160])

    failed = c.failed()
    print("checks: %d, failed: %d, planned restarts: %d" % (len(c.rows), len(failed), PLANNED[0]))
    if failed:
        print("CAN FILTERS FAIL: " + "; ".join(failed))
        return 1
    print("CAN FILTERS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
