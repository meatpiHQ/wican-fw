#!/usr/bin/env python3
"""J1850 VPW (GM Class 2) through the J2534 server  ->  J2534 VPW PASS / SKIP / FAIL

VPW rides the OBD chip, not the native CAN controller: CONNECT runs the VPW
init on the chip, a J1850 message is [3-byte header][payload] whole in the
PASSTHRU_MSG data (no CAN-id prefix), and the chip's reply lines come back as
RX messages through the channel's PASS filters. Wire protocol:
J2534_WIRE_PROTOCOL.md.

Two ways to run it:

  with the rig (--rig <dut http>, the stage `.\\test.ps1 j2534vpw`)
      ECU sim v2 is put on VPW alone (every other bus off, the way one car
      is), the unit's J2534 server is switched on for the run when it is off
      (one planned restart, and one more to put it back), and a reply is
      REQUIRED: no reply is a FAIL. Two functional requests (01 00, then
      01 0C over the cached header), each answered `48 6B 10 41 ..`; the
      chip's memory compared before and after (chip_nvm: nothing written);
      the simulator and the unit's settings as found at the end.
  bare (<dut_ip> [--port 6809], on rpi001 or through a tunnel)
      against whatever is on the bus: `J2534 VPW PASS` when a J1850 node
      answers, `J2534 VPW SKIP (...)` when connect, init and write worked and
      nothing answered, `J2534 VPW FAIL` only on a real defect. Needs
      j2534_server enabled with allow_lan.

Until 2026-11-11 the reply check had never run against a VPW node (the first
simulator was CAN-only), and the channel could not hear one: its init sent
ATTP2 and THEN `ATD`, which puts the chip back on its stored protocol (the
request went out on CAN), and three ST pass filters behind it that let no
reply through on this chip. This bench's own PASS filter asked for replies
to F0 while its request (68 6A F1, functional) is answered `48 6B 10 ..`: it
passes the functional reply target 6B now.

usage: j2534_vpw_bench.py <j2534 host> [--port 6809] [--rig localhost:18081]
  the stage's form: j2534_vpw_bench.py localhost --port 18534 --rig localhost:18081
  (two ssh forwards: 18081 -> the DUT's port 80, 18534 -> its port 6809).
About 1 min with the rig: one simulator restart each way, two planned
restarts of the unit when its J2534 server is off as found.
"""
import argparse
import json
import os
import socket
import struct
import sys
import time
import urllib.request

MAGIC = 0x4A35
HDR = struct.Struct("<HBBHHI")   # magic, ver, type, seq, channel, length
MSG = struct.Struct("<IIIIII")   # protocol, rx_status, tx_flags, ts, extra, size
(HELLO, OPEN, CLOSE, CONNECT, DISCONNECT,
 WRITE_MSGS, START_FILTER, ACK, RX_MSG) = (
    0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x11, 0x80, 0x81)
PROT_J1850VPW = 1
PASS_FILTER = 1
NOERROR, ERR_NOT_SUPPORTED, ERR_FAILED = 0x00, 0x01, 0x07
RX_TX_MSG_TYPE, RX_TX_INDICATION = 0x01, 0x08

# an OBD-II functional request over VPW: priority 0x68, target 0x6A (all OBD
# modules), source 0xF1 (the tester). The answer's header is 48 6B <ecu>:
# its target is the functional reply address 6B, not the tester's
REQ_HDR = bytes([0x68, 0x6A, 0xF1])
REPLY_TARGET = 0x6B
REQUESTS = [(bytes([0x01, 0x00]), bytes([0x41, 0x00])),    # supported PIDs
            (bytes([0x01, 0x0C]), bytes([0x41, 0x0C]))]    # engine speed, the cached header

fails = []
PLANNED = [0]


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""), flush=True)
    if not ok:
        fails.append(name)
    return ok


