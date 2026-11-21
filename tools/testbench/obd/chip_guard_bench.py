#!/usr/bin/env python3
"""The OBD chip's firmware update, guarded -> CHIP GUARD PASS.

GitHub issue #1075 (2026-11-21, Ali): the user's update of the OBD chip is
accepted only in a start that began with the power coming on (the page tells
the user to unplug the adapter for a minute), nobody else talks to the chip
from the update's first step until the chip is up again, and a warm start
and a closed protocol are the last words the chip hears before it is
written.

The bench puts the chip on V2.3.22 (the V2.3.22 image as /data/V2.3.99.txt,
the firmware's "the name is the version" contract, with the bench's
`any_start` word), then walks the user's way:

  G1  the chip on V2.3.22, flagged outdated
  G2  a planned restart: `fw.power_on` false, POST /api/obd_chip/update {}
      is 409 with `power_cycle`, no job ran, the chip untouched
  G3  the supply off for --off-s seconds (60), on again: the start's reset
      reason is power-on, `fw.power_on` true, the chip ready, no reset pulse
  G4  POST {} is 202; a second POST meanwhile 409; AutoPID reports
      `paused_chip_fw` while the job runs and counts no failed poll; a test
      request and a wire probe in the middle are refused; the chip comes
      back on the packaged version through a fresh bring-up
  G5  afterwards: AutoPID polls again; the log ring holds the pause ahead of
      the chip's answer, the warm start ahead of the write and the resume
      behind the bring-up; no reset pulse, no fault code, no E line, no
      unexpected reset

  python chip_guard_bench.py --dut localhost:18081 [--fw22 <V2.3.22.txt>]
         [--off-s 60] [--psu auto]
About 3 minutes. Expected final line: CHIP GUARD PASS

The chip is written twice (down to V2.3.22, up to the packaged version). A
unit found on V2.3.22 ends on the packaged version: the bench never
downgrades a chip for good.
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
from bench_psu import open_psu  # noqa: E402
from wsmin import WS, OP_TEXT  # noqa: E402

fails = []
passes = []


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


def default_fw22():
    env = os.environ.get("MEATPI_COMPONENTS_PATH")
    roots = [env] if env else []
    roots.append(os.path.join(HERE, "..", "..", "..", "..", "..", "components", "meatpi-components", "components"))
    for root in roots:
        p = os.path.join(root, "obd_chip_manager", "obd_fw", "V2.3.22.txt")
        if os.path.exists(p):
            return os.path.abspath(p)
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--fw22", default=default_fw22(), help="the V2.3.22 vendor image")
    ap.add_argument("--off-s", type=float, default=60, help="seconds without power (the page says one minute)")
    ap.add_argument("--psu", default="auto")
    a = ap.parse_args()
    base = "http://" + a.dut
    t_start = time.time()

    def http(path, method="GET", body=None, raw=None, timeout=8):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Content-Type": "application/octet-stream" if raw is not None else "application/json"} if data is not None else {}
        req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                txt, code = r.read().decode("utf-8", "replace"), r.status
        except urllib.error.HTTPError as e:
            txt, code = e.read().decode("utf-8", "replace"), e.code
        except Exception as e:  # noqa: BLE001  (a restart in flight, the tunnel)
            return 0, str(e)
        try:
            return code, json.loads(txt)
        except ValueError:
            return code, txt

    def get(path, timeout=6):
        code, js = http(path, timeout=timeout)
        return js if code == 200 and isinstance(js, dict) else None

    def chip():
        return get("/api/obd_chip") or {}

    def fw(oc=None):
        return (oc if oc is not None else chip()).get("fw") or {}

    def ap_stats():
        return (get("/api/autopid") or {}).get("stats") or {}

    def wait_up(secs):
        end = time.time() + secs
        while time.time() < end:
            st = get("/api/status", 4)
            if st:
                return st
            time.sleep(2)
        return None

    def wait_ready(secs):
        end = time.time() + secs
        oc = {}
        while time.time() < end:
            oc = chip()
            if oc.get("ready"):
                return oc
            time.sleep(1)
        return oc

    def wait_job(secs):
        """From the POST to the chip ready behind the job; (seen running, chip)."""
        end = time.time() + secs
        seen = False
        oc = {}
        while time.time() < end:
            time.sleep(0.5)
            oc = chip()
            up = fw(oc).get("update") or {}
            if up.get("running"):
                seen = True
            elif oc and seen and oc.get("ready"):
                return seen, oc
        return seen, oc

    def console(line, wait_s=1.5):
        """One console line over /ws/cli; what came back (a restart it asks for ends the socket)."""
        host, _, port = a.dut.partition(":")
        out = b""
        try:
            ws = WS(host, int(port or 80), "/ws/cli", timeout=6)
            ws.send((line + "\n").encode(), OP_TEXT)
            ws.settimeout(0.4)
            end = time.time() + wait_s
            while time.time() < end:
                try:
                    _op, payload = ws.recv_frame()
                    out += payload or b""
                except OSError:
                    continue
            ws.close()
        except Exception as e:  # noqa: BLE001
            return "<ws: %s> %s" % (e, out.decode("utf-8", "replace"))
        return out.decode("utf-8", "replace")

    def boots():
        return (get("/api/restart/history") or {}).get("boot_count")

    def ring():
        code, r = http("/api/logs/ring", timeout=10)
        return r if isinstance(r, str) else json.dumps(r)

    def pos(text, needle, start=0):
        return text.find(needle, start)

    st = wait_up(40)        # the caller's tunnel may not stand yet
    if not st:
        print("CHIP GUARD FAIL: no DUT at", a.dut)
        sys.exit(1)
    if not a.fw22 or not os.path.exists(a.fw22):
        print("CHIP GUARD FAIL: no V2.3.22 image (--fw22, or MEATPI_COMPONENTS_PATH)")
        sys.exit(1)
    oc0 = wait_ready(40)
    builtin = fw(oc0).get("builtin") or ""
    unexp0 = st.get("unexpected_resets")
    print("dut: %s, chip %s (ready %s), packaged %s, power_on %s"
          % (st.get("version"), fw(oc0).get("chip"), oc0.get("ready"), builtin, fw(oc0).get("power_on")), flush=True)
    if not oc0.get("ready") or "power_on" not in fw(oc0):
        print("CHIP GUARD FAIL: the chip is not up, or this firmware has no fw.power_on")
        sys.exit(1)
    polling = bool(ap_stats().get("running"))
    print("autopid: %s" % ("polling" if polling else "not polling (the counter checks are skipped)"), flush=True)
    psu = open_psu(a.psu, verbose=False)

    # ---- G1: down to V2.3.22 ------------------------------------------------
    with open(a.fw22, "rb") as f:
        img = f.read()
    code, r = http("/api/fs/upload?path=/data/V2.3.99.txt", "POST", raw=img, timeout=120)
    restarted = False
    try:
        if "V2.3.22" in str(fw(oc0).get("chip")):
            print("the chip is on V2.3.22 as found: no write down", flush=True)
        else:
            code, r = http("/api/obd_chip/update", "POST", {"path": "/data/V2.3.99.txt", "any_start": True}, timeout=15)
            check("G1 the V2.3.22 image is taken with the bench's word (any_start)", code == 202, "%s %s" % (code, str(r)[:120]))
            # a restart the firmware plans itself (the console's `system -r`),
            # asked for 6 s into the write: it waits for the write to end. A
            # write cut short would leave the chip in its download mode, and
            # the next start would complete it with the PACKAGED image: the
            # chip on V2.3.22 behind the restart is the proof it was not.
            time.sleep(6)
            up_mid = fw().get("update") or {}
            b_mid = boots()
            t_req = time.time()
            said = console("system -r")
            last, gone_at, misses = up_mid, None, 0
            while time.time() - t_req < 120:
                code_s, oc_r = http("/api/obd_chip", timeout=3)
                if code_s == 200 and isinstance(oc_r, dict):
                    misses = 0
                    last = (oc_r.get("fw") or {}).get("update") or last
                else:
                    misses += 1
                    if misses >= 2:
                        gone_at = time.time()
                        break
                time.sleep(0.3)
            check("G1 a restart asked for in the middle of the write waits for it: the unit stays up until every record is written",
                  up_mid.get("running") is True and gone_at is not None and (gone_at - t_req) > 8
                  and last.get("lines") == last.get("total") and (last.get("total") or 0) > 3000,
                  "asked at record %s of %s, the unit went away %s s later, last seen at record %s; console: %s"
                  % (up_mid.get("lines"), up_mid.get("total"), "%.0f" % (gone_at - t_req) if gone_at else "never",
                     last.get("lines"), said.strip()[:60]))
            restarted = gone_at is not None
            if restarted:
                time.sleep(4)
                wait_up(90)
                b_now = boots()
                print("the restart behind the write: boot count %s -> %s" % (b_mid, b_now), flush=True)
            else:
                wait_job(90)
    finally:
        for _ in range(6):                      # the unit may be in its restart
            code, _r = http("/api/fs/file?path=/data/V2.3.99.txt", "DELETE")
            if code == 200:
                break
            time.sleep(3)
    oc = wait_ready(60)
    if not check("G1 the chip is on V2.3.22, ready and flagged outdated",
                 "V2.3.22" in str(fw(oc).get("chip")) and fw(oc).get("outdated") is True and oc.get("ready") is True,
                 json.dumps({k: fw(oc).get(k) for k in ("chip", "outdated")})):
        print("CHIP GUARD FAIL: " + ", ".join(fails))
        sys.exit(1)

    # ---- G2: a start that did not begin with the power coming on -------------
    if not restarted:                           # G1's restart is one already
        http("/api/restart", "POST", {})
        time.sleep(6)
        wait_up(90)
    oc = wait_ready(60)
    time.sleep(3)                               # the start-up's own version check ends
    up_a = fw().get("update") or {}
    check("G2 behind a planned restart fw.power_on is false", fw(oc).get("power_on") is False, fw(oc).get("power_on"))
    code, r = http("/api/obd_chip/update", "POST", {}, timeout=15)
    check("G2 POST {} is refused: 409 with power_cycle and the way out in words",
          code == 409 and isinstance(r, dict) and r.get("power_cycle") is True and "unplug" in str(r.get("error")),
          "%s %s" % (code, str(r)[:140]))
    time.sleep(3)
    oc = chip()
    check("G2 no job ran and the chip is untouched",
          (fw(oc).get("update") or {}) == up_a and "V2.3.22" in str(fw(oc).get("chip")) and oc.get("ready") is True,
          json.dumps(fw(oc).get("update")))

    # ---- G3: the power comes on -----------------------------------------------
    off_s = psu.hold_off(a.off_s)
    print("the supply was off for %.0f s" % off_s, flush=True)
    time.sleep(4)
    st = wait_up(150)
    oc = wait_ready(60)
    hist = get("/api/restart/history") or {}
    recs = hist.get("records") or [{}]
    rec = max(recs, key=lambda x: x.get("seq") or 0)       # this boot's record
    check("G3 the unit came back after %.0f s without power: a power-on reset, not planned" % off_s,
          bool(st) and rec.get("reason") == "poweron" and not rec.get("planned"), json.dumps(rec)[:160])
    check("G3 fw.power_on is true, the chip ready on V2.3.22, no reset pulse",
          fw(oc).get("power_on") is True and oc.get("ready") is True and "V2.3.22" in str(fw(oc).get("chip"))
          and (oc.get("reset") or {}).get("pulses") == 0,
          json.dumps({"power_on": fw(oc).get("power_on"), "chip": fw(oc).get("chip"), "reset": oc.get("reset")}))
    unexp1 = (st or {}).get("unexpected_resets")
    err1 = ((st or {}).get("health") or {}).get("log_errors")
    if polling:
        end = time.time() + 40              # the simulator restarted with the supply: the car answers again
        while time.time() < end and not ap_stats().get("running"):
            time.sleep(2)
        time.sleep(8)

    # ---- G4: the update, everybody else watched --------------------------------
    samples = []
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            s = ap_stats()
            oc_w = chip()
            up_w = fw(oc_w).get("update") or {}
            samples.append((time.time(), s.get("paused_chip_fw"), s.get("polls_ok"), s.get("polls_failed"),
                            up_w.get("running"), oc_w.get("ready")))
            time.sleep(0.25)

    th = threading.Thread(target=watch, daemon=True)
    th.start()
    time.sleep(2.0)
    a0 = ap_stats()
    t0 = time.time()
    code, r = http("/api/obd_chip/update", "POST", {}, timeout=15)
    if not check("G4 POST {} in the power-on start is 202", code == 202 and isinstance(r, dict) and r.get("started") is True,
                 "%s %s" % (code, str(r)[:120])):
        stop.set()
        print("CHIP GUARD FAIL: " + ", ".join(fails))
        sys.exit(1)
    code, r = http("/api/obd_chip/update", "POST", {}, timeout=15)
    check("G4 a second POST meanwhile is 409 (already running)", code == 409 and "running" in str(r), "%s %s" % (code, str(r)[:80]))
    time.sleep(5)
    code, r = http("/api/autopid/test", "POST", {"cmd": "0100"}, timeout=12)
    check("G4 a test request in the middle of the write is refused (chip busy)",
          code != 200 or "busy" in str(r), "%s %s" % (code, str(r)[:140]))
    code, r = http("/api/obd_chip/probe", "POST", {"kind": "probe", "command": "ATI"}, timeout=12)
    check("G4 a wire probe in the middle of the write is 409", code == 409, "%s %s" % (code, str(r)[:100]))
    b_g4 = boots()
    code, r = http("/api/restart", "POST", {}, timeout=8)
    check("G4 a restart asked for over the route in the middle of the write is refused: 409 and why",
          code == 409 and "chip" in str(r) and "updated" in str(r), "%s %s" % (code, str(r)[:140]))
    seen, oc = wait_job(120)
    t_job = time.time() - t0
    up_b = fw(oc).get("update") or {}
    check("G4 the job ran to the end: every record, ESP_OK",
          seen and up_b.get("done") is True and up_b.get("last") == "ESP_OK" and up_b.get("lines") == up_b.get("total")
          and (up_b.get("total") or 0) > 3000, json.dumps(up_b))
    check("G4 the unit did not restart", b_g4 is not None and boots() == b_g4, "%s -> %s" % (b_g4, boots()))
    check("G4 the chip is back on %s, ready, not outdated (%.0f s after the POST)" % (builtin, t_job),
          builtin != "" and builtin in str(fw(oc).get("chip")) and fw(oc).get("outdated") is False and oc.get("ready") is True
          and t_job < 60, json.dumps({k: fw(oc).get(k) for k in ("chip", "outdated")}))
    time.sleep(6)
    stop.set()
    th.join(timeout=5)
    a1 = ap_stats()
    in_job = [s for s in samples if t0 <= s[0] <= t0 + t_job]
    paused = [s for s in in_job if s[1]]
    first = (paused[0][0] - t0) if paused else None
    span = (paused[-1][0] - paused[0][0]) if paused else 0
    check("G4 AutoPID reports paused_chip_fw while the job runs (from %s s, for %.0f s of %.0f)"
          % (None if first is None else round(first, 1), span, t_job),
          first is not None and first < 3.0 and span > t_job - 6, "%d paused samples of %d" % (len(paused), len(in_job)))
    if polling:
        moved = (in_job[-1][2] or 0) - (in_job[0][2] or 0) if in_job else None
        check("G4 no poll while the chip is written (polls_ok moved by %s) and none failed over the job (%s -> %s)"
              % (moved, a0.get("polls_failed"), a1.get("polls_failed")),
              moved is not None and moved <= 2 and a1.get("polls_failed") == a0.get("polls_failed"))
    check("G4 AutoPID is not paused any more", ap_stats().get("paused_chip_fw") is False)

    # ---- G5: afterwards ----------------------------------------------------------
    if polling:
        b0 = ap_stats()
        time.sleep(15)
        b1 = ap_stats()
        check("G5 AutoPID polls again and no poll fails (+%s ok, +%s failed in 15 s)"
              % ((b1.get("polls_ok") or 0) - (b0.get("polls_ok") or 0),
                 (b1.get("polls_failed") or 0) - (b0.get("polls_failed") or 0)),
              (b1.get("polls_ok") or 0) - (b0.get("polls_ok") or 0) >= 5
              and b1.get("polls_failed") == b0.get("polls_failed"))
    text = ring()
    p_pause = pos(text, "paused: the OBD chip's firmware is being updated")
    p_warm = pos(text, "chip warm-started and its protocol closed: writing", max(p_pause, 0))
    p_write = pos(text, "starting chip firmware update from builtin", max(p_warm, 0))
    p_done = pos(text, "update job done: bringing the chip up again", max(p_write, 0))
    p_up = pos(text, "bring-up done in", max(p_done, 0))
    p_resume = pos(text, "resumed: the OBD chip's firmware update ended", max(p_up, 0))
    check("G5 the ring in order: AutoPID paused, the warm start and the closed protocol, the write, the bring-up, AutoPID resumed",
          0 <= p_pause < p_warm < p_write < p_done < p_up < p_resume,
          [p_pause, p_warm, p_write, p_done, p_up, p_resume])
    oc = chip()
    st = get("/api/status") or {}
    code, f = http("/api/faults")
    codes = [x.get("code") for x in (f.get("faults") if isinstance(f, dict) else []) or []]
    check("G5 no reset pulse, no fault code", (oc.get("reset") or {}).get("pulses") == 0 and not codes,
          json.dumps({"reset": oc.get("reset"), "faults": codes}))
    check("G5 no E line and no unexpected reset since the power came on",
          err1 == 0 and (st.get("health") or {}).get("log_errors") == 0 and st.get("unexpected_resets") == unexp1
          and st.get("unexpected_resets") in (unexp0, 0),
          {"log_errors": (err1, (st.get("health") or {}).get("log_errors")),
           "unexpected": (unexp0, unexp1, st.get("unexpected_resets"))})

    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t_start), flush=True)
    if fails:
        print("CHIP GUARD FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("CHIP GUARD PASS")


if __name__ == "__main__":
    main()
