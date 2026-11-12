#!/usr/bin/env python3
"""A K-line car on the bench: the acceptance points of GitHub issue #1045  ->  KLINE ACCEPT PASS / FAIL

The report (a 2000 Toyota 4Runner and a 2005 Porsche Cayman S, both ISO
9141-2): the chip talks to the car through an ELM app, and the firmware's
own paths do not. The Test button fails, the detection finds no ECU, and a
poll that restarts the bus before every request never gets an answer in time.
ECU sim v2 plays that car (its K-line ECU alone, 5 baud init, every CAN bus
off). The legs are the points the report's fix has to meet, and the same
Test call on the four other pre-CAN protocols:

  test      the report's own call, three times (`010C1`, init `ATSP3;`), on a
            unit whose own car is a CAN one: the call that starts the bus is
            answered (it gets the bus's start-up time, about 3 s), the two
            behind it are answered in under 1.5 s (the bus is not started
            again), the simulator counts ONE slow init for the three, and the
            reply (`48 6B 10 41 0C ..`, a three byte header and a checksum)
            decodes to the simulator's 800 rpm. The init's `ATSP3` gets the
            protocol's own request header behind it (`ATSH686AF1`): the chip
            keeps the CAN one across the switch, and under `7DF` the bus
            starts and every request reads NO DATA. A call behind 6.5 s of
            silence is answered too: the ECU had ended the session, the
            driver closes the protocol and the bus is started again
  others    the same Test call on the other four pre-CAN protocols, the
            simulator as each car in turn: ISO 14230-4 with the fast and the
            5 baud init (`ATSP5;`, `ATSP4;`), SAE J1850 VPW and PWM (`ATSP2;`,
            `ATSP1;`). Each answers twice with 800 rpm from its own frame,
            with its own request header behind the init's word; a K-line one
            starts its bus once for the two calls. Then what these protocols
            split over messages of seven data bytes, asked through the same
            route: five trouble codes (two messages of three) and the VIN
            (five messages), each read whole. Automate is still off
            here: a poller on another protocol would take the chip back to
            its own between two calls
  pin       `std_protocol` takes `3` (it is the car's protocol, as 6 to 9
            are for CAN)
  poll      with it pinned and Automate on: three rows are answered round
            after round, the simulator counts one slow init for all of it
            (no restart of the bus between two requests), and a Test call in
            the middle is answered and does not end the session. Then the
            car's trouble codes, asked while the rows are polled: five stored
            and four pending ones are all read (these protocols carry three
            codes a message, so that is two messages each; until 2026-11-12
            the first message stood alone and a car with more than three
            codes showed three), seven stored in three messages, the freeze
            frame, and the clear with an empty list behind it; the scans do
            not start the bus again. Then the
            ignition off and on (the simulator's K-line ECU gone for twelve
            seconds, then back): the rows are answered again by a new start
            of the bus, with nobody's help. Then a ten
            second period: the poller's one short request keeps the session
            (the ECU ends it after five seconds without one). Then the same
            rows with `std_protocol` 2 and the simulator as a J1850 VPW car
            (the detection does not try J1850 yet: it is pinned by hand):
            answered, with no session and no keep-alive, and the same five
            and seven trouble codes read there
  detect    (--detect, off by default: it costs two writes of the OBD chip's
            stored protocol, both the one save a detection makes by design)
            "Detect my vehicle" with the simulator as the K-line car: no CAN
            protocol answers, the job walks on to the K-line and finds the
            car on protocol 3, with its VIN and its supported PIDs, and the
            chip's store goes to 3. The simulator's K-line ECU carries the
            VIN of the bench unit's own car, so that car's record moves to
            protocol 3. Then what a person does next: polling on with the
            protocol left on Automatic, which follows the car's record: the
            rows are answered on protocol 3 and the bus is started once.
            The way back is the same road: the simulator as found, Automate
            off (a planned restart: one save per boot), the detection
            again, and the record and the chip's store read 6 as before

The simulator, the unit's tables, its Automate settings and the vehicle
store are put back as found. Automate must be off at the start. Three
planned restarts (five with --detect); no write of the OBD chip without
--detect.

usage: kline_accept_bench.py [--dut localhost:18081] [--only test,others,pin,poll] [--detect]
  the DUT through an ssh tunnel (`ssh -N -L 18081:<dut>:80 rpi001`), ECU sim
  v2 at its own address.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
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
ONLY = [x for x in arg("--only", "test,others,pin,poll").split(",") if x]
DETECT = "--detect" in ARGS
SIM_GROUPS = ("ecu_sim", "kline", "j1850")
RPM = "[B3:B4]/4"
PERIOD_MS = 2000
ROWS = [("kl_rpm", "010C1", RPM, 800), ("kl_speed", "010D1", "B3", None), ("kl_temp", "01051", "B3-40", None)]
# the request header the firmware owes each pre-CAN protocol behind an init that names it
HEADER = {"1": "ATSH616AF1", "2": "ATSH686AF1", "3": "ATSH686AF1", "4": "ATSHC133F1", "5": "ATSHC133F1"}
# the simulator as each of the other pre-CAN cars: its bus, the protocol, how the reply starts
OTHERS = [("kwpfast", "5", "F1 10 41 0C", "ISO 14230-4, fast init"), ("kwp5", "4", "F1 10 41 0C", "ISO 14230-4, 5 baud init"),
          ("vpw", "2", "48 6B 10 41 0C", "SAE J1850 VPW"), ("pwm", "1", "41 6B 10 41 0C", "SAE J1850 PWM")]
# the car's trouble codes for the scan: more than one message of three holds
FIVE = ["P0300", "P0420", "P0171", "P0101", "P0442"]
SEVEN = FIVE + ["C0035", "U0100"]
PENDING = ["P0172", "P0301", "P0302", "P0303"]
DTC_KEYS = ("dtc_enabled", "dtc_allow_clear")
CHECKS = []
PLANNED = [0]


def now():
    return time.strftime("%H:%M:%S")


def say(text):
    print("%s %s" % (now(), text), flush=True)


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


def http(path, method="GET", body=None, timeout=40):
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


def settings(ns="autopid"):
    st, cur = http("/api/settings/" + ns)
    return {k: v for k, v in cur.items() if k not in ("degraded", "pending_reboot")} if isinstance(cur, dict) else {}


def apply(wanted):
    """keys of the autopid settings; a planned restart when one changed. Returns (ok, what the unit said)"""
    cur = settings()
    if all(cur.get(k) == v for k, v in wanted.items()):
        return True, "as wanted"
    new = dict(cur)
    new.update(wanted)
    st, r = http("/api/settings/autopid", "PUT", new)
    if st != 200:
        return False, "%s %s" % (st, str(r)[:160])
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
            return all(settings().get(k) == v for k, v in wanted.items()), "restarted"
        time.sleep(1)
    return False, "the unit did not come back"


def sim_groups():
    return {g: {k: v for k, v in bench_bus.sim_get(g).items() if k not in ("degraded", "pending_reboot")} for g in SIM_GROUPS}


def sim_back(found):
    cur = sim_groups()
    back = {g: {k: v for k, v in found[g].items() if cur[g].get(k) != v} for g in SIM_GROUPS}
    back = {g: ch for g, ch in back.items() if ch}
    if back:
        bench_bus.sim_patch(back)
    return sim_groups() == found


def kline():
    return bench_bus.sim_status().get("kline") or {}


def vehicles():
    st, v = http("/api/autopid/vehicles")
    return v if isinstance(v, dict) else {}


def store_view(v):
    return (v.get("current"), sorted((c.get("key"), c.get("name"), c.get("protocol")) for c in v.get("vehicles", [])))


def stats():
    st, a = http("/api/autopid")
    return a if isinstance(a, dict) else {}


def test_call(proto="3"):
    t0 = time.time()
    st, r = http("/api/autopid/test", "POST", {"cmd": "010C1", "init": "ATSP%s;" % proto, "expressions": [RPM]})
    r = r if isinstance(r, dict) else {"error": str(r)}
    val = (r.get("values") or [r.get("value")])[0]
    lines = [ln.strip() for ln in str(r.get("transcript", "")).replace("\r", "\n").split("\n") if ln.strip()]
    sent = [ln.lstrip("> ").strip() for ln in lines if ln.startswith(">")]
    return {"http": st, "ok": r.get("ok"), "error": r.get("error"), "ms": r.get("elapsed_ms"), "wall": int((time.time() - t0) * 1000),
            "raw": str(r.get("raw", ""))[:60], "value": val, "tail": lines[-2:], "sent": sent}


def answered(c):
    return c["http"] == 200 and isinstance(c["value"], (int, float)) and abs(c["value"] - 800) < 1


def test_raw(cmd, proto):
    """a request through the Test route: the chip's message lines, and the bytes of the payload the firmware made of them"""
    st, r = http("/api/autopid/test", "POST", {"cmd": cmd, "init": "ATSP%s;" % proto, "expressions": ["B0"]})
    r = r if isinstance(r, dict) else {}
    lines = [ln.strip() for ln in str(r.get("raw", "")).replace("\r", "\n").split("\n") if ln.strip() and "BUS INIT" not in ln]
    b = [int(x, 16) for x in str(r.get("payload") or "").split()]
    if b and b[0] >> 4 == 1:            # the frames a CAN ECU would have sent: a first frame and its rows
        data = b[2:8]
        for i in range(8, len(b), 8):
            data += b[i + 1:i + 8]
        return lines, data[:((b[0] & 0x0F) << 8) | b[1]]
    return lines, b[1:1 + (b[0] & 0x0F)] if b else []


