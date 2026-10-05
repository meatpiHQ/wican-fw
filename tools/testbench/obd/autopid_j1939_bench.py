#!/usr/bin/env python3
"""AutoPID on a J1939 vehicle (TASK_j1939_wwh.md phase 5): the gate
`AUTOPID J1939 PASS`.

The claim under test: a truck that speaks SAE J1939 (and no OBD) is found by
the detection job, stored as a vehicle of dialect `j1939`, polled on PGN rows
read from the J1939 listener's store (never through the OBD chip), its
values reach /api/autopid and the broker, they keep updating while an ELM
app has the chip, and its active trouble codes (DM1) land in the DTC report
with the lamps. Nothing is transmitted on the J1939 network.

Rig: the DUT behind rpi001 (HTTP via the ssh tunnel, default
localhost:8081, the obd0 ELM bridge on localhost:35001), the PCAN truck
actor (actors/pcan_j1939_truck.py) at 250 kbit/s, the ECU simulator as the
ACK source with its own ECU off (a listen-only DUT does not acknowledge),
the Pi's broker (mosquitto_sub over ssh). Every DUT change is restored.

Legs:
  a1  detection with the native bus OFF (a new device): the job samples the
      bus, says `dialect j1939`, `j1939_listening false`, `bus_kbps 250`,
      stores the truck by the fingerprint of its controllers with the rows
      of the groups heard (the UI stages the listener from this result)
  a2  the listener on (one restart, as the UI would do): first contact
      finds the stored truck by itself (no detection job), learns its VIN
      from the broadcast; every stored row publishes the truck's value, the
      poller's chip counters stay at zero, the chip's UART sent nothing
      after the first probe, `rx_diag` (the chip's frames on the J1939 bus)
      stays put once the vehicle is known
  a3  a value change on the truck reaches /api/autopid within a few seconds
      and the retained ~/autopid topic on the Pi's broker carries the names
  a4  an ELM app on the chip (hinted 010C loop over the bridge): the poller
      reports paused_client while the PGN rows keep publishing
  a5  pause_mode: voltage pause under `all` stops the PGN rows too (checked
      on the status flags only when the bench PSU can move the voltage;
      otherwise the setting round trip alone)
  a6  the DTC report: the truck's DM1 (three codes, MIL + amber lamp) as
      `protocol j1939`, lamps, SPN-FMI items with source and count; a clear
      is refused with a sentence
  a7  Test-a-PID of a PGN row decodes from the store (transcript names the
      source), a custom pinned row (PGN:F004@0x11) reads the twin's value
  a8  the truck falls silent: nothing new is published, the values keep
      their last reading and grow old, nothing is invented

Fails on an unexpected reset, a new fault, an E line of the tags autopid /
j1939 / can_manager, a task under 512 B of headroom, a heap floor under
20 KB. By hand:
  python autopid_j1939_bench.py [localhost:8081] [--elm localhost:35001]
         [--pcan PCAN_USBBUS2] [--only a1,a2] [--logdir DIR]
"""
import json
import os
import socket
import subprocess
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
ELM_HOST = "localhost:35001"
PCAN = "PCAN_USBBUS2"
ONLY = []
LOGDIR = tempfile.gettempdir()
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--pcan":
        PCAN = args[i + 1]; i += 2
    elif args[i] == "--elm":
        ELM_HOST = args[i + 1]; i += 2
    elif args[i] == "--only":
        ONLY = [x.strip() for x in args[i + 1].split(",") if x.strip()]; i += 2
    elif args[i] == "--logdir":
        LOGDIR = args[i + 1]; i += 2
    else:
        DUT_HOST = args[i]; i += 1

ALL_LEGS = ["a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8"]
VIN = "1WCANJ1939TRUCK01"
TWIN = 0x11
THREE_ARG = "110-0-5,3226-4-1,520192-31-126"
THREE = {"SPN110-0": (0, 5), "SPN3226-4": (0, 1), "SPN520192-31": (0, 126)}
# the truck's key: the fingerprint of its controllers {0, TWIN} as the DUT
# prints it (read from the first detection); the VIN joins it once heard
truck_key = [""]

