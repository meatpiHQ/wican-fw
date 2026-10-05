"""The crash-loop brake and the crash park on the production firmware.

Three runs in a row that crash before they settle, and the fourth boot must
not start the firmware again (TASK_crash_loop_brake.md): it parks asleep,
LED breathing red, so a bad firmware or a bad setting cannot drain the
vehicle's battery or wear the flash. This bench crashes the DUT that often
on purpose (`restart_tracker --panic=fault` over the console, no
`--settle` in between) and judges the park from the outside: the console,
the HTTP port, the supply current.

  p0  as found: the brake at rest; the supply current awake, and asleep in
      the firmware's own sleep mode (`sleep test 30`), as the two
      references; the LED chip breathing red on request, read back from its
      registers (the one part of a park no instrument on this rig can see)
  p1  two quick crashes: the streak counts 1, then 2; the device still boots
  p2  the third: parked. `WICAN PARK streak=3 parks=1`, no LED or OBD chip
      complaint, HTTP silent, the supply current at the sleep level
  p3  the park ends by its timer (the bench's one-shot knob, 40 s): one
      normal start, `park_retry` in the history, the streak kept, the
      stored report says "parked", two small flash writes for the whole
      loop, and the awake current is back (USB rail, OBD chip)
  p4  that start crashes too: parked at once, not after three more, and
      with no timer this time it stays parked
  p5  a power cycle is a fresh start: a normal boot, the count empty, the
      crash report still there for the user
  p6  as found: the report cleared, settings untouched, no fault

    python crash_park_bench.py localhost:18081

`.\\test.ps1 crashnote` runs it as its second stage. The bench holds the
DUT's console and the PSU. The button's two ways out of a park (a 3 s hold
= one start; keep holding = safe mode with the crash report) need a hand on
the device and are not in here: TESTING.md has the manual check. Last line
`CRASH PARK PASS` or `CRASH PARK FAIL: ...`.
"""
import argparse
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", "wifi"))
sys.path.insert(0, HERE)

import bench_ports  # noqa: E402
import crash_note  # noqa: E402
import pcbench  # noqa: E402
from crash_note_bench import (SETTINGS_KEPT, newest, report_text,  # noqa: E402
                              uptime_s, wait_boot)
from owon_psu import OwonPsu  # noqa: E402

CRASH = "restart_tracker --panic=fault"
PARK_LINE = re.compile(r"WICAN PARK streak=(\d+) parks=(\d+) retry_s=(\d+) "
                       r"bare=(\d+)")
RETRY_S = 40            # the bench's knob: the first park ends by itself
PARK_OVER_SLEEP_A = 0.008   # the park may draw this much more than sleep
                            # mode: the LED's breath, and the meter's noise


def mA(amps):
    return f"{amps * 1000:.0f} mA"


def settled_current(psu, secs=8):
    """The median of the supply current over `secs`."""
    return statistics.median(psu.sample_current(secs, 0.5))


def wait_uptime(dut, at_least):
    """Wait until the DUT's run is `at_least` seconds old: its boot is over
    and the current it draws is the awake current."""
    end = time.time() + at_least + 60
    while time.time() < end:
        code, st = dut.api("/api/status", timeout=4)
        up = uptime_s(st) if code == 200 and isinstance(st, dict) else None
        if up is not None and up >= at_least:
            return
        time.sleep(2)
    raise pcbench.Bench(f"the DUT did not reach {at_least} s of uptime")


def silent_for(dut, secs):
    """True when the HTTP port answers nothing for `secs`."""
    end = time.time() + secs
    while time.time() < end:
        if dut.boot_count() is not None:
            return False
        time.sleep(2)
    return True