def dtc_bytes(code):
    return [("PCBU".index(code[0]) << 6) | (int(code[1]) << 4) | int(code[2], 16), int(code[3:5], 16)]


def chip_kline():
    """the driver's view of the K-line session (GET /api/obd_chip `kline`)"""
    st, oc = http("/api/obd_chip")
    return (oc.get("kline") or {}) if isinstance(oc, dict) else {}


def leg_test():
    k0 = kline()
    c0 = chip_kline()
    calls = []
    for _ in range(3):
        calls.append(test_call())
        time.sleep(1)
    k1 = kline()
    for i, c in enumerate(calls):
        say("FACT test call %d: http %s ok %s error %r elapsed %s ms (wall %d ms), raw %r, value %s, ends %s" % (
            i + 1, c["http"], c["ok"], c["error"], c["ms"], c["wall"], c["raw"], c["value"], c["tail"]))
    check("test: the call that starts the K-line bus is answered with the simulator's 800 rpm", answered(calls[0]),
          (calls[0]["error"], calls[0]["raw"], calls[0]["value"]))
    check("test: the two calls behind it are answered, each in under 1.5 s",
          all(answered(c) and isinstance(c["ms"], (int, float)) and c["ms"] < 1500 for c in calls[1:]), [(c["error"], c["ms"]) for c in calls[1:]])
    check("test: the reply is a K-line one (48 6B 10 41 0C ..) and decodes from its three byte header",
          all("48 6B 10 41 0C" in c["raw"] for c in calls) and all(answered(c) for c in calls), [c["raw"] for c in calls])
    # the unit's own car is a CAN one: its prelude's 7DF would stay in the chip across the init's switch
    s0 = calls[0]["sent"]
    check("test: the init's protocol word gets ISO 9141-2's own request header behind it (ATTP3, then ATSH686AF1, then the request)",
          "ATTP3" in s0 and HEADER["3"] in s0 and s0.index("ATTP3") < s0.index(HEADER["3"]) < len(s0) - 1 and s0[-1] == "010C1", s0)
    inits = k1.get("inits_slow", 0) - k0.get("inits_slow", 0)
    check("test: the simulator counts one slow init for the three calls (the bus is started once)", inits == 1,
          "inits_slow +%d, messages in +%d" % (inits, k1.get("messages_in", 0) - k0.get("messages_in", 0)))
    check("test: the call that started the bus took the bus's start time, the two behind it did not",
          isinstance(calls[0]["ms"], (int, float)) and 2000 < calls[0]["ms"] < 5000, [c["ms"] for c in calls])
    c1 = chip_kline()
    check("test: the driver knows the session (up, one start, nothing closed)", c1.get("up") is True and
          c1.get("starts", 0) - c0.get("starts", 0) == 1 and c1.get("closed", 0) == c0.get("closed", 0), c1)
    # the ECU ends a session after five seconds without a request, and the chip then answers NO DATA to
    # everything without starting the bus again: the driver closes the protocol first, so this call starts it
    time.sleep(6.5)
    late = test_call()
    k2 = kline()
    c2 = chip_kline()
    say("FACT test call behind 6.5 s of silence: ok %s error %r elapsed %s ms, raw %r, value %s" % (
        late["ok"], late["error"], late["ms"], late["raw"], late["value"]))
    check("test: a call behind 6.5 s of silence is answered: the session had ended, the bus is started again",
          answered(late) and k2.get("inits_slow", 0) - k1.get("inits_slow", 0) == 1 and
          c2.get("closed", 0) - c1.get("closed", 0) == 1, (late["error"], late["raw"], c2))


