#!/usr/bin/env python3
"""What does an app see from a v6 WiCAN over Bluetooth before it pairs?

Runs ON rpi001 (bleak on the UB500), shipped and started by ble_pi_run.py:

  python tools/testbench/ble/ble_pi_run.py ble_addr_probe_pi.py --dut 10.42.1.194 [--cycles 3]

The two field reports of 2026-11-02 (Discord): "the advertising id changes on
every connection attempt" and "writes to FFF2 are rejected with insufficient
encryption / authentication, and a write that goes through gets no
notification on FFF1". The probe measures, with the Pi's own bond removed
first so BlueZ cannot resolve a private address:

  1. the advertised address and its type (public / random) in a scan;
  2. N connect-without-pairing cycles, each followed by a scan: did the
     address change between connections?
  3. on an unpaired link: a Device Info read, a FFF2 write WITH response,
     a FFF2 write WITHOUT response (an ATT command gets no error reply:
     the device drops it silently), a FFF1 subscribe, and whether anything
     is notified within 3 s;
  4. a paired link (static passkey agent): FFF1 subscribed, "ATI\\r" to
     FFF2, and whether a reply comes within 5 s on a device WITHOUT a
     Bluetooth bridge (the v6 default: no bridge carries FFF1/FFF2 until
     one is added on the Connections page).

Prints one `RESULT ...` line per fact and, with `--expect public|private`
(2026-11-02, the fix), judges them: `BLE ADDR PROBE PASS` when the address
kept its kind and value across the cycles (public: one address, type public;
private: a new random one per cycle), the plain link's subscribe was refused
(the CCCD gate), the paired link's `ATI` was answered by the chip through a
`ble` bridge (the factory `br_ble_obd`), and the device log carries no E line
of its Bluetooth tags. Without `--expect` the probe only records (`BLE ADDR
PROBE DONE`). Leaves the DUT as found (Bluetooth setting restored, submit)
and the Pi bonded again (the pair leg re-bonds).
"""
import argparse
import asyncio
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

from bleak import BleakClient, BleakScanner

from ble_bench import start_passkey_agent, UUID_FFF1, UUID_FFF2, UUID_MANUFACTURER


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


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
    except Exception as e:
        return {"_error": str(e)}


def text(ip, path, timeout=15):
    """A text/plain route (the log ring) as a string, "" when the DUT is away."""
    try:
        with urllib.request.urlopen(f"http://{ip}{path}", timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def wait_dut(ip, seconds=90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if api(ip, "/api/status", timeout=5).get("version"):
            return True
        time.sleep(3)
    return False


def submit(ip):
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"http://{ip}/api/settings/submit", method="POST"),
            timeout=8).read()
    except Exception:
        pass
    time.sleep(8)
    return wait_dut(ip)


RESULTS = {}
FAILS = []


def result(key, value):
    RESULTS[key] = value
    print(f"RESULT {key}={value}", flush=True)


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" + (f"  ({detail})" if detail else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


async def scan(name_prefix, seconds=8.0):
    """(address, address_type, name) of the first WiC_ advertiser, or None."""
    for _ in range(3):
        found = await BleakScanner.discover(timeout=seconds, return_adv=True)
        for dev, adv in found.values():
            label = adv.local_name or dev.name or ""
            if label.startswith(name_prefix):
                props = (dev.details or {}).get("props", {}) if isinstance(dev.details, dict) else {}
                return dev.address, props.get("AddressType", "?"), label
    return None


async def connect(dev_addr):
    client = None
    for attempt in range(3):
        try:
            client = BleakClient(dev_addr, timeout=30.0)
            await client.connect()
            return client
        except Exception as e:
            print(f"  connect attempt {attempt + 1}/3: {e}", flush=True)
            try:
                await client.disconnect()
            except Exception:
                pass
            client = None
            await asyncio.sleep(4)
    return None