run = Run("AUTOPID J1939")
legs_run = []
dut = Dut(DUT_HOST, own_tags=("autopid", "j1939", "can_manager", "can_core"),
          tasks=("autopid", "j1939", "can_core_rx"))
truck = Actor(os.path.join(HERE, "..", "actors", "pcan_j1939_truck.py"),
              LOGDIR, "autopid_j1939_truck", "TRUCK READY", "TRUCK DONE ")
bus_kbps = [0]


# ---- the rig ---------------------------------------------------------------------

def sim_ack_only(kbps):
    """The simulator as the ACK source at `kbps`, its own ECU off (its
    controller keeps the bitrate it LAST ran at: on at that bitrate first)."""
    if bus_kbps[0] == kbps:
        return
    bench_bus.sim_set(bitrate=kbps, enabled=True)
    bench_bus.sim_set(enabled=False)
    bus_kbps[0] = kbps


def start_truck(tag, extra=()):
    truck.start(tag, ["--pcan", PCAN, "--bitrate", "250", "--vin", VIN,
                      "--twin", hex(TWIN), "--on-request-period", "2",
                      "--dtcs", THREE_ARG, "--lamps", "mil=1,awl=1"]
                + list(extra))


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


def stats(doc=None):
    return (doc or autopid()).get("stats", {})


def vehicles():
    code, v = dut.api("/api/autopid/vehicles")
    return v if code == 200 and isinstance(v, dict) else {"vehicles": []}


def current_car():
    v = vehicles()
    return next((e for e in v.get("vehicles", []) if e.get("key") == v.get("current")), {})


def forget(key):
    return dut.api("/api/autopid/vehicles/" + key, "DELETE")[0]


def truck_entry():
    """The stored truck: the entry under the detection's key, else the one
    carrying its VIN or a controller set that starts at source 0."""
    for e in vehicles().get("vehicles", []):
        if e.get("key") == truck_key[0] and truck_key[0]:
            return e
    for e in vehicles().get("vehicles", []):
        if e.get("vin") == VIN or (e.get("dialect") == "j1939"
                                   and str(e.get("ecus", "")).startswith("0:")):
            return e
    return {}


def wait_detection(secs):
    end = time.time() + secs
    st = {}
    while time.time() < end:
        st = dut.get("/api/autopid/std_scan")
        if st.get("status") != "running":
            return st
        time.sleep(1)
    return st


def chip_tx():
    code, d = dut.api("/api/obd_chip")
    return d.get("uart", {}).get("tx_bytes") \
        if code == 200 and isinstance(d, dict) else None


def close_enough(got, want, step):
    return isinstance(got, (int, float)) and abs(got - want) <= step / 2 + 1e-6


def table_names():
    """The parameter names of the PGN rows in the live tables (the rows the
    detection stored for this truck: a 1 s sample misses the on-request
    groups, the listener's store has them)."""
    code, cfg = dut.api("/api/autopid/config")
    names = set()
    for pd in (cfg.get("pids", []) if isinstance(cfg, dict) else []):
        if str(pd.get("cmd", "")).upper().startswith("PGN:"):
            names.update(pr.get("name") for pr in pd.get("parameters", []))
    return names


def all_truck_values(doc, values=None, names=None):
    """(ok, wrong): every stored row carries the truck's value."""
    p = params(doc)
    names = names if names is not None else table_names()
    wrong = {}
    for _pgn, name, want, step in J.expected(values):
        if name not in names:
            continue
        got = p.get(name, {}).get("value")
        if not close_enough(got, want, step):
            wrong[name] = (got, want)
    if not names:
        wrong["(no PGN rows in the tables)"] = (None, None)
    return not wrong, wrong


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


