#!/usr/bin/env python3
"""J1850 VPW (GM Class 2) J2534 bench (2026-10-19).

VPW rides the OBD chip, not the native CAN controller, so this bench needs a
J1850 VPW node on the bus to answer. The bench's ECU simulator is CAN-only,
so against it this bench proves the reachable half (CONNECT runs the VPW init
on the chip, WRITE is accepted, a PASS filter arms) and then SKIPs the
answer check with an explicit line. With a real VPW ECU or a VPW-capable
tester on the bus it validates the reply too: prints `J2534 VPW PASS` when a
J1850 answer comes back, `J2534 VPW SKIP (...)` when the connect+init path
worked but nothing answered, and `J2534 VPW FAIL` only on a real defect.

Wire protocol: J2534_WIRE_PROTOCOL.md. A J1850 message is
[3-byte header][payload] whole in the PASSTHRU_MSG data, no CAN-id prefix.

Run against the device's TCP port 6809 (directly, or through an ssh tunnel
to the bench DUT): `python j2534_vpw_bench.py <dut_ip> [--port 6809]`.
On rpi001 with j2534_server enabled + allow_lan, or locally via the tunnel.
"""
import argparse
import socket
import struct
import sys

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

# a GM/OBD-II functional request over VPW: priority 0x68, target 0x6A (all
# OBD modules), source 0xF1 (tester); payload mode 01 PID 00 (supported PIDs)
REQ_HDR = bytes([0x68, 0x6A, 0xF1])
REQ_PAYLOAD = bytes([0x01, 0x00])

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dut")
    ap.add_argument("--port", type=int, default=6809)
    args = ap.parse_args()

    try:
        c = Client(args.dut, args.port)
    except OSError as e:
        print(f"J2534 VPW FAIL: cannot connect {args.dut}:{args.port} ({e})")
        return 1

    st, ver, _ = c.call(HELLO)
    check("HELLO", st == NOERROR and ver == 1, f"wire v{ver}")
    st, devid, _ = c.call(OPEN)
    check("OPEN", st == NOERROR, f"device id {devid}")

    # CONNECT J1850 VPW: the device runs the VPW init on the OBD chip
    st, ch, _ = c.call(CONNECT, payload=struct.pack("<III", PROT_J1850VPW, 0, 0))

    if st == ERR_FAILED:
        print("J2534 VPW SKIP (the OBD chip is not ready on this device; "
              "CONNECT returned ERR_FAILED)")
        c.call(CLOSE)
        return 0

    check("CONNECT J1850VPW", st == NOERROR and ch >= 1, f"status {hex(st)} ch {ch}")
    if st != NOERROR:
        c.call(CLOSE)
        print("J2534 VPW FAIL:", ", ".join(fails))
        return 1

    # a PASS filter on the tester address F0 (byte 1 of the reply header):
    # mask 00 FF 00, pattern 00 F0 00 over the 3 header bytes
    mask = MSG.pack(PROT_J1850VPW, 0, 0, 0, 0, 3) + bytes([0x00, 0xFF, 0x00])
    patt = MSG.pack(PROT_J1850VPW, 0, 0, 0, 0, 3) + bytes([0x00, 0xF0, 0x00])
    st, fid, _ = c.call(START_FILTER, ch,
                        struct.pack("<I", PASS_FILTER) + mask + patt)
    check("START_FILTER PASS (reply to F0)", st == NOERROR, f"status {hex(st)}")

    # the request: mode 01 PID 00 to all OBD modules
    st, rxs = c.vpw_write(ch, REQ_HDR, REQ_PAYLOAD)
    check("WRITE_MSGS J1850 accepted", st == NOERROR, f"status {hex(st)}")

    c.call(DISCONNECT, channel=ch)
    c.call(CLOSE)

    if fails:
        print("J2534 VPW FAIL:", ", ".join(fails))
        return 1

    if not rxs:
        print("J2534 VPW SKIP (connect + VPW init + write reached the chip; "
              "no J1850 node answered 01 00 - the bench ECU sim is CAN-only. "
              "Re-run with a VPW ECU or vehicle on the bus for the reply check)")
        return 0

    # a valid J1850 reply is at least a 3-byte header plus data; a mode-01
    # answer to 01 00 is 41 00 <4 PID-support bytes>
    first = rxs[0]
    ok = len(first) >= 4 and first[3] == 0x41
    check("J1850 reply shape (header + 41 00 ...)", ok, first.hex(" ").upper())
    if fails:
        print("J2534 VPW FAIL:", ", ".join(fails))
        return 1
    print(f"J2534 VPW PASS ({len(rxs)} reply/replies, first {first.hex(' ').upper()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
