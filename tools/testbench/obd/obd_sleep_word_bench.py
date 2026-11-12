#!/usr/bin/env python3
"""The OBD chip's sleep word and the PPSW repair  ->  OBD SLEEP WORD PASS / FAIL

Two sleep-path changes of 2026-11-07 (Ali, after the review of how the chip
goes to sleep in v6 against v4.52p, internal/OBD_CHIP_SLEEP_V6_VS_V4.md):

  the sleep word   obd_chip_sleep(true) sends STSLEEP0 (the documented ST
                   sleep command, what 4.x sent at every sleep entry and
                   every re-sleep) and pulls the PowerEn pin behind it. The
                   pin alone works only through the vendor's VTPPSW function:
                   a chip whose PowerEn function is off or inverted never
                   slept under v6 (six reset pulses, a recovery reboot, the
                   whole sleep delay awake again). The chip answers OK with
                   a prompt and sleeps on its own a second or two later
                   (READY follows that late), so the OK is the verdict and
                   the first nap's READY check the ground truth. The nap's
                   re-sleep resets the chip by command since 2026-11-12
                   (the warm start, not a reset pulse) before the word.
  the PPSW digits  VT PPSW xy is x = the WAKE trigger, y = the SLEEP trigger
                   (0 low level, 1 high level, 2 rising, 3 falling, F none):
                   10 = high wakes, low sleeps; 0F = a LOW level wakes and
                   nothing sleeps, so a chip on 0F is woken by our own pin at
                   every entry and re-sleep (the first stand-in of this bench
                   did exactly that; the chip's STSLLT read SLEEP: CMD WAKE:
                   PWR EN); FF is refused by the chip; 2F = wake on a rising
                   edge and nothing sleeps: the pin low does nothing and the
                   pin static high does nothing, the stand-in for a chip the
                   pin cannot sleep (1F, wake on a high level, when 2F is
                   refused: then the pin high undoes a command sleep while
                   the device is awake, and only the naps are judged).
  the PPSW repair  VTPPSW10 at the bring-up is acknowledged, given 600 ms
                   (legacy's 100 + 500 ms) and read back as 10 BEFORE the
                   reset; before 2026-11-07 a reset pulse came 0 ms after
                   the OK, since 2026-11-12 the reset is an ATZ (a chip that
                   answers gets no pulse, issue 1023).

  as found      the chip ready and unclaimed; the report's VTPPSWS reads 10;
                AutoPID's state noted (never changed); the console held
  healthy       `sleep test N` on the console: the entry logs "chip asleep by
                command" BEFORE "components down; napping", zero re-sleeps,
                the timed wake (POWER_WAKE from sleep_mode), no E line of
                obd_chip or sleep_manager
  stand-in      the wire probe sends VTPPSW2F (the pin cannot sleep the
                chip: one EEPROM write of that cell; 1F when 2F is refused)
                and reads it back; then the word alone, no naps: STSLEEP0
                over the probe must answer OK with a prompt, and an ATI 3 s
                later must get nothing (the command sleep holds while the
                device is awake, a UART word does not wake the chip); then
                `sleep test N`: the entry's word answered OK if the chip was
                awake (asleep already under 2F: no word), zero re-sleeps
                (the command sleep holds through the naps, the pin low does
                nothing), the timed wake (not the recovery reboot), no E
                line; the boot's bring-up logs "PPSW is 2F, setting to 10"
                and "PPSW reads 10 after the write" (the settle and the
                read-back), the report reads VTPPSWS 10 (the second EEPROM
                write, the repair). The wake itself needs no reset pulse:
                the sleep pin going high is the rising edge 2F wakes on,
                the bring-up waits for READY and takes the warm start
                (pulses 0, refused 0, no OBD chip fault code; until
                2026-11-12 the fixed 300 ms read "not ready" and pulsed
                here). Before 2026-11-07 a chip the pin could
                not sleep read six re-sleeps, the E line and
                internal_recovery (test-reports/logs/obd_chip_down_2026-11-01/
                ppsw_e2_e3a.log, E3a, and this stage's first run on the
                unchanged HEAD): the red run of the change.
  end           the chip ready, VTPPSWS 10, the wakes counted

usage: obd_sleep_word_bench.py [--dut localhost:18081] [--port auto|COMx]
                               [--wake 20] [--skip-ppsw] [--log-dir DIR]
  run on the PC with the DUT through test.ps1's ssh tunnel (the sleepword
  stage). Needs: the branch firmware on the DUT, the chip up, the WiCAN
  console (COM254: the bench holds it and types on it, so no console tail
  beside this bench). Two planned wakes, about 2.5 min; the chip's PPSW cell
  takes 2 EEPROM writes (the fault, the repair); --skip-ppsw drops that leg.
  AutoPID and every setting are left as found.
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
import bench_ports  # noqa: E402
import chip_faults  # noqa: E402  (the chip's device fault codes)
from wican_fresh_bench import Console  # noqa: E402  (DTR/RTS off before the open)

ARGS = sys.argv[1:]
FOUND = {}                      # what leg_as_found read, for the end


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
PORT = arg("--port", "auto")
WAKE_S = int(arg("--wake", "20"))
SKIP_PPSW = "--skip-ppsw" in ARGS
LOG_DIR = arg("--log-dir", os.path.join(HERE, "..", "..", "..", "test-reports", "logs"))
CHECKS = []
WAKES = 0
CON = None

WORD_OK = "chip asleep by command"
WORD_NO = "did not take STSLEEP0"
RESLEEP = re.compile(r"re-sleeping \((\d)/6\)")


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


def chip():
    st, oc = http(DUT, "/api/obd_chip")
    return oc if st == 200 and isinstance(oc, dict) else {}


def wait_dut(limit):
    """/api/info answers again after a planned restart"""
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


def report_item(cmd):
    """one line of the chip configuration report, or None"""
    st, rep = http(DUT, "/api/obd_chip/report", timeout=25)
    if st != 200 or not isinstance(rep, dict):
        return None
    for it in rep.get("items", []):
        if it.get("cmd") == cmd:
            return (it.get("resp") or "").replace("\n", " ").strip()
    return None


def ppsw():
    """the report's VTPPSWS as two hex digits, or None"""
    r = report_item("VTPPSWS")
    if r is None:
        return None
    m = re.search(r"\b([0-9A-Fa-f]{2})\b", r)
    return m.group(1).upper() if m else r