class ElmApp:
    """A Car Scanner style client on the obd0 bridge: ATZ, then hinted 010C
    requests in a loop, from a thread-less generator the leg steps."""

    def __init__(self, hostport):
        host, port = hostport.rsplit(":", 1)
        self.s = socket.create_connection((host, int(port)), timeout=5)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sent = 0
        self.answers = 0

    def cmd(self, c, timeout=3.0):
        self.s.sendall((c + "\r").encode())
        self.sent += 1
        self.s.settimeout(timeout)
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            try:
                chunk = self.s.recv(256)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if b">" in buf:
                break
        text = buf.decode(errors="replace")
        if text.strip(" >\r\n"):
            self.answers += 1
        return text

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


# ---- legs ------------------------------------------------------------------------

def leg_a1():
    """Detection with the native bus off: the sample path. The truck has
    been on since before the DUT's boot (a device powered by the ignition
    meets a live bus; a 500 kbit/s car's rows polled on a silent bus would
    collide with a truck waking up under them, which drove the PCAN actor
    bus-off on the bench)."""
    code, r = dut.api("/api/autopid/vehicles/detect", "POST", {})
    run.check("a1_detection_started", code in (200, 202), f"{code} {r}")
    st = wait_detection(60)
    run.check("a1_detection_done", st.get("status") == "done", json.dumps(st))
    if st.get("status") != "done":
        for ln in dut.ring_lines()[-300:]:
            if " D (" in ln or ") " not in ln:
                continue
            tag = ln.split(") ", 1)[1].split(":", 1)[0].strip()
            if tag in ("autopid", "j1939", "can_manager", "can_core"):
                print("  LOG " + ln[:220])
    code, r = dut.api("/api/autopid/std_scan/result")
    r = r if isinstance(r, dict) else {}
    run.check("a1_result_is_a_j1939_vehicle_without_the_listener",
              r.get("dialect") == "j1939" and r.get("j1939") is True
              and r.get("j1939_listening") is False and r.get("bus_kbps") == 250
              and r.get("protocol_detected") == "",
              json.dumps({k: r.get(k) for k in ("dialect", "j1939", "j1939_listening",
                                                "bus_kbps", "protocol_detected", "vin",
                                                "fingerprint", "found", "key")}))
    rows = r.get("supported", [])
    cmds = {x.get("cmd") for x in rows}
    broadcast = {f"PGN:{p:X}" for p, _n, _v, _s in J.expected()
                 if p not in (J.PGN_VD, J.PGN_HOURS, J.PGN_LFC)}
    run.check("a1_rows_of_the_broadcast_groups",
              broadcast <= cmds and all(
                  x.get("parameters") and x["parameters"][0].get("expression")
                  for x in rows) and len(rows) == r.get("found"),
              f"{sorted(cmds)[:6]}... {len(rows)} rows of {len(cmds)} groups, found {r.get('found')}")
    run.metric("a1_rows_from_the_sample", len(rows))
    e = current_car()
    truck_key[0] = str(r.get("key") or "")
    run.check("a1_stored_by_its_controllers",
              bool(truck_key[0]) and truck_key[0].startswith("fp:")
              and e.get("key") == truck_key[0] and e.get("dialect") == "j1939"
              and e.get("j1939") is True and e.get("protocol") == ""
              and e.get("vin") == "" and r.get("known") is False,
              json.dumps({k: e.get(k) for k in ("key", "dialect", "j1939", "protocol",
                                                "vin", "ecus", "std_supported")}))
    ecus = e.get("ecus", "")
    run.check("a1_controllers_are_the_responder_set",
              ecus.startswith("0:") and ",11:" in ecus.upper(), ecus)
    code, cfg = dut.api("/api/autopid/config")
    pgn_rows = [p for p in (cfg.get("pids", []) if isinstance(cfg, dict) else [])
                if str(p.get("cmd", "")).upper().startswith("PGN:")]
    run.check("a1_tables_hold_the_pgn_rows", len(pgn_rows) == len(rows)
              and all(p.get("type") == "std" for p in pgn_rows),
              f"{len(pgn_rows)} of {len(rows)}")
    truck.stop()


