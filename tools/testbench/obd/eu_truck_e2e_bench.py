#!/usr/bin/env python3
"""End to end on an EU truck (TASK_j1939_wwh.md, phase 7): a vehicle that
answers OBD over UDS (ISO 27145, 29-bit, 250 kbit/s) AND broadcasts a J1939
network, from a device that knows a plain OBD-II car, through the Quick
Setup's path, to values of both kinds at the API and on the broker, both
kinds of trouble codes in one report, the legislated clear, and back to the
car.

The vehicle is actors/pcan_wwh_ecu.py with --truck: the WWH ECUs (engine 00,
aftertreatment 3D) and the J1939 truck (source 0: EEC1, CCVS1 ... at their
periods, DM1 once a second, the VIN by BAM on request) in one PCAN process
with ONE VIN. The ECU simulator only acknowledges (its own ECU off). The
actor runs before the DUT boots (a live bus at the first look; a car's rows
polled on a silent bus collide with a bus waking up at the other bitrate).

Legs:
  e1  the wizard's detection on a device that knows an OBD-II car, the
      native bus off: `dialect uds`, the J1939 network as well (from a bus
      sample), the VIN (one for both), rows of BOTH sets, the store entry
  e2  the wizard's one restart (can_manager listen-only at the measured
      bitrate + j1939): rows of both sets live and equal to the actor's
      values, no failed poll, the chip asks 250 kbit/s 29-bit only, the
      snapshot on the Pi's broker carries both
  e3  the DTC report: WWH codes (19 42, 19 55) and DM1 codes in ONE report
      (`protocol wwh`, `j1939 true`, lamps); the clear is the legislated one
      (14 FF FF 33: the WWH codes go, the DM1 code is heard again)
  e4  the stored car (another bit rate) made current while plugged into
      the truck: the bus guard looks again, nothing leaves the chip at the
      car's bit rate (no bus error at the truck); the truck made current
      again is polled again
  e5  back to the car, as a device is moved: unplugged from the truck
      (polling and the native bus off while the bus becomes the car's), then
      plugged into the simulator's car with the native bus off; the car is
      the current vehicle again and is polled (the stored truck stays known)
  restore: settings, store, tables and the simulator as found.
  x1  (only with --only, e.g. `--only e1,x1 --detections 60`) the detection
      repeated on the truck: no reset, the truck stays the car
  x2  (only with --only) the truck forgotten before each detection, so
      every one stores a NEW vehicle (tables, reload, entry): no reset
  x3  (only with --only) the first run's own scenario again and again: boot
      knowing the car on the truck's bus, first contact, the detection
  x5  (only with --only, alone; the second stage of the eutruck kind of
      test.ps1) a chain of the tables that pins the chip to a 500 kbit/s
      protocol on the truck's bus (a row's init, a type's init, test-a-PID,
      dtc_init), or resets it: nothing may reach the bus at the wrong rate

Verdict: `EU TRUCK E2E PASS`. Run on the PC (IDF venv python; python-can,
can-isotp):
  python eu_truck_e2e_bench.py [host[:port]] [--pcan PCAN_USBBUS2]
        [--only e1,e2] [--detections N] [--forget-after] [--logdir DIR]
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import bench_bus  # noqa: E402
import benchlib as bl  # noqa: E402
import j1939_ref as J  # noqa: E402
from pcbench import Actor, Bench, Dut, Run  # noqa: E402

DUT_HOST = "localhost:8081"
PCAN = "PCAN_USBBUS2"
ONLY = []
LOGDIR = tempfile.gettempdir()
DETECTIONS = 60                 # legs x1, x2, x3
FORGET_AFTER = False            # leg x4: forget the truck after the switch
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--pcan":
        PCAN = args[i + 1]; i += 2
    elif args[i] == "--only":
        ONLY = [x.strip() for x in args[i + 1].split(",") if x.strip()]; i += 2
    elif args[i] == "--logdir":
        LOGDIR = args[i + 1]; i += 2
    elif args[i] == "--detections":
        DETECTIONS = int(args[i + 1]); i += 2
    elif args[i] == "--forget-after":
        FORGET_AFTER = True; i += 1
    else:
        DUT_HOST = args[i]; i += 1

ALL_LEGS = ["e1", "e2", "e3", "e4", "e5"]   # the default run
ORDER = ["x5", "e1", "x1", "x2", "x3", "e2", "e3", "e4", "e5"]  # x*: with --only
VIN = "1WCANEUTRUCK00001"
RPM = 1250.0                    # the WWH ECU's engine speed (--set rpm=)
TRUCK_RPM = 1800.0              # the J1939 engine's (--truck-set rpm=)
WWH_DTCS = "P0420:08:02,P2463-1F:04:04"     # confirmed, pending (format 04)
DM1 = "110-0-5"                             # the J1939 engine's active code
ACTOR_ARGS = ["--id-format", "29", "--bitrate", "250", "--ecu", "0x00", "--ecu2", "0x3D",
              "--vin", VIN, "--set", f"rpm={RPM:g}", "--dtcs", WWH_DTCS,
              "--truck", "--truck-sa", "0x00", "--truck-dtcs", DM1,
              "--truck-set", f"rpm={TRUCK_RPM:g}"]

LEGS = ALL_LEGS if not ONLY else [x for x in ORDER if x in ONLY]

run = Run("EU TRUCK E2E")
# the j1939 task and a lasting can_core_rx task exist from the wizard's
# restart (e2) on; before it the native node is up for a look at a time
dut = Dut(DUT_HOST, own_tags=("autopid", "j1939", "can_manager", "can_core"),
          tasks=("autopid",) + (("can_core_rx", "j1939") if "e2" in LEGS else ()))
actor = Actor(os.path.join(HERE, "..", "actors", "pcan_wwh_ecu.py"), LOGDIR,
              "eu_truck", "TRUCK READY", "ECU DONE ")
bus_kbps = [0]


# ---- the rig ---------------------------------------------------------------------

def sim_ack_only(kbps):
    if bus_kbps[0] == kbps:
        return
    bench_bus.sim_set(bitrate=kbps, enabled=True)
    bench_bus.sim_set(enabled=False)
    bus_kbps[0] = kbps


def wait_for(what, secs, fn):
    end = time.time() + secs
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(0.5)
    raise Bench(f"{what}: not within {secs} s")


def autopid():
    return dut.get("/api/autopid")


def params(doc=None):
    doc = doc or autopid()
    return {p["name"]: p for p in doc.get("params", [])}


def vehicles():
    code, v = dut.api("/api/autopid/vehicles")
    return v if code == 200 and isinstance(v, dict) else {}


def current_car():
    v = vehicles()
    for e in v.get("vehicles", []):
        if e.get("key") == v.get("current"):
            return e
    return {}


def forget(key):
    return dut.api("/api/autopid/vehicles/" + key, "DELETE")[0]


def wait_detection(secs):
    return wait_for("the detection job", secs,
                    lambda: (lambda s: s if s.get("status") in ("done", "failed")
                             else None)(dut.get("/api/autopid/std_scan")))


def close(got, want, step=0.5):
    return isinstance(got, (int, float)) and abs(got - want) <= step


def dtc():
    return dut.get("/api/autopid/dtc")


def scan_dtc(secs=40):
    code, r = dut.api("/api/autopid/dtc/scan", "POST", {})
    if code not in (200, 202):
        raise Bench(f"dtc scan: {code} {r}")
    return wait_for("the DTC scan to finish", secs,
                    lambda: (lambda x: x.get("report") if x.get("scanning") is False
                             and x.get("report", {}).get("valid") else None)(dtc()))


def broker_autopid(device_id):
    """The retained ~/autopid snapshot on the Pi's broker, as a dict."""
    topic = f"wican/{device_id}/autopid"
    rc, out = bl.pi(f"timeout 6 mosquitto_sub -h localhost -t '{topic}' -C 1 2>&1",
                    timeout=30)
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                return json.loads(ln)
            except ValueError:
                pass
    return {}


