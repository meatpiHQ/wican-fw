#!/usr/bin/env python3
"""CAN counter-conservation bench (MEATPI_COMPONENT_STANDARD §7,
performance-truth rules): put EXACTLY N frames on the wire with the
PCAN at three load tiers and assert the DUT's `/api/can` counters
account for every single one, `rx == N`, `rx_missed == 0`,
`dispatch_drops == 0`. "No crash + plausible traffic" is not a pass;
this bench exists because a 30x rx_count error survived every
functional test (the 2026-07-21 TWAI drain-loop bug).

A fourth tier (2026-11-17) is the line rate with the unit writing its
flash in the middle of it: three files of 32 KB uploaded to /data while
the frames run. A flash erase stops every task for its length and leaves
only the receive interrupt, which keeps filling the receive queue; with
the queue at 64 frames (16 ms of the 500 kbit/s line rate) one erase cost
28 to 30 frames. The tier asserts that the flash was written during the
burst (the erase counter moved, else the tier tested nothing) and that
not one frame is missing.

Run on the PC (IDF venv python, needs python-can + the PCAN on the
DUT's bus):
  python tools/testbench/can/can_conservation_test.py [--dut <ip>]
         [--pcan PCAN_USBBUS1] [--only flash] [--flash-frames 20000]
The PCAN channel defaults to the WICAN_PCAN_DUT variable (the adapter's
two plugs may be swapped), the DUT to the newest lease on the Pi.
Expected final line: CAN CONSERVATION PASS
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

import can

N = 5000
TIERS = [  # (name, inter-frame gap seconds; 0 = line rate)
    ("10%", 0.002),
    ("50%", 0.00025),
    ("100%", 0.0),
]
FLASH_UPLOADS = 3
FLASH_UPLOAD_BYTES = 32768
FLASH_PROBE = "/data/conserve_flash_probe.bin"

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def pi(cmd, timeout=40):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "rpi001", cmd],
                       capture_output=True, text=True, timeout=timeout)
    return p.stdout.strip()


def canstats(ip):
    return json.loads(pi(f"curl -s -m 6 http://{ip}/api/can"))


def flash_ops(ip):
    """The unit's flash counters since boot (health.flash on /api/status)."""
    st = json.loads(pi(f"curl -s -m 6 http://{ip}/api/status"))
    return (st.get("health") or {}).get("flash") or {}


def send_exactly(bus, msg, n, gap):
    """n frames on the wire, retried until the adapter took each."""
    sent = 0
    t0 = time.time()
    hb = t0
    while sent < n:
        try:
            bus.send(msg, timeout=0.2)
            sent += 1
        except can.CanError:
            continue  # retry until N really left the adapter
        if gap:
            time.sleep(gap)
        if time.time() - hb >= 5:     # liveness during the burst
            hb = time.time()
            print(f"  sent {sent}/{n} "
                  f"({sent / (hb - t0):.0f} f/s)", flush=True)
    return time.time() - t0


def flash_writer(ip, done):
    """Files uploaded to /data while the frames run: each is erased and
    written on the unit's flash, and tells the configuration backup to
    refresh its copies, which is one more write. done gets the answers."""
    time.sleep(1.0)
    for i in range(FLASH_UPLOADS):
        r = pi(f"head -c {FLASH_UPLOAD_BYTES} /dev/urandom | curl -s -m 30 "
               f"-X POST -H 'Content-Type: application/octet-stream' "
               f"--data-binary @- 'http://{ip}/api/fs/upload?path="
               f"{FLASH_PROBE}'", timeout=60)
        done.append(r[:80])
        time.sleep(1.0)


