#!/usr/bin/env python3
"""The bench device's settings for an app session, switched and put back.

The app tests (testing/app_session_capture.md) need the device in one of a
few shapes; each switch is a settings submit (a reboot). This tool keeps
the as-found documents in a state file and restores exactly those.

  python tools/testbench/ble/app_session_profile.py --via rpi001:10.42.1.75 snapshot
  python tools/testbench/ble/app_session_profile.py --via rpi001:10.42.1.75 ble-on
         [--conn-profile ios|android_fast] [--autopid on|off] [--handover on|off]
  python tools/testbench/ble/app_session_profile.py --dut 192.168.82.1 realdash
  python tools/testbench/ble/app_session_profile.py --dut 192.168.82.1 elm
  python tools/testbench/ble/app_session_profile.py --dut 192.168.82.1 show
  python tools/testbench/ble/app_session_profile.py --dut 192.168.82.1 restore --via rpi001:10.42.1.75

Shapes:
  snapshot  save ble_manager, bridge_manager, can_manager, usb_host_manager,
            interface_manager, autopid and socket_manager as found (refuses
            to overwrite an existing state file: the first snapshot is the
            as-found one)
  ble-on    Bluetooth on (+ the connection profile asked), the USB connector
            in the device role (the NCM link at 192.168.82.1: the witness
            that stays up while a Bluetooth client holds the link and the
            station is suspended); optionally AutoPID and the handover rule
  realdash  the CAN controller on at 500 kbit/s and the RealDash framing on
            the Bluetooth data pipe and on TCP 35000 (`br_ble_can` and
            `br_tcp_can` take the place of `br_ble_obd` and `br_tcp_obd`):
            RealDash's CAN connection; the dash player on the PC's PCAN
            feeds the bus (tools/testbench/can/dash_player.py)
  elm       the factory bridges back (the OBD chip on Bluetooth, TCP 35000,
            USB and the web terminal), the CAN controller as found
  show      the live documents
  restore   every snapshot document put back (the USB role returns to host,
            so the NCM link goes away: --via names the station path the tool
            waits on afterwards), then a diff against the snapshot

--dut HOST talks to the device directly (the NCM link); --via PI:IP runs
curl on the bench Pi against the station address (the only way in while
the connector is in the host role). Either may be given; --dut is tried
first for a command, --via when --dut is absent or does not answer.
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
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
STATE_DEFAULT = os.path.join(REPO, "test-reports", "logs", "app_sessions", "as_found.json")
DOCS = ("ble_manager", "bridge_manager", "can_manager", "usb_host_manager",
        "interface_manager", "autopid", "socket_manager")
NCM = "192.168.82.1"


class Dev:
    def __init__(self, dut, via):
        self.dut = dut
        self.pi, self.sta = (via.split(":", 1) + [""])[:2] if via else ("", "")

    def _direct(self, host, path, method, body, timeout):
        req = urllib.request.Request(f"http://{host}{path}", method=method)
        data = None
        if body is not None:
            req.add_header("Content-Type", "application/json")
            data = json.dumps(body).encode()
        try:
            with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
                blob = r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return 0, str(e)
        return 200, blob.decode(errors="replace")

    def _via(self, path, method, body, timeout):
        cmd = (f"curl -s -m {int(timeout)} -o /dev/stdout -w '\\n%{{http_code}}' -X {method} "
               f"-H 'Content-Type: application/json' "
               + ("--data-binary @- " if body is not None else "")
               + f"http://{self.sta}{path}")
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", self.pi, cmd],
                           input=json.dumps(body) if body is not None else None,
                           capture_output=True, text=True, timeout=timeout + 15)
        out = p.stdout.rstrip("\n")
        if "\n" not in out:
            return 0, out + p.stderr
        text, code = out.rsplit("\n", 1)
        try:
            return int(code), text
        except ValueError:
            return 0, out

    def api(self, path, method="GET", body=None, timeout=8):
        """(status, text); status 0 = no answer on any path."""
        if self.dut:
            st, text = self._direct(self.dut, path, method, body, timeout)
            if st:
                return st, text
        if self.pi and self.sta:
            return self._via(path, method, body, timeout)
        return 0, "no path to the device"

    def get_json(self, path, timeout=8):
        st, text = self.api(path, timeout=timeout)
        if st != 200:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None


def clean(doc):
    doc = dict(doc)
    for k in ("degraded", "pending_reboot"):
        doc.pop(k, None)
    return doc


def put(dev, name, doc):
    st, text = dev.api(f"/api/settings/{name}", "PUT", clean(doc), timeout=15)
    if st != 200:
        print(f"PUT {name} failed: {st} {text[:300]}")
        sys.exit(2)
    print(f"  PUT {name}: {text[:120]}")


def submit(dev):
    st, text = dev.api("/api/settings/submit", "POST", timeout=15)
    print(f"  submit: {st} {text[:100]}")
    return st == 200 and '"reboot":true' in text.replace(" ", "")


def wait_host(host, secs, what):
    """Two consecutive answers from host (the NCM link re-enumerates)."""
    print(f"  waiting for {what} at {host} (up to {secs} s)...", flush=True)
    deadline = time.time() + secs
    ok = 0
    while time.time() < deadline:
        req = urllib.request.Request(f"http://{host}/api/status")
        try:
            with urllib.request.urlopen(req, timeout=4) as r:
                body = json.loads(r.read())
            ok += 1
            if ok >= 2:
                print(f"  {what} up: boot #{body.get('boot_count')} uptime {body.get('uptime')}, "
                      f"ble_enabled={body.get('bits', {}).get('ble_enabled')}")
                return True
        except Exception:  # noqa: BLE001
            ok = 0
        time.sleep(2)
    print(f"  {what} did not answer within {secs} s")
    return False


def wait_via(dev, secs):
    print(f"  waiting for the station path through {dev.pi} at {dev.sta} (up to {secs} s)...",
          flush=True)
    deadline = time.time() + secs
    while time.time() < deadline:
        st, text = dev._via("/api/status", "GET", None, 5)
        if st == 200:
            try:
                body = json.loads(text)
                print(f"  station path up: boot #{body.get('boot_count')} uptime "
                      f"{body.get('uptime')}")
                return True
            except ValueError:
                pass
        time.sleep(3)
    print(f"  the station path did not answer within {secs} s")
    return False


def load_state(path):
    if not os.path.exists(path):
        print(f"no state file {path}: run `snapshot` first")
        sys.exit(2)
    return json.load(open(path, encoding="utf-8"))


def realdash_bridges(found):
    rows = []
    for r in found["bridge_manager"]["bridges"]:
        if r["name"] == "br_ble_obd":
            rows.append({"name": "br_ble_can", "a": "can", "b": "ble",
                         "translator": "realdash", "enabled": True})
        elif r["name"] == "br_tcp_obd":
            rows.append({"name": "br_tcp_can", "a": "can", "b": "obd0",
                         "translator": "realdash", "enabled": True})
        else:
            rows.append(r)
    return {"bridges": rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("snapshot", "ble-on", "realdash", "elm", "show", "restore"))
    ap.add_argument("--dut", default="", help=f"the device's direct address (the NCM link {NCM})")
    ap.add_argument("--via", default="", help="PI:STA_IP, curl on the bench Pi")
    ap.add_argument("--state", default=STATE_DEFAULT)
    ap.add_argument("--conn-profile", choices=("ios", "android_fast"), default="")
    ap.add_argument("--autopid", choices=("on", "off"), default="")
    ap.add_argument("--handover", choices=("on", "off"), default="")
    ap.add_argument("--wait", type=int, default=180)
    a = ap.parse_args()
    dev = Dev(a.dut, a.via)

    if a.cmd == "show":
        for name in DOCS:
            doc = dev.get_json(f"/api/settings/{name}")
            print(name, json.dumps(clean(doc)) if doc else "(no answer)")
        info = dev.get_json("/api/info") or {}
        print("fw", info.get("fw_build"), "device", info.get("device_id"))
        return 0

    if a.cmd == "snapshot":
        if os.path.exists(a.state):
            print(f"state file exists, kept as the as-found record: {a.state}")
            return 0
        found = {}
        for name in DOCS:
            doc = dev.get_json(f"/api/settings/{name}")
            if doc is None:
                print(f"GET {name}: no answer; nothing saved")
                return 2
            found[name] = clean(doc)
        info = dev.get_json("/api/info") or {}
        found["_info"] = {"fw_build": info.get("fw_build"), "device_id": info.get("device_id"),
                          "taken": time.strftime("%Y-%m-%d %H:%M:%S")}
        os.makedirs(os.path.dirname(a.state), exist_ok=True)
        json.dump(found, open(a.state, "w", encoding="utf-8"), indent=1)
        print(f"as-found documents saved: {a.state}")
        for name in DOCS:
            print(" ", name, json.dumps(found[name])[:160])
        return 0

    found = load_state(a.state)

    if a.cmd == "ble-on":
        ble = dev.get_json("/api/settings/ble_manager")
        usb = dev.get_json("/api/settings/usb_host_manager")
        if ble is None or usb is None:
            print("the device did not answer")
            return 2
        ble["enabled"] = True
        if a.conn_profile:
            ble["conn_profile"] = a.conn_profile
        put(dev, "ble_manager", ble)
        usb["enabled"] = True
        usb["role"] = "device"
        usb["device_class"] = "ncm"
        put(dev, "usb_host_manager", usb)
        if a.autopid:
            apd = dev.get_json("/api/settings/autopid")
            apd["enabled"] = a.autopid == "on"
            put(dev, "autopid", apd)
        if a.handover:
            im = dev.get_json("/api/settings/interface_manager")
            im["sta_ble_handover"] = a.handover == "on"
            put(dev, "interface_manager", im)
        if submit(dev):
            time.sleep(10)
            ok = wait_host(NCM, a.wait, "the USB link")
            if dev.pi:
                wait_via(dev, 60)
            return 0 if ok else 3
        print("  nothing changed (already in that shape)")
        return 0

    if a.cmd in ("realdash", "elm"):
        can = dev.get_json("/api/settings/can_manager")
        if can is None:
            print("the device did not answer")
            return 2
        if a.cmd == "realdash":
            can["enabled"] = True
            can["baud"] = "500"
            can["silent"] = False
            put(dev, "can_manager", can)
            put(dev, "bridge_manager", realdash_bridges(found))
        else:
            put(dev, "can_manager", found["can_manager"])
            put(dev, "bridge_manager", found["bridge_manager"])
        if submit(dev):
            time.sleep(10)
            host = a.dut or NCM
            ok = wait_host(host, a.wait, "the device")
            br = dev.get_json("/api/bridges") or {}
            for b in br.get("bridges", []):
                print(f"  bridge {b['name']}: {b['a']} <-{b['translator']}-> {b['b']} "
                      f"enabled={b['enabled']} up={b['up']}"
                      + (f" reason={b['reason']}" if b.get("reason") else ""))
            return 0 if ok else 3
        print("  nothing changed (already in that shape)")
        return 0

    if a.cmd == "restore":
        for name in DOCS:
            put(dev, name, found[name])
        if submit(dev):
            time.sleep(10)
            if dev.pi and dev.sta:
                wait_via(dev, a.wait)
            else:
                print("  (no --via: not waiting; the NCM link is gone with the host role)")
        else:
            print("  nothing changed (already as found)")
        # the diff, over whatever path answers now
        dev2 = Dev("" if found["usb_host_manager"].get("role") != "device" else a.dut, a.via)
        bad = 0
        for name in DOCS:
            live = dev2.get_json(f"/api/settings/{name}")
            if live is None:
                print(f"  {name}: no answer for the diff")
                bad += 1
                continue
            live = clean(live)
            if live != found[name]:
                diff = {k: (found[name].get(k), live.get(k)) for k in set(live) | set(found[name])
                        if found[name].get(k) != live.get(k)}
                print(f"  {name}: DIFFERS {diff}")
                bad += 1
            else:
                print(f"  {name}: as found")
        print("APP SESSION PROFILE RESTORE " + ("PASS" if bad == 0 else f"FAIL ({bad} documents)"))
        return 0 if bad == 0 else 4


if __name__ == "__main__":
    sys.exit(main())
