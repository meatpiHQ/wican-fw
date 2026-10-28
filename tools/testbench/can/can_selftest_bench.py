#!/usr/bin/env python3
"""CAN self-test and chip diagnostics bench  ->  CAN SELFTEST PASS / FAIL  (issue #1023)

Proves, on the bench bus (the ECU simulator beside the DUT), the routes behind
the OBD chip and CAN self-test cards of System > Maintenance:

  selftest as found   POST /api/can/selftest -> 202, the result on GET: PASS,
                      the three steps ok, the chip read 1726 rpm from the ESP32,
                      the bus reported live or silent as the simulator has it
  selftest flipped    the simulator's broadcast toggled (its settings submit,
                      a 20 s reboot): the other bus state, PASS again; a second
                      POST while a run is in flight answers 409
  chip report         GET /api/obd_chip/report: the 11 commands, VTVERS with
                      the chip's version, STSLCS with the sleep summary, ATPPS
                      with the programmable parameters, nothing timed out
  wire tools          POST /api/obd_chip/probe {kind:"probe"} -> 202, the
                      result on GET: ATI and VTVERS with a prompt at 2000000,
                      no prompt at 115200 (the chip answers the garbled
                      bytes with garbage of its own, read at the wrong
                      speed; bench: 481 bytes); {kind:"scan"} names 2000000
  chip status         GET /api/obd_chip carries fw.chip (the cached VTVERS),
                      fw.builtin V2.3.24, fw.outdated false and bringup.wire
                      (empty on a chip that answered at once)
  chip on V2.3.22     (--fw22 <V2.3.22.txt>, Ali 2026-10-28) the image goes
                      to /data over /api/fs/upload and the chip is written
                      from it (force + path, ~20 s): the chip reports V2.3.22
                      and fw.outdated is true; the unforced packaged update
                      leaves it alone (0 records: the v4.52p policy); the
                      forced packaged rewrite brings it back to V2.3.24 and
                      fw.outdated false; the file is deleted
  console             (optional, --ws) `can_selftest` on /ws/cli prints the
                      last result's verdict

usage: can_selftest_bench.py [--dut localhost:18081] [--sim http://192.168.8.1]
                             [--sim-restore asis|on|off] [--skip-flip]
                             [--fw22 <path to V2.3.22.txt>]
  run on the PC with the DUT through test.ps1's ssh tunnel (the canselftest
  stage) and the simulator at its NCM address (the requests to it are bound
  to the PC's own address on that link by lib/sim_net.py: on this PC the LAN
  router is 192.168.8.1 too and answers a plain request first). The
  simulator is put back as found at the end (or as --sim-restore says).
Needs: the branch firmware on the DUT, the chip up, autopid idle or off, the
simulator on the bus (its broadcast in either state).
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sim_net  # noqa: E402  (the simulator's own link on any LAN)

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT = "http://" + arg("--dut", "localhost:18081")
SIM = arg("--sim", "http://192.168.8.1")
sim_net.install(SIM)
SIM_RESTORE = arg("--sim-restore", "asis")
SKIP_FLIP = "--skip-flip" in ARGS
FW22 = arg("--fw22", "")
CHECKS = []


def now():
    return time.strftime("%H:%M:%S")


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""))
    return bool(ok)


def http(base, path, method="GET", body=None, timeout=8):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            return exc.code, None
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def wait_up(base, budget_s):
    t0 = time.time()
    while time.time() - t0 < budget_s:
        st, _ = http(base, "/api/info", timeout=3)
        if st == 200:
            return True
        time.sleep(2)
    return False


def wait_selftest(budget_s=20):
    t0 = time.time()
    r = None
    while time.time() - t0 < budget_s:
        st, r = http(DUT, "/api/can/selftest")
        if st == 200 and isinstance(r, dict) and not r.get("running") and r.get("done"):
            return r
        time.sleep(0.5)
    return r


def run_selftest(label, expect_live):
    print("== self-test (%s) ==" % label)
    st, r = http(DUT, "/api/can/selftest", "POST", {})
    check("POST /api/can/selftest -> 202 (%s)" % label, st == 202, "%s %s" % (st, r))
    st2, r2 = http(DUT, "/api/can/selftest", "POST", {})
    if st2 == 409:
        check("a second POST while running -> 409 (%s)" % label, True)
    else:
        print("%s (the run was already over when the second POST landed: %s)" % (now(), st2))
    r = wait_selftest()
    ok = isinstance(r, dict) and r.get("done") and not r.get("refusal")
    check("the run finished without a refusal (%s)" % label, ok, r and (r.get("refusal") or r.get("verdict")))
    if not ok:
        return r
    check("verdict PASS (%s)" % label, r.get("pass") and str(r.get("verdict", "")).startswith("PASS"), r.get("verdict"))
    steps = r.get("steps", [])
    check("three steps, all ok (%s)" % label, len(steps) == 3 and all(s.get("ok") for s in steps),
          [(s.get("name"), s.get("ok"), s.get("ms")) for s in steps])
    check("the chip read 1726 rpm from the ESP32 (%s)" % label, r.get("rpm") == 1726 and r.get("reply") == "ours", (r.get("rpm"), r.get("reply")))
    bus = r.get("bus", {})
    if expect_live is not None:
        check("the bus reported %s (%s)" % ("live" if expect_live else "silent", label), bus.get("live") == expect_live, bus)
    check("the bitrate used is 500 kbit/s (%s)" % label, bus.get("baud_kbps") == 500, bus)
    check("the exchange lines carry the request and the reply (%s)" % label,
          any("010C" in l for l in r.get("lines", [])) and any("7EF" in l and "41 0C" in l for l in r.get("lines", [])),
          r.get("lines", [])[-4:])
    print("%s took %s ms: %s" % (now(), r.get("took_ms"), r.get("verdict")))
    return r


def http_raw(base, path, data, timeout=60):
    req = urllib.request.Request(base + path, data=data, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()[:200].decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:200].decode("utf-8", "replace")
    except Exception as exc:
        return 0, str(exc)


def wait_update(limit):
    """the fw.update object once the job started by a POST has ended"""
    t0 = time.time()
    seen = False
    fw = None
    while time.time() - t0 < limit:
        st, oc = http(DUT, "/api/obd_chip")
        fw = (oc or {}).get("fw", {}).get("update") if st == 200 and isinstance(oc, dict) else None
        if fw and fw.get("running"):
            seen = True
        elif fw and fw.get("done") and (seen or time.time() - t0 > 3):
            return fw
        time.sleep(0.5)
    return fw


def wait_ready(limit):
    """GET /api/obd_chip once the chip is ready and unclaimed"""
    t0 = time.time()
    oc = {}
    while time.time() - t0 < limit:
        st, oc = http(DUT, "/api/obd_chip")
        if st == 200 and isinstance(oc, dict) and oc.get("ready") and oc.get("claim") == "none":
            return oc
        time.sleep(0.5)
    return oc if isinstance(oc, dict) else {}


def sim_broadcast(value):
    st, s = http(SIM, "/api/settings/ecu_sim")
    if st != 200 or not isinstance(s, dict):
        return None
    if s.get("broadcast_enabled") == value:
        return value
    v = {k: x for k, x in s.items() if k not in ("degraded", "pending_reboot")}
    v["broadcast_enabled"] = value
    http(SIM, "/api/settings/ecu_sim", "PUT", v)
    http(SIM, "/api/settings/submit", "POST", {})
    time.sleep(20)
    wait_up(SIM, 90)
    time.sleep(5)
    st, s = http(SIM, "/api/settings/ecu_sim")
    return s.get("broadcast_enabled") if isinstance(s, dict) else None


def main():
    print("%s CAN self-test bench: DUT %s, simulator %s" % (now(), DUT, SIM))
    st, info = http(DUT, "/api/info")
    if st != 200:
        print("CAN SELFTEST FAIL: no DUT at %s (%s)" % (DUT, info))
        sys.exit(1)
    print("DUT %s %s" % (info.get("fw_build"), info.get("partition")))
    st, sim = http(SIM, "/api/settings/ecu_sim")
    sim_bc = sim.get("broadcast_enabled") if isinstance(sim, dict) else None
    print("simulator broadcast as found:", sim_bc)

    # ---- chip status and the cached version ----
    print("== chip status ==")
    st, oc = http(DUT, "/api/obd_chip")
    check("GET /api/obd_chip answers with the chip up", st == 200 and oc.get("ready"), oc and {"ready": oc.get("ready"), "baud": oc.get("baud")})
    fw = (oc or {}).get("fw", {})
    check("fw.chip carries the cached VTVERS", str(fw.get("chip", "")).startswith("MIC3624"), fw.get("chip"))
    check("fw.builtin is V2.3.24 with the v4.52p list", fw.get("builtin") == "V2.3.24" and "V2.3.22" in str(fw.get("current")), (fw.get("builtin"), fw.get("current")))
    check("bringup.wire is empty on a chip that answered at once", (oc or {}).get("bringup", {}).get("wire") == [], (oc or {}).get("bringup"))
    check("fw.outdated is false on a chip at the packaged version", fw.get("outdated") is False, fw)

    # ---- the self-test, as found ----
    run_selftest("as found", sim_bc if sim_bc in (True, False) else None)

    # ---- flipped ----
    if not SKIP_FLIP and sim_bc in (True, False):
        print("== flipping the simulator's broadcast to %s ==" % (not sim_bc))
        got = sim_broadcast(not sim_bc)
        check("the simulator's broadcast flipped", got == (not sim_bc), got)
        if got == (not sim_bc):
            time.sleep(3)
            run_selftest("flipped", not sim_bc)

    # ---- the chip report ----
    print("== chip report ==")
    st, rep = http(DUT, "/api/obd_chip/report", timeout=12)
    items = (rep or {}).get("items", []) if isinstance(rep, dict) else []
    by = {it.get("cmd"): it for it in items}
    check("GET /api/obd_chip/report -> 200 with 11 items", st == 200 and len(items) == 11, (st, len(items)))
    check("VTVERS names the chip's firmware", "MIC3624" in str(by.get("VTVERS", {}).get("resp")), by.get("VTVERS"))
    check("STSLCS carries the sleep summary", "VL_SLEEP" in str(by.get("STSLCS", {}).get("resp")) and "\n" in str(by.get("STSLCS", {}).get("resp")), str(by.get("STSLCS", {}).get("resp"))[:120])
    check("ATPPS carries the programmable parameters", "0E:" in str(by.get("ATPPS", {}).get("resp")) and "0F:" in str(by.get("ATPPS", {}).get("resp")), str(by.get("ATPPS", {}).get("resp"))[:120])
    check("no command timed out", all(not it.get("err") for it in items), [(it.get("cmd"), it.get("err")) for it in items if it.get("err")])

    # ---- the wire tools ----
    print("== wire tools ==")
    st, r = http(DUT, "/api/obd_chip/probe", "POST", {"kind": "probe", "baud": 0, "seconds": 0})
    check("POST /api/obd_chip/probe probe -> 202", st == 202, (st, r))
    t0 = time.time()
    pr = None
    while time.time() - t0 < 30:
        st, pr = http(DUT, "/api/obd_chip/probe")
        if st == 200 and isinstance(pr, dict) and not pr.get("running") and pr.get("done"):
            break
        time.sleep(0.5)
    lines = (pr or {}).get("lines", [])
    at2m = [l for l in lines if l.get("baud") == 2000000]
    at115 = [l for l in lines if l.get("baud") == 115200]
    check("ATI and VTVERS answer with a prompt at 2000000", len(at2m) == 2 and all(l.get("prompt") for l in at2m), at2m)
    check("no prompt at 115200 on a chip at 2 Mbaud", len(at115) == 2 and all(not l.get("prompt") for l in at115), at115)
    st, r = http(DUT, "/api/obd_chip/probe", "POST", {"kind": "scan", "baud": 0, "seconds": 0})
    check("POST scan -> 202", st == 202, (st, r))
    t0 = time.time()
    while time.time() - t0 < 40:
        st, pr = http(DUT, "/api/obd_chip/probe")
        if st == 200 and isinstance(pr, dict) and not pr.get("running") and pr.get("done"):
            break
        time.sleep(0.5)
    check("the scan names 2000000", (pr or {}).get("auto", {}).get("detected") == 2000000, (pr or {}).get("auto"))
    st, oc = http(DUT, "/api/obd_chip")
    check("the chip is back to ready after the tools", st == 200 and oc.get("ready") and oc.get("claim") == "none", oc and (oc.get("ready"), oc.get("claim")))

    # ---- a chip left on V2.3.22: the warning (--fw22) ----
    if FW22:
        print("== a chip left on V2.3.22 (%s) ==" % FW22)
        data = open(FW22, "rb").read()
        st, r = http_raw(DUT, "/api/fs/upload?path=/data/V2.3.22.txt", data)
        check("V2.3.22.txt uploaded to /data (%d bytes)" % len(data), st in (200, 201), (st, r))
        st, r = http(DUT, "/api/obd_chip/update", "POST", {"force": True, "path": "/data/V2.3.22.txt"})
        check("POST update {force, path} -> 202", st == 202, (st, r))
        fw = wait_update(90)
        check("the .22 write ends ESP_OK with every record", bool(fw) and fw.get("last") == "ESP_OK" and fw.get("lines") == fw.get("total") and fw.get("total", 0) > 0, fw)
        oc = wait_ready(40)
        check("the chip reports V2.3.22 after it", "V2.3.22" in str(oc.get("fw", {}).get("chip")), oc.get("fw"))
        check("fw.outdated is true on a chip at V2.3.22", oc.get("fw", {}).get("outdated") is True, oc.get("fw"))
        st, r = http(DUT, "/api/obd_chip/update", "POST", {"force": False})
        check("POST the packaged update without force -> 202", st == 202, (st, r))
        fw = wait_update(30)
        check("it leaves a .22 chip alone (ESP_OK, 0 records: the v4.52p policy)", bool(fw) and fw.get("last") == "ESP_OK" and fw.get("lines") == 0, fw)
        oc = wait_ready(40)
        check("still V2.3.22 and still outdated", "V2.3.22" in str(oc.get("fw", {}).get("chip")) and oc.get("fw", {}).get("outdated") is True, oc.get("fw"))
        st, r = http(DUT, "/api/obd_chip/update", "POST", {"force": True})
        check("POST the packaged update with force -> 202", st == 202, (st, r))
        fw = wait_update(90)
        check("the forced packaged rewrite ends ESP_OK with every record", bool(fw) and fw.get("last") == "ESP_OK" and fw.get("lines") == fw.get("total") and fw.get("total", 0) > 0, fw)
        oc = wait_ready(40)
        check("the chip is back on V2.3.24 and not outdated", "V2.3.24" in str(oc.get("fw", {}).get("chip")) and oc.get("fw", {}).get("outdated") is False, oc.get("fw"))
        st, r = http(DUT, "/api/fs/file?path=/data/V2.3.22.txt", "DELETE")
        check("V2.3.22.txt deleted from /data", st == 200, (st, r))
    else:
        print("(no --fw22 <V2.3.22.txt>: the warning on a chip left on V2.3.22 is not exercised)")

    # ---- restore the simulator ----
    target = {"asis": sim_bc, "on": True, "off": False}.get(SIM_RESTORE, sim_bc)
    if target in (True, False):
        st, s = http(SIM, "/api/settings/ecu_sim")
        if isinstance(s, dict) and s.get("broadcast_enabled") != target:
            print("== restoring the simulator's broadcast to %s ==" % target)
            sim_broadcast(target)

    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    print("CAN SELFTEST %s (%d checks, %d failed)" % ("PASS" if not fails else "FAIL", len(CHECKS), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