# ---- legs ------------------------------------------------------------------------

def leg_e1():
    """The wizard's Detect on a device that knows a car, the native bus off."""
    # first contact may have started a detection of its own already (the
    # car's rows fail, the chip's walk meets the WWH ECU): the wizard's
    # Detect then lands on a running job (409) and waits for it, as here
    code, r = dut.api("/api/autopid/vehicles/detect", "POST", {})
    run.check("e1_detection_started_or_running", code in (200, 202, 409), f"{code} {r}")
    t0 = time.time()
    st = wait_detection(90)
    run.metric("e1_detection_s", round(time.time() - t0, 1), "s")
    run.check("e1_detection_done", st.get("status") == "done", json.dumps(st)[:200])
    code, r = dut.api("/api/autopid/std_scan/result")
    r = r if isinstance(r, dict) else {}
    run.check("e1_a_wwh_vehicle_with_a_j1939_network",
              r.get("dialect") == "uds" and r.get("protocol_detected") == "9"
              and r.get("j1939") is True and r.get("j1939_listening") is False
              and r.get("bus_kbps") == 250 and r.get("vin") == VIN and r.get("key") == VIN,
              json.dumps({k: r.get(k) for k in ("dialect", "protocol_detected", "j1939",
                                                "j1939_listening", "bus_kbps", "vin", "key",
                                                "found")}))
    rows = r.get("supported", [])
    cmds = [str(x.get("cmd", "")).upper() for x in rows]
    wwh = [c for c in cmds if c.startswith("22F4")]
    pgn = [c for c in cmds if c.startswith("PGN:")]
    run.check("e1_rows_of_both_sets",
              len(wwh) >= 5 and "22F40C" in wwh and len(pgn) >= 10 and "PGN:F004" in pgn
              and len(rows) == r.get("found"),
              f"{len(wwh)} WWH rows, {len(pgn)} PGN rows, found {r.get('found')}")
    e = current_car()
    run.check("e1_store_entry",
              e.get("key") == VIN and e.get("dialect") == "uds" and e.get("j1939") is True
              and e.get("protocol") == "9" and e.get("vin") == VIN,
              json.dumps({k: e.get(k) for k in ("key", "dialect", "j1939", "protocol", "vin",
                                                "ecus")}))
    ecus = e.get("ecus", "")
    run.check("e1_chip_responders_are_the_print",
              "18DAF100:" in ecus.upper() and "18DAF13D:" in ecus.upper(), ecus)


def leg_x1():
    """NOT in the default set (`--only e1,x1 [--detections N]`): the
    detection again and again on the truck with the native bus off. Each one
    looks at the bus with a listen-only node of its own (the guard's probe,
    the one-second sample), walks the chip's protocols and stores the car.
    Written for the hunt of 2026-10-04: the bench's first run lost the
    device to a reset within half a second of a detection's end, once."""
    b0 = dut.boot_count()
    done = 0
    why = ""
    t0 = time.time()
    for n in range(DETECTIONS):
        code, r = dut.api("/api/autopid/vehicles/detect", "POST", {})
        if code not in (200, 202, 409):
            why = f"detect: {code} {r}"
            break
        st = {}
        end = time.time() + 60
        while time.time() < end:
            code, st = dut.api("/api/autopid/std_scan")
            st = st if code == 200 and isinstance(st, dict) else {}
            if st.get("status") in ("done", "failed"):
                break
            b = dut.boot_count()
            if b is not None and b != b0:
                break
            time.sleep(0.2)
        b = dut.boot_count()
        if b is not None and b != b0:
            why = f"the device restarted in detection {n + 1} (boot_count {b0} -> {b})"
            break
        if st.get("status") != "done":
            why = f"detection {n + 1}: {json.dumps(st)[:160]}"
            break
        done += 1
    run.metric("x1_detections", done)
    run.metric("x1_detection_avg_s", round((time.time() - t0) / max(done, 1), 1), "s")
    run.check("x1_detections_without_a_reset", done == DETECTIONS and not why,
              why or f"{done} detections")
    e = current_car()
    run.check("x1_truck_still_the_car", e.get("key") == VIN and e.get("j1939") is True,
              json.dumps({k: e.get(k) for k in ("key", "dialect", "j1939", "protocol")}))


