#!/usr/bin/env python3
"""Cold GPS time-to-first-fix series on the ESPNetLink dongle (2026-10-09).

The measurement the August campaign used, as a repo bench instead of a
session scratchpad: the PSU output is cut for --off-s seconds (the WiCAN
and the bus-powered dongle are fully dead), t0 = `OUTP 1`, and the Pi's
free radio (wtest1, profile espnl-client) joins the dongle's AP and polls
/api/gps + /api/wifi_modem once a second until --after-fix seconds after
the first fix (cap --max). Independent of the WiCAN's own GPS polls.

By default the bench AP (the P4 instrument) is taken down for the series
(`--bench-ap off`) so the WiCAN parks on the dongle's AP and polls it
every 2 s: the configuration of the 2026-08-24/25 baseline (wifi_modem,
boot-cut steady state, zero USB contact after the first pairing). It is
restored at the end; the WiCAN roams home within sta_roam_interval_s.

Per cycle: tfix (s after PSU on), tfix_uptime (the dongle's uptime at that
poll, immune to the PC/Pi clock offset), sats_max, siv_max, fixpct over
the window, when LTE connected and its RSSI. Verdict ESPNL TTFF PASS when
at least half the cycles fixed within --max and the median tfix is inside
the campaign envelope (<= 121 s, near-window placement); FAIL otherwise.
The series is printed as TTFF SERIES and appended to test-reports/logs/.

usage: python tools/testbench/wifi/espnetlink_ttff_bench.py [--n 6] [--psu auto]
         [--max 300] [--after-fix 30] [--off-s 10] [--settle-s 20]
         [--bench-ap off|keep] [--label <name>]
needs: PSU on the bench, ssh rpi001 (wtest1 free, espnl-client carrying
the dongle's current key), the dongle paired on the WiCAN's connector.
Antenna placement decides the numbers: judge by envelope, not single runs.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import bench_ports  # noqa: E402
from owon_psu import OwonPsu  # noqa: E402

PI = "rpi001"
PI_POLLER_SRC = os.path.join(HERE, "..", "pi", "pi_ttff_poll.py")
PI_POLLER = "/tmp/pi_ttff_poll.py"
ENVELOPE_MAX_S = 121.0
REPORT_DIR = os.path.join(HERE, "..", "..", "..", "test-reports", "logs")


def ssh(cmd, timeout=60):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", PI, cmd],
                          capture_output=True, text=True, timeout=timeout)


def pi_offset():
    """Pi clock minus PC clock from one round trip (seconds), and the trip time."""
    a = time.time()
    r = ssh("date +%s.%N", timeout=20)
    b = time.time()
    return float(r.stdout.strip()) - (a + b) / 2.0, b - a


def bench_ap(state):
    r = ssh("cd ~/wican/tools/testbench/pi && python3 bench_ap_cli.py 'ap %s' 2>&1 | tail -1" % state, timeout=30)
    print("bench_ap %s: %s" % (state, r.stdout.strip()[-80:]), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--psu", default="auto")
    ap.add_argument("--max", type=float, default=300)
    ap.add_argument("--after-fix", type=float, default=30)
    ap.add_argument("--off-s", type=float, default=10)
    ap.add_argument("--settle-s", type=float, default=20)
    ap.add_argument("--bench-ap", choices=("off", "keep"), default="off")
    ap.add_argument("--label", default=time.strftime("ttff_%Y%m%d_%H%M"))
    a = ap.parse_args()

    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, "%s.jsonl" % a.label)
    out = open(out_path, "a")
    r = subprocess.run(["scp", "-o", "BatchMode=yes", PI_POLLER_SRC, "%s:%s" % (PI, PI_POLLER)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("could not deploy the Pi poller: " + r.stderr.strip())
    psu_port = bench_ports.resolve(a.psu, "psu", "COM50")
    psu = OwonPsu(psu_port)
    print("psu %s: %s V=%s out=%s" % (psu_port, psu.idn, psu.query("MEAS:VOLT?"), psu.query("OUTP?")), flush=True)
    if a.bench_ap == "off":
        bench_ap("off")
        time.sleep(25)
    results = []
    try:
        for i in range(1, a.n + 1):
            print("\n=== cycle %d/%d  %s" % (i, a.n, time.strftime("%H:%M:%S")), flush=True)
            psu.output(False)
            time.sleep(1)
            print("psu off: %s %s" % (psu.query("OUTP?"), psu.query("OUTP?")), flush=True)
            time.sleep(max(0.0, a.off_s - 1))
            off, rtt = pi_offset()
            t0 = time.time()
            psu.output(True)
            print("PSU ON at %s (t0; pi clock offset %.2f s, rtt %.2f)"
                  % (time.strftime("%H:%M:%S", time.localtime(t0)), off, rtt), flush=True)
            samples = "/tmp/%s_%d.jsonl" % (a.label, i)
            r = ssh("python3 -u %s --t0 %.3f --max %d --after-fix %d --out %s"
                    % (PI_POLLER, t0 + off, a.max, a.after_fix, samples), timeout=a.max + 90)
            lines = [l for l in r.stdout.splitlines() if l.startswith("RESULT ")]
            res = json.loads(lines[0][7:]) if lines else {"error": (r.stdout + r.stderr)[-300:]}
            res.update({"cycle": i, "label": a.label,
                        "t0": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                        "psu_v": psu.query("MEAS:VOLT?"), "psu_a": psu.query("MEAS:CURR?")})
            subprocess.run(["scp", "-o", "BatchMode=yes", "%s:%s" % (PI, samples),
                            os.path.join(REPORT_DIR, "%s_samples_%d.jsonl" % (a.label, i))],
                           capture_output=True)
            print("RESULT " + json.dumps(res), flush=True)
            out.write(json.dumps(res) + "\n")
            out.flush()
            results.append(res)
            time.sleep(a.settle_s)
    finally:
        psu.output(True)
        psu.close()
        ssh("sudo -n nmcli con down espnl-client >/dev/null 2>&1; true", timeout=30)
        if a.bench_ap == "off":
            bench_ap("on")
        out.close()
    tf = [r["tfix"] for r in results if r.get("tfix") is not None]
    nofix = sum(1 for r in results if r.get("tfix") is None)
    print("\nTTFF SERIES %s: %s  (no fix within %ds: %d of %d)  uptime-based: %s"
          % (a.label, tf, a.max, nofix, len(results),
             [r.get("tfix_uptime") for r in results]), flush=True)
    med = statistics.median(tf) if tf else None
    if tf:
        print("median %.1f s  min %.1f  max %.1f  fixpct %s  sats_max %s  lte_rssi %s"
              % (med, min(tf), max(tf), [r.get("fixpct") for r in results],
                 [r.get("sats_max") for r in results], [r.get("lte_rssi") for r in results]), flush=True)
    ok = bool(tf) and nofix * 2 <= len(results) and med <= ENVELOPE_MAX_S
    print("ESPNL TTFF PASS" if ok else "ESPNL TTFF FAIL: median %s, no fix %d of %d"
          % (med, nofix, len(results)), flush=True)
    print("results: %s" % out_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
