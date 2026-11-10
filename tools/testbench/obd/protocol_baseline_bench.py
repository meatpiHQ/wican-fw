#!/usr/bin/env python3
"""What today's image does on every vehicle bus ECU sim v2 can play  ->  PROTOCOL BASELINE DONE / FAIL

A measurement, not a pass/fail of the product: the firmware knows the four
ISO 15765-4 CAN protocols and nothing else, and before anything is built for
the others (J1850 PWM and VPW, ISO 9141-2, KWP2000, MS CAN, CH CAN,
single-wire CAN) this bench writes down what happens there today. The bench
FAILS only on what must hold whatever the product can do: the rig staged
right, the unit rode through, NOTHING WROTE THE OBD CHIP, everything as
found at the end. What the product did on each bus is printed as FACT lines
and as a table.

Per bus, with the simulator in a ONE-bus state (bench_bus.sim_only):
  app path   an ELM app over /ws/obd: reset, echo off, headers on, ATM0, what
             the chip hears (VT SHOW_BUS), the protocol by its preset
             (ATSPn as an app sends it, STP nn, VTP 2Cn), then 0100, 010C,
             010D, 0902, 03: answered or not, the time of the first request
             (it carries the bus's start-up) and of a later one, the replies
             as printed. Then the chip's own search (ATSP0 + 0100): does it
             find this bus, and how long does it take
  firmware   POST /api/autopid/test, 010C with the preset as the row's init
             and the standard expression, twice: ok or the error, the time,
             the bytes the expression saw, the value (the simulator's 800 rpm)
  the chip   chip_nvm's snapshot against the one taken at the start: SAME

--issue1045 (on the iso9141 leg, GitHub issue #1045): the Test button's call
as the report makes it (`010C1`, init `ATSP3;`, three times) and the
detection job. --issue1045-poll adds AutoPID polling the unit's rows with
`std_init` `ATSP3;` for 30 s. That step is NOT part of a plain run: a poll
that answers lets first contact identify the car on the K-line, and a car it
takes for a new one is detected and gets its protocol SAVED in the chip (the
product's one save per car): one write of the chip's store, and one more to
put it back. The bench's own check then says the chip was written.

The unit's own CAN node (can_manager) is switched off for the run: at
another bit rate than the bus it would cost the first frames of a leg. Put
back at the end with AutoPID's settings (planned restarts).

usage: protocol_baseline_bench.py [--dut localhost:18081] [--only iso9141,vpw,...]
                                  [--issue1045] [--issue1045-poll] [--out DIR]
  run on the PC with the DUT through an ssh tunnel (`ssh -N -L
  18081:<dut>:80 rpi001`) and ECU sim v2 at its own address (lib/sim_net.py).
About 80 s per bus (one simulator restart each), 16 min for all twelve.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)
import bench_bus  # noqa: E402  (the simulator's settings; installs sim_net)
import chip_nvm   # noqa: E402  (the chip's stored cells)

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
SIM = bench_bus.SIM_URL
ONLY = [s for s in arg("--only", "").split(",") if s]
ISSUE1045 = "--issue1045" in ARGS or "--issue1045-poll" in ARGS
ISSUE1045_POLL = "--issue1045-poll" in ARGS
OUT = arg("--out", os.path.join(HERE, "..", "..", "..", "test-reports", "logs",
                                time.strftime("%Y-%m-%d_%H%M") + "_protocol_baseline"))
RPM = 800.0                         # the simulator's engine speed, every profile of the bench
#: bus -> (words an app sends ahead of the preset, the preset, the same as a row's init, what the chip calls it)
BUSES = [
    ("hs11_500", [], "ATSP6", "ATSP6;", "ISO 15765-4 CAN 11/500"),
    ("hs29_500", [], "ATSP7", "ATSP7;", "ISO 15765-4 CAN 29/500"),
    ("hs11_250", [], "ATSP8", "ATSP8;", "ISO 15765-4 CAN 11/250"),
    ("hs29_250", [], "ATSP9", "ATSP9;", "ISO 15765-4 CAN 29/250"),
    ("ms11", [], "STP 53", "STP53;", "MS CAN 125k 11-bit (pins 3, 11)"),
    ("ch11", [], "VTP 2C3", "VTP2C3;", "CH CAN 500k 11-bit (pins 12, 13)"),
    ("sw11", [], "STP 63", "STP63;", "single-wire CAN 33.3k 11-bit (pin 1)"),
    ("iso9141", [], "ATSP3", "ATSP3;", "ISO 9141-2 (5-baud init)"),
    ("kwp5", [], "ATSP4", "ATSP4;", "ISO 14230-4 KWP (5-baud init)"),
    ("kwpfast", [], "ATSP5", "ATSP5;", "ISO 14230-4 KWP (fast init)"),
    ("vpw", [], "ATSP2", "ATSP2;", "SAE J1850 VPW"),
    ("pwm", [], "ATSP1", "ATSP1;", "SAE J1850 PWM"),
]
REQUESTS = [("0100", "41 00"), ("010C", "41 0C"), ("010D", "41 0D"), ("0902", "49 02"), ("03", "43")]
CHECKS = []
FACTS = {}
RESTARTS = [0]


def now():
    return time.strftime("%H:%M:%S")


def say(text):
    print("%s %s" % (now(), text), flush=True)


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


def fact(bus, key, value):
    FACTS.setdefault(bus, {})[key] = value
    print("%s FACT %s %s: %s" % (now(), bus, key, str(value)[:260]), flush=True)


def http(path, method="GET", body=None, timeout=15):
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
        text = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(text)
        except ValueError:
            return exc.code, text
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


# ---- the DUT -------------------------------------------------------------------

def chip():
    st, oc = http("/api/obd_chip")
    return oc if isinstance(oc, dict) else {}


def guard():
    return chip().get("eeprom_guard", {})


def history():
    st, h = http("/api/restart/history")
    return h if isinstance(h, dict) else {}


def wait_ready(limit=150):
    t0 = time.time()
    while time.time() - t0 < limit:
        st, oc = http("/api/obd_chip", timeout=4)
        if st == 200 and isinstance(oc, dict) and oc.get("ready"):
            return True
        time.sleep(1)
    return False


def settings(name):
    st, cur = http("/api/settings/" + name)
    assert st == 200 and isinstance(cur, dict), "%s settings unreachable: %s %s" % (name, st, cur)
    return {k: v for k, v in cur.items() if k not in ("degraded", "pending_reboot")}


def apply(changes):
    """{component: {key: value}} staged and submitted: one planned restart
    when something changed. True = the unit is back with the chip up."""
    changed = False
    for name, vals in changes.items():
        cur = settings(name)
        new = dict(cur)
        new.update(vals)
        if new != cur:
            st, r = http("/api/settings/" + name, "PUT", new)
            assert st == 200 and isinstance(r, dict) and "error" not in r, "%s PUT: %s %s" % (name, st, r)
            changed = True
    if not changed:
        return True
    b0 = history().get("boot_count")
    http("/api/settings/submit", "POST", {}, timeout=15)
    RESTARTS[0] += 1
    time.sleep(6)
    t0 = time.time()
    while time.time() - t0 < 150:
        if history().get("boot_count") not in (None, b0) and wait_ready(60):
            return True
        time.sleep(1)
    return False


def ring():
    st, text = http("/api/logs/ring", timeout=20)
    text = text if isinstance(text, str) else json.dumps(text)
    return [re.sub(r"\x1b\[[0-9;]*m", "", ln).strip() for ln in text.splitlines()]


def vehicles():
    st, v = http("/api/autopid/vehicles")
    return v if isinstance(v, dict) else {}


def store_view(v):
    return (v.get("current"), [(c.get("key"), c.get("name"), c.get("protocol"), c.get("chip_protocol"))
                               for c in v.get("vehicles", [])])


# ---- one bus ---------------------------------------------------------------------

def timed(c, word, wait_s):
    t0 = time.time()
    text = c.cmd(word, wait_s)
    return text, int((time.time() - t0) * 1000)


def app_path(bus, pre, preset):
    """an ELM app's session on this bus; returns True when 0100 was answered"""
    c = chip_nvm.Chip(DUT_HP)
    answered = False
    try:
        for w in ("ATZ", "ATE0", "ATL0", "ATS1", "ATH1"):
            c.cmd(w)
        m0 = c.cmd("ATM0")
        fact(bus, "app: what the chip hears (VT SHOW_BUS)", c.cmd("VT SHOW_BUS", 8.0))
        for w in pre:
            fact(bus, "app: %s" % w, c.cmd(w))
        fact(bus, "app: %s (ATM0 %s)" % (preset, m0), c.cmd(preset))
        seen = []
        for i, (req, echo) in enumerate(REQUESTS):
            text, ms = timed(c, req, 14.0 if i == 0 else 8.0)
            ok = echo in text
            seen.append("%s %s %d ms" % (req, "ok" if ok else "NO", ms))
            if i == 0:
                answered = ok
                fact(bus, "app: first 0100", "%s in %d ms: %s" % ("answered" if ok else "NOT answered", ms, text[:150]))
            elif i == 1:
                fact(bus, "app: 010C as printed (headers on)", text[:120])
            elif req == "0902":
                fact(bus, "app: 0902 as printed", text[:230])
            elif req == "03":
                fact(bus, "app: 03 as printed", text[:120])
        text, ms = timed(c, "0100", 8.0)
        fact(bus, "app: requests", "; ".join(seen) + "; 0100 again %d ms" % ms)
        fact(bus, "app: ATDPN / ATDP", "%s / %s" % (c.cmd("ATDPN"), c.cmd("ATDP")))
        # the chip's own search, from a reset, nothing stored used on purpose
        for w in ("ATPC", "ATZ", "ATE0", "ATH1"):
            c.cmd(w)
        c.cmd("ATM0")
        c.cmd("ATSP0")
        text, ms = timed(c, "0100", 20.0)
        fact(bus, "app: the chip's own search (ATSP0, 0100)", "%s in %d ms, ATDPN %s: %s" % (
            "found" if "41 00" in text else "NOT found", ms, c.cmd("ATDPN"), text[:110]))
        c.cmd("ATPC")
    finally:
        c.close()
    return answered


