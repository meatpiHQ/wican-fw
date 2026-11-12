#!/usr/bin/env python3
"""The OBD chip's own sleep triggers, switched off: the stored thresholds have no effect
-> OBD SLEEP TRIGGERS PASS / FAIL

The bring-up provisions the chip with voltage thresholds (STSLVLW >wake,
STSLVLS <sleep for time) and a UART inactivity timeout (STSLUIT 1200) and
then switches the triggers that would act on them OFF (STSLVL off,off;
STSLU off, off): the ESP32 owns the sleep policy. Before those stored values
are dropped from the provisioning (Ali, 2026-11-07: "make sure these values
have no effect"), this bench asks the chip itself, with the PSU driving the
voltage the chip measures (ATRV) and the controls as the chip reports them
in STSLCS:

  as found    STSLCS reads VL_SLEEP OFF, VL_WAKE OFF, UART_SLEEP OFF,
              UART_WAKE OFF with their stored values; PPSW 10; the PSU at its
              setpoint (13.5 V on the bench); the chip ready
  VL sleep    the PSU under the stored sleep threshold (12.5 V against
              <13.20 V) for longer than the stored time (150 s + 20 s) with
              the chip awake and the device awake; every 30 s the chip's own
              ATRV reads under the threshold; at the end ATI answers: the
              chip did not sleep. The device's own countdown (5 min at that
              voltage) is watched and the leg aborts short of it
  VL wake     VTPPSW2F over the wire probe (the pin cannot wake the chip: a
              rising edge only, the pin static high; one EEPROM write of
              that cell); the PSU under the stored wake threshold (13.0 V
              against >13.50 V); STSLEEP0 over the probe answers OK; ATI gets
              nothing (asleep, and a UART word does not wake it: STSLU wake
              off); the PSU over the wake threshold (14.0 V) for 20 s and
              again 10 s: ATI gets nothing twice: the chip did not wake. A
              planned restart then wakes it and repairs PPSW (the second
              EEPROM write). That wake is the one place a healthy chip meets
              the reset line since 2026-11-12 (issue 1023): asleep by
              command, a pin that gives it no edge across a restart, READY
              high for a full second, no answer at either UART speed. The
              boot must ask it on the wire BEFORE the pulse, pulse once
              (reset.pulses 1, refused 0) and latch obd_chip_silent, "came
              back after the reset pulse"; the bench clears that code at its
              end when the store was empty as found
  a factory   a chip v6 never provisioned with thresholds (a unit whose first
  chip        v6 image dates from 2026-11-07 or later) stores the factory's:
              VL_SLEEP <5.53 V for 65534 s, VL_WAKE >65.52 V (seen on unit
              14c19f457fd9). The supply cannot go there, so the two
              checks that need a reading on the far side of a threshold are
              printed as SKIP and counted in the verdict line; the hold at
              12.5 V (170 s) and the waits at 14.0 V run all the same and
              the chip must neither sleep nor wake
  UART sleep  (--uart-silence MIN, 25 by default when given: longer than the
              stored 1200 s) nothing is sent to the chip for MIN minutes with
              the device awake (uart.tx_bytes must not move); then ATI
              answers: the chip did not sleep on UART silence
  provision   (--provision-leg) the provisioning after the change of
              2026-11-07: obd_chip wake_voltage_mv set to 13000 and back
              (two reboots) provisions nothing and leaves STSLCS as found
              (the setting is no longer compared with the chip); STSLVL on,
              off over the probe (one EEPROM write) then a planned restart
              logs "provisioning sleep config: a control is on", the four
              controls read OFF again and the stored thresholds and timeout
              are untouched; PP 0F went out inside the block (pp_restores 0)
  end         STSLCS line for line as found (no provisioning happened on
              the voltage legs: pp_restores 0, no "provisioning sleep
              config" in those boots' rings), PPSW 10, the PSU back at its
              setpoint, the device awake

usage: obd_sleep_triggers_bench.py [--dut localhost:18081] [--psu-port auto|COMx]
                                   [--uart-silence MIN] [--provision-leg] [--log-dir DIR]
  run on the PC with the DUT through test.ps1's ssh tunnel (the sleeptrig
  stage) and the OWON PSU on the PC. Needs: the branch firmware on the DUT,
  the chip up, sleep enabled with its default thresholds (the device must
  stay awake through the legs: the 12.5 V leg runs 190 s against a 5 min
  countdown; the critical floor at 11.9 V is never approached). One planned
  restart, 2 EEPROM writes of the chip's PPSW cell; about 6 min, plus the
  UART leg. AutoPID and every setting are left as found.
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
import bench_psu  # noqa: E402  (ECU sim v2's OBD pin 16 since 2026-11-09)
import chip_faults  # noqa: E402  (the chip's device fault codes)

ARGS = sys.argv[1:]
FOUND = {}                      # what leg_as_found read, for the end


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
PSU_PORT = arg("--psu-port", "auto")
UART_MIN = int(arg("--uart-silence", "0"))
PROVISION = "--provision-leg" in ARGS or "--only-provision" in ARGS
ONLY_PROVISION = "--only-provision" in ARGS  # skip the voltage legs (no PPSW writes)
LOG_DIR = arg("--log-dir", os.path.join(HERE, "..", "..", "..", "test-reports", "logs"))
V_UNDER = 12.5   # under the stored sleep threshold, well over the 11.9 V floor
V_LOW = 13.0     # under the stored wake threshold, for the command sleep
V_OVER = 14.0    # over the stored wake threshold (the matrix's awake voltage)
HOLD_MAX_S = 240       # the longest hold short of the device's own 5 min countdown
HOLD_NO_REACH_S = 170  # the hold when the stored threshold is out of reach (the
                       # classic 150 s + 20 s)
CHECKS = []
SKIPS = []             # checks this chip's stored values make untestable
RESTARTS = 0
PSU = None


def now():
    return time.strftime("%H:%M:%S")


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


def skip(name, why):
    SKIPS.append(name)
    print("%s SKIP: %s (%s)" % (now(), name, why), flush=True)


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
    t0 = time.time()
    time.sleep(5)
    while time.time() - t0 < limit:
        st, _ = http(DUT, "/api/info", timeout=3)
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
    st, rep = http(DUT, "/api/obd_chip/report", timeout=25)
    if st != 200 or not isinstance(rep, dict):
        return {}
    return {it.get("cmd"): (it.get("resp") or "") for it in rep.get("items", [])}


def stslcs_lines(rep):
    return [l.strip() for l in rep.get("STSLCS", "").splitlines() if l.strip()]


def stslcs_values(lines):
    """the controls and their stored values out of the STSLCS lines"""
    out = {}
    for l in lines:
        m = re.match(r"VL_SLEEP:\s+(ON|OFF),\s*<!?([\d.]+)V FOR (\d+) s", l)
        if m:
            out["vl_sleep"] = (m.group(1), float(m.group(2)), int(m.group(3)))
        m = re.match(r"VL_WAKE:\s+(ON|OFF),\s*>!?([\d.]+)V FOR (\d+) s", l)
        if m:
            out["vl_wake"] = (m.group(1), float(m.group(2)), int(m.group(3)))
        m = re.match(r"UART_SLEEP:\s+(ON|OFF),\s*(\d+) s", l)
        if m:
            out["uart_sleep"] = (m.group(1), int(m.group(2)))
        m = re.match(r"UART_WAKE:\s+(ON|OFF)", l)
        if m:
            out["uart_wake"] = (m.group(1),)
    return out


def ppsw(rep):
    m = re.search(r"\b([0-9A-Fa-f]{2})\b", rep.get("VTPPSWS", ""))
    return m.group(1).upper() if m else rep.get("VTPPSWS", "").strip()


def probe(word):
    """the bare-wire probe, unguarded: the word's answer line, the whole result"""
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


