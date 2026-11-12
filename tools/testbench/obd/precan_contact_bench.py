#!/usr/bin/env python3
"""A pre-CAN car found without being asked for, and by the detection  ->  PRECAN CONTACT PASS / FAIL

A unit plugged into a K-line or SAE J1850 car polled the protocol of the car
it knew, gave it up for the chip's own search, and never found the car: the
walk that looks for whoever is there ended behind the CAN protocols, and the
detection did not try SAE J1850 (2026-11-13, before the change: 172 failed
polls in 150 s on an ISO 9141-2 car, the K-line never started; "Detect my
vehicle" on a J1850 VPW car tried 6, 7, 5, 3, 4 and said "no ECU response").
ECU sim v2 plays one car at a time. Its ECUs all carry the VIN of the bench
unit's own car, so first contact meets "the same car on another protocol":
the store records the protocol and nothing is written to the OBD chip.

  startup   Automate on, the protocol on Automatic, three rows in the tables.
            The simulator as an ISO 9141-2 car alone, a restart: the stored
            CAN protocol stays silent, the walk goes on behind the CAN
            protocols and finds the car on protocol 3; the car's record
            names 3, the rows are answered, the K-line session is up. A
            restart on a CAN vehicle that answers nothing standard (frames on
            its CAN pins: a car that needs its profile, a truck): the walk
            asks on CAN, the simulator counts the requests, and SAE J1850 and
            the K-line are left alone. A restart with nobody there at all:
            the walk goes round with a growing gap (the driver counts the
            K-line starts that failed) and nothing else happens. The
            simulator becomes a J1850 VPW car in that boot: the next round
            finds it on protocol 2. A restart on the unit's own CAN car:
            found there again, the record as it was. No protocol save on
            the way
  detect    (--detect, off by default: three writes of the OBD chip's stored
            protocol, each the one save a detection makes by design)
            A car WiCAN has never seen: the unit's own car is forgotten, the
            simulator is a J1850 VPW car, Automate on, a restart. Nobody
            asks for a detection: the walk finds the car on protocol 2, the
            store does not know it, the detection job starts by itself and
            stores it with its VIN and its supported PIDs, and the chip
            holds 2. A restart, the simulator as an ISO 14230-4 car, "Detect
            my vehicle" by hand: the job goes on behind the CAN protocols
            and SAE J1850, finds the car on protocol 5, the VIN from five
            messages of seven data bytes, the chip holds 5. The way back:
            the unit's own CAN car, a restart, the detection again

The simulator, the unit's tables, its Automate settings and the vehicle
store are put back as found. Automate must be off at the start. Five planned
restarts (eight with --detect); no write of the OBD chip without --detect.

usage: precan_contact_bench.py [--dut localhost:18081] [--only startup] [--detect] [--can off|on]
  the DUT through an ssh tunnel (`ssh -N -L 18081:<dut>:80 rpi001`), ECU sim
  v2 at its own address (firmware v1.00-16 or later for the detect leg).
  --can: the unit's own CAN node off or on for the run, put back after (as
  found without it). The two ask "is anybody else on the CAN pins" in two
  ways: a running node acknowledges the OBD chip's requests and counts what
  it hears while the chip is quiet; with the node off a listener looks, and
  the chip's requests are never acknowledged. A unit from the factory has
  the node off.
"""
import json
import os
import re
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)
import bench_bus            # noqa: E402
import chip_nvm             # noqa: E402
import kline_accept_bench as kb     # noqa: E402  (its helpers; it reads --dut and --detect the same way)

ONLY = [x for x in kb.arg("--only", "startup").split(",") if x]
DETECT = "--detect" in kb.ARGS
CAN = kb.arg("--can", "")           # "off" / "on": the unit's own CAN node for the run (as found without it)
ROWS = kb.ROWS
PERIOD_S = kb.PERIOD_MS / 1000.0
check, say, http = kb.check, kb.say, kb.http


def restart():
    """a planned restart; true once the unit is back with its chip ready"""
    st, h = http("/api/restart/history")
    b0 = h.get("boot_count") if isinstance(h, dict) else None
    http("/api/restart", "POST", {})
    kb.PLANNED[0] += 1
    time.sleep(5)
    t0 = time.time()
    while time.time() - t0 < 150:
        st, h = http("/api/restart/history", timeout=4)
        st2, oc = http("/api/obd_chip", timeout=4)
        if isinstance(h, dict) and h.get("boot_count") not in (None, b0) and isinstance(oc, dict) and oc.get("ready"):
            return True
        time.sleep(1)
    return False


