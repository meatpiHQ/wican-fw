#!/usr/bin/env python3
"""Pi-side TTFF poller: join the ESPNetLink's AP and poll its GPS at 1 Hz.

Deployed to /tmp by tools/testbench/wifi/espnetlink_ttff_bench.py (scp) and
run over ssh once per cold cycle:

  python3 /tmp/pi_ttff_poll.py --t0 <epoch on the Pi clock> [--max 300]
          [--after-fix 30] [--con espnl-client] [--ifname wtest1]
          [--host 192.168.80.1] [--out /tmp/ttff.jsonl]

Re-ups the NetworkManager profile every 6 s until the radio is associated
(the dongle's AP comes up a few seconds after its boot and every cold cycle
drops the association), then reads /api/gps and /api/wifi_modem once a
second. Stops --after-fix seconds after the first fix or at --max. Every
sample is a JSON line in --out; the summary is printed as one line
prefixed RESULT (tfix = seconds after t0, tfix_uptime = the dongle's own
uptime at that poll, sats_max, siv_max, fixpct, lte_connected_at).
"""
import argparse
import json
import subprocess
import time

import requests


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=40)


def joined(ifname):
    r = sh("nmcli -t dev status")
    return any(l.startswith(ifname + ":wifi:connected") for l in r.stdout.splitlines())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--t0", type=float, required=True)
    ap.add_argument("--max", type=float, default=300)
    ap.add_argument("--after-fix", type=float, default=30)
    ap.add_argument("--con", default="espnl-client")
    ap.add_argument("--ifname", default="wtest1")
    ap.add_argument("--host", default="192.168.80.1")
    ap.add_argument("--out", default="/tmp/ttff.jsonl")
    a = ap.parse_args()

    t0 = a.t0
    deadline = t0 + a.max
    first_fix = None
    first_fix_t = None
    first_fix_uptime = None
    first_valid = None
    first_poll = None
    join_at = None
    sats_max = 0
    siv_max = 0
    n_polls = 0
    n_fix = 0
    last_up_try = 0.0
    lte_connected_at = None
    lte_rssi_last = None
    samples = open(a.out, "w")
    sess = requests.Session()
    while True:
        now = time.time()
        if now > deadline:
            break
        if first_fix is not None and now > first_fix + a.after_fix:
            break
        if not joined(a.ifname):
            if now - last_up_try > 6:
                last_up_try = now
                sh("sudo -n nmcli con up %s >/dev/null 2>&1" % a.con)
            time.sleep(1)
            continue
        if join_at is None:
            join_at = now - t0
        try:
            g = sess.get("http://%s/api/gps" % a.host, timeout=1.5).json()
            h = sess.get("http://%s/api/wifi_modem" % a.host, timeout=1.5).json()
        except Exception as e:  # noqa: BLE001
            samples.write(json.dumps({"t": round(now - t0, 1), "err": str(e)[:80]}) + "\n")
            time.sleep(1)
            continue
        t = round(now - t0, 1)
        if first_poll is None:
            first_poll = t
        n_polls += 1
        lte = h.get("lte", {})
        rec = {"t": t, "uptime_s": h.get("uptime_s"), "valid": g.get("valid"), "fix": g.get("fix"),
               "satellites": g.get("satellites"), "sats_in_view": g.get("sats_in_view"),
               "hdop": g.get("hdop"), "fix_quality": g.get("fix_quality"),
               "lte_stage": lte.get("stage"), "lte_rssi": lte.get("rssi_dbm"),
               "usb_data": h.get("usb_data"), "boot_cut": h.get("boot_cut"),
               "clients": h.get("ap", {}).get("clients")}
        samples.write(json.dumps(rec) + "\n")
        samples.flush()
        sats_max = max(sats_max, g.get("satellites") or 0)
        siv_max = max(siv_max, g.get("sats_in_view") or 0)
        if g.get("valid") and first_valid is None:
            first_valid = t
        if g.get("fix"):
            n_fix += 1
            if first_fix is None:
                first_fix = now
                first_fix_t = t
                first_fix_uptime = h.get("uptime_s")
        if lte.get("connected") and lte_connected_at is None:
            lte_connected_at = t
        if lte.get("rssi_dbm") is not None:
            lte_rssi_last = lte.get("rssi_dbm")
        time.sleep(max(0.0, 1.0 - (time.time() - now)))
    samples.close()
    res = {"tfix": first_fix_t, "tfix_uptime": first_fix_uptime, "first_valid": first_valid,
           "first_poll": first_poll, "join_at": (round(join_at, 1) if join_at else None),
           "sats_max": sats_max, "siv_max": siv_max, "polls": n_polls,
           "fixpct": (round(100.0 * n_fix / n_polls, 1) if n_polls else 0.0),
           "lte_connected_at": lte_connected_at, "lte_rssi": lte_rssi_last, "max": a.max}
    print("RESULT " + json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
