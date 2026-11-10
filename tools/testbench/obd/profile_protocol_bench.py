#!/usr/bin/env python3
"""A profile with ATSP6 and ATSP7 rows beside the chip's one protocol save  ->  PROFILE PROTOCOL PASS / FAIL

The firmware saves ONE protocol per car in the OBD chip (a real ATSP after
the detection job, obd_chip_protocol_save). Most vehicle profiles pin a
protocol themselves, and some pin both (the Audi / VW MEB profile: `ATSP7`
rows for the battery ECU beside `ATSP6` rows for the gateway). This bench
proves the two do not touch each other: the rows of such a profile poll
right whatever the chip holds, they never write the chip, and the profile's
protocol never becomes what is saved.

The simulator plays a SECOND car (the soak's fixture with another VIN,
uploaded as profile_proto.json and made its active profile); the DUT gets
that car's rows: two standard rows and four rows of the MEB profile, two on
`ATSP7` and two on `ATSP6`, in an order that changes the protocol at every
row. The unit's own car is never touched.

  flip       the car's standard requests on the protocol the chip holds.
             AutoPID on, a restart: the unknown car is detected at first
             contact, the save sends nothing (protocol_kept). The rows are
             polled for 40 s: every value right, no failed poll, nothing
             saved, refused or held, an ATM0 ahead of the switches. The same
             after a detection run while polling, after a restart (the
             bring-up reads the same store), and after an app's ATZ over
             /ws/obd (which also shows the store on the wire)
             Then the two standard rows are taken out and the detection job
             runs again with only the profile's rows live: it must finish
             and read the VIN (a row's own flow control, ATFCSH / ATFCSM1,
             used to stay in effect under the job: the VIN's reply stopped
             after its first frame and the job failed, "no ECU response")
  byprofile  the car answers no standard request (the simulator's 0100 and
             0902 silenced) and the Quick Setup's probe confirms it by the
             profile, whose first protocol is the OTHER one: the car is
             stored on the profile's protocol and NOTHING is saved to the
             chip (the save takes only a protocol a standard request
             answered on). The profile's rows poll right, a restart reads
             the same store
  write      (--write: TWO store writes, the flip and the way back) the
             car's standard requests on the other protocol: ONE ATSP at the
             detection; the same six rows poll right on a chip that now
             holds the other protocol; a restart reads it back; then the way
             back (one ATSP, a restart, the store as found)
  restore    the bench's cars forgotten, the unit's own car current, its
             tables and AutoPID settings as found, the simulator on its own
             profile and settings, the chip's store as found

usage: profile_protocol_bench.py [--dut localhost:18081] [--write] [--only flip,byprofile,write]
  run on the PC with the DUT through an ssh tunnel (`ssh -N -L
  18081:<dut>:80 rpi001`) and ECU sim v2 at its own address (lib/sim_net.py).
Needs: the firmware on the DUT, the chip up with 6 in its store, ECU sim v2
on the bus at 500 kbit/s, the /ws/obd bridge. Without --write: no write of
the chip, three planned restarts, about 8 min. With it: two store writes,
four more restarts, about 14 min.
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
import bench_bus  # noqa: E402  (the simulator's settings; installs sim_net)
import wsmin      # noqa: E402  (the /ws/obd bridge)

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


FIXTURE = os.path.join(HERE, "fixtures", "ecu_sim_meb_soak_profile.json")
VIN1 = "1WCAN0FW0P0000001"      # the simulator's own car, the bench unit's as found
VIN2 = "1WCAN0FW0P0000003"      # this bench's car
SIM_PROFILE = "profile_proto.json"
DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
SIM = bench_bus.SIM_URL
WRITE = "--write" in ARGS
ONLY = [s for s in arg("--only", "").split(",") if s]
FORMAT = {"6": "11bit", "7": "29bit"}
PROFILE_NAME = "Bench: rows on ATSP6 and ATSP7"
SPEC_INIT = "ATAT1;ATST96;"
PERIOD_MS = 200
#: four rows of the published MEB profile, as its file has them
ROW7A = ("SOC", "22028C", "ATSP7;ATCP17;ATSHFC007B;ATFCSH17FC007B;ATCRA17FE007B;", "(B4/2.5)", 50.8, 0.6)
ROW6A = ("RANGE", "222AB6", "ATSP6;ATSH710;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A;", "(B5*256)+B6", 32768, 1.0)
ROW7B = ("BATT_TEMP", "222A0B1", "ATSP7;ATCP17;ATSHFC007B;ATCRA17FE007B;ATFCSH17FC007B;ATFCSD300000;ATFCSM1;",
         "((B4/2)-40)", 24.0, 0.6)
ROW6B = ("OUTSIDE_TEMPERATURE", "2226091", "ATSP6;ATSH746;ATFCSH746;ATFCSD300000;ATFCSM1;ATCRA7B0;",
         "((B4*0.5)-50)", 14.0, 0.6)
SPEC = {"7": [ROW7A, ROW7B], "6": [ROW6A, ROW6B]}
STD = [("EngineRPM", "010C", "[B3:B4]*0.25", "0C"), ("VehicleSpeed", "010D", "B3", "0D")]
CHECKS = []
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


def http(path, method="GET", body=None, timeout=15, base=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request((base or DUT) + path, data=data, method=method)
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


# ---- the DUT -------------------------------------------------------------------

def guard():
    st, oc = http("/api/obd_chip")
    return oc.get("eeprom_guard", {}) if isinstance(oc, dict) else {}


def digit(text):
    t = str(text).strip().upper()
    return t[1:] if len(t) == 2 and t[0] == "A" else t


def stored():
    return digit(guard().get("stored_protocol", ""))


def wait_ready(limit=120):
    t0 = time.time()
    while time.time() - t0 < limit:
        st, oc = http("/api/obd_chip", timeout=4)
        if st == 200 and isinstance(oc, dict) and oc.get("ready"):
            return True
        time.sleep(1)
    return False


def restart():
    st, r = http("/api/restart", "POST", {})
    RESTARTS[0] += 1
    time.sleep(6)
    return st == 200 and wait_ready()


def history():
    st, h = http("/api/restart/history")
    return h if isinstance(h, dict) else {}


def ring():
    st, text = http("/api/logs/ring", timeout=20)
    text = text if isinstance(text, str) else json.dumps(text)
    return [re.sub(r"\x1b\[[0-9;]*m", "", ln).strip() for ln in text.splitlines()]


def ring_show(pattern, last=2):
    for ln in [ln for ln in ring() if pattern in ln][-last:]:
        print("    ring: " + ln[-170:], flush=True)


def vehicles():
    st, v = http("/api/autopid/vehicles")
    return v if isinstance(v, dict) else {}


def car(key):
    return next((c for c in vehicles().get("vehicles", []) if c.get("key") == key), None)


def forget(keep):
    """every car the bench made (whatever is not in `keep`) is forgotten"""
    for c in vehicles().get("vehicles", []):
        if c.get("key") not in keep:
            st, _ = http("/api/autopid/vehicles/" + c["key"], "DELETE")
            say("forgot %s (%s): %s" % (c.get("key"), c.get("name"), st))
    return all(c.get("key") in keep for c in vehicles().get("vehicles", []))


def own_car_current(keep):
    """forgetting the current car leaves its rows live and no car current:
    the unit's own car (its rows, its protocol) is what a boot starts from"""
    if keep and vehicles().get("current") != keep[0]:
        st, r = http("/api/autopid/vehicles/" + keep[0] + "/activate", "POST", {})
        say("the unit's own car made current: %s" % st)