def leg_x2():
    """NOT in the default set (`--only e1,x2 [--detections N]`): the truck
    forgotten and detected again, N times. A detection of the car that is
    already current only updates its entry (x1); forgotten first, every one
    takes the NEW-vehicle path of the failing run: the tables written
    (config.json and the car's file), the live tables reloaded under a poller
    that keeps running the passive rows, the entry created, the event."""
    b0 = dut.boot_count()
    done = 0
    why = ""
    t0 = time.time()
    for n in range(DETECTIONS):
        code = forget(VIN)
        if code not in (200, 204):
            why = f"forget before detection {n + 1}: HTTP {code}"
            break
        code, r = dut.api("/api/autopid/vehicles/detect", "POST", {})
        if code not in (200, 202):
            why = f"detect {n + 1}: {code} {r}"
            break
        st = {}
        end = time.time() + 60
        while time.time() < end:
            code, st = dut.api("/api/autopid/std_scan")
            st = st if code == 200 and isinstance(st, dict) else {}
            if st.get("status") in ("done", "failed"):
                break
            b = dut.boot_count()
            if b is not None and b != b0:
                break
            time.sleep(0.2)
        b = dut.boot_count()
        if b is not None and b != b0:
            why = f"the device restarted in detection {n + 1} (boot_count {b0} -> {b})"
            break
        if st.get("status") != "done" or current_car().get("key") != VIN:
            why = f"detection {n + 1}: {json.dumps(st)[:120]}, car {current_car().get('key')}"
            break
        done += 1
    run.metric("x2_new_vehicle_detections", done)
    run.metric("x2_detection_avg_s", round((time.time() - t0) / max(done, 1), 1), "s")
    run.check("x2_new_vehicle_detections_without_a_reset", done == DETECTIONS and not why,
              why or f"{done} detections, each of a forgotten truck")


def leg_x3(found_car):
    """NOT in the default set (`--only e1,x3 [--detections N]`): the failing
    run's own scenario, N times. The device boots knowing the car, on the
    truck's bus; first contact finds the truck and starts the detection by
    itself; the bench asks for one too (409) and watches the job, as leg e1
    does. Then the car is made current again and the truck forgotten, for
    the next boot. About half a minute a round."""
    done = 0
    why = ""
    t0 = time.time()
    for n in range(DETECTIONS):
        code, r = dut.api("/api/autopid/vehicles/" + found_car + "/activate", "POST", {})
        if code != 200:
            why = f"round {n + 1}: activate the car: {code} {r}"
            break
        code = forget(VIN)
        if code not in (200, 204):
            why = f"round {n + 1}: forget the truck: HTTP {code}"
            break
        if not actor.running():
            why = f"round {n + 1}: the truck actor is gone (the bench's fault, not the device's)"
            break
        dut.restart()
        b0 = dut.boot_count()
        # the device's OWN detection (first contact, once the chip is up,
        # about 14 s after boot): a Detect asked for earlier than that runs
        # before the chip answers and finds no ECU. Once it runs, ask too
        # (409, as leg e1's request got) and watch the job at 2 Hz.
        st = {}
        asked = False
        end = time.time() + 60
        while time.time() < end:
            code, st = dut.api("/api/autopid/std_scan")
            st = st if code == 200 and isinstance(st, dict) else {}
            if st.get("status") == "running" and not asked:
                asked = True
                code, r = dut.api("/api/autopid/vehicles/detect", "POST", {})
                if code != 409:
                    why = f"round {n + 1}: a second detect was taken: {code} {r}"
                    break
            if st.get("status") in ("done", "failed"):
                break
            b = dut.boot_count()
            if b is not None and b0 is not None and b != b0:
                break
            time.sleep(0.5)
        if why:
            break
        time.sleep(1.5)         # the half second after the job's end is the place
        b = dut.boot_count()
        if b is None or b0 is None or b != b0:
            why = f"the device restarted in round {n + 1} (boot_count {b0} -> {b})"
            break
        if st.get("status") != "done" or current_car().get("key") != VIN:
            why = f"round {n + 1}: {json.dumps(st)[:120]}, car {current_car().get('key')}"
            break
        done += 1
        if done % 10 == 0:
            print(f"  x3: {done} rounds clean", flush=True)
    run.metric("x3_boot_and_detect_rounds", done)
    run.metric("x3_round_avg_s", round((time.time() - t0) / max(done, 1), 1), "s")
    run.check("x3_boot_first_contact_detection_without_a_reset",
              done == DETECTIONS and not why, why or f"{done} rounds")