def leg_a2():
    """The listener on: first contact by itself, every row live, the chip
    untouched."""
    start_truck("a2")
    t_restart = dut.restart({"can_manager": {"enabled": True, "baud": "250",
                                             "silent": True},
                             "j1939": {"enabled": True}})
    code, j = dut.api("/api/j1939")
    run.check("a2_listener_up", code == 200 and j.get("state") == "listening",
              json.dumps(j)[:120])
    tx0 = chip_tx()
    scan_ts0 = dut.get("/api/autopid/std_scan").get("ts")
    # first contact: the stored truck becomes current without a detection job
    # (a2 alone: the first contact finds an unknown truck and the detection
    # job stores it; either way the VIN is learned from the broadcast)
    e = wait_for("the truck current with its VIN", 60,
                 lambda: (lambda c: c if c.get("dialect") == "j1939" and c.get("vin") == VIN else None)(current_car()))
    run.metric("a2_vin_learned_s", round(time.time() - t_restart, 1))
    run.check("a2_first_contact_learned_the_vin_by_itself",
              e.get("vin") == VIN and e.get("dialect") == "j1939"
              and (not truck_key[0] or e.get("key") == truck_key[0]),
              json.dumps({k: e.get(k) for k in ("key", "vin", "dialect", "j1939")}))
    if not truck_key[0]:
        truck_key[0] = str(e.get("key") or "")
    st = dut.get("/api/autopid/std_scan")
    if "a1" in legs_run:
        run.check("a2_no_detection_job_for_a_known_truck", st.get("status") != "running"
                  and st.get("ts") == scan_ts0, json.dumps(st))
    names = table_names()
    run.metric("a2_rows_in_the_tables", len(names))
    doc = wait_for("every row with the truck's value", 30,
                   lambda: (lambda d: d if all_truck_values(d, names=names)[0] else None)(autopid()))
    run.metric("a2_all_values_s", round(time.time() - t_restart, 1))
    ok, wrong = all_truck_values(doc, names=names)
    run.check("a2_every_row_carries_the_trucks_value", ok, json.dumps(wrong)[:300])
    s = stats(doc)
    run.check("a2_the_chip_polled_nothing",
              s.get("polls_ok") == 0 and s.get("polls_failed") == 0
              and s.get("passive_ok", 0) > 0 and s.get("passive_published", 0) >= len(names),
              json.dumps({k: s.get(k) for k in ("polls_ok", "polls_failed", "passive_ok",
                                                "passive_failed", "passive_published",
                                                "j1939_listening")}))
    run.check("a2_running_not_paused", s.get("running") is True
              and not s.get("paused_bus") and not s.get("paused_client"), json.dumps(s)[:200])
    # the chip: nothing on the bus for the J1939 vehicle after contact
    time.sleep(12)
    tx1 = chip_tx()
    code, j = dut.api("/api/j1939")
    diag0 = j.get("stats", {}).get("rx_diag")
    time.sleep(12)
    code, j = dut.api("/api/j1939")
    diag1 = j.get("stats", {}).get("rx_diag")
    run.metric("a2_chip_tx_bytes_in_12_s", (tx1 - tx0) if None not in (tx0, tx1) else -1)
    run.check("a2_no_chip_frame_on_the_bus_once_known", diag0 == diag1,
              f"rx_diag {diag0} -> {diag1}")
    can = dut.get("/api/can")
    run.check("a2_dut_transmitted_nothing", can.get("tx") == 0, f"tx {can.get('tx')}")
    run.check("a2_j1939_listening_flag", s.get("j1939_listening") is True)