def led_breathes(run, dut, con):
    """The breathing pattern on the LED chip, read back while the firmware
    is up: the park sets the same registers through the same code, and
    nobody can ask the chip then. PATST is the chip's own word that its
    pattern engine runs on the red channel."""
    code, _ = dut.api("/api/led", "PUT",
                      {"r": 255, "g": 0, "b": 0, "mode": "breathe"})
    time.sleep(1.5)
    led = dut.api("/api/led")[1]
    n0 = len(con.snapshot())
    con.cmd("led -d", r"^OK", 8)
    regs = {}
    for _, ln in con.snapshot()[n0:]:
        m = re.match(r"(\w+)\s+\(0x[0-9A-Fa-f]{2}\) = 0x([0-9A-Fa-f]{2})", ln)
        if m:
            regs[m.group(1)] = int(m.group(2), 16)
    said = con.cmd("led", r"^LED: ", 8)
    dut.api("/api/led", "DELETE")
    run.check("p0_led_chip_breathes_red",
              code == 200 and isinstance(led, dict)
              and led.get("mode") == "breathe"
              and regs.get("LCTR", 0) & 0x07 == 0x07
              and regs.get("LCFG0", 0) & 0x10 != 0
              and regs.get("PATST", 0) & 0x01 != 0
              and regs.get("L0T0") == 0x61 and regs.get("L0T1") == 0x68
              and said is not None
              and "alert breathe rgb(255,0,0)" in said,
              f"GET {led}; " + " ".join(f"{k}={v:#04x}" for k, v in
                                        regs.items()) + f"; `{said}`")
    after = dut.api("/api/led")[1]
    run.check("p0_led_back_to_idle", isinstance(after, dict)
              and after.get("priority") == "idle", str(after))


def crash_and_boot(run, dut, con, tag, streak):
    """A quick crash the device must survive: it boots, the streak counts."""
    b0 = dut.boot_count()
    mark = time.time() - con.t0
    said = con.cmd(CRASH, r"Triggering test panic", 8)
    run.check(f"{tag}_command_taken", said is not None)
    wait_boot(dut, b0)
    hist = dut.get("/api/restart/history")
    brake = hist.get("brake") or {}
    run.check(f"{tag}_boots_with_streak_{streak}",
              brake.get("verdict") == "normal"
              and brake.get("streak") == streak
              and newest(hist).get("mode") == "normal", str(brake))
    said = con.wait_for(rf"crash-loop brake: {streak} of 3 quick crashes", 20,
                        since=mark)
    run.check(f"{tag}_log_says_{streak}_of_3", said is not None)
    return hist