def leg_e4(found_car):
    """The stored car (500 kbit/s) made the current vehicle while the device
    is plugged into the truck (250 kbit/s, talking): the UI's "use this car"
    pressed on the wrong vehicle. The bus guard has to look again and read a
    LIVE bus at 250: nothing may leave the chip at the car's bit rate. Judged
    on the truck's side (its adapter reports every bus error with the wall
    clock, and the frames it could not send) and on the guard's word. Then
    the truck is made current again and is polled. Found 2026-10-05 by a
    soak leg that did this between two boots: about every other time the
    guard was not asked, `ATTP6` went out, and the truck's adapter was
    bus-off 40 ms after its first error. `--forget-after` (with --only)
    forgets the truck straight after the switch, as that soak did."""
    mark = actor.mark()
    doc0 = autopid()
    t0 = time.time()
    code, r = dut.api("/api/autopid/vehicles/" + found_car + "/activate", "POST", {})
    t_act = time.time()
    run.check("e4_car_activated", code == 200, f"{code} {r}")
    if FORGET_AFTER:
        # the soak leg's sequence: the truck forgotten straight after (file
        # work in the HTTP task while the guard takes its look)
        code = forget(VIN)
        print(f"  truck forgotten +{time.time() - t0:.2f} s (HTTP {code})", flush=True)
    seen = []
    cans = []
    while time.time() - t0 < 8:
        doc = autopid()
        g = doc.get("bus_guard", {})
        st = doc.get("stats", {})
        row = (g.get("bus"), g.get("bus_kbps"), g.get("verdict"), g.get("parked"),
               st.get("paused_bus"))
        if not seen or row != seen[-1][1]:
            seen.append((round(time.time() - t0, 2), row, g.get("reason")))
        code, c = dut.api("/api/can")
        if code == 200 and isinstance(c, dict):
            crow = (c.get("running"), c.get("link"), c.get("baud_detected"), c.get("rx"),
                    c.get("rx_bad"), c.get("rx_deaf"), c.get("rx_storms"),
                    (c.get("probe") or {}).get("result"))
            if not cans or crow != cans[-1][1]:
                cans.append((round(time.time() - t0, 2), crow))
        time.sleep(0.05)
    doc1 = autopid()
    for t, row, reason in seen:
        print(f"  guard +{t:.2f} s: bus {row[0]} {row[1]} kbit/s, verdict {row[2]}, "
              f"parked {row[3]}, paused_bus {row[4]}: {reason}", flush=True)
    for t, crow in cans[:40]:
        print(f"  can   +{t:.2f} s: running {crow[0]}, link {crow[1]}, detected {crow[2]}, "
              f"rx {crow[3]}, rx_bad {crow[4]}, rx_deaf {crow[5]}, storms {crow[6]}, "
              f"probe {crow[7]}", flush=True)
    evs = actor.events("BUS ", mark)
    for e in evs[:12]:
        print(f"  truck +{e.get('t', 0) - t0:.2f} s: {json.dumps(e)}", flush=True)
    run.metric("e4_activate_http_s", round(t_act - t0, 2), "s")
    last = seen[-1][1] if seen else ()
    run.check("e4_guard_reads_the_live_bus",
              bool(last) and last[0] == "live" and last[1] == 250,
              f"{len(seen)} guard states, the last: {seen[-1] if seen else None}")
    run.check("e4_guard_never_said_silent", not [s for s in seen if s[1][0] == "silent"],
              json.dumps([(s[0], s[1][0], s[1][2]) for s in seen])[:300])
    errs = [e for e in evs if e.get("event") == "errors_begin"]
    fails = [e for e in evs if e.get("event") == "tx_failing"]
    run.check("e4_no_bus_error_at_the_truck", not errs and not fails,
              f"{len(errs)} error bursts, {len(fails)} times the truck could not send; first: "
              + json.dumps((errs + fails)[:2]))
    s0, s1 = doc0.get("stats", {}), doc1.get("stats", {})
    run.metric("e4_polls_failed_after_the_switch",
               s1.get("polls_failed", 0) - s0.get("polls_failed", 0))
    if FORGET_AFTER:
        return                  # the stress variant: the truck is gone

    # ... and back: the truck made current again is the right car on this bus
    code, r = dut.api("/api/autopid/vehicles/" + VIN + "/activate", "POST", {})
    t1 = time.time()
    p = {}
    while time.time() - t1 < 40:
        p = params()
        if close(p.get("EngineRPM", {}).get("value"), RPM, 0.25):
            break
        time.sleep(0.5)
    g = autopid().get("bus_guard", {})
    run.metric("e4_truck_back_s", round(time.time() - t1, 1), "s")
    run.check("e4_truck_current_again_and_polled",
              code == 200 and current_car().get("key") == VIN
              and close(p.get("EngineRPM", {}).get("value"), RPM, 0.25)
              and g.get("bus") == "live" and g.get("verdict") == "allow",
              f"activate {code}, car {current_car().get('key')}, EngineRPM "
              f"{p.get('EngineRPM', {}).get('value')}, guard {g.get('bus')} / {g.get('verdict')}")
    evs = actor.events("BUS ", mark)
    run.check("e4_no_bus_error_on_the_way_back", not evs, json.dumps(evs[:2]))


PIN = "ATSP6;ATSH7DF"           # a 500 kbit/s protocol, as most profiles pin
PINNED_ROW = {"name": "PinnedRow", "type": "specific", "cmd": "010C", "init": PIN,
              "group": "default",
              "parameters": [{"name": "PinnedRow", "expression": "[B2:B3]*0.25",
                              "unit": "rpm", "class": "none"}]}
PLAIN_ROW = {"name": "PlainRow", "type": "custom", "cmd": "010C", "group": "default",
             "parameters": [{"name": "PlainRow", "expression": "[B2:B3]*0.25",
                             "unit": "rpm", "class": "none"}]}
X5_QUIET = {"dtc_enabled": False, "dtc_init": "", "custom_init": ""}
x5_actor_restarts = [0]


def truck_failing():
    failing = False
    for ev in actor.events("BUS ", 0):
        if ev.get("event") == "tx_failing":
            failing = True
        elif ev.get("event") == "tx_ok_again":
            failing = False
    return failing


def ensure_truck():
    """The truck on the bus. A PCAN adapter that went bus-off stays off (the
    actor does not reset it): the actor is started again. Only while nothing
    of the device transmits at the wrong bit rate, or it is off again at
    once."""
    if actor.running() and not truck_failing():
        return False
    actor.start("e2e", ["--pcan", PCAN] + ACTOR_ARGS, secs=1500)
    x5_actor_restarts[0] += 1
    print("  (the truck's adapter was bus-off: its actor started again)", flush=True)
    return True


def bus_rest(secs=3.0, limit=30.0):
    """Wait until the truck sends and reported nothing for `secs`. Returns
    (the actor's log mark a step is judged from, rested)."""
    end = time.time() + limit
    while True:
        m = actor.mark()
        time.sleep(secs)
        if not actor.events("BUS ", m) and not truck_failing() and actor.running():
            return actor.mark(), True
        if time.time() > end:
            return actor.mark(), False


def x5_judge(step, mark, what, rested=True):
    """No bus error at the truck since `mark`: its adapter reports the first
    error after a quiet second, and the moment it cannot send any more. A
    step that began on a bus not at rest is not judged: it fails."""
    evs = actor.events("BUS ", mark)
    errs = [x for x in evs if x.get("event") == "errors_begin"]
    fails = [x for x in evs if x.get("event") == "tx_failing"]
    for x in evs[:4]:
        print(f"  truck: {json.dumps(x)}", flush=True)
    ok = rested and not errs and not fails
    run.check(f"x5{step}_no_bus_error_at_the_truck", ok,
              f"{what}: {len(errs)} error bursts, {len(fails)} times the truck could not send"
              + ("" if rested else " (NOT JUDGED: the bus was not at rest before the step)"))
    return ok


