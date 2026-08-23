#!/usr/bin/env python3
"""OBD <-> BLE live bridge — the PRODUCTION path (a phone app talking
ELM327 over BLE), never live-tested before 2026-07-26. Runs ON rpi001
(UB500 dongle + bleak + the D-Bus passkey agent from ble_bench.py).

Flow:
  1. over WiFi: enable ble_manager + add the br_ble_obd bridge
     (obd <-> ble, raw) next to the shipped trio — the obd jack is
     multi_consumer, so TCP/WS/USB stay live; submit-reboot.
  2. bluetoothctl-remove any stale WiC_ bond (the BlueZ stale-LTK trap),
     scan, connect, pair (static passkey agent).
  3. subscribe FFF1 (data OUT), write FFF2 (data IN):
       ATI\r   -> ELM327 ...>        (the chip through the bridge)
       0100\r  -> 41 00 ...          (live ECU via the bench sim)
  4. disconnect, wait for the STA to resume (interface_manager suspends
     WiFi while a BLE client is attached), restore settings, faults clean.

Usage (on rpi001):  sudo python3 obd_ble_bridge_test.py [dut_ip]
Expected final line: OBD BLE BRIDGE PASS
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

from bleak import BleakClient, BleakScanner

from ble_bench import start_passkey_agent, UUID_FFF1, UUID_FFF2

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True,
                          text=True).stdout


def dut_ip_search():
    for i in range(30):
        for dev in ("wint0", "wtest0"):
            for ip in sh(f"ip neigh show dev {dev} | awk '{{print $1}}'"
                         ).split():
                try:
                    if api(ip, "/api/status").get("version"):
                        return ip
                except Exception:
                    pass
        if i % 4 == 3:                    # liveness during the wait
            print(f"  searching for the DUT… (~{(i + 1) * 3} s)",
                  flush=True)
        time.sleep(3)
    return None


def api(ip, path, method="GET", body=None, timeout=8):
    req = urllib.request.Request(f"http://{ip}{path}", method=method)
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()}


def submit_and_wait(ip):
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"http://{ip}/api/settings/submit",
                                   method="POST"), timeout=8).read()
    except Exception:
        pass
    time.sleep(8)
    return dut_ip_search()


async def ble_leg(passkey):
    """Returns dict of results from the BLE session."""
    out = {"found": False, "ati": b"", "p0100": b""}

    # stale-LTK trap (memory of 2026-07-1x): remove any known WiC_ bond
    for line in sh("bluetoothctl devices").splitlines():
        if "WiC_" in line:
            mac = line.split()[1]
            sh(f"bluetoothctl remove {mac}")
            print(f"  (removed stale bond {mac})")

    start_passkey_agent(passkey)

    device = None
    for _ in range(3):
        for d in await BleakScanner.discover(timeout=8.0):
            if d.name and d.name.startswith("WiC_"):
                device = d
                break
        if device:
            break

    if device is None:
        return out

    out["found"] = True
    print(f"  found {device.name} @ {device.address}")

    # connect with retries — BlueZ throws transient br-connection-canceled
    # on the first attempt after a fresh advertise (bitten 2026-07-26)
    client = None
    for attempt in range(3):
        try:
            client = BleakClient(device, timeout=30.0)
            await client.connect()
            break
        except Exception as e:
            print(f"  connect attempt {attempt + 1}/3: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            client = None
            await asyncio.sleep(5)

    if client is None:
        return out

    try:
        try:
            await client.pair()
        except Exception as e:
            print(f"  pair(): {e} (bond may already exist)")

        rx = asyncio.Queue()

        def on_data(_, data):
            rx.put_nowait(bytes(data))

        await client.start_notify(UUID_FFF1, on_data)

        async def transact(cmd, secs=6):
            buf = b""
            while not rx.empty():
                rx.get_nowait()
            await client.write_gatt_char(UUID_FFF2, cmd, response=False)
            deadline = time.time() + secs
            while time.time() < deadline and b">" not in buf:
                try:
                    buf += await asyncio.wait_for(rx.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            return buf

        out["ati"] = await transact(b"ATI\r")
        out["p0100"] = await transact(b"0100\r")
        await client.stop_notify(UUID_FFF1)
    finally:
        await client.disconnect()

    return out


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else dut_ip_search()
    print("DUT", ip)
    if not ip or not api(ip, "/api/status").get("version"):
        print("FATAL: DUT unreachable")
        return 1

    ble_before = api(ip, "/api/settings/ble_manager")
    bm_before = api(ip, "/api/settings/bridge_manager")
    for c in (ble_before, bm_before):
        for k in ("degraded", "pending_reboot"):
            c.pop(k, None)

    passkey = int(ble_before.get("passkey", 123456))

    # ---- 1. configure: BLE on + the production bridge ----
    ble = dict(ble_before)
    ble["enabled"] = True
    r = api(ip, "/api/settings/ble_manager", "PUT", ble)
    check("configure_ble", "_status" not in r, r)

    bm = dict(bm_before)
    bridges = [b for b in bm.get("bridges", [])
               if b.get("name") != "br_ble_obd"]
    # the standing bench config carries FIVE bridges (shipped trio +
    # two rig bridges, 2026-07-20) — the honest bound is a free
    # slot in the 6-cap table for br_ble_obd
    check("bridge_slot_free", len(bridges) < 6, f"{len(bridges)} bridges")
    bridges.append({"name": "br_ble_obd", "a": "obd", "b": "ble",
                    "translator": "raw", "enabled": True})
    bm["bridges"] = bridges
    r = api(ip, "/api/settings/bridge_manager", "PUT", bm)
    check("configure_bridge", "_status" not in r, r)

    print("rebooting into BLE+bridge config…")
    ip = submit_and_wait(ip)
    if ip is None:
        print("FATAL: DUT did not come back")
        return 1
    print("PROGRESS 1/4", flush=True)

    # ---- 2./3. the BLE session (WiFi suspends while connected) ----
    print("BLE leg: scan + pair + ATI/0100 over FFF1/FFF2…", flush=True)
    # a BLE-stack exception must never skip the restore path (a crashed
    # 2026-07-26 run left br_ble_obd + BLE enabled on the DUT)
    try:
        res = asyncio.run(ble_leg(passkey))
    except Exception as e:
        print(f"  ble_leg crashed: {e}")
        res = {"found": False, "ati": b"", "p0100": b""}
    check("ble_advertising_found", res["found"], "")
    check("ble_obd_ati", b"ELM327" in res["ati"], repr(res["ati"][:40]))
    check("ble_obd_0100_live_ecu", b"41 00" in res["p0100"],
          repr(res["p0100"][:40]))
    print("PROGRESS 2/4", flush=True)

    # ---- 4. restore (wait for the STA to resume first) ----
    print("waiting for WiFi to resume after BLE disconnect…")
    time.sleep(10)
    ip = dut_ip_search()
    check("wifi_resumed", ip is not None, f"dut_ip={ip}")
    print("PROGRESS 3/4", flush=True)

    if ip is not None:
        api(ip, "/api/settings/ble_manager", "PUT", ble_before)
        api(ip, "/api/settings/bridge_manager", "PUT", bm_before)
        print("restoring; rebooting…")
        ip = submit_and_wait(ip)
        f = api(ip, "/api/faults") if ip else {}
        check("restore_faults_clean", f.get("faults") == [], f)
    print("PROGRESS 4/4", flush=True)

    if fails:
        print("OBD BLE BRIDGE FAIL: " + ", ".join(fails))
        return 1
    print("OBD BLE BRIDGE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
