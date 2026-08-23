#!/usr/bin/env python3
"""BLE CLI wedge bench (PC orchestrator) — MAIN firmware.

Enables BLE (WiFi stays ON — post-cliff this is the supported combo),
runs ble_cli_wedge_pi.py on the bench Pi (UB500 + bleak) against the
real cmdline_manager CLI characteristics, restores BLE off. See the Pi
script for the legs (quick / wedge / flood).

  python ble_cli_wedge_test.py [--usb 192.168.82.1] [--bench rpi001]
Ends with: BLE CLI WEDGE PASS
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PP = "/home/meatpi/.local/lib/python3.11/site-packages"
fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def api(usb, path, method="GET", body=None, retries=3):
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for _ in range(retries):
        req = urllib.request.Request(
            "http://" + usb + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                t = r.read().decode()
                return r.status, (json.loads(t)
                                  if t.strip().startswith(("{", "["))
                                  else t)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except Exception as e:
            last = e
            time.sleep(2)
    raise last


def wait_dut(usb, secs=90):
    ok = 0
    for _ in range(int(secs / 1.5)):
        try:
            api(usb, "/api/status", retries=1)
            ok += 1
            if ok >= 2:
                return True
        except Exception:
            ok = 0
        time.sleep(1.5)
    return False


def pi(bench, cmd, timeout=120):
    for attempt in range(2):
        try:
            p = subprocess.run(["ssh", "-o", "BatchMode=yes",
                                "-o", "ConnectTimeout=10", bench, cmd],
                               capture_output=True, text=True,
                               timeout=timeout)
            return p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            if attempt:
                raise
    return ""


def set_ble(usb, enabled, passkey):
    b = api(usb, "/api/settings/ble_manager")[1]
    for k in ("degraded", "pending_reboot"):
        b.pop(k, None)
    b["enabled"] = enabled
    b["passkey"] = passkey
    api(usb, "/api/settings/ble_manager", "PUT", b)


def submit_reboot(usb):
    try:
        api(usb, "/api/settings/submit", "POST", retries=1)
    except Exception:
        pass
    time.sleep(12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", default="192.168.82.1")
    ap.add_argument("--bench", default="rpi001")
    ap.add_argument("--passkey", type=int, default=421337)
    args = ap.parse_args()

    if not wait_dut(args.usb, 30):
        print("FAIL: DUT unreachable")
        return 1

    subprocess.run(["scp", "-q",
                    os.path.join(HERE, "ble_cli_wedge_pi.py"),
                    f"{args.bench}:/tmp/ble_cli_wedge_pi.py"], check=True)

    print("setup: BLE on (WiFi stays on), rebooting...")
    set_ble(args.usb, True, args.passkey)
    submit_reboot(args.usb)

    try:
        if not wait_dut(args.usb):
            check("DUT back after BLE enable", False)
            return 1
        time.sleep(5)

        st = api(args.usb, "/api/status")[1]
        check("BLE + WiFi enabled together (post-cliff)",
              st["bits"]["ble_enabled"] and
              st["memory"]["internal"]["free"] > 20000,
              f"free={st['memory']['internal']['free']}")

        # bond hygiene BEFORE discovery: a stale LTK breaks pairing, and
        # removing a bond after scanning kills the IRK the reported
        # identity address needs (RPA advertiser). Clean every cached
        # WiC_* device (awk matches old WiCAN_ bonds too), then let the Pi probe discover fresh.
        pi(args.bench,
           "bluetoothctl devices | awk '/WiC/{print $2}' | "
           "while read m; do bluetoothctl -- disconnect $m >/dev/null "
           "2>&1; bluetoothctl -- remove $m >/dev/null 2>&1; done; true")

        out = pi(args.bench,
                 f"cd /tmp && sudo PYTHONPATH={PP} timeout 140 "
                 f"python3 -u ble_cli_wedge_pi.py "
                 f"--passkey {args.passkey}", timeout=155)
        print(out)
        check("Pi probe verdict", "BLE CLI WEDGE PASS" in out)
    finally:
        print("restore: BLE off, rebooting...")
        set_ble(args.usb, False, 123456)
        submit_reboot(args.usb)
        wait_dut(args.usb)

    if fails:
        print("BLE CLI WEDGE FAIL: " + ", ".join(fails))
        return 1
    print("BLE CLI WEDGE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
