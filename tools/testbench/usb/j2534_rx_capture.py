#!/usr/bin/env python3
"""Capture the ISO15765 RX round-trip through the J2534 server.

  python j2534_rx_capture.py <dut_ip> [port]

Sends non-reflash UDS requests (10 02 DiagnosticSessionControl, 27 01
SecurityAccess seed) and prints the ECU responses collected as RX_MSG
frames. Proves the full write -> bus -> ECU -> bus -> RX path end to end
WITHOUT needing allow_reflash (these SIDs are never gated). Needs a
responding ECU on WiCAN's CAN bus (pcan_reflash_ecu.py --scenario happy).
"""
import struct
import sys

import j2534_bench as b

NOERROR = 0x00
PROT_ISO15765 = 6
fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" + (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    dut = sys.argv[1] if len(sys.argv) > 1 else "192.168.82.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 6809

    c = b.Client(dut, port)
    c.call(b.HELLO)
    c.call(b.OPEN)
    st, ch, _ = c.call(b.CONNECT,
                       payload=struct.pack("<IIIII", PROT_ISO15765, 0, 500000,
                                           0x7E0, 0x7E8))
    check("CONNECT ISO15765 tx7E0/rx7E8", st == NOERROR, f"channel {ch}")

    def last(rxs):
        return rxs[-1].hex(" ").upper() if rxs else "(none)"

    st, rx = c.write_uds(ch, bytes([0x10, 0x02]))
    check("10 02 -> 50 02 (RX round-trip)",
          bool(rx) and rx[-1][:2] == b"\x50\x02", last(rx))

    st, rx = c.write_uds(ch, bytes([0x27, 0x01]))
    seed = rx[-1][2:] if rx and rx[-1][:2] == b"\x67\x01" else b""
    check("27 01 -> 67 01 + 4-byte seed", len(seed) == 4, last(rx))

    c.call(b.CLOSE)

    if fails:
        print("J2534 RX CAPTURE FAIL:", ", ".join(fails))
        sys.exit(1)
    print("J2534 RX CAPTURE PASS")


if __name__ == "__main__":
    main()
