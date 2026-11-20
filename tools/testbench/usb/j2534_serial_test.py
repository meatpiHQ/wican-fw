#!/usr/bin/env python3
"""J2534 over the USB cable: the server on the CDC-ACM serial transport  ->  J2534 SERIAL TEST PASS

The same framed wire protocol as the TCP path, over the virtual COM port the WiCAN PRO
is to a PC when its USB connector is in the Device role with the CDC serial class.
No J2534 DLL is involved: this is the transport and the server.

  python j2534_serial_test.py auto            # find the port by its USB ids (16D0:1263)
  python j2534_serial_test.py COM<N> [--rx]   # --rx: also 10 02 -> 50 02 (an ECU answering 7E0)

The unit needs, on its web page (a restart applies them):
  USB page:    Connector role = Device, USB device class = CDC serial
  J2534 page:  enabled
As shipped the connector is in the Host role. The PC then sees only the two COM ports of
the board's USB bridge (the console and the OBD chip), which do not speak J2534, and
"auto" finds nothing and says so.

What it checks: HELLO, OPEN, CONNECT ISO15765 with the ids, a 10 02 written, and a
34 RequestDownload written. Until 2026-10-27 a content gate refused the 34
(ERR_NOT_SUPPORTED) and this test expected that; the gate is gone, the server forwards
what the tool sends and counts the reprogramming services (`reflash_seen` on /api/j2534).
Needs pyserial.
"""
import struct
import sys

import serial  # pyserial
from serial.tools import list_ports

MAGIC = 0x4A35
HDR = struct.Struct("<HBBHHI")  # magic, ver, type, seq, channel, length
(HELLO, OPEN, CLOSE, CONNECT, DISCONNECT, WRITE_MSGS, ACK, RX_MSG) = (
    0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x80, 0x81)
NOERROR = 0x00
PROT_ISO15765 = 6
USB_VID, USB_PID = 0x16D0, 0x1263       # the WiCAN PRO as a CDC serial device

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" + (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def find_port():
    """The COM port of a WiCAN PRO in the Device role with the CDC serial class, or None."""
    for p in list_ports.comports():
        if p.vid == USB_VID and p.pid == USB_PID:
            return p.device
    return None


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

    def write_uds(self, channel, uds, collect_ms=1000, tx_id=0x7E0):
        # SAE J2534-1 layout (2026-10-19): the 4-byte id first, pad flag 0x40;
        # answers come back with their id, indications (rx_status 0x01/0x02) skipped
        d = struct.pack(">I", tx_id) + uds
        m = struct.pack("<IIIIII", PROT_ISO15765, 0, 0x40, 0, 0, len(d)) + d
        payload = struct.pack("<I", 1) + m
        st, _, _ = self.call(WRITE_MSGS, channel, payload)
        rxs = []
        self.s.timeout = collect_ms / 1000.0
        try:
            while True:
                rt, _, body = self._read_frame()
                if rt == RX_MSG and len(body) >= 24:
                    rx_status = struct.unpack_from("<I", body, 4)[0]
                    dsz = struct.unpack_from("<I", body, 20)[0]
                    if rx_status & 0x03:
                        continue
                    rxs.append(body[28:24 + dsz])
        except (TimeoutError, AssertionError):
            pass
        self.s.timeout = 2
        return st, rxs


def main():
    if len(sys.argv) < 2:
        print("usage: j2534_serial_test.py COM<N>|auto [--rx]")
        sys.exit(2)
    port = sys.argv[1]
    do_rx = "--rx" in sys.argv

    if port.lower() == "auto":
        port = find_port()
        if port is None:
            print("J2534 SERIAL TEST FAIL: no WiCAN PRO serial port (USB %04X:%04X) on this PC. On the unit's web page: "
                  "USB page, Connector role = Device and USB device class = CDC serial; J2534 enabled; submit and let it "
                  "restart. In the Host role the PC sees only the board's console ports." % (USB_VID, USB_PID))
            sys.exit(1)
        print("port: %s (USB %04X:%04X)" % (port, USB_VID, USB_PID))

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
    check("10 02 written (ACK NOERROR)", st == NOERROR, f"0x{st:02X}")
    st, _ = c.write_uds(ch, bytes([0x34, 0x00, 0x44, 0, 0, 0x04, 0x00]),
                        collect_ms=200)
    check("34 RequestDownload forwarded (no content gate since 2026-10-27)", st == NOERROR, f"0x{st:02X}")

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