def probe(word):
    """the bare-wire probe, unguarded: the word as typed, the answer line"""
    st, r = http(DUT, "/api/obd_chip/probe", "POST",
                 {"kind": "probe", "baud": 2000000, "seconds": 0, "cmd": word})
    if st != 202:
        return None, "%s %s" % (st, r)
    pr = None
    t0 = time.time()
    while time.time() - t0 < 30:
        time.sleep(0.5)
        st, pr = http(DUT, "/api/obd_chip/probe")
        if st == 200 and isinstance(pr, dict) and pr.get("done") and not pr.get("running"):
            break
    lines = (pr or {}).get("lines", [])
    for ln in lines:
        if ln.get("cmd") == word:
            return ln, [(x.get("cmd"), x.get("prompt"), x.get("text"), x.get("hex")) for x in lines]
    return None, [(x.get("cmd"), x.get("prompt"), x.get("text"), x.get("hex")) for x in lines]


def newest_restart():
    st, h = http(DUT, "/api/restart/history")
    if st != 200 or not isinstance(h, dict) or not h.get("records"):
        return {}
    return h["records"][0]


# ---- the console ------------------------------------------------------------------

def con_since():
    return time.time() - CON.t0


def con_lines(since):
    return [l for t, l in CON.snapshot() if t >= since]


def con_wait(pattern, timeout, since):
    hit = CON.wait_for(pattern, timeout, since)
    return hit[1] if hit else None


