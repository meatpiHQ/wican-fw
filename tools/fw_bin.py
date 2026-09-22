#!/usr/bin/env python3
"""Resolve the current firmware image of a build tree.

The app binary carries the git describe in its name
(build/wican-fw_obd_pro_<tag>.bin, see the root CMakeLists.txt), so old
images pile up in build/ and nothing can hard-code the file name. This
prints the image the LAST successful configure produced, read from
build/project_description.json (the same source idf.py flash uses).

  python tools/fw_bin.py                 -> build/wican-fw_obd_pro_....bin
  python tools/fw_bin.py build_public    -> another build dir
  python tools/fw_bin.py path/to/x.bin   -> an explicit image passes through
  python tools/fw_bin.py --elf / --map   -> the .elf / .map next to it
  python tools/fw_bin.py --scp pi:/tmp/wican-fw.bin   -> scp it there

Exit 1 with a message on stderr when the build dir has no description
(never configured) or the image it names is missing (build failed after
configure - flash nothing, that file may be stale or absent).
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve(where="build", kind="bin"):
    """Return the absolute path of the current image of build dir `where`.

    `where` may be a build directory (relative to the repo root or
    absolute) or an explicit .bin/.elf/.map file, which passes through.
    """
    path = where if os.path.isabs(where) else os.path.join(ROOT, where)
    if os.path.isfile(path):
        return os.path.abspath(path)
    desc = os.path.join(path, "project_description.json")
    if not os.path.isfile(desc):
        raise FileNotFoundError(f"{where}: no project_description.json "
                                "(not configured yet - run the build)")
    with open(desc, encoding="utf-8") as f:
        d = json.load(f)
    name = d["app_bin"] if kind == "bin" else d["app_elf"]
    if kind == "map":
        name = os.path.splitext(d["app_elf"])[0] + ".map"
    out = os.path.join(path, name)
    if not os.path.isfile(out):
        raise FileNotFoundError(f"{out}: named by project_description.json "
                                "but missing - the last build did not finish")
    return os.path.abspath(out)


def stale_siblings(image):
    """Other wican-fw_*.<ext> files in the same dir (older builds)."""
    d, cur = os.path.split(image)
    ext = os.path.splitext(cur)[1]
    return sorted(f for f in os.listdir(d)
                  if f.startswith("wican-fw") and f.endswith(ext) and f != cur)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("where", nargs="?", default="build",
                    help="build dir or explicit image (default: build)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--elf", action="store_true", help="print the .elf")
    g.add_argument("--map", action="store_true", help="print the .map")
    ap.add_argument("--scp", metavar="DEST",
                    help="copy the image to DEST with scp instead of printing")
    ap.add_argument("--stale", action="store_true",
                    help="also list older images left in the build dir")
    a = ap.parse_args()
    kind = "elf" if a.elf else "map" if a.map else "bin"
    try:
        image = resolve(a.where, kind)
    except FileNotFoundError as e:
        print(f"fw_bin: {e}", file=sys.stderr)
        return 1
    if a.stale:
        for f in stale_siblings(image):
            print(f"stale: {f}", file=sys.stderr)
    if a.scp:
        print(f"scp {image} -> {a.scp}", file=sys.stderr)
        return subprocess.call(["scp", image, a.scp])
    print(image)
    return 0


if __name__ == "__main__":
    sys.exit(main())
