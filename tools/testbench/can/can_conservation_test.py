#!/usr/bin/env python3
"""CAN counter-conservation bench (MEATPI_COMPONENT_STANDARD §7,
performance-truth rules): put EXACTLY N frames on the wire with the
PCAN at three load tiers and assert the DUT's `/api/can` counters
account for every single one — `rx == N`, `rx_missed == 0`,
`dispatch_drops == 0`. "No crash + plausible traffic" is not a pass;
this bench exists because a 30x rx_count error survived every
functional test (the 2026-07-21 TWAI drain-loop bug).

Run on the PC (IDF venv python — needs python-can + the PCAN on the
DUT's bus):  python tools/testbench/can_conservation_test.py
Expected final line: CAN CONSERVATION PASS
"""
import json
import subprocess
import sys
import time

import can

N = 5000
TIERS = [  # (name, inter-frame gap seconds; 0 = line rate)
    ("10%", 0.002),
    ("50%", 0.00025),
    ("100%", 0.0),
]

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def pi(cmd):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "rpi001", cmd],
                       capture_output=True, text=True, timeout=40)
    return p.stdout.strip()


def canstats(ip):
    return json.loads(pi(f"curl -s -m 6 http://{ip}/api/can"))


def main():
    ip = pi("sudo cat /var/lib/NetworkManager/dnsmasq-wint0.leases "
            "/var/lib/NetworkManager/dnsmasq-wtest0.leases 2>/dev/null | "
            "sort -n | tail -1 | awk '{print $3}'")
    print("DUT", ip)

    # quiet-bus precondition: nothing else may inject frames
    print("quiet-bus precheck (3 s counter watch)…", flush=True)
    a = canstats(ip)
    time.sleep(3)
    b = canstats(ip)
    check("bus_quiet", b["rx"] - a["rx"] == 0,
          f"{b['rx'] - a['rx']} stray frames in 3 s")

    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS2",
                  bitrate=500000)
    msg = can.Message(arbitration_id=0x321, is_extended_id=False,
                      data=bytes(8))

    for tier, (name, gap) in enumerate(TIERS, 1):
        print(f"tier {tier}/{len(TIERS)} ({name} load): sending exactly "
              f"{N} frames…", flush=True)
        before = canstats(ip)
        sent = 0
        t0 = time.time()
        hb = t0
        while sent < N:
            try:
                bus.send(msg, timeout=0.2)
                sent += 1
            except can.CanError:
                continue  # retry until N really left the adapter
            if gap:
                time.sleep(gap)
            if time.time() - hb >= 5:     # liveness during the burst
                hb = time.time()
                print(f"  sent {sent}/{N} "
                      f"({sent / (hb - t0):.0f} f/s)", flush=True)
        dt = time.time() - t0
        print(f"  all {N} on the wire in {dt:.1f} s; 2 s settle, "
              "reading counters…", flush=True)
        time.sleep(2)  # queue drain + counter settle
        after = canstats(ip)

        drx = after["rx"] - before["rx"]
        dmiss = after["rx_missed"] - before["rx_missed"]
        ddrop = after["dispatch_drops"] - before["dispatch_drops"]
        dbuserr = after.get("bus_errors", 0) - before.get("bus_errors", 0)
        rate = sent / dt
        check(f"conserve_{name}",
              drx == N and dmiss == 0 and ddrop == 0,
              f"sent {sent} @ {rate:.0f} f/s -> rx +{drx}, "
              f"missed +{dmiss}, drops +{ddrop}, bus_err +{dbuserr}")
        print(f"PROGRESS {tier}/{len(TIERS)}", flush=True)

    bus.shutdown()

    if fails:
        print("CAN CONSERVATION FAIL: " + ", ".join(fails))
        return 1
    print("CAN CONSERVATION PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
