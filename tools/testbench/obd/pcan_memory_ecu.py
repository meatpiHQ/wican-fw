#!/usr/bin/env python3
"""A PCAN-hosted UDS "memory ECU" for the large-payload benches: the same
synthetic memory the WiCAN ECU Simulator serves (byte at address A =
A & 0xFF), on an address the simulator does not own, with a real ISO-TP
stack (python-can + can-isotp) so flow control, STmin and sequence numbers
are standards-checked on the PC side.

  0x23 ReadMemoryByAddress  -> 63 + pattern (up to 4094 bytes)
  0x3D WriteMemoryByAddress -> 7D + echo when every data byte matches the
                               pattern, NRC 0x13 on a length mismatch,
                               NRC 0x72 on a content mismatch
  0x36 TransferData         -> 76 <bsc> when the block data is pattern(0,n)
                               (the MTPS-OBD tester's shape), NRC 0x72 else;
                               --pending N sends N x `7F 36 78` first, as
                               the tester box does (`7E87F3678`, `7E87600`)
  0x3E TesterPresent        -> 7E 00
  anything else             -> NRC 0x11

usage: pcan_memory_ecu.py [--pcan PCAN_USBBUS2] [--rxid 0x7E4] [--txid 0x7EC]
                          [--secs 120] [--stmin 0] [--bs 0]
Prints one `ECU {json}` line per request served and `ECU DONE {stats}`.
Run it on the PC with the v6.0.2 IDF venv python (python-can + isotp).
"""
import argparse
import json
import sys
import time

import can
import isotp


def pattern(addr, n):
    return bytes((addr + i) & 0xFF for i in range(n))


def parse_alfid(req):
    """23/3D <ALFID> <addr> <size> -> (addr, size, header_len) or None."""
    if len(req) < 2:
        return None
    alen, slen = req[1] & 0x0F, (req[1] >> 4) & 0x0F
    if alen == 0 or slen == 0 or len(req) < 2 + alen + slen:
        return None
    addr = int.from_bytes(req[2:2 + alen], "big")
    size = int.from_bytes(req[2 + alen:2 + alen + slen], "big")
    return addr, size, 2 + alen + slen


def handle(req, stats):
    sid = req[0]
    if sid == 0x3E:
        return bytes([0x7E, 0x00])
    if sid == 0x36:
        # TransferData, the MTPS-OBD tester's block shape: 36 <bsc> <data>;
        # the data must be pattern(0, len) - the same "memory" as 0x23/0x3D
        if len(req) < 2:
            return bytes([0x7F, sid, 0x13])
        data = req[2:]
        if data != pattern(0, len(data)):
            bad = next(i for i in range(len(data)) if data[i] != i & 0xFF)
            stats["write_content_errors"] += 1
            stats["last_bad"] = bad
            return bytes([0x7F, sid, 0x72])
        stats["writes"] += 1
        stats["write_bytes"] += len(data)
        return bytes([0x76, req[1]])
    if sid == 0x23:
        p = parse_alfid(req)
        if p is None:
            return bytes([0x7F, sid, 0x13])
        addr, size, _ = p
        size = min(size, 4094)
        stats["reads"] += 1
        stats["read_bytes"] += size
        return bytes([0x63]) + pattern(addr, size)
    if sid == 0x3D:
        p = parse_alfid(req)
        if p is None:
            return bytes([0x7F, sid, 0x13])
        addr, size, hdr = p
        data = req[hdr:]
        if len(data) != size:
            stats["write_len_errors"] += 1
            return bytes([0x7F, sid, 0x13])
        if data != pattern(addr, size):
            bad = next(i for i in range(size) if data[i] != (addr + i) & 0xFF)
            stats["write_content_errors"] += 1
            stats["last_bad"] = bad
            return bytes([0x7F, sid, 0x72])
        stats["writes"] += 1
        stats["write_bytes"] += size
        return bytes([0x7D]) + req[1:hdr]
    return bytes([0x7F, sid, 0x11])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--rxid", default="0x7E4")
    ap.add_argument("--txid", default="0x7EC")
    ap.add_argument("--secs", type=float, default=120)
    ap.add_argument("--stmin", type=int, default=0)
    ap.add_argument("--bs", type=int, default=0)
    ap.add_argument("--pending", type=int, default=1,
                    help="7F xx 78 responsePending frames before a 36/3D "
                         "positive (the tester box sends one)")
    a = ap.parse_args()
    rxid, txid = int(a.rxid, 16), int(a.txid, 16)

    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    addr = isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=rxid,
                         txid=txid)
    errors = []
    stack = isotp.CanStack(bus, address=addr,
                           error_handler=lambda e: errors.append(
                               f"{e.__class__.__name__}: {e}"),
                           params={"stmin": a.stmin, "blocksize": a.bs,
                                   "tx_padding": 0x00, "rx_flowcontrol_timeout": 1000,
                                   "rx_consecutive_frame_timeout": 1000,
                                   "max_frame_size": 4095,
                                   "wftmax": 4})
    stats = {"reads": 0, "read_bytes": 0, "writes": 0, "write_bytes": 0,
             "write_len_errors": 0, "write_content_errors": 0,
             "last_bad": None, "requests": 0}
    print(f"ECU READY rx=0x{rxid:03X} tx=0x{txid:03X} on {a.pcan}", flush=True)
    t0 = time.time()
    while time.time() - t0 < a.secs:
        stack.process()
        if stack.available():
            req = stack.recv()
            stats["requests"] += 1
            t1 = time.perf_counter()
            resp = handle(req, stats)
            if req[0] in (0x36, 0x3D) and resp[:1] != b"\x7F":
                for _ in range(a.pending):
                    stack.send(bytes([0x7F, req[0], 0x78]))
                    while stack.transmitting():
                        stack.process()
                        time.sleep(0.0005)
                    time.sleep(0.02)
            stack.send(resp)
            while stack.transmitting():
                stack.process()
                time.sleep(0.0005)
            print("ECU " + json.dumps({
                "sid": f"{req[0]:02X}", "req_len": len(req),
                "resp_len": len(resp), "tx_ms": round((time.perf_counter() - t1) * 1000, 1),
                "errors": errors[-3:]}), flush=True)
        time.sleep(0.0005)
    stats["isotp_errors"] = errors[:10]
    print("ECU DONE " + json.dumps(stats), flush=True)
    bus.shutdown()


if __name__ == "__main__":
    sys.exit(main())
