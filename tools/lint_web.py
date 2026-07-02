#!/usr/bin/env python3
"""Static regression guard for the WiCAN web UI (no browser, no node needed).

Catches the class of bug introduced by commit d372fc9 during the #5 trim, where
removing a feature's code left dangling references behind (an undeclared var made
the Files tab throw; an amputated population block made Submit clobber settings).
Three checks:

  1. Every getElementById("X") string-literal in src/main.js resolves to an
     id="X" defined in homepage_full.html (or an id assigned to an element in
     main.js). Dynamic calls (getElementById('x' + k)) are skipped.
  2. Every function invoked from an inline on*= handler in homepage_full.html is
     defined in src/main.js.
  3. src/homepage.html is not stale (delegates to build_web.py --check; skipped
     with a warning when node/npx is unavailable).

Usage:
    python tools/lint_web.py               # all checks; exit 1 on any failure
    python tools/lint_web.py --no-build-check   # skip check 3 (no node needed)

No third-party dependencies. Run it in the regression runbook and before every
web commit.
"""

import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
FULL = REPO / "main" / "web" / "homepage_full.html"
MAIN_JS = REPO / "main" / "web" / "src" / "main.js"
BUILD_WEB = REPO / "tools" / "build_web.py"

# Globals/keywords that can legitimately appear as `name(` in an inline handler
# and are NOT user functions. Method calls (`x.click()`) are already excluded by
# the extraction regex (negative look-behind on '.'), so this only lists bare
# globals and control-flow keywords.
BUILTIN_CALLS = {
    "parseInt", "parseFloat", "Number", "String", "Boolean", "Array", "Object",
    "isNaN", "isFinite", "encodeURIComponent", "decodeURIComponent",
    "setTimeout", "clearTimeout", "setInterval", "clearInterval",
    "alert", "confirm", "prompt", "fetch",
    "if", "for", "while", "switch", "return", "catch", "typeof", "function",
    "await", "new", "delete", "void", "in", "of",
}


def html_ids(html: str) -> set:
    return set(re.findall(r"""\bid\s*=\s*["']([^"']+)["']""", html))


def js_assigned_ids(js: str) -> set:
    """ids that main.js attaches to elements it builds (createElement + .id)."""
    ids = set(re.findall(r"""\.id\s*=\s*["']([^"']+)["']""", js))
    ids |= set(re.findall(
        r"""setAttribute\(\s*["']id["']\s*,\s*["']([^"']+)["']""", js))
    return ids


def js_defined_functions(js: str) -> set:
    fns = set(re.findall(r"""\bfunction\s+([A-Za-z_$][\w$]*)\s*\(""", js))
    fns |= set(re.findall(
        r"""\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"""
        r"""(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)""",
        js))
    return fns


def get_by_id_literals(js: str) -> set:
    # Only pure single-string-literal calls; dynamic ('x' + k) calls won't match
    # (the closing paren must follow the quote directly), which is intended.
    return set(re.findall(r"""getElementById\(\s*["']([^"']+)["']\s*\)""", js))


def inline_handler_calls(html: str) -> set:
    names = set()
    for m in re.finditer(r"""\son[a-z]+\s*=\s*"([^"]*)"|\son[a-z]+\s*=\s*'([^']*)'""", html):
        body = m.group(1) if m.group(1) is not None else m.group(2)
        # identifier immediately before '(' that is NOT a member access (no '.')
        for name in re.findall(r"""(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(""", body):
            names.add(name)
    return names


def check_get_by_id(html: str, js: str) -> list:
    valid = html_ids(html) | js_assigned_ids(js)
    missing = sorted(i for i in get_by_id_literals(js) if i not in valid)
    return missing


def check_handlers(html: str, js: str) -> list:
    defined = js_defined_functions(js) | BUILTIN_CALLS
    missing = sorted(n for n in inline_handler_calls(html) if n not in defined)
    return missing


def check_build(no_build_check: bool) -> tuple:
    """Returns (ok: bool, message: str). Soft-skip when node/npx is missing."""
    if no_build_check:
        return True, "skipped (--no-build-check)"
    result = subprocess.run(
        [sys.executable, str(BUILD_WEB), "--check"],
        capture_output=True, text=True,
    )
    out = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0:
        return True, out.strip() or "up to date"
    if "npx not found" in out or "node.js is required" in out:
        return True, "skipped (node/npx unavailable)"
    return False, out.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-build-check", action="store_true",
        help="skip check 3 (build_web.py --check); no node/npx needed",
    )
    args = parser.parse_args()

    for path in (FULL, MAIN_JS):
        if not path.is_file():
            sys.exit(f"error: missing {path}")

    html = FULL.read_text(encoding="utf-8", errors="replace")
    js = MAIN_JS.read_text(encoding="utf-8", errors="replace")

    failed = False

    missing_ids = check_get_by_id(html, js)
    if missing_ids:
        failed = True
        print("FAIL check 1: getElementById targets with no matching id=:")
        for i in missing_ids:
            print(f"    - {i}")
    else:
        print("ok  check 1: all getElementById literals resolve to an id")

    missing_fns = check_handlers(html, js)
    if missing_fns:
        failed = True
        print("FAIL check 2: inline on*= handlers calling undefined functions:")
        for n in missing_fns:
            print(f"    - {n}")
    else:
        print("ok  check 2: all inline handler calls resolve to a function")

    build_ok, build_msg = check_build(args.no_build_check)
    if build_ok:
        print(f"ok  check 3: build_web --check ({build_msg})")
    else:
        failed = True
        print("FAIL check 3: src/homepage.html is stale or build failed:")
        print(f"    {build_msg}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