def x5_guard():
    g = autopid().get("bus_guard", {})
    return {k: g.get(k) for k in ("bus", "bus_kbps", "verdict", "refused", "refused_reason")}


def x5_activate(key, what):
    code, r = dut.api("/api/autopid/vehicles/" + key + "/activate", "POST", {})
    if code != 200:
        raise Bench(f"activate {what}: {code} {r}")


def x5_boot(found_car, cfg, settings):
    """The device as its owner left it in the car: the car the current
    vehicle with `cfg` as its tables, the truck unknown, `settings` staged;
    restarted on the truck's bus. The truck is put back on the bus while the
    device boots (nothing of it transmits then). Returns the actor's log
    mark of the boot."""
    # a detection first contact started is left to end (the store is its)
    end = time.time() + 60
    while time.time() < end:
        code, st = dut.api("/api/autopid/std_scan")
        if code == 200 and isinstance(st, dict) and st.get("status") != "running":
            break
        time.sleep(0.5)
    x5_activate(found_car, "the car")
    code, r = dut.api("/api/autopid/config", "PUT", cfg)
    if code != 200:
        raise Bench(f"PUT the car's tables: {code} {r}")
    forget(VIN)
    # dut.restart(), with the truck's turn in the middle
    dut.sweep()
    changed = dut.stage("autopid", settings)
    b0 = dut.boot_count()
    if b0 is None:
        raise Bench("no boot_count before a restart")
    t0 = time.time()
    dut.api("/api/settings/submit" if changed else "/api/restart", "POST", {})
    time.sleep(2.0)             # the device is down a second after the request
    ensure_truck()
    mark = actor.mark()
    while time.time() - t0 < 150:
        b = dut.boot_count()
        if b is not None and b != b0:
            dut.restarts += 1
            return mark
        time.sleep(0.5)
    raise Bench("the DUT did not come back within 150 s of a restart")


def x5_first_contact(secs=60):
    """The boot until the truck is the current vehicle and its detection is
    over (or `secs`). Returns the current car."""
    t0 = time.time()
    seen = []
    car = {}
    while time.time() - t0 < secs:
        g = autopid().get("bus_guard", {})
        row = (g.get("bus"), g.get("bus_kbps"), g.get("verdict"), g.get("refused"))
        if not seen or row != seen[-1][1]:
            seen.append((round(time.time() - t0, 1), row))
        car = current_car()
        code, st = dut.api("/api/autopid/std_scan")
        if car.get("key") == VIN and isinstance(st, dict) and st.get("status") != "running":
            break
        time.sleep(0.5)
    for ts, row in seen:
        print(f"  guard +{ts:.1f} s: bus {row[0]} {row[1]} kbit/s, verdict {row[2]}, "
              f"refused {row[3]}", flush=True)
    print(f"  current car after {time.time() - t0:.0f} s: {car.get('key')}", flush=True)
    return car


