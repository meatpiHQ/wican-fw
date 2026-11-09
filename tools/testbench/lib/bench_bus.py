#!/usr/bin/env python3
"""The bench CAN bus as one thing: bitrate and id format of every node.

The DUT's bus has four nodes: the DUT's OBD chip, the DUT's native CAN
controller, the ECU simulator's HS CAN and the PCAN adapter's channel 1
(DB9 J2 of ECU sim v2). A node left at another bitrate in normal mode
destroys the traffic with error frames, and a listen-only DUT never ACKs, so
a 250k leg needs the simulator AT 250k (it is the ACK source) or out of the
bus. This helper moves the simulator's HS CAN and tells what the DUT's native
controller is set to; PCAN scripts take their bitrate as an argument and the
chip follows its protocol (ATTP).

  python bench_bus.py status
  python bench_bus.py restart                  (the simulator's own reboot forensics)
  python bench_bus.py sim --bitrate 250 [--id-format 29bit] [--enabled 1]
  python bench_bus.py sim --restore            (back to 500k / 11bit / enabled)

As a module: sim_get/sim_set/sim_status, sim_restart_state/sim_restart_delta,
dut_can(). The simulator's REST is
reachable from the PC only (its USB network link, found by sim_net.py); its
settings PUT replaces the whole object, hence GET, modify, PUT. A submit
reboots it (about 7 s on its firmware of 2026-11-09, up to 20 s before) and
puts its supply back to the boot defaults. Until that firmware the boot
parked the supply off for a moment and the DUT rode through only 9 of 12
such reboots (a `poweron` reason in its /api/restart/history marked the
others); since then the boot adopts a running port and the DUT rode through
20 of 20. Read the DUT's boot count after a sim_set when its state matters
all the same, and ask the simulator itself whether a boot was planned:
sim_restart_state() reads its own restart history (that firmware on).

ECU sim v2 (2026-11-09) configures each CAN interface on its own: the HS CAN
keys are `hs_enabled`, `hs_bitrate_kbps`, `hs_obd11`, `hs_obd29`, `hs_j1939`,
`hs_uds`, `hs_broadcast`, `hs_dialect` (the first-generation box had one
`bitrate` / `id_format` / `enabled` set). sim_get() returns the raw settings
PLUS that legacy view (`bitrate` as a string of kbit/s, `id_format`
"11bit" / "29bit" / "both", `enabled` = the HS CAN answers), and sim_set()
takes the legacy names, so the benches written against the first box keep
working. The MS CAN mirror on DB9 J3 (125 kbit/s) and the other interfaces
are left alone: they are not on the DUT's bus.

`enabled=False` on ECU sim v2 means "acknowledge, answer nothing": the HS
node stays up (`hs_enabled` true) and its OBD 11-bit, 29-bit and UDS answers
go off. The first box's disabled controller kept acknowledging at its last
bitrate by accident, and the J1939 / WWH / EU-truck legs built on that: the
PCAN's actor needs an ACK source while a listen-only DUT reads. On v2 a
really disabled node leaves the bus (verified 2026-11-09), so the trick is
spelled out here instead.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_net  # noqa: E402

sim_net.install()
SIM_URL = sim_net.sim_url()
SIM_DEFAULT = {"bitrate": "500", "id_format": "11bit", "enabled": True}
SIM_REBOOT_WINDOW = 60
#: the PCAN channel on the DUT's bus (ECU sim v2's DB9 J2 = HS CAN) and the
#: one on DB9 J3 (MS CAN or CH CAN through the simulator's relay)
PCAN_DUT_BUS = os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1")
PCAN_J3_BUS = os.environ.get("WICAN_PCAN_J3", "PCAN_USBBUS2")


def _http(url, method="GET", body=None, timeout=6):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


# ---- the two settings shapes ---------------------------------------------------

def _is_v2(cfg):
    return isinstance(cfg, dict) and "hs_bitrate_kbps" in cfg


def _fmt(o11, o29):
    return "both" if (o11 and o29) else "29bit" if o29 else "11bit" if o11 else "none"


def _legacy_view(cfg):
    """`bitrate` (str kbit/s), `id_format`, `enabled` derived from a v2
    settings object; a first-generation object already has them. On v2 the
    id format remembers the last one asked for while the answers are off
    (`_quiet_fmt`), so a restore puts the same one back."""
    if not _is_v2(cfg):
        return cfg
    out = dict(cfg)
    out["bitrate"] = str(int(cfg.get("hs_bitrate_kbps", 500)))
    o11, o29 = bool(cfg.get("hs_obd11", True)), bool(cfg.get("hs_obd29", False))
    answers = bool(cfg.get("hs_enabled", True)) and (o11 or o29 or bool(cfg.get("hs_uds", True)))
    out["id_format"] = _fmt(o11, o29) if (o11 or o29) else _quiet_fmt.get("fmt", "11bit")
    out["enabled"] = bool(cfg.get("enabled", True)) and answers
    return out


_quiet_fmt = {}                 # the id format of the answers switched off by sim_set


def _apply_legacy(cfg, bitrate=None, id_format=None, enabled=None):
    """The legacy request written into the object the simulator stores."""
    new = dict(cfg)
    if _is_v2(cfg):
        for k in ("bitrate", "id_format"):
            new.pop(k, None)
        if bitrate is not None:
            new["hs_bitrate_kbps"] = int(bitrate)
        if id_format is not None:
            new["hs_obd11"] = id_format in ("11bit", "both")
            new["hs_obd29"] = id_format in ("29bit", "both")
            _quiet_fmt["fmt"] = id_format
        if enabled is not None:
            new["enabled"] = True
            new["hs_enabled"] = True       # the node stays on the bus: the ACK source
            if enabled:
                new["hs_uds"] = True
                if not (new.get("hs_obd11") or new.get("hs_obd29")):
                    fmt = _quiet_fmt.get("fmt", "11bit")
                    new["hs_obd11"] = fmt in ("11bit", "both")
                    new["hs_obd29"] = fmt in ("29bit", "both")
            else:
                if new.get("hs_obd11") or new.get("hs_obd29"):
                    _quiet_fmt["fmt"] = _fmt(new.get("hs_obd11"), new.get("hs_obd29"))
                new["hs_obd11"] = False    # acknowledge, answer nothing
                new["hs_obd29"] = False
                new["hs_uds"] = False
        return new
    if bitrate is not None:
        new["bitrate"] = str(bitrate)
    if id_format is not None:
        new["id_format"] = id_format
    if enabled is not None:
        new["enabled"] = bool(enabled)
    return new


def _store_keys(cfg):
    """The keys to compare after a PUT (the ones a legacy request touches)."""
    return ("hs_bitrate_kbps", "hs_obd11", "hs_obd29", "hs_uds", "hs_enabled", "enabled") \
        if _is_v2(cfg) else ("bitrate", "id_format", "enabled")


# ---- the simulator -----------------------------------------------------------

def sim_get(name="ecu_sim", base=SIM_URL):
    """The settings object `name`; for `ecu_sim` with the legacy view added."""
    cfg = _http(f"{base}/api/settings/{name}")
    return _legacy_view(cfg) if name == "ecu_sim" else cfg


def sim_status(base=SIM_URL):
    """The simulator's running bus: {'running', 'bitrate', 'id_format', ...};
    on ECU sim v2 also one object per interface (`hs_can`, `ms_can`, ...).
    None when it does not answer."""
    try:
        return _http(f"{base}/api/ecu/status")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _hs(st):
    """The HS CAN's live state out of a status (the whole status on the
    first-generation box): running, bitrate, id_format, and `answers`
    (False on a v2 node that only acknowledges)."""
    hs = st.get("hs_can") if isinstance(st, dict) else None
    if isinstance(hs, dict):
        o11, o29 = bool(hs.get("obd11")), bool(hs.get("obd29"))
        return {"running": bool(hs.get("running")), "bitrate": hs.get("bitrate"),
                "id_format": _fmt(o11, o29) if (o11 or o29) else _quiet_fmt.get("fmt", "11bit"),
                "answers": o11 or o29 or bool(hs.get("uds")), "v2": True}
    return {"running": bool(st.get("running")), "bitrate": st.get("bitrate"),
            "id_format": st.get("id_format"), "answers": bool(st.get("running")), "v2": False}


def _sim_wait(want_bitrate, want_fmt, want_enabled, base):
    """Wait for the reboot and for the running bus to match."""
    time.sleep(3)                       # the restart fires ~1 s after submit
    end = time.time() + SIM_REBOOT_WINDOW
    while time.time() < end:
        st = sim_status(base)
        if st is not None:
            hs = _hs(st)
            if not want_enabled:
                # v2: the node is up at the bitrate and answers nothing;
                # the first box: the controller is reported down
                if hs["v2"] and hs["running"] and not hs["answers"] and \
                        hs["bitrate"] == int(want_bitrate) * 1000:
                    return st
                if not hs["v2"] and not hs["running"]:
                    return st
            elif hs["running"] and hs["answers"] and \
                    hs["bitrate"] == int(want_bitrate) * 1000 and \
                    (want_fmt is None or hs["id_format"] == want_fmt):
                return st
        time.sleep(1)
    raise SystemExit("bench_bus: the simulator did not come back with "
                     f"{want_bitrate}k/{want_fmt} enabled={want_enabled} in {SIM_REBOOT_WINDOW} s")


def sim_set(bitrate=None, id_format=None, enabled=None, base=SIM_URL):
    """Change the simulator's HS CAN. Returns (previous, running): `previous`
    is the legacy dict to hand back to sim_set(**previous) to restore. No
    reboot when nothing changes."""
    raw = _http(f"{base}/api/settings/ecu_sim")
    cur = _legacy_view(raw)
    prev = {"bitrate": cur["bitrate"], "id_format": cur["id_format"],
            "enabled": cur["enabled"]}
    new = _apply_legacy(raw, bitrate, id_format, enabled)
    for k in ("degraded", "pending_reboot"):
        new.pop(k, None)
    if _legacy_view(new) == cur and all(new.get(k) == raw.get(k) for k in _store_keys(raw)):
        return prev, sim_status(base)
    _http(f"{base}/api/settings/ecu_sim", "PUT", new)
    back = _http(f"{base}/api/settings/ecu_sim")
    if any(back.get(k) != new.get(k) for k in _store_keys(raw)):
        raise SystemExit(f"bench_bus: the simulator did not stage {new}")
    try:
        _http(f"{base}/api/settings/submit", "POST", {})
    except (urllib.error.URLError, OSError):
        pass                            # the reply can lose to the restart
    want = _legacy_view(new)
    return prev, _sim_wait(want["bitrate"], want["id_format"], want["enabled"], base)


def dut_can(dut):
    """GET /api/can of the DUT (`dut` = host[:port], e.g. localhost:8081)."""
    return _http(f"http://{dut}/api/can")


# ---- the simulator's own reboot forensics -------------------------------------

def sim_restart_state(base=SIM_URL):
    """ECU sim v2's restart history (GET /api/restart/history, its firmware of
    2026-11-09 on): the same shape as the DUT's, so a stage can tell a
    simulator fault from a DUT fault. Returns {'boot_count',
    'unexpected_resets', 'reason' (this boot's), 'planned', 'parked' (its
    crash-loop brake left the ECU engines off), 'coredump' (a crash dump sits
    in its flash), 'crash' (one line about the crash this boot follows, or
    '')}, or None when it does not answer or its firmware predates the
    tracker (404)."""
    try:
        h = _http(f"{base}/api/restart/history", timeout=8)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(h, dict) or "boot_count" not in h:
        return None
    recs = h.get("records") or []
    top = recs[0] if recs else {}
    crash = top.get("crash") if isinstance(top.get("crash"), dict) else None
    return {"boot_count": h.get("boot_count"),
            "unexpected_resets": h.get("unexpected_resets"),
            "reason": top.get("reason"), "planned": bool(top.get("planned")),
            "parked": (h.get("brake") or {}).get("verdict") not in (None, "normal"),
            "coredump": bool((h.get("coredump") or {}).get("present")),
            "crash": crash.get("summary", "") if crash else ""}


def sim_restart_delta(before, after):
    """What the simulator did between two sim_restart_state() readings, as
    text for a stage's notes, and whether it is a bench fault: ('', False)
    when nothing happened; a planned reboot count otherwise; a reset nobody
    asked for, a park or a stored crash dump make it a fault (the stage's
    verdict then says the bench, not the DUT, failed)."""
    if not before or not after:
        return "", False
    boots = (after["boot_count"] or 0) - (before["boot_count"] or 0)
    unexpected = (after["unexpected_resets"] or 0) - (before["unexpected_resets"] or 0)
    if boots < 0:
        # its PSRAM history started again: a power cycle of the simulator
        return (f"ECU SIM V2 POWER-CYCLED (boot_count {before['boot_count']} -> "
                f"{after['boot_count']}): a bench fault, not the DUT's"), True
    parts = []
    fault = False
    if unexpected > 0:
        parts.append(f"ECU SIM V2 RESET x {unexpected} nobody asked for (reason {after['reason']}"
                     + (f"; {after['crash']}" if after["crash"] else "") + "): a bench fault, not the DUT's")
        fault = True
    elif boots > 0:
        parts.append(f"ecu_sim rebooted {boots} x (planned)")
    if after["parked"]:
        parts.append("ECU SIM V2 PARKED by its crash-loop brake (its ECU engines are off; POST /api/restart)")
        fault = True
    if after["coredump"] and not before["coredump"]:
        parts.append("ECU SIM V2 left a crash dump (GET /api/restart/coredump/raw, then DELETE)")
        fault = True
    return "; ".join(parts), fault


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="what", required=True)
    st = sub.add_parser("status")
    st.add_argument("--dut", default="", help="host[:port] of the DUT API")
    rs = sub.add_parser("restart", help="the simulator's own reboot forensics")
    sm = sub.add_parser("sim")
    sm.add_argument("--bitrate", choices=("250", "500"))
    sm.add_argument("--id-format", choices=("11bit", "29bit", "both"))
    sm.add_argument("--enabled", choices=("0", "1"))
    sm.add_argument("--restore", action="store_true")
    for p in (st, rs, sm):
        p.add_argument("--sim", default=SIM_URL)
    a = ap.parse_args()
    sim_net.install(a.sim)

    if a.what == "restart":
        print("simulator at     :", a.sim)
        print("simulator restarts:", json.dumps(sim_restart_state(a.sim)))
        return 0

    if a.what == "status":
        print("simulator at     :", a.sim)
        print("simulator settings:", json.dumps(sim_get("ecu_sim", a.sim)))
        print("simulator bus     :", json.dumps(sim_status(a.sim)))
        if a.dut:
            print("DUT native CAN    :", json.dumps(dut_can(a.dut)))
        return 0

    if a.restore:
        prev, run = sim_set(base=a.sim, **SIM_DEFAULT)
    else:
        prev, run = sim_set(a.bitrate, a.id_format,
                            None if a.enabled is None else a.enabled == "1",
                            base=a.sim)
    print("BENCH BUS previous:", json.dumps(prev))
    print("BENCH BUS simulator now:", json.dumps(run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