class Client:
    def __init__(self, ip, port):
        self.s = socket.create_connection((ip, port), timeout=5)
        self.seq = 0

    def _recv(self, n):
        b = b""
        while len(b) < n:
            chunk = self.s.recv(n - len(b))
            if not chunk:
                raise EOFError
            b += chunk
        return b

    def _read_frame(self):
        magic, ver, rt, rseq, rch, rlen = HDR.unpack(self._recv(HDR.size))
        assert magic == MAGIC, "bad magic"
        return rt, rseq, rch, self._recv(rlen)

    def call(self, mtype, channel=0, payload=b""):
        self.seq = (self.seq + 1) & 0xFFFF
        self.s.sendall(HDR.pack(MAGIC, 1, mtype, self.seq, channel,
                                len(payload)) + payload)
        while True:
            rt, rseq, rch, body = self._read_frame()
            if rt == ACK:
                break
        status = struct.unpack_from("<I", body, 0)[0]
        result = (struct.unpack_from("<I", body, 4)[0]
                  if len(body) >= 8 else None)
        return status, result, rch

    def vpw_write(self, channel, hdr, payload, collect_ms=2000):
        """WRITE one J1850 message ([header][payload]) and collect replies:
        the RX_MSG data of received messages (not the TxDone echo)."""
        data = bytes(hdr) + bytes(payload)
        msg = MSG.pack(PROT_J1850VPW, 0, 0, 0, 0, len(data)) + data
        st, _, _ = self.call(WRITE_MSGS, channel, struct.pack("<I", 1) + msg)
        rxs = []
        self.s.settimeout(collect_ms / 1000.0)
        try:
            while True:
                rt, _, _, body = self._read_frame()
                if rt != RX_MSG:
                    continue
                fields = MSG.unpack_from(body, 0)
                rx_status, size = fields[1], fields[5]
                if rx_status & (RX_TX_MSG_TYPE | RX_TX_INDICATION):
                    continue   # the TxDone echo, not an answer
                rxs.append(body[MSG.size:MSG.size + size])
        except (socket.timeout, TimeoutError):
            pass
        finally:
            self.s.settimeout(5)
        return st, rxs


def exchange(host, port, need_reply):
    """The J2534 session. Returns 'pass', 'skip' or 'fail'."""
    try:
        c = Client(host, port)
    except OSError as e:
        check("the J2534 port answers", False, f"{host}:{port} ({e})")
        return "fail"

    st, ver, _ = c.call(HELLO)
    check("HELLO", st == NOERROR and ver == 1, f"wire v{ver}")
    st, devid, _ = c.call(OPEN)
    check("OPEN", st == NOERROR, f"device id {devid}")

    # CONNECT J1850 VPW: the device runs the VPW init on the OBD chip
    st, ch, _ = c.call(CONNECT, payload=struct.pack("<III", PROT_J1850VPW, 0, 0))

    if st == ERR_FAILED and not need_reply:
        print("J2534 VPW SKIP (the OBD chip is not ready on this device; "
              "CONNECT returned ERR_FAILED)")
        c.call(CLOSE)
        return "skip"

    check("CONNECT J1850VPW", st == NOERROR and ch >= 1, f"status {hex(st)} ch {ch}")
    if st != NOERROR:
        c.call(CLOSE)
        return "fail"

    # a PASS filter on the functional reply target 6B (byte 1 of the reply
    # header): mask 00 FF 00, pattern 00 6B 00 over the 3 header bytes
    mask = MSG.pack(PROT_J1850VPW, 0, 0, 0, 0, 3) + bytes([0x00, 0xFF, 0x00])
    patt = MSG.pack(PROT_J1850VPW, 0, 0, 0, 0, 3) + bytes([0x00, REPLY_TARGET, 0x00])
    st, fid, _ = c.call(START_FILTER, ch,
                        struct.pack("<I", PASS_FILTER) + mask + patt)
    check("START_FILTER PASS (functional replies, target 6B)", st == NOERROR, f"status {hex(st)}")

    answered = 0
    for payload, want in REQUESTS:
        name = payload.hex(" ").upper()
        st, rxs = c.vpw_write(ch, REQ_HDR, payload)
        check(f"WRITE_MSGS {name} accepted", st == NOERROR, f"status {hex(st)}")
        if not rxs:
            if need_reply:
                check(f"{name} answered by the VPW node", False, "no reply in 2 s")
            continue
        answered += 1
        first = rxs[0]
        # header (48 6B <ecu>) + the positive response to the request
        ok = (len(first) >= 5 and first[1] == REPLY_TARGET and first[3:5] == want)
        check(f"{name}: a J1850 reply with its header and {want.hex(' ').upper()}", ok,
              f"{len(rxs)} message(s), first {first.hex(' ').upper()}")

    c.call(DISCONNECT, channel=ch)
    c.call(CLOSE)

    if fails:
        return "fail"
    if answered == 0:
        print("J2534 VPW SKIP (connect + VPW init + write reached the chip; "
              "no J1850 node answered. With ECU sim v2 on the bus run it with "
              "--rig, which puts the simulator on VPW and requires the reply)")
        return "skip"
    return "pass"


# ---- the rig ---------------------------------------------------------------