def leg_x5(found_car, found_cfg):
    """NOT in the default set (`--only x5`, alone): a chain of the TABLES
    that sets a CAN protocol (`ATSP6`, as most vehicle profiles carry) on a
    bus that runs at another bit rate. The bus guard's verdict is about the
    protocol autopid pins itself; these chains go to the chip as written.
    Four ways in, each on the truck's bus (250 kbit/s, talking), each judged
    at the truck (no bus error, no frame it could not send):
      a  dtc_init, at a trouble code scan of the car
      b  a row tested from the UI (test-a-PID) with its own init
      c  a row's own init, polled after a boot
      d  a type's init (custom_init), polled after a boot
      e  a reset inside a chain (`ATWS`): the prelude follows it
      f  Automate off, a row tested as the first chip traffic of a boot:
         the prelude goes out first
    and in c and d first contact still finds the truck. Found by reading the
    code on 2026-10-05, then here: before the fix the truck's adapter was
    bus-off 19 ms after the first error of step b."""
    rows = found_cfg.get("pids", [])

    # boot 1: the car's tables as found; dtc_init sets protocol 6; the
    # specific rows off (boot 2 brings one in, live from its boot on)
    mark = x5_boot(found_car, found_cfg,
                   {"dtc_enabled": True, "dtc_allow_clear": False, "dtc_protocol": "obd",
                    "dtc_scan_period_min": 0, "dtc_init": "ATSP6", "custom_init": "",
                    "specific_enabled": False})
    car = x5_first_contact()
    run.check("x5_truck_found_with_the_tables_as_found", car.get("key") == VIN,
              f"current car {car.get('key')}")
    x5_judge("_boot", mark, "a boot with the car's tables as found")

    # a. dtc_init at a scan of the car (made current here, on the truck's bus)
    x5_activate(found_car, "the car")
    mark, rested = bus_rest()
    code, r = dut.api("/api/autopid/dtc/scan", "POST", {})
    t0 = time.time()
    while time.time() - t0 < 30 and (code in (200, 202)) and dtc().get("scanning"):
        time.sleep(0.5)
    time.sleep(2.5)
    print(f"  dtc scan: HTTP {code} {json.dumps(r)[:260]}", flush=True)
    x5_judge("a_dtc_init", mark, "a trouble code scan with dtc_init ATSP6", rested)
    run.check("x5a_scan_refused_with_the_reason",
              code == 409 and "500" in json.dumps(r) and "250" in json.dumps(r),
              f"HTTP {code} {json.dumps(r)[:200]}")

    # b. test-a-PID with an init that sets protocol 6; the truck current
    # again first (its prelude takes the chip off whatever a left behind)
    x5_activate(VIN, "the truck")
    time.sleep(2.5)
    ensure_truck()
    mark, rested = bus_rest()
    code, r = dut.api("/api/autopid/test", "POST",
                      {"cmd": "010C", "type": "custom", "init": PIN}, timeout=25)
    time.sleep(2.5)
    print(f"  test-a-PID: HTTP {code} {json.dumps(r)[:260]}", flush=True)
    x5_judge("b_test_a_pid", mark, "a row tested with init " + PIN, rested)
    run.check("x5b_test_refused_with_the_reason",
              code == 409 and "500" in json.dumps(r) and "250" in json.dumps(r),
              f"HTTP {code} {json.dumps(r)[:200]}")

    # e. a reset inside a chain: the chip comes back on the protocol STORED in
    # it (the last vehicle detected) and its first request went out on that
    # one about every third time, at 500 kbit/s when the store was the
    # car's. The baseline prelude has to follow the reset. (The truck is the
    # current vehicle here: the prelude is its own.)
    ensure_truck()
    mark, rested = bus_rest()
    code, r = dut.api("/api/autopid/test", "POST",
                      {"cmd": "22F40C", "type": "custom", "init": "ATWS"}, timeout=25)
    time.sleep(2.5)
    tr = str(r.get("transcript")) if isinstance(r, dict) else str(r)
    print(f"  test-a-PID: HTTP {code} {json.dumps(r)[:300]}", flush=True)
    x5_judge("e_reset_in_a_chain", mark, "a row tested with init ATWS", rested)
    run.check("x5e_the_prelude_follows_the_reset",
              code == 200 and "> ATWS" in tr and "> ATTP" in tr.split("> ATWS", 1)[-1],
              " ".join(tr.split())[:200])

    # boot 2: c. a row with its own init, first in the car's tables
    cfg = json.loads(json.dumps(found_cfg))
    cfg["pids"] = [PINNED_ROW] + rows
    mark = x5_boot(found_car, cfg, dict(X5_QUIET, specific_enabled=True))
    car = x5_first_contact()
    ok = x5_judge("c_row_init", mark, "a boot with a row whose init is " + PIN)
    g = x5_guard()
    run.check("x5c_row_refused_and_said_so",
              ok and isinstance(g.get("refused"), int) and g["refused"] > 0
              and "500" in str(g.get("refused_reason")), json.dumps(g))
    run.check("x5c_truck_found_anyway", car.get("key") == VIN, f"current car {car.get('key')}")

    # boot 3: d. the type's init sets the protocol, the row itself is plain
    cfg = json.loads(json.dumps(found_cfg))
    cfg["pids"] = [PLAIN_ROW] + rows
    mark = x5_boot(found_car, cfg, dict(X5_QUIET, custom_init=PIN))
    car = x5_first_contact()
    ok = x5_judge("d_type_init", mark, "a boot with custom_init " + PIN)
    g = x5_guard()
    run.check("x5d_row_refused_and_said_so",
              ok and isinstance(g.get("refused"), int) and g["refused"] > 0
              and "500" in str(g.get("refused_reason")), json.dumps(g))
    run.check("x5d_truck_found_anyway", car.get("key") == VIN, f"current car {car.get('key')}")

    # boot 4: f. Automate off, and a row tested as the first chip traffic of
    # the boot. The driver's bring-up reset leaves the chip on the protocol
    # STORED in it; before 2026-10-05 the test sent no prelude and its
    # request went out on the stored one (8 of 8 boots with the truck's
    # protocol stored and a 500 kbit/s vehicle on the bus).
    mark = x5_boot(found_car, found_cfg, dict(X5_QUIET, enabled=False))
    time.sleep(10)              # the chip's bring-up
    code, r = dut.api("/api/autopid/test", "POST", {"cmd": "0100", "type": "custom"},
                      timeout=25)
    time.sleep(2.5)
    tr = str(r.get("transcript")) if isinstance(r, dict) else str(r)
    print(f"  test-a-PID: HTTP {code} {json.dumps(r)[:300]}", flush=True)
    x5_judge("f_first_test_of_a_boot", mark, "a row tested as the first chip traffic of a boot")
    run.check("x5f_the_test_starts_from_the_baseline",
              code == 200 and "> ATTP" in tr.split("> 0100", 1)[0],
              " ".join(tr.split())[:200])
    run.metric("x5_truck_actor_restarts", x5_actor_restarts[0])
    run.check("x5_truck_never_left_the_bus", x5_actor_restarts[0] == 0,
              f"its actor had to be started again {x5_actor_restarts[0]} times (bus-off)")


