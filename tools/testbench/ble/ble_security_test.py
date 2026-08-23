#!/usr/bin/env python3
"""BLE security + bonding end-to-end test (runs on the PC; orchestrates
the Pi's UB500 dongle over ssh + the DUT over the USB link).

Verifies the 2026-07-08 BLE hardening on real hardware:
  * gate    — a WRONG passkey cannot pair, so no characteristic is
              readable (item 4: chars only readable by an authenticated peer)
  * pair    — the CORRECT passkey pairs (MITM) and the gated Device-Info
              read succeeds
  * persist — after a DUT REBOOT the bonded central reconnects and reads
              WITHOUT re-pairing / re-entering the passkey (items 1 + 3:
              NVS bond persistence)

BLE + the full firmware composition sits on the internal-RAM cliff
(~2 KB free), so this test needs a BLE-focused profile: **WiFi off, BLE
on**. Pass --setup to switch the DUT into it (saves nothing — restore with
--restore), or set it yourself first. The USB link (192.168.82.1) stays up
with WiFi off, so the DUT is controllable throughout.

  python ble_security_test.py [--usb 192.168.82.1] [--bench rpi001]
                              [--passkey N] [--setup] [--restore]

Needs on the Pi: python3-dbus + bleak (see ble_bond_probe.py). The probe
is scp'd to the Pi automatically.
"""
import argparse
import json
import os
import subprocess
import sys
import time
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
    for attempt in range(retries):
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
        except Exception as e:  # timeout / USB-NCM re-enumeration blip
            last = e
            time.sleep(2)
    raise last


def wait_dut(usb, secs=90):
    # the USB-NCM link re-enumerates on a DUT reboot, so require TWO
    # consecutive good reads before calling it up
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


def pi(bench, cmd, timeout=60):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", bench, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return (p.stdout + p.stderr)


def set_ble(usb, enabled, passkey):
    b = api(usb, "/api/settings/ble_manager")[1]
    for k in ("degraded", "pending_reboot"):
        b.pop(k, None)
    b["enabled"] = enabled
    b["passkey"] = passkey
    api(usb, "/api/settings/ble_manager", "PUT", b)


def set_wifi_mode(usb, mode):
    w = api(usb, "/api/settings/wifi_manager")[1]
    for k in ("degraded", "pending_reboot"):
        w.pop(k, None)
    w["mode"] = mode
    api(usb, "/api/settings/wifi_manager", "PUT", w)


def submit_reboot(usb):
    try:
        api(usb, "/api/settings/submit", "POST")
    except Exception:
        pass


def scan_addr(bench, timeout=90):
    out = pi(bench,
             f"sudo PYTHONPATH={PP} timeout 25 python3 -c \""
             "import asyncio\n"
             "from bleak import BleakScanner\n"
             "async def m():\n"
             "    for d in await BleakScanner.discover(timeout=12):\n"
             "        if d.name and d.name.startswith('WiC_'):\n"
             "            print(d.address); return\n"
             "asyncio.run(m())\"", timeout=timeout)
    for line in out.splitlines():
        line = line.strip()
        if len(line) == 17 and line.count(":") == 5:
            return line
    return None


def probe(bench, addr, passkey, mode, timeout=70):
    out = pi(bench,
             f"cd /tmp && sudo PYTHONPATH={PP} timeout {timeout - 5} "
             f"python3 -u ble_bond_probe.py --mac {addr} --passkey {passkey} "
             f"--mode {mode}", timeout=timeout)
    for line in out.splitlines():
        if line.startswith("RESULT:"):
            return line
    return "RESULT: (no output)\n" + out[-300:]


def remove_bond(bench, addr):
    pi(bench, f"bluetoothctl -- disconnect {addr} 2>/dev/null; "
              f"bluetoothctl -- remove {addr} 2>/dev/null; true")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", default="192.168.82.1")
    ap.add_argument("--bench", default="rpi001")
    ap.add_argument("--passkey", type=int, default=421337)
    ap.add_argument("--setup", action="store_true",
                    help="switch DUT to WiFi-off/BLE-on first")
    ap.add_argument("--restore", action="store_true",
                    help="restore WiFi apsta + BLE off, then exit")
    args = ap.parse_args()

    if args.restore:
        set_wifi_mode(args.usb, "apsta")
        set_ble(args.usb, False, 123456)
        submit_reboot(args.usb)
        print("restoring: WiFi apsta + BLE off, rebooting...")
        wait_dut(args.usb)
        sys.exit(0)

    # push the probe
    subprocess.run(["scp", "-q", os.path.join(HERE, "ble_bond_probe.py"),
                    f"{args.bench}:/tmp/ble_bond_probe.py"], check=True)

    if args.setup:
        set_wifi_mode(args.usb, "off")
        set_ble(args.usb, True, args.passkey)
        submit_reboot(args.usb)
        print("setup: WiFi off, BLE on, rebooting...")
        if not wait_dut(args.usb):
            check("DUT came back after setup", False)
            sys.exit(1)
        time.sleep(5)

    st = api(args.usb, "/api/status")[1]
    check("BLE enabled + off the RAM cliff",
          st["bits"]["ble_enabled"] and st["memory"]["internal"]["free"] >
          20000, f"free={st['memory']['internal']['free']}")

    addr = scan_addr(args.bench)
    check("DUT advertising (scannable)", addr is not None, addr or "no adv")
    if addr is None:
        print("BLE SECURITY FAIL:", ", ".join(fails))
        sys.exit(1)

    # 1) GATE: wrong passkey must be refused, nothing readable
    remove_bond(args.bench, addr)
    r = probe(args.bench, addr, (args.passkey + 1) % 1000000, "wrongkey")
    check("gate: wrong passkey blocked (no char readable unpaired)",
          "wrongkey_read=blocked" in r, r.strip())

    # 2) PAIR: correct passkey pairs (MITM) + gated read works
    remove_bond(args.bench, addr)
    r = probe(args.bench, addr, args.passkey, "pair")
    check("pair: correct passkey pairs + gated Device-Info read",
          "ok=True" in r, r.strip())

    # 3) PERSIST: reboot the DUT, reconnect with NO re-pair
    api(args.usb, "/api/restart", "POST")
    print("  rebooting DUT to test bond persistence...")
    time.sleep(14)
    if not wait_dut(args.usb):
        check("DUT came back after reboot", False)
        sys.exit(1)
    time.sleep(5)
    addr2 = scan_addr(args.bench) or addr
    r = probe(args.bench, addr2, args.passkey, "reconnect")
    check("persist: bond survives reboot, reconnect w/o passkey",
          "bond_persisted=True" in r, r.strip())

    if fails:
        print("BLE SECURITY FAIL:", ", ".join(fails))
        sys.exit(1)
    print("BLE SECURITY PASS")


if __name__ == "__main__":
    main()
