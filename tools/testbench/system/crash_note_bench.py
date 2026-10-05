"""The crash note and the stored crash report on the production firmware.

A WiCAN that crashed must be able to say where, and still say it after it
was unplugged (TASK_crash_note.md). This bench crashes the DUT on purpose
in the three ways the restart tracker tells apart (`restart_tracker
--panic`, `--panic=fault`, `--panic=wdt`, over the console) and judges what
the NEXT boot says about each, in RAM and in flash:

  c0  the ELF on this PC is the firmware on the DUT (image id)
  c1  abort        reset `panic`, kind abort, the abort text
  c2  fault        kind exception, StoreProhibited at the test address
  c3  fault again  the same crash: its note is filed, flash is not written
  c4  watchdog     reset `interrupt_wdt`, kind int_wdt
      each: the note's frames are the console's `Backtrace:` frames, the
      decoded frames name the function that crashed, the task and the
      uptime are right; a crash that is news is stored in NVS by ONE small
      write of the boot that filed it, and is then the stored report
  c5  a planned restart carries no note and writes nothing, the notes
      before it keep theirs
  c6  one `previous run crashed` log line per crash, no E line of the tracker
  c7  tools/crash_decode.py decodes the notes, the stored report, and the
      report text a user would send
  c8  a PSU power cycle empties counters, history and notes, and the stored
      report is still there, word for word, read without a flash write
  c9  as found: the report cleared (DELETE /api/restart/report), settings
      untouched, no fault

    python crash_note_bench.py localhost:18081 [--no-power-cycle]

`.\\test.ps1 crashnote` opens the tunnel and runs it, then the crash park's
bench (crash_park_bench.py). The bench holds the DUT's console itself (it
types the commands there). Every run that follows a crash is marked settled
(`restart_tracker --settle`), so the four crashes are four first crashes
and the crash-loop brake stays out of this bench. Last line
`CRASH NOTE PASS` or `CRASH NOTE FAIL: ...`.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", "wifi"))

import bench_ports  # noqa: E402
import crash_note  # noqa: E402
import pcbench  # noqa: E402
from owon_psu import OwonPsu  # noqa: E402

ROOT = crash_note.ROOT
FAULT_ADDR = "0x0000bad0"           # restart_tracker_cli.c, RT_CLI_FAULT_ADDR
BT_ENTRY = re.compile(r"0x([0-9a-fA-F]{8}):0x[0-9a-fA-F]{8}")
FLASH_LINE = re.compile(r"WICAN FLASH writes=(\d+) wbytes=(\d+) "
                        r"erases=(\d+) ebytes=(\d+)")
SETTINGS_KEPT = ("can_manager", "autopid", "sleep_manager", "j1939",
                 "restart_tracker")
# One NVS blob of 360 bytes: a handful of 32-byte entries and their state
# bits, and at most one page erase when the page it lands on is full.
STORE_MAX_WRITES = 96
STORE_MAX_BYTES = 2048

KINDS = [
    # name, console command, reset reason, note kind, a function the decoded
    # backtrace must name, True when the crash is news for the stored report
    ("abort", "restart_tracker --panic", "panic", "abort",
     "cmd_restart_tracker", True),
    ("fault", "restart_tracker --panic=fault", "panic", "exception",
     "panic_fault", True),
    ("fault_again", "restart_tracker --panic=fault", "panic", "exception",
     "panic_fault", False),
    ("wdt", "restart_tracker --panic=wdt", "interrupt_wdt", "int_wdt",
     "panic_wdt", True),
]


def uptime_s(st):
    """/api/status `uptime` (HH:MM:SS) in seconds, or None."""
    m = re.fullmatch(r"(\d+):(\d\d):(\d\d)", str(st.get("uptime", "")))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) \
        if m else None


def wait_boot(dut, b0, timeout=150):
    """The boot count once it is no longer `b0`; Bench when it stays."""
    end = time.time() + timeout
    while time.time() < end:
        b = dut.boot_count()
        if b is not None and b != b0:
            return b
        time.sleep(1)
    raise pcbench.Bench(f"the DUT did not come back within {timeout} s"
                        + pcbench.GONE_HINT)


def unpark(dut, con, psu_arg):
    """A bench must not leave the rig parked. When the DUT does not answer
    and its console said `WICAN PARK`, say so with the lines that tell why,
    and power-cycle it. True when it had parked."""
    if dut.boot_count() is not None or con.find(r"WICAN PARK") is None:
        return False
    print("THE DUT PARKED ITSELF (three crashes in a row before any run "
          "settled). Its console:", flush=True)
    for _, ln in con.snapshot():
        if re.search(r"previous run crashed|crash-loop brake|WICAN PARK|"
                     r"Run marked settled", ln):
            print("   " + ln[:200], flush=True)
    print("power-cycling it: the stored report (GET /api/restart/report) "
          "keeps the crash that parked it", flush=True)
    mark = time.time() - con.t0
    psu = OwonPsu(bench_ports.resolve(psu_arg, "psu", "COM2016"))
    try:
        psu.output(False)
        time.sleep(4)
        psu.output(True)
    finally:
        psu.close()
    # its next boot on the console: the capture must not end on the park
    back = con.wait_for(r"restart_tracker: boot", 40, since=mark)
    print("it boots again" if back else "no boot line after the power cycle",
          flush=True)
    return True


def newest(hist):
    return max(hist.get("records", []), key=lambda r: r.get("seq", 0))


def console_frames(lines):
    """The frames of the first `Backtrace:` line in `lines` that arrived
    whole. At 2 Mbaud the line may break off into garbage: stop there."""
    for ln in lines:
        if "Backtrace:" not in ln:
            continue
        out = []
        for tok in ln.split("Backtrace:", 1)[1].split():
            m = BT_ENTRY.fullmatch(tok)
            if not m:
                break
            out.append("0x" + m.group(1).lower())
        return out
    return []


def flash_of_boot(con, mark, timeout=40):
    """(writes, wbytes, erases, ebytes) of the boot whose lines follow
    `mark`, from its `WICAN FLASH` line; None when the line never came."""
    con.wait_for(r"WICAN FLASH ", timeout, since=mark)
    hits = [FLASH_LINE.search(ln) for ts, ln in con.snapshot() if ts >= mark]
    hits = [m for m in hits if m]
    return tuple(int(x) for x in hits[-1].groups()) if hits else None


def settle(run, con, name):
    """Tell the tracker this run is healthy: the crash that comes next is a
    first crash, not one of a streak (the bench's knob on the brake)."""
    said = con.cmd("restart_tracker --settle", r"Run marked settled", 8)
    return run.check(f"{name}_run_marked_settled", said is not None)


def report_text(dut):
    """GET /api/restart/report: (200, text) or (404, ...)."""
    code, body = dut.api("/api/restart/report")
    return code, body if isinstance(body, str) else str(body)


def crash(run, dut, con, elf, kind, st_version):
    name, command, reset, note_kind, function, news = kind
    st0 = dut.get("/api/status")
    b0, up0 = st0.get("boot_count"), uptime_s(st0)
    mark = time.time() - con.t0
    said = con.cmd(command, r"Triggering test panic", 8)
    run.check(f"{name}_command_taken", said is not None, f"`{command}`")
    t_crash = time.time()
    wait_boot(dut, b0)
    hist = dut.get("/api/restart/history")
    rec = newest(hist)
    c = rec.get("crash") or {}
    print(f"  {crash_note.one_line(rec)}", flush=True)

    run.check(f"{name}_reset_reason",
              rec.get("reason") == reset and rec.get("planned") is False,
              f"reason {rec.get('reason')}, planned {rec.get('planned')}")
    run.check(f"{name}_note_filed",
              bool(c) and c.get("complete") is True
              and c.get("kind") == note_kind,
              f"kind {c.get('kind')}, complete {c.get('complete')}")
    if not c:
        return None
    run.check(f"{name}_image",
              c.get("elf_sha") == hist.get("elf_sha")
              and c.get("same_image") is True,
              f"note {c.get('elf_sha')}, running {hist.get('elf_sha')}")
    if note_kind == "abort":
        ok = c.get("text", "").startswith("abort() was called at PC 0x") \
            and c.get("reason") == ""
    elif note_kind == "exception":
        ok = c.get("reason") == "StoreProhibited" \
            and c.get("excvaddr") == FAULT_ADDR and c.get("cause") == 29
    else:
        ok = c.get("reason", "").startswith("Interrupt wdt timeout on CPU") \
            and c.get("reason", "")[-1:] == str(c.get("core"))
    run.check(f"{name}_what",
              ok, f"reason '{c.get('reason')}', text '{c.get('text')}', "
              f"excvaddr {c.get('excvaddr')}, core {c.get('core')}")
    # a command typed on the console runs on the console's own task
    run.check(f"{name}_task", c.get("task") == "cli_console"
              and c.get("in_isr") is False,
              f"task '{c.get('task')}', in_isr {c.get('in_isr')}")
    # the run was `up0` seconds old when asked, and crashed within seconds
    run.check(f"{name}_uptime",
              up0 is not None
              and up0 <= c.get("uptime_s", -1) <= up0 + 15,
              f"note {c.get('uptime_s')} s, the status said {up0} s "
              "just before")

    lines = [ln for ts, ln in con.snapshot() if ts >= mark]
    con_bt = console_frames(lines)
    mine = c.get("backtrace", [])
    run.check(f"{name}_frames_are_the_consoles",
              len(con_bt) >= 3 and mine[:len(con_bt)] == con_bt[:len(mine)],
              f"{len(mine)} frames in the note, {len(con_bt)} whole on the "
              f"console" + ("" if mine[:len(con_bt)] == con_bt[:len(mine)]
                            else f": {mine} vs {con_bt}"))
    run.metric(f"{name}_frames_note", len(mine))
    run.metric(f"{name}_frames_console_whole", len(con_bt))
    try:
        names = crash_note.functions(crash_note.decode(elf, mine))
    except RuntimeError as e:
        names = [f"({e})"]
    run.check(f"{name}_decoded_names_the_function", function in names,
              crash_note.chain([{"functions": names}], most=6))

    # the boot that filed the note: its own lines on the console
    flash = flash_of_boot(con, mark)
    lines = [ln for ts, ln in con.snapshot() if ts >= mark]
    said = [ln for ln in lines if "previous run crashed:" in ln]
    run.check(f"{name}_one_log_line", len(said) == 1
              and c.get("summary", "\x00") in said[0],
              f"{len(said)} lines" + (f": {said[0][:120]}" if said else ""))

    # the stored report: this crash, by one small write when it is news and
    # by none when the report already says it (the wear guard)
    stored = [ln for ln in lines if "crash report stored" in ln]
    same = [ln for ln in lines
            if "crash report: the stored one is this crash" in ln]
    if news:
        run.check(f"{name}_stored_by_one_small_write",
                  flash is not None and 1 <= flash[0] <= STORE_MAX_WRITES
                  and flash[1] <= STORE_MAX_BYTES and flash[2] <= 1
                  and len(stored) == 1 and not same,
                  f"WICAN FLASH {flash}, {len(stored)} `stored` line(s)")
        if flash:
            run.metric(f"{name}_store_writes", flash[0])
            run.metric(f"{name}_store_bytes", flash[1], "B")
            run.metric(f"{name}_store_erases", flash[2])
    else:
        run.check(f"{name}_same_crash_writes_no_flash",
                  flash == (0, 0, 0, 0) and not stored and len(same) == 1,
                  f"WICAN FLASH {flash}, {len(stored)} `stored` line(s), "
                  f"{len(same)} `is this crash` line(s)")
    rep = hist.get("report") or {}
    rc = rep.get("crash") or {}
    run.check(f"{name}_is_the_stored_report",
              rc.get("pc") == c.get("pc") and rc.get("kind") == c.get("kind")
              and rc.get("backtrace") == mine
              and rc.get("elf_sha") == c.get("elf_sha")
              and rep.get("firmware") == st_version
              and rep.get("parked") is False,
              f"report pc {rc.get('pc')} kind {rc.get('kind')}, firmware "
              f"'{rep.get('firmware')}', parked {rep.get('parked')}")
    run.check(f"{name}_record_mode_normal", rec.get("mode") == "normal",
              f"mode {rec.get('mode')}")
    run.metric(f"{name}_back_after_s", round(time.time() - t_crash, 1))
    settle(run, con, name)
    return rec.get("seq")


def report_checks(run, dut, st, hist, tag):
    """The stored report as text: the lines a user sends on. Returns it."""
    rep = hist.get("report") or {}
    rc = rep.get("crash") or {}
    code, text = report_text(dut)
    device = dut.get("/api/info").get("device_id")
    want = ["WiCAN crash report", f"Device:    {device}",
            f"Firmware:  {rep.get('firmware')}",
            f"Image:     {rc.get('elf_sha')}",
            "Backtrace: " + " ".join(rc.get("backtrace", []))]
    missing = [w for w in want if w not in text]
    run.check(f"{tag}_report_text_has_its_lines", code == 200 and not missing,
              f"HTTP {code}" + (f", missing: {missing}" if missing else ""))
    run.check(f"{tag}_report_text_has_no_loop_line",
              "Loop:" not in text and "parked" not in text)
    return text if code == 200 else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dut", help="host:port of the DUT's HTTP API")
    ap.add_argument("--console", default="auto")
    ap.add_argument("--psu", default="auto")
    ap.add_argument("--no-power-cycle", action="store_true",
                    help="leave counters, notes and the report on the DUT "
                    "(debugging)")
    a = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    from wican_fresh_bench import Console
    run = pcbench.Run("CRASH NOTE")
    dut = pcbench.Dut(a.dut, own_tags=("restart_tracker",))
    port = bench_ports.resolve(a.console, ("wican_console", "ch342_console"),
                               "COM7")
    con = Console(port, 2000000)
    print(f"console {port} held by the bench (an open may reset the DUT once)",
          flush=True)
    rc = 1
    try:
        time.sleep(4)
        wait_boot(dut, None, 120)       # answers, whatever the open did
        dut.as_found()
        kept = {n: dut.settings(n) for n in SETTINGS_KEPT}
        st = dut.get("/api/status")
        hist = dut.get("/api/restart/history")
        print(f"as found: {st.get('version')}, boot {st.get('boot_count')}, "
              f"unexpected_resets {st.get('unexpected_resets')}, image "
              f"{hist.get('elf_sha')}, brake {hist.get('brake')}", flush=True)
        if hist.get("report"):
            # the run replaces it and clears it at the end: keep it here
            print("a crash report was stored BEFORE this run; its text, "
                  "because the run replaces it:\n" + report_text(dut)[1],
                  flush=True)

        # c0: the PC's ELF is the DUT's firmware, or nothing below means much
        elf, why = crash_note.find_elf(hist.get("elf_sha", ""))
        if not run.check("c0_elf_is_the_firmware_on_the_dut", elf is not None,
                         os.path.relpath(elf, ROOT) if elf else why):
            return run.verdict()
        u0 = st.get("unexpected_resets")
        # the run the bench found may be seconds old (the console open):
        # its crash must be a first crash like the others
        settle(run, con, "c0")

        # c1..c4
        seqs = [crash(run, dut, con, elf, k, st.get("version")) for k in KINDS]
        st = dut.get("/api/status")
        hist = dut.get("/api/restart/history")
        run.check("four_unexpected_resets_counted",
                  st.get("unexpected_resets") == u0 + len(KINDS),
                  f"{u0} -> {st.get('unexpected_resets')}")
        brake = hist.get("brake") or {}
        run.check("no_streak_after_four_settled_crashes",
                  brake.get("verdict") == "normal" and brake.get("streak") == 0
                  and brake.get("settled") is True
                  and brake.get("report_budget") == 4, str(brake))
        text_before = report_checks(run, dut, st, hist, "c4")

        # c5: a planned restart has no note, the notes before it stay
        mark = time.time() - con.t0
        dut.restart()
        hist = dut.get("/api/restart/history")
        rec = newest(hist)
        run.check("c5_planned_restart_has_no_note",
                  rec.get("planned") is True and "crash" not in rec,
                  crash_note.one_line(rec))
        have = {r.get("seq") for r, _ in crash_note.notes(hist)}
        run.check("c5_notes_stay", all(s in have for s in seqs),
                  f"filed under boots {seqs}, still there: {sorted(have)}")
        flash = flash_of_boot(con, mark)
        run.check("c5_planned_restart_writes_no_flash", flash == (0, 0, 0, 0),
                  f"WICAN FLASH {flash}")
        lines = [ln for ts, ln in con.snapshot() if ts >= mark]
        run.check("c6_no_log_line_without_a_crash",
                  not any("previous run crashed:" in ln or "crash report" in ln
                          for ln in lines))
        bad = [ln for _, ln in con.snapshot()
               if ln.startswith("E (") and "restart_tracker:" in ln]
        run.check("c6_no_E_line_of_the_tracker", not bad, " | ".join(bad)[:300])

        # c7: the tools a person would use: on the device, and on the text
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "crash_decode.py"),
             "--url", "http://" + a.dut], capture_output=True, text=True,
            timeout=180)
        missing = [k[4] for k in KINDS if k[4] not in r.stdout]
        run.check("c7_crash_decode_tool", r.returncode == 0 and not missing
                  and "stored crash report (kept in flash)" in r.stdout,
                  f"exit {r.returncode}" + (f", not named: {missing}"
                                            if missing else "")
                  + (f"; {r.stderr.strip()[:160]}" if r.stderr.strip() else ""))
        print(r.stdout, flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wican_crash_report.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text_before)
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "crash_decode.py"),
                 "--report", path], capture_output=True, text=True, timeout=180)
        run.check("c7_the_report_text_decodes_on_its_own",
                  r.returncode == 0 and "panic_wdt" in r.stdout,
                  f"exit {r.returncode}; " + r.stdout.strip()[-200:]
                  + r.stderr.strip()[:160])
        print(r.stdout, flush=True)

        # the console's view of the same report, and the store it sits in
        mark_n = len(con.snapshot())
        said = con.cmd("restart_tracker --report", r"^OK", 8)
        out = [ln for _, ln in con.snapshot()[mark_n:]]
        run.check("console_prints_the_report",
                  said is not None
                  and any(ln.startswith("WiCAN crash report") for ln in out)
                  and any(ln.startswith("Brake: verdict=normal") for ln in out),
                  " | ".join(ln for ln in out if ln.startswith(("Brake", "NVS")))
                  [:200])
        nvs = [re.search(r"NVS: (\d+) of (\d+) entries used, (\d+) free", ln)
               for ln in out]
        nvs = [m for m in nvs if m]
        if run.check("nvs_has_room_for_the_report",
                     bool(nvs) and int(nvs[0].group(3)) >= 40,
                     nvs[0].group(0) if nvs else "no NVS line"):
            run.metric("nvs_entries_used", int(nvs[0].group(1)))
            run.metric("nvs_entries_free", int(nvs[0].group(3)))

        dut.passing_checks(run)

        # c8: the power goes. Counters and notes live in RAM; the report
        # does not
        if a.no_power_cycle:
            print("power cycle skipped: the unexpected resets, their notes "
                  "and the stored report stay on the DUT", flush=True)
        else:
            mark = time.time() - con.t0
            psu = OwonPsu(bench_ports.resolve(a.psu, "psu", "COM2016"))
            try:
                volts = psu.voltage_setpoint()
                psu.output(False)
                time.sleep(4)
                psu.output(True)
            finally:
                psu.close()
            time.sleep(6)
            wait_boot(dut, None, 180)
            st = dut.get("/api/status")
            hist = dut.get("/api/restart/history")
            run.check("c8_counters_empty_after_power_cycle",
                      st.get("unexpected_resets") == 0
                      and st.get("boot_count") == 1,
                      f"boot {st.get('boot_count')}, unexpected_resets "
                      f"{st.get('unexpected_resets')}, PSU at {volts} V")
            run.check("c8_no_note_out_of_power_on_memory",
                      not crash_note.notes(hist)
                      and newest(hist).get("reason") == "poweron",
                      "; ".join(crash_note.one_line(r)
                                for r in hist.get("records", []))[:300])
            text_after = report_text(dut)[1]
            run.check("c8_the_report_outlived_the_power",
                      bool(text_before) and text_after == text_before
                      and (hist.get("report") or {}).get("crash", {})
                      .get("kind") == "int_wdt",
                      f"{len(text_after)} bytes after against "
                      f"{len(text_before)} before")
            flash = flash_of_boot(con, mark, 60)
            run.check("c8_reading_it_back_wrote_no_flash",
                      flash == (0, 0, 0, 0), f"WICAN FLASH {flash}")
            brake = hist.get("brake") or {}
            run.check("c8_brake_starts_again", brake.get("streak") == 0
                      and brake.get("verdict") == "normal"
                      and brake.get("report_budget") == 4, str(brake))

            # c9: as found
            code, body = dut.api("/api/restart/report", "DELETE")
            gone = dut.get("/api/restart/history")
            run.check("c9_report_cleared",
                      code == 200 and isinstance(body, dict)
                      and body.get("cleared") is True and "report" not in gone
                      and report_text(dut)[0] == 404,
                      f"DELETE {code} {body}, GET {report_text(dut)[0]}")
            now = {n: dut.settings(n) for n in SETTINGS_KEPT}
            changed = [n for n in SETTINGS_KEPT if now[n] != kept[n]]
            run.check("c9_settings_untouched", not changed, ", ".join(changed))
            code, faults = dut.api("/api/faults")
            run.check("c9_no_fault_latched", code == 200
                      and isinstance(faults, dict)
                      and faults.get("faults") == [], str(faults)[:200])
        rc = run.verdict()
    except pcbench.Bench as e:
        run.check("bench_could_go_on", False, str(e))
        rc = run.verdict()
    finally:
        try:
            unpark(dut, con, a.psu)
        except (AssertionError, OSError, ValueError, SystemExit) as e:
            print(f"the DUT may still be parked (PSU: {e})", flush=True)
        con.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