def answered(ln):
    return ln is not None and bool(ln.get("prompt")) and bool(str(ln.get("text") or "").strip())


def silent(ln):
    return ln is not None and not ln.get("prompt") and not str(ln.get("text") or "").strip()


def chip_volts():
    """the chip's own battery reading over the probe (ATRV: '12.5V'), or None"""
    ln, detail = probe("ATRV")
    m = re.search(r"([\d.]+)V", str((ln or {}).get("text") or ""))
    return (float(m.group(1)) if m else None), detail


def sleep_status():
    st, sl = http(DUT, "/api/sleep")
    return sl if st == 200 and isinstance(sl, dict) else {}


def psu_to(volts, settle_s=3.0):
    PSU.set_voltage(volts)
    time.sleep(settle_s)
    return PSU.meas_voltage()


def ring_lines():
    st, txt = http(DUT, "/api/logs/ring", timeout=15)
    if st != 200:
        return []
    text = txt if isinstance(txt, str) else json.dumps(txt)
    esc = chr(27)
    out = []
    for l in text.splitlines():
        while esc in l:
            i = l.index(esc)
            j = l.find("m", i)
            if j < 0:
                l = l[:i]
                break
            l = l[:i] + l[j + 1:]
        out.append(l.strip())
    return out


def restart():
    global RESTARTS
    http(DUT, "/api/logs/ring", "DELETE")
    st, _ = http(DUT, "/api/restart", "POST", {})
    RESTARTS += 1
    ok = st == 200 and wait_dut(60)
    oc = wait_ready(40) if ok else {}
    time.sleep(2)
    return ok and bool(oc.get("ready")), oc


