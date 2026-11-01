#!/usr/bin/env python3
"""The OBD chip's protocol store under v6  ->  ATTP STORE PASS / FAIL  (issue #1023)

The MIC3624's memory function is ON after every reset, and with it on a
protocol switch that a request then answers on is written into the chip's
non-volatile protocol store, RAM-only ATTP or not (bench 2026-11-01,
internal/TASK_obd_chip_protocol_store.md): the Audi MEB profile flipped the
chip 6 <-> 7 seven times per poll cycle against a 10k-cycle EEPROM. The fix
sends ATM0 at the bring-up and ahead of every protocol switch on the
request path (obd_chip_request: AutoPID, the scans, the UDS tool, J2534).
The store is read back the only way the chip offers: ATDPN right after a
reset (ATZ) is the protocol the chip comes back on.

  as found      the store (ATZ, ATE0, ATDPN over /ws/obd) and the simulator's
                id format; the other protocol is the flip target (6 <-> 7,
                both 500 kbit/s, so the DUT's CAN setting never changes)
  engine switch the simulator on the other id format; POST /api/autopid/test
                with init ATSP<other> (the guard sends ATTP<other>) and cmd
                0100, answered on the new protocol; then ATZ + ATDPN must
                read the store AS FOUND, and eeprom_guard.memory_off must
                have stepped once per switch word in the transcript. On an
                image without the fix this leg reads <other>: the
                reproduction.
  bring-up      a planned restart; memory_off reads 1 (the bring-up's ATM0);
                then the app path's own switch over /ws/obd (ATTP<other> +
                0100 answered, no ATM0 from the driver on that path) and the
                read-back must still be as found: the bring-up's ATM0 held
  app reset     (an image with app_resets on /api/obd_chip) the app's ATZ
                over /ws/obd is answered as a warm start: the banner alone,
                one prompt, no OK, no stray bytes after it, under 800 ms
                (a real ATZ took 1500 on this bridge), the next answers the
                app's own (nothing shifted), app_resets and memory_off +1;
                ATWS and ATD from the app the same
  canary        (--skip-canary drops it) an app's ATZ first, then the same
                switch over /ws/obd. With the app reset covered the store
                stays as found (the app's session runs with the memory
                function off); on the image before that (memory_off but no
                app_resets) it DOES read <other>: the limit that build had,
                and the proof that the read-back sees a write (two store
                writes on that image)
  power cycle   (--psu COMxx) the store as found across a PSU power cycle
  restore       the store put back when a leg moved it, the simulator back
                as found, AutoPID back on when the bench switched it off

usage: attp_store_bench.py [--dut localhost:18081] [--sim http://192.168.8.1]
                           [--skip-bringup] [--skip-canary] [--psu COMxx]
  run on the PC with the DUT through test.ps1's ssh tunnel (the attpstore
  stage) and the simulator at its NCM address (lib/sim_net.py binds the
  requests to the simulator's own link). The simulator reboots at every id
  format change (about 20 s each; 2 on the fixed image, up to 6 on one
  without the fix).
Needs: the branch firmware on the DUT, the chip up, the simulator on the bus
at 500 kbit/s, the /ws/obd bridge (br_obd, the canonical bench settings).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import bench_bus  # noqa: E402  (the simulator's id format; installs sim_net)
import wsmin      # noqa: E402  (the /ws/obd bridge)

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
SIM = arg("--sim", bench_bus.SIM_URL)
SKIP_BRINGUP = "--skip-bringup" in ARGS
SKIP_CANARY = "--skip-canary" in ARGS
PSU = arg("--psu", "")
CHECKS = []
FORMAT = {"6": "11bit", "7": "29bit"}


def now():
    return time.strftime("%H:%M:%S")


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""), flush=True)
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


def chip():
    st, oc = http(DUT, "/api/obd_chip")
    return oc if st == 200 and isinstance(oc, dict) else {}


def guard():
    return chip().get("eeprom_guard", {})


def wait_dut(limit):
    """/api/info answers again (after a planned restart or a power cycle)"""
    t0 = time.time()
    time.sleep(5)
    while time.time() - t0 < limit:
        st, _ = http(DUT, "/api/info", timeout=3)
        if st == 200:
            return True
        time.sleep(2)
    return False


def wait_ready(limit):
    """GET /api/obd_chip once the chip is ready and unclaimed"""
    t0 = time.time()
    oc = {}
    while time.time() - t0 < limit:
        oc = chip()
        if oc.get("ready") and oc.get("claim") == "none":
            return oc
        time.sleep(0.5)
    return oc


class ElmWs:
    """The chip through the /ws/obd bridge, like a browser terminal: one
    command, the bytes up to the prompt."""

    def __init__(self):
        host, _, port = DUT_HP.partition(":")
        self.ws = wsmin.WS(host, int(port or 80), "/ws/obd", timeout=3)
        self.ws.settimeout(0.2)
        self.opcode = wsmin.OP_BIN
        # the bridge takes binary or text frames; find out with a harmless CR
        if not self.cmd("", wait_s=1.0):
            self.opcode = wsmin.OP_TEXT

    def drain(self):
        self.ws.pump()

    def cmd(self, text, wait_s=2.0):
        self.drain()
        self.ws.send((text + "\r").encode(), self.opcode)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < wait_s:
            frames = self.ws.pump()
            for _, payload in frames:
                buf += payload
            if b">" in buf:
                break
            if not frames:
                time.sleep(0.02)
        return buf.decode(errors="replace").replace("\r", " ").replace("\n", " ").strip()

    def close(self):
        self.ws.close()


def reply_of(text):
    """the chip's words before the prompt, echo or not"""
    return text.split(">")[0].strip()


