#!/usr/bin/env python3
"""Regenerate the embedded web UI from its readable source.

    main/web/homepage_full.html   readable, authoritative -- EDIT THIS
            |
            v  html-minifier-terser (pinned version, via npx)
    main/web/src/homepage.html    minified, embedded in firmware -- GENERATED

Usage:
    python tools/build_web.py           # regenerate src/homepage.html
    python tools/build_web.py --check   # exit 1 if src/ is stale (CI gate)

Requires node/npx on PATH. The minifier version and flags are pinned so the
output is reproducible; attribute quotes are kept so the minified file stays
re-parseable.
"""

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
FULL = REPO / "main" / "web" / "homepage_full.html"
SRC = REPO / "main" / "web" / "src" / "homepage.html"

MINIFIER = "html-minifier-terser@7.2.0"
FLAGS = [
    "--collapse-whitespace",
    "--remove-comments",
    "--minify-css", "true",
    "--minify-js", "true",
]


def minify(dest: pathlib.Path) -> None:
    npx = shutil.which("npx")
    if npx is None:
        sys.exit("error: npx not found on PATH (node.js is required)")
    cmd = [npx, "--yes", MINIFIER, *FLAGS, "-o", str(dest), str(FULL)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        sys.exit(f"error: minifier failed (exit {result.returncode})")
    if not dest.is_file() or dest.stat().st_size == 0:
        sys.exit("error: minifier produced no output")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify src/homepage.html is up to date; do not modify anything",
    )
    args = parser.parse_args()

    if not FULL.is_file():
        sys.exit(f"error: missing source {FULL}")

    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "homepage.min.html"
        minify(out)
        new = out.read_bytes()

    old = SRC.read_bytes() if SRC.is_file() else b""
    if new == old:
        print(f"up to date: {SRC.relative_to(REPO)} ({len(new)} bytes)")
        return
    if args.check:
        sys.exit(
            f"STALE: {SRC.relative_to(REPO)} does not match "
            f"{FULL.relative_to(REPO)} -- run `python tools/build_web.py`"
        )
    SRC.write_bytes(new)
    print(f"regenerated: {SRC.relative_to(REPO)} ({len(old)} -> {len(new)} bytes)")


if __name__ == "__main__":
    main()