def e_lines(lines):
    return [l for l in lines if re.match(r"E \(\d+\) (obd_chip|sleep_manager):", l)]


def refusals(lines):
    """the W lines of a word the chip did not take at all (a '?', silence, a
    claim): not the 'OK answered, READY awake' form, which is the word taken
    and the chip still on its way to sleep"""
    return [l for l in lines if WORD_NO in l and "OK answered" not in l]


def save_console(label, lines):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        p = os.path.join(LOG_DIR, "console_sleep_word_%s_%s.log" % (label, time.strftime("%Y%m%d_%H%M%S")))
        with open(p, "w", encoding="utf-8", errors="replace") as f:
            f.write("\n".join(lines) + "\n")
        print("%s console (%s): %d lines in %s" % (now(), label, len(lines), p), flush=True)
    except OSError as exc:
        print("%s console (%s): not saved (%s)" % (now(), label, exc), flush=True)


def sleep_cycle(label):
    """`sleep test WAKE_S` on the console; back with the DUT up: the console
    lines of the whole cycle and the newest restart record"""
    global WAKES
    since = con_since()
    http(DUT, "/api/logs/ring", "DELETE")
    ack = CON.cmd("sleep test %d" % WAKE_S, r"entering test sleep|TEST sleep", 8)
    check("%s: the console took `sleep test %d`" % (label, WAKE_S), ack is not None, ack)
    WAKES += 1
    entry = con_wait(r"sleep_manager: entering sleep", 40, since)
    check("%s: the entry started" % label, entry is not None, entry)
    end = con_wait(r"waking by reboot|recovery reboot|will not sleep", WAKE_S + 90, since)
    check("%s: the sleep ended by a reboot" % label, end is not None, end)
    boot = con_wait(r"obd_chip: bring-up done|restart_tracker: boot", 90, since)
    check("%s: the DUT booted again" % label, boot is not None, boot)
    up = wait_dut(60)
    oc = wait_ready(40) if up else {}
    time.sleep(2)
    check("%s: the DUT back with the chip ready" % label, up and oc.get("ready"),
          json.dumps({k: oc.get(k) for k in ("ready", "claim")}))
    lines = con_lines(since)
    save_console(label, lines)
    return lines, newest_restart()


def entry_lines(lines):
    """the lines between the entry and 'components down'"""
    out = []
    on = False
    for l in lines:
        if "entering sleep" in l:
            on = True
        if on:
            out.append(l)
        if "components down; napping" in l:
            break
    return out


def faults():
    """every latched device fault code: {code: fault}"""
    st, f = http(DUT, "/api/faults")
    return chip_faults.by_code(f)


def leg_as_found():
    oc = wait_ready(20)
    check("the chip ready and unclaimed as found", oc.get("ready") and oc.get("claim") == "none",
          json.dumps({k: oc.get(k) for k in ("ready", "claim", "baud")}))
    FOUND["faults"] = faults()
    cf = chip_faults.chip(FOUND["faults"])
    check("no OBD chip fault code latched as found", not cf, json.dumps(cf))
    p = ppsw()
    check("the report's VTPPSWS reads 10 as found", p == "10", p)
    st, sl = http(DUT, "/api/sleep")
    print("%s sleep: %s" % (now(), json.dumps({k: sl.get(k) for k in ("enabled", "state", "voltage", "sleep_v", "wake_v")}) if isinstance(sl, dict) else sl), flush=True)
    st, ap = http(DUT, "/api/settings/autopid")
    enabled = bool(isinstance(ap, dict) and ap.get("enabled"))
    print("%s AutoPID %s (left as found)" % (now(), "on" if enabled else "off"), flush=True)
    return enabled