def leg_a3():
    """A change on the truck reaches the API and the broker."""
    truck.stop()
    start_truck("a3", ["--set", "rpm=2200,speed_kmh=45"])
    t0 = time.time()
    doc = wait_for("the new rpm", 20,
                   lambda: (lambda d: d if close_enough(params(d).get("EngineSpeed", {}).get("value"), 2200, 0.125) else None)(autopid()))
    run.metric("a3_change_seen_s", round(time.time() - t0, 1))
    p = params(doc)
    run.check("a3_values_follow_the_truck",
              close_enough(p.get("EngineSpeed", {}).get("value"), 2200, 0.125)
              and close_enough(p.get("WheelBasedVehicleSpeed", {}).get("value"), 45, 1 / 256),
              json.dumps({k: p.get(k, {}).get("value") for k in ("EngineSpeed", "WheelBasedVehicleSpeed")}))
    info = dut.get("/api/info")
    dev = str(info.get("device_id", ""))
    snap = {}
    end = time.time() + 20
    while time.time() < end:
        snap = broker_autopid(dev)
        if close_enough(snap.get("EngineSpeed"), 2200, 0.125):
            break
        time.sleep(2)
    run.check("a3_broker_carries_the_truck", close_enough(snap.get("EngineSpeed"), 2200, 0.125)
              and "EngineCoolantTemperature" in snap,
              json.dumps({k: snap.get(k) for k in ("EngineSpeed", "EngineCoolantTemperature", "timestamp")}))
    truck.stop()
    start_truck("a3b")
    wait_for("the default rpm back", 20,
             lambda: close_enough(params().get("EngineSpeed", {}).get("value"), 1500, 0.125))


def leg_a4():
    """An ELM app holds the chip: the PGN rows keep publishing."""
    s0 = stats()
    app = ElmApp(ELM_HOST)
    try:
        app.cmd("ATZ", 4.0)
        app.cmd("ATE0")
        app.cmd("ATSP9")        # 29-bit 250 kbit/s: the bus's bitrate (a
                                # 500 kbit/s request would wreck the truck's
                                # frames and is not what a truck app does)
        paused = None
        pub = []
        end = time.time() + 15
        while time.time() < end:
            app.cmd("010C 1", 2.0)
            s = stats()
            pub.append(s.get("passive_published", 0))
            if s.get("paused_client"):
                paused = s
            time.sleep(0.3)
    finally:
        app.close()
    run.check("a4_app_was_answered_at_all", app.answers >= app.sent // 2,
              f"{app.answers} of {app.sent}")
    run.check("a4_poller_yielded_the_chip_to_the_app", paused is not None
              and paused.get("paused_client") is True, json.dumps(paused)[:200] if paused else "never paused")
    grew = pub and pub[-1] - (s0.get("passive_published") or 0)
    run.metric("a4_pgn_publications_during_the_app", grew or 0)
    run.check("a4_pgn_rows_kept_publishing_while_paused", bool(grew) and grew >= 20,
              f"{grew} in 15 s")
    ok, wrong = all_truck_values(autopid())
    run.check("a4_values_still_the_trucks", ok, json.dumps(wrong)[:200])
    # the resume: within the yield window + a little
    s = wait_for("the client pause to clear", 25, lambda: (lambda x: x if not x.get("paused_client") else None)(stats()))
    run.check("a4_resumed_after_the_app", s.get("paused_client") is False)


def leg_a5():
    """pause_mode: the knob round-trips; the poller's two gates are read
    from the status (a voltage pause needs the PSU: the matrix bench has
    that leg, here the setting alone)."""
    before = dut.settings("autopid")
    run.check("a5_pause_mode_as_found_requests_only",
              before.get("pause_mode") == "requests_only", before.get("pause_mode"))
    dut.restart({"autopid": {"pause_mode": "all"}})
    now = dut.settings("autopid")
    run.check("a5_pause_mode_all_accepted", now.get("pause_mode") == "all", now.get("pause_mode"))
    doc = wait_for("rows live again after the restart", 40,
                   lambda: (lambda d: d if all_truck_values(d)[0] else None)(autopid()))
    s = stats(doc)
    run.check("a5_without_a_voltage_pause_the_rows_run_under_all",
              s.get("paused_voltage") is False and s.get("passive_published", 0) > 0,
              json.dumps({k: s.get(k) for k in ("paused_voltage", "passive_published", "running")}))
    dut.restart({"autopid": {"pause_mode": "requests_only"}})
    wait_for("rows live again", 40, lambda: all_truck_values(autopid())[0])