def leg_others():
    vin_want = (lambda c: c.get("vin", c.get("key")))(own_car())    # the simulator's ECUs carry the VIN of the unit's own car
    for bus, proto, shape, name in OTHERS:
        prev, run = bench_bus.sim_only(bus)
        time.sleep(3)
        k0 = chip_kline()
        calls = [test_call(proto)]
        time.sleep(1)
        calls.append(test_call(proto))
        k1 = chip_kline()
        say("FACT %s, the simulator as %s: %s" % (name, " ".join(bench_bus.sim_bus_live(run)), "; ".join(
            "raw %r value %s in %s ms%s" % (c["raw"], c["value"], c["ms"], "" if c["error"] is None else " error %r" % c["error"]) for c in calls)))
        check("others: %s (ATSP%s) answers the Test call twice with 800 rpm from its own frame (%s ..)" % (name, proto, shape),
              all(answered(c) and shape in c["raw"] for c in calls), [(c["error"], c["raw"], c["value"]) for c in calls])
        check("others: %s: the protocol's header behind the init's word in the first call (%s), the second call inside 1.5 s" % (
            name, HEADER[proto]), HEADER[proto] in calls[0]["sent"] and isinstance(calls[1]["ms"], (int, float)) and calls[1]["ms"] < 1500,
            (calls[0]["sent"], calls[1]["ms"]))
        if proto in ("4", "5"):
            check("others: %s: the driver counts one start of the bus for the two calls" % name,
                  k1.get("starts", 0) - k0.get("starts", 0) == 1 and k1.get("up") is True, k1)
        else:
            check("others: %s: no K-line session in the driver (the bus is not a started one)" % name, k1.get("up") is False, k1)
        # what these protocols split over messages of seven data bytes: the trouble codes (three a message) and the VIN
        bench_bus.sim_dtcs(stored=FIVE, pending=[], mil=True)
        code_lines, codes = test_raw("03", proto)
        vin_lines, vin = test_raw("0902", proto)
        say("FACT %s: 03 -> %s; 0902 -> %s" % (name, " | ".join(code_lines), " | ".join(vin_lines)))
        check("others: %s: five trouble codes in two messages and the VIN in five, each read whole through the Test call" % name,
              len(code_lines) == 2 and codes == [0x43, len(FIVE)] + [b for c in FIVE for b in dtc_bytes(c)] and
              len(vin_lines) == 5 and vin[:3] == [0x49, 0x02, 0x01] and bytes(vin[3:]).decode("ascii", "replace") == vin_want,
              (len(code_lines), codes, len(vin_lines), bytes(vin[3:]).decode("ascii", "replace")))
    prev, run = bench_bus.sim_only("iso9141")           # the K-line car again for the legs behind
    check("others: the simulator is the ISO 9141-2 car again", bench_bus.sim_bus_live(run) == ["kline"], " ".join(bench_bus.sim_bus_live(run)))
    time.sleep(3)


