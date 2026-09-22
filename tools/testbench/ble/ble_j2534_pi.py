#!/usr/bin/env python3
"""J2534 PassThru over BLE (ble_j2534): the wire protocol on the FFF5/FFF6 channel.

Runs ON rpi001 (UB500 + bleak + the D-Bus passkey agent). Same protocol as
tools/testbench/usb/j2534_bench.py speaks over TCP (J2534_WIRE_PROTOCOL.md),
carried as a byte stream on the `j2534` channel. Stages (state in
/tmp/ble_j2534_bench.json, a baseline already there is KEPT):

  configure : snapshot; j2534_server enabled + allow_lan (the second-tester
              leg comes from the Pi's STA side over TCP) + exclusive; BLE on
              (passkey); interface_manager.sta_ble_handover OFF so the STA
              stays up as the witness; submit-reboot; FFF5/FFF6 must now be
              registered (ble_j2534 keys on j2534_server.enabled).
  ble       : bond hygiene, leave the DUT's AP, connect + pair, then
              L0 link gate (2 cycles), the channel present
              L1 HELLO (wire v1) / OPEN (device 1); /api/j2534 shows
                 client_connected + transport "ble" + autopid_paused
              L2 CONNECT CAN, WRITE_MSGS 7DF [02 01 00] -> RX_MSG from 7E8
                 starting 06 41 00 18 3F 80 03 (the ECU simulator), the
                 native /api/can tx counter stepped (witness)
              L3 START_FILTER PASS 7E8 -> still answered; STOP + BLOCK 7E8 ->
                 silence; STOP; DISCONNECT
              L4 CONNECT ISO15765 7E0/7E8, 22 F1 90 -> 62 F1 90 + VIN,
                 10 02 -> 50 02; reflash gate: 34 .. -> ERR_NOT_SUPPORTED
              L5 a second tester over TCP (the Pi) while BLE holds the
                 session -> ACK ERR_DEVICE_IN_USE (0x1A)
              L6 RTT: 30 x HELLO p50/p95 (report only)
              L7 drop the BLE link mid-session -> /api/j2534 client_connected
                 false + transport none within 5 s; a TCP session then
                 succeeds; a second BLE session: HELLO/OPEN/CLOSE clean
  restore   : settings back, submit-reboot, no new faults, 0 unexpected
              resets, 0 non-whitelisted E lines.

Usage (on rpi001):
  sudo env PYTHONPATH=/home/meatpi/.local/lib/python3.11/site-packages \
      python3 -u ble_j2534_pi.py --dut 10.42.1.194 [--stage configure|ble|restore]
Expected final line: BLE J2534 PASS
"""
import argparse
import asyncio
import json
import os
import socket
import statistics
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ble_link import (UUID_J2534_IN, UUID_J2534_OUT, Stream, api, bt_identity,  # noqa: E402
                      clean, connect, find_dut, prep_adapter, ring_errors,
                      submit_and_wait)

STATE = "/tmp/ble_j2534_bench.json"
MAGIC = 0x4A35
HDR = struct.Struct("<HBBHHI")   # magic, ver, type, seq, channel, length
MSG = struct.Struct("<IIIIII")   # protocol, rx_status, tx_flags, ts, extra, size
(HELLO, OPEN, CLOSE, CONNECT, DISCONNECT, WRITE_MSGS, START_FILTER, STOP_FILTER,
 ACK, RX_MSG) = (0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x11, 0x12, 0x80, 0x81)
PROT_CAN, PROT_ISO15765 = 5, 6
NOERROR, ERR_NOT_SUPPORTED, ERR_DEVICE_IN_USE = 0x00, 0x01, 0x1A
PASS_FILTER, BLOCK_FILTER = 1, 2
VIN = bytes.fromhex("31574341 4E304657 30503030 30303031".replace(" ", ""))

results = []
warns = []


def api_json(ip, path, tries=4, timeout=5):
    """GET a JSON document with retries (the STA may be slow beside BLE)."""
    for _ in range(tries):
        st, body = api(ip, path, timeout=timeout)
        if st == 200 and isinstance(body, dict):
            return st, body
        time.sleep(1.5)
    return st, (body if isinstance(body, dict) else {"_error": str(body)[:80]})


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" + (f"  ({detail})" if detail else ""), flush=True)
    results.append((name, ok))


