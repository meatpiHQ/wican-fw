#!/usr/bin/env python3
"""Shared BLE bench plumbing for the stream-channel benches (runs ON rpi001).

What the two channel benches (ble_http_pi.py, ble_j2534_pi.py) share:
the DUT HTTP helper, the BT identity-address rule, adapter/bond hygiene,
a connect-and-pair routine that survives the RPA/bond gotchas recorded in
TESTING.md, and `Stream`: one notify + one write characteristic turned
into a byte stream with `read_exact` / `write` (MTU-chunked), which is
exactly what the FFF3..FFF6 channels are (BLE_API.md 4b).

Usage from a bench script:
    from ble_link import *
    prep_adapter(identity, nm_profile)
    client, mtu = await connect(name, identity, passkey)
    s = Stream(client, UUID_HTTP_OUT, UUID_HTTP_IN, mtu); await s.start()
"""
import asyncio
import json
import subprocess
import time
import urllib.error
import urllib.request

from bleak import BleakClient, BleakScanner

from ble_bench import start_passkey_agent  # the D-Bus KeyboardOnly agent


def uuid16(v):
    return f"0000{v:04x}-0000-1000-8000-00805f9b34fb"


UUID_HTTP_OUT = uuid16(0xFFF3)    # notify: device -> app (ble_http)
UUID_HTTP_IN = uuid16(0xFFF4)     # write:  app -> device
UUID_J2534_OUT = uuid16(0xFFF5)   # notify: device -> tester (ble_j2534)
UUID_J2534_IN = uuid16(0xFFF6)    # write:  tester -> device
UUID_MANUFACTURER = uuid16(0x2A29)

E_WHITELIST = ("wifi:Invalid MMIE", "select() timeout",
               "Failed to open a new connection", "Connection failed, sock < 0",
               "tcp_read error", "delayed connect error")


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          errors="replace").stdout


def api(ip, path, method="GET", body=None, timeout=8, raw=False, ct=None):
    """(status, body). body is parsed JSON when it parses, else text.
    status 0 = no answer (connection error)."""
    req = urllib.request.Request(f"http://{ip}{path}", method=method)
    data = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
            req.add_header("Content-Type", ct or "application/octet-stream")
        else:
            data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            blob = r.read()
            if raw:
                return r.status, blob
            try:
                return r.status, json.loads(blob or b"{}")
            except ValueError:
                return r.status, blob.decode(errors="replace")
    except urllib.error.HTTPError as e:
        blob = e.read()
        try:
            return e.code, json.loads(blob or b"{}")
        except ValueError:
            return e.code, blob.decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def clean(cfg):
    for k in ("degraded", "pending_reboot"):
        cfg.pop(k, None)
    return cfg


def find_dut(hint, device_id, window=180):
    """Probe the hint, then mDNS and the hotspot neighbours."""
    t0 = time.time()
    while time.time() - t0 < window:
        cands = [hint] if hint else []
        if device_id:
            m = sh(f"getent hosts wican_{device_id}.local").split()
            if m:
                cands.append(m[0])
        for dev in ("wtest0", "wint0", "wtest1"):
            cands += sh(f"ip neigh show dev {dev} | awk '{{print $1}}'").split()
        # the DUT's own AP (192.168.0.x via wtest1) LAST: the BLE legs take
        # the Pi off that AP (ap_ble_exclusive), so a witness must use the
        # hotspot lease (2026-09-21: a post-reboot resolve landed on the AP
        # address and every witness call then failed "network unreachable")
        cands = sorted(dict.fromkeys(cands), key=lambda c: c.startswith("192.168.0."))
        for ip in cands:
            st, body = api(ip, "/api/status", timeout=3)
            if st == 200 and isinstance(body, dict) and body.get("version"):
                return ip, body
        time.sleep(3)
    return None, {}


def submit_and_wait(ip, device_id, boot_before, window=180):
    api(ip, "/api/settings/submit", "POST", timeout=8)
    time.sleep(12)
    t0 = time.time()
    while time.time() - t0 < window:
        nip, st = find_dut(ip, device_id, window=20)
        if nip and (st.get("boot_count", 0) != boot_before or
                    st.get("uptime", "99:99:99") < "00:02:00"):
            return nip, st
        time.sleep(3)
    return None, {}


def bt_identity(sta_mac):
    """The DUT's BT identity address = the ESP base MAC + 2."""
    try:
        b = bytes.fromhex(sta_mac.replace(":", ""))
        n = (int.from_bytes(b, "big") + 2) & 0xFFFFFFFFFFFF
        return ":".join(f"{x:02X}" for x in n.to_bytes(6, "big"))
    except Exception:  # noqa: BLE001
        return ""