def leg_pin():
    cur = settings()
    new = dict(cur)
    new["std_protocol"] = "3"
    st, r = http("/api/settings/autopid", "PUT", new)
    check("pin: `std_protocol` takes 3 (ISO 9141-2)", st == 200, (st, str(r)[:140]))
    if st == 200:
        http("/api/settings/autopid", "PUT", cur)      # not submitted: nothing restarts, nothing is kept


def dtc_scan():
    """a trouble code scan on request: its report (`fresh` false when the scan left the report before it standing)"""
    st, g = http("/api/autopid/dtc")
    ts0 = ((g.get("report") or {}).get("ts")) if isinstance(g, dict) else None
    st, r = http("/api/autopid/dtc/scan", "POST", {})
    if st not in (200, 202):
        return {"error": "the scan was refused: %s %s" % (st, str(r)[:120])}
    t0 = time.time()
    while time.time() - t0 < 60:
        time.sleep(1)
        st, g = http("/api/autopid/dtc")
        if isinstance(g, dict) and not g.get("scanning"):
            rep = dict(g.get("report") or {})
            rep["fresh"] = rep.get("ts") != ts0
            return rep
    return {"error": "the scan did not end"}


def poll_codes(car, clear):
    """inside the poll, the rows being asked: the car's trouble codes, more of them than one message of three holds"""
    found = bench_bus.sim_dtcs()
    k0, c0 = kline(), chip_kline()
    bench_bus.sim_dtcs(stored=FIVE, pending=PENDING, mil=True)
    r5 = dtc_scan()
    bench_bus.sim_dtcs(stored=SEVEN, pending=[], mil=True)
    r7 = dtc_scan()
    frz = r5.get("freeze") or {}
    say("FACT %s, five stored and four pending: valid %s error %r, stored %s, pending %s, lamp %s, count %s, from %s; freeze frame of %s with %d values" % (
        car, r5.get("valid"), r5.get("error"), r5.get("stored"), r5.get("pending"), r5.get("mil"), r5.get("mil_count"),
        [s.get("ecu") for s in r5.get("sources") or []], frz.get("dtc"), len(frz.get("params") or {})))
    say("FACT %s, seven stored: stored %s, new %s, pending %s, count %s" % (car, r7.get("stored"), r7.get("new"), r7.get("pending"), r7.get("mil_count")))
    check("codes: %s: five stored codes are all read (two messages of three), and the four pending ones, the lamp on, the count 5" % car,
          r5.get("fresh") and r5.get("valid") and sorted(r5.get("stored") or []) == sorted(FIVE) and
          sorted(r5.get("pending") or []) == sorted(PENDING) and r5.get("mil") is True and r5.get("mil_count") == 5,
          (r5.get("error"), r5.get("stored"), r5.get("pending"), r5.get("mil_count")))
    check("codes: %s: the freeze frame is read with it (its code one of the stored ones, its values decoded)" % car,
          frz.get("dtc") in FIVE and len(frz.get("params") or {}) >= 3, (frz.get("dtc"), sorted(frz.get("params") or {})))
    check("codes: %s: seven stored codes in three messages, a chassis and a network code among them, the two that came named as new" % car,
          r7.get("fresh") and sorted(r7.get("stored") or []) == sorted(SEVEN) and sorted(r7.get("new") or []) == ["C0035", "U0100"] and
          not r7.get("pending") and r7.get("mil_count") == 7, (r7.get("error"), r7.get("stored"), r7.get("new")))
    if clear:
        st, r = http("/api/autopid/dtc/clear", "POST", {"confirm": True, "mode": "always"})
        left = bench_bus.sim_dtcs()
        r0 = dtc_scan()
        say("FACT %s, the clear: %s %s; the simulator holds %s; the scan behind it: stored %s, lamp %s" % (
            car, st, str(r)[:160], left, r0.get("stored"), r0.get("mil")))
        check("codes: %s: the clear empties the car's list (service 04), and the scan behind it reads no code and the lamp off" % car,
              st == 200 and isinstance(r, dict) and r.get("cleared") is True and not (left.get("dtcs") or {}).get("stored") and
              r0.get("fresh") and r0.get("valid") and not r0.get("stored") and r0.get("mil") is False, (st, r, r0.get("stored")))
    k1, c1, p1 = kline(), chip_kline(), (stats().get("stats") or {})
    time.sleep(3 * PERIOD_MS / 1000.0)
    p2 = stats().get("stats") or {}
    check("codes: %s: the scans did not start the bus again, and the rows are answered behind them" % car,
          k1.get("inits_slow", 0) == k0.get("inits_slow", 0) and c1.get("starts", 0) == c0.get("starts", 0) and
          c1.get("closed", 0) == c0.get("closed", 0) and p2.get("polls_ok", 0) - p1.get("polls_ok", 0) >= len(ROWS) * 2 and
          p2.get("polls_failed", 0) == p1.get("polls_failed", 0),
          (c1, "polls ok +%d failed +%d" % (p2.get("polls_ok", 0) - p1.get("polls_ok", 0), p2.get("polls_failed", 0) - p1.get("polls_failed", 0))))
    codes = found.get("dtcs") or {}
    bench_bus.sim_dtcs(stored=codes.get("stored", []), pending=codes.get("pending", []), permanent=codes.get("permanent", []),
                       mil=found.get("mil_on", False))


