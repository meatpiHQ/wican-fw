#!/usr/bin/env python3
"""Static stack audit: compiler frame sizes vs task stack sizes.

Motivated by 2026-07-22: the autopid DTC job task's 6144-BYTE stack had
been silently overflowing (the response assembler's line table alone is
a ~5.6 KB frame) — on a PSRAM stack that corrupts neighbouring .bss
instead of panicking. This tool makes the hazard visible at build time:

  1. reads every .su file the build emits (-fstack-usage is permanently
     on in the root CMakeLists), giving the compiler-measured stack
     frame of every function;
  2. scans the sources for xTaskCreate*() calls and resolves each
     task's stack size (literal, macro, or sizeof(array)) plus whether
     the stack array is PSRAM (EXT_RAM_BSS_ATTR) or internal;
  3. reports big frames, the task table, and CANDIDATE OVERFLOWS: any
     function whose frame exceeds --flag-pct (default 40%) of a task
     stack in the same component. A frame that big means at most ONE
     level of calls fits on top — human triage required, because .su
     has no call graph.

Usage (after a normal idf.py build):
  python tools/stack_audit.py [--build build] [--min-frame 1024]
                              [--flag-pct 40] [--strict]

--strict exits 1 when any candidate overflow is found (report stays the
same). This tool finds CANDIDATES statically; pair it with a runtime
watermark check on real hardware (uxTaskGetStackHighWaterMark / the
`system -t` CLI) to prove actual headroom.
"""
import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# source roots scanned for task creations (order = report order)
SRC_ROOTS = [
    os.path.join(REPO, "main"),
    os.path.normpath(os.path.join(
        REPO, "..", "..", "wican-fw-dev", "meatpi-components",
        "components")),
    os.path.normpath(os.path.join(
        REPO, "..", "..", "wican-fw-dev", "meatpi-components-internal",
        "components")),
]

TASK_RE = re.compile(
    r"xTaskCreate(?:Static)?(?:PinnedToCore)?\s*\(", re.S)
SKIP_DIRS = {"host_test", "test_apps", "build", "managed_components",
             "tests", "web"}


def parse_su_files(build_dir):
    """{function: (frame_bytes, su_path)} — biggest frame wins on dups."""
    frames = {}
    for root, dirs, files in os.walk(build_dir):
        for f in files:
            if not f.endswith(".su"):
                continue
            path = os.path.join(root, f)
            try:
                text = open(path, encoding="utf-8",
                            errors="replace").read()
            except OSError:
                continue
            for line in text.splitlines():
                # file:line:col:func<TAB>bytes<TAB>static|dynamic[,...]
                parts = line.rsplit("\t", 2)
                if len(parts) != 3:
                    continue
                sig, size, _qual = parts
                func = sig.split(":")[-1]
                try:
                    n = int(size)
                except ValueError:
                    continue
                if n > frames.get(func, (0, ""))[0]:
                    frames[func] = (n, path)
    return frames


def iter_sources():
    for root_dir in SRC_ROOTS:
        if not os.path.isdir(root_dir):
            continue
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if f.endswith((".c", ".h")):
                    yield os.path.join(root, f)


def component_of(path):
    """The component dir name a source or build (.su) file belongs to."""
    parts = os.path.normpath(path).split(os.sep)
    # build tree: build/esp-idf/<component>/CMakeFiles/...
    if "esp-idf" in parts:
        i = parts.index("esp-idf")
        if i + 1 < len(parts):
            return parts[i + 1]
    if "components" in parts:
        i = len(parts) - 1 - parts[::-1].index("components")
        if i + 1 < len(parts):
            return parts[i + 1]
    if "main" in parts:
        return "main"
    return "?"


def safe_arith(expr):
    """Evaluate a plain integer arithmetic expression, else None."""
    expr = expr.strip().rstrip("uUlL")
    if not re.fullmatch(r"[\d\s+*/()-]+", expr):
        return None
    try:
        return int(eval(expr, {"__builtins__": {}}, {}))  # digits+ops only
    except Exception:
        return None


