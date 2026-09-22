#!/usr/bin/env python3
"""The HTTP API over BLE (ble_http): storage, settings, UDS, errors, end to end.

Runs ON rpi001 (UB500 + bleak + the D-Bus passkey agent). Proves the
`http` stream channel (FFF3 notify / FFF4 write, BLE_HTTP_PROTOCOL.md):
framed HTTP requests from a paired central are replayed against the
device's own web server over loopback and the responses stream back, so a
phone app manages the device without WiFi. Stages (state in
/tmp/ble_http_bench.json; a baseline already there is KEPT across re-runs):

  configure : snapshot settings + faults; BLE on (passkey), ble_http on,
              interface_manager.sta_ble_handover OFF so the STA stays up as
              the HTTP witness during the BLE session (--wifi-off instead
              switches wifi mode to `ap` and skips the witness checks: the
              fallback for a rig where BLE will not hold beside WiFi);
              submit-reboot.
  ble       : bond hygiene, leave the DUT's AP, connect + pair, find
              FFF3/FFF4, then:
              L0 link gate: 2 extra connect/pair/disconnect cycles (a link
                 that does not hold prints BLE API BLOCKED: link and stops)
              L1 GET /api/status + /api/info (device_id matches)
              L2 settings: GET ble_http + schema, identical PUT round trip
              L3 storage on /data (and /sd when mounted): info vs the WiFi
                 witness, mkdir, upload 1 KB / 64 KB / 512 KB raw honouring
                 CREDIT, download byte-exact (v2: notifications + credits, the
                 frame counter proves no PDU was lost), SHA-256 cross-checked through
                 GET /api/fs/download over WiFi, list, delete, throughput
                 METRIC lines
              L4 errors: 403 path, 400 invalid path, 404, 429 busy,
                 concurrent WiFi upload (one side gets 503: reported)
              L5 disconnect mid-upload -> no torn file, reconnect, fresh
                 upload completes
              L6 UDS: POST /api/uds/request 22 F1 90 -> VIN, 10 02 -> 50 02,
                 /api/uds/session begin -> session_active -> end
              L7 GET /api/logs/ring through the tunnel (a chunked text body)
  restore   : settings back, submit-reboot, no new faults, 0 unexpected
              resets, 0 non-whitelisted E lines.

Usage (on rpi001; root does not see the user's bleak):
  sudo env PYTHONPATH=/home/meatpi/.local/lib/python3.11/site-packages \
      python3 -u ble_http_pi.py --dut 10.42.1.194 [--stage configure|ble|restore]
      [--wifi-off] [--passkey 421337] [--nm-profile wican-bench-ap]
Expected final lines: BLE STORAGE PASS then BLE API PASS
"""
import argparse
import asyncio
import hashlib
import json
import os
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ble_link import (UUID_HTTP_IN, UUID_HTTP_OUT, Stream, api, bt_identity,  # noqa: E402
                      btmon_start, btmon_summary, channel_stats, clean, connect,
                      find_dut, prep_adapter, ring_errors, submit_and_wait)

BTMON_LOG = "/tmp/ble_http_btmon.log"

STATE = "/tmp/ble_http_bench.json"
HDR = struct.Struct("<BBBBHH")   # magic, ver, type, flags, seq, len
MAGIC, VER = 0x57, 2             # v2 (2026-09-22): body-frame counter + credits both ways
T_REQ, T_REQ_BODY, T_RSP, T_RSP_BODY, T_ABORT, T_CREDIT = 1, 2, 3, 4, 5, 6
F_LAST = 1
CTR_SHIFT = 4                    # flags[7:4] = body-frame counter mod 16
FRAME_MAX = 4096
CREDIT_WINDOW = 16384            # upload: bytes we may have unacknowledged
CREDIT_STEP = 4096               # download (notify mode): CREDIT the device every 4 KB


def ctr_flags(ctr, last):
    return ((ctr & 0x0F) << CTR_SHIFT) | (F_LAST if last else 0)
DIR = "/blebench"
VIN = "31 57 43 41 4E 30 46 57 30 50 30 30 30 30 30 30 31"  # 1WCAN0FW0P0000001

results = []
storage_results = []
warns = []


def check(name, ok, detail="", storage=False):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f"  ({detail})" if detail else ""), flush=True)
    (storage_results if storage else results).append((name, ok))


