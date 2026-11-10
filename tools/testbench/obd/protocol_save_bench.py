#!/usr/bin/env python3
"""The OBD chip's protocol save: read back first, one ATSP per boot  ->  PROTOCOL SAVE PASS / FAIL

After a vehicle detection the firmware saves the car's protocol in the chip
(obd_chip_protocol_save, a real ATSP: the one deliberate write of the chip's
own memory). Since 2026-11-10 the bring-up reads the chip's stored protocol
(ATDPN right after the chip's reset, `eeprom_guard.stored_protocol`), the
save sends nothing when the chip holds the protocol already
(`protocol_kept`), and the one ATSP of a boot is counted when it goes out.

  as found  stored_protocol on GET /api/obd_chip equals what a reset shows
            over /ws/obd (ATZ, ATE0, ATDPN); the bring-up's log line carries
            it; the vehicle store and the live tables noted
  kept      the simulator plays a SECOND car (the fixture profile with
            another VIN, uploaded as save_probe.json and reloaded warm); the
            detection job meets it as a new car on the protocol the chip
            holds: protocol_saves +0, protocol_kept +1, the car's record
            says the chip holds it, the ring reads "ATSP6 not sent", the
            store unchanged over the bridge
  write     (--write: TWO store writes, the flip and the way back) the
            simulator on the other id format, the second car detected there:
            ONE real ATSP (protocol_saves +1, stored_protocol the new one,
            the ring reads "saved to the chip's EEPROM"); a planned restart:
            the bring-up reads the new store back and a reset over the
            bridge shows the same; the car forgotten and detected again:
            nothing sent (protocol_kept +1, protocol_saves +0); then the way
            back the same way (one ATSP, a restart, the store as found)
  restore   the second car forgotten, the unit's own car current again, the
            simulator back on default.json with the second car's profile
            deleted, the vehicle store and the live tables as found

usage: protocol_save_bench.py [--dut localhost:18081] [--profile <json>] [--write]
  run on the PC with the DUT through an ssh tunnel (`ssh -N -L
  18081:<dut>:80 rpi001`) and ECU sim v2 at its own address (lib/sim_net.py).
  --profile = a simulator profile to derive the second car from (default: the
  soak's fixture, which carries the simulator's own VIN). The unit's own car
  is never touched: its record, name and tables are compared at the end.
Needs: the firmware on the DUT, the chip up, ECU sim v2 on the bus at 500
kbit/s, the /ws/obd bridge. Without --write: no write of the chip, about 20 s.
With it: two store writes, two planned restarts, four simulator restarts,
about 90 s. The lost-answer case cannot be staged on a real chip: the host
suite covers it (obd_chip, test_save_lost_answer_is_not_written_twice).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import bench_bus  # noqa: E402  (the simulator's id format; installs sim_net)
import wsmin      # noqa: E402  (the /ws/obd bridge)

ARGS = sys.argv[1:]
DEFAULT_PROFILE = (ARGS[ARGS.index("--profile") + 1] if "--profile" in ARGS
                   else os.path.join(HERE, "fixtures", "ecu_sim_meb_soak_profile.json"))
VIN1 = "1WCAN0FW0P0000001"      # the simulator's own car, the bench unit's as found
VIN2 = "1WCAN0FW0P0000002"      # this run's second car
SIM_PROFILE = "save_probe.json"
DUT_HP = ARGS[ARGS.index("--dut") + 1] if "--dut" in ARGS else "localhost:18081"
DUT = "http://" + DUT_HP
SIM = bench_bus.SIM_URL
WRITE = "--write" in ARGS
CHECKS = []
FORMAT = {"6": "11bit", "7": "29bit"}


def now():
    return time.strftime("%H:%M:%S")


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name, (" (" + str(detail)[:260] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


def http(path, method="GET", body=None, timeout=15):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(DUT + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def guard():
    st, oc = http("/api/obd_chip")
    return oc.get("eeprom_guard", {}) if isinstance(oc, dict) else {}


def wait_ready(limit=90):
    t0 = time.time()
    while time.time() - t0 < limit:
        st, oc = http("/api/obd_chip", timeout=4)
        if st == 200 and isinstance(oc, dict) and oc.get("ready") and oc.get("claim") == "none":
            return True
        time.sleep(1)
    return False


def restart():
    st, r = http("/api/restart", "POST", {})
    time.sleep(6)
    ok = wait_ready(120)
    return st == 200 and ok


def ring_lines(pattern):
    st, ring = http("/api/logs/ring", timeout=20)
    text = ring if isinstance(ring, str) else json.dumps(ring)
    return [ln.strip()[-150:] for ln in text.split("\n") if pattern in ln]


def vehicles():
    st, v = http("/api/autopid/vehicles")
    return v if isinstance(v, dict) else {}


def forget_second():
    http("/api/autopid/vehicles/" + VIN2, "DELETE")
    return all(c.get("key") != VIN2 for c in vehicles().get("vehicles", []))


def sim_http(path, method="GET", raw=None, timeout=40):
    req = urllib.request.Request(SIM + path, data=raw, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def detect():
    st, r = http("/api/autopid/vehicles/detect", "POST", {})
    if st != 202:
        return None, "detect: %s %s" % (st, r)
    t0 = time.time()
    js = {}
    while time.time() - t0 < 180:
        time.sleep(3)
        st, js = http("/api/autopid/std_scan")
        if isinstance(js, dict) and js.get("status") in ("done", "failed"):
            break
    v = vehicles()
    cur = v.get("current")
    car = next((c for c in v.get("vehicles", []) if c.get("key") == cur), None)
    return car, "status %s in %.0f s" % (js.get("status") if isinstance(js, dict) else js, time.time() - t0)


def bridge_store():
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


def sim_to(proto):
    """the id format (a simulator reboot, which puts it back on default.json),
    then the second car reloaded warm"""
    prev, run = bench_bus.sim_set(id_format=FORMAT[proto], base=SIM)
    ok = bool(run) and run.get("id_format") == FORMAT[proto]
    body = open(DEFAULT_PROFILE, "rb").read()
    assert body.count(VIN1.encode()) >= 1, "the profile does not carry the simulator's VIN"
    body = body.replace(VIN1.encode(), VIN2.encode())
    st1, r1 = sim_http("/api/ecu/profiles/" + SIM_PROFILE, "POST", raw=body, timeout=60)
    st2, r2 = sim_http("/api/ecu/profiles/" + SIM_PROFILE + "/reload", "POST", raw=b"")
    time.sleep(1.5)
    ok = ok and st1 == 200 and st2 == 200
    print("%s simulator on %s, playing the second car (%s; upload %s, reload %s)" % (now(), FORMAT[proto], "ok" if ok else run, st1, st2), flush=True)
    return ok


def digit(text):
    t = text.strip().upper()
    return t[1:] if len(t) == 2 and t[0] == "A" else t


def detect_leg(label, proto, want_saves, want_kept):
    g0 = guard()
    car, how = detect()
    g1 = guard()
    print("%s %s: detection %s; car %s" % (now(), label, how, json.dumps({k: (car or {}).get(k) for k in ("key", "protocol", "chip_protocol", "name")})), flush=True)
    check("%s: the detection found the car on protocol %s" % (label, proto), bool(car) and car.get("protocol") == proto, car)
    check("%s: protocol_saves +%d, protocol_kept +%d" % (label, want_saves, want_kept),
          g1.get("protocol_saves", 0) - g0.get("protocol_saves", 0) == want_saves and
          g1.get("protocol_kept", 0) - g0.get("protocol_kept", 0) == want_kept, (g0, g1))
    check("%s: the car's record says the chip holds %s" % (label, proto), bool(car) and car.get("chip_protocol") == proto, (car or {}).get("chip_protocol"))
    check("%s: the driver's stored protocol is %s" % (label, proto), digit(g1.get("stored_protocol", "")) == proto, g1.get("stored_protocol"))
    return g1


def main():
    st, info = http("/api/info")
    print("DUT %s" % (info.get("fw_build") if isinstance(info, dict) else info), flush=True)
    check("the chip is up", wait_ready(30))
    g = guard()
    print("%s guard as found: %s" % (now(), g), flush=True)
    v0 = vehicles()
    st, cfg0 = http("/api/autopid/config", timeout=30)
    print("%s vehicle store as found: current %r, %s" % (now(), v0.get("current"), [(c.get("key"), c.get("name"), c.get("protocol"), c.get("chip_protocol")) for c in v0.get("vehicles", [])]), flush=True)
    if not check("the second car is not in the store as found", all(c.get("key") != VIN2 for c in v0.get("vehicles", []))):
        sys.exit(1)
    sim = bench_bus.sim_get(base=SIM)
    sim_prev = sim.get("id_format")
    as_text = g.get("stored_protocol", "")
    as_found = digit(as_text)
    raw = bridge_store()
    check("stored_protocol (%r, read at the bring-up) is what a reset shows over the bridge (%r)" % (as_text, raw), as_text == raw and as_found in FORMAT, (as_text, raw))
    for ln in ring_lines("started: chip"):
        print("    ring: " + ln, flush=True)
    check("the bring-up's line carries the stored protocol", any("stored protocol " + as_text in ln for ln in ring_lines("started: chip")))
    other = "7" if as_found == "6" else "6"

    print("== kept: a new car on the protocol the chip holds already ==", flush=True)
    check("the simulator on the stored protocol's id format, the second car playing", sim_to(as_found))
    detect_leg("kept", as_found, 0, 1)
    for ln in ring_lines("not sent: the chip's store holds"):
        print("    ring: " + ln, flush=True)
    check("the store is unchanged over the bridge", digit(bridge_store()) == as_found)
    check("the second car forgotten again", forget_second())

    if WRITE:
        print("== write: the car on the other protocol, ONE ATSP, then the read-back across a restart ==", flush=True)
        check("the simulator on the other id format", sim_to(other))
        detect_leg("write", other, 1, 0)
        for ln in ring_lines("saved to the chip's EEPROM"):
            print("    ring: " + ln, flush=True)
        check("a planned restart", restart())
        g = guard()
        check("the bring-up read the new store back (%s), no save this boot" % other, digit(g.get("stored_protocol", "")) == other and g.get("protocol_saves") == 0, g)
        check("a reset over the bridge shows the same", digit(bridge_store()) == other)
        check("the second car forgotten", forget_second())
        detect_leg("after the read-back", other, 0, 1)
        check("the second car forgotten again", forget_second())

        print("== the way back: the car on the as-found protocol, ONE ATSP ==", flush=True)
        check("the simulator back on the as-found id format", sim_to(as_found))
        detect_leg("back", as_found, 1, 0)
        check("a planned restart", restart())
        g = guard()
        check("the bring-up reads %s again" % as_found, digit(g.get("stored_protocol", "")) == as_found, g)
        check("the second car forgotten", forget_second())

    print("== restore ==", flush=True)
    forget_second()
    if v0.get("current"):
        st, r = http("/api/autopid/vehicles/" + v0["current"] + "/activate", "POST", {})
        check("the unit's own car made current again", st == 200, (st, r))
    if sim_prev in ("11bit", "29bit"):
        bench_bus.sim_set(id_format=sim_prev, base=SIM)      # a reboot when it changes: back on default.json
    sim_http("/api/ecu/profiles/default.json/reload", "POST", raw=b"")
    time.sleep(1.5)
    st, r = sim_http("/api/ecu/profiles/" + SIM_PROFILE, "DELETE")
    if st == 409:
        # the warm reload plays default.json but the simulator counts the
        # second car's profile as active until it reboots: a settings submit
        sim_http("/api/settings/submit", "POST", raw=b"{}")
        time.sleep(8)
        for _ in range(20):
            st3, _ = sim_http("/api/ecu/status", timeout=5)
            if st3 == 200:
                break
            time.sleep(2)
        st, r = sim_http("/api/ecu/profiles/" + SIM_PROFILE, "DELETE")
    st2, plist = sim_http("/api/ecu/profiles")
    check("the simulator back on default.json, the second car's profile deleted", SIM_PROFILE not in plist and '"active":"default.json"' in plist.replace(" ", ""), (st, plist[:160]))
    v1 = vehicles()
    strip = lambda v: [(c.get("key"), c.get("name"), c.get("protocol"), c.get("chip_protocol"), c.get("profile")) for c in v.get("vehicles", [])]
    check("the vehicle store as found", v1.get("current") == v0.get("current") and strip(v1) == strip(v0), (v1.get("current"), strip(v1)))
    st, cfg1 = http("/api/autopid/config", timeout=30)
    check("the live tables as found", cfg1 == cfg0)
    g = guard()
    print("%s guard at the end: %s (as found the store read %r)" % (now(), g, as_text), flush=True)
    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    print("PROTOCOL SAVE %s (%d checks, %d failed)" % ("PASS" if not fails else "FAIL", len(CHECKS), len(fails)), flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
