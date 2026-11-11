#!/usr/bin/env python3
"""AutoPID rows on MS, single-wire and CH CAN beside the car's OBD protocol  ->  AUTOPID BUS PASS / FAIL

A row of the tables has a `bus` (obd, ms, sw, ch) and, for a car whose bus
does not run at the usual speed, a `bus_kbps`. The poller asks the rows of a
bus together, takes the OBD chip to MS, single-wire or CH CAN through an AT
user protocol defined in the chip's RAM, and brings it back to the car's OBD
protocol as soon as no row of another bus is due. Nothing of it writes the
chip's memory (a preset, `STP nn` / `VTP 2Cn`, would at every selection).

Against ECU sim v2 with every CAN bus live (its HS, MS, CH and single-wire
interfaces answer the same OBD requests):

  test      POST /api/autopid/test on each bus: answered with the simulator's
            800 rpm; the transcript shows the slot's definition and choice,
            no preset, no link word, no stored protocol, and behind the
            request the car's protocol and the slot's definition taken back
            (the chip is home again, as found). The refusals: an
            unknown bus, a speed without a bus, a speed the chip cannot
            make, a row of another bus that picks a protocol itself
  speed     the simulator's MS CAN at 250 and its CH CAN at 125 kbit/s: a
            test with `bus_kbps` is answered, one at the bus's usual speed
            is not
  poll      seven rows (three on the car's protocol, two on MS CAN, one on
            CH CAN, one on single-wire CAN), Automate on: every row has the
            simulator's value, every bus counts answers and no failure, the
            chip changes bus about four times a round (once to each other
            bus and home) and not once per row, and it is found on the
            car's protocol between the rounds
  guard     the simulator's HS CAN off: the rows of the car's protocol are
            not answered, the rows of the other buses still are (the bus
            guard listens on the HS pins and has no say about them)
  park      the simulator's MS CAN at 250 kbit/s while its rows ask at
            125: three requests end in CAN ERROR and the rows of that bus
            rest (30 s; the other buses go on); with the bus back at 125
            they are answered again behind the rest. There is no listener
            on these buses, so a wrong speed is found out by sending, and
            the rest keeps those tries off a live bus
  app       an ELM app over /ws/obd in the middle of it (`ATZ`, `0100`):
            answered on the car's protocol; ten seconds after its last word
            the rows of every bus are answered again
  the chip  chip_nvm's snapshot the same before and after, no protocol
            save, the stored protocol as found; with Automate off the chip
            reads the car's protocol and the link table as found

The unit's tables, its Automate settings and the simulator are put back as
found (planned restarts: Automate on, Automate back). AutoPID must be off at
the start.

usage: autopid_bus_bench.py [--dut localhost:18081] [--only test,speed,poll,guard,park,app]
  the DUT through an ssh tunnel (`ssh -N -L 18081:<dut>:80 rpi001`), ECU sim
  v2 at its own address. About 6 min.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)
import bench_bus  # noqa: E402
import chip_nvm   # noqa: E402

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
ONLY = [x for x in arg("--only", "test,speed,poll,guard,park,app").split(",") if x]
SIM_GROUPS = ("ecu_sim", "kline", "j1850")
PERIOD_MS = 2000
RPM = "[B3:B4]/4"
#: name, request (one reply asked for, so the chip does not wait out its timeout), expression, value, bus
ROWS = [
    ("bus_obd_rpm", "010C1", RPM, 800, None),
    ("bus_obd_speed", "010D1", "B3", None, None),
    ("bus_obd_temp", "01051", "B3-40", None, None),
    ("bus_ms_rpm", "010C1", RPM, 800, "ms"),
    ("bus_ms_speed", "010D1", "B3", None, "ms"),
    ("bus_ch_rpm", "010C1", RPM, 800, "ch"),
    ("bus_sw_rpm", "010C1", RPM, 800, "sw"),
]
OTHER = ("ms", "ch", "sw")
SLOT = {"ms": ("B", "MS_CAN"), "ch": ("C", "CH_CAN"), "sw": ("F", "SW_CAN,3")}
#: every CAN interface of the simulator answering 11 bit OBD
ALL_LIVE = {"enabled": True, "hs_bitrate_kbps": 500, "ms_bitrate_kbps": 125, "ch_bitrate_kbps": 500}
for _b in ("hs", "ms", "ch", "sw"):
    ALL_LIVE.update({_b + "_enabled": True, _b + "_obd11": True, _b + "_obd29": False, _b + "_uds": True})
CHECKS = []
PLANNED = [0]


def now():
    return time.strftime("%H:%M:%S")


def say(text):
    print("%s %s" % (now(), text), flush=True)


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:320] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


def http(path, method="GET", body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(DUT + path, data=data, method=method)
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
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def settings(ns):
    st, cur = http("/api/settings/" + ns)
    return {k: v for k, v in cur.items() if k not in ("degraded", "pending_reboot")} if isinstance(cur, dict) else {}


def apply(ns, wanted):
    """the keys of one settings group; a planned restart when one changed"""
    cur = settings(ns)
    if all(cur.get(k) == v for k, v in wanted.items()):
        return True
    new = dict(cur)
    new.update(wanted)
    st, r = http("/api/settings/" + ns, "PUT", new)
    if st != 200:
        return False
    st, h = http("/api/restart/history")
    b0 = h.get("boot_count") if isinstance(h, dict) else None
    http("/api/settings/submit", "POST", {})
    PLANNED[0] += 1
    time.sleep(6)
    t0 = time.time()
    while time.time() - t0 < 150:
        st, h = http("/api/restart/history", timeout=4)
        st2, oc = http("/api/obd_chip", timeout=4)
        if isinstance(h, dict) and h.get("boot_count") not in (None, b0) and isinstance(oc, dict) and oc.get("ready"):
            time.sleep(3)
            return all(settings(ns).get(k) == v for k, v in wanted.items())
        time.sleep(1)
    return False


def sim_groups():
    return {g: {k: v for k, v in bench_bus.sim_get(g).items() if k not in ("degraded", "pending_reboot")} for g in SIM_GROUPS}


def sim_set(ecu):
    """keys of the simulator's ecu_sim group (one reboot when one changes), its single-wire transceiver awake"""
    cur = bench_bus.sim_get("ecu_sim")
    want = {k: v for k, v in ecu.items() if cur.get(k) != v}
    if want:
        bench_bus.sim_patch({"ecu_sim": want})
    bench_bus.sim_tester(swcan="normal")
    time.sleep(0.5)
    return bench_bus.sim_bus_live(bench_bus.sim_status())


