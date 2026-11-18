#!/usr/bin/env python3
"""The Bluetooth data pipe under overload, the central's half (runs ON rpi001).

The PC half is ble_overload_bench.py: it owns the PCAN, starts this script
through ble_pi_run.py, reads the MARK lines below to switch its floods on and
off, and makes the verdict from the OVL lines. Run by hand this script only
shows what the central saw.

The bridge is `can <-slcan-> ble` on the data pipe (FFF1 notify, FFF2 write):
slcan text, one line per frame. Its configure and restore are those of
ble_can_raw_pi.py with the translator changed, and AutoPID is off for the run
so that the bus carries the bench's frames alone. slcan and not the binary
records on purpose: the bridge hands a translated stream over in units of up
to 1 KB, which is where a full queue can cut one (2026-11-18: with the raw
bridge the units are a record or two, and a packet of 490 bytes holds exactly
35 of its 14-byte records, so neither defect showed).

  configure : AutoPID off, then ble_can_raw_pi's configure (BLE on, the bridge,
              the station kept beside Bluetooth), one submit.
  ble       : pair, subscribe FFF1, then
              leg S  MARK s_on .. s_end: five lines written on a quiet bus,
                     the control (the PC must hear their frames).
              leg T  MARK t_on .. t_off: the PC floods the bus with more frames
                     than the link carries. Every notification is recorded:
                     bytes, lines, the frame numbers, lines that are not
                     the flood's, lines that are no slcan line at all. The unit's
                     counters before and after (`pipe` on /api/ble, /api/can,
                     the bridge). All through it this side writes one line
                     every half second WITH response and times the answer.
              leg R  MARK r_on .. r_off: the PC keeps the bus full with a frame
                     that wins every arbitration, so the unit cannot send (and
                     the pipe toward this side is overloaded all the while).
                     This side writes slcan lines to FFF2 WITH response, whole
                     lines per write, times each answer and notes a write the
                     unit refused. The CAN interface's counters before and
                     after: what it was handed, sent, gave up on.
              At the end: the unit's own log lines about Bluetooth.
  restore   : AutoPID as found, then ble_can_raw_pi's restore.

Usage (on rpi001, through ble_pi_run.py):
  python3 -u ble_overload_pi.py --dut <the unit> [--stage configure|ble|restore]
"""
import argparse
import asyncio
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ble_can_raw_pi as raw  # noqa: E402
from ble_bench import UUID_FFF1, UUID_FFF2  # noqa: E402
from ble_link import (api, bt_identity, btmon_start, clean, connect, find_dut,  # noqa: E402
                      prep_adapter, sh)

raw.STATE = "/tmp/ble_overload_bench.json"
raw.BRIDGE = {"name": "br_ble_can", "a": "can", "b": "ble", "translator": "slcan", "enabled": True}
AUTOPID_STATE = "/tmp/ble_overload_autopid.json"
BTSNOOP = "/tmp/ble_overload.btsnoop"
FLOOD_HEAD = b"t1238"   # leg T: the PC's frames, id 123, 8 data bytes, the first 4 = the frame number
FLOOD_LINE = 21         # such a line without its CR
WRITE_LINE = 14         # this side's frames: `t7004` + 8 hex digits (the frame number) + CR
S_BASE, T_BASE = 1000000, 2000000   # the numbers of leg S's and leg T's own lines (leg R: from 0)
HEX = set(b"0123456789ABCDEFabcdef")


def line(num):
    return b"t7004" + struct.pack("<I", num).hex().upper().encode() + b"\r"


def wellformed(text):
    """a complete slcan data line of any frame: t iii l dd.. or T iiiiiiii l dd.."""
    idl = 3 if text[:1] == b"t" else 8 if text[:1] == b"T" else 0
    if idl == 0 or len(text) < 2 + idl or not all(c in HEX for c in text[1:]):
        return False
    dlc = text[1 + idl] - 0x30
    return 0 <= dlc <= 8 and len(text) == 2 + idl + 2 * dlc


