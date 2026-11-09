"""Ask "which COM port is the PSU / the WiCAN console / the ELM bridge?" the
same way in every bench script, instead of carrying a COM number that moves
on the next re-plug.

    import bench_ports
    con = Console(bench_ports.resolve(args.console, "wican_console", "COM116"))
    ap.add_argument("--console", default="auto")       # auto = detect

`resolve(value, role, default)` returns `value` unless it is "auto" / "" /
None; then it detects live (tools/testbench/detect_ports.py, enumeration
only - nothing is opened), falls back to the last explicit detection record
(`bench_ports.json`, e.g. a `--probe-psu` run that disambiguated two FTDI
ports) when that port is still present, and finally to `default`. `role`
may be a sequence of roles tried in order, e.g. ("wican_console",
"ch342_console") for "whatever console is wired today".

The DUT's supply is not a COM port since 2026-11-09 (ECU sim v2's OBD pin
16 over its USB network link): benches open it with
`bench_psu.open_psu(args.psu)`, and the `psu` role here names only a
legacy OWON on an FTDI port when a rig still has one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import detect_ports  # noqa: E402

AUTO = ("", "auto", None)


def detect() -> dict:
    """Live, name-only detection (never opens a port)."""
    return detect_ports.detect(probe=False)


def port(role, default: str | None = None) -> str | None:
    roles = (role,) if isinstance(role, str) else tuple(role)
    res = detect()
    for r in roles:
        hit = res["roles"].get(r)
        if hit:
            return hit["port"]
    # an earlier explicit run may have pinned an ambiguous role (two FTDI ports)
    rec = detect_ports.load_record()
    if rec:
        present = {p["port"] for p in detect_ports.enumerate_ports()}
        for r in roles:
            hit = rec.get("roles", {}).get(r)
            if hit and hit["port"] in present:
                return hit["port"]
    for r in roles:                       # a lone FTDI candidate is the PSU
        cands = res["candidates"].get(r) or []
        if len(cands) == 1:
            return cands[0]["port"]
    return default


def resolve(value, role, default: str | None = None) -> str:
    if value not in AUTO:
        return value
    p = port(role, default)
    if p is None:
        roles = role if isinstance(role, str) else "/".join(role)
        raise SystemExit(f"bench_ports: no port detected for {roles}; run "
                         f"`python tools/testbench/detect_ports.py` and pass the port explicitly")
    return p