def leg_poll():
    group, pids = rows_table(PERIOD_MS)
    st, r = http("/api/autopid/config", "PUT", {"groups": [group], "pids": pids, "filters": []})
    if not check("poll: the tables are accepted", st == 200, r):
        return
    ok, why = apply({"enabled": True, "std_protocol": "3", "dtc_enabled": True, "dtc_allow_clear": True})
    if not check("poll: Automate on with the protocol pinned to 3 (a planned restart)", ok, why):
        return
    time.sleep(8)
    k0 = kline()
    p0 = (stats().get("stats") or {})
    rounds = 8
    time.sleep(rounds * PERIOD_MS / 1000.0 / 2)
    mid = test_call()
    time.sleep(rounds * PERIOD_MS / 1000.0 / 2)
    k1 = kline()
    a = stats()
    p1 = a.get("stats") or {}
    vals = {p.get("name"): p.get("value") for p in (a.get("params") or [])}
    for name, _, _, want in ROWS:
        check("poll: %s has a value%s" % (name, "" if want is None else " (%s)" % want),
              vals.get(name) is not None and (want is None or vals.get(name) == want), vals.get(name))
    ok_d, fail_d = p1.get("polls_ok", 0) - p0.get("polls_ok", 0), p1.get("polls_failed", 0) - p0.get("polls_failed", 0)
    check("poll: the rows are answered round after round (%d rounds of %d rows)" % (rounds, len(ROWS)),
          ok_d >= len(ROWS) * (rounds - 2) and fail_d == 0, "polls ok +%d, failed +%d" % (ok_d, fail_d))
    inits = k1.get("inits_slow", 0) - k0.get("inits_slow", 0)
    check("poll: the bus is not started again between two requests (no slow init while polling)", inits == 0 and
          k1.get("messages_in", 0) - k0.get("messages_in", 0) >= len(ROWS) * (rounds - 2),
          "inits_slow +%d, messages in +%d" % (inits, k1.get("messages_in", 0) - k0.get("messages_in", 0)))
    check("poll: a Test call in the middle is answered and does not end the session", answered(mid) and inits == 0,
          (mid["error"], mid["raw"], mid["tail"]))
    poll_codes("ISO 9141-2", True)
    # the ignition off and on: the K-line ECU is gone for a while and comes back without the session. The chip
    # prints NO DATA from then on and starts nothing by itself; the driver closes a session that has not answered
    # for 4.5 s, the next request then starts the bus (and fails while the car is off, and goes through once it is on)
    c_a = chip_kline()
    bench_bus.sim_only("hs11_500")                      # no ECU on pin 7
    time.sleep(12)
    c_off, p_off = chip_kline(), (stats().get("stats") or {})
    prev, run = bench_bus.sim_only("iso9141")
    t0, back, p_now = time.time(), None, p_off
    while time.time() - t0 < 60:
        p_now = stats().get("stats") or {}
        if p_now.get("polls_ok", 0) - p_off.get("polls_ok", 0) >= len(ROWS):
            back = int(time.time() - t0)
            break
        time.sleep(1)
    c_b = chip_kline()
    say("FACT ignition off for 12 s and on again: the driver while it was off %s; the rows answered again %s s after the car was back; the driver then %s" % (
        c_off, back, c_b))
    check("poll: with the car off the driver closes the session that stopped answering and counts the failed starts",
          c_off.get("up") is False and c_off.get("closed", 0) - c_a.get("closed", 0) == 1 and c_off.get("failed", 0) > c_a.get("failed", 0), c_off)
    check("poll: with the car on again the rows are answered within a minute, by a new start of the bus",
          back is not None and c_b.get("up") is True and c_b.get("starts", 0) - c_a.get("starts", 0) >= 1, (back, c_b))
    # a slow table: nothing is due for ten seconds, and the ECU ends a session after five without a request.
    # The poller asks one short request in time, so no row has to start the bus again
    group["period_ms"] = 10000
    st, r = http("/api/autopid/config", "PUT", {"groups": [group], "pids": pids, "filters": []})
    time.sleep(12)
    k2, c2, p2 = kline(), chip_kline(), (stats().get("stats") or {})
    time.sleep(32)
    k3, c3, p3 = kline(), chip_kline(), (stats().get("stats") or {})
    keeps = p3.get("kline_keepalives", 0) - p2.get("kline_keepalives", 0)
    say("FACT ten second period, 32 s: polls ok +%d failed +%d, keep-alives +%d, the simulator's messages in +%d, slow inits +%d, closed +%d" % (
        p3.get("polls_ok", 0) - p2.get("polls_ok", 0), p3.get("polls_failed", 0) - p2.get("polls_failed", 0), keeps,
        k3.get("messages_in", 0) - k2.get("messages_in", 0), k3.get("inits_slow", 0) - k2.get("inits_slow", 0),
        c3.get("closed", 0) - c2.get("closed", 0)))
    check("poll: with a ten second period the session is kept (keep-alives sent, no slow init, nothing closed, every row answered)",
          st == 200 and keeps >= 4 and k3.get("inits_slow", 0) == k2.get("inits_slow", 0) and c3.get("closed", 0) == c2.get("closed", 0) and
          p3.get("polls_ok", 0) - p2.get("polls_ok", 0) >= len(ROWS) * 2 and p3.get("polls_failed", 0) == p2.get("polls_failed", 0) and
          c3.get("up") is True, c3)
    # SAE J1850 is pinned by hand (the detection walks the K-line only): the same rows on a VPW car
    group["period_ms"] = PERIOD_MS
    st, r = http("/api/autopid/config", "PUT", {"groups": [group], "pids": pids, "filters": []})
    prev, run = bench_bus.sim_only("vpw")
    ok, why = apply({"enabled": True, "std_protocol": "2"})
    if not check("poll: `std_protocol` 2 applied with the simulator as a J1850 VPW car (a planned restart)",
                 st == 200 and ok and bench_bus.sim_bus_live(run) == ["vpw"], (why, " ".join(bench_bus.sim_bus_live(run)))):
        return
    time.sleep(8)
    p4 = stats().get("stats") or {}
    time.sleep(5 * PERIOD_MS / 1000.0)
    a = stats()
    p5, c5 = a.get("stats") or {}, chip_kline()
    vals = {p.get("name"): p.get("value") for p in (a.get("params") or [])}
    say("FACT J1850 VPW pinned, five rounds: polls ok +%d failed +%d, values %s, keep-alives %s, the driver's session %s" % (
        p5.get("polls_ok", 0) - p4.get("polls_ok", 0), p5.get("polls_failed", 0) - p4.get("polls_failed", 0), vals,
        p5.get("kline_keepalives"), c5))
    check("poll: on J1850 VPW the three rows are answered round after round (800 rpm), with no K-line session and no keep-alive",
          vals.get("kl_rpm") == 800 and vals.get("kl_speed") is not None and vals.get("kl_temp") is not None and
          p5.get("polls_ok", 0) - p4.get("polls_ok", 0) >= len(ROWS) * 3 and p5.get("polls_failed", 0) == p4.get("polls_failed", 0) and
          c5.get("up") is False and p5.get("kline_keepalives", 0) == 0, (vals, c5))
    poll_codes("SAE J1850 VPW", False)