def sim_back(found, tester):
    cur = sim_groups()
    back = {g: {k: v for k, v in found[g].items() if cur[g].get(k) != v} for g in SIM_GROUPS}
    back = {g: ch for g, ch in back.items() if ch}
    if back:
        bench_bus.sim_patch(back)
    bench_bus.sim_tester(swcan=tester.get("swcan", "sleep"))
    return sim_groups() == found


def test_call(bus=None, kbps=None, cmd="010C1", init=None):
    body = {"cmd": cmd, "expression": RPM, "type": "custom"}
    if bus is not None:
        body["bus"] = bus
    if kbps is not None:
        body["bus_kbps"] = kbps
    if init is not None:
        body["init"] = init
    return http("/api/autopid/test", "POST", body)


def stats():
    st, a = http("/api/autopid")
    return a if isinstance(a, dict) else {}


def buses():
    return (stats().get("stats") or {}).get("buses") or {}


def values():
    return {p.get("name"): p.get("value") for p in (stats().get("params") or [])}


def tables():
    group = {"name": "default", "enabled": True, "period_ms": PERIOD_MS}
    pids = []
    for name, cmd, expr, _, bus in ROWS:
        row = {"name": name, "type": "custom", "cmd": cmd, "group": "default", "period_ms": 0, "enabled": True,
               "parameters": [{"name": name, "expression": expr, "unit": ""}]}
        if bus:
            row["bus"] = bus
        pids.append(row)
    return {"groups": [group], "pids": pids, "filters": []}