def flash_tier(ip, bus, msg, min_frames):
    # bus.send() returns when the adapter's driver has a frame, seconds before
    # it is on the wire (the driver queues tens of thousands and the bus takes
    # about 3850 a second of this frame). So the tier keeps handing frames
    # over until the last upload is answered: the wire trails the hand-over,
    # which puts every upload inside the burst whatever each one takes. The
    # number of frames is what was sent, and every one of them is counted.
    print(f"tier flash (100% load, {FLASH_UPLOADS} uploads of "
          f"{FLASH_UPLOAD_BYTES} bytes to the unit's flash meanwhile): frames "
          f"until the uploads are done, at least {min_frames}…", flush=True)
    before, f0 = canstats(ip), flash_ops(ip)
    done = []
    th = threading.Thread(target=flash_writer, args=(ip, done), daemon=True)
    th.start()
    n = 0
    t0 = time.time()
    while th.is_alive() or n < min_frames:
        try:
            bus.send(msg, timeout=0.2)
            n += 1
        except can.CanError:
            continue  # retry until the adapter took it
        if time.time() - t0 > 120:
            break
    print(f"  {n} frames handed over in {time.time() - t0:.1f} s, about "
          f"{n / 3850.0:.0f} s on the wire; waiting for the unit's count to "
          "stand still…", flush=True)
    last, still = None, 0
    while still < 2:            # the unit's own count says when it is over
        time.sleep(1.0)
        rx = canstats(ip)["rx"]
        still = still + 1 if rx == last else 0
        last = rx
    after, f1 = canstats(ip), flash_ops(ip)
    pi(f"curl -s -m 10 -X DELETE 'http://{ip}/api/fs/file?path={FLASH_PROBE}'")

    drx = after["rx"] - before["rx"]
    dmiss = after["rx_missed"] - before["rx_missed"]
    ddrop = after["dispatch_drops"] - before["dispatch_drops"]
    erases = (f1.get("erases") or 0) - (f0.get("erases") or 0)
    writes = (f1.get("writes") or 0) - (f0.get("writes") or 0)
    check("flash_written_during_the_burst",
          erases > 0 and len(done) == FLASH_UPLOADS
          and all('"ok":true' in r for r in done),
          f"{len(done)} of {FLASH_UPLOADS} uploads answered, flash "
          f"+{writes} writes +{erases} erases")
    check("conserve_100%_flash",
          drx == n and dmiss == 0 and ddrop == 0,
          f"sent {n} -> rx +{drx}, missed +{dmiss}, drops +{ddrop}")


def main():
    ap = argparse.ArgumentParser(description="CAN counter conservation")
    ap.add_argument("--dut", default=os.environ.get("WICAN_DUT_IP"),
                    help="the DUT's address as the Pi reaches it")
    ap.add_argument("--pcan",
                    default=os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"),
                    help="the PCAN channel on the DUT's bus")
    ap.add_argument("--only", choices=("tiers", "flash"),
                    help="run the three load tiers alone, or the flash tier")
    ap.add_argument("--flash-frames", type=int, default=20000,
                    help="the least frames of the flash tier (it runs until "
                         "the uploads are done)")
    a = ap.parse_args()

    ip = a.dut or pi(
        "sudo cat /var/lib/NetworkManager/dnsmasq-wint0.leases "
        "/var/lib/NetworkManager/dnsmasq-wtest0.leases 2>/dev/null | "
        "sort -n | tail -1 | awk '{print $3}'")
    print("DUT", ip, "PCAN", a.pcan)

    # quiet-bus precondition: nothing else may inject frames
    print("quiet-bus precheck (3 s counter watch)…", flush=True)
    c0 = canstats(ip)
    time.sleep(3)
    c1 = canstats(ip)
    check("bus_quiet", c1["rx"] - c0["rx"] == 0,
          f"{c1['rx'] - c0['rx']} stray frames in 3 s")

    bus = can.Bus(interface="pcan", channel=a.pcan,
                  bitrate=500000)
    msg = can.Message(arbitration_id=0x321, is_extended_id=False,
                      data=bytes(8))

    tiers = [] if a.only == "flash" else TIERS
    for tier, (name, gap) in enumerate(tiers, 1):
        print(f"tier {tier}/{len(TIERS)} ({name} load): sending exactly "
              f"{N} frames…", flush=True)
        before = canstats(ip)
        dt = send_exactly(bus, msg, N, gap)
        print(f"  all {N} on the wire in {dt:.1f} s; 2 s settle, "
              "reading counters…", flush=True)
        time.sleep(2)  # queue drain + counter settle
        after = canstats(ip)

        drx = after["rx"] - before["rx"]
        dmiss = after["rx_missed"] - before["rx_missed"]
        ddrop = after["dispatch_drops"] - before["dispatch_drops"]
        dbuserr = after.get("bus_errors", 0) - before.get("bus_errors", 0)
        rate = N / dt
        check(f"conserve_{name}",
              drx == N and dmiss == 0 and ddrop == 0,
              f"sent {N} @ {rate:.0f} f/s -> rx +{drx}, "
              f"missed +{dmiss}, drops +{ddrop}, bus_err +{dbuserr}")
        print(f"PROGRESS {tier}/{len(TIERS)}", flush=True)

    if a.only != "tiers":
        flash_tier(ip, bus, msg, a.flash_frames)

    bus.shutdown()

    if fails:
        print("CAN CONSERVATION FAIL: " + ", ".join(fails))
        return 1
    print("CAN CONSERVATION PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