def guard():
    st, oc = http("/api/obd_chip")
    return (oc.get("eeprom_guard") or {}) if isinstance(oc, dict) else {}


def digit(text):
    t = str(text).strip().upper()
    return t[1:] if len(t) == 2 and t[0] == "A" else t


def rows_table(period_ms):
    """the three rows of the poll as a tables document's group and pids"""
    group = {"name": "default", "enabled": True, "period_ms": period_ms}
    pids = [{"name": n, "type": "custom", "cmd": c, "group": "default", "period_ms": 0, "enabled": True,
             "parameters": [{"name": n, "expression": e, "unit": ""}]} for n, c, e, _ in ROWS]
    return group, pids


def detect_run():
    """the detection job on request. Returns (the start's http status, the job's last status, the protocols it tried, seconds)"""
    t0 = time.time()
    st = 0
    while time.time() - t0 < 60:
        st, r = http("/api/autopid/vehicles/detect", "POST", {})
        if st == 202:
            break
        time.sleep(3)                   # 409: another job holds the chip
    t1 = time.time()
    tried, js, ran = [], {}, False
    while time.time() - t1 < 180:
        st2, js = http("/api/autopid/std_scan", timeout=6)
        js = js if isinstance(js, dict) else {}
        ran = ran or js.get("status") == "running"
        if js.get("trying") and (not tried or tried[-1] != js["trying"]):
            tried.append(js["trying"])
        if js.get("status") in ("done", "failed") and (ran or time.time() - t1 > 6):
            break
        time.sleep(0.25)
    return st, js, tried, int(time.time() - t0)