def leg_a6():
    """The DTC report: the truck's DM1."""
    dut.restart({"autopid": {"dtc_enabled": True, "dtc_allow_clear": True}})
    wait_for("rows live again", 40, lambda: all_truck_values(autopid())[0])
    code, d = dut.api("/api/autopid/dtc")
    run.check("a6_path_is_j1939", code == 200 and d.get("path") == "j1939", json.dumps(d)[:200])
    code, r = dut.api("/api/autopid/dtc/scan", "POST", {})
    run.check("a6_scan_started", code in (200, 202), f"{code} {r}")
    d = wait_for("the scan to finish", 30,
                 lambda: (lambda x: x if x.get("scanning") is False and x.get("report", {}).get("valid") else None)(dut.get("/api/autopid/dtc")))
    rep = d.get("report", {})
    items = {it.get("code"): it for it in rep.get("items", [])}
    run.check("a6_report_is_the_dm1",
              rep.get("protocol") == "j1939" and rep.get("j1939") is True
              and set(rep.get("stored", [])) == set(THREE) and rep.get("mil") is True,
              json.dumps({k: rep.get(k) for k in ("protocol", "j1939", "stored", "mil", "mil_count", "error")}))
    lamps = rep.get("lamps", {})
    run.check("a6_lamps", lamps.get("mil") is True and lamps.get("awl") is True
              and lamps.get("rsl") is False and lamps.get("pl") is False, json.dumps(lamps))
    run.check("a6_items_name_source_and_count",
              all(c in items and items[c].get("sa") == sa and items[c].get("oc") == oc
                  for c, (sa, oc) in THREE.items()),
              json.dumps(list(items.values()))[:300])
    srcs = {s.get("sa"): s for s in rep.get("sources", [])}
    run.check("a6_source_0_with_its_lamps", 0 in srcs and srcs[0].get("count") == 3
              and srcs[0].get("lamps", {}).get("awl") is True, json.dumps(srcs)[:200])
    code, r = dut.api("/api/autopid/dtc/clear", "POST",
                      {"confirm": True, "mode": "always"})
    # listen mode cannot clear (DM11 / DM3 are requests): 403 naming the
    # setting that would (phase 6: "clearing needs mode active")
    run.check("a6_clear_refused_on_a_listener", code == 403
              and "mode active" in json.dumps(r), f"{code} {r}")
    dut.restart({"autopid": {"dtc_enabled": False, "dtc_allow_clear": False}})
    wait_for("rows live again", 40, lambda: all_truck_values(autopid())[0])