def watch(seconds):
    """the bus counters over a stretch of polling: the delta, and where the chip was found"""
    b0 = buses()
    seen = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        seen.append(buses().get("now"))
        time.sleep(0.35)
    b1 = buses()
    delta = {b: {k: (b1.get(b) or {}).get(k, 0) - (b0.get(b) or {}).get(k, 0) for k in ("ok", "failed")} for b in ("obd",) + OTHER}
    return delta, b1.get("changes", 0) - b0.get("changes", 0), seen


def leg_test():
    for bus in OTHER:
        st, r = test_call(bus)
        tr = (r.get("transcript") or "") if isinstance(r, dict) else ""
        slot, where = SLOT[bus]
        check("test: a row on %s is answered with the simulator's 800 rpm" % bus,
              st == 200 and isinstance(r, dict) and r.get("ok") and r.get("value") == 800, r if not isinstance(r, dict) else (r.get("raw"), r.get("error")))
        check("test: %s: the slot defined, then chosen (VTSET_CAN %s,.., %s; ATTP%s)" % (bus, slot, where, slot),
              ("VTSET_CAN %s,81," % slot) in tr and where in tr and tr.find("VTSET_CAN %s," % slot) < tr.find("> ATTP%s" % slot) != -1)
        check("test: %s: no preset, no link word, no stored protocol in what was sent" % bus,
              not any(w in tr for w in ("> STP", "> VTP", "> ATLNK", "> ATSP", "> VTCFG")))
        behind = tr[tr.find("> 010C1"):]
        check("test: %s: behind the request the chip is taken home (the car's protocol) and the slot's definition taken back" % bus,
              "> ATTP" in behind and behind.find("> ATTP") < behind.find("> VTSET_CAN %s\n" % slot) != -1 and buses().get("now") == "obd",
              behind.replace("\n", " | ")[:200])
    st, r = test_call(None)
    check("test: a row without a bus is asked on the car's protocol, no slot word", st == 200 and isinstance(r, dict) and r.get("ok") and
          r.get("value") == 800 and "VTSET_CAN" not in (r.get("transcript") or ""))
    st, r = test_call("obd")
    check("test: `obd` is the car's protocol too", st == 200 and isinstance(r, dict) and r.get("ok") and "VTSET_CAN" not in (r.get("transcript") or ""))
    for name, got in (("an unknown bus", test_call("hs")), ("a speed without a bus", test_call(None, 250)),
                      ("a speed the chip cannot make", test_call("ms", 300)),
                      ("a row of another bus that picks a protocol itself", test_call("ms", None, "010C1", "ATSP6;ATSH7DF")),
                      ("a row of another bus with a preset as its init", test_call("ms", None, "010C1", "STP53"))):
        check("test: %s is refused with 400" % name, got[0] == 400, got)