def warn(name, ok, detail=""):
    print(("PASS" if ok else "WARN") + f": {name}" +
          (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        warns.append(name)


def metric(name, value, unit):
    print(f"METRIC {name}={value:.1f} {unit}", flush=True)


def frame(t, flags, seq, payload=b""):
    return HDR.pack(MAGIC, VER, t, flags, seq, len(payload)) + payload


class Tunnel:
    """The app side of BLE_HTTP_PROTOCOL.md over a Stream."""

    def __init__(self, stream, notify=True):
        self.s = stream
        self.seq = 0
        self.credits = {}      # seq -> last CREDIT value
        self.stray = []        # frames not belonging to the active request
        self.resync = 0        # bad headers skipped (should stay 0)
        self.notify = notify   # FFF3 subscribed for notifications: we owe CREDITs
        self.holes = 0         # RSP_BODY counter gaps = lost PDUs (must stay 0)
        self.credits_sent = 0

    async def _read_frame(self, timeout):
        while True:
            h = await self.s.read_exact(HDR.size, timeout)
            magic, ver, t, flags, seq, ln = HDR.unpack(h)
            if magic == MAGIC and ver == VER and 1 <= t <= 6 and ln <= FRAME_MAX:
                break
            # resync like the device does: drop one byte, look for the next
            # magic (a lost/partial notification desyncs the stream; the
            # byte-exact compares catch the data loss, this keeps us alive)
            self.resync += 1
            if self.resync == 1:
                print(f"  (stream desync: bad header {h.hex()}, resyncing)", flush=True)
            self.s.buf[0:0] = h[1:]   # put the 7 tail bytes back, minus the first
        payload = await self.s.read_exact(ln, timeout) if ln else b""
        return t, flags, seq, payload

    async def request(self, method, path, body=b"", ct="", timeout=30,
                      response=False, abort_after=None, on_progress=None):
        """-> (status, headers-dict, body bytes). abort_after = bytes of body
        after which the caller-supplied coroutine is awaited (disconnect
        tests); on_progress(sent) callback."""
        self.seq = (self.seq + 1) & 0xFFFF
        seq = self.seq
        head = {"m": method, "p": path, "len": len(body)}
        if ct:
            head["ct"] = ct
        await self.s.write(frame(T_REQ, 0, seq, json.dumps(head).encode()),
                           response=response)
        sent = 0
        credited = 0
        ctr = 0
        while sent < len(body):
            # window: at most CREDIT_WINDOW bytes beyond the last CREDIT
            while sent - credited >= CREDIT_WINDOW:
                t, fl, sq, pl = await self._read_frame(timeout)
                if t == T_CREDIT and sq == seq:
                    credited = struct.unpack("<I", pl)[0]
                elif t == T_RSP and sq == seq:
                    # an early response (error): drain its LAST and return
                    rsp = json.loads(pl.decode())
                    out = await self._body(seq, timeout)
                    return rsp.get("s"), rsp, out
                else:
                    self.stray.append((t, sq))
            n = min(FRAME_MAX, len(body) - sent)
            last = sent + n == len(body)
            await self.s.write(frame(T_REQ_BODY, ctr_flags(ctr, last), seq,
                                     body[sent:sent + n]), response=response)
            ctr = (ctr + 1) & 0x0F
            sent += n
            if on_progress:
                on_progress(sent)
            if abort_after is not None and sent >= abort_after:
                await abort_after_cb(self)
                return None, {}, b""
        # the response head (skipping late credits)
        while True:
            t, fl, sq, pl = await self._read_frame(timeout)
            if t == T_RSP and sq == seq:
                break
            if t == T_CREDIT and sq == seq:
                continue
            self.stray.append((t, sq))
        rsp = json.loads(pl.decode())
        out = await self._body(seq, timeout)
        return rsp.get("s"), rsp, out

    async def _body(self, seq, timeout):
        out = bytearray()
        expect = 0
        paid = 0
        while True:
            t, fl, sq, pl = await self._read_frame(timeout)
            if t == T_RSP_BODY and sq == seq:
                ctr = fl >> CTR_SHIFT
                if ctr != expect:
                    # a whole frame (= one notification PDU) never arrived
                    self.holes += 1
                    if self.holes == 1:
                        print(f"  (stream hole: counter {ctr} after {len(out)} B)", flush=True)
                    expect = ctr
                expect = (expect + 1) & 0x0F
                out += pl
                if fl & F_LAST:
                    return bytes(out)
                if self.notify and len(out) - paid >= CREDIT_STEP:
                    # notify mode: the device keeps <= 16 KB unacknowledged
                    paid = len(out)
                    await self.s.write(frame(T_CREDIT, 0, seq, struct.pack("<I", paid)))
                    self.credits_sent += 1
            else:
                self.stray.append((t, sq))

    async def get(self, path, timeout=30):
        st, rsp, body = await self.request("GET", path, timeout=timeout)
        try:
            return st, rsp, json.loads(body.decode())
        except Exception:  # noqa: BLE001
            return st, rsp, body


abort_after_cb = None  # set by the disconnect leg


# ------------------------------------------------------------------ configure
def stage_configure(ip, passkey, wifi_off):
    st, status = api(ip, "/api/status")
    _, info = api(ip, "/api/info")
    device_id = info.get("device_id", "")
    if os.path.exists(STATE):
        saved = json.load(open(STATE))
        print(f"  baseline kept from {STATE}", flush=True)
    else:
        saved = {"device_id": device_id, "sta_mac": info.get("mac", ""),
                 "ble": clean(api(ip, "/api/settings/ble_manager")[1]),
                 "ble_http": clean(api(ip, "/api/settings/ble_http")[1]),
                 "ifm": clean(api(ip, "/api/settings/interface_manager")[1]),
                 "wifi": clean(api(ip, "/api/settings/wifi_manager")[1]),
                 "faults": api(ip, "/api/faults")[1].get("faults", []),
                 "sd_mounted": bool(status.get("bits", {}).get("sdcard_mounted")),
                 "wifi_off": wifi_off}
        json.dump(saved, open(STATE, "w"))
    check("configure_baseline", "enabled" in saved["ble"] and device_id != "",
          f"device {device_id} sd_mounted={saved['sd_mounted']}")

    ble = dict(saved["ble"]); ble.update({"enabled": True, "passkey": passkey})
    check("configure_put_ble", api(ip, "/api/settings/ble_manager", "PUT", ble)[0] == 200)
    bh = dict(saved["ble_http"]); bh["enabled"] = True
    check("configure_put_ble_http", api(ip, "/api/settings/ble_http", "PUT", bh)[0] == 200)
    ifm = dict(saved["ifm"]); ifm["sta_ble_handover"] = False
    check("configure_put_ifm", api(ip, "/api/settings/interface_manager", "PUT", ifm)[0] == 200)
    if wifi_off:
        wifi = dict(saved["wifi"]); wifi["mode"] = "ap"
        for k in list(wifi):
            if k.endswith("_password") and wifi[k] == "":
                pass  # "" keeps the stored secret
        check("configure_put_wifi_ap", api(ip, "/api/settings/wifi_manager", "PUT", wifi)[0] == 200)
    print("  submit + reboot...", flush=True)
    nip, st2 = submit_and_wait(ip, device_id, status.get("boot_count"))
    check("configure_dut_back", nip is not None, f"ip={nip}")
    if nip:
        # (the ble_enabled STATUS bit is checked in the BLE leg, after the
        # Pi has left the DUT's AP: a station there stops BLE by policy)
        st3, bhs = api(nip, "/api/settings/ble_http")
        check("configure_ble_http_enabled", st3 == 200 and bhs.get("enabled") is True, "")
        st4, bm = api(nip, "/api/settings/ble_manager")
        check("configure_ble_setting_on", st4 == 200 and bm.get("enabled") is True, "")
    return nip


# ------------------------------------------------------------------------ ble
async def link_gate(name, identity, passkey, bonded):
    for i in range(2):
        try:
            client, cap = await connect(name, identity, passkey, bonded=bonded or i > 0, tries=3)
            await client.read_gatt_char("00002a29-0000-1000-8000-00805f9b34fb")
            await client.disconnect()
            await asyncio.sleep(2)
        except Exception as e:  # noqa: BLE001
            check("L0_link_gate", False, f"cycle {i + 1}: {str(e)[:100]}")
            return False
    check("L0_link_gate", True, "2 connect/pair/disconnect cycles")
    return True


def rand_blob(n, seed):
    out = bytearray()
    x = seed
    while len(out) < n:
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        out += struct.pack("<I", x)
    return bytes(out[:n])


async def storage_legs(t, ip, witness, mounts):
    for mount in mounts:
        base = mount + DIR
        st, rsp, info = await t.get(f"/api/fs/info?path={mount}")
        check(f"L3_{mount}_info", st == 200 and isinstance(info, dict) and "total" in info,
              f"{info if isinstance(info, dict) else str(info)[:60]}", storage=True)
        if witness:
            wst, winfo = api(ip, f"/api/fs/info?path={mount}")
            check(f"L3_{mount}_info_matches_wifi", wst == 200 and isinstance(winfo, dict)
                  and winfo.get("total") == info.get("total"),
                  f"ble {info.get('total')} wifi {winfo.get('total')}", storage=True)
        st, rsp, body = await t.request("POST", f"/api/fs/mkdir?path={base}")
        check(f"L3_{mount}_mkdir", st == 200, f"{st} {body[:60]}", storage=True)

        for size in (1024, 65536, 524288):
            blob = rand_blob(size, size)
            path = f"{base}/f{size}.bin"
            t0 = time.monotonic()
            st, rsp, body = await t.request("POST", f"/api/fs/upload?path={path}", blob,
                                            ct="application/octet-stream", timeout=180)
            dt = time.monotonic() - t0
            ok = st == 200
            try:
                ok = ok and json.loads(body)["size"] == size
            except Exception:  # noqa: BLE001
                ok = False
            check(f"L3_{mount}_upload_{size}", ok, f"{st} {body[:80]} {dt:.1f}s", storage=True)
            metric(f"upload_{mount.strip('/')}_{size}", size / 1024 / max(dt, 1e-3), "KB/s")

            dev0 = channel_stats(ip, "http") if witness else {}
            rx0 = t.s.rx_bytes
            t0 = time.monotonic()
            st, rsp, back = await t.request("GET", f"/api/fs/download?path={path}", timeout=300)
            dt = time.monotonic() - t0
            dev1 = channel_stats(ip, "http") if witness else {}
            dtx = (dev1.get("tx_bytes", 0) - dev0.get("tx_bytes", 0)) if dev1 else None
            check(f"L3_{mount}_download_{size}_exact", st == 200 and back == blob,
                  f"{st} len {len(back)} ct {rsp.get('ct')} {dt:.1f}s; device tx {dtx} B vs client rx "
                  f"{t.s.rx_bytes - rx0} B, tx_timeouts {dev1.get('tx_timeouts')} link_down {dev1.get('tx_link_down')}",
                  storage=True)
            metric(f"download_{mount.strip('/')}_{size}", size / 1024 / max(dt, 1e-3), "KB/s")

            if witness and size == 65536:
                wst, wblob = api(ip, f"/api/fs/download?path={path}", raw=True, timeout=30)
                check(f"L3_{mount}_sha256_wifi_witness",
                      wst == 200 and hashlib.sha256(wblob).hexdigest() == hashlib.sha256(blob).hexdigest(),
                      f"wifi {wst} {len(wblob) if isinstance(wblob, bytes) else wblob}", storage=True)

        st, rsp, lst = await t.get(f"/api/fs/list?path={base}")
        names = sorted(e["name"] for e in lst.get("entries", [])) if isinstance(lst, dict) else []
        check(f"L3_{mount}_list", names == ["f1024.bin", "f524288.bin", "f65536.bin"], str(names),
              storage=True)
        for size in (1024, 65536, 524288):
            st, rsp, body = await t.request("DELETE", f"/api/fs/file?path={base}/f{size}.bin")
            check(f"L3_{mount}_delete_{size}", st == 200, f"{st} {body[:40]}", storage=True)
        st, rsp, lst = await t.get(f"/api/fs/list?path={base}")
        check(f"L3_{mount}_list_empty", st == 200 and lst.get("entries") == [], str(lst)[:80],
              storage=True)
        st, rsp, body = await t.request("DELETE", f"/api/fs/file?path={base}")
        check(f"L3_{mount}_rmdir", st == 200, f"{st} {body[:40]}", storage=True)


async def error_legs(t, ip, witness):
    st, rsp, body = await t.request("GET", "/ui/index.html")
    check("L4_403_outside_api", st == 403 and rsp.get("err") == "path", f"{st} {rsp}")
    st, rsp, body = await t.request("GET", "/api/fs/list?path=/etc")
    check("L4_400_invalid_path_passthrough", st == 400, f"{st} {body[:60]}")
    st, rsp, body = await t.request("GET", "/api/fs/download?path=/data/nope.bin")
    check("L4_404_passthrough", st == 404, f"{st} {body[:60]}")
    st, rsp, body = await t.request("GET", "/api/nope")
    check("L4_404_unknown_route", st == 404, f"{st} {body[:60]}")

    # 429: a second REQ while a body is in flight
    seq = t.seq + 1
    t.seq = seq
    await t.s.write(frame(T_REQ, 0, seq, json.dumps(
        {"m": "POST", "p": f"/api/fs/upload?path=/data{DIR}/busy.bin",
         "ct": "application/octet-stream", "len": 8192}).encode()))
    await t.s.write(frame(T_REQ_BODY, ctr_flags(0, False), seq, b"\x00" * 4096))   # body frame 0
    seq2 = seq + 1
    await t.s.write(frame(T_REQ, 0, seq2, json.dumps({"m": "GET", "p": "/api/status"}).encode()))
    got429 = False
    for _ in range(4):
        tt, fl, sq, pl = await t._read_frame(10)
        if tt == T_RSP and sq == seq2:
            got429 = json.loads(pl.decode()).get("s") == 429
            await t._body(seq2, 10)
            break
    check("L4_429_busy_second_request", got429, "")
    # finish the upload cleanly: v2 counter continues at 1 (a 0 here is a
    # hole and the device answers 400 "hole", verified live 2026-09-22)
    await t.s.write(frame(T_REQ_BODY, ctr_flags(1, True), seq, b"\x00" * 4096))
    while True:
        tt, fl, sq, pl = await t._read_frame(30)
        if tt == T_RSP and sq == seq:
            st = json.loads(pl.decode()).get("s")
            await t._body(seq, 10)
            break
    t.seq = seq2
    check("L4_upload_after_busy_completes", st == 200, f"{st}")
    await t.request("DELETE", f"/api/fs/file?path=/data{DIR}/busy.bin")

    if witness:
        # a WiFi upload racing a BLE upload: exactly one side is refused 503
        await t.request("POST", f"/api/fs/mkdir?path=/data{DIR}")
        res = {}

        def wifi_up():
            res["wifi"] = api(ip, f"/api/fs/upload?path=/data{DIR}/wifi.bin",
                              body=rand_blob(262144, 7), ct="application/octet-stream", timeout=60)[0]
        th = threading.Thread(target=wifi_up)
        th.start()
        await asyncio.sleep(0.3)
        st, rsp, body = await t.request("POST", f"/api/fs/upload?path=/data{DIR}/ble.bin",
                                        rand_blob(131072, 9), ct="application/octet-stream", timeout=90)
        th.join(90)
        pair = (res.get("wifi"), st)
        warn("L4_concurrent_wifi_upload_one_refused", 503 in pair and 200 in pair, f"wifi/ble {pair}")
        api(ip, f"/api/fs/file?path=/data{DIR}/wifi.bin", "DELETE")
        api(ip, f"/api/fs/file?path=/data{DIR}/ble.bin", "DELETE")


async def uds_legs(t):
    st, rsp, body = await t.request("POST", "/api/uds/request",
                                    json.dumps({"tx_id": "7E0", "rx_id": "7E8", "data": "22 F1 90"}).encode(),
                                    ct="application/json", timeout=20)
    try:
        r = json.loads(body)
    except Exception:  # noqa: BLE001
        r = {}
    warn("L6_uds_vin_22F190", st == 200 and r.get("ok") and r.get("response", "").startswith("62 F1 90 " + VIN),
         f"{st} {r.get('response', body[:60])} backend={r.get('backend')} {r.get('elapsed_ms')}ms")
    # the simulator's ECU name DID (F187 = "WCAN-ECU-SIM") answers on every
    # sim boot; its VIN DID (F190) came back NRC 0x31 on 2026-09-21
    st, rsp, body = await t.request("POST", "/api/uds/request",
                                    json.dumps({"tx_id": "7E0", "rx_id": "7E8", "data": "22 F1 87"}).encode(),
                                    ct="application/json", timeout=20)
    try:
        r = json.loads(body)
    except Exception:  # noqa: BLE001
        r = {}
    check("L6_uds_request_positive", st == 200 and r.get("ok") and r.get("positive") is True and
          r.get("response", "").startswith("62 F1 87 57 43 41 4E"),
          f"{st} {r.get('response', body[:60])} backend={r.get('backend')} {r.get('elapsed_ms')}ms")
    st, rsp, body = await t.request("POST", "/api/uds/request",
                                    json.dumps({"tx_id": "7E0", "rx_id": "7E8", "data": "10 02"}).encode(),
                                    ct="application/json", timeout=20)
    try:
        r = json.loads(body)
    except Exception:  # noqa: BLE001
        r = {}
    check("L6_uds_session_control", st == 200 and r.get("response", "").startswith("50 02"),
          f"{st} {r.get('response', body[:60])}")
    st, rsp, body = await t.request("POST", "/api/uds/session",
                                    json.dumps({"action": "begin", "tx_id": "7E0", "rx_id": "7E8"}).encode(),
                                    ct="application/json", timeout=20)
    st2, rsp2, u = await t.get("/api/uds")
    check("L6_uds_session_begin", st == 200 and isinstance(u, dict) and u.get("session_active") is True,
          f"{st} {u.get('session_active') if isinstance(u, dict) else u}")
    st, rsp, body = await t.request("POST", "/api/uds/session", json.dumps({"action": "end"}).encode(),
                                    ct="application/json", timeout=20)
    st2, rsp2, u = await t.get("/api/uds")
    check("L6_uds_session_end", st == 200 and isinstance(u, dict) and u.get("session_active") is False, f"{st}")


async def ble_leg(saved, passkey, nm_profile):
    global abort_after_cb
    device_id = saved["device_id"]
    name = f"WiC_{device_id}"
    identity = bt_identity(saved["sta_mac"])
    witness = not saved.get("wifi_off")
    ip = saved.get("ip")
    bonded = prep_adapter(identity, nm_profile)
    if witness and ip:
        # with the Pi off the DUT's AP, interface_manager restarts BLE
        wst, wstatus = api(ip, "/api/status", timeout=5)
        check("L0_ble_enabled_bit", wst == 200 and wstatus.get("bits", {}).get("ble_enabled") is True,
              f"wifi {wst} ble_enabled={wstatus.get('bits', {}).get('ble_enabled') if isinstance(wstatus, dict) else '?'}")

    if not await link_gate(name, identity, passkey, bonded):
        print("BLE API BLOCKED: link", flush=True)
        return
    bonded = True

    client, cap = await connect(name, identity, passkey, bonded=True)
    svc_uuids = [c.uuid for s in client.services for c in s.characteristics]
    check("L0_channel_present", UUID_HTTP_OUT in svc_uuids and UUID_HTTP_IN in svc_uuids,
          f"{len(svc_uuids)} characteristics")
    s = Stream(client, UUID_HTTP_OUT, UUID_HTTP_IN, cap)
    await s.start()
    t = Tunnel(s)

    # L1
    t0 = time.monotonic()
    st, rsp, status = await t.get("/api/status")
    rtt = (time.monotonic() - t0) * 1000
    check("L1_get_status", st == 200 and isinstance(status, dict) and "bits" in status,
          f"{st} ct={rsp.get('ct')} len={rsp.get('len')} {rtt:.0f} ms")
    metric("get_status_rtt", rtt, "ms")
    st, rsp, info = await t.get("/api/info")
    check("L1_get_info_device_id", st == 200 and info.get("device_id") == device_id, f"{info}")
    if witness and ip:
        wst, wstatus = api(ip, "/api/status", timeout=5)
        check("L1_wifi_witness_alive", wst == 200 and isinstance(wstatus, dict)
              and wstatus.get("bits", {}).get("ble_connected") is True,
              f"wifi {wst} ble_connected={wstatus.get('bits', {}).get('ble_connected') if isinstance(wstatus, dict) else '?'}")
        if wst != 200:
            # the STA fell back to the client-isolated dongle AP after the
            # configure reboot: the WiFi witness legs would only crash the run
            print("  (WiFi witness unreachable: the cross-check legs are skipped for this run)", flush=True)
            witness = False

    # L2
    st, rsp, cfg = await t.get("/api/settings/ble_http")
    check("L2_get_settings", st == 200 and cfg.get("enabled") is True, f"{cfg}")
    st, rsp, sch = await t.get("/api/settings/ble_http/schema")
    check("L2_get_schema", st == 200 and isinstance(sch, dict) and "properties" in sch, f"{st}")
    st, rsp, body = await t.request("PUT", "/api/settings/ble_http", json.dumps(clean(dict(cfg))).encode(),
                                    ct="application/json")
    st2, rsp2, cfg2 = await t.get("/api/settings/ble_http")
    check("L2_put_identical_roundtrip", st == 200 and cfg2.get("enabled") is True and
          cfg2.get("pending_reboot") is False, f"put {st} {body[:60]} pending={cfg2.get('pending_reboot')}")

    # L3
    mounts = ["/data"] + (["/sd"] if saved.get("sd_mounted") else [])
    await storage_legs(t, ip, witness and ip, mounts)

    # L4
    await error_legs(t, ip, witness and ip)

    # L5: drop the link mid-upload, then reconnect and finish a fresh one
    torn = f"/data{DIR}/torn.bin"
    await t.request("POST", f"/api/fs/mkdir?path=/data{DIR}")

    async def drop(tun):
        await tun.s.client.disconnect()
    abort_after_cb = drop
    try:
        await t.request("POST", f"/api/fs/upload?path={torn}", rand_blob(524288, 3),
                        ct="application/octet-stream", timeout=60, abort_after=131072)
    except Exception as e:  # noqa: BLE001
        print(f"  (disconnect mid-upload: {str(e)[:60]})", flush=True)
    await asyncio.sleep(4)
    client, cap = await connect(name, identity, passkey, bonded=True)
    s = Stream(client, UUID_HTTP_OUT, UUID_HTTP_IN, cap)
    await s.start()
    t = Tunnel(s)
    st, rsp, lst = await t.get(f"/api/fs/list?path=/data{DIR}")
    names = [e["name"] for e in lst.get("entries", [])] if isinstance(lst, dict) else None
    check("L5_no_torn_file_after_drop", st == 200 and names is not None and "torn.bin" not in names
          and not any(n.endswith(".tmp") for n in names), f"{names}")
    st, rsp, body = await t.request("POST", f"/api/fs/upload?path={torn}", rand_blob(65536, 5),
                                    ct="application/octet-stream", timeout=60)
    check("L5_fresh_upload_after_reconnect", st == 200, f"{st} {body[:60]}")
    await t.request("DELETE", f"/api/fs/file?path={torn}")
    await t.request("DELETE", f"/api/fs/file?path=/data{DIR}")

    # L6
    await uds_legs(t)

    # L7: a chunked text body
    st, rsp, ring = await t.request("GET", "/api/logs/ring", timeout=60)
    check("L7_logs_ring_streamed", st == 200 and len(ring) > 200,
          f"{st} len={len(ring)} declared={rsp.get('len')} notifs={s.notifs}")
    metric("stream_notifications", s.notifs, "count")
    metric("stream_rx_bytes", s.rx_bytes, "B")
    check("L7_no_stray_frames", not t.stray, str(t.stray[:5]))
    check("L7_no_stream_resync", t.resync == 0, f"{t.resync} bad headers skipped")
    check("L8_no_stream_holes", t.holes == 0, f"{t.holes} RSP_BODY counter gaps (lost notifications)")
    check("L8_credits_flowed", (not t.notify) or t.credits_sent > 0,
          f"{t.credits_sent} CREDIT frames sent (notify mode={t.notify})")
    metric("stream_credits_sent", t.credits_sent, "count")
    if witness and ip:
        http = channel_stats(ip, "http")
        check("L8_dut_out_mode_notify", http.get("out") == ("notify" if t.notify else "indicate"),
              f"dut out={http.get('out')} modes={http.get('out_modes')} "
              f"notifications={http.get('tx_notifications')} indications={http.get('tx_indications')}")
        check("L8_dut_channel_clean", http.get("tx_timeouts", 0) == 0 and http.get("rx_overflow", 0) == 0,
              f"tx_timeouts {http.get('tx_timeouts')} rx_overflow {http.get('rx_overflow')}")

    await s.stop()
    await client.disconnect()


def stage_ble(passkey, nm_profile):
    saved = json.load(open(STATE))
    ip = saved.get("ip")
    if not saved.get("wifi_off"):
        nip, st = find_dut(ip, saved["device_id"], window=60)
        saved["ip"] = nip or ip
        json.dump(saved, open(STATE, "w"))
    mon = btmon_start(BTMON_LOG)
    try:
        asyncio.run(ble_leg(saved, passkey, nm_profile))
    finally:
        notifs, errs = btmon_summary(mon, BTMON_LOG)
        print(f"  btmon: {notifs} ATT notifications on air; last events: {errs}", flush=True)
        metric("btmon_notifications", notifs, "count")


# --------------------------------------------------------------------- restore
def stage_restore(hint, nm_profile):
    saved = json.load(open(STATE))
    device_id = saved["device_id"]
    if nm_profile and saved.get("wifi_off"):
        os.system(f"nmcli con up {nm_profile} >/dev/null 2>&1")
        time.sleep(8)
    nip, st = find_dut(saved.get("ip") or hint, device_id, window=150)
    check("restore_dut_reachable", nip is not None, f"ip={nip}")
    if nip is None:
        print(f"RESTORE SKIPPED: DUT unreachable; saved settings in {STATE}", flush=True)
        return
    own, other, errs = ring_errors(nip)
    check("restore_zero_E_lines_own_tags", own is not None and not own,
          f"{len(errs) if errs is not None else '?'} E lines total, {len(own) if own else 0} from the BLE/J2534/HTTP tags"
          + (f": {own[0][:100]}" if own else ""))
    warn("restore_other_E_lines", not other,
         f"{len(other) if other else 0} pre-existing/other: {other[0][:90] if other else ''}")
    for name, path in (("ble", "ble_manager"), ("ble_http", "ble_http"), ("ifm", "interface_manager")):
        check(f"restore_put_{name}", api(nip, f"/api/settings/{path}", "PUT", saved[name])[0] == 200)
    if saved.get("wifi_off"):
        check("restore_put_wifi", api(nip, "/api/settings/wifi_manager", "PUT", saved["wifi"])[0] == 200)
    print("  restoring; submit + reboot...", flush=True)
    nip2, st2 = submit_and_wait(nip, device_id, st.get("boot_count"))
    check("restore_dut_back", nip2 is not None, f"ip={nip2}")
    if nip2:
        check("restore_ble_setting", api(nip2, "/api/settings/ble_manager")[1].get("enabled") ==
              saved["ble"].get("enabled"))
        before = {f["code"] for f in saved["faults"]}
        new = [f for f in api(nip2, "/api/faults")[1].get("faults", []) if f["code"] not in before]
        check("restore_no_new_faults", not new, str(new)[:200])
        check("restore_no_unexpected_resets", st2.get("unexpected_resets", 0) == 0,
              f"unexpected_resets={st2.get('unexpected_resets')}")
        os.replace(STATE, STATE + ".done")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="10.42.1.194")
    ap.add_argument("--stage", choices=["all", "configure", "ble", "restore"], default="all")
    ap.add_argument("--passkey", type=int, default=421337)
    ap.add_argument("--nm-profile", default="wican-bench-ap")
    ap.add_argument("--wifi-off", action="store_true",
                    help="wifi mode ap for the run (no WiFi witness): the fallback rig mode")
    a = ap.parse_args()
    ip = a.dut
    if a.stage in ("all", "configure"):
        ip, _ = find_dut(ip, None, window=30)
        if not ip:
            print("FATAL: DUT unreachable")
            return 1
        ip = stage_configure(ip, a.passkey, a.wifi_off) or ip
        saved = json.load(open(STATE)); saved["ip"] = ip; json.dump(saved, open(STATE, "w"))
        print("PROGRESS configure done", flush=True)
    if a.stage in ("all", "ble"):
        stage_ble(a.passkey, a.nm_profile)
        print("PROGRESS ble done", flush=True)
    if a.stage in ("all", "restore"):
        stage_restore(ip, a.nm_profile)
        print("PROGRESS restore done", flush=True)

    sf = [n for n, ok in storage_results if not ok]
    fails = [n for n, ok in results if not ok] + sf
    if warns:
        print("WARN: " + ", ".join(warns), flush=True)
    if a.stage in ("all", "ble"):
        # the verdict lines belong to the BLE leg only (test.ps1 greps the
        # concatenated stage outputs for them)
        print(("BLE STORAGE FAIL: " + ", ".join(sf)) if sf or not storage_results
              else "BLE STORAGE PASS", flush=True)
        print(("BLE API FAIL: " + ", ".join(fails)) if fails else "BLE API PASS", flush=True)
    else:
        print(f"STAGE {a.stage} " + ("FAIL: " + ", ".join(fails) if fails else "OK"), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
