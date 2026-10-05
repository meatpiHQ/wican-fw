"""Verdict for the restart_tracker on-target test app (the crash note).

The app (meatpi-components/components/restart_tracker/test_apps) runs one
step per boot and crashes on purpose in most of them; the next boot prints
the note the tracker filed (`RT NOTE`, `RT NOTEBT`) and its own checks
(`RT CHECK`, `RT RESULT`). This script resets the DUT, reads the console to
`TEST DONE`, and adds what only the PC can see: every note against IDF's
own panic text of the same crash (the `Backtrace:` line, the register
dump), and how many panics a step printed.

    python rt_target_check.py COM254 [out.log] [--timeout 240]

Last line `RT TARGET PASS (<n> checks)` or `RT TARGET FAIL ...`; exit 0 / 1.
`test.ps1 target restart_tracker` runs it after flashing the app.
"""
import argparse
import re
import sys
import time

import serial

STEPS = ["planned_restart", "store_in_task", "store_on_psram_stack", "abort",
         "assert", "stack_overflow", "int_wdt_cpu0", "int_wdt_cpu1",
         "store_in_isr", "call_null", "store_cache_off", "corrupt_chain",
         "stage_b_fault", "stage_b_hang", "garbage_store"]
DONE = len(STEPS)                      # the boot that reports on the last step
NO_PANIC = {0, 14}                     # planned restarts
PANICS = {12: 2}                       # stage B's fault is a second panic
BT_ENTRY = re.compile(r"0x([0-9a-fA-F]{8}):0x([0-9a-fA-F]{8})")

fails = []
checks = 0


def check(name, ok, detail=""):
    global checks
    checks += 1
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        fails.append(name)
    return ok


def capture(port, seconds):
    """Reset the DUT (RTS = EN) and read until TEST DONE, a second start of
    the run, or the timeout."""
    s = serial.Serial(port, 115200, timeout=0.2)
    s.dtr = False
    s.rts = True
    time.sleep(0.1)
    s.rts = False
    buf = bytearray()
    deadline = time.time() + seconds
    why = "timeout"
    while time.time() < deadline:
        chunk = s.read(4096)
        if not chunk:
            continue
        buf.extend(chunk)
        if b"TEST DONE" in buf:
            time.sleep(0.5)
            buf.extend(s.read(65536))
            why = "done"
            break
        if buf.count(b"RT STEP 0 ") > 1:
            why = "the run started over (its progress did not survive a reset)"
            break
    s.close()
    text = buf.decode("utf-8", errors="replace")
    return re.sub(r"\x1b\[[0-9;]*m", "", text).replace("\r", ""), why


def fields(line):
    """key=value and key="quoted value" pairs of an RT line."""
    out = {}
    for m in re.finditer(r'(\w+)=("([^"]*)"|\S+)', line):
        out[m.group(1)] = m.group(3) if m.group(3) is not None else m.group(2)
    return out