def proto_digit(dpn):
    """ATDPN's answer as the protocol digit: '6', 'A6' -> '6'; '34 [ST]' -> '34'"""
    s = reply_of(dpn).replace("ATDPN", "").strip()
    s = re.sub(r"\s*\[.*\]$", "", s).strip()
    if len(s) == 2 and s[0] == "A":
        s = s[1:]
    return s


def store_read(e):
    """the stored protocol: the chip reset by ATZ comes back on it. Leaves
    the echo off and the memory function ON (the reset's default)."""
    banner = e.cmd("ATZ", wait_s=3.0)
    e.cmd("ATE0")
    dpn = e.cmd("ATDPN")
    return proto_digit(dpn), banner


def answered(text):
    r = reply_of(text).replace(" ", "")
    return "4100" in r


def sim_format_to(proto):
    """the simulator's id format for the protocol: a reboot of about 20 s
    when it changes, nothing when it is there already"""
    want = FORMAT[proto]
    t0 = time.time()
    prev, run = bench_bus.sim_set(id_format=want, base=SIM)
    took = time.time() - t0
    ok = bool(run) and run.get("id_format") == want and run.get("running")
    print("%s simulator on %s (%s, %.0f s)" % (now(), want, "ok" if ok else run, took), flush=True)
    return ok


def memory_on_unguarded():
    """the bench's own way to a memory-ON chip on an image that hides an ATM0
    behind every app reset: the bare-wire probe sends the word it is given,
    unguarded (ATM1 is RAM-only; the chip answers OK with a prompt)"""
    st, r = http(DUT, "/api/obd_chip/probe", "POST", {"kind": "probe", "baud": 2000000, "seconds": 0, "cmd": "ATM1"})
    if st != 202:
        print("%s probe ATM1 refused: %s %s" % (now(), st, r), flush=True)
        return False
    pr = None
    t0 = time.time()
    while time.time() - t0 < 30:
        time.sleep(0.5)
        st, pr = http(DUT, "/api/obd_chip/probe")
        if st == 200 and isinstance(pr, dict) and pr.get("done") and not pr.get("running"):
            break
    lines = (pr or {}).get("lines", [])
    ok = any(ln.get("cmd") == "ATM1" and ln.get("prompt") for ln in lines)
    print("%s probe ATM1 (memory on for the restore): %s" % (now(), [(ln.get("cmd"), ln.get("prompt"), ln.get("text")) for ln in lines]), flush=True)
    wait_ready(20)
    return ok


def restore_store(e, as_found, label, covered=False):
    """a leg that moved the store puts it back: the simulator on the as-found
    format, one switch with an answered request under memory ON (the ATZ of
    the read-back left it on; on an image that hides an ATM0 behind every
    app reset the probe's unguarded ATM1 turns it on), the read-back"""
    print("%s the store moved during '%s': putting it back to %s" % (now(), label, as_found), flush=True)
    if not sim_format_to(as_found):
        return False
    if covered and not memory_on_unguarded():
        return False
    e.cmd("ATTP" + as_found)
    r = e.cmd("0100", wait_s=3.0)
    got, _ = store_read(e)
    return check("the store put back to %s after '%s'" % (as_found, label), got == as_found and answered(r),
                 "0100 %r, ATDPN after ATZ %s" % (reply_of(r), got))