def resolve_size(expr, text, all_texts):
    """Best-effort byte count for a stack-size expression."""
    expr = re.sub(r"\s+", " ", expr.strip())
    n = safe_arith(expr)
    if n is not None:
        return n, ""
    # sizeof(arr) or sizeof(arr) / sizeof(arr[0]) / sizeof(StackType_t)
    # — StackType_t is 1 byte on xtensa, so both reduce to the array size
    m = re.fullmatch(
        r"sizeof\s*\(\s*(\w+)\s*\)"
        r"(?:\s*/\s*sizeof\s*\([^)]*\))?", expr)
    ident = (m.group(1) if m
             else expr if re.fullmatch(r"\w+", expr) else None)
    if ident is None:
        return None, expr
    # array decl in the same file, then anywhere; then macro bodies
    for t in [text] + all_texts:
        m = re.search(r"StackType_t\s+" + re.escape(ident) +
                      r"\s*\[\s*([^\]]+?)\s*\]", t)
        if m:
            n = safe_arith(m.group(1))
            if n is not None:
                return n, ident
        m = re.search(r"#define\s+" + re.escape(ident) +
                      r"[ \t]+(.+)", t)
        if m:
            n = safe_arith(m.group(1).split("//")[0].split("/*")[0])
            if n is not None:
                return n, ident
    return None, ident


def find_tasks():
    """[{name, size, psram, file, line, component}]"""
    tasks = []
    texts = {}
    for path in iter_sources():
        try:
            texts[path] = open(path, encoding="utf-8",
                               errors="replace").read()
        except OSError:
            pass
    all_texts = list(texts.values())
    for path, text in texts.items():
        for m in TASK_RE.finditer(text):
            # capture the call's argument list (balanced parens)
            depth, i = 1, m.end()
            while i < len(text) and depth:
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                i += 1
            args = text[m.end():i - 1]
            parts, depth, cur = [], 0, ""
            for ch in args:
                if ch == "," and depth == 0:
                    parts.append(cur)
                    cur = ""
                    continue
                depth += {"(": 1, "[": 1, ")": -1, "]": -1}.get(ch, 0)
                cur += ch
            parts.append(cur)
            if len(parts) < 3:
                continue
            name = parts[1].strip().strip('"')
            size, ident = resolve_size(parts[2], text, all_texts)
            psram = None
            if ident:
                mm = re.search(r"StackType_t\s+" + re.escape(ident) +
                               r"\s*\[[^\]]*\][^;]*;", text)
                if mm:
                    psram = "EXT_RAM" in mm.group(0)
            line = text[:m.start()].count("\n") + 1
            tasks.append({
                "name": name, "size": size, "psram": psram,
                "file": os.path.relpath(path, REPO), "line": line,
                "component": component_of(path),
            })
    return tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.path.join(REPO, "build"))
    ap.add_argument("--min-frame", type=int, default=1024)
    ap.add_argument("--flag-pct", type=int, default=40)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    frames = parse_su_files(a.build)
    if not frames:
        print("no .su files under", a.build,
              "— build first (root CMakeLists sets -fstack-usage)")
        sys.exit(2)
    tasks = find_tasks()

    big = sorted(((n, fn, su) for fn, (n, su) in frames.items()
                  if n >= a.min_frame), reverse=True)
    print(f"== frames >= {a.min_frame} B ({len(big)} of "
          f"{len(frames)} functions) ==")
    for n, fn, su in big:
        comp = component_of(su)
        print(f"  {n:6d} B  {fn}  [{comp}]")

    print(f"\n== tasks ({len(tasks)}) ==")
    for t in sorted(tasks, key=lambda t: (t["size"] is None,
                                          t["size"] or 0)):
        size = "?" if t["size"] is None else t["size"]
        loc = "PSRAM" if t["psram"] else \
              ("int" if t["psram"] is False else "?")
        print(f"  {str(size):>6}  {loc:5}  {t['name']:14} "
              f"{t['file']}:{t['line']}")

    # candidate overflows: big frame + task stack in the same component
    print(f"\n== candidate overflows (frame > {a.flag_pct}% of a "
          "same-component task stack) ==")
    found = 0
    for n, fn, su in big:
        comp = component_of(su)
        for t in tasks:
            if t["component"] != comp or not t["size"]:
                continue
            if n * 100 > t["size"] * a.flag_pct:
                found += 1
                print(f"  {fn} frame {n} B = "
                      f"{n * 100 // t['size']}% of task "
                      f"'{t['name']}' ({t['size']} B) — "
                      f"{t['file']}:{t['line']}")
    if not found:
        print("  none")
    else:
        print(f"\n{found} candidate(s) — triage by hand: .su has no "
              "call graph; a flagged frame only matters if that "
              "function runs on that task.")
    sys.exit(1 if (a.strict and found) else 0)


if __name__ == "__main__":
    main()
