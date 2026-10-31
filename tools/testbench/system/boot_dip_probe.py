#!/usr/bin/env python3
"""The post-boot dip: where the internal heap's floor is set after the boot.

The boot map (`WICAN RAMMIN <step>=<min>` on the console) names the lowest
internal-RAM point of the boot SEQUENCE, but the heap probe's `min_free`
(read 150 s after a boot) is set later, by the services that come up after
`WICAN RAMMIN end`: the station's join and DHCP, the USB host enumerating the
dongle, the backup copies' first sweep (the first PSRAM-stack caller of a
boot, which creates the flash worker), mDNS, MQTT, the webhook poster. This
probe restarts the DUT and samples `/api/status` memory.internal (free,
min_free, largest block) and the task list every --period seconds over the
first --seconds of the boot, so the moment the floor moves and the tasks that
appeared around it are named. Two configurations by default:

  found      the DUT as found (the dongle enumerating if it is on)
  usb_off    espnetlink.enabled=false + usb_host_manager.enabled=false
             (nothing on the USB side: the enumeration's share of the dip)
  restored   the found values back (no samples)

  python tools/testbench/system/boot_dip_probe.py --dut localhost:18085
         [--seconds 60] [--period 2] [--only found,usb_off] [--logdir DIR]

Prints one line per sample where min_free moved (with the tasks that came
and went since the previous sample), a summary per configuration (the floor,
its moment, the tasks alive at that moment) and BOOT DIP PROBE DONE. A
measurement, not a pass/fail bench; the samples land as JSON in the log dir.
A run that dies midway leaves the DUT on the configuration under test:
restore by hand (the first line of the output names the found values).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
from pcbench import Dut, Bench  # noqa: E402


def uptime_s(text):
    """'HH:MM:SS' (or a number) to seconds; None when unreadable."""
    if isinstance(text, (int, float)):
        return float(text)
    try:
        parts = [int(p) for p in str(text).split(":")]
    except ValueError:
        return None
    s = 0
    for p in parts:
        s = s * 60 + p
    return float(s)


def sample(dut):
    code, st = dut.api("/api/status", timeout=4)
    if code != 200 or not isinstance(st, dict):
        return None
    m = st.get("memory", {}).get("internal", {})
    code, t = dut.api("/api/status/tasks", timeout=4)
    tasks = t.get("tasks", []) if code == 200 and isinstance(t, dict) else []
    return {
        "t": time.time(),
        "uptime": uptime_s(st.get("uptime")),
        "free": m.get("free"),
        "min_free": m.get("min_free"),
        "largest": m.get("largest_block"),
        "tasks": sorted(x.get("name", "?") for x in tasks),
        "stack_hw": {x.get("name"): x.get("stack_hw") for x in tasks},
    }


def watch(dut, t_back, seconds, period):
    """Samples from the moment the DUT answered again, every `period` s."""
    rows = []
    prev = None
    end = t_back + seconds
    while time.time() < end:
        r = sample(dut)
        if r is not None:
            rows.append(r)
            if prev is not None and r["min_free"] is not None and \
                    prev["min_free"] is not None and \
                    r["min_free"] < prev["min_free"]:
                came = sorted(set(r["tasks"]) - set(prev["tasks"]))
                went = sorted(set(prev["tasks"]) - set(r["tasks"]))
                print(f"  {r['uptime']:6.0f} s  free {r['free']:6d}  "
                      f"min_free {prev['min_free']:6d} -> {r['min_free']:6d}"
                      f"  ({r['min_free'] - prev['min_free']:+d})"
                      + (f"  came: {' '.join(came)}" if came else "")
                      + (f"  went: {' '.join(went)}" if went else ""),
                      flush=True)
            prev = r
        time.sleep(max(0.0, period - 0.3))
    return rows


def summary(name, rows):
    if not rows:
        print(f"{name}: no samples")
        return {}
    low = min(rows, key=lambda r: (r["min_free"] if r["min_free"] is not None
                                   else 1 << 30))
    first = rows[0]
    last = rows[-1]
    # the sample before the floor was set: what changed between the two
    i = rows.index(low)
    before = rows[i - 1] if i > 0 else None
    came = sorted(set(low["tasks"]) - set(before["tasks"])) if before else []
    print(f"{name}: first sample at {first['uptime']:.0f} s free {first['free']}"
          f" min_free {first['min_free']}; floor {low['min_free']} B at "
          f"{low['uptime']:.0f} s (free {low['free']}, largest "
          f"{low['largest']}); last sample at {last['uptime']:.0f} s free "
          f"{last['free']} min_free {last['min_free']}; {len(low['tasks'])} "
          f"tasks at the floor"
          + (f", came since the previous sample: {' '.join(came)}"
             if came else ""), flush=True)
    return {"first": first, "floor": low, "last": last, "came": came}


def ring_around(dut, uptime_from, uptime_to):
    """The log ring's lines whose timestamp (ms since boot) falls in the
    window; the boot's own lines name what ran at the dip."""
    out = []
    for ln in dut.ring_lines():
        # "I (12345) tag: ..." : the number is ms since boot
        if not ln[:1] in "IWED" or " (" not in ln:
            continue
        try:
            ms = int(ln.split("(", 1)[1].split(")", 1)[0])
        except ValueError:
            continue
        if uptime_from * 1000 <= ms <= uptime_to * 1000:
            out.append(ln)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--period", type=float, default=2.0)
    ap.add_argument("--only", default="",
                    help="comma list of configurations (default: all)")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M")
    logdir = a.logdir or os.path.join(os.path.dirname(__file__), "..", "..",
                                      "..", "test-reports", "logs",
                                      "boot_dip_" + stamp)
    os.makedirs(logdir, exist_ok=True)

    dut = Dut(a.dut)
    found_link = dut.settings("espnetlink").get("enabled")
    found_usb = dut.settings("usb_host_manager").get("enabled")
    st = dut.get("/api/status")
    print(f"DUT {st.get('version')} on {st.get('partition')}, found: "
          f"espnetlink.enabled={found_link} usb_host_manager.enabled={found_usb}",
          flush=True)

    plan = [
        ("found", {}),
        ("usb_off", {"espnetlink": {"enabled": False},
                     "usb_host_manager": {"enabled": False}}),
        ("restored", {"espnetlink": {"enabled": found_link},
                      "usb_host_manager": {"enabled": found_usb}}),
    ]
    only = [s.strip() for s in a.only.split(",") if s.strip()]
    if only:
        plan = [p for p in plan if p[0] in only or p[0] == "restored"]

    result = {}
    try:
        for name, changes in plan:
            print(f"== {name}: restarting ==", flush=True)
            t_back = dut.restart(changes)
            if name == "restored":
                time.sleep(5)
                r = sample(dut)
                print(f"restored: free {r['free']} min_free {r['min_free']}"
                      if r else "restored: no sample", flush=True)
                continue
            rows = watch(dut, t_back, a.seconds, a.period)
            s = summary(name, rows)
            if s:
                lo = s["floor"]["uptime"] or 0
                lines = ring_around(dut, max(0, lo - 6), lo + 2)
                print(f"  the log between {max(0, lo - 6):.0f} and "
                      f"{lo + 2:.0f} s of the boot ({len(lines)} lines):")
                for ln in lines[-30:]:
                    print("    " + ln[:150])
            result[name] = {"rows": rows, "summary": s,
                            "log": ring_around(dut, 0, a.seconds + 5)}
            with open(os.path.join(logdir, f"{name}.json"), "w") as f:
                json.dump(result[name], f, indent=1)
    except Bench as e:
        print(f"PROBE ABORTED: {e}", flush=True)
        print("restore by hand: espnetlink.enabled="
              f"{found_link} usb_host_manager.enabled={found_usb}")
        return 2

    print(f"rows: {logdir}")
    print("BOOT DIP PROBE DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