OWN_TAGS = ("ble_manager", "ble_http", "ble_j2534", "j2534_server", "NimBLE",
            "BLE_", "http_server_manager", "esp_http_client")


def ring_errors(ip):
    """(E lines from the tags under test, other non-whitelisted E lines,
    all E lines) from /api/logs/ring. The first list fails a bench; the
    second is reported (pre-existing bench-DUT noise such as the data
    logger's SD `disk I/O error` is not this feature's)."""
    st, txt = api(ip, "/api/logs/ring", timeout=15, raw=True)
    if st != 200:
        return None, None, None
    lines = txt.decode(errors="replace").splitlines()
    errs = [l for l in lines if "E (" in l]
    own = [l for l in errs if any(t in l for t in OWN_TAGS)
           and not any(w in l for w in E_WHITELIST)]
    other = [l for l in errs if l not in own and not any(w in l for w in E_WHITELIST)]
    return own, other, errs


def btmon_start(path):
    """HCI trace of the leg (decode with `btmon -r <path>`)."""
    subprocess.run(f"pkill -x btmon; rm -f {path}", shell=True)
    return subprocess.Popen(["btmon", "-w", path], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def btmon_summary(proc, path):
    """Stop the trace; return (notifications seen, attribute value bytes) of
    ATT Handle Value Notifications toward the central."""
    proc.terminate()
    time.sleep(0.5)
    txt = sh(f"btmon -r {path} 2>/dev/null")
    notifs = txt.count("ATT: Handle Value Notification") + txt.count("ATT: Handle Value Indication")
    errs = [l.strip() for l in txt.splitlines() if "Error" in l or "Disconnect Complete" in l]
    return notifs, errs[-6:]


def channel_stats(ip, name):
    """The device's view of one stream channel (GET /api/ble)."""
    st, body = api(ip, "/api/ble", timeout=5)
    if st != 200 or not isinstance(body, dict):
        return {}
    for c in body.get("channels", []):
        if c.get("name") == name:
            return c
    return {}


def prep_adapter(identity, nm_profile, drop_bond=False):
    """Bond hygiene + adapter power cycle + leave the DUT's AP (a station
    on the WiCAN's own AP stops BLE: interface_manager.ap_ble_exclusive).
    Returns True when the DUT's own bond is kept (connect by identity)."""
    bonded = False
    for line in sh("bluetoothctl devices").splitlines():
        if "WiC_" not in line:
            continue
        mac = line.split()[1]
        # BlueZ refreshes its cached GATT database (properties, handles)
        # only when the device is removed: after a firmware that changed
        # the table, run once with drop_bond=True (2026-09-21: the cache
        # still said FFF3 = notify and StartNotify failed NotSupported)
        if mac.upper() == identity and not drop_bond:
            bonded = "Paired: yes" in sh(f"bluetoothctl info {mac}")
            print(f"  (keeping the DUT's bond {mac}, paired={bonded})", flush=True)
            continue
        sh(f"bluetoothctl -- disconnect {mac}")
        sh(f"bluetoothctl remove {mac}")
        print(f"  (removed bond {mac})", flush=True)
    sh("bluetoothctl power off")
    time.sleep(2)
    sh("bluetoothctl power on")
    if nm_profile:
        print(f"  leaving the DUT's AP (nmcli con down {nm_profile})", flush=True)
        sh(f"nmcli con down {nm_profile}")
        time.sleep(6)
    return bonded


_agent_started = False


async def _find(name, identity, bonded, rssi_out):
    """Bonded: the identity address directly (a bonded DUT is invisible to a
    bleak scan). Else a fresh scan matched on the ADVERTISED local name."""
    if bonded:
        return identity
    for _ in range(3):
        found = await BleakScanner.discover(timeout=8.0, return_adv=True)
        for d, adv in found.values():
            if adv.local_name == name or d.name == name or \
                    (identity and d.address.upper() == identity):
                rssi_out.append(adv.rssi)
                return d
    return None


async def connect(name, identity, passkey, bonded=False, tries=6, want_mtu=517):
    """Connect + pair; returns (BleakClient, payload_cap). Raises on failure."""
    global _agent_started
    if not _agent_started:
        start_passkey_agent(passkey)  # one D-Bus agent per process
        _agent_started = True
    rssi = []
    last = ""
    for attempt in range(1, tries + 1):
        target = await _find(name, identity, bonded, rssi)
        if target is None:
            last = "no advertiser"
            print(f"  connect {attempt}/{tries}: {last}", flush=True)
            await asyncio.sleep(3)
            continue
        client = BleakClient(target, timeout=30.0)
        try:
            await client.connect()
            await client.pair()
            await asyncio.sleep(0.5)
            chars = [c.uuid for s in client.services for c in s.characteristics]
            if UUID_MANUFACTURER not in chars:
                # a fresh bond makes BlueZ re-resolve the services and bleak's
                # collection can be stale/empty: one reconnect (now bonded)
                print("  (services not resolved after pairing; reconnecting)", flush=True)
                await client.disconnect()
                await asyncio.sleep(2)
                client = BleakClient(target if bonded else identity or target, timeout=30.0)
                await client.connect()
                await client.pair()
                await asyncio.sleep(0.5)
                chars = [c.uuid for s in client.services for c in s.characteristics]
                if UUID_MANUFACTURER not in chars:
                    raise RuntimeError(f"services not resolved ({len(chars)} chars)")
            # BlueZ negotiates the MTU on its own; bleak learns it lazily
            mtu = 23
            try:
                await client._backend._acquire_mtu()  # noqa: SLF001
                mtu = client.mtu_size
            except Exception:  # noqa: BLE001
                mtu = getattr(client, "mtu_size", 23) or 23
            cap = max(20, min(490, mtu - 3))
            print(f"  connected {name} attempt {attempt}, rssi {rssi[-1] if rssi else '?'}, "
                  f"MTU {mtu} (payload {cap}), {len(chars)} characteristics", flush=True)
            return client, cap
        except Exception as e:  # noqa: BLE001
            last = (str(e) or type(e).__name__)[:120]
            print(f"  connect {attempt}/{tries} failed: {last}", flush=True)
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(3)
    raise RuntimeError(f"BLE connect failed: {last}")


class Stream:
    """A notify + write characteristic pair as a byte stream."""

    def __init__(self, client, uuid_out, uuid_in, cap):
        self.client = client
        self.uuid_out = uuid_out
        self.uuid_in = uuid_in
        self.cap = cap
        self.buf = bytearray()
        self.cond = asyncio.Condition()
        self.notifs = 0
        self.rx_bytes = 0
        self.tx_bytes = 0

    async def start(self):
        self.ev = asyncio.Event()

        def on_notify(_, data):
            # bleak (BlueZ) calls this on the event loop thread and may reuse
            # the bytearray: copy NOW and append in order, no task hop
            chunk = bytes(data)
            self.notifs += 1
            self.rx_bytes += len(chunk)
            self.buf.extend(chunk)
            self.ev.set()

        # BlueZ's default StartNotify path (D-Bus PropertiesChanged signals)
        # DROPPED whole 490 B notifications at ~8 KB/s on rpi001 (device
        # tx 65730 B vs client rx 63770 B with 0 device-side timeouts,
        # 2026-09-21); AcquireNotify hands us a socket and keeps every one.
        # Since 2026-09-22 the http channel (FFF3) offers notify AND
        # indicate; BlueZ picks notify when the property is there, so this
        # Stream runs in NOTIFY mode (the tunnel then owes CREDITs and checks
        # the v2 frame counter). AcquireNotify is NotSupported on an
        # indicate-only characteristic (FFF5): plain StartNotify then.
        try:
            await self.client.start_notify(self.uuid_out, on_notify,
                                           use_notify_acquire=True)
        except Exception:  # noqa: BLE001
            await self.client.start_notify(self.uuid_out, on_notify)
        await asyncio.sleep(0.2)

    async def stop(self):
        try:
            await self.client.stop_notify(self.uuid_out)
        except Exception:  # noqa: BLE001
            pass

    async def read_exact(self, n, timeout):
        end = time.monotonic() + timeout
        while len(self.buf) < n:
            left = end - time.monotonic()
            if left <= 0:
                raise asyncio.TimeoutError(f"read_exact({n}) have {len(self.buf)}")
            self.ev.clear()
            try:
                await asyncio.wait_for(self.ev.wait(), timeout=left)
            except asyncio.TimeoutError:
                continue
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def skip(self, n):
        del self.buf[:n]

    def pending(self):
        return len(self.buf)

    async def write(self, data, response=False):
        for i in range(0, len(data), self.cap):
            await self.client.write_gatt_char(self.uuid_in, data[i:i + self.cap],
                                              response=response)
        self.tx_bytes += len(data)
