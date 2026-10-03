#!/usr/bin/env python3
"""The bench CAN bus as one thing: bitrate and id format of every node.

The bench bus has four nodes: the DUT's OBD chip, the DUT's native CAN
controller, the ECU simulator and the PCAN adapter. A node left at another
bitrate in normal mode destroys the traffic with error frames, and a
listen-only DUT never ACKs, so a 250k leg needs the simulator AT 250k (it is
the ACK source) or out of the bus. This helper moves the simulator and tells
what the DUT's native controller is set to; PCAN scripts take their bitrate
as an argument and the chip follows its protocol (ATTP).

  python bench_bus.py status
  python bench_bus.py sim --bitrate 250 [--id-format 29bit] [--enabled 1]
  python bench_bus.py sim --restore            (back to 500k / 11bit / enabled)

As a module: sim_get/sim_set/sim_status, dut_can(). The simulator's REST is
reachable from the PC only (USB-NCM 192.168.8.1); its settings PUT replaces
the whole object, hence GET, modify, PUT. A submit reboots it (~20 s).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

SIM_URL = "http://192.168.8.1"
SIM_DEFAULT = {"bitrate": "500", "id_format": "11bit", "enabled": True}
SIM_REBOOT_WINDOW = 60


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


def sim_get(name="ecu_sim", base=SIM_URL):
    return _http(f"{base}/api/settings/{name}")


def sim_status(base=SIM_URL):
    """The simulator's running bus: {'running', 'bitrate', 'id_format', ...}
    or None when it does not answer."""
    try:
        return _http(f"{base}/api/ecu/status")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _sim_wait(want_bitrate, want_fmt, want_enabled, base):
    """Wait for the reboot and for the running bus to match."""
    time.sleep(3)                       # the restart fires ~1 s after submit
    end = time.time() + SIM_REBOOT_WINDOW
    while time.time() < end:
        st = sim_status(base)
        if st is not None:
            if not want_enabled:
                if not st.get("running"):
                    return st
            elif st.get("running") and \
                    st.get("bitrate") == int(want_bitrate) * 1000 and \
                    st.get("id_format") == want_fmt:
                return st
        time.sleep(1)
    raise SystemExit("bench_bus: the simulator did not come back with "
                     f"{want_bitrate}k/{want_fmt} in {SIM_REBOOT_WINDOW} s")


def sim_set(bitrate=None, id_format=None, enabled=None, base=SIM_URL):
    """Change the simulator's bus settings. Returns (previous, running):
    `previous` is the dict to hand back to sim_set(**previous) to restore.
    No reboot when nothing changes."""
    cur = sim_get("ecu_sim", base)
    prev = {"bitrate": cur["bitrate"], "id_format": cur["id_format"],
            "enabled": cur["enabled"]}
    new = dict(cur)
    if bitrate is not None:
        new["bitrate"] = str(bitrate)
    if id_format is not None:
        new["id_format"] = id_format
    if enabled is not None:
        new["enabled"] = bool(enabled)
    if new == cur:
        return prev, sim_status(base)
    _http(f"{base}/api/settings/ecu_sim", "PUT", new)
    back = sim_get("ecu_sim", base)
    if any(back[k] != new[k] for k in ("bitrate", "id_format", "enabled")):
        raise SystemExit(f"bench_bus: the simulator did not stage {new}")
    try:
        _http(f"{base}/api/settings/submit", "POST", {})
    except (urllib.error.URLError, OSError):
        pass                            # the reply can lose to the restart
    return prev, _sim_wait(new["bitrate"], new["id_format"], new["enabled"],
                           base)


def dut_can(dut):
    """GET /api/can of the DUT (`dut` = host[:port], e.g. localhost:8081)."""
    return _http(f"http://{dut}/api/can")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="what", required=True)
    st = sub.add_parser("status")
    st.add_argument("--dut", default="", help="host[:port] of the DUT API")
    sm = sub.add_parser("sim")
    sm.add_argument("--bitrate", choices=("250", "500"))
    sm.add_argument("--id-format", choices=("11bit", "29bit"))
    sm.add_argument("--enabled", choices=("0", "1"))
    sm.add_argument("--restore", action="store_true")
    for p in (st, sm):
        p.add_argument("--sim", default=SIM_URL)
    a = ap.parse_args()

    if a.what == "status":
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