def rig_run(args):
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "lib"))
    sys.path.insert(0, os.path.join(here, "..", "obd"))
    import bench_bus  # noqa: E402  (the simulator's settings)
    import chip_nvm   # noqa: E402  (the chip's stored cells)

    dut = "http://" + args.rig

    def http(path, method="GET", body=None, timeout=15):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(dut + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode(errors="replace")
                try:
                    return r.status, json.loads(text)
                except ValueError:
                    return r.status, text
        except Exception as exc:  # noqa: BLE001
            return 0, str(exc)

    def settings(name):
        st, cur = http("/api/settings/" + name)
        if st != 200 or not isinstance(cur, dict):
            return None
        return {k: v for k, v in cur.items() if k not in ("degraded", "pending_reboot")}

    def guard():
        st, oc = http("/api/obd_chip")
        return oc.get("eeprom_guard", {}) if isinstance(oc, dict) else {}

    def apply_j2534(body):
        """staged and submitted: one planned restart; True = back with the chip ready"""
        st, r = http("/api/settings/j2534_server", "PUT", body)
        if st != 200:
            return False
        st, h = http("/api/restart/history")
        b0 = h.get("boot_count") if isinstance(h, dict) else None
        http("/api/settings/submit", "POST", {})
        PLANNED[0] += 1
        time.sleep(6)
        t0 = time.time()
        while time.time() - t0 < 150:
            st, h = http("/api/restart/history", timeout=4)
            st2, oc = http("/api/obd_chip", timeout=4)
            if (isinstance(h, dict) and h.get("boot_count") not in (None, b0) and
                    isinstance(oc, dict) and oc.get("ready")):
                return True
            time.sleep(1)
        return False

    j0 = settings("j2534_server")
    if not check("the unit answers and its chip is ready", j0 is not None and
                 bool((http("/api/obd_chip")[1] or {}).get("ready")), args.rig):
        return "fail"
    st, h0 = http("/api/restart/history")
    g0 = guard()
    snap0 = chip_nvm.snapshot(args.rig)
    print("as found: j2534_server %s, chip store %s, %d memory cells read" % (
        json.dumps(j0), snap0[chip_nvm.STORED_PROTOCOL], len(snap0)), flush=True)

    previous = None
    result = "fail"
    try:
        previous, running = bench_bus.sim_only("vpw")
        live = bench_bus.sim_bus_live(running)
        check("ECU sim v2 answers on VPW alone", live == ["vpw"], " ".join(live))
        if not (j0.get("enabled") and j0.get("allow_lan")):
            j1 = dict(j0)
            j1.update({"enabled": True, "allow_lan": True})
            check("the J2534 server switched on for the run", apply_j2534(j1))
            time.sleep(3)
        result = exchange(args.dut, args.port, need_reply=True)
    finally:
        if settings("j2534_server") != j0:
            apply_j2534(j0)
        check("the unit's J2534 settings as found", settings("j2534_server") == j0)
        if previous is not None:
            bench_bus.sim_patch(previous)
        back = bench_bus.sim_bus_live(bench_bus.sim_status())
        check("the simulator back on its buses", len(back) > 1, " ".join(back))

    snap1 = chip_nvm.snapshot(args.rig)
    changes = chip_nvm.diff(snap0, snap1)
    chip_nvm.show_diff(changes)
    g1 = guard()
    check("nothing wrote the chip (the memory snapshot the same, no protocol save)",
          not changes and g1.get("protocol_saves") == 0 and g1.get("stored_protocol") == g0.get("stored_protocol"),
          "%d of %d cells changed, protocol_saves %s, store %s" % (
              len(changes), len(snap0), g1.get("protocol_saves"), g1.get("stored_protocol")))
    st, h1 = http("/api/restart/history")
    check("the unit rode through", isinstance(h0, dict) and isinstance(h1, dict) and
          h1.get("boot_count") == h0.get("boot_count") + PLANNED[0] and
          h1.get("unexpected_resets") == h0.get("unexpected_resets"),
          "boot %s -> %s with %d planned" % ((h0 or {}).get("boot_count"), (h1 or {}).get("boot_count"), PLANNED[0]))
    return "fail" if fails else result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dut", help="where the J2534 port answers (the DUT, or localhost through a tunnel)")
    ap.add_argument("--port", type=int, default=6809)
    ap.add_argument("--rig", default="", help="the DUT's HTTP (host:port): the simulator on VPW, a reply required")
    args = ap.parse_args()

    result = rig_run(args) if args.rig else exchange(args.dut, args.port, need_reply=False)

    if result == "fail" or fails:
        print("J2534 VPW FAIL (%d failed): %s" % (len(fails), ", ".join(fails)))
        return 1
    if result == "skip":
        return 0
    print("J2534 VPW PASS (%d planned restart%s)" % (PLANNED[0], "" if PLANNED[0] == 1 else "s"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