def leg_healthy():
    lines, rec = sleep_cycle("healthy")
    ent = entry_lines(lines)
    word = [l for l in ent if WORD_OK in l]
    check("healthy: the entry reads '%s' before 'components down; napping'" % WORD_OK,
          len(word) == 1, word or ent[-6:])
    res = [l for l in lines if RESLEEP.search(l)]
    check("healthy: zero re-sleeps", not res, res)
    check("healthy: the timed wake, not the recovery reboot",
          any("waking by reboot (test timer)" in l for l in lines) and not any("recovery reboot" in l for l in lines))
    check("healthy: the wake record reads power_wake from sleep_mode",
          rec.get("planned_reason") == "power_wake" and rec.get("source") == "sleep_mode",
          json.dumps({k: rec.get(k) for k in ("planned_reason", "source", "reason")}))
    e = e_lines(lines)
    check("healthy: no E line of obd_chip or sleep_manager", not e, e)
    cf = chip_faults.chip(faults())
    check("healthy: no OBD chip fault code latched by the sleep and the wake", not cf, json.dumps(cf))


def leg_stand_in():
    """the PowerEn pin unable to sleep the chip: 2F (wake on a rising edge,
    no sleep function: the pin low does nothing and the pin high, static
    while the device is awake, does nothing either), or 1F (wake on a high
    level) when the chip refuses 2F. FF is refused by the chip (2026-11-07)."""
    value = None
    for v in ("2F", "1F"):
        ln, detail = probe("VTPPSW" + v)
        if ln is not None and ln.get("prompt") and "OK" in str(ln.get("text")):
            value = v
            print("%s the probe's VTPPSW%s answered OK" % (now(), v), flush=True)
            break
        print("%s the probe's VTPPSW%s was refused: %s" % (now(), v, detail), flush=True)
        wait_ready(20)
    check("the wire probe set a PowerEn value the pin cannot sleep the chip with (2F, else 1F)", value is not None, value)
    if value is None:
        return
    wait_ready(20)
    ln, detail = probe("VTPPSWS")
    check("the probe reads PPSW %s after the fault" % value, ln is not None and value in str(ln.get("text")), detail)
    wait_ready(20)
    # the word alone, no naps
    ln, detail = probe("STSLEEP0")
    check("STSLEEP0 over the wire answered OK with a prompt (the chip's bytes recorded)",
          ln is not None and ln.get("prompt") and "OK" in str(ln.get("text")), detail)
    time.sleep(3)
    ln, detail = probe("ATI")
    silent = ln is not None and not ln.get("prompt") and not str(ln.get("text") or "")
    if value == "2F":
        check("ATI 3 s later gets nothing: the command sleep holds while the device is awake, a UART word does not wake the chip",
              silent, detail)
    else:
        print("%s ATI 3 s later: %s (under 1F the pin's high level wakes a command-slept chip: reported, not judged)"
              % (now(), "nothing" if silent else detail), flush=True)
    lines, rec = sleep_cycle("standin")
    ent = entry_lines(lines)
    check("stand-in: the entry's word, if the chip was awake, answered OK (no refusal)",
          not any(WORD_NO in l for l in ent), [l for l in ent if "obd_chip" in l])
    print("%s stand-in: the entry %s" % (now(), "sent the word" if any(WORD_OK in l for l in ent) else "found the chip asleep already (no word)"), flush=True)
    res = [RESLEEP.search(l).group(1) for l in lines if RESLEEP.search(l)]
    print("%s stand-in: %d re-sleep(s) %s" % (now(), len(res), res), flush=True)
    check("stand-in: zero re-sleeps (the command sleep holds through the naps)", not res, res)
    ref = refusals(lines)
    check("stand-in: no word refused (no '?', no silence, no claim)", not ref, ref)
    check("stand-in: the timed wake, not the recovery reboot",
          any("waking by reboot (test timer)" in l for l in lines) and not any("recovery reboot" in l for l in lines),
          [l for l in lines if "waking by reboot" in l or "recovery" in l][:3])
    check("stand-in: the wake record reads power_wake from sleep_mode",
          rec.get("planned_reason") == "power_wake" and rec.get("source") == "sleep_mode",
          json.dumps({k: rec.get(k) for k in ("planned_reason", "source", "reason")}))
    e = e_lines(lines)
    check("stand-in: no E line of obd_chip or sleep_manager", not e, e)
    ends = [i for i, l in enumerate(lines) if "waking by reboot" in l or "recovery reboot" in l]
    boot = [l for l in lines[ends[0]:] if "obd_chip:" in l] if ends else []
    print("%s stand-in: the boot read %s" % (now(), [l for l in boot if "chip awake" in l or "chip not ready" in l][:1]), flush=True)
    check("stand-in: the boot repaired PPSW ('PPSW is %s, setting to 10')" % value,
          any("PPSW is %s, setting to 10" % value in l for l in boot), [l for l in boot if "PPSW" in l])
    check("stand-in: the repair read back before the reset ('PPSW reads 10 after the write')",
          any("PPSW reads 10 after the write" in l for l in boot), [l for l in boot if "PPSW" in l])
    p = ppsw()
    check("the report's VTPPSWS reads 10 after the repair", p == "10", p)
    # the stand-in slept through every nap and woke on the pin all the same:
    # the sleep pin going high at the wake is the rising edge 2F wakes on (a
    # high level under 1F). The bring-up waits for READY and takes the warm
    # start, so this wake needs no reset pulse and latches no fault code
    # (measured on a healthy chip, 2026-11-12: READY steady 704 ms behind the
    # pin; until that day the fixed 300 ms read "not ready" here and pulsed)
    st, oc = http(DUT, "/api/obd_chip")
    rs = (oc or {}).get("reset") or {}
    check("stand-in: the wake needed no reset pulse (READY settled, pulses 0, refused 0)",
          rs.get("wake_ready") is True and rs.get("wake_pulsed") is False
          and rs.get("pulses") == 0 and rs.get("refused") == 0, json.dumps(rs))
    check("stand-in: the boot took the warm start, not the last resort",
          any("chip awake; warm start" in l for l in boot)
          and not any("the last resort" in l or "chip not ready" in l for l in boot),
          [l for l in boot if "chip awake" in l or "chip not ready" in l or "the last resort" in l][:3])
    cf = chip_faults.chip(faults())
    check("stand-in: no OBD chip fault code latched (not silent, not woke, not no_sleep, not damaged)",
          not cf, json.dumps(cf))