async def unpaired_leg(dev_addr):
    client = await connect(dev_addr)
    if client is None:
        result("unpaired_connect", "failed")
        return
    try:
        # the order every ELM app uses: subscribe the OUT pipe, then write
        # the IN pipe without response; the secured accesses come last so
        # the central's stack has no reason to pair before them
        rx = asyncio.Queue()

        def on_data(_, data):
            rx.put_nowait(bytes(data))

        try:
            await client.start_notify(UUID_FFF1, on_data)
            result("unpaired_fff1_subscribe", "ok")
        except Exception as e:
            result("unpaired_fff1_subscribe", f"error:{e}")

        try:
            await client.write_gatt_char(UUID_FFF2, b"ATI\r", response=False)
            result("unpaired_fff2_write_no_response", "accepted_by_client_stack")
        except Exception as e:
            result("unpaired_fff2_write_no_response", f"error:{e}")

        got = b""
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                got += await asyncio.wait_for(rx.get(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
        result("unpaired_fff1_notified_bytes", len(got))
        result("unpaired_still_connected", client.is_connected)

        try:
            await client.write_gatt_char(UUID_FFF2, b"ATI\r", response=True)
            result("unpaired_fff2_write_with_response", "ok")
        except Exception as e:
            result("unpaired_fff2_write_with_response", f"error:{e}")

        try:
            v = await client.read_gatt_char(UUID_MANUFACTURER)
            result("unpaired_devinfo_read", f"ok:{bytes(v)!r}")
        except Exception as e:
            result("unpaired_devinfo_read", f"error:{e}")

        try:
            await client.stop_notify(UUID_FFF1)
        except Exception:
            pass
    finally:
        await client.disconnect()
    await asyncio.sleep(2)


async def paired_leg(dev_addr, passkey):
    start_passkey_agent(passkey)
    client = await connect(dev_addr)
    if client is None:
        result("paired_connect", "failed")
        return
    try:
        try:
            await client.pair()
            result("pair", "ok")
        except Exception as e:
            result("pair", f"error:{e}")

        rx = asyncio.Queue()

        def on_data(_, data):
            rx.put_nowait(bytes(data))

        try:
            await client.start_notify(UUID_FFF1, on_data)
            result("paired_fff1_subscribe", "ok")
        except Exception as e:
            result("paired_fff1_subscribe", f"error:{e}")

        try:
            await client.write_gatt_char(UUID_FFF2, b"ATI\r", response=True)
            result("paired_fff2_write_with_response", "ok")
        except Exception as e:
            result("paired_fff2_write_with_response", f"error:{e}")

        got = b""
        deadline = time.time() + 5.0
        while time.time() < deadline and b">" not in got:
            try:
                got += await asyncio.wait_for(rx.get(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
        result("paired_fff1_reply", repr(got))
        try:
            await client.stop_notify(UUID_FFF1)
        except Exception:
            pass
    finally:
        await client.disconnect()
    await asyncio.sleep(2)


async def run(a):
    ip = a.dut
    st = api(ip, "/api/status")
    if not st.get("version"):
        print("FATAL: DUT unreachable", flush=True)
        return 1
    print(f"DUT {ip} firmware {st.get('version')}", flush=True)

    ble_before = api(ip, "/api/settings/ble_manager")
    for k in ("degraded", "pending_reboot"):
        ble_before.pop(k, None)
    passkey = int(ble_before.get("passkey", 123456))
    bridges = api(ip, "/api/settings/bridge_manager").get("bridges", [])
    result("dut_ble_bridges", [b["name"] for b in bridges if "ble" in (b.get("a"), b.get("b"))])

    # the Pi's own bond: gone, so BlueZ shows the on-air address
    for line in sh("bluetoothctl devices").splitlines():
        if "WiC_" in line:
            mac = line.split()[1]
            sh(f"bluetoothctl remove {mac}")
            print(f"  removed the Pi's bond {mac}", flush=True)

    if not ble_before.get("enabled"):
        ble = dict(ble_before)
        ble["enabled"] = True
        r = api(ip, "/api/settings/ble_manager", "PUT", ble)
        print(f"  Bluetooth on: {r}", flush=True)
        if not submit(ip):
            print("FATAL: DUT did not come back after the submit", flush=True)
            return 1
        time.sleep(5)

    types = []
    try:
        first = await scan("WiC_")
        if first is None:
            result("advertiser", "not seen")
            return 1
        addr, atype, name = first
        result("adv_name", name)
        result("adv_address_0", f"{addr} type={atype}")

        addrs = [addr]
        types = [atype]
        for i in range(a.cycles):
            await unpaired_leg(addr)
            nxt = await scan("WiC_")
            if nxt is None:
                result(f"adv_address_{i + 1}", "not seen")
                break
            addr, atype, _ = nxt
            result(f"adv_address_{i + 1}", f"{addr} type={atype}")
            addrs.append(addr)
            types.append(atype)
        result("distinct_addresses", len(set(addrs)))
        result("address_changed_per_connection", len(set(addrs)) == len(addrs) and len(addrs) > 1)

        await paired_leg(addr, passkey)
    finally:
        if not ble_before.get("enabled"):
            # the STA may be suspended for a moment after the BLE session
            wait_dut(ip, 120)
            r = api(ip, "/api/settings/ble_manager", "PUT", ble_before)
            print(f"  Bluetooth restored: {r}", flush=True)
            submit(ip)

    if not a.expect:
        print("BLE ADDR PROBE DONE", flush=True)
        return 0

    # ---- the verdict (the fix of 2026-11-02) ----------------------------------------
    if a.expect == "public":
        check("address_fixed_public", len(set(addrs)) == 1 and set(types) == {"public"},
              f"{len(set(addrs))} distinct, types {sorted(set(types))}")
        api_ble = api(ip, "/api/ble")
        check("api_address_matches_the_air",
              api_ble.get("address_type") == "public" and api_ble.get("address", "").upper() == addrs[0].upper(),
              f"api {api_ble.get('address_type')} {api_ble.get('address')} vs air {addrs[0]}")
    else:
        check("address_private_rotates", len(set(addrs)) == len(addrs) and len(addrs) > 1
              and set(types) == {"random"}, f"{len(set(addrs))} distinct of {len(addrs)}")
    check("plain_link_subscribe_refused",
          str(RESULTS.get("unpaired_fff1_subscribe", "")).startswith("error"),
          RESULTS.get("unpaired_fff1_subscribe"))
    check("paired_ati_answered_through_the_bridge",
          "ELM327" in str(RESULTS.get("paired_fff1_reply", "")),
          str(RESULTS.get("paired_fff1_reply"))[:60])
    ring = text(ip, "/api/logs/ring")
    e_lines = [ln for ln in ring.splitlines()
               if ln.startswith("\x1b[0;31mE") and ("ble_manager" in ln or "NimBLE" in ln)]
    check("no_bluetooth_E_lines", not e_lines, " | ".join(e_lines)[:200])
    if FAILS:
        print("BLE ADDR PROBE FAIL: " + ", ".join(FAILS), flush=True)
        return 1
    print("BLE ADDR PROBE PASS", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="10.42.1.194")
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--expect", choices=("public", "private"), default="",
                    help="judge the run against this address setting")
    a = ap.parse_args()
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
