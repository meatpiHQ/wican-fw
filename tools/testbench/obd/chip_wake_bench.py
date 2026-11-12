#!/usr/bin/env python3
"""The OBD chip is never reset inside one of its own boots  ->  CHIP WAKE PASS / FAIL

Issue 1023, the device killer. The chip tidies its stored settings in the
first boot behind every 264 writes of its store, and a reset inside that
tidying destroys the store: the chip loses its identity and its PowerEn
setting, or never answers again. Until 2026-11-12 every wake from sleep
pulsed the chip's reset line 300 ms behind the sleep pin, inside the chip's
own wake-up boot, right where the tidying starts. Two bench chips died of
it. The bring-up now waits until READY has held "ready" for 300 ms and takes
the warm start; no reset pulse goes out inside a boot of the chip. And the
reset line is left to a chip that does not answer (asleep or stuck for a
full second, or silent at both UART speeds): a chip that answers is reset
by command, so a healthy chip never gets a pulse, whatever the device does.

This bench proves that on the product's own sleep and wake path:

  as found    the image carries the guard (`reset` with `refused` on GET
              /api/obd_chip; an image without it is NOT put through a wake
              by this bench), the chip ready and not damaged, its
              configuration report kept
  wake        `sleep test N` on the console, a real sleep and a timed wake.
              The wake's bring-up: READY settled as ready, 600 to 1600 ms
              behind the sleep pin (a figure near 300 would mean the chip
              was not asleep), no reset pulse, the warm start, no `chip not
              ready` line; zero re-sleeps; the chip ready, its report as
              found
  tidy wake   only when every check before it passed. The store is filled:
              the memory function switched on through the wire probe, then
              276 protocol changes with answered requests over /ws/obd (the
              simulator answering 11 bit and 29 bit ids), so the next boot
              of the chip is the one in which it tidies its store: THE boot
              a reset pulse kills the chip in. `sleep test N` again. The
              wake must show that boot (READY settles at least 100 ms later
              than in the leg before: the proof that the dangerous wake was
              the one tested), no reset pulse, the chip ready, its report as
              found, `fw.damaged` false
  end         the reset line's counters, AutoPID, the sleep setting and the
              simulator as found; no OBD chip fault code latched (GET
              /api/faults: obd_chip_woke, obd_chip_no_sleep, obd_chip_silent,
              obd_chip_damaged; also wanted as found and behind each wake)

usage: chip_wake_bench.py [--dut localhost:18081] [--port auto|COMx] [--wake 15]
                          [--skip-tidy] [--log-dir DIR]
  run on the PC with the DUT through test.ps1's ssh tunnel (the chipwake
  stage). The bench holds the WiCAN console and types on it, so no console
  tail beside it. Two planned wakes (one with --skip-tidy), a planned restart
  for each setting it has to change (AutoPID off, the sleep on) and one to
  put them back; about 3 min. The chip's store takes 276 writes in the tidy
  leg (one block of its log, tidied by the chip in the wake that follows).

A FAIL in the tidy leg on a healthy image cannot happen without the first
leg failing too; a regression of the guard fails the first leg, which costs
the chip nothing.
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
sys.path.insert(0, os.path.join(HERE, "..", "wifi"))
sys.path.insert(0, HERE)
import bench_bus  # noqa: E402
import bench_ports  # noqa: E402
import chip_nvm  # noqa: E402  (the chip through /ws/obd)
import chip_faults  # noqa: E402  (the chip's device fault codes)
from wican_fresh_bench import Console  # noqa: E402  (DTR/RTS off before the open)

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
PORT = arg("--port", "auto")
WAKE_S = int(arg("--wake", "15"))
SKIP_TIDY = "--skip-tidy" in ARGS
LOG_DIR = arg("--log-dir", os.path.join(HERE, "..", "..", "..", "test-reports", "logs"))
STORE_WRITES = 276          # more than the 264 that fill a block, fewer than two blocks
WAKE_MS = (600, 1600)       # READY steady behind the sleep pin at a real wake
TIDY_EXTRA_MS = 100         # the tidying boot is about 155 ms longer
VOLATILE = ("ATRV",)        # report items that move by themselves
CHECKS = []
WAKES = 0
RESTARTS = 0
CON = None
RESLEEP = re.compile(r"re-sleeping \((\d)/6\)")


def now():
    return time.strftime("%H:%M:%S")


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


def failed():
    return [n for n, ok in CHECKS if not ok]


def http(path, method="GET", body=None, timeout=8):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(DUT + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
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


def chip_fault_codes():
    """the chip's own fault codes latched now: {code: 'detail (count N)'}"""
    st, f = http("/api/faults")
    return chip_faults.chip(chip_faults.by_code(f))