class Lines:
    """The central's side of the stream: slcan lines, split at CR."""

    def __init__(self):
        self.buf = bytearray()
        self.bytes = 0
        self.notifs = 0
        self.nums = []          # the frame numbers of the flood's lines, as they came
        self.foreign = 0        # whole slcan lines of another frame
        self.malformed = 0      # what ends in a CR and is no slcan line
        self.samples = []       # the first few of those
        self.others = []        # the first few lines of another frame
        self.t_first = self.t_last = None

    def feed(self, chunk, t):
        self.notifs += 1
        self.bytes += len(chunk)
        self.t_first = self.t_first or t
        self.t_last = t
        self.buf.extend(chunk)
        while True:
            i = self.buf.find(b"\r")
            if i < 0:
                return
            text = bytes(self.buf[:i])
            del self.buf[:i + 1]
            if len(text) == FLOOD_LINE and text[:5] == FLOOD_HEAD and wellformed(text):
                self.nums.append(struct.unpack("<I", bytes.fromhex(text[5:13].decode()))[0])
            elif wellformed(text):
                self.foreign += 1
                if len(self.others) < 8:
                    self.others.append(text.decode(errors="replace"))
            else:
                self.malformed += 1
                if len(self.samples) < 4:
                    self.samples.append(text[:60].decode(errors="replace"))


def pipe(ip):
    _, b = api(ip, "/api/ble", timeout=6)
    return (b.get("pipe") or {}) if isinstance(b, dict) else {}


def delta(a, b, keys=None):
    return {k: b.get(k, 0) - a.get(k, 0) for k in (keys or b)
            if isinstance(b.get(k, 0), (int, float)) and not isinstance(b.get(k, 0), bool)}


def stage_configure(ip, passkey):
    if not os.path.exists(AUTOPID_STATE):
        json.dump(clean(api(ip, "/api/settings/autopid")[1]), open(AUTOPID_STATE, "w"))
    found = json.load(open(AUTOPID_STATE))
    if found.get("enabled"):
        off = dict(found)
        off["enabled"] = False
        raw.check("configure_autopid_off", api(ip, "/api/settings/autopid", "PUT", off)[0] == 200)
    return raw.stage_configure(ip, passkey)


def stage_restore(hint, nm_profile):
    saved = json.load(open(raw.STATE))
    nip, _ = find_dut(saved.get("ip") or hint, saved["device_id"], window=150)
    if nip and os.path.exists(AUTOPID_STATE):
        found = json.load(open(AUTOPID_STATE))
        raw.check("restore_put_autopid", api(nip, "/api/settings/autopid", "PUT", found)[0] == 200)
    raw.stage_restore(hint, nm_profile)
    if nip and os.path.exists(AUTOPID_STATE) and not os.path.exists(raw.STATE):
        back = clean(api(find_dut(nip, saved["device_id"], window=60)[0] or nip, "/api/settings/autopid")[1])
        raw.check("restore_autopid_as_found", back == json.load(open(AUTOPID_STATE)), "")
        os.remove(AUTOPID_STATE)


async def leg_s(client):
    print("MARK s_on", flush=True)
    await asyncio.sleep(0.5)
    acked = 0
    for i in range(5):
        try:
            await client.write_gatt_char(UUID_FFF2, line(S_BASE + i), response=True)
            acked += 1
        except Exception as e:  # noqa: BLE001
            print("  leg S write %d: %s" % (i, (str(e) or type(e).__name__)[:90]), flush=True)
        await asyncio.sleep(0.2)
    await asyncio.sleep(1.0)
    print("OVL S " + json.dumps({"writes": 5, "acked": acked}), flush=True)
    print("MARK s_end", flush=True)