def own_car():
    v = vehicles()
    return next((c for c in v.get("vehicles", []) if c.get("key") == v.get("current")), {})


def leg_detect(sim0):
    g0, k0, car0 = guard(), kline(), own_car()
    st, js, tried, secs = detect_run()
    g1, k1, car1 = guard(), kline(), own_car()
    st2, res = http("/api/autopid/std_scan/result", timeout=20)
    res = res if isinstance(res, dict) else {}
    say("FACT detection on the K-line car: start http %s, %s after %d s, tried %s, error %r; result protocol %s, vin %s, %s PIDs; the simulator's inits slow +%d fast +%d" % (
        st, js.get("status"), secs, "".join(tried), js.get("error"), res.get("protocol_detected"), res.get("vin"), js.get("found"),
        k1.get("inits_slow", 0) - k0.get("inits_slow", 0), k1.get("inits_fast", 0) - k0.get("inits_fast", 0)))
    check("detect: the detection finds the K-line car", js.get("status") == "done" and not js.get("error"), js)
    check("detect: it went on to the K-line behind the CAN protocols and stopped on protocol 3", bool(tried) and tried[-1] == "3", tried)
    check("detect: the result names protocol 3, the car's VIN and its supported PIDs",
          res.get("protocol_detected") == "3" and res.get("vin") == car0.get("vin", car0.get("key")) and (js.get("found") or 0) > 0,
          (res.get("protocol_detected"), res.get("vin"), js.get("found")))
    check("detect: the car's record reads protocol 3, and the chip holds 3 after ONE save",
          car1.get("protocol") == "3" and digit(g1.get("stored_protocol", "")) == "3" and
          g1.get("protocol_saves", 0) - g0.get("protocol_saves", 0) == 1,
          (car1.get("protocol"), g1.get("stored_protocol"), g1.get("protocol_saves")))
    # what a person does next: polling on, the protocol left on Automatic. It follows the car's record
    group, pids = rows_table(PERIOD_MS)
    st, r = http("/api/autopid/config", "PUT", {"groups": [group], "pids": pids, "filters": []})
    k2 = kline()
    ok, why = apply({"enabled": True})
    if check("detect: Automate on with the protocol left on Automatic (a planned restart)",
             st == 200 and ok and settings().get("std_protocol") == "0", (st, why, settings().get("std_protocol"))):
        time.sleep(10)
        p0 = stats().get("stats") or {}
        time.sleep(5 * PERIOD_MS / 1000.0)
        a = stats()
        p1, c1, k3 = a.get("stats") or {}, chip_kline(), kline()
        vals = {p.get("name"): p.get("value") for p in (a.get("params") or [])}
        say("FACT Automatic behind the detection, five rounds: polls ok +%d failed +%d, values %s, the simulator's slow inits +%d since the restart, the driver %s, the car's record on %s" % (
            p1.get("polls_ok", 0) - p0.get("polls_ok", 0), p1.get("polls_failed", 0) - p0.get("polls_failed", 0), vals,
            k3.get("inits_slow", 0) - k2.get("inits_slow", 0), c1, own_car().get("protocol")))
        check("detect: on Automatic the rows are polled on the car's protocol 3: answered round after round, the bus started once since the restart",
              vals.get("kl_rpm") == 800 and vals.get("kl_speed") is not None and vals.get("kl_temp") is not None and
              p1.get("polls_ok", 0) - p0.get("polls_ok", 0) >= len(ROWS) * 3 and p1.get("polls_failed", 0) == p0.get("polls_failed", 0) and
              k3.get("inits_slow", 0) - k2.get("inits_slow", 0) == 1 and c1.get("up") is True and c1.get("starts") == 1 and
              own_car().get("protocol") == "3", (vals, c1))
    # the way back, the same road: the simulator as found (its CAN ECU carries the same VIN), a boot's one save free again
    check("detect, the way back: the simulator as found", sim_back(sim0))
    ok, why = apply({"enabled": False})
    check("detect, the way back: Automate off (a planned restart)", ok, why)
    g2 = guard()
    check("detect, the way back: the bring-up reads 3 in the chip's store", digit(g2.get("stored_protocol", "")) == "3", g2.get("stored_protocol"))
    st, js, tried, secs = detect_run()
    g3, car3 = guard(), own_car()
    say("FACT detection on the unit's own car again: %s after %d s, tried %s, error %r" % (js.get("status"), secs, "".join(tried), js.get("error")))
    check("detect, the way back: the unit's own car on its CAN protocol again (%s), the chip holds it after ONE save" % car0.get("protocol"),
          js.get("status") == "done" and car3.get("protocol") == car0.get("protocol") and
          digit(g3.get("stored_protocol", "")) == digit(g0.get("stored_protocol", "")) and g3.get("protocol_saves") == 1,
          (car3.get("protocol"), g3.get("stored_protocol"), g3.get("protocol_saves")))
    prev, run = bench_bus.sim_only("iso9141")           # the K-line car again for the legs behind
    check("detect: the simulator is the ISO 9141-2 car again", bench_bus.sim_bus_live(run) == ["kline"], " ".join(bench_bus.sim_bus_live(run)))
    time.sleep(3)