def chip():
    st, oc = http("/api/obd_chip")
    return oc if st == 200 and isinstance(oc, dict) else {}


def wait_dut(limit):
    t0 = time.time()
    time.sleep(5)
    while time.time() - t0 < limit:
        st, _ = http("/api/info", timeout=3)
        if st == 200:
            return True
        time.sleep(2)
    return False


def wait_ready(limit):
    t0 = time.time()
    oc = {}
    while time.time() - t0 < limit:
        oc = chip()
        if oc.get("ready") and oc.get("claim") == "none":
            return oc
        time.sleep(0.5)
    return oc


def report():
    """the chip's configuration report as {word: answer}, the items that
    move by themselves left out"""
    st, rep = http("/api/obd_chip/report", timeout=30)
    if st != 200 or not isinstance(rep, dict):
        return None
    return {it.get("cmd"): (it.get("resp") or "").replace("\n", " ").strip()
            for it in rep.get("items", []) if it.get("cmd") not in VOLATILE}


def report_diff(a, b):
    if a is None or b is None:
        return ["a report could not be read"]
    return ["%s: '%s' -> '%s'" % (k, a.get(k), b.get(k))
            for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def probe(word):
    """the bare-wire probe: the answer line of `word`, or None"""
    st, r = http("/api/obd_chip/probe", "POST",
                 {"kind": "probe", "baud": 2000000, "seconds": 0, "cmd": word})
    if st != 202:
        return None
    pr = None
    t0 = time.time()
    while time.time() - t0 < 30:
        time.sleep(0.5)
        st, pr = http("/api/obd_chip/probe")
        if st == 200 and isinstance(pr, dict) and pr.get("done") and not pr.get("running"):
            break
    for ln in (pr or {}).get("lines", []):
        if ln.get("cmd") == word:
            return ln
    return None


def settings(name):
    st, doc = http("/api/settings/" + name)
    if st != 200 or not isinstance(doc, dict):
        raise RuntimeError("GET /api/settings/%s: %s %s" % (name, st, doc))
    return {k: v for k, v in doc.items() if k not in ("degraded", "pending_reboot")}


def restart_with(changes):
    """stage {component: {key: value}} and submit: one planned restart"""
    global RESTARTS
    staged = False
    for name, vals in changes.items():
        cur = settings(name)
        new = dict(cur)
        new.update(vals)
        if new != cur:
            st, r = http("/api/settings/" + name, "PUT", new)
            if st != 200:
                raise RuntimeError("PUT %s: %s %s" % (name, st, r))
            staged = True
    if not staged:
        return
    http("/api/settings/submit", "POST", {})
    RESTARTS += 1
    wait_dut(90)
    wait_ready(40)
    time.sleep(2)


# ---- the console ------------------------------------------------------------------

def con_since():
    return time.time() - CON.t0


def con_lines(since):
    return [l for t, l in CON.snapshot() if t >= since]


def con_wait(pattern, timeout, since):
    hit = CON.wait_for(pattern, timeout, since)
    return hit[1] if hit else None


def save_console(label, lines):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        p = os.path.join(LOG_DIR, "console_chip_wake_%s_%s.log" % (label, time.strftime("%Y%m%d_%H%M%S")))
        with open(p, "w", encoding="utf-8", errors="replace") as f:
            f.write("\n".join(lines) + "\n")
        print("%s console (%s): %d lines in %s" % (now(), label, len(lines), p), flush=True)
    except OSError as exc:
        print("%s console (%s): not saved (%s)" % (now(), label, exc), flush=True)


def sleep_cycle(label):
    """`sleep test WAKE_S` on the console; back with the DUT up: the console
    lines of the whole cycle, and those of the boot that followed the wake"""
    global WAKES
    since = con_since()
    ack = CON.cmd("sleep test %d" % WAKE_S, r"entering test sleep|TEST sleep", 8)
    check("%s: the console took `sleep test %d`" % (label, WAKE_S), ack is not None, ack)
    WAKES += 1
    entry = con_wait(r"sleep_manager: entering sleep", 40, since)
    check("%s: the entry started" % label, entry is not None, entry)
    end = con_wait(r"waking by reboot|recovery reboot|will not sleep", WAKE_S + 90, since)
    check("%s: the sleep ended by its timer" % label,
          end is not None and "waking by reboot" in end, end)
    boot = con_wait(r"obd_chip: bring-up done", 90, since)
    check("%s: the chip's bring-up ran in the boot behind the wake" % label, boot is not None, boot)
    up = wait_dut(60)
    oc = wait_ready(40) if up else {}
    time.sleep(2)
    lines = con_lines(since)
    save_console(label, lines)
    ends = [i for i, l in enumerate(lines) if "waking by reboot" in l or "recovery reboot" in l]
    return lines, (lines[ends[0]:] if ends else []), oc


def judge_wake(label, lines, boot, oc, found):
    """what every wake must show, tidying or not; the settle time (ms)"""
    rs = oc.get("reset", {})
    chip_boot = [l for l in boot if "obd_chip:" in l]
    check("%s: the chip is ready behind the wake" % label, oc.get("ready") is True,
          json.dumps({k: oc.get(k) for k in ("ready", "claim", "baud")}))
    check("%s: READY settled as ready behind the sleep pin" % label, rs.get("wake_ready") is True,
          json.dumps(rs))
    check("%s: the bring-up sent NO reset pulse (reset.wake_pulsed false, pulses 0 this boot)" % label,
          rs.get("wake_pulsed") is False and rs.get("pulses") == 0, json.dumps(rs))
    check("%s: no reset pulse was asked for either (reset.refused 0)" % label,
          rs.get("refused") == 0, json.dumps(rs))
    cf = chip_fault_codes()
    check("%s: no OBD chip fault code latched (not woke, not no_sleep, not silent, not damaged)" % label,
          not cf, json.dumps(cf))
    ms = rs.get("wake_ms") or 0
    check("%s: the bring-up waited out the chip's own boot (%d..%d ms behind the sleep pin)"
          % (label, WAKE_MS[0], WAKE_MS[1]), WAKE_MS[0] <= ms <= WAKE_MS[1], "%s ms" % ms)
    check("%s: the boot's log has the warm start and no `chip not ready` line" % label,
          any("chip awake; warm start" in l for l in chip_boot)
          and not any("chip not ready" in l for l in chip_boot)
          and not any("soft reset not confirmed" in l for l in chip_boot),
          [l[-90:] for l in chip_boot if "chip " in l or "reset" in l][:4])
    res = [l for l in lines if RESLEEP.search(l)]
    check("%s: zero re-sleeps (the chip slept through the naps)" % label, not res, res[:2])
    e = [l for l in lines if re.match(r"E \(\d+\) (obd_chip|sleep_manager):", l)]
    check("%s: no E line of obd_chip or sleep_manager" % label, not e, e[:2])
    check("%s: the chip's stored identity is not damaged" % label,
          oc.get("fw", {}).get("damaged") is False, json.dumps(oc.get("fw", {}))[:160])
    diff = report_diff(found, report())
    check("%s: the chip's configuration report as found" % label, not diff, diff[:3])
    return ms


def fill_store(stored):
    """STORE_WRITES protocol changes with answered requests, the memory
    function on: the chip's next boot is the one in which it tidies its
    store. Ends on the protocol the store held as found."""
    ln = probe("ATM1")
    check("tidy wake: the wire probe switched the chip's memory function on (ATM1 answered OK)",
          ln is not None and ln.get("prompt") and "OK" in str(ln.get("text")),
          (ln or {}).get("text"))
    wait_ready(20)
    other = "7" if stored == "6" else "6"
    c = chip_nvm.Chip(DUT_HP)
    answered = 0
    try:
        c.cmd("ATE0")
        for i in range(STORE_WRITES):
            proto = other if i % 2 == 0 else stored
            c.cmd("ATTP" + proto)
            if "41 00" in c.cmd("0100", 4.0):
                answered += 1
    finally:
        c.close()
    check("tidy wake: %d protocol changes, every one with an answered request (a store write each)"
          % STORE_WRITES, answered == STORE_WRITES, "%d answered" % answered)
    return answered == STORE_WRITES


def main():
    global CON
    print("%s CHIP WAKE bench on %s (wake %d s%s)" % (
        now(), DUT, WAKE_S, ", the tidy leg skipped" if SKIP_TIDY else ""), flush=True)
    if not wait_dut(60):
        print("the DUT does not answer through %s" % DUT)
        print("CHIP WAKE FAIL (no DUT)")
        sys.exit(1)

    # ---- as found: nothing is done to an image without the guard --------------
    oc = wait_ready(30)
    has_guard = (isinstance(oc.get("reset"), dict) and "refused" in oc["reset"]
                 and "damaged" in oc.get("fw", {}))
    check("the image carries the wake guard and the reset line's rule (`reset.refused` and `fw.damaged` "
          "on GET /api/obd_chip)", has_guard, json.dumps(oc.get("reset")))
    if not has_guard:
        print("this image pulses the chip's reset inside its wake-up boot (issue 1023): "
              "the bench does not put it through a wake")
        print("CHIP WAKE FAIL (%d checks, %d failed, 0 planned wakes)" % (len(CHECKS), len(failed())))
        sys.exit(1)
    check("the chip ready and unclaimed as found", oc.get("ready") and oc.get("claim") == "none",
          json.dumps({k: oc.get(k) for k in ("ready", "claim", "baud")}))
    cf = chip_fault_codes()
    check("no OBD chip fault code latched as found (one that is there would hide a new one: "
          "read it, then POST /api/faults/clear)", not cf, json.dumps(cf))
    check("the chip's stored identity is not damaged as found",
          oc.get("fw", {}).get("damaged") is False, json.dumps(oc.get("fw", {}))[:160])
    if failed():
        print("CHIP WAKE FAIL (%d checks, %d failed, 0 planned wakes)" % (len(CHECKS), len(failed())))
        sys.exit(1)
    found = {"autopid": settings("autopid").get("enabled"),
             "sleep": settings("sleep_manager").get("enabled")}
    stored = oc.get("eeprom_guard", {}).get("stored_protocol")
    print("%s as found: AutoPID %s, sleep %s, the chip's stored protocol '%s', chip '%s'" % (
        now(), found["autopid"], found["sleep"], stored, oc.get("fw", {}).get("chip")), flush=True)
    restart_with({"autopid": {"enabled": False}, "sleep_manager": {"enabled": True}})
    report_found = report()
    check("the chip's configuration report read as found", report_found is not None,
          sorted(report_found or {}))

    port = bench_ports.resolve(PORT, ("wican_console", "ch342_console"), "COM254")
    CON = Console(port, 2000000)
    print("%s console held on %s" % (now(), port), flush=True)
    time.sleep(2)
    sim_previous = {}
    try:
        if not wait_dut(60):
            raise RuntimeError("the DUT did not come back behind the console's open")
        wait_ready(40)

        # ---- wake: costs the chip nothing whatever the firmware does -----------
        lines, boot, oc = sleep_cycle("wake")
        plain_ms = judge_wake("wake", lines, boot, oc, report_found)

        # ---- tidy wake: only behind a clean first leg --------------------------
        if SKIP_TIDY:
            print("%s the tidy leg is skipped (--skip-tidy)" % now(), flush=True)
        elif failed():
            check("tidy wake: not run, a check before it failed (the chip is not risked)", False,
                  failed()[:2])
        elif stored not in ("6", "7"):
            check("tidy wake: the chip's stored protocol is 6 or 7 (the leg ends its writes on it)",
                  False, stored)
        else:
            sim_previous, _ = bench_bus.sim_patch(
                {"ecu_sim": {"hs_enabled": True, "hs_bitrate_kbps": 500,
                             "hs_obd11": True, "hs_obd29": True}})
            time.sleep(3)
            wait_ready(40)
            if fill_store(stored):
                lines, boot, oc = sleep_cycle("tidy")
                tidy_ms = judge_wake("tidy wake", lines, boot, oc, report_found)
                check("tidy wake: the chip's tidying boot was the one tested (READY settled at "
                      "least %d ms later than in the wake before)" % TIDY_EXTRA_MS,
                      tidy_ms >= plain_ms + TIDY_EXTRA_MS,
                      "%s ms against %s ms" % (tidy_ms, plain_ms))
                check("tidy wake: the chip's stored protocol as found",
                      oc.get("eeprom_guard", {}).get("stored_protocol") == stored,
                      oc.get("eeprom_guard", {}).get("stored_protocol"))
    finally:
        CON.close()
        if sim_previous:
            bench_bus.sim_patch(sim_previous)
            print("%s the simulator as found: %s" % (now(), json.dumps(sim_previous)), flush=True)
        try:
            restart_with({"autopid": {"enabled": found["autopid"]},
                          "sleep_manager": {"enabled": found["sleep"]}})
        except RuntimeError as exc:
            check("the settings put back as found", False, exc)

    oc = wait_ready(40)
    check("the end: the chip ready, not damaged, the reset line not used (pulses 0, refused 0)",
          oc.get("ready") and oc.get("fw", {}).get("damaged") is False
          and oc.get("reset", {}).get("pulses") == 0 and oc.get("reset", {}).get("refused") == 0,
          json.dumps({"ready": oc.get("ready"), "reset": oc.get("reset")}))
    cf = chip_fault_codes()
    check("the end: no OBD chip fault code latched", not cf, json.dumps(cf))
    print("%s planned wakes: %d, planned restarts: %d" % (now(), WAKES, RESTARTS), flush=True)
    fails = failed()
    for f in fails:
        print("FAIL:", f)
    print("CHIP WAKE %s (%d checks, %d failed, %d planned wakes)" % (
        "PASS" if not fails else "FAIL", len(CHECKS), len(fails), WAKES))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