def main():
    global CON
    print("%s OBD SLEEP WORD bench on %s (wake %d s%s)" % (
        now(), DUT, WAKE_S, ", PPSW leg skipped" if SKIP_PPSW else ""), flush=True)
    port = bench_ports.resolve(PORT, ("wican_console", "ch342_console"), "COM254")
    CON = Console(port, 2000000)
    print("%s console held on %s" % (now(), port), flush=True)
    time.sleep(2)
    if not wait_dut(60):
        print("the DUT does not answer through %s" % DUT)
        print("OBD SLEEP WORD FAIL (no DUT)")
        sys.exit(1)
    try:
        leg_as_found()
        leg_healthy()
        if not SKIP_PPSW:
            leg_stand_in()
        oc = wait_ready(20)
        p = ppsw()
        check("the chip ready at the end, VTPPSWS 10", oc.get("ready") and p == "10", p)
        ok, what = chip_faults.clear_own(FOUND.get("faults", {}), faults(), (),
                                         lambda: http(DUT, "/api/faults/clear", "POST", {}))
        check("the end: the fault store holds nothing the bench did not put there", ok, what)
        print("%s fault codes: %s" % (now(), what), flush=True)
    finally:
        CON.close()
    print("%s planned wakes: %d" % (now(), WAKES), flush=True)
    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    print("OBD SLEEP WORD %s (%d checks, %d failed, %d planned wakes)" % (
        "PASS" if not fails else "FAIL", len(CHECKS), len(fails), WAKES))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