def autopid():
    st, a = http("/api/autopid", timeout=20)
    return a if isinstance(a, dict) else {}


def put_settings(patch):
    st, cur = http("/api/settings/autopid")
    assert st == 200 and isinstance(cur, dict), "autopid settings unreachable: %s %s" % (st, cur)
    new = {k: v for k, v in cur.items() if k not in ("degraded", "pending_reboot")}
    new.update(patch)
    st, r = http("/api/settings/autopid", "PUT", new)
    assert st == 200 and isinstance(r, dict) and "error" not in r, "autopid PUT: %s %s" % (st, r)
    return bool(r.get("changed"))


def apply_settings(patch):
    """the settings staged and applied (a planned restart); a plain restart
    when nothing changed, so the caller always gets a fresh boot"""
    if put_settings(patch):
        st, r = http("/api/settings/submit", "POST", {}, timeout=15)
        assert st == 200 and isinstance(r, dict) and r.get("reboot"), "submit: %s %s" % (st, r)
        RESTARTS[0] += 1
        time.sleep(8)
        return wait_ready(150)
    return restart()


def scan_wait(limit=180):
    t0 = time.time()
    js = {}
    while time.time() - t0 < limit:
        st, js = http("/api/autopid/std_scan")
        if isinstance(js, dict) and js.get("status") in ("done", "failed"):
            return js
        time.sleep(3)
    return js if isinstance(js, dict) else {}