def leg_a7():
    """Test-a-PID through the store; a pinned custom row."""
    code, r = dut.api("/api/autopid/test", "POST",
                      {"cmd": "PGN:F004", "type": "std",
                       "expressions": ["(B3+B4*256)*0.125", "B2-125"]})
    run.check("a7_test_decodes_from_the_store",
              code == 200 and r.get("ok") is True and r.get("values")
              and close_enough(r["values"][0], 1500, 0.125) and close_enough(r["values"][1], 40, 1)
              and "store" in str(r.get("transcript", "")) and "from source 0" in str(r.get("transcript", "")),
              json.dumps(r)[:300])
    code, r = dut.api("/api/autopid/test", "POST",
                      {"cmd": "PGN:F004@0x11", "type": "custom",
                       "expressions": ["(B3+B4*256)*0.125"]})
    run.check("a7_test_of_a_pinned_source_reads_the_twin",
              code == 200 and r.get("ok") is True and r.get("values")
              and close_enough(r["values"][0], 2500, 0.125), json.dumps(r)[:200])
    code, r = dut.api("/api/autopid/test", "POST",
                      {"cmd": "PGN:FEF2@0x11", "type": "custom", "expressions": ["B0"]})
    run.check("a7_test_of_a_group_nobody_sends_says_so",
              code == 200 and r.get("ok") is False and "store" in str(r.get("error", "")),
              json.dumps(r)[:200])
    # a pinned custom row joins the table and publishes the twin's value
    code, cfg = dut.api("/api/autopid/config")
    cfg["pids"].append({"name": "TwinEngineSpeed", "type": "custom", "cmd": "PGN:F004@0x11",
                        "group": "default", "period_ms": 500,
                        "parameters": [{"name": "TwinEngineSpeed", "expression": "(B3+B4*256)*0.125",
                                        "unit": "rpm", "min": 0, "max": 8031.875}]})
    code, r = dut.api("/api/autopid/config", "PUT", cfg)
    run.check("a7_pinned_row_accepted", code == 200, f"{code} {r}")
    v = wait_for("the twin's value", 15,
                 lambda: (lambda x: x if close_enough(x, 2500, 0.125) else None)(params().get("TwinEngineSpeed", {}).get("value")))
    run.check("a7_pinned_row_publishes_the_twin", close_enough(v, 2500, 0.125), str(v))
    cfg["pids"] = [p for p in cfg["pids"] if p.get("name") != "TwinEngineSpeed"]
    code, r = dut.api("/api/autopid/config", "PUT", cfg)
    # a refused row: init on a PGN row
    bad = json.loads(json.dumps(cfg))
    bad["pids"].append({"name": "Bad", "type": "custom", "cmd": "PGN:F004", "init": "ATSH7E0",
                        "group": "default", "parameters": [{"name": "Bad", "expression": "B0"}]})
    code, r = dut.api("/api/autopid/config", "PUT", bad)
    run.check("a7_init_on_a_pgn_row_refused", code == 400 and "init" in json.dumps(r), f"{code} {r}")
    code, cfg2 = dut.api("/api/autopid/config")
    run.check("a7_tables_unchanged_by_the_refusal", cfg2 == cfg)


def leg_a8():
    """The truck falls silent: the store keeps the last messages (ages grow),
    the rows publish nothing new and keep their last reading."""
    truck.stop()
    time.sleep(3)               # the last frames in flight land
    d0 = autopid()
    s0, p0 = stats(d0), params(d0)
    time.sleep(30)
    d1 = autopid()
    s1, p1 = stats(d1), params(d1)
    pub = s1.get("passive_published", 0) - s0.get("passive_published", 0)
    run.check("a8_nothing_new_published", pub == 0, f"{pub} publications in 30 s of silence")
    run.check("a8_rows_still_looked_at", s1.get("passive_ok", 0) > s0.get("passive_ok", 0),
              f"passive_ok {s0.get('passive_ok')} -> {s1.get('passive_ok')}")
    same = all(p1.get(n, {}).get("value") == p0.get(n, {}).get("value")
               and p1.get(n, {}).get("ts_us") == p0.get(n, {}).get("ts_us") for n in p0)
    run.check("a8_values_keep_their_last_reading_and_time", same)
    now_us = s1.get("now_us", 0)
    ages = [(now_us - p1[n]["ts_us"]) / 1000 for n in p1 if isinstance(p1[n].get("ts_us"), (int, float))]
    run.check("a8_values_are_old_now", bool(ages) and min(ages) >= 30000,
              f"min age {round(min(ages)) if ages else None} ms")