def crash_and_park(run, dut, con, psu, tag, streak, parks, retry_s):
    """A quick crash that must park the device. Returns (mark, time of the
    park line, parked current in A)."""
    mark = time.time() - con.t0
    said = con.cmd(CRASH, r"Triggering test panic", 8)
    run.check(f"{tag}_command_taken", said is not None)
    hit = con.wait_for(PARK_LINE.pattern, 60, since=mark)
    t_park = time.time()
    m = PARK_LINE.search(hit[1]) if hit else None
    got = tuple(int(x) for x in m.groups()) if m else None
    run.check(f"{tag}_parked", got == (streak, parks, retry_s, 0),
              hit[1].strip() if hit else "no WICAN PARK line within 60 s")
    if not hit:
        raise pcbench.Bench("the DUT did not park")
    # the board goes down in under a second; then it only naps
    time.sleep(8)
    amps = settled_current(psu, 12)
    lines = [ln for ts, ln in con.snapshot() if ts >= mark]
    bad = [ln for ln in lines if "park: LED" in ln
           or "OBD chip awake while parked" in ln]
    run.check(f"{tag}_led_and_chip_did_as_told", not bad,
              " | ".join(bad)[:200])
    run.check(f"{tag}_says_how_to_get_out",
              any("hold the button" in ln and "safe mode" in ln
                  for ln in lines))
    return mark, t_park, amps


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dut", help="host:port of the DUT's HTTP API")
    ap.add_argument("--console", default="auto")
    ap.add_argument("--psu", default="auto")
    a = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    from wican_fresh_bench import Console
    run = pcbench.Run("CRASH PARK")
    dut = pcbench.Dut(a.dut, own_tags=("restart_tracker", "park"))
    port = bench_ports.resolve(a.console, ("wican_console", "ch342_console"),
                               "COM7")
    con = Console(port, 2000000)
    print(f"console {port} held by the bench (an open may reset the DUT once)",
          flush=True)
    psu = OwonPsu(bench_ports.resolve(a.psu, "psu", "COM2016"))
    parked = False
    rc = 1
    try:
        try:
            psu.meas_current()      # a cold OWON's first answer is not one
        except (AssertionError, ValueError):
            pass
        volts = psu.voltage_setpoint()
        time.sleep(4)
        wait_boot(dut, None, 120)
        dut.as_found()
        kept = {n: dut.settings(n) for n in SETTINGS_KEPT}
        st = dut.get("/api/status")
        hist = dut.get("/api/restart/history")
        print(f"as found: {st.get('version')}, boot {st.get('boot_count')}, "
              f"unexpected_resets {st.get('unexpected_resets')}, PSU "
              f"{volts} V, brake {hist.get('brake')}", flush=True)
        if hist.get("report"):
            print("a crash report was stored BEFORE this run; its text, "
                  "because the run replaces it:\n" + report_text(dut)[1],
                  flush=True)
            dut.api("/api/restart/report", "DELETE")

        # p0: the two references
        wait_uptime(dut, 30)
        awake_a = settled_current(psu)
        run.metric("awake_ma", round(awake_a * 1000))
        led_breathes(run, dut, con)
        b0 = dut.boot_count()
        mark = time.time() - con.t0
        said = con.cmd("sleep test 30", r"entering test sleep|TEST sleep", 8)
        napping = con.wait_for(r"components down; napping", 60, since=mark)
        run.check("p0_sleep_mode_entered", said is not None
                  and napping is not None, "`sleep test 30`")
        time.sleep(6)
        sleep_a = settled_current(psu, 12)
        run.metric("sleep_mode_ma", round(sleep_a * 1000))
        wait_boot(dut, b0)
        hist = dut.get("/api/restart/history")
        brake = hist.get("brake") or {}
        run.check("p0_brake_at_rest",
                  brake.get("verdict") == "normal" and brake.get("streak") == 0
                  and newest(hist).get("planned_reason") == "power_wake",
                  str(brake))
        budget0 = brake.get("report_budget")

        # p1: two quick crashes; the knob makes the first park end by itself
        said = con.cmd(f"restart_tracker --park-retry {RETRY_S}",
                       r"The next park ends by itself", 8)
        run.check("p1_knob_taken", said is not None)
        crash_and_boot(run, dut, con, "p1a", 1)
        crash_and_boot(run, dut, con, "p1b", 2)

        # p2: the third parks
        mark, t_park, park_a = crash_and_park(run, dut, con, psu, "p2", 3, 1,
                                              RETRY_S)
        parked = True
        run.metric("parked_ma", round(park_a * 1000))
        run.check("p2_parked_draws_what_sleep_mode_draws",
                  park_a <= sleep_a + PARK_OVER_SLEEP_A
                  and park_a <= 0.5 * awake_a,
                  f"parked {mA(park_a)}, sleep mode {mA(sleep_a)}, awake "
                  f"{mA(awake_a)}")
        run.check("p2_http_silent_while_parked", dut.boot_count() is None)

        # p3: the knob's timer ends the park: one normal start
        hit = con.wait_for(r"park ends \(its timer\)", RETRY_S + 30,
                           since=mark)
        took = time.time() - t_park
        run.check("p3_park_ended_by_its_timer",
                  hit is not None and RETRY_S - 2 <= took <= RETRY_S + 12,
                  f"{took:.0f} s after the park line, asked for {RETRY_S} s")
        run.metric("park_lasted_s", round(took, 1))
        wait_boot(dut, None, 120)
        parked = False
        hist = dut.get("/api/restart/history")
        recs = sorted(hist.get("records", []), key=lambda r: -r.get("seq", 0))
        now, before = recs[0], recs[1]
        run.check("p3_history_tells_the_story",
                  now.get("planned") is True
                  and now.get("planned_reason") == "park_retry"
                  and now.get("source") == "park" and now.get("mode") == "normal"
                  and before.get("mode") == "park"
                  and before.get("planned") is False
                  and isinstance(before.get("crash"), dict),
                  f"now {now.get('planned_reason')}/{now.get('source')}/"
                  f"{now.get('mode')}, before {before.get('reason')}/"
                  f"{before.get('mode')}")
        brake = hist.get("brake") or {}
        run.check("p3_streak_kept_for_the_one_try",
                  brake.get("verdict") == "normal" and brake.get("streak") == 3
                  and brake.get("parks") == 1, str(brake))
        said = con.wait_for(r"crash-loop brake: one try after a park", 20,
                            since=mark)
        run.check("p3_log_says_one_try", said is not None)
        rep = hist.get("report") or {}
        code, text = report_text(dut)
        run.check("p3_report_says_parked",
                  rep.get("parked") is True and rep.get("streak") == 3
                  and code == 200
                  and "Loop:      3 crashes in a row; the device parked "
                  "itself" in text, f"parked {rep.get('parked')}, streak "
                  f"{rep.get('streak')}")
        # the wear guard through a real loop: the crash once, "parked" once
        stored = [ln for _, ln in con.snapshot() if "crash report stored" in ln]
        run.check("p3_loop_cost_two_flash_writes",
                  len(stored) == 2 and isinstance(budget0, int)
                  and brake.get("report_budget") == budget0 - 2,
                  f"{len(stored)} `stored` lines, budget {budget0} -> "
                  f"{brake.get('report_budget')}")
        wait_uptime(dut, 30)
        back_a = settled_current(psu)
        run.metric("awake_after_park_ma", round(back_a * 1000))
        run.check("p3_start_after_park_gives_everything_back",
                  back_a >= 0.9 * awake_a,
                  f"{mA(back_a)} against {mA(awake_a)} before")

        # p4: the try crashes too: parked at once, and for good this time
        mark, t_park, park2_a = crash_and_park(run, dut, con, psu, "p4", 4, 2,
                                               0)
        parked = True
        run.metric("parked_again_ma", round(park2_a * 1000))
        run.check("p4_parked_again_at_the_sleep_level",
                  park2_a <= sleep_a + PARK_OVER_SLEEP_A,
                  f"parked {mA(park2_a)}, sleep mode {mA(sleep_a)}")
        quiet = silent_for(dut, RETRY_S + 10)
        ended = con.find(r"park ends", since=mark)
        run.check("p4_stays_parked_without_a_timer", quiet and ended is None,
                  f"{RETRY_S + 10} s watched")
        stored = [ln for _, ln in con.snapshot() if "crash report stored" in ln]
        run.check("p4_failed_try_wrote_no_flash", len(stored) == 2,
                  f"{len(stored)} `stored` lines in the whole run")

        # p5: the user's way out: power off, power on
        psu.output(False)
        time.sleep(4)
        psu.output(True)
        time.sleep(6)
        wait_boot(dut, None, 180)
        parked = False
        st = dut.get("/api/status")
        hist = dut.get("/api/restart/history")
        brake = hist.get("brake") or {}
        run.check("p5_power_cycle_is_a_fresh_start",
                  st.get("boot_count") == 1 and st.get("unexpected_resets") == 0
                  and brake.get("verdict") == "normal"
                  and brake.get("streak") == 0 and brake.get("parks") == 0
                  and not crash_note.notes(hist), f"boot "
                  f"{st.get('boot_count')}, brake {brake}")
        code, text5 = report_text(dut)
        run.check("p5_report_still_there_for_the_user",
                  code == 200 and text5 == text
                  and (hist.get("report") or {}).get("parked") is True,
                  f"HTTP {code}, {len(text5)} bytes against {len(text)}")
        print(text5, flush=True)
        wait_uptime(dut, 30)
        back_a = settled_current(psu)
        run.metric("awake_after_power_cycle_ma", round(back_a * 1000))
        run.check("p5_awake_current_after_power_cycle",
                  back_a >= 0.9 * awake_a, f"{mA(back_a)} against "
                  f"{mA(awake_a)} before")

        # p6: as found
        dut.passing_checks(run)
        code, body = dut.api("/api/restart/report", "DELETE")
        run.check("p6_report_cleared", code == 200 and isinstance(body, dict)
                  and body.get("cleared") is True
                  and report_text(dut)[0] == 404, f"DELETE {code} {body}")
        now_s = {n: dut.settings(n) for n in SETTINGS_KEPT}
        changed = [n for n in SETTINGS_KEPT if now_s[n] != kept[n]]
        run.check("p6_settings_untouched", not changed, ", ".join(changed))
        code, faults = dut.api("/api/faults")
        run.check("p6_no_fault_latched", code == 200
                  and isinstance(faults, dict)
                  and faults.get("faults") == [], str(faults)[:200])
        rc = run.verdict()
    except pcbench.Bench as e:
        run.check("bench_could_go_on", False, str(e))
        rc = run.verdict()
    finally:
        if parked:
            # never leave the rig parked: a power cycle is the way out
            print("the DUT is parked: power cycle to release it", flush=True)
            try:
                mark = time.time() - con.t0
                psu.output(False)
                time.sleep(4)
                psu.output(True)
                # its next boot on the console: the capture must not end on
                # the park (the console says so when it is closed)
                con.wait_for(r"restart_tracker: boot", 40, since=mark)
            except (AssertionError, OSError, ValueError) as e:
                print(f"PSU: {e}", flush=True)
        psu.close()
        con.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