def open_psu():
    last = None
    for attempt in range(3):
        try:
            return bench_psu.open_psu(PSU_PORT)
        except SystemExit as exc:  # a dropped request on the simulator's link
            last = exc
            time.sleep(1.5)
    raise SystemExit("the supply (%s): %s" % (PSU_PORT, last))


# ---- legs ---------------------------------------------------------------------------

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
    rep = report()
    lines = stslcs_lines(rep)
    vals = stslcs_values(lines)
    print("%s STSLCS as found: %s" % (now(), " | ".join(lines)), flush=True)
    check("STSLCS reads the four controls OFF with their stored values",
          vals.get("vl_sleep", ("?",))[0] == "OFF" and vals.get("vl_wake", ("?",))[0] == "OFF" and
          vals.get("uart_sleep", ("?",))[0] == "OFF" and vals.get("uart_wake", ("?",))[0] == "OFF", vals)
    check("VTPPSWS reads 10 as found", ppsw(rep) == "10", ppsw(rep))
    setpoint = PSU.voltage_setpoint()
    print("%s PSU as found: setpoint %.2f V, measured %.2f V, output %s" % (now(), setpoint, PSU.meas_voltage(), PSU.output_on()), flush=True)
    sl = sleep_status()
    print("%s sleep: %s" % (now(), json.dumps({k: sl.get(k) for k in ("enabled", "state", "voltage", "sleep_v", "wake_v", "sleep_in_s")})), flush=True)
    check("the device awake and not counting down", sl.get("state") == "normal" and sl.get("pending") == "none", sl.get("state"))
    return lines, vals, setpoint


def leg_vl_sleep(vals, setpoint):
    _, sleep_v, sleep_t = vals.get("vl_sleep", ("OFF", 13.2, 150))
    # a threshold the supply can get under, for a time the device's own
    # countdown leaves room for; a factory chip (<5.53 V for 65534 s) has neither
    reach = V_UNDER < sleep_v and sleep_t + 20 <= HOLD_MAX_S
    hold = sleep_t + 20 if reach else HOLD_NO_REACH_S
    v = psu_to(V_UNDER)
    if reach:
        print("%s PSU to %.2f V (measured %.2f): under the stored sleep threshold %.2f V, holding %d s (stored time %d s)"
              % (now(), V_UNDER, v, sleep_v, hold, sleep_t), flush=True)
    else:
        print("%s PSU to %.2f V (measured %.2f): the stored sleep threshold (<%.2f V for %d s) is out of the supply's "
              "reach on this chip; holding %d s all the same, the chip must stay awake"
              % (now(), V_UNDER, v, sleep_v, sleep_t, hold), flush=True)
    t0 = time.time()
    seen = []
    aborted = ""
    while time.time() - t0 < hold:
        cv, detail = chip_volts()
        seen.append(cv)
        sl = sleep_status()
        print("%s  t=%3.0f s: the chip reads %s V, the device %s (sleeps in %s s)"
              % (now(), time.time() - t0, cv, sl.get("state"), sl.get("sleep_in_s")), flush=True)
        if sl.get("state") in ("sleeping", "wake_pending") or (sl.get("pending") != "none" and (sl.get("sleep_in_s") or 999) < 45):
            aborted = "the device's own countdown came too close: %s" % json.dumps({k: sl.get(k) for k in ("state", "pending", "sleep_in_s")})
            break
        time.sleep(30 if time.time() - t0 + 30 < hold else max(0.0, hold - (time.time() - t0)))
    if reach:
        check("VL sleep: the chip's own reading stayed under the stored threshold through the hold",
              seen and all(x is not None and x < sleep_v for x in seen), seen)
    else:
        skip("VL sleep: the chip's own reading stayed under the stored threshold through the hold",
             "stored <%.2f V for %d s, the chip read %s V: no supply voltage the device runs on is under it"
             % (sleep_v, sleep_t, seen[:3]))
    check("VL sleep: the hold ran its full length without the device's own sleep", not aborted, aborted or "%d s" % hold)
    ln, detail = probe("ATI")
    check("VL sleep: ATI answers after %d s at %.1f V: the chip did not sleep" % (hold, V_UNDER), answered(ln), detail)
    v = psu_to(setpoint)
    t1 = time.time()
    while time.time() - t1 < 40 and sleep_status().get("state") != "normal":
        time.sleep(2)
    check("VL sleep: the PSU back at %.2f V and the device back to normal" % setpoint,
          abs(v - setpoint) < 0.1 and sleep_status().get("state") == "normal", "%.2f V, %s" % (v, sleep_status().get("state")))