def leg_speed(found_live):
    live = sim_set({"ms_bitrate_kbps": 250, "ch_bitrate_kbps": 125})
    if not check("speed: the simulator's MS CAN at 250 and CH CAN at 125 kbit/s", "ms11" in live and "ch11" in live, " ".join(live)):
        return
    for bus, kbps, usual in (("ms", 250, 125), ("ch", 125, 500)):
        st, r = test_call(bus, kbps)
        tr = (r.get("transcript") or "") if isinstance(r, dict) else ""
        check("speed: %s at %d kbit/s is answered" % (bus, kbps), st == 200 and isinstance(r, dict) and r.get("ok") and r.get("value") == 800,
              r if not isinstance(r, dict) else (r.get("raw"), r.get("error")))
        check("speed: %s: the divisor of 500 kbit/s in the definition (%02X)" % (bus, 500 // kbps),
              ("VTSET_CAN %s,81,%02X," % (SLOT[bus][0], 500 // kbps)) in tr)
        st, r = test_call(bus)
        check("speed: %s at its usual %d kbit/s is not answered on that bus" % (bus, usual),
              st == 200 and isinstance(r, dict) and "41 0C" not in (r.get("raw") or "") and r.get("value") is None,
              r if not isinstance(r, dict) else (r.get("raw"), r.get("value")))
    live = sim_set({"ms_bitrate_kbps": 125, "ch_bitrate_kbps": 500})
    check("speed: the simulator's buses at their usual speeds again", all(b in live for b in found_live), " ".join(live))


def leg_poll():
    st, r = http("/api/autopid/config", "PUT", tables())
    if not check("poll: the tables with a bus per row are accepted", st == 200, r):
        return False
    if not check("poll: Automate on (a planned restart)", apply("autopid", {"enabled": True})):
        return False
    time.sleep(2 * PERIOD_MS / 1000.0 + 2)
    rounds = 6
    delta, changes, seen = watch(rounds * PERIOD_MS / 1000.0)
    vals = values()
    for name, _, _, want, bus in ROWS:
        got = vals.get(name)
        check("poll: %s has a value%s" % (name, "" if want is None else " (%s)" % want), got is not None and (want is None or got == want), got)
    for bus in ("obd",) + OTHER:
        rows = len([r for r in ROWS if (r[4] or "obd") == bus])
        check("poll: %s: every row answered in every round (%d rows)" % (bus, rows),
              delta[bus]["ok"] >= rows * (rounds - 1) and delta[bus]["failed"] == 0, delta[bus])
    per_round = changes / float(rounds)
    say("FACT %d changes of bus in %d rounds of %d rows: %.1f a round (once to each of the three other buses and home = 4)" % (
        changes, rounds, len(ROWS), per_round))
    check("poll: the chip changes bus once per bus a round, not once per row", 3.0 <= per_round <= 5.0, "%.1f" % per_round)
    home = seen.count("obd")
    say("FACT the chip was found on %s in %d looks" % (", ".join("%s %d" % (b, seen.count(b)) for b in sorted(set(seen), key=str)), len(seen)))
    check("poll: between the rounds the chip is on the car's protocol", home * 2 >= len(seen), "%d of %d" % (home, len(seen)))
    return True


def leg_guard(found_live):
    live = sim_set({"hs_enabled": False, "hs_obd11": False, "hs_uds": False, "hs_broadcast": False, "hs_j1939": False})
    if not check("guard: the simulator's HS CAN off, the other buses live", not any(b.startswith("hs") for b in live) and "ms11" in live, " ".join(live)):
        return
    time.sleep(PERIOD_MS / 1000.0 + 1)
    delta, _, _ = watch(4 * PERIOD_MS / 1000.0)
    check("guard: the rows of the car's protocol are not answered", delta["obd"]["ok"] == 0, delta["obd"])
    for bus in OTHER:
        check("guard: the rows on %s are still answered" % bus, delta[bus]["ok"] >= 2 and delta[bus]["failed"] == 0, delta[bus])
    say("FACT with the HS bus silent: paused_bus %s" % (stats().get("stats") or {}).get("paused_bus"))
    live = sim_set({k: True for k in ("hs_enabled", "hs_obd11", "hs_uds")})
    time.sleep(PERIOD_MS / 1000.0 + 6)
    delta, _, _ = watch(3 * PERIOD_MS / 1000.0)
    check("guard: with the HS bus back the rows of the car's protocol are answered again", delta["obd"]["ok"] >= 3, delta["obd"])


def leg_park():
    b0 = buses()
    live = sim_set({"ms_bitrate_kbps": 250})
    if not check("park: the simulator's MS CAN at 250 kbit/s, its rows still ask at 125", "ms11" in live, " ".join(live)):
        return
    t0 = time.time()
    parked = False
    while time.time() - t0 < 25 and not parked:
        time.sleep(1)
        parked = bool((buses().get("ms") or {}).get("parked"))
    b1 = buses()
    check("park: after three CAN ERRORs in a row the rows on MS CAN rest", parked and
          (b1.get("ms") or {}).get("parks", 0) == (b0.get("ms") or {}).get("parks", 0) + 1,
          "%.0f s, %s" % (time.time() - t0, b1.get("ms")))
    st, ring = http("/api/logs/ring", timeout=30)
    text = ring if isinstance(ring, str) else json.dumps(ring)
    check("park: the unit says why, once, as a W line", text.count("requests in a row ended in CAN ERROR") == 1,
          text.count("requests in a row ended in CAN ERROR"))
    delta, _, _ = watch(3 * PERIOD_MS / 1000.0)
    check("park: resting, the rows on MS CAN are not answered and the other buses go on",
          delta["ms"]["ok"] == 0 and all(delta[b]["ok"] >= 2 and delta[b]["failed"] == 0 for b in ("obd", "ch", "sw")), delta)
    live = sim_set({"ms_bitrate_kbps": 125})
    t1 = time.time()
    while time.time() - t1 < 45 and (buses().get("ms") or {}).get("parked"):
        time.sleep(1)
    check("park: the rest ends by itself (30 s)", not (buses().get("ms") or {}).get("parked"), "%.0f s after the park began" % (time.time() - t0))
    time.sleep(2 * PERIOD_MS / 1000.0 * 4 + 1)     # the rows had backed off: four periods
    delta, _, _ = watch(3 * PERIOD_MS / 1000.0)
    check("park: with the bus back at its speed the rows on MS CAN are answered again", delta["ms"]["ok"] >= 2 and delta["ms"]["failed"] == 0, delta["ms"])


def leg_app():
    chip = chip_nvm.Chip(DUT_HP)
    try:
        chip.cmd("ATZ", 4.0)            # the firmware sends it as a warm start: the slot definitions are gone
        chip.cmd("ATE0")
        chip.cmd("ATH1")
        proto = chip.cmd("ATDPN")
        reply = chip.cmd("0100", 8.0)
        check("app: an app's 0100 in the middle of it is answered on the car's protocol", "41 00" in reply and proto.strip("A") in ("6", "7", "8", "9"),
              "ATDPN %s, %s" % (proto, reply[:40]))
        paused = (stats().get("stats") or {}).get("paused_client")
        check("app: the poller stays off the chip while the app talks", paused is True, paused)
        chip.cmd("ATPC")
    finally:
        chip.close()
    time.sleep(12 + PERIOD_MS / 1000.0)
    delta, _, _ = watch(3 * PERIOD_MS / 1000.0)
    for bus in ("obd",) + OTHER:
        check("app: ten seconds after its last word the rows on %s are answered again" % bus,
              delta[bus]["ok"] >= 2 and delta[bus]["failed"] == 0, delta[bus])


def main():
    st, ap = http("/api/settings/autopid")
    st2, oc = http("/api/obd_chip")
    if not (isinstance(ap, dict) and isinstance(oc, dict) and oc.get("ready")):
        print("AUTOPID BUS FAIL (the unit does not answer at %s, or its chip is not ready)" % DUT_HP)
        return 1
    if not check("Automate is off at the start", not ap.get("enabled")):
        print("AUTOPID BUS FAIL (switch Automate off for the run)")
        return 1
    found_ap = settings("autopid")
    st, found_cfg = http("/api/autopid/config")
    st, h0 = http("/api/restart/history")
    g0 = oc.get("eeprom_guard", {})
    sim0 = sim_groups()
    tester0 = bench_bus.sim_tester()
    snap0 = chip_nvm.snapshot(DUT_HP)
    chip = chip_nvm.Chip(DUT_HP)
    chip.cmd("ATE0")
    links0, proto0 = chip.cmd("ATDPCLK"), chip.cmd("ATDPN")
    chip.cmd("ATPC")
    chip.close()
    say("as found: the chip %s on protocol %s, store %s; %d rows in the tables" % (
        (oc.get("fw") or {}).get("chip"), proto0, g0.get("stored_protocol"), len((found_cfg or {}).get("pids") or []) if isinstance(found_cfg, dict) else -1))
    polling = False
    try:
        live = sim_set(ALL_LIVE)
        if not check("the simulator's four CAN buses are live", all(b in live for b in ("hs11_500", "ms11", "ch11", "sw11")), " ".join(live)):
            raise SystemExit(1)
        time.sleep(9)                   # an interface of a simulator before its 2026-11-11 firmware could answer late
        if "test" in ONLY:
            leg_test()
        if "speed" in ONLY:
            leg_speed(("hs11_500", "ms11", "ch11", "sw11"))
        if any(x in ONLY for x in ("poll", "guard", "park", "app")):
            polling = leg_poll()
        if polling and "guard" in ONLY:
            leg_guard(live)
        if polling and "park" in ONLY:
            leg_park()
        if polling and "app" in ONLY:
            leg_app()
    except SystemExit:
        pass
    finally:
        check("Automate as found (a planned restart when it changed)", apply("autopid", {k: found_ap[k] for k in ("enabled",)}))
        if isinstance(found_cfg, dict):
            st, r = http("/api/autopid/config", "PUT", found_cfg)
            st, back = http("/api/autopid/config")
            check("the unit's tables as found", st == 200 and back == found_cfg)
        check("the simulator's settings as found", sim_back(sim0, tester0))

    check("Automate's settings read as found", settings("autopid") == found_ap)
    time.sleep(2)
    chip = chip_nvm.Chip(DUT_HP)
    chip.cmd("ATE0")
    links1, proto1 = chip.cmd("ATDPCLK"), chip.cmd("ATDPN")
    chip.cmd("ATPC")
    chip.close()
    check("with Automate off the chip is on the car's protocol and its link table reads as found",
          links1 == links0 and proto1 == proto0 and buses().get("now") == "obd", "ATDPN %s, %s" % (proto1, links1[-48:]))
    snap1 = chip_nvm.snapshot(DUT_HP)
    diff = chip_nvm.diff(snap0, snap1)
    check("the chip's stored cells the same before and after (%d cells)" % len(snap0), not diff, [w for w, _, _ in diff])
    st, oc1 = http("/api/obd_chip")
    g1 = oc1.get("eeprom_guard", {}) if isinstance(oc1, dict) else {}
    check("the chip's stored protocol as found, no protocol save, no refused switch",
          g1.get("stored_protocol") == g0.get("stored_protocol") and g1.get("protocol_saves") == 0 and not g1.get("memory_refused"),
          "%s -> %s, saves %s, memory_refused %s" % (g0.get("stored_protocol"), g1.get("stored_protocol"), g1.get("protocol_saves"), g1.get("memory_refused")))
    st, ring = http("/api/logs/ring", timeout=30)
    text = ring if isinstance(ring, str) else json.dumps(ring)
    e_lines = [ln.strip()[-140:] for ln in text.splitlines() if ln.lstrip("\x1b[0;31m").startswith("E (")]
    check("no E line in the unit's log", not e_lines, e_lines[:3])
    st, h1 = http("/api/restart/history")
    check("the unit rode through", isinstance(h0, dict) and isinstance(h1, dict) and
          h1.get("boot_count") == h0.get("boot_count") + PLANNED[0] and h1.get("unexpected_resets") == h0.get("unexpected_resets"),
          "boot %s -> %s with %d planned" % ((h0 or {}).get("boot_count"), (h1 or {}).get("boot_count") if isinstance(h1, dict) else h1, PLANNED[0]))
    failed = [n for n, ok in CHECKS if not ok]
    print("AUTOPID BUS %s (%d checks, %d failed, %d planned restarts)" % ("PASS" if not failed else "FAIL", len(CHECKS), len(failed), PLANNED[0]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