def test_pid(bus, init, cmd="010C", label="firmware: test-a-PID"):
    out = []
    for n in (1, 2):
        t0 = time.time()
        st, r = http("/api/autopid/test", "POST", {"cmd": cmd, "init": init, "expressions": ["[B3:B4]*0.25"]}, timeout=40)
        wall = int((time.time() - t0) * 1000)
        r = r if isinstance(r, dict) else {"error": str(r)}
        val = (r.get("values") or [None])[0]
        tr = [ln.strip() for ln in str(r.get("transcript", "")).replace("\r", "\n").split("\n") if ln.strip()]
        out.append({"http": st, "ok": r.get("ok"), "error": r.get("error"), "elapsed_ms": r.get("elapsed_ms"),
                    "wall_ms": wall, "raw": str(r.get("raw", ""))[:120], "payload": r.get("payload"),
                    "value": val, "transcript_tail": tr[-4:]})
        fact(bus, "%s, call %d (%s init %s)" % (label, n, cmd, init),
             "http %s ok %s error %r elapsed %s ms; raw %r; payload %s; value %s" % (
                 st, r.get("ok"), r.get("error"), r.get("elapsed_ms"), str(r.get("raw", ""))[:60], r.get("payload"), val))
        time.sleep(1.0)
    right = [o for o in out if isinstance(o["value"], (int, float)) and abs(o["value"] - RPM) < 1.0]
    fact(bus, "%s: decodes the simulator's %d rpm" % (label, RPM), "%d of %d calls" % (len(right), len(out)))
    return out