def detect(body=None):
    """the detection job on request (the Quick Setup's button)"""
    t0 = time.time()
    st, r = 0, None
    while time.time() - t0 < 60:
        st, r = http("/api/autopid/vehicles/detect", "POST", body or {})
        if st == 202:
            break
        time.sleep(3)                   # 409: a job of the poller holds the chip
    if st != 202:
        return {"status": "not started", "http": st, "body": r}
    time.sleep(2)
    return scan_wait()


def detected_by_itself(key, limit=110):
    """first contact meets an unknown car: the detection job starts without a
    request. True when the car became current on its own; a request after
    `limit` seconds otherwise (the bench goes on, the check says which)."""
    t0 = time.time()
    while time.time() - t0 < limit:
        if vehicles().get("current") == key:
            scan_wait(60)
            return True
        time.sleep(3)
    js = detect()
    say("no detection by itself in %d s; on request: %s" % (limit, json.dumps(js)[:160]))
    return False


def bridge_store():
    """an app over /ws/obd: ATZ, ATE0, ATDPN = the chip's store on the wire"""
    host, _, port = DUT_HP.partition(":")
    ws = wsmin.WS(host, int(port or 80), "/ws/obd", timeout=3)
    ws.settimeout(0.2)

    def cmd(text, wait_s=3.0):
        ws.pump()
        ws.send((text + "\r").encode(), wsmin.OP_BIN)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < wait_s:
            frames = ws.pump()
            for _, p in frames:
                buf += p
            if b">" in buf:
                break
            if not frames:
                time.sleep(0.02)
        return buf.decode(errors="replace").replace("\r", " ").split(">")[0].strip()

    cmd("", 1.0)
    cmd("ATZ")
    cmd("ATE0")
    dpn = cmd("ATDPN").replace("ATDPN", "").strip()
    ws.close()
    return dpn


# ---- the simulator -------------------------------------------------------------

def sim_http(path, method="GET", body=None, raw=None, timeout=40):
    return http(path, method, body, timeout, base=SIM, raw=raw)


def sim_car(fmt, silent_std=False):
    """the bench's car as the simulator's active profile, its standard answers
    on `fmt`; `silent_std` = the car answers no 0100 and no 0902"""
    prev, run = bench_bus.sim_patch({"ecu_sim": {
        "active_profile": SIM_PROFILE, "hs_obd11": fmt in ("11bit", "both"),
        "hs_obd29": fmt in ("29bit", "both"), "hs_uds": True, "hs_dialect": "obd2"}}, base=SIM)
    time.sleep(1.5)
    hs = (run or {}).get("hs_can", {})
    ok = bool(run) and "MEB" in str(run.get("profile")) and bool(hs.get("obd11")) == (fmt in ("11bit", "both")) \
        and bool(hs.get("obd29")) == (fmt in ("29bit", "both"))
    for mode, pid in (("01", "00"), ("09", "02")):
        if silent_std:
            st, r = sim_http("/api/ecu/errors/%s/%s" % (mode, pid), "POST",
                             {"enabled": True, "response_type": "no_response", "trigger": "continuous"})
            ok = ok and st == 200
        else:
            sim_http("/api/ecu/errors/%s/%s" % (mode, pid), "DELETE")
    say("simulator: the bench's car, standard answers %s%s (%s)" % (
        fmt, ", 0100 and 0902 silenced" if silent_std else "", "ok" if ok else json.dumps(run)[:200]))
    return ok


# ---- the rows -------------------------------------------------------------------