def leg_vl_wake(vals, setpoint):
    _, wake_v, wake_t = vals.get("vl_wake", ("OFF", 13.5, 1))
    ln, detail = probe("VTPPSW2F")
    check("VL wake: VTPPSW2F over the probe answered OK (the pin cannot wake the chip)", answered(ln) and "OK" in str(ln.get("text")), detail)
    wait_ready(20)
    ln, detail = probe("VTPPSWS")
    check("VL wake: the probe reads PPSW 2F", ln is not None and "2F" in str(ln.get("text")), detail)
    wait_ready(20)
    v = psu_to(V_LOW)
    cv, detail = chip_volts()
    check("VL wake: the PSU at %.2f V (measured %.2f), the chip reads under the stored wake threshold %.2f V" % (V_LOW, v, wake_v),
          cv is not None and cv < wake_v, cv)
    ln, detail = probe("STSLEEP0")
    check("VL wake: STSLEEP0 over the probe answered OK", answered(ln) and "OK" in str(ln.get("text")), detail)
    time.sleep(3)
    ln, detail = probe("ATI")
    check("VL wake: ATI gets nothing: asleep by command, and the UART word did not wake it (STSLU wake off)", silent(ln), detail)
    over = V_OVER > wake_v
    v = psu_to(V_OVER)
    if over:
        print("%s PSU to %.2f V (measured %.2f): over the stored wake threshold %.2f V (stored hold %d s); waiting 20 s" % (now(), V_OVER, v, wake_v, wake_t), flush=True)
    else:
        print("%s PSU to %.2f V (measured %.2f): the stored wake threshold (>%.2f V) is out of the supply's reach on "
              "this chip; waiting 20 s all the same, the chip must stay asleep" % (now(), V_OVER, v, wake_v), flush=True)
    time.sleep(20)
    ln, detail = probe("ATI")
    check("VL wake: ATI gets nothing 20 s at %.1f V: the chip did not wake" % V_OVER, silent(ln), detail)
    time.sleep(10)
    ln, detail = probe("ATI")
    check("VL wake: ATI gets nothing 30 s at %.1f V" % V_OVER, silent(ln), detail)
    ok, oc = restart()
    check("VL wake: the planned restart brought the chip back", ok, json.dumps({k: oc.get(k) for k in ("ready", "claim")}))
    ring = [l for l in ring_lines() if "obd_chip:" in l]
    check("VL wake: the boot found the chip asleep ('chip not ready', READY high for a full second)",
          any("chip not ready (READY stayed high" in l for l in ring), ring[:4])
    # the reset line is the last resort (issue 1023): the chip is asked on the
    # wire at both UART speeds first, then pulsed once, and never twice
    asked = [i for i, l in enumerate(ring) if "asking it on the wire before any reset" in l]
    pulsed = [i for i, l in enumerate(ring) if "hardware reset, the last resort" in l]
    check("VL wake: the chip was asked on the wire before the reset line was used",
          bool(asked) and bool(pulsed) and asked[0] < pulsed[0],
          [l for l in ring if "asking it" in l or "nothing received" in l or "the last resort" in l][:5])
    rs = (oc or {}).get("reset") or {}
    check("VL wake: one reset pulse and no more (reset.pulses 1, refused 0, wake_pulsed true)",
          rs.get("pulses") == 1 and rs.get("refused") == 0 and rs.get("wake_pulsed") is True, json.dumps(rs))
    check("VL wake: the boot repaired PPSW (2F to 10, read back before the reset)",
          any("PPSW is 2F, setting to 10" in l for l in ring) and any("PPSW reads 10 after the write" in l for l in ring), [l for l in ring if "PPSW" in l])
    check("VL wake: no provisioning of the sleep config at that boot", not any("provisioning sleep config" in l for l in ring), [l for l in ring if "provision" in l])
    cf = chip_faults.chip(faults())
    check("VL wake: the wake by the last resort is latched (obd_chip_silent, 'came back after the reset pulse')",
          "came back after the reset pulse" in str(cf.get("obd_chip_silent")), json.dumps(cf))
    cv, detail = chip_volts()
    if over:
        check("VL wake: awake after the reset the chip reads over the wake threshold (%.2f V)" % wake_v, cv is not None and cv > wake_v, cv)
    else:
        skip("VL wake: awake after the reset the chip reads over the wake threshold (%.2f V)" % wake_v,
             "the chip reads %s V: no supply voltage is over it" % cv)
        check("VL wake: awake after the reset the chip reads the supply (%.1f V)" % V_OVER,
              cv is not None and abs(cv - V_OVER) < 0.5, cv)
    v = psu_to(setpoint)
    check("VL wake: the PSU back at %.2f V" % setpoint, abs(v - setpoint) < 0.1, "%.2f V" % v)