def warn(name, ok, detail=""):
    print(("PASS" if ok else "WARN") + f": {name}" + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        warns.append(name)


def metric(name, value, unit):
    print(f"METRIC {name}={value:.1f} {unit}", flush=True)


def can_msg(can_id, data, ext=False):
    d = struct.pack(">I", can_id) + bytes(data)
    return MSG.pack(PROT_CAN, 0, 0x100 if ext else 0, 0, 0, len(d)) + d


def iso_msg(data):
    return MSG.pack(PROT_ISO15765, 0, 0, 0, 0, len(data)) + bytes(data)


def id_msg(can_id):
    d = struct.pack(">I", can_id)
    return MSG.pack(PROT_CAN, 0, 0, 0, 0, len(d)) + d


class BleTester:
    """The J2534 client over a Stream (port of j2534_bench.Client)."""

    def __init__(self, stream):
        self.s = stream
        self.seq = 0
        self.rx = []      # (channel, data) of RX_MSG frames seen

    async def _read_frame(self, timeout):
        h = await self.s.read_exact(HDR.size, timeout)
        magic, ver, rt, rseq, rch, rlen = HDR.unpack(h)
        if magic != MAGIC:
            raise RuntimeError(f"bad magic {h.hex()}")
        body = await self.s.read_exact(rlen, timeout) if rlen else b""
        if rt == RX_MSG:
            size = MSG.unpack_from(body, 0)[5]
            self.rx.append((rch, body[MSG.size:MSG.size + size]))
        return rt, rseq, rch, body

    async def call(self, mtype, channel=0, payload=b"", timeout=10):
        self.seq = (self.seq + 1) & 0xFFFF
        await self.s.write(HDR.pack(MAGIC, 1, mtype, self.seq, channel, len(payload)) + payload)
        while True:
            rt, rseq, rch, body = await self._read_frame(timeout)
            if rt == ACK and rseq == self.seq:
                status = struct.unpack_from("<I", body, 0)[0]
                result = struct.unpack_from("<I", body, 4)[0] if len(body) >= 8 else None
                return status, result, rch

    async def collect_rx(self, secs):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            try:
                await self._read_frame(max(0.05, end - time.monotonic()))
            except asyncio.TimeoutError:
                break

    async def write(self, channel, msg):
        return await self.call(WRITE_MSGS, channel, struct.pack("<I", 1) + msg)


class TcpTester:
    def __init__(self, ip, port=6809, timeout=5):
        self.s = socket.create_connection((ip, port), timeout=timeout)
        self.seq = 0

    def _recv(self, n):
        b = b""
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise EOFError
            b += c
        return b

    def call(self, mtype, channel=0, payload=b""):
        self.seq += 1
        self.s.sendall(HDR.pack(MAGIC, 1, mtype, self.seq, channel, len(payload)) + payload)
        while True:
            magic, ver, rt, rseq, rch, rlen = HDR.unpack(self._recv(HDR.size))
            body = self._recv(rlen)
            if rt == ACK:
                return struct.unpack_from("<I", body, 0)[0]

    def close(self):
        self.s.close()


# ------------------------------------------------------------------ configure
def stage_configure(ip, passkey):
    st, status = api(ip, "/api/status")
    _, info = api(ip, "/api/info")
    device_id = info.get("device_id", "")
    if os.path.exists(STATE):
        saved = json.load(open(STATE))
        print(f"  baseline kept from {STATE}", flush=True)
    else:
        saved = {"device_id": device_id, "sta_mac": info.get("mac", ""),
                 "ble": clean(api(ip, "/api/settings/ble_manager")[1]),
                 "j2534": clean(api(ip, "/api/settings/j2534_server")[1]),
                 "ifm": clean(api(ip, "/api/settings/interface_manager")[1]),
                 "faults": api(ip, "/api/faults")[1].get("faults", [])}
        json.dump(saved, open(STATE, "w"))
    check("configure_baseline", "enabled" in saved["j2534"] and device_id != "", f"device {device_id}")
    ble = dict(saved["ble"]); ble.update({"enabled": True, "passkey": passkey})
    check("configure_put_ble", api(ip, "/api/settings/ble_manager", "PUT", ble)[0] == 200)
    j = dict(saved["j2534"]); j.update({"enabled": True, "allow_lan": True, "exclusive": True})
    check("configure_put_j2534", api(ip, "/api/settings/j2534_server", "PUT", j)[0] == 200)
    ifm = dict(saved["ifm"]); ifm["sta_ble_handover"] = False
    check("configure_put_ifm", api(ip, "/api/settings/interface_manager", "PUT", ifm)[0] == 200)
    print("  submit + reboot...", flush=True)
    nip, st2 = submit_and_wait(ip, device_id, status.get("boot_count"))
    check("configure_dut_back", nip is not None, f"ip={nip}")
    if nip:
        st3, js = api(nip, "/api/j2534")
        check("configure_j2534_listening", st3 == 200 and js.get("enabled") and js.get("listening"),
              f"{js}")
        check("configure_transport_none", js.get("transport") == "none", f"{js.get('transport')}")
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


async def open_session(name, identity, passkey):
    client, cap = await connect(name, identity, passkey, bonded=True)
    s = Stream(client, UUID_J2534_OUT, UUID_J2534_IN, cap)
    await s.start()
    return client, s, BleTester(s)


async def ble_leg(saved, passkey, nm_profile):
    device_id = saved["device_id"]
    ip = saved["ip"]
    name = f"WiC_{device_id}"
    identity = bt_identity(saved["sta_mac"])
    # the GATT table grew by the J2534 pair since the last bond (BlueZ keeps
    # a per-bond cache and would not see FFF5/FFF6): pair fresh
    bonded = prep_adapter(identity, nm_profile, drop_bond=True)
    wst, wstatus = api_json(ip, "/api/status")
    check("L0_ble_enabled_bit", wst == 200 and wstatus.get("bits", {}).get("ble_enabled") is True,
          f"wifi {wst} ble_enabled={wstatus.get('bits', {}).get('ble_enabled') if isinstance(wstatus, dict) else '?'}")
    if not await link_gate(name, identity, passkey, bonded):
        print("BLE J2534 BLOCKED: link", flush=True)
        return

    client, s, t = await open_session(name, identity, passkey)
    uuids = [c.uuid for sv in client.services for c in sv.characteristics]
    check("L0_channel_present", UUID_J2534_OUT in uuids and UUID_J2534_IN in uuids,
          f"{len(uuids)} characteristics")

    # L1
    st, ver, _ = await t.call(HELLO)
    check("L1_hello", st == NOERROR and ver == 1, f"status {st} wire v{ver}")
    st, dev, _ = await t.call(OPEN)
    check("L1_open", st == NOERROR and dev == 1, f"status {st} device {dev}")
    await asyncio.sleep(1.0)
    wst, js = api_json(ip, "/api/j2534")
    check("L1_status_transport_ble", wst == 200 and js.get("client_connected") is True and
          js.get("transport") == "ble", f"{wst} {js.get('transport')} connected={js.get('client_connected')}")
    warn("L1_autopid_paused", js.get("autopid_paused") is True, f"{js.get('autopid_paused')}")

    # L2: CAN
    _, can0 = api_json(ip, "/api/can")
    tx0 = can0.get("tx", can0.get("stats", {}).get("tx", 0)) if isinstance(can0, dict) else 0
    st, ch, _ = await t.call(CONNECT, 0, struct.pack("<III", PROT_CAN, 0, 500000))
    check("L2_connect_can", st == NOERROR and ch == 1, f"status {st} ch {ch}")
    t.rx.clear()
    st, _, _ = await t.write(ch, can_msg(0x7DF, [0x02, 0x01, 0x00]))
    check("L2_write_7df", st == NOERROR, f"status {st}")
    await t.collect_rx(1.5)
    answers = [d for c, d in t.rx if c == ch and d[:4] == b"\x00\x00\x07\xE8"]
    # the mode 01 PID 00 positive response (`06 41 00` + the supported-PID
    # bitmask); the bitmask is the simulator's business (18 3F 80 03 in the
    # brief, all zeros on the 2026-09-21 sim boot) -> WARN only
    check("L2_rx_msg_from_7e8", any(d[4:7] == b"\x06\x41\x00" for d in answers),
          f"{len(t.rx)} rx, first {t.rx[0][1].hex() if t.rx else '-'}")
    warn("L2_supported_pids_bitmask", any(d[7:11] == bytes.fromhex("183F8003") for d in answers),
         f"{answers[0][7:11].hex() if answers else '-'}")
    _, can1 = api_json(ip, "/api/can")
    tx1 = can1.get("tx", can1.get("stats", {}).get("tx", 0)) if isinstance(can1, dict) else 0
    warn("L2_can_tx_counter_stepped", tx1 > tx0, f"tx {tx0} -> {tx1}")

    # L3: filters
    st, fid, _ = await t.call(START_FILTER, ch, struct.pack("<I", PASS_FILTER) +
                              id_msg(0xFFFFFFFF) + id_msg(0x7E8))
    check("L3_pass_filter", st == NOERROR and fid, f"status {st} id {fid}")
    t.rx.clear()
    await t.write(ch, can_msg(0x7DF, [0x02, 0x01, 0x00]))
    await t.collect_rx(1.5)
    check("L3_pass_still_answered", any(d[:4] == b"\x00\x00\x07\xE8" for c, d in t.rx), f"{len(t.rx)} rx")
    st, _, _ = await t.call(STOP_FILTER, ch, struct.pack("<I", fid))
    check("L3_stop_filter", st == NOERROR, f"status {st}")
    st, bid, _ = await t.call(START_FILTER, ch, struct.pack("<I", BLOCK_FILTER) +
                              id_msg(0xFFFFFFFF) + id_msg(0x7E8))
    t.rx.clear()
    await t.write(ch, can_msg(0x7DF, [0x02, 0x01, 0x00]))
    await t.collect_rx(1.0)
    check("L3_block_filter_silences_7e8", st == NOERROR and
          not any(d[:4] == b"\x00\x00\x07\xE8" for c, d in t.rx), f"{len(t.rx)} rx")
    await t.call(STOP_FILTER, ch, struct.pack("<I", bid))
    st, _, _ = await t.call(DISCONNECT, ch)
    check("L3_disconnect_can", st == NOERROR, f"status {st}")

    # L4: ISO15765
    st, ich, _ = await t.call(CONNECT, 0, struct.pack("<IIIII", PROT_ISO15765, 0, 500000, 0x7E0, 0x7E8))
    check("L4_connect_iso15765", st == NOERROR and ich >= 1, f"status {st} ch {ich}")
    t.rx.clear()
    st, _, _ = await t.write(ich, iso_msg([0x22, 0xF1, 0x87]))   # ECU name DID
    await t.collect_rx(2.0)
    nm = [d for c, d in t.rx if c == ich and d[:3] == b"\x62\xF1\x87"]
    check("L4_uds_did_via_isotp", st == NOERROR and nm and nm[0][3:] == b"WCAN-ECU-SIM",
          f"status {st} {nm[0].hex() if nm else [d.hex() for c, d in t.rx][:2]}")
    t.rx.clear()
    await t.write(ich, iso_msg([0x22, 0xF1, 0x90]))
    await t.collect_rx(1.5)
    vin = [d for c, d in t.rx if c == ich and d[:3] == b"\x62\xF1\x90"]
    warn("L4_uds_vin_22F190", bool(vin) and vin[0][3:20] == VIN,
         f"{[d.hex() for c, d in t.rx][:2]} (the sim answered NRC 31 on 2026-09-21)")
    t.rx.clear()
    st, _, _ = await t.write(ich, iso_msg([0x10, 0x02]))
    await t.collect_rx(1.5)
    check("L4_uds_10_02", st == NOERROR and any(d[:2] == b"\x50\x02" for c, d in t.rx if c == ich),
          f"{[d.hex() for c, d in t.rx][:2]}")
    st, _, _ = await t.write(ich, iso_msg([0x34, 0x00, 0x44, 0x00, 0x00, 0x00, 0x04, 0x00]))
    check("L4_reflash_gate_not_supported", st == ERR_NOT_SUPPORTED, f"status {st}")

    # L5: second tester over TCP while BLE holds the session
    try:
        tcp = TcpTester(ip)
        st2 = tcp.call(HELLO)
        tcp.close()
        check("L5_second_tester_device_in_use", st2 == ERR_DEVICE_IN_USE, f"tcp HELLO status {st2:#x}")
    except Exception as e:  # noqa: BLE001
        check("L5_second_tester_device_in_use", False, f"{str(e)[:80]}")
    st, ver, _ = await t.call(HELLO)
    check("L5_ble_session_intact", st == NOERROR, f"status {st}")

    # L6: RTT
    lat = []
    for _ in range(30):
        t0 = time.perf_counter()
        await t.call(HELLO)
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    metric("hello_rtt_p50", statistics.median(lat), "ms")
    metric("hello_rtt_p95", lat[int(len(lat) * 0.95) - 1], "ms")
    lat = []
    for _ in range(10):
        t.rx.clear()
        t0 = time.perf_counter()
        await t.write(ich, iso_msg([0x22, 0xF1, 0x87]))
        # time to the FIRST RX_MSG (not the collect deadline: the first
        # run of this metric reported ~1.16 s = RTT + a 1 s collect window)
        end = time.monotonic() + 1.0
        while time.monotonic() < end and not any(d[:3] == b"\x62\xF1\x87" for c, d in t.rx):
            try:
                await t._read_frame(max(0.05, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
        if any(d[:3] == b"\x62\xF1\x87" for c, d in t.rx):
            lat.append((time.perf_counter() - t0) * 1000)
    if lat:
        metric("uds_request_to_rx_p50", statistics.median(lat), "ms")
    warn("L6_uds_rtt_sampled", len(lat) >= 8, f"{len(lat)}/10 answered")

    # L7: drop the link mid-session (no CLOSE)
    await client.disconnect()
    released = False
    for _ in range(10):
        await asyncio.sleep(0.5)
        wst, js = api_json(ip, "/api/j2534")
        if wst == 200 and js.get("client_connected") is False and js.get("transport") == "none":
            released = True
            break
    check("L7_session_released_on_drop", released, f"{js.get('client_connected')} {js.get('transport')}")
    try:
        tcp = TcpTester(ip)
        ok = tcp.call(HELLO) == NOERROR and tcp.call(OPEN) == NOERROR and tcp.call(CLOSE) == NOERROR
        tcp.close()
        check("L7_tcp_session_after_ble_drop", ok, "")
    except Exception as e:  # noqa: BLE001
        check("L7_tcp_session_after_ble_drop", False, f"{str(e)[:80]}")
    await asyncio.sleep(2)
    client, s, t = await open_session(name, identity, passkey)
    st, ver, _ = await t.call(HELLO)
    st2, _, _ = await t.call(OPEN)
    st3, _, _ = await t.call(CLOSE)
    check("L7_second_ble_session_clean", st == NOERROR and st2 == NOERROR and st3 == NOERROR,
          f"{st} {st2} {st3}")
    metric("stream_notifications", s.notifs, "count")
    await s.stop()
    await client.disconnect()


def stage_ble(passkey, nm_profile):
    saved = json.load(open(STATE))
    nip, st = find_dut(saved.get("ip"), saved["device_id"], window=60)
    saved["ip"] = nip or saved.get("ip")
    json.dump(saved, open(STATE, "w"))
    asyncio.run(ble_leg(saved, passkey, nm_profile))


# --------------------------------------------------------------------- restore
def stage_restore(hint):
    saved = json.load(open(STATE))
    device_id = saved["device_id"]
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
    for name, path in (("ble", "ble_manager"), ("j2534", "j2534_server"), ("ifm", "interface_manager")):
        check(f"restore_put_{name}", api(nip, f"/api/settings/{path}", "PUT", saved[name])[0] == 200)
    print("  restoring; submit + reboot...", flush=True)
    nip2, st2 = submit_and_wait(nip, device_id, st.get("boot_count"))
    check("restore_dut_back", nip2 is not None, f"ip={nip2}")
    if nip2:
        check("restore_j2534_setting", api(nip2, "/api/settings/j2534_server")[1].get("enabled") ==
              saved["j2534"].get("enabled"))
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
    a = ap.parse_args()
    ip = a.dut
    if a.stage in ("all", "configure"):
        ip, _ = find_dut(ip, None, window=30)
        if not ip:
            print("FATAL: DUT unreachable")
            return 1
        ip = stage_configure(ip, a.passkey) or ip
        saved = json.load(open(STATE)); saved["ip"] = ip; json.dump(saved, open(STATE, "w"))
        print("PROGRESS configure done", flush=True)
    if a.stage in ("all", "ble"):
        stage_ble(a.passkey, a.nm_profile)
        print("PROGRESS ble done", flush=True)
    if a.stage in ("all", "restore"):
        stage_restore(ip)
        print("PROGRESS restore done", flush=True)
    fails = [n for n, ok in results if not ok]
    if warns:
        print("WARN: " + ", ".join(warns), flush=True)
    if a.stage in ("all", "ble"):
        # the verdict line belongs to the BLE leg only (test.ps1 greps the
        # concatenated stage outputs for it)
        print(("BLE J2534 FAIL: " + ", ".join(fails)) if fails else "BLE J2534 PASS", flush=True)
    else:
        print(f"STAGE {a.stage} " + ("FAIL: " + ", ".join(fails) if fails else "OK"), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