async def leg_t(ip, client, p, secs):
    loop = asyncio.get_running_loop()
    b0, c0, br0 = pipe(ip), raw.can_counters(ip), raw.bridge_stats(ip)
    n0, bytes0, mal0, notifs0, for0 = len(p.nums), p.bytes, p.malformed, p.notifs, p.foreign
    p.t_first = None
    acks, errs, wrote = [], {}, [0]
    flood_over = asyncio.Event()

    async def writer():
        await asyncio.sleep(2.0)                   # the pipe is behind by then
        while not flood_over.is_set():
            a = loop.time()
            num = T_BASE + wrote[0]
            wrote[0] += 1
            try:
                await asyncio.wait_for(client.write_gatt_char(UUID_FFF2, line(num), response=True), 8.0)
                acks.append(round(loop.time() - a, 3))
            except Exception as e:  # noqa: BLE001
                key = (str(e) or type(e).__name__)[:90]
                errs[key] = errs.get(key, 0) + 1
            await asyncio.sleep(0.5)

    print("MARK t_on", flush=True)
    t0 = loop.time()
    task = asyncio.ensure_future(writer())
    await asyncio.sleep(secs)
    flood_over.set()
    await task
    print("MARK t_off", flush=True)
    await asyncio.sleep(5.0)                       # what the unit still holds arrives
    b1, c1, br1 = pipe(ip), raw.can_counters(ip), raw.bridge_stats(ip)
    nums = p.nums[n0:]
    span = (p.t_last - p.t_first) if p.t_first else 0.0
    out = {"secs": round(loop.time() - t0, 1), "bytes": p.bytes - bytes0, "notifs": p.notifs - notifs0,
           "flood_lines": len(nums), "foreign": p.foreign - for0,
           "malformed": p.malformed - mal0, "samples": p.samples, "others": p.others, "leftover": len(p.buf),
           "writes": wrote[0], "acked": len(acks), "slowest_ack_s": max(acks) if acks else None,
           "write_errors": errs,
           "first": nums[0] if nums else None, "last": nums[-1] if nums else None,
           "unique": len(set(nums)), "in_order": all(b > a for a, b in zip(nums, nums[1:])),
           "link_bytes_per_s": round((p.bytes - bytes0) / span) if span > 0 else 0,
           "pipe_has_counters": bool(b1), "pipe": delta(b0, b1),
           "can_rx": c1.get("rx", 0) - c0.get("rx", 0),
           "bridge": delta(br0, br1)}
    print("OVL T " + json.dumps(out), flush=True)
    print("MARK t_end", flush=True)