def panics_of(segment):
    """IDF's panic texts of a console segment: for each, the register dump's
    PC and EXCVADDR (None for an abort) and the backtraces as PC lists."""
    out = []
    # at a line's start only: the next boot quotes the abort text in its log
    # line and in RT NOTE
    marks = [m.start() for m in re.finditer(
        r"^(?:Guru Meditation Error|abort\(\) was called at PC|assert failed:"
        r"|\*\*\*ERROR\*\*\* A stack overflow)", segment, re.M)]
    for i, at in enumerate(marks):
        part = segment[at:marks[i + 1] if i + 1 < len(marks) else len(segment)]
        pc = re.search(r"PC\s+: 0x([0-9a-fA-F]{8})", part)
        va = re.search(r"EXCVADDR: 0x([0-9a-fA-F]{8})", part)
        bts = []
        for m in re.finditer(r"Backtrace:([^\n]*)", part):
            bts.append({"pcs": ["0x" + p.lower()
                                for p, _ in BT_ENTRY.findall(m.group(1))],
                        "sps": [sp.lower() for _, sp in BT_ENTRY.findall(m.group(1))],
                        "corrupt": "CORRUPTED" in m.group(1),
                        "more": "CONTINUES" in m.group(1)})
        out.append({"pc": pc and "0x" + pc.group(1).lower(),
                    "excvaddr": va and "0x" + va.group(1).lower(),
                    "bts": bts})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("log", nargs="?")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--from-log", help="judge a saved console text instead")
    a = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    if a.from_log:
        text, why = open(a.from_log, encoding="utf-8").read(), "done"
    else:
        text, why = capture(a.port, a.timeout)
    if a.log and not a.from_log:
        with open(a.log, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    print("=" * 72)

    check("run_reached_TEST_DONE", "TEST DONE" in text, why)

    # one console segment per boot of the run: from its RT STEP line on
    marks = [(int(m.group(1)), m.start())
             for m in re.finditer(r"^RT STEP (\d+) ", text, re.M)]
    seg = {}
    for i, (n, at) in enumerate(marks):
        seg[n] = text[at:marks[i + 1][1] if i + 1 < len(marks) else len(text)]
    check("every_step_ran", sorted(seg) == list(range(DONE + 1)),
          f"steps seen: {sorted(seg)}")

    results = {int(m.group(1)): (m.group(2), m.group(3)) for m in re.finditer(
        r"^RT RESULT step=(\d+) (\S+) (ok|FAIL)", text, re.M)}
    for n, name in enumerate(STEPS):
        got = results.get(n)
        bad = sorted(set(re.findall(
            rf"^RT CHECK step={n} (\S+) FAIL", text, re.M)))
        check(f"step{n}_{name}", got is not None and got[1] == "ok",
              "no RT RESULT line" if got is None
              else ("the app's checks: " + ", ".join(bad) if bad else ""))

    # the PC's part: each note against IDF's own text of the same crash
    for n, name in enumerate(STEPS):
        if n not in seg or n + 1 not in seg:
            continue
        panics = panics_of(seg[n])
        want = 0 if n in NO_PANIC else PANICS.get(n, 1)
        check(f"step{n}_{name}_panics", len(panics) == want,
              f"{len(panics)} panic texts on the console, {want} expected")
        note = re.search(rf"^RT NOTE step={n} (.*)$", seg[n + 1], re.M)
        nbt = re.search(rf"^RT NOTEBT step={n} (.*)$", seg[n + 1], re.M)
        nbt2 = re.search(rf"^RT NOTEBT2 step={n} core=\d+ (.*)$", seg[n + 1], re.M)
        f = fields(note.group(1)) if note else {}
        if n in NO_PANIC or not panics or f.get("found") != "1":
            continue
        first = panics[0]
        if first["pc"]:
            check(f"step{n}_{name}_pc_is_the_register_dumps",
                  f.get("pc") == first["pc"], f"note {f.get('pc')}, console {first['pc']}")
        if first["excvaddr"] and f.get("kind") == "exception":
            check(f"step{n}_{name}_excvaddr_is_the_register_dumps",
                  f.get("excvaddr") == first["excvaddr"],
                  f"note {f.get('excvaddr')}, console {first['excvaddr']}")
        if f.get("complete") != "1":
            continue
        if not first["bts"]:
            check(f"step{n}_{name}_console_backtrace", False, "none on the console")
            continue
        con = first["bts"][0]
        mine = nbt.group(1).split() if nbt else []
        check(f"step{n}_{name}_backtrace_is_the_consoles",
              mine == con["pcs"][:16] and len(mine) >= 1,
              f"{len(mine)} frames in the note, {len(con['pcs'])} on the console"
              + ("" if mine == con["pcs"][:16] else f": {mine} vs {con['pcs'][:16]}"))
        check(f"step{n}_{name}_backtrace_end_flags",
              (f.get("corrupt") == "1") == con["corrupt"]
              and (f.get("more") == "1") == (len(con["pcs"]) > 16 or (con["more"] and len(con["pcs"]) >= 16)),
              f"note corrupt={f.get('corrupt')} more={f.get('more')}, console "
              f"{len(con['pcs'])} frames corrupted={con['corrupt']} continues={con['more']}")
        if nbt2 and len(first["bts"]) > 1:
            other = nbt2.group(1).split()
            check(f"step{n}_{name}_other_core_is_the_consoles",
                  other == first["bts"][1]["pcs"][:6], f"{other} vs {first['bts'][1]['pcs'][:6]}")
        if n == 2:
            check("step2_stack_was_in_psram", con["sps"][0][:2] in ("3c", "3d"),
                  f"first stack pointer 0x{con['sps'][0]}")

    if 12 in seg:
        p = panics_of(seg[12])
        check("step12_first_panic_is_the_original",
              len(p) >= 1 and p[0]["excvaddr"] == "0x0000bad0"
              and len(p[0]["bts"]) >= 1 and len(p[0]["bts"][0]["pcs"]) >= 2,
              "the first panic text must be whole: EXCVADDR 0x0000bad0 and its backtrace")
        check("step12_second_panic_is_stage_bs",
              len(p) >= 2 and p[1]["excvaddr"] == "0x0000bad4")

    # the crash-loop brake and the stored report (2026-10-05): the run's 13
    # crashes are quick ones and all different, so the streak must count
    # every one, the verdict turn to park at the third, and the report be
    # stored four times and not once more (the wear guard's budget)
    brake = {int(m.group(1)): (m.group(2), int(m.group(3)), int(m.group(4)))
             for m in re.finditer(
                 r"^RT BRAKE step=(\d+) verdict=(\S+) streak=(\d+) "
                 r"parks=\d+ budget=(\d+)", text, re.M)}
    crash_steps = [n for n in range(1, 14) if n in brake]
    check("brake_counted_every_quick_crash",
          len(crash_steps) == 13
          and all(brake[n][1] == n for n in crash_steps),
          "streaks " + " ".join(str(brake[n][1]) for n in crash_steps))
    check("brake_verdict_park_from_the_third",
          bool(crash_steps) and all(
              brake[n][0] == ("park" if n >= 3 else "normal")
              for n in crash_steps),
          " ".join(brake[n][0] for n in crash_steps))
    budgets = [brake[n][2] for n in crash_steps]
    check("report_stored_four_times_then_no_more",
          budgets[:4] == [3, 2, 1, 0] and all(b == 0 for b in budgets[4:]),
          "budget after each crash: " + " ".join(str(b) for b in budgets))
    check("brake_at_rest_after_the_planned_restarts",
          brake.get(0, ("", -1, -1))[1:] == (0, 4)
          and brake.get(14, ("", -1, -1))[1:] == (0, 4),
          f"step 0 {brake.get(0)}, step 14 {brake.get(14)}")
    check("nvs_was_there", "RT NVS init=ESP_OK" in text)
    check("report_cleared_at_the_end", "RT CLEANUP report_cleared=1" in text)

    summary = re.search(r"^RT SUMMARY ok=(\d+) of=(\d+)", text, re.M)
    check("summary", summary is not None and summary.group(1) == summary.group(2)
          == str(DONE), summary.group(0) if summary else "no RT SUMMARY line")
    filed = len(re.findall(r"^RT NOTE step=\d+ found=1", text, re.M))
    said = len(re.findall(r"restart_tracker: previous run crashed:", text))
    check("one_log_line_per_filed_note", filed == said and filed >= 12,
          f"{filed} notes filed, {said} `previous run crashed` lines")

    print("-" * 72)
    for m in re.finditer(r"^RT MEASURE (.*)$", text, re.M):
        print("MEASURE " + m.group(1))
    if fails:
        print(f"RT TARGET FAIL ({len(fails)} of {checks} checks): " + ", ".join(fails))
        return 1
    print(f"RT TARGET PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
