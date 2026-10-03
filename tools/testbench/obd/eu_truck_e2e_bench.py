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
  e4  back to the car: the actor stops, the simulator's car is on, the
      device's settings as found; the car is the current vehicle again and
      is polled (the stored truck stays known)
  restore: settings, store, tables and the simulator as found.

Verdict: `EU TRUCK E2E PASS`. Run on the PC (IDF venv python; python-can,
can-isotp):
  python eu_truck_e2e_bench.py [host[:port]] [--pcan PCAN_USBBUS2]
        [--only e1,e2] [--logdir DIR]
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
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--pcan":
        PCAN = args[i + 1]; i += 2
    elif args[i] == "--only":
        ONLY = [x.strip() for x in args[i + 1].split(",") if x.strip()]; i += 2
    elif args[i] == "--logdir":
        LOGDIR = args[i + 1]; i += 2
    else:
        DUT_HOST = args[i]; i += 1

ALL_LEGS = ["e1", "e2", "e3", "e4"]
VIN = "1WCANEUTRUCK00001"
RPM = 1250.0                    # the WWH ECU's engine speed (--set rpm=)
TRUCK_RPM = 1800.0              # the J1939 engine's (--truck-set rpm=)
WWH_DTCS = "P0420:08:02,P2463-1F:04:04"     # confirmed, pending (format 04)
DM1 = "110-0-5"                             # the J1939 engine's active code
ACTOR_ARGS = ["--id-format", "29", "--bitrate", "250", "--ecu", "0x00", "--ecu2", "0x3D",
              "--vin", VIN, "--set", f"rpm={RPM:g}", "--dtcs", WWH_DTCS,
              "--truck", "--truck-sa", "0x00", "--truck-dtcs", DM1,
              "--truck-set", f"rpm={TRUCK_RPM:g}"]

run = Run("EU TRUCK E2E")
dut = Dut(DUT_HOST, own_tags=("autopid", "j1939", "can_manager", "can_core"),
          tasks=("autopid", "j1939", "can_core_rx"))
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
    j = dut.get("/api/j1939")
    run.check("e2_listener_up_listen_only",
              j.get("state") == "listening" and j.get("bus") == "j1939" and j.get("mode") == "listen"
              and j["can"]["listen_only"] is True and j["can"]["baud_kbps"] == 250
              and j.get("vin") == VIN,
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
              and set(rep.get("stored", [])) == {"P0420-08", "SPN110-0"}
              and rep.get("pending") == ["P2463-1F"]
              and wwh_items.get("P0420-08", {}).get("ecu", "").upper() == "18DAF100"
              and dm_items.get("SPN110-0", {}).get("sa") == 0 and dm_items["SPN110-0"].get("oc") == 5,
              json.dumps({k: rep.get(k) for k in ("protocol", "j1939", "stored", "pending",
                                                  "permanent", "error")}) + json.dumps(items)[:300])
    lamps = rep.get("lamps", {})
    run.check("e3_mil_from_both", rep.get("mil") is True and lamps.get("mil") is True,
              json.dumps({"mil": rep.get("mil"), "lamps": lamps, "sources": rep.get("sources")})[:300])
    mark = actor.mark()
    code, r = dut.api("/api/autopid/dtc/clear", "POST", {"confirm": True, "mode": "always"})
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


def leg_e4(found, found_sim, found_car):
    """Back to the car."""
    actor.stop()
    bench_bus.sim_set(bitrate=found_sim["bitrate"], id_format=found_sim["id_format"], enabled=True)
    bench_bus.sim_set(enabled=found_sim["enabled"])
    bus_kbps[0] = int(found_sim["bitrate"])
    dut.restart(found)
    t0 = time.time()
    e = wait_for("the car as the current vehicle", 90,
                 lambda: (lambda x: x if x.get("key") == found_car else None)(current_car()))
    run.metric("e4_car_back_s", round(time.time() - t0, 1), "s after the restart")
    run.check("e4_car_is_current_again", e.get("key") == found_car, e.get("key"))
    s0 = autopid().get("stats", {})
    time.sleep(6)
    s1 = autopid().get("stats", {})
    run.check("e4_car_polled", s1.get("polls_ok", 0) - s0.get("polls_ok", 0) > 5,
              f"polls_ok {s0.get('polls_ok')} -> {s1.get('polls_ok')}")
    keys = {x["key"] for x in vehicles().get("vehicles", [])}
    run.check("e4_truck_still_known", VIN in keys, str(sorted(keys))[:200])


# ---- main ------------------------------------------------------------------------

def main():
    legs = ALL_LEGS if not ONLY else [x for x in ALL_LEGS if x in ONLY]
    unknown = [x for x in ONLY if x not in ALL_LEGS]
    if unknown or not legs:
        print(f"unknown leg(s) {unknown}; legs: {' '.join(ALL_LEGS)}")
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
        actor.start("e2e", ["--pcan", PCAN] + ACTOR_ARGS)   # on the bus before the DUT boots
        # a device that knows a car: the native bus off, polling on, the chip's
        # protocol follows the car
        dut.restart({"can_manager": {"enabled": False}, "j1939": {"enabled": False},
                     "autopid": {"enabled": True, "std_protocol": "0", "pause_mode": "requests_only",
                                 "dtc_enabled": False, "dtc_allow_clear": False}})
        time.sleep(6)           # the car's rows fail, the chip probes
        for leg in legs:
            print(f"--- {leg}", flush=True)
            if leg == "e4":
                leg_e4(found, found_sim, found_car)
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
            run.check("no_new_fault", f1 == faults0, json.dumps(f1)[:300])
            st1 = dut.get("/api/status")
            run.check("no_unexpected_reset",
                      st1.get("unexpected_resets") == st0.get("unexpected_resets")
                      and st1.get("boot_count") - st0.get("boot_count") == dut.restarts,
                      f"unexpected_resets {st0.get('unexpected_resets')} -> {st1.get('unexpected_resets')}, "
                      f"boot_count +{st1.get('boot_count') - st0.get('boot_count')} for "
                      f"{dut.restarts} restarts")
            dut.passing_checks(run)
        except Bench as e:
            run.check("restore", False, str(e))

    return run.verdict("legs: " + " ".join(legs) if ONLY else "")


if __name__ == "__main__":
    sys.exit(main())
