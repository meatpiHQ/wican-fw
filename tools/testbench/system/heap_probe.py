#!/usr/bin/env python3
"""Internal-heap probe: what each configuration costs at idle.

Restarts the DUT under a short list of configurations and reads the internal
heap (free, min_free since boot, largest block) and the task list a fixed
time after each boot, so a change to a component's RAM placement can be
measured on the same protocol before and after. The configurations:

  found      the DUT as found (twice: the run-to-run noise of the reading)
  link_off   espnetlink.enabled=false (the dongle link logic; the USB host
             still enumerates whatever is on the connector)
  usb_off    usb_host_manager.enabled=false on top (nothing on the USB side)
  restored   the found values back, read once more

Every configuration ends with the found settings restored; a run that dies
midway leaves the DUT on the configuration it was testing (restore by hand:
espnetlink.enabled and usb_host_manager.enabled as the table's first row).

  python tools/testbench/system/heap_probe.py --dut localhost:18081
         [--settle 150] [--only found,link_off] [--logdir DIR]

Prints one row per configuration and HEAP PROBE DONE; the rows also land as
JSON in the log dir. This is a measurement, not a pass/fail bench.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
from pcbench import Dut, Bench  # noqa: E402


def mem_row(dut):
    st = dut.get("/api/status")
    m = st.get("memory", {}).get("internal", {})
    tasks = dut.get("/api/status/tasks").get("tasks", [])
    return {
        "uptime": st.get("uptime"),
        "total": m.get("total"),
        "free": m.get("free"),
        "min_free": m.get("min_free"),
        "largest": m.get("largest_block"),
        "log_errors": st.get("health", {}).get("log_errors"),
        "tasks": sorted(t.get("name", "?") for t in tasks),
        "stack_hw": {t.get("name"): t.get("stack_hw") for t in tasks},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--settle", type=int, default=150,
                    help="seconds after the boot before the reading")
    ap.add_argument("--only", default="",
                    help="comma list of configurations (default: all)")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M")
    logdir = a.logdir or os.path.join(os.path.dirname(__file__), "..", "..",
                                      "..", "test-reports", "logs",
                                      "heap_probe_" + stamp)
    os.makedirs(logdir, exist_ok=True)

    dut = Dut(a.dut)
    found_link = dut.settings("espnetlink").get("enabled")
    found_usb = dut.settings("usb_host_manager").get("enabled")
    st = dut.get("/api/status")
    print(f"DUT {st.get('version')} on {st.get('partition')}, found: "
          f"espnetlink.enabled={found_link} usb_host_manager.enabled={found_usb}")

    plan = [
        ("found", {}),
        ("found_again", {}),
        ("link_off", {"espnetlink": {"enabled": False}}),
        ("usb_off", {"espnetlink": {"enabled": False},
                     "usb_host_manager": {"enabled": False}}),
        ("restored", {"espnetlink": {"enabled": found_link},
                      "usb_host_manager": {"enabled": found_usb}}),
    ]
    only = [s.strip() for s in a.only.split(",") if s.strip()]
    if only:
        plan = [p for p in plan if p[0] in only or p[0] == "restored"]

    rows = []
    base_tasks = None
    try:
        for name, changes in plan:
            t_back = dut.restart(changes)
            time.sleep(max(0, a.settle - (time.time() - t_back)))
            r = mem_row(dut)
            r["config"] = name
            rows.append(r)
            if base_tasks is None:
                base_tasks = set(r["tasks"])
            gone = sorted(base_tasks - set(r["tasks"]))
            new = sorted(set(r["tasks"]) - base_tasks)
            print(f"{name:12s} uptime {r['uptime']}  total {r['total']}  "
                  f"free {r['free']}  min_free {r['min_free']}  "
                  f"largest {r['largest']}  log_errors {r['log_errors']}"
                  + (f"  tasks gone: {' '.join(gone)}" if gone else "")
                  + (f"  tasks new: {' '.join(new)}" if new else ""),
                  flush=True)
    except Bench as e:
        print(f"PROBE ABORTED: {e}", flush=True)
        print("restore by hand: espnetlink.enabled="
              f"{found_link} usb_host_manager.enabled={found_usb}")
        with open(os.path.join(logdir, "rows.json"), "w") as f:
            json.dump(rows, f, indent=1)
        return 2

    with open(os.path.join(logdir, "rows.json"), "w") as f:
        json.dump(rows, f, indent=1)
    base = rows[0]
    print("deltas against the first reading (free / min_free):")
    for r in rows[1:]:
        print(f"  {r['config']:12s} {r['free'] - base['free']:+6d} / "
              f"{r['min_free'] - base['min_free']:+6d}")
    print(f"rows: {os.path.join(logdir, 'rows.json')}")
    print("HEAP PROBE DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