def leg_e2():
    """The wizard's one restart: the native bus listen-only at the measured
    bitrate + the J1939 listener. Both row sets live."""
    dut.restart({"can_manager": {"enabled": True, "baud": "250", "silent": True},
                 "j1939": {"enabled": True},
                 "autopid": {"dtc_enabled": True, "dtc_allow_clear": True, "dtc_pending": True,
                             "dtc_permanent": True, "dtc_protocol": "obd",
                             "dtc_scan_period_min": 0}})
    t0 = time.time()
    p = wait_for("values of both sets", 60,
                 lambda: (lambda x: x if close(x.get("EngineRPM", {}).get("value"), RPM, 0.25)
                          and close(x.get("EngineSpeed", {}).get("value"), TRUCK_RPM, 0.125)
                          else None)(params()))
    run.metric("e2_both_sets_live_s", round(time.time() - t0, 1), "s after the restart")
    run.check("e2_values_of_both_sets_are_the_actors",
              close(p["EngineRPM"]["value"], RPM, 0.25)
              and close(p["EngineSpeed"]["value"], TRUCK_RPM, 0.125)
              and close(p.get("EngineCoolantTemperature", {}).get("value"), J.DEFAULTS["coolant_c"], 1),
              json.dumps({k: p.get(k, {}).get("value") for k in ("EngineRPM", "EngineSpeed",
                                                                 "EngineCoolantTemperature")}))
    # (no VIN from the network here: this truck sends PGN 65260 on request
    # only and a listener never asks; the vehicle's VIN came from 22 F8 02)
    j = dut.get("/api/j1939")
    run.check("e2_listener_up_listen_only",
              j.get("state") == "listening" and j.get("bus") == "j1939" and j.get("mode") == "listen"
              and j["can"]["listen_only"] is True and j["can"]["baud_kbps"] == 250,
              json.dumps({k: j.get(k) for k in ("state", "bus", "mode", "can", "vin")}))
    s0 = autopid().get("stats", {})
    mark = actor.mark()
    time.sleep(10)
    s1 = autopid().get("stats", {})
    ok = s1.get("polls_ok", 0) - s0.get("polls_ok", 0)
    failed = s1.get("polls_failed", 0) - s0.get("polls_failed", 0)
    pub = s1.get("passive_published", 0) - s0.get("passive_published", 0)
    run.check("e2_chip_rows_poll_and_pgn_rows_publish",
              ok > 20 and failed == 0 and pub > 50,
              f"{ok} polls answered, {failed} failed, {pub} J1939 publications in 10 s")
    reqs = actor.events("ECU ", mark)
    stray = [x for x in reqs if not str(x.get("req", "")).startswith("22f4") or x.get("func")]
    run.check("e2_chip_asks_22f4xx_physically_only", len(reqs) > 20 and not stray,
              f"{len(reqs)} requests, {len(stray)} stray: {json.dumps(stray[:2])}")
    can = dut.get("/api/can")
    run.check("e2_native_bus_transmitted_nothing", can["tx"] == 0, f"tx {can['tx']}")
    dev = str(dut.get("/api/info").get("device_id", ""))
    snap = {}
    end = time.time() + 20
    while time.time() < end:
        snap = broker_autopid(dev)
        if close(snap.get("EngineRPM"), RPM, 0.25) and close(snap.get("EngineSpeed"), TRUCK_RPM, 0.125):
            break
        time.sleep(2)
    run.check("e2_broker_snapshot_carries_both",
              close(snap.get("EngineRPM"), RPM, 0.25) and close(snap.get("EngineSpeed"), TRUCK_RPM, 0.125),
              json.dumps({k: snap.get(k) for k in ("EngineRPM", "EngineSpeed", "timestamp")})
              + f" (device {dev})")


def leg_e3():
    """One report, two kinds of codes; the legislated clear."""
    code, d = dut.api("/api/autopid/dtc")
    run.check("e3_path_is_wwh", code == 200 and d.get("path") == "wwh", json.dumps(d)[:120])
    rep = scan_dtc()
    items = rep.get("items", [])
    wwh_items = {it["code"]: it for it in items if it.get("ecu")}
    dm_items = {it["code"]: it for it in items if it.get("sa") is not None}
    run.check("e3_wwh_and_dm1_codes_in_one_report",
              rep.get("protocol") == "wwh" and rep.get("j1939") is True
              and set(rep.get("stored", [])) == {"P0420", "SPN110-0"}
              and rep.get("pending") == ["P2463-1F"]
              and wwh_items.get("P0420", {}).get("ecu", "").upper() == "18DAF100"
              and dm_items.get("SPN110-0", {}).get("sa") == 0 and dm_items["SPN110-0"].get("oc") == 5,
              json.dumps({k: rep.get(k) for k in ("protocol", "j1939", "stored", "pending",
                                                  "permanent", "error")}) + json.dumps(items)[:300])
    lamps = rep.get("lamps", {})
    run.check("e3_mil_from_both", rep.get("mil") is True and lamps.get("mil") is True,
              json.dumps({"mil": rep.get("mil"), "lamps": lamps, "sources": rep.get("sources")})[:300])
    mark = actor.mark()
    code, r = dut.api("/api/autopid/dtc/clear", "POST", {"confirm": True, "mode": "always"},
                      timeout=30)
    run.check("e3_legislated_clear", code == 200 and r.get("cleared") is True, f"{code} {r}")
    clears = [x for x in actor.events("ECU ", mark) if str(x.get("req", "")).startswith("14ffff33")]
    run.check("e3_clear_is_14_ffff33_functional", len(clears) >= 1 and all(x.get("func") for x in clears),
              json.dumps(clears)[:200])
    time.sleep(2.0)
    rep = scan_dtc()
    run.check("e3_wwh_codes_gone_dm1_still_heard",
              rep.get("stored") == ["SPN110-0"] and rep.get("pending") == []
              and rep.get("j1939") is True,
              json.dumps({k: rep.get(k) for k in ("stored", "pending", "error")}))


def leg_e5(found_sim, found_car):
    """Back to the car, as a device is moved: unplugged from the truck
    (nothing of it transmits while the bus becomes the car's: a chip polling
    at 250 kbit/s into the simulator coming up at 500 is a fight no vehicle
    has), then plugged into the car as its owner runs it (the native bus
    off)."""
    actor.stop()
    dut.restart({"can_manager": {"enabled": False}, "j1939": {"enabled": False},
                 "autopid": {"enabled": False, "dtc_enabled": False, "dtc_allow_clear": False}})
    bench_bus.sim_set(bitrate=found_sim["bitrate"], id_format=found_sim["id_format"], enabled=True)
    bus_kbps[0] = int(found_sim["bitrate"])
    dut.restart({"autopid": {"enabled": True}})
    t0 = time.time()
    e = wait_for("the car as the current vehicle", 90,
                 lambda: (lambda x: x if x.get("key") == found_car else None)(current_car()))
    run.metric("e5_car_back_s", round(time.time() - t0, 1), "s after the restart")
    run.check("e5_car_is_current_again", e.get("key") == found_car, e.get("key"))
    s0 = autopid().get("stats", {})
    time.sleep(6)
    s1 = autopid().get("stats", {})
    run.check("e5_car_polled", s1.get("polls_ok", 0) - s0.get("polls_ok", 0) > 5,
              f"polls_ok {s0.get('polls_ok')} -> {s1.get('polls_ok')}")
    keys = {x["key"] for x in vehicles().get("vehicles", [])}
    run.check("e5_truck_still_known", VIN in keys, str(sorted(keys))[:200])


# ---- main ------------------------------------------------------------------------