async def leg_r(ip, client, cap, writes):
    b0, c0, br0 = pipe(ip), raw.can_counters(ip), raw.bridge_stats(ip)
    per = max(1, cap // WRITE_LINE)
    print("MARK r_on", flush=True)
    await asyncio.sleep(1.5)                       # the flood owns the bus now
    refused, errors, slowest = [], {}, 0.0
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    for w in range(writes):
        payload = b"".join(line(i) for i in range(w * per, (w + 1) * per))
        a = loop.time()
        try:
            await client.write_gatt_char(UUID_FFF2, payload, response=True)
            slowest = max(slowest, loop.time() - a)
        except Exception as e:  # noqa: BLE001
            if not client.is_connected:
                raise
            refused.append(w)
            key = (str(e) or type(e).__name__)[:90]
            errors[key] = errors.get(key, 0) + 1
    took = loop.time() - t0
    print("MARK r_off", flush=True)
    await asyncio.sleep(6.0)                       # the unit sends what it holds
    b1, c1, br1 = pipe(ip), raw.can_counters(ip), raw.bridge_stats(ip)
    out = {"per_write": per, "writes": writes, "refused": refused, "errors": errors,
           "write_secs": round(took, 1), "lines_per_s": round(writes * per / took) if took > 0 else 0,
           "slowest_ack_s": round(slowest, 3),
           "pipe_has_counters": bool(b1), "pipe": delta(b0, b1),
           "can": delta(c0, c1, ("tx", "tx_done", "tx_lost", "tx_retries", "arb_lost", "tx_refused",
                                 "tx_errors")),
           "bridge": delta(br0, br1)}
    print("OVL R " + json.dumps(out), flush=True)
    print("MARK r_end", flush=True)


async def ble_leg(saved, passkey, nm_profile, a):
    ip = saved["ip"]
    identity = bt_identity(saved["sta_mac"])
    prep_adapter(identity, nm_profile, drop_bond=True)
    client, cap = await connect(f"WiC_{saved['device_id']}", identity, passkey, bonded=False)
    p = Lines()
    mon = btmon_start(BTSNOOP) if a.btmon else None
    loop = asyncio.get_running_loop()

    def on_notify(_, data):
        p.feed(bytes(data), loop.time())
    try:
        await client.start_notify(UUID_FFF1, on_notify, use_notify_acquire=True)
    except Exception:  # noqa: BLE001
        await client.start_notify(UUID_FFF1, on_notify)
    await asyncio.sleep(1.0)
    levels = {}
    for item in [x for x in a.log_level.split(",") if x]:
        # a log level for the run (it lasts until the unit's next restart,
        # which the restore stage is): how much does the console cost?
        tag, _, level = item.partition("=")
        st, _ = api(ip, "/api/logs/level", "PUT", {"tag": tag, "level": level})
        levels[tag] = level if st == 200 else "refused (%s)" % st
    print("OVL LINK " + json.dumps({"cap": cap, "pipe_has_counters": bool(pipe(ip)),
                                    "log_levels": levels}), flush=True)
    try:
        if "s" in a.legs:
            await leg_s(client)
        if "t" in a.legs:
            await leg_t(ip, client, p, a.t_secs)
        if "r" in a.legs:
            await leg_r(ip, client, cap, a.r_writes)
        print("OVL END " + json.dumps({"connected": bool(client.is_connected)}), flush=True)
    except Exception as e:  # noqa: BLE001
        print("OVL ERROR " + json.dumps({"error": (str(e) or type(e).__name__)[:200],
                                         "connected": bool(client.is_connected)}), flush=True)
    finally:
        print("MARK stop", flush=True)
        if mon is not None:
            # what the central's own radio did with its writes: sent, taken
            # by the unit's radio (Number of Completed Packets), answered
            mon.terminate()
            await asyncio.sleep(0.5)
            keep = ("< ACL Data TX", "ATT: Write", "ATT: Error", "Number of Completed", "Count:",
                    "Disconnect", "Reason:", "Handle Value Confirmation")
            rows = [x.rstrip() for x in sh(f"btmon -r {BTSNOOP} 2>/dev/null").splitlines()
                    if any(k in x for k in keep)]
            for x in rows[-70:]:
                print("  hci| " + x[:150], flush=True)
        try:
            await client.stop_notify(UUID_FFF1)
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(1.5)
        st, txt = api(ip, "/api/logs/ring", timeout=15, raw=True)
        if st == 200:
            own = [x for x in txt.decode(errors="replace").splitlines()
                   if "ble_manager" in x or "NimBLE" in x or "bridge" in x]
            for x in own[-24:]:
                print("  unit| " + x[:200], flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="10.42.1.194")
    ap.add_argument("--stage", choices=["configure", "ble", "restore"], required=True)
    ap.add_argument("--passkey", type=int, default=421337)
    ap.add_argument("--nm-profile", default="wican-bench-ap")
    ap.add_argument("--legs", default="str")
    ap.add_argument("--t-secs", type=float, default=12.0)
    ap.add_argument("--r-writes", type=int, default=60)
    ap.add_argument("--btmon", action="store_true", help="print what the central's radio did with its writes")
    ap.add_argument("--log-level", default="",
                    help="log levels for the run, tag=level[,tag=level] ('*' = every tag): e.g. '*=none'")
    a = ap.parse_args()
    if a.stage == "configure":
        ip, _ = find_dut(a.dut, None, window=30)
        if not ip:
            print("FATAL: DUT unreachable", flush=True)
            return 1
        ip = stage_configure(ip, a.passkey) or ip
        saved = json.load(open(raw.STATE))
        saved["ip"] = ip
        json.dump(saved, open(raw.STATE, "w"))
    elif a.stage == "ble":
        saved = json.load(open(raw.STATE))
        nip, _ = find_dut(saved.get("ip") or a.dut, saved["device_id"], window=90)
        saved["ip"] = nip or saved.get("ip")
        json.dump(saved, open(raw.STATE, "w"))
        asyncio.run(ble_leg(saved, a.passkey, a.nm_profile, a))
    else:
        stage_restore(a.dut, a.nm_profile)
    fails = [n for n, ok in raw.results if not ok]
    print(f"STAGE {a.stage} " + ("FAIL: " + ", ".join(fails) if fails else "OK"), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