def std_expect():
    fx = json.load(open(FIXTURE, encoding="utf-8", errors="replace"))
    out = {}
    for name, _, _, pid in STD:
        p = next(x for x in fx["standard_pids"] if x["mode"] == "01" and x["pid"] == pid)
        out[name] = (float(p["parameters"][0]["default"]), 1.0)
    return out


def table(order, std):
    """`order` = the protocols of the specific rows in turn; with `std` a
    standard row ahead of each pair, so every row changes the protocol"""
    pids = []
    left = {"6": list(SPEC["6"]), "7": list(SPEC["7"])}
    stdrows = list(STD)
    want = {}
    for i, p in enumerate(order):
        if std and i % 2 == 0 and stdrows:
            name, cmd, expr, _ = stdrows.pop(0)
            pids.append({"name": name, "type": "std", "cmd": cmd, "group": "default", "enabled": True,
                         "parameters": [{"name": name, "expression": expr, "unit": ""}]})
        name, cmd, init, expr, exp, tol = left[p].pop(0)
        pids.append({"name": name, "type": "specific", "cmd": cmd, "init": init, "group": "default",
                     "enabled": True, "period_ms": 0,
                     "parameters": [{"name": name, "expression": expr, "unit": ""}]})
        want[name] = (exp, tol)
    if std:
        want.update(std_expect())
    return {"groups": [{"name": "default", "enabled": True, "period_ms": PERIOD_MS}], "pids": pids, "filters": []}, want


def attach(key, order, std):
    st, r = http("/api/autopid/vehicles/" + key, "PUT", {"profile": PROFILE_NAME, "specific_init": SPEC_INIT})
    ok = st == 200
    tbl, want = table(order, std)
    st2, r2 = http("/api/autopid/config", "PUT", tbl, timeout=30)
    ok = ok and st2 == 200 and isinstance(r2, dict) and r2.get("ok")
    check("the profile's rows on the car (%d rows: %s)" % (len(tbl["pids"]), ", ".join(
        "%s%s" % (p["name"], (" " + p["init"][:5]) if p.get("init") else "") for p in tbl["pids"])), ok, (st, r, st2, r2))
    return tbl, want