def issue_1045(bus, found):
    """GitHub issue #1045 on the bench's ISO 9141-2 car: the report's own calls"""
    print("-- issue #1045 on %s --" % bus, flush=True)
    for n in (1, 2, 3):
        t0 = time.time()
        st, r = http("/api/autopid/test", "POST", {"cmd": "010C1", "init": "ATSP3;"}, timeout=40)
        r = r if isinstance(r, dict) else {"error": str(r)}
        tr = [ln.strip() for ln in str(r.get("transcript", "")).replace("\r", "\n").split("\n") if ln.strip()]
        fact(bus, "#1045 Test button call %d (010C1, init ATSP3;)" % n, "ok %s error %r elapsed_ms %s wall %d ms; ends %s" % (
            r.get("ok"), r.get("error"), r.get("elapsed_ms"), int((time.time() - t0) * 1000), tr[-2:]))
        time.sleep(1)
    t0 = time.time()
    st, r = http("/api/autopid/vehicles/detect", "POST", {})
    js = {}
    while time.time() - t0 < 120:
        st2, js = http("/api/autopid/std_scan")
        if isinstance(js, dict) and js.get("status") in ("done", "failed"):
            break
        time.sleep(1)
    fact(bus, "#1045 detection job", "start http %s; %s after %d s: %s" % (
        st, js.get("status") if isinstance(js, dict) else js, int(time.time() - t0),
        js.get("error") if isinstance(js, dict) else ""))
    check("%s: #1045 the detection changed nothing in the vehicle store" % bus, store_view(vehicles()) == found["store"],
          store_view(vehicles()))
    if not ISSUE1045_POLL:
        return
    # polling as the report sets it: the standard rows behind std_init ATSP3;
    ap0 = settings("autopid")
    check("%s: #1045 AutoPID on with std_init ATSP3; (a planned restart)" % bus,
          apply({"autopid": {"enabled": True, "std_enabled": True, "std_init": "ATSP3;"}}))
    time.sleep(30)
    st, a = http("/api/autopid", timeout=20)
    stats = a.get("stats", {}) if isinstance(a, dict) else {}
    vals = [p.get("name") for p in (a.get("params", []) if isinstance(a, dict) else []) if p.get("value") is not None]
    fact(bus, "#1045 AutoPID polling 30 s (std_init ATSP3;)", "polls_ok %s polls_failed %s, %d parameters with a value; bus_guard %s" % (
        stats.get("polls_ok"), stats.get("polls_failed"), len(vals), json.dumps((a or {}).get("bus_guard", {}))[:150]))
    for pat in ("vehicle identity", "contact:", "stored protocol", "detection "):
        for ln in [ln for ln in ring() if pat in ln][-2:]:
            print("    ring: " + ln[-170:], flush=True)
    fact(bus, "#1045 the vehicle store after polling", str(store_view(vehicles())))
    check("%s: #1045 AutoPID's settings back (a planned restart)" % bus, apply({"autopid": ap0}))