def main():
    ap = settings()
    st2, oc = http("/api/obd_chip")
    if not (ap and isinstance(oc, dict) and oc.get("ready")):
        print("KLINE ACCEPT FAIL (the unit does not answer at %s, or its chip is not ready)" % DUT_HP)
        return 1
    if not check("Automate is off at the start", not ap.get("enabled")):
        print("KLINE ACCEPT FAIL (switch Automate off for the run)")
        return 1
    st, found_cfg = http("/api/autopid/config")
    found_store = vehicles()
    st, h0 = http("/api/restart/history")
    g0 = oc.get("eeprom_guard", {})
    sim0 = sim_groups()
    snap0 = chip_nvm.snapshot(DUT_HP)
    say("as found: the chip %s, store %s; %d rows in the tables; the vehicle store %s" % (
        (oc.get("fw") or {}).get("chip"), g0.get("stored_protocol"),
        len((found_cfg or {}).get("pids") or []) if isinstance(found_cfg, dict) else -1, store_view(found_store)))
    try:
        prev, run = bench_bus.sim_only("iso9141")
        live = bench_bus.sim_bus_live(run)
        if not check("the simulator is a K-line car alone (ISO 9141-2, 5 baud init)", live == ["kline"], " ".join(live)):
            raise SystemExit(1)
        time.sleep(3)
        if "test" in ONLY:
            leg_test()
        if "others" in ONLY:
            leg_others()
        if "pin" in ONLY:
            leg_pin()
        if DETECT:
            leg_detect(sim0)
        if "poll" in ONLY:
            leg_poll()
    except SystemExit:
        pass
    finally:
        ok, why = apply({k: ap[k] for k in ("enabled", "std_protocol", "std_init") + DTC_KEYS})
        check("Automate's settings as found (a planned restart when one changed)", ok, why)
        v = vehicles()
        for c in v.get("vehicles", []):
            if c.get("key") not in [x.get("key") for x in found_store.get("vehicles", [])]:
                http("/api/autopid/vehicles/" + urllib.parse.quote(c.get("key"), safe=""), "DELETE")
        if found_store.get("current") and vehicles().get("current") != found_store.get("current"):
            http("/api/autopid/vehicles/%s/activate" % urllib.parse.quote(found_store.get("current"), safe=""), "POST", {})
        if isinstance(found_cfg, dict):
            http("/api/autopid/config", "PUT", found_cfg)
            st, back = http("/api/autopid/config")
            check("the unit's tables as found", st == 200 and back == found_cfg)
        check("the vehicle store as found", store_view(vehicles()) == store_view(found_store), store_view(vehicles()))
        check("the simulator's settings as found", sim_back(sim0))

    check("Automate's settings read as found", settings() == ap)
    time.sleep(12)                      # the firmware takes the chip back ten seconds behind the last word
    snap1 = chip_nvm.snapshot(DUT_HP)
    diff = chip_nvm.diff(snap0, snap1)
    st, oc1 = http("/api/obd_chip")
    g1 = oc1.get("eeprom_guard", {}) if isinstance(oc1, dict) else {}
    if DETECT:
        check("the chip's stored cells the same before and after (%d cells): the stored protocol went to 3 and came back" % len(snap0),
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
          h1.get("boot_count") == h0.get("boot_count") + PLANNED[0] and h1.get("unexpected_resets") == h0.get("unexpected_resets"),
          "boot %s -> %s with %d planned" % ((h0 or {}).get("boot_count"), (h1 or {}).get("boot_count") if isinstance(h1, dict) else h1, PLANNED[0]))
    failed = [n for n, ok in CHECKS if not ok]
    print("KLINE ACCEPT %s (%d checks, %d failed, %d planned restarts)" % ("PASS" if not failed else "FAIL", len(CHECKS), len(failed), PLANNED[0]))
    for n in failed:
        print("  red: " + n)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