def can_node(enabled):
    """the unit's own CAN node on or off by its setting, for the next restart to apply. True when it is so, or will be"""
    cur = kb.settings("can_manager")
    if not cur or cur.get("enabled") == enabled:
        return bool(cur)
    cur["enabled"] = enabled
    st, r = http("/api/settings/can_manager", "PUT", cur)
    return st == 200


def contact_lines():
    """the unit's own words about first contact in THIS boot: [(uptime ms, the line)]. The log ring outlives a restart:
    where the uptime in front of a line falls back, a new boot begins and what was collected belongs to the one before"""
    st, ring = http("/api/logs/ring", timeout=30)
    text = ring if isinstance(ring, str) else json.dumps(ring)
    out, last = [], -1
    for ln in text.splitlines():
        t = re.search(r"[IWED] \((\d+)\) ", ln)
        if not t:
            continue
        if int(t.group(1)) < last:
            out = []
        last = int(t.group(1))
        m = re.search(r"\) autopid: (contact: a vehicle answers.*|vehicle identity.*|stored protocol.*|detection.*)", ln)
        if m:
            out.append((last, m.group(1).replace("\x1b[0m", "").strip()))
    return out


def wait_protocol(proto, timeout):
    """seconds until the current car's record names `proto`, or None"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if kb.own_car().get("protocol") == proto:
            return int(time.time() - t0)
        time.sleep(1)
    return None


def found(car, proto, session, timeout, g0):
    """the car is found on `proto` by the unit itself, its rows are answered, nothing is written to the chip"""
    secs = wait_protocol(proto, timeout)
    lines = contact_lines()
    at = next((ms for ms, ln in lines if "answers on protocol %s" % proto in ln), None)
    time.sleep(2 * PERIOD_S)
    p0 = kb.stats().get("stats") or {}
    time.sleep(3 * PERIOD_S)
    a = kb.stats()
    p1, c1, g1 = a.get("stats") or {}, kb.chip_kline(), kb.guard()
    vals = {p.get("name"): p.get("value") for p in (a.get("params") or [])}
    say("FACT %s: the record on %s after %s s (the unit's own line at %s ms of its uptime); polls ok +%d failed +%d, values %s; "
        "the driver's K-line %s; the car %s" % (car, proto, secs, at, p1.get("polls_ok", 0) - p0.get("polls_ok", 0),
                                               p1.get("polls_failed", 0) - p0.get("polls_failed", 0), vals, c1, kb.own_car()))
    check("startup: %s: found on protocol %s by the unit itself, the car's record names it" % (car, proto),
          secs is not None and at is not None, (secs, [ln for _, ln in lines][-3:]))
    check("startup: %s: the rows are answered behind it (800 rpm), round after round" % car,
          vals.get("kl_rpm") == 800 and p1.get("polls_ok", 0) - p0.get("polls_ok", 0) >= len(ROWS) * 2 and
          p1.get("polls_failed", 0) == p0.get("polls_failed", 0), (vals, p1.get("polls_ok"), p1.get("polls_failed")))
    check("startup: %s: %s" % (car, "the K-line session is up" if session else "no K-line session in the driver"),
          c1.get("up") is session, c1)
    check("startup: %s: nothing written to the OBD chip (no protocol save, its store as it was)" % car,
          g1.get("protocol_saves") == 0 and kb.digit(g1.get("stored_protocol", "")) == kb.digit(g0.get("stored_protocol", "")),
          (g1.get("protocol_saves"), g1.get("stored_protocol")))


def leg_startup(car0, g0, node_on):
    group, pids = kb.rows_table(kb.PERIOD_MS)
    st, r = http("/api/autopid/config", "PUT", {"groups": [group], "pids": pids, "filters": []})
    if not check("startup: the tables are accepted", st == 200, r):
        return
    # the first boot: the car the unit knows on a CAN protocol is an ISO 9141-2 one
    bench_bus.sim_only("iso9141")
    ok, why = kb.apply({"enabled": True, "std_protocol": "0"})
    if ok and why == "as wanted":       # the detect leg left Automate on: the boot is what first contact needs
        ok = restart()
    if not check("startup: Automate on, the protocol on Automatic (a planned restart)", ok, why):
        return
    st, can = http("/api/can")
    can = can if isinstance(can, dict) else {}
    check("startup: the unit's own CAN node is %s in this run" % ("on" if node_on else "off"),
          can.get("enabled") is node_on and can.get("running") is node_on, {k: can.get(k) for k in ("enabled", "running", "listen_only")})
    found("an ISO 9141-2 car", "3", True, 90, g0)
    # the second boot: a CAN vehicle that answers nothing standard (traffic on its CAN pins, no OBD: a car that needs its
    # profile, a truck). The walk asks on CAN and leaves the other pins alone
    bench_bus.sim_only("hs11_500")
    bench_bus.sim_patch({"ecu_sim": {"hs_obd11": False, "hs_uds": False, "hs_broadcast": True, "broadcast_enabled": True}})
    if not check("startup: a restart on a CAN vehicle that answers nothing standard", restart()):
        return
    t0 = time.time()
    while time.time() - t0 < 40 and not any("stored protocol" in ln for _, ln in contact_lines()):
        time.sleep(1)                   # the stored K-line protocol is given up first: three starts of the bus that fail
    c0, r0 = kb.chip_kline(), bench_bus.sim_rx_seq()
    time.sleep(40)
    c1, r1 = kb.chip_kline(), bench_bus.sim_rx_seq()
    say("FACT traffic on the CAN pins and no answer, 40 s behind the stored protocol's end: the simulator received +%d frames, K-line "
        "starts that failed +%d; the bus guard %s" % (r1 - r0, c1.get("failed", 0) - c0.get("failed", 0),
                                                       (kb.stats().get("bus_guard") or {}).get("reason")))
    check("startup: traffic on the CAN pins and no answer to a standard request: the walk asks on CAN and leaves SAE J1850 and the "
          "K-line alone", r1 - r0 >= 4 and c1.get("failed", 0) == c0.get("failed", 0) and c1.get("up") is False and
          kb.own_car().get("protocol") == "3", (r1 - r0, c0, c1))
    # the third boot: nobody there at all (every bus of the simulator off), then a J1850 VPW car turns up
    bench_bus.sim_patch({"ecu_sim": {"hs_enabled": False, "broadcast_enabled": False}})
    if not check("startup: a restart with nobody there", restart() and not bench_bus.sim_bus_live(bench_bus.sim_status()),
                 " ".join(bench_bus.sim_bus_live(bench_bus.sim_status()))):
        return
    time.sleep(8)
    c0, p0, t0 = kb.chip_kline(), (kb.stats().get("stats") or {}), time.time()
    time.sleep(45)
    c1, p1 = kb.chip_kline(), (kb.stats().get("stats") or {})
    failed = c1.get("failed", 0) - c0.get("failed", 0)
    say("FACT nobody there, %d s: K-line starts that failed +%d (three a round of the walk), polls ok +%d failed +%d, the record on %s; %s" % (
        int(time.time() - t0), failed, p1.get("polls_ok", 0) - p0.get("polls_ok", 0),
        p1.get("polls_failed", 0) - p0.get("polls_failed", 0), kb.own_car().get("protocol"), [ln for _, ln in contact_lines()][-2:]))
    check("startup: nobody there: the walk goes round and finds nothing, with a growing gap (one to four rounds in 45 s), the record stays",
          3 <= failed <= 12 and p1.get("polls_ok", 0) == p0.get("polls_ok", 0) and kb.own_car().get("protocol") == "3" and
          c1.get("up") is False, (failed, c1, kb.own_car().get("protocol")))
    bench_bus.sim_only("vpw")
    found("a SAE J1850 VPW car that turns up in that boot", "2", False, 120, g0)
    # the fourth boot: the unit's own car on its CAN bus again
    bench_bus.sim_only("hs11_500")
    if not check("startup: a restart on the unit's own CAN car", restart()):
        return
    found("the unit's own CAN car", car0.get("protocol"), False, 90, g0)


def leg_detect(car0, g0):
    vin = car0.get("vin", car0.get("key"))
    # a car WiCAN has never seen, a J1850 VPW one: nobody asks for a detection. The unit's own car is forgotten first
    # (the simulator's ECUs all carry its VIN), and comes back as that new car
    st, r = http("/api/autopid/vehicles/" + urllib.parse.quote(car0.get("key"), safe=""), "DELETE")
    bench_bus.sim_only("vpw")
    ok, why = kb.apply({"enabled": True, "std_protocol": "0"})
    if not check("detect: the unit's own car forgotten, Automate on with the protocol on Automatic (a planned restart)",
                 st in (200, 204) and ok, (st, str(r)[:80], why)):
        return
    t0, car, js = time.time(), {}, {}
    while time.time() - t0 < 120:
        car = kb.own_car()
        st, js = http("/api/autopid/std_scan", timeout=6)
        js = js if isinstance(js, dict) else {}
        if car.get("protocol") == "2" and js.get("status") != "running" and kb.guard().get("protocol_saves") == 1:
            break
        time.sleep(1)
    gb, lines = kb.guard(), contact_lines()
    say("FACT a car never seen, J1850 VPW, at start-up: after %d s the store holds %s; the job %s (%s PIDs); the chip's store %s -> %s, "
        "saves %s; %s" % (int(time.time() - t0), {k: car.get(k) for k in ("key", "name", "protocol", "chip_protocol", "ecus")},
                          js.get("status"), js.get("found"), g0.get("stored_protocol"), gb.get("stored_protocol"),
                          gb.get("protocol_saves"), [ln for _, ln in lines][-3:]))
    check("detect: a car never seen, a J1850 VPW one: found at start-up and stored by a detection nobody asked for (protocol 2, its "
          "VIN, its supported PIDs)", car.get("vin") == vin and car.get("protocol") == "2" and js.get("status") == "done" and
          (js.get("found") or 0) > 0 and any("answers on protocol 2" in ln for _, ln in lines), (car.get("vin"), car.get("protocol"), js))
    check("detect: a car never seen: the chip holds 2 after ONE save", kb.digit(gb.get("stored_protocol", "")) == "2" and
          gb.get("protocol_saves") == 1, (gb.get("stored_protocol"), gb.get("protocol_saves")))
    for bus, proto, name in (("kwpfast", "5", "ISO 14230-4, fast init"), ("hs11_500", car0.get("protocol"), "the unit's own CAN car")):
        bench_bus.sim_only(bus)
        if not check("detect: %s: a restart (one save of the chip's protocol a boot)" % name, restart()):
            return
        ga, ka = kb.guard(), kb.kline()
        st, js, tried, secs = kb.detect_run()
        gb, car, kc = kb.guard(), kb.own_car(), kb.kline()
        st2, res = http("/api/autopid/std_scan/result", timeout=20)
        res = res if isinstance(res, dict) else {}
        say("FACT detection, %s: start http %s, %s after %d s, tried %s, error %r; result protocol %s, vin %s, %s PIDs; the chip's "
            "store %s -> %s, saves %s; the simulator's K-line inits slow +%d fast +%d" % (
                name, st, js.get("status"), secs, "".join(tried), js.get("error"), res.get("protocol_detected"), res.get("vin"),
                js.get("found"), ga.get("stored_protocol"), gb.get("stored_protocol"), gb.get("protocol_saves"),
                kc.get("inits_slow", 0) - ka.get("inits_slow", 0), kc.get("inits_fast", 0) - ka.get("inits_fast", 0)))
        check("detect: %s: the detection finds the car on protocol %s, with its VIN and its supported PIDs" % (name, proto),
              js.get("status") == "done" and not js.get("error") and res.get("protocol_detected") == proto and
              res.get("vin") == vin and (js.get("found") or 0) > 0, (js.get("status"), js.get("error"), res.get("protocol_detected"), res.get("vin")))
        check("detect: %s: the car's record names %s and the chip holds it after ONE save" % (name, proto),
              car.get("protocol") == proto and kb.digit(gb.get("stored_protocol", "")) == proto and gb.get("protocol_saves") == 1,
              (car.get("protocol"), gb.get("stored_protocol"), gb.get("protocol_saves")))
    check("detect: the chip's store reads as found (%s)" % g0.get("stored_protocol"),
          kb.digit(kb.guard().get("stored_protocol", "")) == kb.digit(g0.get("stored_protocol", "")), kb.guard().get("stored_protocol"))


def main():
    ap = kb.settings()
    st2, oc = http("/api/obd_chip")
    if not (ap and isinstance(oc, dict) and oc.get("ready")):
        print("PRECAN CONTACT FAIL (the unit does not answer at %s, or its chip is not ready)" % kb.DUT_HP)
        return 1
    if not check("Automate is off at the start", not ap.get("enabled")):
        print("PRECAN CONTACT FAIL (switch Automate off for the run)")
        return 1
    st, found_cfg = http("/api/autopid/config")
    found_store, car0, can0 = kb.vehicles(), kb.own_car(), kb.settings("can_manager")
    st, h0 = http("/api/restart/history")
    g0 = oc.get("eeprom_guard", {})
    sim0 = kb.sim_groups()
    snap0 = chip_nvm.snapshot(kb.DUT_HP)
    say("as found: the chip %s, store %s; %d rows in the tables; the vehicle store %s, the car's ECU set %s; the unit's own CAN "
        "node %s" % ((oc.get("fw") or {}).get("chip"), g0.get("stored_protocol"),
                     len((found_cfg or {}).get("pids") or []) if isinstance(found_cfg, dict) else -1, kb.store_view(found_store),
                     car0.get("ecus"), "on" if can0.get("enabled") else "off"))
    try:
        if not check("the unit's own car is a CAN one with a VIN (the simulator's)", car0.get("protocol") in ("6", "7", "8", "9") and
                     bool(car0.get("vin")), car0):
            raise SystemExit(1)
        if CAN in ("on", "off") and not check("the unit's own CAN node %s for this run (with the first restart)" % CAN,
                                              can_node(CAN == "on")):
            raise SystemExit(1)
        if DETECT:
            leg_detect(car0, g0)
        if "startup" in ONLY:
            leg_startup(car0, g0, (CAN == "on") if CAN in ("on", "off") else bool(can0.get("enabled")))
    except SystemExit:
        pass
    finally:
        # the car's record back on its CAN protocol when a leg was cut short: the unit's own car alone, Automate on, a restart
        if kb.own_car().get("protocol") != car0.get("protocol"):
            bench_bus.sim_only("hs11_500")
            kb.apply({"enabled": True, "std_protocol": "0"})
            if kb.own_car().get("protocol") != car0.get("protocol"):
                restart()
            wait_protocol(car0.get("protocol"), 90)
        can_node(bool(can0.get("enabled")))
        ok, why = kb.apply({k: ap[k] for k in ("enabled", "std_protocol", "std_init")})
        if ok and kb.settings("can_manager") != can0:       # nothing of Automate's changed: the CAN setting is still owed
            http("/api/settings/submit", "POST", {})
            kb.PLANNED[0] += 1
            time.sleep(25)
        check("Automate's settings and the unit's CAN node as found (a planned restart when one changed)",
              ok and kb.settings("can_manager") == can0, (why, kb.settings("can_manager").get("enabled")))
        v = kb.vehicles()
        for c in v.get("vehicles", []):
            if c.get("key") not in [x.get("key") for x in found_store.get("vehicles", [])]:
                http("/api/autopid/vehicles/" + urllib.parse.quote(c.get("key"), safe=""), "DELETE")
        if isinstance(found_cfg, dict):
            http("/api/autopid/config", "PUT", found_cfg)
            st, back = http("/api/autopid/config")
            check("the unit's tables as found", st == 200 and back == found_cfg)
        check("the vehicle store as found (the car, its name, its protocol)", kb.store_view(kb.vehicles()) == kb.store_view(found_store),
              kb.store_view(kb.vehicles()))
        check("the simulator's settings as found", kb.sim_back(sim0))

    check("Automate's settings read as found", kb.settings() == ap)
    time.sleep(12)                      # the firmware takes the chip back ten seconds behind the last word
    snap1 = chip_nvm.snapshot(kb.DUT_HP)
    diff = chip_nvm.diff(snap0, snap1)
    st, oc1 = http("/api/obd_chip")
    g1 = oc1.get("eeprom_guard", {}) if isinstance(oc1, dict) else {}
    if DETECT:
        check("the chip's stored cells the same before and after (%d cells): the stored protocol went to 2, to 5 and came back" % len(snap0),
              not diff, [w for w, _, _ in diff])
    else:
        check("the chip's stored cells the same before and after (%d cells), no protocol save" % len(snap0),
              not diff and g1.get("protocol_saves") == 0, ([w for w, _, _ in diff], g1.get("protocol_saves")))
    st, ring = http("/api/logs/ring", timeout=30)
    text = ring if isinstance(ring, str) else json.dumps(ring)
    e_lines = [ln.strip()[-140:] for ln in text.splitlines() if ln.lstrip("\x1b[0;31m").startswith("E (")]
    check("no E line in the unit's log", not e_lines, e_lines[:3])
    st, h1 = http("/api/restart/history")
    check("the unit rode through", isinstance(h0, dict) and isinstance(h1, dict) and
          h1.get("boot_count") == h0.get("boot_count") + kb.PLANNED[0] and h1.get("unexpected_resets") == h0.get("unexpected_resets"),
          "boot %s -> %s with %d planned" % ((h0 or {}).get("boot_count"), (h1 or {}).get("boot_count") if isinstance(h1, dict) else h1,
                                             kb.PLANNED[0]))
    failed = [n for n, ok in kb.CHECKS if not ok]
    print("PRECAN CONTACT %s (%d checks, %d failed, %d planned restarts)" % ("PASS" if not failed else "FAIL", len(kb.CHECKS), len(failed),
                                                                              kb.PLANNED[0]))
    for n in failed:
        print("  red: " + n)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