def leg_uart_silence(vals):
    _, uart_t = vals.get("uart_sleep", ("OFF", 1200))
    secs = UART_MIN * 60
    check("UART sleep: the silence (%d s) outlasts the stored timeout (%d s)" % (secs, uart_t), secs > uart_t, "%d s" % uart_t)
    oc0 = chip()
    tx0 = oc0.get("uart", {}).get("tx_bytes")
    print("%s UART sleep: %d min of silence on the chip's UART starts (tx_bytes %s); /api/info only meanwhile" % (now(), UART_MIN, tx0), flush=True)
    t0 = time.time()
    while time.time() - t0 < secs:
        time.sleep(60)
        st, _ = http(DUT, "/api/info", timeout=5)
        print("%s  t=%4.0f s: the device %s" % (now(), time.time() - t0, "answers" if st == 200 else "silent"), flush=True)
    oc1 = chip()
    tx1 = oc1.get("uart", {}).get("tx_bytes")
    check("UART sleep: nothing was sent to the chip meanwhile (tx_bytes %s -> %s)" % (tx0, tx1), tx0 is not None and tx1 == tx0)
    ln, detail = probe("ATI")
    check("UART sleep: ATI answers after %d min of silence: the chip did not sleep" % UART_MIN, answered(ln), detail)


def boot_count():
    st, h = http(DUT, "/api/restart/history")
    return h.get("boot_count") if st == 200 and isinstance(h, dict) else None


def put_setting(comp, patch):
    """merge @p patch into the component's stored settings (the PUT stores
    the document and does not reboot: no config_apply record, pending_reboot
    false), then a planned restart makes it the running one: back with the
    chip ready, one boot more"""
    st, cur = http(DUT, "/api/settings/" + comp)
    if st != 200 or not isinstance(cur, dict):
        return False, "GET %s %s" % (st, cur)
    cur.update(patch)
    before = boot_count()
    st, r = http(DUT, "/api/settings/" + comp, "PUT", cur, timeout=10)
    if st != 200:
        return False, "PUT %s %s" % (st, r)
    ok, oc = restart()
    after = boot_count()
    return (ok and before is not None and after == before + 1,
            "PUT %s %s; boots %s -> %s" % (st, json.dumps(r)[:60] if r is not None else r, before, after))


