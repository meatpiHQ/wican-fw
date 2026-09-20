#!/usr/bin/env python3
"""slcan over BLE: the can <-> ble bridge with the slcan translator, end to end.

Born 2026-09-20 from the meatpi-components PR #1 check ("22-byte SLCAN lines
truncated on FFF1/FFF2, first PDU dropped on a full BLE TX queue"). Neither
reproduced on the NimBLE build; this bench keeps that proof repeatable and
adds the overload + recovery leg the PR's queue change is about.

Runs ON rpi001 (UB500 dongle + bleak + the D-Bus passkey agent from
ble_bench.py). Stages (they share /tmp/ble_slcan_bridge.json):

  configure : BLE on + bridge br_ble_can (can <-slcan-> ble) next to the
              standing bridges (the can endpoint fans out) + wifi mode
              apsta -> ap so the STA is down for the BLE leg (--keep-sta
              leaves it: 2026-09-21 every BLE connect then died at the
              link layer, `Connection Failed to be Established (0x3e)` on
              the Pi / supervision timeout on the DUT, with the STA up on
              the hotspot beside the beaconing AP); submit-reboot. A
              baseline already in the state file is KEPT (re-runs never
              snapshot the bench's own config as "before").
  ble       : power-cycle the Pi adapter, leave the DUT's AP
              (interface_manager.ap_ble_exclusive stops BLE while a station
              sits on the WiCAN's own AP, and the Pi's wtest1 profile
              --nm-profile IS that station), scan on the ADVERTISED name,
              connect (6 tries, rescan each), pair, subscribe FFF1, write
              Lawicel lines to FFF2, record every notification:
              A  a 22-byte 0100 request written right after subscribe (no
                 settle delay) is answered by the ECU simulator in ONE
                 complete 22-byte t7E8 line
              B  20 sequential requests 150 ms apart: 20 answers, every
                 line 22 B, no notification split at 20 B
              C  a 27-byte 29-bit line is written (bus proof = the PCAN
                 companion), then a VIN 0902 request: the sim's first frame
                 arrives, the bench answers the flow control from 7E0 and
                 the two consecutive frames follow (the VIN decodes)
              D  with --flood: touch the marker the PCAN companion polls,
                 30 requests while it floods 0x123 at 1000 fps (REPORTED
                 not judged: monitor-all over BLE drops on a full TX queue
                 by design), then
              E  recovery: 5 requests ~8 s after the flood ended must be
                 answered again (a TX path that stays wedged after an
                 overload is the defect this leg is for)
  restore   : rejoin the AP, wait for the DUT, print the ring's ble_manager
              lines + the bridge's stats, put the saved settings back,
              submit-reboot, no new faults vs the saved baseline.

PC companion (proves the FFF2 writes reached the bus with DLC 8 and drives
the flood; PCAN_USBBUS2 sees the DUT/sim bus). Start it BEFORE the ble stage:
  python tools/testbench/can/pcan_watch_flood.py --secs 240 --marker rpi001

Usage (on rpi001; root does not see the user's bleak):
  sudo env PYTHONPATH=/home/meatpi/.local/lib/python3.11/site-packages \
      python3 -u ble_slcan_bridge_test.py --dut 192.168.0.10 [--flood]
  ... --stage configure|ble|restore     (one stage at a time)
Expected final line: BLE SLCAN BRIDGE PASS
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bleak import BleakClient, BleakScanner                      # noqa: E402
from ble_bench import start_passkey_agent, UUID_FFF1, UUID_FFF2  # noqa: E402

BRIDGE = {"name": "br_ble_can", "a": "can", "b": "ble",
          "translator": "slcan", "enabled": True}
STATE = "/tmp/ble_slcan_bridge.json"
BTMON_LOG = "/tmp/ble_slcan_bridge_btmon.log"
FLOOD_MARKER = "/tmp/ble_slcan_flood_go"     # the PCAN companion polls this
FLOOD_SECS = 24                               # must match the companion
VIN_EXPECTED = b"1WCAN0FW0P0000001"           # the ECU simulator's fixed VIN

results = []
warns = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f"  ({detail})" if detail else ""), flush=True)
    results.append((name, ok))


def warn(name, ok, detail=""):
    """Informational: printed, never fails the bench (sim-side answers)."""
    print(("PASS" if ok else "WARN") + f": {name}" +
          (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        warns.append(name)


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          errors="replace").stdout


def api(ip, path, method="GET", body=None, timeout=8):
    req = urllib.request.Request(f"http://{ip}{path}", method=method)
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            raw = r.read() or b"{}"
            try:
                return json.loads(raw)
            except ValueError:
                return {"_raw": raw.decode(errors="replace")}
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode(errors="replace")}
    except Exception as e:
        return {"_error": str(e)}


def put_ok(r):
    return "_status" not in r and "_error" not in r


def clean(cfg):
    for k in ("degraded", "pending_reboot"):
        cfg.pop(k, None)
    return cfg


def find_dut(hint, device_id, window=180):
    """Probe the hint, then the mDNS name and the hotspot/AP neighbours."""
    t0 = time.time()
    while time.time() - t0 < window:
        cands = [hint] if hint else []
        if device_id:
            m = sh(f"getent hosts wican_{device_id}.local").split()
            if m:
                cands.append(m[0])
        for dev in ("wtest1", "wtest0", "wint0"):
            cands += sh(f"ip neigh show dev {dev} | awk '{{print $1}}'").split()
        for ip in dict.fromkeys(cands):
            st = api(ip, "/api/status", timeout=3)
            if st.get("version"):
                return ip, st
        time.sleep(3)
    return None, {}


def submit_and_wait(ip, device_id, boot_before):
    api(ip, "/api/settings/submit", "POST", timeout=8)
    time.sleep(12)
    t0 = time.time()
    while time.time() - t0 < 180:
        nip, st = find_dut(ip, device_id, window=20)
        if nip and (st.get("boot_count", 0) != boot_before or
                    st.get("uptime", "99:99:99") < "00:02:00"):
            return nip, st
        time.sleep(3)
    return None, {}


def load_state():
    with open(STATE) as f:
        return json.load(f)


# ------------------------------------------------------------------ configure
def stage_configure(ip, keep_sta):
    st = api(ip, "/api/status")
    info = api(ip, "/api/info")
    device_id = info.get("device_id", "")

    # the baseline: taken once; a re-run after a broken restore keeps it
    saved = load_state() if os.path.exists(STATE) else {}
    if saved.get("device_id") == device_id and "ble" in saved:
        print(f"  (baseline kept from {STATE}: ble/bridges/wifi as first seen)", flush=True)
    else:
        saved = {"device_id": device_id, "ip": ip, "sta_mac": info.get("mac", ""),
                 "ble": clean(api(ip, "/api/settings/ble_manager")),
                 "bm": clean(api(ip, "/api/settings/bridge_manager")),
                 "faults": api(ip, "/api/faults").get("faults", [])}
    if "wifi" not in saved:
        saved["wifi"] = clean(api(ip, "/api/settings/wifi_manager"))
    with open(STATE, "w") as f:
        json.dump(saved, f)
    print(f"DUT {ip} id {device_id} fw {st.get('version')} boot_count "
          f"{st.get('boot_count')} wifi mode {saved['wifi'].get('mode')}; saved {STATE}",
          flush=True)

    ble = dict(saved["ble"])
    ble["enabled"] = True
    check("configure_ble", put_ok(r := api(ip, "/api/settings/ble_manager", "PUT", ble)),
          str(r)[:120])

    bm = dict(saved["bm"])
    bridges = [b for b in bm.get("bridges", []) if b.get("name") != BRIDGE["name"]]
    check("bridge_slot_free", len(bridges) < 6, f"{len(bridges)} bridges")
    bridges.append(BRIDGE)
    bm["bridges"] = bridges
    check("configure_bridge", put_ok(r := api(ip, "/api/settings/bridge_manager", "PUT", bm)),
          str(r)[:160])

    if not keep_sta and saved["wifi"].get("mode") in ("apsta", "sta"):
        # GET redacts secrets to ""; a PUT with "" keeps the stored value
        # (HTTP_API.md), so the round trip is safe. AP-only = clean radio
        # for the BLE link; the AP keeps the DUT reachable from wtest1.
        wifi = dict(saved["wifi"])
        wifi["mode"] = "ap"
        check("configure_wifi_ap_only",
              put_ok(r := api(ip, "/api/settings/wifi_manager", "PUT", wifi)), str(r)[:160])

    print("submit + reboot...", flush=True)
    nip, st = submit_and_wait(ip, device_id, st.get("boot_count"))
    check("dut_back_after_config", nip is not None,
          f"ip={nip} boot_count={st.get('boot_count')} uptime={st.get('uptime')}")
    if nip:
        check("ble_enabled_now",
              api(nip, "/api/settings/ble_manager").get("enabled") is True, "")
        bm_now = api(nip, "/api/settings/bridge_manager")
        check("bridge_present_now",
              any(b.get("name") == BRIDGE["name"] for b in bm_now.get("bridges", [])), "")
        ws = api(nip, "/api/wifi/status")
        print(f"  wifi now: mode {api(nip, '/api/settings/wifi_manager').get('mode')} "
              f"sta_connected {ws.get('sta_connected')} ap_started {ws.get('ap_started')}",
              flush=True)
    return nip


# ------------------------------------------------------------------ ble
def slcan_lines(buf):
    parts = bytes(buf).split(b"\r")
    return parts[:-1], parts[-1]


def req_line(pad5):
    """t 7DF 8 02 01 00 <5 pad bytes> CR = 22 bytes (mode 01 PID 00)."""
    return b"t7DF8020100" + pad5.hex().upper().encode() + b"\r"


def bt_identity(sta_mac):
    """The DUT's BT identity address: the ESP base MAC + 2 (BLE_INIT log
    `Bluetooth MAC: ..:3e` for STA ..:3c)."""
    try:
        b = bytes.fromhex(sta_mac.replace(":", ""))
        n = (int.from_bytes(b, "big") + 2) & 0xFFFFFFFFFFFF
        return ":".join(f"{x:02X}" for x in n.to_bytes(6, "big"))
    except Exception:
        return ""


async def ble_leg(device_id, sta_mac, passkey, nm_profile, flood):
    out = {"found": False, "attempts": 0, "rssi": None, "tests": {}, "notifs": [],
           "link_lost": None}
    name = f"WiC_{device_id}"
    identity = bt_identity(sta_mac)
    if os.path.exists(FLOOD_MARKER):
        os.remove(FLOOD_MARKER)

    # Keep the DUT's OWN bond (identity address): a reconnect then rides the
    # stored keys on both sides. Only bonds of OTHER WiC_ units are stale.
    bonded = False
    for line in sh("bluetoothctl devices").splitlines():
        if "WiC_" in line:
            mac = line.split()[1]
            if mac.upper() == identity:
                bonded = "Paired: yes" in sh(f"bluetoothctl info {mac}")
                print(f"  (keeping the DUT's bond {mac}, paired={bonded})", flush=True)
                continue
            sh(f"bluetoothctl remove {mac}")
            print(f"  (removed stale bond {mac})", flush=True)

    start_passkey_agent(passkey)

    # a known-good adapter state for every run (cheap; bonds live in bluetoothd)
    sh("bluetoothctl power off")
    await asyncio.sleep(2)
    sh("bluetoothctl power on")

    if nm_profile:
        print(f"  leaving the DUT's AP (nmcli con down {nm_profile})", flush=True)
        sh(f"nmcli con down {nm_profile}")
        await asyncio.sleep(6)      # DUT: "BLE restarted (AP station left)"

    async def find():
        """Bonded: connect to the IDENTITY address without a scan. BlueZ
        resolves the DUT's RPA to it and emits no RSSI change for a known
        device at a steady signal, so bleak's scan lists nothing although
        the HCI trace shows the advertisements (2026-09-21). Unbonded: a
        FRESH scan each time (BlueZ drops the RPA object when advertising
        stops), matched on the ADVERTISED local name: BLEDevice.name
        (BlueZ's cached Name) was empty while the scan response carried it."""
        if bonded:
            return identity
        for _ in range(3):
            found = await BleakScanner.discover(timeout=8.0, return_adv=True)
            for d, adv in found.values():
                if (adv.local_name == name or d.name == name or
                        (identity and d.address.upper() == identity)):
                    out["rssi"] = adv.rssi
                    print(f"  adv: {d.address} rssi {adv.rssi} name {adv.local_name!r}",
                          flush=True)
                    return d
        return None

    def addr_of(dev):
        return dev if isinstance(dev, str) else dev.address

    device = await find()
    if device is None:
        return out
    out["found"] = True
    print(f"  target {addr_of(device)}" + (" (stored bond)" if bonded else ""), flush=True)

    client = None
    for attempt in range(6):
        out["attempts"] = attempt + 1
        try:
            client = BleakClient(device, timeout=30.0)
            await client.connect()
            break
        except Exception as e:
            print(f"  connect attempt {attempt + 1}/6 @ {addr_of(device)}: {e!r}", flush=True)
            try:
                await client.disconnect()
            except Exception:
                pass
            client = None
            await asyncio.sleep(3)
            if bonded and attempt == 2:
                # three misses on the stored bond: drop it and pair fresh
                # (the DUT's REPEAT_PAIRING handler deletes its own copy)
                print("  dropping the Pi-side bond, re-pairing fresh", flush=True)
                sh(f"bluetoothctl remove {identity}")
                bonded = False
            device = await find() or device
    if client is None:
        return out

    try:
        try:
            await client.pair()
        except Exception as e:
            print(f"  pair(): {e} (bond may already exist)", flush=True)

        notifs = out["notifs"]          # (t, bytes)
        stream = bytearray()
        rx_event = asyncio.Event()

        def on_data(_, data):
            b = bytes(data)
            notifs.append((time.time(), b))
            stream.extend(b)
            rx_event.set()

        async def wait_lines(pred, secs):
            deadline = time.time() + secs
            while time.time() < deadline:
                if pred(slcan_lines(stream)[0]):
                    return True
                rx_event.clear()
                try:
                    await asyncio.wait_for(rx_event.wait(),
                                           timeout=max(0.05, deadline - time.time()))
                except asyncio.TimeoutError:
                    pass
            return False

        def lines_from(prefix):
            return [l for l in slcan_lines(stream)[0] if l.startswith(prefix)]

        async def write(line):
            await client.write_gatt_char(UUID_FFF2, line, response=False)

        def link_ok(leg):
            if client.is_connected:
                return True
            out["link_lost"] = leg
            print(f"  link lost after leg {leg}", flush=True)
            return False

        async def request_burst(tag, n, gap, wait):
            """n padded 0100 requests; returns the count of 7E8 answers."""
            stream.clear()
            n0 = len(notifs)
            t0 = time.time()
            for i in range(n):
                await write(req_line(bytes([tag, i, 0x44, 0x55, 0x66])))
                await asyncio.sleep(gap)
            await wait_lines(lambda ls: len([l for l in ls if l.startswith(b"t7E8")
                                            and b"4100" in l]) >= n, wait)
            secs = time.time() - t0
            lines, _ = slcan_lines(stream)
            return {"sent": n,
                    "answers_7E8": len([l for l in lines if l.startswith(b"t7E8") and b"4100" in l]),
                    "flood_lines": len([l for l in lines if l.startswith(b"t123")]),
                    "total_lines": len(lines),
                    "ble_bytes_per_s": round(sum(len(b) for _, b in notifs[n0:]) / secs),
                    "notifs": len(notifs) - n0,
                    "malformed_lines": len([l for l in lines if l[:1] in (b"t", b"T")
                                            and len(l) not in (21, 26)])}

        # ---- A: immediate request right after subscribe (no settle delay)
        await client.start_notify(UUID_FFF1, on_data)
        n0 = len(notifs)
        t_a = time.time()
        line_a = req_line(bytes([0xA1, 0xA2, 0xA3, 0xA4, 0xA5]))
        assert len(line_a) == 22
        await write(line_a)
        ok = await wait_lines(lambda ls: any(l.startswith(b"t7E8") for l in ls), 3.0)
        first = lines_from(b"t7E8")
        out["tests"]["A"] = {
            "answered": ok,
            "latency_ms": round((notifs[n0][0] - t_a) * 1000) if len(notifs) > n0 else None,
            "first_answer": first[0].decode(errors="replace") if first else None,
            "notif_sizes": [len(b) for _, b in notifs[n0:]][:12]}
        print("  A:", out["tests"]["A"], flush=True)
        if not link_ok("A"):
            return out

        # ---- B: 20 sequential requests, 150 ms apart, unique padding
        stream.clear()
        n0 = len(notifs)
        for i in range(20):
            await write(req_line(bytes([0xB0, i, 0x11, 0x22, 0x33])))
            await asyncio.sleep(0.15)
        await wait_lines(lambda ls: len([l for l in ls if l.startswith(b"t7E8")]) >= 20, 3.0)
        b_lines = lines_from(b"t7E8")
        sizes = [len(b) for _, b in notifs[n0:]]
        out["tests"]["B"] = {
            "sent": 20, "answers_7E8": len(b_lines),
            "answer_lens": sorted(set(len(l) + 1 for l in b_lines)),
            "notif_sizes_hist": {s: sizes.count(s) for s in sorted(set(sizes))}}
        print("  B:", out["tests"]["B"], flush=True)
        if not link_ok("B"):
            return out

        # ---- C: 27-byte 29-bit line (bus proof on the PC) + VIN multi-frame
        stream.clear()
        n0 = len(notifs)
        ext = b"T18DB33F18020100C1C2C3C4C5\r"      # functional OBD, 29-bit
        assert len(ext) == 27
        await write(ext)
        await asyncio.sleep(0.4)
        await write(b"t7DF80209020000000000\r")     # mode 09 PID 02 (VIN)
        ff = await wait_lines(lambda ls: any(l.startswith(b"t7E8810") for l in ls), 3.0)
        cfs = []
        if ff:
            await write(b"t7E083000000000000000\r")  # flow control from 7E0
            await wait_lines(lambda ls: len([l for l in ls if l.startswith(b"t7E882")]) >= 2, 3.0)
            cfs = lines_from(b"t7E882")
        c_lines = lines_from(b"t7E8")
        vin = b""
        if ff and len(cfs) >= 2:
            payload = bytes.fromhex(lines_from(b"t7E8810")[0][5:].decode())[2:] \
                + b"".join(bytes.fromhex(l[5:].decode())[1:] for l in cfs[:2])
            vin = payload[3:20]
        out["tests"]["C"] = {"first_frame": ff, "consecutive": len(cfs),
                             "vin": vin.decode(errors="replace"),
                             "lines": [l.decode(errors="replace") for l in c_lines[:5]],
                             "notif_sizes": [len(b) for _, b in notifs[n0:]][:12]}
        print("  C:", out["tests"]["C"], flush=True)
        if not link_ok("C"):
            return out

        # ---- D: requests during the PCAN flood, E: recovery after it
        if flood:
            open(FLOOD_MARKER, "w").close()
            t_go = time.time()
            print(f"  D: flood marker set ({FLOOD_MARKER}); companion polls it", flush=True)
            await asyncio.sleep(6.0)             # ssh poll (2 s) + flood ramp
            out["tests"]["D"] = await request_burst(0xD0, 30, 0.3, 1.0)
            print("  D:", out["tests"]["D"], flush=True)
            if not link_ok("D"):
                return out
            rest = (t_go + 4 + FLOOD_SECS + 8) - time.time()
            if rest > 0:
                await asyncio.sleep(rest)        # the flood is over, queue drained
            out["tests"]["E"] = await request_burst(0xE0, 5, 0.4, 2.0)
            print("  E:", out["tests"]["E"], flush=True)
            link_ok("E")

        await client.stop_notify(UUID_FFF1)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return out


def btmon_summary(path, n=24):
    """The HCI trace's security / link lines (why a connect failed)."""
    txt = sh(f"btmon -r {path}")
    keys = ("SMP: Pairing (Request|Response|Failed)", "Encryption Change", "Long Term Key",
            "Security Request", "Disconnect Complete", "Reason:", "LE Enhanced Connection",
            "Connection Update Complete", "Passkey", "Pairing Failed")
    import re
    pat = re.compile("|".join(keys))
    lines = [l.rstrip() for l in txt.splitlines() if pat.search(l)]
    return lines[-n:]


HCI_DBG = "/sys/kernel/debug/bluetooth/hci0"
SUPERVISION_10MS = 200     # 2 s, what phones use; BlueZ's default 42 = 420 ms


def le_param(name, value=None):
    """BlueZ LE connection parameter (debugfs, root). Returns the value."""
    if value is not None:
        sh(f"echo {value} > {HCI_DBG}/{name}")
    return sh(f"cat {HCI_DBG}/{name}").strip()


def stage_ble(nm_profile, flood, trace):
    saved = load_state()
    passkey = int(saved["ble"].get("passkey", 123456))
    mon = None
    if trace:
        mon = subprocess.Popen(["btmon", "-w", BTMON_LOG], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    # The DUT stops its WiFi AP/STA the moment a BLE client connects
    # (interface_manager); BlueZ's 420 ms supervision timeout does not ride
    # out that hiccup (2026-09-21: Connection Timeout 0x08 on 4 of 6 tries).
    sup_before = le_param("supervision_timeout")
    print(f"  BlueZ LE params: interval {le_param('conn_min_interval')}-"
          f"{le_param('conn_max_interval')} x1.25 ms, supervision "
          f"{sup_before} -> {le_param('supervision_timeout', SUPERVISION_10MS)} x10 ms",
          flush=True)
    try:
        res = asyncio.run(ble_leg(saved["device_id"], saved.get("sta_mac", ""), passkey,
                                  nm_profile, flood))
    except Exception as e:
        print(f"  ble_leg crashed: {e!r}", flush=True)
        res = {"found": False, "tests": {}, "notifs": []}
    finally:
        if sup_before.isdigit():
            le_param("supervision_timeout", sup_before)
        if nm_profile:
            print(f"  rejoining the DUT's AP (nmcli con up {nm_profile})", flush=True)
            print("   ", sh(f"nmcli con up {nm_profile} 2>&1").strip()[:120], flush=True)
        if mon:
            mon.terminate()
            try:
                mon.wait(timeout=5)
                print(f"  HCI trace {BTMON_LOG} (security / link lines):", flush=True)
                for l in btmon_summary(BTMON_LOG):
                    print("   ", l[:150], flush=True)
            except Exception as e:
                print(f"  (trace summary failed: {e!r})", flush=True)

    t = res.get("tests", {})
    print(f"  session: found={res.get('found')} rssi={res.get('rssi')} "
          f"connect_attempts={res.get('attempts')} link_lost={res.get('link_lost')}",
          flush=True)
    check("ble_found_and_connected", bool(t),
          f"attempts={res.get('attempts')} rssi={res.get('rssi')}")
    A = t.get("A", {})
    check("A_immediate_answered", bool(A.get("answered")),
          f"latency={A.get('latency_ms')} ms first={A.get('first_answer')} sizes={A.get('notif_sizes')}")
    fa = A.get("first_answer") or ""
    check("A_answer_full_22B", fa.startswith("t7E88") and len(fa) == 21, f"len={len(fa)}+CR")
    B = t.get("B", {})
    hist = B.get("notif_sizes_hist") or {}
    check("B_all_20_answered", B.get("answers_7E8") == 20, str(B))
    check("B_no_20B_split", B.get("answer_lens") == [22] and 20 not in hist, f"hist={hist}")
    C = t.get("C", {})
    check("C_vin_first_frame", bool(C.get("first_frame")), str(C.get("lines")))
    warn("C_vin_consecutive_frames", C.get("consecutive", 0) >= 2,
         f"consecutive={C.get('consecutive')} (sim-side flow control)")
    warn("C_vin_decoded", C.get("vin", "").encode() == VIN_EXPECTED, f"vin={C.get('vin')!r}")
    if flood:
        D = t.get("D", {})
        print("  D (reported, not judged):", D, flush=True)
        E = t.get("E", {})
        check("E_recovered_after_flood", E.get("answers_7E8", 0) >= 4, str(E))
    check("link_held", res.get("link_lost") is None, f"lost after {res.get('link_lost')}")
    with open("/tmp/ble_slcan_bridge_ble.json", "w") as f:
        json.dump({"rssi": res.get("rssi"), "attempts": res.get("attempts"), "tests": t,
                   "notif_sizes": [len(b) for _, b in res.get("notifs", [])]}, f)


# ------------------------------------------------------------------ restore
def stage_restore(ip):
    saved = load_state()
    device_id = saved["device_id"]
    print("waiting for the DUT after the BLE disconnect...", flush=True)
    time.sleep(8)
    nip, st = find_dut(ip, device_id, window=150)
    check("dut_reachable_after_ble", nip is not None, f"ip={nip} uptime={st.get('uptime')}")
    if nip is None:
        print(f"RESTORE SKIPPED: DUT unreachable; saved settings in {STATE}", flush=True)
        return
    ring = api(nip, "/api/logs/ring", timeout=15)
    txt = ring.get("_raw", "") if isinstance(ring, dict) else str(ring)
    hits = [l for l in txt.splitlines() if "ble_manager" in l or "MTU" in l]
    drops = [l for l in hits if "tx queue full" in l]
    print(f"  ring: {len(hits)} ble_manager lines, {len(drops)} 'tx queue full'"
          + (f" (last: {drops[-1].strip()[-40:]})" if drops else ""), flush=True)
    for l in [h for h in hits if "tx queue full" not in h][-8:]:
        print("   ", l.strip()[:140], flush=True)
    br = api(nip, "/api/bridges")
    mine = [b for b in br.get("bridges", []) if b.get("name") == BRIDGE["name"]]
    print("  br_ble_can stats:", json.dumps(mine[0].get("stats")) if mine else br, flush=True)

    check("restore_put_ble", put_ok(api(nip, "/api/settings/ble_manager", "PUT", saved["ble"])), "")
    check("restore_put_bridges", put_ok(api(nip, "/api/settings/bridge_manager", "PUT", saved["bm"])), "")
    if "wifi" in saved:
        check("restore_put_wifi", put_ok(api(nip, "/api/settings/wifi_manager", "PUT", saved["wifi"])), "")
    print("restoring; submit + reboot...", flush=True)
    nip2, st2 = submit_and_wait(nip, device_id, st.get("boot_count"))
    check("dut_back_after_restore", nip2 is not None, f"ip={nip2}")
    if nip2:
        check("ble_restored",
              api(nip2, "/api/settings/ble_manager").get("enabled") == saved["ble"].get("enabled"), "")
        bm_now = api(nip2, "/api/settings/bridge_manager")
        check("bridge_removed",
              not any(b.get("name") == BRIDGE["name"] for b in bm_now.get("bridges", [])), "")
        if "wifi" in saved:
            check("wifi_mode_restored",
                  api(nip2, "/api/settings/wifi_manager").get("mode") == saved["wifi"].get("mode"),
                  f"want {saved['wifi'].get('mode')}")
        before = {f["code"] for f in saved["faults"]}
        new = [f for f in api(nip2, "/api/faults").get("faults", []) if f["code"] not in before]
        check("no_new_faults", not new, str(new)[:200])
        st3 = api(nip2, "/api/status")
        check("no_unexpected_resets", st3.get("unexpected_resets", 0) == 0,
              f"unexpected_resets={st3.get('unexpected_resets')}")
        os.replace(STATE, STATE + ".done")   # the baseline is consumed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="192.168.0.10", help="DUT address hint")
    ap.add_argument("--stage", choices=["all", "configure", "ble", "restore"], default="all")
    ap.add_argument("--nm-profile", default="wican-bench-ap",
                    help="NetworkManager profile of the Pi radio sitting on the DUT's AP "
                         "(downed for the BLE leg); '' to skip")
    ap.add_argument("--flood", action="store_true",
                    help="legs D + E: the PCAN companion (--marker) floods when the "
                         "marker appears")
    ap.add_argument("--no-trace", action="store_true",
                    help="skip the btmon HCI trace of the BLE leg")
    ap.add_argument("--keep-sta", action="store_true",
                    help="leave the wifi mode alone (STA up beside BLE: the product "
                         "scenario, flaky link establishment on the bench)")
    a = ap.parse_args()

    ip = a.dut
    if a.stage in ("all", "configure"):
        ip, _ = find_dut(ip, None, window=30)
        if not ip:
            print("FATAL: DUT unreachable")
            return 1
        ip = stage_configure(ip, a.keep_sta) or ip
        print("PROGRESS configure done", flush=True)
    if a.stage in ("all", "ble"):
        stage_ble(a.nm_profile, a.flood, not a.no_trace)
        print("PROGRESS ble done", flush=True)
    if a.stage in ("all", "restore"):
        stage_restore(ip)
        print("PROGRESS restore done", flush=True)

    fails = [n for n, ok in results if not ok]
    if warns:
        print("WARN: " + ", ".join(warns), flush=True)
    print(("BLE SLCAN BRIDGE FAIL: " + ", ".join(fails)) if fails else "BLE SLCAN BRIDGE PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