def leg(bus, pre, preset, init, name, snap0, found):
    print("== %s: %s ==" % (bus, name), flush=True)
    b0 = history().get("boot_count")
    assert b0 is not None, "the unit does not answer (the tunnel?): the run stops here"
    prev, run = bench_bus.sim_only(bus, SIM)
    live = bench_bus.sim_bus_live(run)
    spec = bench_bus.SIM_BUSES[bus]
    want = "kline" if "kline" in spec else spec.get("j1850") or \
        (spec["can"] + spec["fmt"][:2] + ("_%d" % spec["kbps"] if spec["can"] == "hs" else ""))
    check("%s: the simulator answers on this bus alone (%s)" % (bus, " ".join(live) or "nothing"), live == [want], (live, want))
    check("%s: the unit rode through the simulator's restart" % bus, history().get("boot_count") == b0 and wait_ready(20),
          (b0, history().get("boot_count")))
    g0 = guard()
    answered = app_path(bus, pre, preset)
    time.sleep(11)                      # the app left: the firmware takes the chip back after 10 s
    test_pid(bus, init)
    if bus == "iso9141" and ISSUE1045:
        issue_1045(bus, found)
    snap = chip_nvm.snapshot(DUT_HP)
    changes = chip_nvm.diff(snap0, snap)
    chip_nvm.show_diff(changes)
    g1 = guard()
    check("%s: nothing wrote the chip (the snapshot of %d cells reads as at the start, the store %s)" % (
        bus, len(snap), snap.get(chip_nvm.STORED_PROTOCOL)), not changes, [c[0] for c in changes])
    fact(bus, "the firmware's guard", "rewrites +%d, blocked +%d, protocol_saves %s, memory_refused +%d, requests_held +%d" % (
        g1.get("rewrites", 0) - g0.get("rewrites", 0), g1.get("blocked", 0) - g0.get("blocked", 0), g1.get("protocol_saves"),
        g1.get("memory_refused", 0) - g0.get("memory_refused", 0), g1.get("requests_held", 0) - g0.get("requests_held", 0)))
    return answered


