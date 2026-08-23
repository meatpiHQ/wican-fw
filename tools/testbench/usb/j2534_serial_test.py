#!/usr/bin/env python3
"""Exercise the J2534 server over the CDC-ACM SERIAL transport
(device_class=cdc). Same framed wire protocol as the TCP path, just over a
virtual COM port.

  python j2534_serial_test.py COM<N>            # gate + basic ops
  python j2534_serial_test.py COM<N> --rx       # also 10 02 -> 50 02 (needs ECU)

Proves HELLO/OPEN/CONNECT/StartFilter-equiv/WRITE dispatch + the safety
gate over serial, so the CDC transport is validated without a J2534 DLL.
Needs pyserial.
"""
import struct
import sys

import serial  # pyserial

MAGIC = 0x4A35
HDR = struct.Struct("<HBBHHI")  # magic, ver, type, seq, channel, length
(HELLO, OPEN, CLOSE, CONNECT, DISCONNECT, WRITE_MSGS, ACK, RX_MSG) = (
    0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x80, 0x81)
NOERROR = 0x00
ERR_NOT_SUPPORTED = 0x01
PROT_ISO15765 = 6

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" + (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


class SerialClient:
    def __init__(self, port):
        self.s = serial.Serial(port, 2000000, timeout=2)
        self.s.dtr = True  # opening the port asserts DTR -> server starts
        self.seq = 0

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.read(n - len(buf))
            if not chunk:
                raise TimeoutError("serial read timeout")
            buf += chunk
        return buf

    def _read_frame(self):
        hdr = self._read(HDR.size)
        magic, ver, mtype, seq, ch, length = HDR.unpack(hdr)
        assert magic == MAGIC, f"bad magic {magic:#x}"
        body = self._read(length) if length else b""
        return mtype, ch, body

    def call(self, mtype, channel=0, payload=b""):
        self.seq = (self.seq + 1) & 0xFFFF
        self.s.write(HDR.pack(MAGIC, 1, mtype, self.seq, channel, len(payload)))
        if payload:
            self.s.write(payload)
        while True:
            rt, rch, body = self._read_frame()
            if rt == ACK:
                status = struct.unpack_from("<I", body, 0)[0]
                result = struct.unpack_from("<I", body, 4)[0] if len(body) >= 8 else 0
                return status, result, rch
            # ignore async RX_MSG before our ACK

    def write_uds(self, channel, uds, collect_ms=1000):
        m = struct.pack("<IIIIII", PROT_ISO15765, 0, 0, 0, 0, len(uds)) + uds
        payload = struct.pack("<I", 1) + m
        st, _, _ = self.call(WRITE_MSGS, channel, payload)
        rxs = []
        self.s.timeout = collect_ms / 1000.0
        try:
            while True:
                rt, _, body = self._read_frame()
                if rt == RX_MSG and len(body) >= 24:
                    dsz = struct.unpack_from("<I", body, 20)[0]
                    rxs.append(body[24:24 + dsz])
        except (TimeoutError, AssertionError):
            pass
        self.s.timeout = 2
        return st, rxs


def main():
    if len(sys.argv) < 2:
        print("usage: j2534_serial_test.py COM<N> [--rx]")
        sys.exit(2)
    port = sys.argv[1]
    do_rx = "--rx" in sys.argv

    c = SerialClient(port)
    st, ver, _ = c.call(HELLO)
    check("HELLO over serial", st == NOERROR and ver == 1, f"wire v{ver}")
    st, dev, _ = c.call(OPEN)
    check("OPEN", st == NOERROR, f"device {dev}")
    st, ch, _ = c.call(CONNECT,
                       payload=struct.pack("<IIIII", PROT_ISO15765, 0, 500000,
                                           0x7E0, 0x7E8))
    check("CONNECT ISO15765", st == NOERROR, f"channel {ch}")

    st, _ = c.write_uds(ch, bytes([0x10, 0x02]), collect_ms=200)
    check("10 02 not gated", st == NOERROR, f"0x{st:02X}")
    st, _ = c.write_uds(ch, bytes([0x34, 0x00, 0x44, 0, 0, 0x04, 0x00]),
                        collect_ms=200)
    check("34 RequestDownload BLOCKED", st == ERR_NOT_SUPPORTED, f"0x{st:02X}")

    if do_rx:
        st, rx = c.write_uds(ch, bytes([0x10, 0x02]))
        last = rx[-1].hex(" ").upper() if rx else "(none)"
        check("10 02 -> 50 02 RX round-trip",
              bool(rx) and rx[-1][:2] == b"\x50\x02", last)

    c.call(CLOSE)
    if fails:
        print("J2534 SERIAL TEST FAIL:", ", ".join(fails))
        sys.exit(1)
    print("J2534 SERIAL TEST PASS")


if __name__ == "__main__":
    main()