def main():
    legs = ALL_LEGS if not ONLY else [x for x in ALL_LEGS if x in ONLY]
    unknown = [x for x in ONLY if x not in ALL_LEGS]
    if unknown or not legs:
        print(f"unknown leg(s) {unknown}; legs: {' '.join(ALL_LEGS)}")
        return 2

    # ---- as found -----------------------------------------------------------------
    st0 = dut.get("/api/status")
    found = {n: dut.settings(n) for n in ("can_manager", "j1939", "autopid")}
    found_sim = bench_bus.sim_get("ecu_sim")
    # a truck left behind by an aborted run: its VIN, or a J1939 car whose
    # controller set starts at source 0 (the bench's truck)
    for e in list(vehicles().get("vehicles", [])):
        if e.get("vin") == VIN or (e.get("dialect") == "j1939"
                                   and str(e.get("ecus", "")).startswith("0:")):
            print(f"forgetting a truck left behind: {e.get('key')}")
            forget(e["key"])
    found_store = vehicles()
    found_keys = {e["key"] for e in found_store.get("vehicles", [])}
    found_car = found_store.get("current")
    found_cfg = dut.get("/api/autopid/config")
    code, faults0 = dut.api("/api/faults")
    print(f"DUT {DUT_HOST}: boot_count {st0.get('boot_count')}, unexpected_resets "
          f"{st0.get('unexpected_resets')}, version {st0.get('version')}")
    print(f"as found: {json.dumps({n: {k: found[n].get(k) for k in ('enabled', 'baud', 'silent', 'pause_mode', 'dtc_enabled') if k in found[n]} for n in found})}")
    print(f"as found: simulator {json.dumps(found_sim)}, current car {found_car}")
    dut.as_found()

    done = 0
    try:
        sim_ack_only(250)
        # the truck runs before the DUT boots: a live bus at the first look
        start_truck("pre")
        if legs[0] == "a1":
            # a new device: the native bus off, polling on, the chip's protocol search
            dut.restart({"can_manager": {"enabled": False}, "j1939": {"enabled": False},
                         "autopid": {"enabled": True, "std_protocol": "0", "pause_mode": "requests_only",
                                     "dtc_enabled": False, "dtc_allow_clear": False}})
            time.sleep(8)       # the chip's probes of a new vehicle go first
        else:
            dut.restart({"can_manager": {"enabled": True, "baud": "250", "silent": True},
                         "j1939": {"enabled": True},
                         "autopid": {"enabled": True, "std_protocol": "0", "pause_mode": "requests_only",
                                     "dtc_enabled": False, "dtc_allow_clear": False}})
        for leg in legs:
            print(f"--- {leg}", flush=True)
            legs_run.append(leg)
            globals()["leg_" + leg]()
            dut.sweep()
            done += 1
            print(f"PROGRESS {done}/{len(legs)}", flush=True)
    except Bench as e:
        run.check("bench_ran_to_the_end", False, str(e))
        # the device's own words of the moment, for the diagnosis
        for ln in dut.ring_lines()[-400:]:
            if " D (" in ln or ": " not in ln:
                continue
            tag = ln.split(") ", 1)[1].split(":", 1)[0].strip() if ") " in ln else ""
            if tag in ("autopid", "j1939", "can_manager", "battery_monitor"):
                print("  LOG " + ln[:220])
    finally:
        # ---- restore, as found ------------------------------------------------------
        print("--- restore", flush=True)
        try:
            truck.stop()
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
            run.check("restored_tables", cfg == found_cfg, "" if cfg == found_cfg else f"{len(cfg.get('pids', []))} pids now")
            sim_now = bench_bus.sim_get("ecu_sim")
            run.check("restored_simulator", sim_now == found_sim, "" if sim_now == found_sim else json.dumps(sim_now))
            code, f1 = dut.api("/api/faults")
            run.check("no_new_fault", f1 == faults0, json.dumps(f1)[:300])
            dut.reset_check(run, st0)
            dut.passing_checks(run)
        except Bench as e:
            run.check("restore", False, str(e))

    return run.verdict("legs: " + " ".join(legs) if ONLY else "")


if __name__ == "__main__":
    sys.exit(main())