def table():
    rows = ["| Bus | App path: first 0100 | The chip's own search | Test-a-PID (the firmware's path) | Decodes 800 rpm |",
            "|---|---|---|---|---|"]
    for bus, _, preset, init, name in BUSES:
        f = FACTS.get(bus)
        if not f:
            continue
        t1 = next((v for k, v in f.items() if k.startswith("firmware: test-a-PID, call 1")), "")
        m = re.search(r"ok (\S+) error (.*?) elapsed (\S+) ms", t1)
        rows.append("| %s (%s) | %s | %s | %s | %s |" % (
            name, preset, f.get("app: first 0100", "").split(":")[0],
            f.get("app: the chip's own search (ATSP0, 0100)", "").split(":")[0],
            ("ok %s, error %s, %s ms" % m.groups()) if m else t1[:60],
            f.get("firmware: test-a-PID: decodes the simulator's %d rpm" % RPM, "")))
    return "\n".join(rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    st, info = http("/api/info")
    print("DUT %s" % (info.get("fw_build") if isinstance(info, dict) else info), flush=True)
    check("the chip is up", wait_ready(30))
    legs = [b for b in BUSES if not ONLY or b[0] in ONLY]
    unknown = [o for o in ONLY if o not in [b[0] for b in BUSES]]
    assert not unknown, "no bus %s (buses: %s)" % (unknown, " ".join(b[0] for b in BUSES))
    found = {"can_manager": settings("can_manager"), "autopid": settings("autopid"),
             "store": store_view(vehicles()), "h": history(),
             "sim": {g: bench_bus.sim_get(g, SIM) for g in ("ecu_sim", "kline", "j1850")},
             "tester": bench_bus.sim_tester(SIM)}
    st, cfg0 = http("/api/autopid/config", timeout=30)
    say("as found: can_manager %s; autopid enabled %s std_init %r; store %s; chip %s" % (
        json.dumps(found["can_manager"]), found["autopid"].get("enabled"), found["autopid"].get("std_init"),
        found["store"], chip().get("fw", {}).get("chip")))
    e0 = set(ln for ln in ring() if re.match(r"^E[ (]", ln))
    try:
        check("the unit's own CAN node and AutoPID off for the run (a planned restart when they were on)",
              apply({"can_manager": {"enabled": False}, "autopid": {"enabled": False}}))
        snap0 = chip_nvm.snapshot(DUT_HP)
        json.dump({"cells": snap0}, open(os.path.join(OUT, "nvm_start.json"), "w", encoding="utf-8"), indent=1)
        check("the chip's stored cells read (%d cells, the store %s)" % (len(snap0), snap0.get(chip_nvm.STORED_PROTOCOL)),
              len(snap0) >= 30 and snap0.get(chip_nvm.STORED_PROTOCOL, "") != "", chip_nvm.REREAD[0])
        for bus, pre, preset, init, name in legs:
            leg(bus, pre, preset, init, name, snap0, found)
    except AssertionError as exc:
        check("the run reached its end", False, exc)
    finally:
        print("== restore ==", flush=True)
        sim_now = {g: bench_bus.sim_get(g, SIM) for g in ("ecu_sim", "kline", "j1850")}
        back = {}
        for g in ("ecu_sim", "kline", "j1850"):
            keys = [k for k in found["sim"][g] if k in sim_now[g] and k not in ("bitrate", "id_format", "degraded", "pending_reboot")
                    and (g != "ecu_sim" or k != "enabled") and sim_now[g].get(k) != found["sim"][g].get(k)]
            if keys:
                back[g] = {k: found["sim"][g][k] for k in keys}
        if back:
            bench_bus.sim_patch(back, SIM)
        bench_bus.sim_tester(SIM, swcan=found["tester"].get("swcan", "sleep"))
        sim_end = {g: bench_bus.sim_get(g, SIM) for g in ("ecu_sim", "kline", "j1850")}
        check("the simulator's settings as found", sim_end == found["sim"],
              {g: {k: (found["sim"][g].get(k), v) for k, v in sim_end[g].items() if found["sim"][g].get(k) != v} for g in sim_end})
        check("the unit's settings as found (a planned restart when they changed)",
              apply({"can_manager": found["can_manager"], "autopid": found["autopid"]}))
        check("the settings read as found", settings("can_manager") == found["can_manager"] and
              settings("autopid") == found["autopid"])
        check("the vehicle store as found", store_view(vehicles()) == found["store"], store_view(vehicles()))
        st, cfg1 = http("/api/autopid/config", timeout=30)
        check("the live tables as found", cfg1 == cfg0)
        h1 = history()
        check("%d planned restarts, none unexpected" % RESTARTS[0],
              h1.get("unexpected_resets") == found["h"].get("unexpected_resets") and
              (h1.get("boot_count", 0) - found["h"].get("boot_count", 0)) == RESTARTS[0],
              (found["h"].get("boot_count"), h1.get("boot_count"), h1.get("unexpected_resets")))
        new_e = sorted(set(ln for ln in ring() if re.match(r"^E[ (]", ln)) - e0)
        check("no new E line in the log ring", not new_e, new_e[:3])
    md = table()
    print("\n" + md + "\n", flush=True)
    json.dump({"dut": info, "facts": FACTS, "checks": CHECKS}, open(os.path.join(OUT, "baseline.json"), "w", encoding="utf-8"), indent=1)
    open(os.path.join(OUT, "baseline.md"), "w", encoding="utf-8").write(md + "\n")
    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    print("PROTOCOL BASELINE %s (%d buses, %d checks, %d failed; facts in %s)" % (
        "DONE" if not fails else "FAIL", len(legs), len(CHECKS), len(fails), os.path.normpath(OUT)), flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
