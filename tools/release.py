#!/usr/bin/env python3
"""Release this firmware: one command from a tagged build to the published
release list (internal/RELEASE_PROCESS.md).

    python tools/release.py check
    python tools/release.py publish --channel alfa [--tag] [--no-build] [--dry-run] [--no-push] [--resume]
    python tools/release.py publish --channel stable
    python tools/release.py promote 6.1.0-beta.2 --to stable
    python tools/release.py yank 6.1.0-alfa.1 --reason "..."

The publisher itself lives in the meatpi-devices checkout
(tools/fwpub/publish.py), found through MEATPI_DEVICES_PATH, --devices, or
a `meatpi-devices` folder beside one of this checkout's parents. The
product is described by tools/release.json. A fine-grained GitHub token
(Contents read and write on meatpi-devices and this repository) comes from
MEATPI_RELEASE_TOKEN or --token-file; `check` and `--dry-run` need none.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(HERE, "release.json")


def find_devices_root():
    env = os.environ.get("MEATPI_DEVICES_PATH")
    if env:
        return env
    for i, arg in enumerate(sys.argv):
        if arg == "--devices" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith("--devices="):
            return arg.split("=", 1)[1]
    here = REPO_ROOT
    for _ in range(7):
        cand = os.path.join(here, "meatpi-devices")
        if os.path.isfile(os.path.join(cand, "tools", "fwpub", "publish.py")):
            return cand
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    sys.exit("error: no meatpi-devices checkout found: set MEATPI_DEVICES_PATH or pass --devices PATH")


def main():
    devices_root = os.path.abspath(find_devices_root())
    sys.path.insert(0, os.path.join(devices_root, "tools"))
    from fwpub import publish  # noqa: E402
    return publish.main(sys.argv[1:], REPO_ROOT, CONFIG, devices_root)


if __name__ == "__main__":
    sys.exit(main())
