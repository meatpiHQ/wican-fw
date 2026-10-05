#!/usr/bin/env python3
"""Where did the device crash? Decode the restart tracker's crash notes.

A WiCAN that crashed keeps a note of it for the next boot (kind, PC, task,
16 backtrace addresses, the image's id) and shows it in
`GET /api/restart/history`; the newest distinct crash is also kept in flash
as the stored crash report, which a user copies from the Status page or
from safe mode and sends on. This turns the addresses of either into
function names with the ELF of the image that crashed:

  python tools/crash_decode.py --report report.txt      what a user sent
  python tools/crash_decode.py --report -               ... pasted on stdin
  python tools/crash_decode.py --url http://192.168.80.1
  python tools/crash_decode.py --file history.json
  python tools/crash_decode.py --url ... --elf build/wican-fw_....elf
  python tools/crash_decode.py --url ... --seq 12       one boot only

The ELF is taken from --elf, the current build (build/), or any *.elf in
build*/; it is used only when the first 16 hex characters of its SHA-256
equal the note's image id (`elf_sha`, the report's `Image:` line).
Addresses decoded against another image name the wrong functions, so a
mismatch is an error (--force decodes anyway and says so).

Exit 0: everything decoded (or there is nothing to decode). 2: an ELF is
not at hand. 1: the input could not be read.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "testbench", "lib"))
import crash_note  # noqa: E402


def load(a):
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            return json.load(f)
    url = a.url.rstrip("/")
    if "://" not in url:
        url = "http://" + url
    with urllib.request.urlopen(url + "/api/restart/history", timeout=15) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def decode_note(c, a):
    """Print the frames of one note as function names; True when they were
    decoded with the note's own ELF (or there was nothing to decode)."""
    if not c.get("backtrace"):
        return True     # nothing to decode
    elf, why = crash_note.find_elf(c.get("elf_sha", ""), a.elf)
    forced = False
    if elf is None and a.force and a.elf:
        elf, forced = a.elf, True
    if elf is None:
        print(f"  NOT DECODED: {why}")
        print("  backtrace: " + " ".join(c["backtrace"]))
        return False
    if forced:
        print(f"  FORCED: {os.path.relpath(elf)} is image "
              f"{crash_note.elf_id(elf)}, the note is of {c.get('elf_sha')}: "
              "these names may be wrong")
    else:
        running = c.get("same_image")
        print(f"  image {c.get('elf_sha')} = {os.path.relpath(elf)}"
              + ("" if running is None else
                 " (running now)" if running else " (NOT the running image)"))
    for title, pcs in (("backtrace", c["backtrace"]),
                       ("the other core meanwhile", c.get("other_core") or [])):
        if not pcs:
            continue
        print(f"  {title}:")
        for e in crash_note.decode(elf, pcs):
            for i, (fn, where) in enumerate(zip(e["functions"], e["where"])):
                print(f"    {e['pc'] if i == 0 else ' ' * len(e['pc'])} "
                      f"{'' if i == 0 else '(inlined by) '}{fn} at {where}")
    if c.get("backtrace_corrupt"):
        print("  (the stack walk ended on a frame that is no stack)")
    elif c.get("backtrace_more"):
        print("  (the stack went on after the 16th frame)")
    return not forced


def show(rec, c, a):
    """Print one note of the history; True when it was decoded."""
    print(f"boot {rec.get('seq')}: {c.get('summary')}")
    print(f"  reset reason '{rec.get('reason')}', "
          + ("planned" if rec.get("planned") else "not planned")
          + ("" if c.get("complete") else
             "; the handler did not finish the note: no backtrace"))
    return decode_note(c, a)


def show_stored(rep, a):
    """The report the device keeps in flash, from a history reply."""
    c = rep.get("crash") or {}
    print("stored crash report (kept in flash): " + str(c.get("summary")))
    print(f"  firmware '{rep.get('firmware') or 'not the one that stored it'}'"
          + (f", {rep.get('streak')} crashes in a row, the device parked "
             "itself" if rep.get("parked") else ""))
    return decode_note(c, a)


def report_main(a):
    """--report: the text a user sent."""
    try:
        if a.report == "-":
            text = sys.stdin.read()
        else:
            with open(a.report, encoding="utf-8", errors="replace") as f:
                text = f.read()
    except OSError as e:
        print(f"cannot read the report: {e}", file=sys.stderr)
        return 1
    c = crash_note.parse_report(text)
    if c is None:
        print("this is no crash report (no `WiCAN crash report` line)",
              file=sys.stderr)
        return 1
    print(f"device {c.get('device', '?')}, firmware "
          f"'{c.get('firmware', '?')}', stored {c.get('stored', '?')}")
    if c.get("loop"):
        print(f"  {c['loop']}")
    print(f"  {c.get('summary', '(no crash line)')}")
    try:
        return 0 if decode_note(c, a) else 2
    except RuntimeError as e:
        print(f"cannot decode: {e}", file=sys.stderr)
        return 2


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--report", help="a crash report as the device prints "
                     "it (a file, or - for stdin)")
    src.add_argument("--url", help="the device, e.g. http://192.168.80.1")
    src.add_argument("--file", help="a saved /api/restart/history reply")
    ap.add_argument("--elf", help="the ELF to try first")
    ap.add_argument("--force", action="store_true",
                    help="decode with --elf even when it is another image")
    ap.add_argument("--seq", type=int, help="only the note of this boot")
    a = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")
    if a.report:
        return report_main(a)
    try:
        hist = load(a)
    except (OSError, ValueError, urllib.error.URLError) as e:
        print(f"cannot read the history: {e}", file=sys.stderr)
        return 1
    found = [(r, c) for r, c in crash_note.notes(hist)
             if a.seq is None or r.get("seq") == a.seq]
    stored = hist.get("report") if a.seq is None else None
    print(f"{hist.get('boot_count')} boots, {hist.get('unexpected_resets')} "
          f"unexpected resets, running image {hist.get('elf_sha')}; "
          f"{len(found)} crash note(s)"
          + (", a stored crash report" if isinstance(stored, dict) else ""))
    try:
        ok = [show(r, c, a) for r, c in found]
        if isinstance(stored, dict):
            ok.append(show_stored(stored, a))
    except RuntimeError as e:
        print(f"cannot decode: {e}", file=sys.stderr)
        return 2
    return 0 if all(ok) else 2


if __name__ == "__main__":
    sys.exit(main())