def leg_provision(lines0):
    """the provisioning after the change of 2026-11-07: a hidden settings
    number provisions nothing; a control switched on provisions the four
    controls off and leaves the stored thresholds alone"""
    # 14000: inside the field's range and above sleep_voltage_mv (13200), which
    # the validator insists on; 13000 is refused with a 400
    ok, detail = put_setting("obd_chip", {"wake_voltage_mv": 14000})
    check("provision: obd_chip wake_voltage_mv 14000 stored and a planned restart made it the running one", ok, detail)
    ring = ring_lines()
    check("provision: no 'provisioning sleep config' at that boot (the setting is not compared with the chip)",
          not any("provisioning sleep config" in l for l in ring), [l for l in ring if "provision" in l])
    lines = stslcs_lines(report())
    check("provision: STSLCS as found after the settings change (the stored 13.50 V untouched)",
          lines == lines0, [l for l in lines if l not in lines0] or "same")
    ok, detail = put_setting("obd_chip", {"wake_voltage_mv": 13500})
    check("provision: the setting put back to 13500 mV", ok, detail)
    # the probe's stimulus is one word without spaces (commas are fine); the
    # chip reads STSLVLon,off as STSLVL on, off
    ln, detail = probe("STSLVLon,off")
    check("provision: STSLVLon,off over the probe answered OK (the chip's voltage sleep trigger ON: one EEPROM write)",
          answered(ln) and "OK" in str(ln.get("text")), detail)
    wait_ready(20)
    vals = stslcs_values(stslcs_lines(report()))
    print("%s provision: STSLCS right after the write, before any reset: VL_SLEEP %s" % (now(), vals.get("vl_sleep")), flush=True)
    ok, oc = restart()
    check("provision: the planned restart brought the chip back", ok, json.dumps({k: oc.get(k) for k in ("ready", "claim")}))
    ring = [l for l in ring_lines() if "obd_chip:" in l]
    check("provision: the boot provisioned ('provisioning sleep config: a control is on')",
          any("provisioning sleep config: a control is on" in l for l in ring), [l for l in ring if "provision" in l] or ring[:6])
    lines = stslcs_lines(report())
    vals = stslcs_values(lines)
    check("provision: the four controls OFF again",
          all(vals.get(k, ("?",))[0] == "OFF" for k in ("vl_sleep", "vl_wake", "uart_sleep", "uart_wake")), vals)
    check("provision: the stored thresholds and timeout untouched (STSLCS as found)",
          lines == lines0, [l for l in lines if l not in lines0] or "same")
    g = oc.get("eeprom_guard", {})
    check("provision: PP 0F went out inside the provisioning, no separate restore (pp_restores 0)",
          g.get("pp_restores") == 0, json.dumps(g))


def leg_end(lines0, setpoint):
    oc = wait_ready(20)
    rep = report()
    lines1 = stslcs_lines(rep)
    check("end: STSLCS line for line as found", lines1 == lines0, [l for l in lines1 if l not in lines0] or "same")
    check("end: VTPPSWS 10", ppsw(rep) == "10", ppsw(rep))
    g = oc.get("eeprom_guard", {})
    check("end: no PP 0F restore this boot (pp_restores 0)", g.get("pp_restores") == 0, json.dumps(g))
    sl = sleep_status()
    check("end: the device awake, the PSU at %.2f V" % setpoint,
          sl.get("state") == "normal" and abs(PSU.voltage_setpoint() - setpoint) < 0.01, "%s, %.2f V" % (sl.get("state"), PSU.voltage_setpoint()))
    ok, what = chip_faults.clear_own(FOUND.get("faults", {}), faults(), ("obd_chip_silent",),
                                     lambda: http(DUT, "/api/faults/clear", "POST", {}))
    check("end: the fault store holds nothing the bench did not put there", ok, what)
    print("%s fault codes: %s" % (now(), what), flush=True)


def main():
    global PSU
    print("%s OBD SLEEP TRIGGERS bench on %s (UART silence %s, provisioning leg %s)" % (
        now(), DUT, "%d min" % UART_MIN if UART_MIN else "skipped", "on" if PROVISION else "off"), flush=True)
    if not wait_dut(60):
        print("the DUT does not answer through %s" % DUT)
        print("OBD SLEEP TRIGGERS FAIL (no DUT)")
        sys.exit(1)
    PSU = open_psu()
    setpoint = PSU.voltage_setpoint()
    try:
        lines0, vals, setpoint = leg_as_found()
        if not ONLY_PROVISION:
            leg_vl_sleep(vals, setpoint)
            leg_vl_wake(vals, setpoint)
        if UART_MIN > 0:
            leg_uart_silence(vals)
        if PROVISION:
            leg_provision(lines0)
        leg_end(lines0, setpoint)
    finally:
        try:
            PSU.set_voltage(setpoint)
            PSU.output(True)
        except Exception as exc:  # noqa: BLE001
            print("%s PSU restore: %s" % (now(), exc), flush=True)
        PSU.close()
    print("%s planned restarts: %d" % (now(), RESTARTS), flush=True)
    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    for s in SKIPS:
        print("SKIP:", s)
    print("OBD SLEEP TRIGGERS %s (%d checks, %d failed, %d planned restarts%s)" % (
        "PASS" if not fails else "FAIL", len(CHECKS), len(fails), RESTARTS,
        ", %d not testable on this chip's stored thresholds" % len(SKIPS) if SKIPS else ""))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
