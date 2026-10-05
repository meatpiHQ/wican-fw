"""Crash notes of the restart tracker, on the PC side.

A boot that follows a crash carries a `crash` object in its record of
`GET /api/restart/history` (meatpi-components restart_tracker/README.md,
"The crash note"). This module turns such notes into text for a bench
report and their program counters into function names.

The PCs mean something only against the ELF of the image that crashed. The
note names that image (`elf_sha`: the first 16 hex characters of the ELF
file's SHA-256), so `find_elf()` can prove an ELF is the right one instead
of trusting the newest file in build/ (five builds shared one version
string on 2026-10-05).

Used by tools/crash_decode.py, lib/pcbench.py (a reset nobody asked for
names its function) and system/crash_note_bench.py.
"""
import glob
import hashlib
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
_ids = {}       # (path, mtime, size) -> elf id


def notes(history):
    """The records of a history reply that carry a crash note, newest
    first: [(record, crash)]."""
    recs = history.get("records", []) if isinstance(history, dict) else []
    out = [(r, r["crash"]) for r in recs if isinstance(r.get("crash"), dict)]
    return sorted(out, key=lambda rc: -(rc[0].get("seq") or 0))


def one_line(rec):
    """A record for a report: the reset reason, and the note when it has one."""
    text = f"boot {rec.get('seq')}: reset reason '{rec.get('reason')}'"
    c = rec.get("crash")
    if isinstance(c, dict):
        text += ": " + str(c.get("summary", "crash note without a summary"))
        if c.get("backtrace"):
            text += "; backtrace " + " ".join(c["backtrace"])
    return text


def parse_report(text):
    """A crash report as the device prints it (GET /api/restart/report, safe
    mode's page, `restart_tracker --report`: restart_tracker_report_core.c)
    as a dict shaped like a note: {"device", "firmware", "elf_sha",
    "stored", "loop", "summary", "backtrace": [...], "other_core": [...],
    "backtrace_corrupt", "backtrace_more"}. None when `text` is no report.
    This is what a user sends on, so it is what a decoder must take."""
    if "WiCAN crash report" not in text:
        return None
    out = {"backtrace": [], "other_core": [], "backtrace_corrupt": False,
           "backtrace_more": False}
    names = {"Device": "device", "Firmware": "firmware", "Image": "elf_sha",
             "Stored": "stored", "Loop": "loop", "Crash": "summary"}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        value = value.strip()
        if not sep:
            continue
        if key in names:
            out[names[key]] = value
        elif key == "Backtrace":
            out["backtrace_corrupt"] = value.endswith("(corrupt)")
            out["backtrace_more"] = value.endswith("...")
            out["backtrace"] = [t for t in value.split() if t.startswith("0x")]
        elif key.startswith("Core "):
            out["other_core"] = [t for t in value.split()
                                 if t.startswith("0x")]
    if out.get("elf_sha") == "not recorded":
        out["elf_sha"] = ""
    out["complete"] = bool(out["backtrace"]) or "not recorded" not in \
        out.get("summary", "")
    return out


def elf_id(path):
    """The image id of an ELF file, as a crash note carries it."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime, st.st_size)
    if key not in _ids:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _ids[key] = h.hexdigest()[:16]
    return _ids[key]


def find_elf(elf_sha, hint=None):
    """(path, None) of the ELF whose id is `elf_sha`, or (None, why).
    Looked at, in this order: `hint`, the current build's ELF
    (build/project_description.json), every *.elf in the repo's build*
    directories."""
    seen = []
    cands = [hint] if hint else []
    try:
        import sys
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import fw_bin
        cands.append(fw_bin.resolve("build", "elf"))
    except Exception:  # noqa: BLE001  (no build yet: the glob below decides)
        pass
    cands += sorted(glob.glob(os.path.join(ROOT, "build*", "*.elf")),
                    key=os.path.getmtime, reverse=True)
    for p in cands:
        if not p or not os.path.isfile(p) or os.path.abspath(p) in seen:
            continue
        seen.append(os.path.abspath(p))
        if elf_id(p) == elf_sha:
            return os.path.abspath(p), None
    return None, (f"no ELF with image id {elf_sha} among "
                  f"{len(seen)} looked at (" + ", ".join(
                      f"{os.path.basename(p)} = {elf_id(p)}" for p in seen[:4])
                  + ("" if len(seen) <= 4 else ", ...") + ")")


def addr2line_exe():
    """The Xtensa addr2line of the IDF tools, or None."""
    env = os.environ.get("XTENSA_ADDR2LINE")
    if env and os.path.isfile(env):
        return env
    tools = os.environ.get("IDF_TOOLS_PATH", r"C:\Espressif")
    hits = sorted(glob.glob(os.path.join(
        tools, "tools", "xtensa-esp-elf", "*", "xtensa-esp-elf", "bin",
        "xtensa-esp32s3-elf-addr2line*")), reverse=True)
    return hits[0] if hits else shutil.which("xtensa-esp32s3-elf-addr2line")


def decode(elf, pcs):
    """addr2line over `pcs` (hex strings): one entry per address,
    {"pc", "functions": [innermost, inlined-by...], "where": [file:line...]}.
    Raises RuntimeError when the tool is missing or fails."""
    exe = addr2line_exe()
    if not exe:
        raise RuntimeError("no xtensa-esp32s3-elf-addr2line (IDF_TOOLS_PATH?)")
    if not pcs:
        return []
    r = subprocess.run([exe, "-pfiaC", "-e", elf] + list(pcs),
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("addr2line: " + r.stderr.strip()[:200])
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("0x") and ": " in line:
            pc, rest = line.split(": ", 1)
            out.append({"pc": pc, "functions": [], "where": []})
        elif line.startswith("(inlined by) ") and out:
            rest = line[len("(inlined by) "):]
        else:
            continue
        fn, _, where = rest.partition(" at ")
        if not where:               # "?? ??:0": an address in no function
            fn = fn.split(" ")[0]
        out[-1]["functions"].append(fn.strip())
        out[-1]["where"].append(where.strip())
    return out


def functions(decoded):
    """Every function of a decoded backtrace, innermost first."""
    return [fn for e in decoded for fn in e["functions"]]


def chain(decoded, most=8):
    """`a <- b <- c` of the first functions that have a name."""
    names = [fn for fn in functions(decoded) if fn and fn != "??"]
    return " <- ".join(names[:most]) + (" <- ..." if len(names) > most else "")


def decoded_line(crash, hint=None):
    """`= fn <- fn <- fn` for a report, or '' when the right ELF is not at
    hand (a report must not name functions out of another image)."""
    if not crash.get("backtrace") or not crash.get("elf_sha"):
        return ""
    try:
        elf, _ = find_elf(crash["elf_sha"], hint)
        return (" = " + chain(decode(elf, crash["backtrace"]))) if elf else ""
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return ""