def test_switch(other):
    """the engine's switch: POST /api/autopid/test with the profile's own
    words (ATSP<other>, which the guard sends as ATTP<other>) and 0100"""
    st, r = http(DUT, "/api/autopid/test", "POST", {"cmd": "0100", "init": "ATSP" + other}, timeout=30)
    tr = r.get("transcript", "") if isinstance(r, dict) else ""
    lines = [ln for ln in str(tr).replace("\r", "\n").split("\n") if ln.strip()]
    switches = [ln for ln in lines if ln.lstrip().startswith(">") and
                re.match(r"^>\s*AT\s*[ST]\s*P\s*[0-9A-Ca-c]", ln.lstrip())]
    return st, r, lines, switches


def main():
    print("%s ATTP store bench: DUT %s, simulator %s" % (now(), DUT, SIM), flush=True)
    st, info = http(DUT, "/api/info")
    if st != 200:
        print("ATTP STORE FAIL: no DUT at %s (%s)" % (DUT, info))
        sys.exit(1)
    print("DUT %s %s" % (info.get("fw_build"), info.get("partition")), flush=True)
    oc = wait_ready(30)
    if not check("the chip is up and unclaimed", oc.get("ready") and oc.get("claim") == "none",
                 (oc.get("ready"), oc.get("claim"), oc.get("baud"))):
        print("ATTP STORE FAIL: the chip is down")
        sys.exit(1)
    g0 = guard()
    fixed = "memory_off" in g0
    covered = "app_resets" in g0
    print("%s guard counters as found: %s" % (now(), g0), flush=True)
    print("%s image %s" % (now(), "CARRIES the fix (memory_off present)" if fixed else
                           "WITHOUT the fix: the engine and bring-up legs are expected to write (the reproduction)"), flush=True)
    print("%s the app's reset %s" % (now(), "COVERED (app_resets present): ATZ answered as a warm start with ATM0 hidden behind it"
                                     if covered else "not covered on this image: the canary is expected to write"), flush=True)

    # ---- AutoPID off for the run (it would re-send its prelude and poll) ----
    st, aps = http(DUT, "/api/settings/autopid")
    ap_was_on = bool(isinstance(aps, dict) and aps.get("enabled"))
    if ap_was_on:
        v = {k: x for k, x in aps.items() if k not in ("degraded", "pending_reboot")}
        v["enabled"] = False
        http(DUT, "/api/settings/autopid", "PUT", v)
        st, r = http(DUT, "/api/settings/submit", "POST", {})
        print("%s autopid.enabled -> false, submit: %s %s (planned restart)" % (now(), st, r), flush=True)
        check("AutoPID switched off for the run (planned restart)", wait_dut(120) and wait_ready(60).get("ready"))

    # ---- the simulator and the store as found ----
    sim = bench_bus.sim_get(base=SIM)
    sim_prev = {"bitrate": sim["bitrate"], "id_format": sim["id_format"], "enabled": sim["enabled"]}
    print("%s simulator as found: %s" % (now(), sim_prev), flush=True)
    if not check("the simulator runs at 500 kbit/s (protocols 6 and 7)", sim_prev["bitrate"] == "500" and sim_prev["enabled"], sim_prev):
        print("ATTP STORE FAIL: put the simulator on 500 kbit/s first (bench_bus.py sim --restore)")
        sys.exit(1)

    e = ElmWs()
    r = e.cmd("ATI")
    check("the /ws/obd bridge reaches the chip (ATI)", "ELM327" in r or "MIC" in r, r)
    as_found, banner = store_read(e)
    check("the store as found is a pinned CAN protocol (6 or 7)", as_found in FORMAT, "ATDPN after ATZ %r, banner %r" % (as_found, reply_of(banner)))
    if as_found not in FORMAT:
        print("ATTP STORE FAIL: the store reads %r; put it back to 6 by hand first (ATTP6 + 0100 answered, ATZ)" % as_found)
        e.close()
        sys.exit(1)
    other = "7" if as_found == "6" else "6"
    print("%s store as found %s (%s), the flip target %s (%s)" % (now(), as_found, FORMAT[as_found], other, FORMAT[other]), flush=True)

    # ---- an app's reset: a warm start, the ATM0 hidden, nothing shifted ----
    if covered:
        print("== an app's reset: ATZ answered as a warm start, ATM0 hidden behind it, no shifted answer ==", flush=True)
        g_a = guard()
        t0 = time.time()
        r = e.cmd("ATZ", wait_s=3.0)
        ms = (time.time() - t0) * 1000
        time.sleep(0.3)
        stray = b"".join(p for _, p in e.ws.pump())
        before = reply_of(r)
        check("ATZ answered with the banner alone: one prompt, no OK, nothing after it within 300 ms", "ELM327" in before and "OK" not in before and r.count(">") == 1 and stray == b"", (r, stray))
        check("the warm start answers within 800 ms (a real ATZ took about 1500 on this bridge)", ms < 800, "%.0f ms" % ms)
        r1 = e.cmd("ATE0")
        r2 = e.cmd("ATI")
        r3 = e.cmd("ATDPN")
        check("the next answers are the app's own (ATE0 OK, ATI the banner, ATDPN the store): nothing shifted", "OK" in reply_of(r1) and "ELM327" not in reply_of(r1) and "ELM327" in reply_of(r2) and "OK" not in reply_of(r2) and proto_digit(r3) == as_found, (reply_of(r1), reply_of(r2), proto_digit(r3)))
        g_b = guard()
        check("app_resets +1 and memory_off +1 behind the app's ATZ", g_b.get("app_resets", 0) - g_a.get("app_resets", 0) == 1 and g_b.get("memory_off", 0) - g_a.get("memory_off", 0) == 1, (g_a, g_b))
        rw = e.cmd("ATWS", wait_s=3.0)
        rw1 = e.cmd("ATE0")
        rd = e.cmd("ATD")
        rd1 = e.cmd("ATE0")
        g_c = guard()
        check("ATWS and ATD from the app: the banner / OK once each, their ATM0 hidden too (app_resets +2, memory_off +2)", "ELM327" in reply_of(rw) and rw.count(">") == 1 and "OK" in reply_of(rd) and "ELM327" not in reply_of(rd) and rd.count(">") == 1 and "OK" in reply_of(rw1) and "OK" in reply_of(rd1) and g_c.get("app_resets", 0) - g_b.get("app_resets", 0) == 2 and g_c.get("memory_off", 0) - g_b.get("memory_off", 0) == 2, (reply_of(rw), reply_of(rd), g_b, g_c))
        check("hidden_bytes counts the three hidden answers", g_c.get("hidden_bytes", 0) - g_a.get("hidden_bytes", 0) >= 3 * 4, (g_a.get("hidden_bytes"), g_c.get("hidden_bytes")))

    # ---- the engine's switch (the fix's main leg) ----
    print("== the engine's switch: ATSP%s in a test-a-PID init, 0100 answered ==" % other, flush=True)
    check("the simulator on the other id format", sim_format_to(other))
    g1 = guard()
    st, r, lines, switches = test_switch(other)
    raw = str(r.get("raw", "")) if isinstance(r, dict) else ""
    check("POST /api/autopid/test {init ATSP%s, cmd 0100} answers 200 ok" % other, st == 200 and isinstance(r, dict) and r.get("ok"), (st, r if not isinstance(r, dict) else {k: r.get(k) for k in ("ok", "error", "elapsed_ms")}))
    check("0100 answered on protocol %s (41 00 in the reply)" % other, "4100" in raw.replace(" ", ""), raw[:80])
    check("the transcript carries ATTP%s (the guard's form of the profile's ATSP)" % other, any("TP" + other in ln.replace(" ", "").upper() for ln in switches), switches)
    for ln in lines:
        print("    " + ln, flush=True)
    g2 = guard()
    if fixed:
        delta = g2.get("memory_off", 0) - g1.get("memory_off", 0)
        check("memory_off stepped once per switch word (%d switch(es), +%d)" % (len(switches), delta), delta == len(switches) and delta >= 1, (g1, g2))
    got, _ = store_read(e)
    moved = not check("the engine's switch did not write the store (ATDPN after ATZ = %s as found)" % as_found, got == as_found, "reads %s" % got)
    if moved:
        restore_store(e, as_found, "the engine's switch", covered)

    # ---- the bring-up's ATM0 ----
    if not SKIP_BRINGUP:
        print("== the bring-up: a planned restart, then the app path's own switch ==", flush=True)
        st, r = http(DUT, "/api/restart", "POST", {})
        check("POST /api/restart -> 200 (planned restart)", st == 200, (st, r))
        e.close()
        ok = wait_dut(120)
        oc = wait_ready(60) if ok else {}
        check("the DUT is back with the chip up", ok and oc.get("ready") and oc.get("claim") == "none", (ok, oc.get("ready"), oc.get("claim")))
        gb = guard()
        if fixed:
            check("the bring-up sent one ATM0 and the chip took it (memory_off 1)", gb.get("memory_off") == 1, gb)
        e = ElmWs()
        check("the simulator still on the other id format", sim_format_to(other))
        r1 = e.cmd("ATTP" + other)
        r2 = e.cmd("0100", wait_s=3.0)
        cur = proto_digit(e.cmd("ATDPN"))
        check("the app path's switch: ATTP%s OK, 0100 answered, the chip on %s" % (other, other), "OK" in r1 and answered(r2) and cur == other, (reply_of(r1), reply_of(r2), cur))
        got, _ = store_read(e)
        moved = not check("the bring-up's ATM0 held: the store still %s after an app-path switch with an answer" % as_found, got == as_found, "reads %s" % got)
        if moved:
            restore_store(e, as_found, "the bring-up leg", covered)
    else:
        print("(--skip-bringup: the bring-up leg is not exercised)", flush=True)

    # ---- the canary: the app path after its own reset ----
    if not SKIP_CANARY:
        print("== the canary: an app's ATZ, then its own switch ==", flush=True)
        check("the simulator still on the other id format", sim_format_to(other))
        e.cmd("ATZ", wait_s=3.0)
        e.cmd("ATE0")
        r1 = e.cmd("ATTP" + other)
        r2 = e.cmd("0100", wait_s=3.0)
        check("after an app's ATZ: ATTP%s OK, 0100 answered" % other, "OK" in r1 and answered(r2), (reply_of(r1), reply_of(r2)))
        got, _ = store_read(e)
        if covered:
            moved = not check("an app's switch after its own ATZ no longer writes the store (reads %s as found: the warm start left the memory function off)" % as_found, got == as_found, "reads %s" % got)
            if moved:
                restore_store(e, as_found, "the canary", covered)
        else:
            check("the app path writes the store after its own reset (reads %s: the limit of this image, and the read-back sees a write)" % other, got == other, "reads %s" % got)
            restore_store(e, as_found, "the canary")
    else:
        print("(--skip-canary: the app path after its own reset is not exercised)", flush=True)

    # ---- across a power cycle (optional) ----
    if PSU:
        print("== the store across a PSU power cycle (%s) ==" % PSU, flush=True)
        e.close()
        from owon_psu import OwonPsu  # noqa: E402  (pyserial, the bench PSU)
        with OwonPsu(PSU) as psu:
            try:
                psu.meas_current()
            except (AssertionError, ValueError):
                pass
            psu.output(False)
            time.sleep(4)
            psu.output(True)
        ok = wait_dut(120)
        oc = wait_ready(60) if ok else {}
        check("the DUT is back after the power cycle with the chip up", ok and oc.get("ready"), (ok, oc.get("ready")))
        e = ElmWs()
        got, _ = store_read(e)
        check("the store reads %s across the power cycle" % as_found, got == as_found, "reads %s" % got)

    # ---- restore ----
    print("== restore ==", flush=True)
    got, _ = store_read(e)
    check("the store is as found at the end (%s)" % as_found, got == as_found, "reads %s" % got)
    e.cmd("ATE0")
    e.close()
    t0 = time.time()
    prev, run = bench_bus.sim_set(base=SIM, **sim_prev)
    check("the simulator back as found (%s)" % sim_prev["id_format"], bool(run) and run.get("id_format") == sim_prev["id_format"], "%s, %.0f s" % (run, time.time() - t0))
    if ap_was_on:
        st, aps = http(DUT, "/api/settings/autopid")
        v = {k: x for k, x in aps.items() if k not in ("degraded", "pending_reboot")}
        v["enabled"] = True
        http(DUT, "/api/settings/autopid", "PUT", v)
        st, r = http(DUT, "/api/settings/submit", "POST", {})
        print("%s autopid.enabled -> true, submit: %s %s (planned restart)" % (now(), st, r), flush=True)
        check("AutoPID put back on (planned restart)", wait_dut(120) and wait_ready(60).get("ready"))
    print("%s guard counters at the end: %s" % (now(), guard()), flush=True)

    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    print("ATTP STORE %s (%d checks, %d failed)" % ("PASS" if not fails else "FAIL", len(CHECKS), len(fails)), flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
