#!/usr/bin/env python3
"""BLE security + bonding end-to-end test (runs on the PC; orchestrates
the Pi's UB500 dongle over ssh + the DUT over the USB link).

Verifies the 2026-07-08 BLE hardening on real hardware:
  * gate:     a WRONG passkey cannot pair, so no characteristic is
              readable (item 4: chars only readable by an authenticated peer)
  * pair:     the CORRECT passkey pairs (MITM) and the gated Device-Info
              read succeeds
  * persist: after a DUT REBOOT the bonded central reconnects and reads
              WITHOUT re-pairing / re-entering the passkey (items 1 + 3:
              NVS bond persistence)

Since 2026-11-01 (the NimBLE host task on a PSRAM stack, its bond store's
NVS writes on the flash_jobs worker) the pair and persist legs also read
the firmware's side of the write: the `nimble_host` row of
/api/status/tasks (`stack_ext` true, at least 1 KB of headroom after the
pairing, the deepest path that task runs) and `health.flash_jobs` of
/api/status (the marshalled count grew across the pairing, no refusal),
and the log ring carries no E line from the Bluetooth tags (E lines of
other tags are reported, not failed: with WiFi off the bench DUT's other
clients complain).

BLE + the full firmware composition sits on the internal-RAM cliff
(~2 KB free), so this test needs a BLE-focused profile: **WiFi off, BLE
on**. Pass --setup to switch the DUT into it: the found wifi_manager and
ble_manager documents are saved first (test-reports/logs/
ble_security_found.json) and --restore puts exactly those back (the bench
DUT runs in station mode; the earlier restore to apsta left it on a mode
it never had). The USB link (192.168.82.1, the device role's NCM
adapter) stays up with WiFi off, so the DUT is controllable throughout.

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
FOUND = os.path.normpath(os.path.join(HERE, "..", "..", "..", "test-reports",
                                      "logs", "ble_security_found.json"))
# E lines of these tags fail the bench; the rest are reported
OWN_TAGS = ("ble_manager", "ble_http", "ble_j2534", "NimBLE", "BT_", "BLE_",
            "nimble", "flash_jobs", "bt_", "ble_hs", "ble_store")
fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def api(usb, path, method="GET", body=None, retries=3, raw=False, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            "http://" + usb + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                t = r.read().decode(errors="replace")
                if raw:
                    return r.status, t
                return r.status, (json.loads(t)
                                  if t.strip().startswith(("{", "["))
                                  else t)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")
        except Exception as e:  # timeout / USB-NCM re-enumeration blip
            last = e
            time.sleep(2)
    raise last


def wait_dut(usb, secs=180):
    # the USB-NCM link re-enumerates on a DUT reboot, so require TWO
    # consecutive good reads before calling it up. Bounded by the wall
    # clock (2026-11-01): the earlier loop counted tries, each a 15 s
    # timeout, and a dead link (a parked DUT) held the stage for 16 min
    deadline = time.time() + secs
    ok = 0
    while time.time() < deadline:
        try:
            api(usb, "/api/status", retries=1, timeout=5)
            ok += 1
            if ok >= 2:
                return True
        except Exception:
            ok = 0
        time.sleep(1.5)
    print(f"  DUT not back on {usb} within {secs} s")
    return False


def pi(bench, cmd, timeout=60):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", bench, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return (p.stdout + p.stderr)


def put_doc(usb, comp, doc):
    doc = dict(doc)
    for k in ("degraded", "pending_reboot"):
        doc.pop(k, None)
    st, body = api(usb, "/api/settings/" + comp, "PUT", doc)
    if st != 200:
        print(f"  PUT {comp} answered {st}: {str(body)[:160]}")
    return st == 200


def set_ble(usb, enabled, passkey):
    b = api(usb, "/api/settings/ble_manager")[1]
    b["enabled"] = enabled
    b["passkey"] = passkey
    put_doc(usb, "ble_manager", b)


def set_wifi_mode(usb, mode):
    w = api(usb, "/api/settings/wifi_manager")[1]
    w["mode"] = mode
    put_doc(usb, "wifi_manager", w)


def submit_reboot(usb):
    try:
        api(usb, "/api/settings/submit", "POST")
    except Exception:
        pass


def save_found(usb):
    found = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wifi_manager": api(usb, "/api/settings/wifi_manager")[1],
        "ble_manager": api(usb, "/api/settings/ble_manager")[1],
    }
    if (os.path.exists(FOUND) and found["wifi_manager"].get("mode") == "off"
            and found["ble_manager"].get("enabled")):
        # a run that starts on a DUT already in the bench profile (the
        # earlier run never restored: a crash, a stopped stage) keeps the
        # documents that run found, so --restore still puts the real
        # ones back
        print(f"setup: the DUT is already in the bench profile; keeping "
              f"the found documents of the earlier run in {FOUND}")
        return
    os.makedirs(os.path.dirname(FOUND), exist_ok=True)
    with open(FOUND, "w") as f:
        json.dump(found, f, indent=1)
    print(f"setup: found documents saved (wifi mode "
          f"{found['wifi_manager'].get('mode')}, BLE "
          f"{'on' if found['ble_manager'].get('enabled') else 'off'}) -> {FOUND}")


def restore(usb):
    if os.path.exists(FOUND):
        found = json.load(open(FOUND))
        w, b = found["wifi_manager"], found["ble_manager"]
        ok = put_doc(usb, "wifi_manager", w) and put_doc(usb, "ble_manager", b)
        print(f"restoring: the found documents of {found.get('saved_at')} "
              f"(wifi mode {w.get('mode')}, BLE "
              f"{'on' if b.get('enabled') else 'off'}, passkey {b.get('passkey')})"
              + ("" if ok else " [a PUT failed]"))
    else:
        # the pre-2026-11-01 behaviour, for a run without --setup
        set_wifi_mode(usb, "apsta")
        set_ble(usb, False, 123456)
        print("restoring: no found documents; WiFi apsta + BLE off")
    submit_reboot(usb)
    print("  rebooting...")
    wait_dut(usb)


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


# ---- the firmware's side of the bond write (2026-11-01) -------------------

def host_task_row(usb):
    try:
        st, body = api(usb, "/api/status/tasks")
    except Exception:
        return None
    if st != 200 or not isinstance(body, dict):
        return None
    return next((t for t in body.get("tasks", [])
                 if t.get("name") == "nimble_host"), None)


def flash_jobs(usb):
    try:
        st, body = api(usb, "/api/status")
    except Exception:
        return {}
    if st != 200 or not isinstance(body, dict):
        return {}
    return body.get("health", {}).get("flash_jobs", {}) or {}


def check_host_task(usb, when):
    row = host_task_row(usb)
    check(f"nimble_host on a PSRAM stack ({when})",
          row is not None and row.get("stack_ext") is True,
          f"stack_ext {row.get('stack_ext')} stack_hw {row.get('stack_hw')}"
          if row else "row not found")
    if row is not None:
        hw = row.get("stack_hw", 0)
        check(f"nimble_host headroom >= 1 KB ({when})", hw >= 1024,
              f"stack_hw {hw} B")


def check_worker(usb, before, when):
    after = flash_jobs(usb)
    grew = after.get("marshalled", 0) - before.get("marshalled", 0)
    check(f"bond written through the flash worker ({when})", grew >= 1,
          f"marshalled {before.get('marshalled')} -> {after.get('marshalled')}, "
          f"spawns {after.get('spawns')}, stack_hw_min {after.get('stack_hw_min')}")
    check(f"no flash job refused ({when})", after.get("refusals", 0) == 0,
          f"refusals {after.get('refusals')}")
    return after


def check_ring(usb):
    try:
        st, txt = api(usb, "/api/logs/ring", raw=True)
    except Exception as e:
        check("log ring readable", False, str(e))
        return
    if st != 200:
        check("log ring readable", False, f"status {st}")
        return
    errs = [l for l in txt.splitlines() if "E (" in l]
    own = [l for l in errs if any(t in l for t in OWN_TAGS)]
    check("no E line from the Bluetooth tags", not own,
          f"{len(errs)} E lines total, {len(own)} own"
          + (f": {own[0][:120]}" if own else ""))
    other = [l for l in errs if l not in own]
    if other:
        print(f"  WARN: {len(other)} E line(s) of other tags in the ring "
              f"(WiFi is off for this bench): {other[0][:120]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", default="192.168.82.1")
    ap.add_argument("--bench", default="rpi001")
    ap.add_argument("--passkey", type=int, default=421337)
    ap.add_argument("--setup", action="store_true",
                    help="save the found documents, then WiFi off / BLE on")
    ap.add_argument("--restore", action="store_true",
                    help="put the found documents back, then exit")
    args = ap.parse_args()

    if args.restore:
        restore(args.usb)
        sys.exit(0)

    # push the probe
    subprocess.run(["scp", "-q", os.path.join(HERE, "ble_bond_probe.py"),
                    f"{args.bench}:/tmp/ble_bond_probe.py"], check=True)

    if args.setup:
        save_found(args.usb)
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
    check_host_task(args.usb, "before the pairing")

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
    fj0 = flash_jobs(args.usb)
    r = probe(args.bench, addr, args.passkey, "pair")
    check("pair: correct passkey pairs + gated Device-Info read",
          "ok=True" in r, r.strip())
    check_worker(args.usb, fj0, "the pairing")
    check_host_task(args.usb, "after the pairing")

    # 3) PERSIST: reboot the DUT, reconnect with NO re-pair
    api(args.usb, "/api/restart", "POST")
    print("  rebooting DUT to test bond persistence...")
    time.sleep(14)
    if not wait_dut(args.usb):
        check("DUT came back after reboot", False)
        sys.exit(1)
    time.sleep(5)
    fj1 = flash_jobs(args.usb)
    addr2 = scan_addr(args.bench) or addr
    r = probe(args.bench, addr2, args.passkey, "reconnect")
    check("persist: bond survives reboot, reconnect w/o passkey",
          "bond_persisted=True" in r, r.strip())
    # a bonded peer's CCCD subscription is a store write too
    fj2 = flash_jobs(args.usb)
    print(f"  flash worker after the reconnect: marshalled "
          f"{fj1.get('marshalled')} -> {fj2.get('marshalled')}, refusals "
          f"{fj2.get('refusals')}, stack_hw_min {fj2.get('stack_hw_min')}")
    check("no flash job refused (the reconnect)", fj2.get("refusals", 0) == 0,
          f"refusals {fj2.get('refusals')}")
    check_host_task(args.usb, "after the reconnect")
    check_ring(args.usb)

    if fails:
        print("BLE SECURITY FAIL:", ", ".join(fails))
        sys.exit(1)
    print("BLE SECURITY PASS")


if __name__ == "__main__":
    main()