def main():
    legs = LEGS
    unknown = [x for x in ONLY if x not in ORDER]
    if unknown or not legs:
        print(f"unknown leg(s) {unknown}; legs: {' '.join(ORDER)}")
        return 2

    st0 = dut.get("/api/status")
    comps = ("can_manager", "j1939", "autopid")
    found = {n: dut.settings(n) for n in comps}
    found_sim = bench_bus.sim_get("ecu_sim")
    for e in list(vehicles().get("vehicles", [])):
        if e.get("vin") == VIN:
            print(f"forgetting a truck left behind: {e.get('key')}")
            forget(e["key"])
    found_store = vehicles()
    found_keys = {e["key"] for e in found_store.get("vehicles", [])}
    found_car = found_store.get("current")
    found_cfg = dut.get("/api/autopid/config")
    code, faults0 = dut.api("/api/faults")
    print(f"DUT {DUT_HOST}: boot_count {st0.get('boot_count')}, unexpected_resets "
          f"{st0.get('unexpected_resets')}, version {st0.get('version')}")
    print("as found: " + json.dumps({n: {k: found[n].get(k) for k in
                                         ("enabled", "baud", "silent", "mode", "std_protocol",
                                          "dtc_enabled") if k in found[n]} for n in found}))
    print(f"as found: simulator {json.dumps(found_sim)}, current car {found_car}")
    if not found_car:
        print("no current car in the store: the e2e needs a known OBD-II car as found")
        return 2
    dut.as_found()

    done = 0
    try:
        sim_ack_only(250)
        # on the bus before the DUT boots, and for as long as the legs take (a
        # soak of 2026-10-05 outlived the actor's 15 minutes: the truck left
        # the bus in round 41 and the round "failed")
        life = 900 + (DETECTIONS * 30 if any(x in legs for x in ("x1", "x2", "x3")) else 0)
        actor.start("e2e", ["--pcan", PCAN] + ACTOR_ARGS, secs=life)
        # a device that knows a car: the native bus off, polling on, the chip's
        # protocol follows the car
        dut.restart({"can_manager": {"enabled": False}, "j1939": {"enabled": False},
                     "autopid": {"enabled": True, "std_protocol": "0", "pause_mode": "requests_only",
                                 "dtc_enabled": False, "dtc_allow_clear": False}})
        time.sleep(6)           # the car's rows fail, the chip probes
        for leg in legs:
            print(f"--- {leg}", flush=True)
            if leg == "e5":
                leg_e5(found_sim, found_car)
            elif leg == "x5":
                leg_x5(found_car, found_cfg)
            elif leg in ("x3", "e4"):
                globals()["leg_" + leg](found_car)
            else:
                globals()["leg_" + leg]()
            dut.sweep()
            done += 1
            print(f"PROGRESS {done}/{len(legs)}", flush=True)
    except Exception as e:  # noqa: BLE001 - a bench bug is a verdict too
        if not isinstance(e, Bench):
            import traceback
            traceback.print_exc()
        run.check("bench_ran_to_the_end", False, f"{type(e).__name__}: {e}")
        for ln in dut.ring_lines()[-400:]:
            if " D (" in ln or ": " not in ln or ") " not in ln:
                continue
            tag = ln.split(") ", 1)[1].split(":", 1)[0].strip()
            if tag in ("autopid", "j1939", "can_manager", "can_core"):
                print("  LOG " + ln[:220])
    finally:
        print("--- restore", flush=True)
        try:
            actor.stop()
            for e in list(vehicles().get("vehicles", [])):
                if e["key"] not in found_keys:
                    forget(e["key"])
            if found_car:
                dut.api("/api/autopid/vehicles/" + found_car + "/activate", "POST", {})
            if dut.get("/api/autopid/config") != found_cfg:
                code, r = dut.api("/api/autopid/config", "PUT", found_cfg)
                if code != 200:
                    raise Bench(f"PUT config back: {code} {r}")
            now = {n: dut.settings(n) for n in found}
            if now != found:
                dut.restart(found)
            if bench_bus.sim_get("ecu_sim") != found_sim:
                bench_bus.sim_set(bitrate=found_sim["bitrate"], id_format=found_sim["id_format"],
                                  enabled=True)
                bench_bus.sim_set(enabled=found_sim["enabled"])
            now = {n: dut.settings(n) for n in found}
            run.check("restored_settings", now == found, "" if now == found else json.dumps(now)[:300])
            sv = vehicles()
            same_store = sorted(e["key"] for e in sv.get("vehicles", [])) == \
                sorted(e["key"] for e in found_store.get("vehicles", [])) and sv.get("current") == found_car
            run.check("restored_vehicle_store", same_store, "" if same_store else json.dumps(sv)[:300])
            cfg = dut.get("/api/autopid/config")
            run.check("restored_tables", cfg == found_cfg,
                      "" if cfg == found_cfg else f"{len(cfg.get('pids', []))} pids now")
            sim_now = bench_bus.sim_get("ecu_sim")
            run.check("restored_simulator", sim_now == found_sim, "" if sim_now == found_sim else json.dumps(sim_now))
            code, f1 = dut.api("/api/faults")
            churn = isinstance(f1, dict) and \
                [x.get("code") for x in f1.get("faults", [])] == ["flash_churn"]
            if churn and faults0 == {"faults": []} and any(x in legs for x in ("x1", "x2", "x3")):
                # a stress leg rewrites the store every few seconds: the
                # device's flash-wear guard says so, as it should. The bench
                # caused it, so the bench clears it (faults stay until then).
                detail = f1["faults"][0].get("detail")
                code, _ = dut.api("/api/faults/clear", "POST", {})
                code, f1 = dut.api("/api/faults")
                run.check("no_new_fault", f1 == faults0,
                          f"flash_churn raised by the stress leg ({detail}), cleared: "
                          + json.dumps(f1)[:120])
            else:
                run.check("no_new_fault", f1 == faults0, json.dumps(f1)[:300])
            dut.reset_check(run, st0)
            dut.passing_checks(run)
        except Bench as e:
            run.check("restore", False, str(e))

    return run.verdict("legs: " + " ".join(legs) if ONLY else "")


if __name__ == "__main__":
    sys.exit(main())