def window(label, secs, want, proto):
    """`secs` of polling: the values, the counters, the chip's memory guard"""
    time.sleep(4)                       # a detection, an app or a boot just ended
    a0 = autopid()
    g0 = guard()
    time.sleep(secs)
    a1 = autopid()
    g1 = guard()
    s0, s1 = a0.get("stats", {}), a1.get("stats", {})
    ok_n = s1.get("polls_ok", 0) - s0.get("polls_ok", 0)
    bad_n = s1.get("polls_failed", 0) - s0.get("polls_failed", 0)
    vals = {p.get("name"): p.get("value") for p in a1.get("params", [])}
    rows = len(want)
    check("%s: %d s of polling, every poll answered (%d ok, %d failed)" % (label, secs, ok_n, bad_n),
          ok_n >= secs and bad_n == 0, s1)
    wrong = {n: vals.get(n) for n, (exp, tol) in want.items()
             if not isinstance(vals.get(n), (int, float)) or abs(vals[n] - exp) > tol}
    check("%s: the %d values are the simulator's (%s)" % (label, rows, ", ".join(
        "%s %s" % (n, vals.get(n)) for n in want)), not wrong, wrong)
    switches = g1.get("memory_off", 0) - g0.get("memory_off", 0)
    quiet = all(g1.get(k, 0) == g0.get(k, 0) for k in ("protocol_saves", "memory_refused", "requests_held", "blocked"))
    check("%s: nothing saved, refused or held; %d ATM0 for %d polls; the store reads %s" % (
        label, switches, ok_n, g1.get("stored_protocol")),
        quiet and not g1.get("switch_owed") and digit(g1.get("stored_protocol", "")) == proto and
        switches >= ok_n // max(rows, 1), (g0, g1))
    errs = [ln for ln in ring() if re.match(r"^E[ (]", ln)]
    check("%s: no E line in the log ring" % label, not errs, errs[:3])


# ---- the legs -------------------------------------------------------------------

def leg_flip(held, keep):
    print("== flip: rows on ATSP7 and ATSP6, the car's standard requests on the protocol the chip holds (%s) ==" % held, flush=True)
    check("the simulator plays the bench's car on %s" % FORMAT[held], sim_car(FORMAT[held]))
    check("AutoPID on (standard and vehicle specific rows), a planned restart",
          apply_settings({"enabled": True, "std_enabled": True, "specific_enabled": True, "custom_enabled": True}))
    check("the unknown car is detected at first contact, without a request", detected_by_itself(VIN2))
    c = car(VIN2) or {}
    g = guard()
    say("the car: %s; guard %s" % (json.dumps({k: c.get(k) for k in ("key", "name", "protocol", "chip_protocol", "confirmed_by")}), g))
    ring_show("not sent: the chip's store holds")
    check("the car is stored on protocol %s, the chip was not written (protocol_saves 0, protocol_kept 1)" % held,
          c.get("protocol") == held and c.get("chip_protocol") == held and g.get("protocol_saves") == 0 and
          g.get("protocol_kept") == 1 and digit(g.get("stored_protocol", "")) == held, (c.get("protocol"), c.get("chip_protocol"), g))
    tbl, want = attach(VIN2, ["7", "6", "7", "6"], True)
    window("flip", 40, want, held)

    js = detect()
    c = car(VIN2) or {}
    st, cfg = http("/api/autopid/config", timeout=30)
    names = [p.get("name") for p in cfg.get("pids", [])] if isinstance(cfg, dict) else []
    check("a detection run while polling: done, protocol %s again, the rows kept" % held,
          js.get("status") == "done" and c.get("protocol") == held and c.get("chip_protocol") == held and
          names == [p["name"] for p in tbl["pids"]], (json.dumps(js)[:160], c.get("protocol"), names))
    window("after the detection", 20, want, held)

    check("a planned restart", restart())
    g = guard()
    check("the bring-up reads %s in the chip's store, no save this boot" % held,
          digit(g.get("stored_protocol", "")) == held and g.get("protocol_saves") == 0, g)
    window("after the restart", 30, want, held)

    raw = bridge_store()
    check("an app's ATZ and ATDPN over the bridge: the store reads %s on the wire" % held, digit(raw) == held, raw)
    time.sleep(6)                       # the app left: the poller takes the chip back
    window("after the app's reset", 20, want, held)

    print("-- only the profile's rows live: a detection run starts from what a row left in the chip --", flush=True)
    tbl, want = attach(VIN2, ["7", "6", "7", "6"], False)
    time.sleep(8)
    js = detect()
    st, res = http("/api/autopid/std_scan/result")
    res = res if isinstance(res, dict) else {}
    check("a detection run with only the profile's rows live: done, the VIN read (%s)" % (res.get("vin") if res.get("ts") == js.get("ts") else js.get("error")),
          js.get("status") == "done" and res.get("vin") == VIN2 and res.get("ts") == js.get("ts"), json.dumps(js)[:200])
    ring_show("detection ", 1)
    window("only the profile's rows", 20, want, held)


def leg_byprofile(held, other, keep):
    print("== byprofile: no standard answer, the probe confirms the car by a profile whose first protocol is %s; the chip holds %s ==" % (other, held), flush=True)
    check("the bench's cars forgotten", forget(keep))
    own_car_current(keep)
    check("the simulator plays the bench's car, 0100 and 0902 silenced", sim_car(FORMAT[held], silent_std=True))
    check("AutoPID on, a planned restart",
          apply_settings({"enabled": True, "std_enabled": True, "specific_enabled": True, "custom_enabled": True}))
    time.sleep(20)                      # first contact has nothing to find
    before = [c.get("key") for c in vehicles().get("vehicles", [])]
    g0 = guard()
    rows = SPEC[other] + SPEC[held]
    body = {"profile": PROFILE_NAME, "name": "Bench flip", "protocol": other, "init": SPEC_INIT,
            "cmds": [{"cmd": r[1], "init": r[2]} for r in rows[:3]]}
    js = detect(body)
    v = vehicles()
    key = v.get("current")
    c = car(key) or {}
    g1 = guard()
    say("detection: %s; the car: %s; guard %s" % (json.dumps(js)[:200], json.dumps(
        {k: c.get(k) for k in ("key", "name", "protocol", "chip_protocol", "confirmed_by", "profile")}), g1))
    ring_show("the profile's")
    ring_show("EEPROM")
    check("the probe confirmed the car by its profile (a new car, on the profile's protocol %s)" % other,
          js.get("status") == "done" and key not in before and c.get("confirmed_by") == "profile" and
          c.get("protocol") == other, (json.dumps(js)[:160], key, c.get("confirmed_by"), c.get("protocol")))
    check("the profile's protocol was NOT saved to the chip (protocol_saves +0, the store reads %s)" % held,
          g1.get("protocol_saves", 0) == g0.get("protocol_saves", 0) and digit(g1.get("stored_protocol", "")) == held,
          (g0, g1))
    check("the car's record does not claim the chip holds its protocol", c.get("chip_protocol", "") in ("", held),
          c.get("chip_protocol"))
    if not key or key in before:
        return
    tbl, want = attach(key, [other, held, other, held], False)
    now_held = stored()
    window("byprofile", 30, want, now_held)
    check("a planned restart", restart())
    g = guard()
    check("the bring-up reads %s in the chip's store" % held, digit(g.get("stored_protocol", "")) == held, g)
    window("byprofile, after the restart", 20, want, stored())


def move_store(to, keep, label):
    """the car's standard requests on `to` while the chip holds the other
    protocol: a new car, ONE ATSP. Returns True when the store reads `to`."""
    check("%s: the bench's cars forgotten" % label, forget(keep))
    own_car_current(keep)
    check("%s: the simulator plays the bench's car on %s" % (label, FORMAT[to]), sim_car(FORMAT[to]))
    check("%s: AutoPID on, a planned restart (a boot's one save is free again)" % label,
          apply_settings({"enabled": True, "std_enabled": True, "specific_enabled": True, "custom_enabled": True}))
    check("%s: the unknown car is detected at first contact, without a request" % label, detected_by_itself(VIN2))
    c = car(VIN2) or {}
    g = guard()
    ring_show("EEPROM")
    check("%s: ONE ATSP%s (protocol_saves 1), the car stored on %s, the store reads %s" % (label, to, to, to),
          g.get("protocol_saves") == 1 and c.get("protocol") == to and c.get("chip_protocol") == to and
          digit(g.get("stored_protocol", "")) == to, (c.get("protocol"), c.get("chip_protocol"), g))
    return digit(g.get("stored_protocol", "")) == to


def leg_write(held, other, keep):
    print("== write: the car's standard requests on %s, the chip holds %s: ONE ATSP, then the rows on both protocols ==" % (other, held), flush=True)
    move_store(other, keep, "write")
    tbl, want = attach(VIN2, [held, other, held, other], True)
    window("write, the chip holds %s" % other, 40, want, other)
    check("a planned restart", restart())
    g = guard()
    check("the bring-up reads the new store back (%s), no save this boot" % other,
          digit(g.get("stored_protocol", "")) == other and g.get("protocol_saves") == 0, g)
    window("write, after the restart", 30, want, other)
    js = detect()
    g = guard()
    check("a detection run on the known car: nothing saved", js.get("status") == "done" and g.get("protocol_saves") == 0, g)
    print("== the way back: the car's standard requests on %s again, ONE ATSP ==" % held, flush=True)
    move_store(held, keep, "back")
    check("a planned restart", restart())
    check("the bring-up reads %s again" % held, stored() == held, guard())


def main():
    st, info = http("/api/info")
    print("DUT %s" % (info.get("fw_build") if isinstance(info, dict) else info), flush=True)
    check("the chip is up", wait_ready(30))
    g = guard()
    held = digit(g.get("stored_protocol", ""))
    say("guard as found: %s" % g)
    v0 = vehicles()
    keep = [c.get("key") for c in v0.get("vehicles", [])]
    st, cfg0 = http("/api/autopid/config", timeout=30)
    st, ap0 = http("/api/settings/autopid")
    h0 = history()
    sim0 = bench_bus.sim_get(base=SIM)
    sim_keys = {k: sim0.get(k) for k in ("active_profile", "hs_obd11", "hs_obd29", "hs_uds", "hs_dialect")}
    say("vehicle store as found: current %r, %s; autopid enabled %s; simulator %s" % (
        v0.get("current"), [(c.get("key"), c.get("name"), c.get("protocol"), c.get("chip_protocol")) for c in v0.get("vehicles", [])],
        ap0.get("enabled") if isinstance(ap0, dict) else ap0, sim_keys))
    if not check("the chip holds 6 as found and the bench's car is not in the store", held == "6" and VIN2 not in keep, (held, keep)):
        sys.exit(1)
    other = "7"
    body = open(FIXTURE, "rb").read()
    assert body.count(VIN1.encode()) >= 1, "the fixture does not carry the simulator's VIN"
    st, r = sim_http("/api/ecu/profiles/" + SIM_PROFILE, "POST", raw=body.replace(VIN1.encode(), VIN2.encode()), timeout=60)
    check("the bench's car uploaded to the simulator", st == 200, (st, r))
    try:
        if not ONLY or "flip" in ONLY:
            leg_flip(held, keep)
        if not ONLY or "byprofile" in ONLY:
            leg_byprofile(held, other, keep)
        if WRITE and (not ONLY or "write" in ONLY):
            leg_write(held, other, keep)
    except AssertionError as exc:
        check("the run reached its end", False, exc)
    finally:
        print("== restore ==", flush=True)
        if stored() != held:
            say("the chip's store reads %s, as found %s: the way back" % (stored(), held))
            move_store(held, keep, "restore")
            restart()
        check("the bench's cars forgotten", forget(keep))
        if v0.get("current"):
            st, r = http("/api/autopid/vehicles/" + v0["current"] + "/activate", "POST", {})
            check("the unit's own car made current again", st == 200, (st, r))
        prev, run = bench_bus.sim_patch({"ecu_sim": sim_keys}, base=SIM)
        for mode, pid in (("01", "00"), ("09", "02")):
            sim_http("/api/ecu/errors/%s/%s" % (mode, pid), "DELETE")
        st, r = sim_http("/api/ecu/profiles/" + SIM_PROFILE, "DELETE")
        st2, plist = sim_http("/api/ecu/profiles")
        check("the simulator on its own profile and settings, the bench's profile deleted",
              isinstance(plist, dict) and plist.get("active") == sim_keys["active_profile"] and
              all(p.get("name") != SIM_PROFILE for p in plist.get("profiles", [])), (st, plist))
        if isinstance(ap0, dict):
            vals = {k: v for k, v in ap0.items() if k not in ("degraded", "pending_reboot")}
            check("AutoPID's settings as found (a planned restart)", apply_settings(vals))
        v1 = vehicles()
        strip = lambda v: [(c.get("key"), c.get("name"), c.get("protocol"), c.get("chip_protocol"), c.get("profile")) for c in v.get("vehicles", [])]
        check("the vehicle store as found", v1.get("current") == v0.get("current") and strip(v1) == strip(v0), (v1.get("current"), strip(v1)))
        st, cfg1 = http("/api/autopid/config", timeout=30)
        check("the live tables as found", cfg1 == cfg0)
        st, ap1 = http("/api/settings/autopid")
        check("the AutoPID settings read as found", isinstance(ap1, dict) and isinstance(ap0, dict) and
              all(ap1.get(k) == ap0.get(k) for k in ap0 if k not in ("degraded", "pending_reboot")))
        g = guard()
        check("the chip's store as found (%s)" % held, digit(g.get("stored_protocol", "")) == held, g)
        h1 = history()
        check("%d planned restarts, none unexpected" % RESTARTS[0],
              h1.get("unexpected_resets") == h0.get("unexpected_resets") and
              (h1.get("boot_count", 0) - h0.get("boot_count", 0)) == RESTARTS[0],
              (h0.get("boot_count"), h1.get("boot_count"), h0.get("unexpected_resets"), h1.get("unexpected_resets")))
        say("guard at the end: %s" % g)
    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    print("PROFILE PROTOCOL %s (%d checks, %d failed)" % ("PASS" if not fails else "FAIL", len(CHECKS), len(fails)), flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
