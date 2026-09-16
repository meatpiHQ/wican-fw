#!/usr/bin/env python3
"""run_be.py <base-url> <script.be> [--tx 7E2 --rx 7EA] [--timeout 60]
Run a Berry script on the WiCAN through POST /api/scripts/run and print its
output. Optional --tx/--rx rewrite the script's `var TX = 0x...` /
`var RX = 0x...` header lines (reflash.be, uds_bindings.be) so one script
serves the ECU simulator (7E0/7E8) and the PCAN reflash ECU (e.g. 7E2/7EA).
Exit 0 when the run reported ok AND the output has no 'FAIL' line."""
import json
import re
import sys
import urllib.request


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        return 2
    base, path = args[0].rstrip("/"), args[1]

    def opt(name, default=None):
        if name in sys.argv:
            i = sys.argv.index(name)
            if i + 1 < len(sys.argv):
                return sys.argv[i + 1]
        return default

    src = open(path, encoding="utf-8").read()
    for key, val in (("TX", opt("--tx")), ("RX", opt("--rx"))):
        if val:
            src, n = re.subn(r"^var %s = 0x[0-9A-Fa-f]+" % key,
                             "var %s = 0x%s" % (key, val.upper()), src, flags=re.M)
            print("%s -> 0x%s (%d line)" % (key, val.upper(), n))
    body = json.dumps({"src": src}).encode()
    req = urllib.request.Request(base + "/api/scripts/run", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=float(opt("--timeout", 60))) as r:
        d = json.load(r)
    out = d.get("output") or ""
    print(out.rstrip())
    if d.get("error"):
        print("error:", d["error"])
    ok = bool(d.get("ok")) and "FAIL" not in out
    print("SCRIPT RUN", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
