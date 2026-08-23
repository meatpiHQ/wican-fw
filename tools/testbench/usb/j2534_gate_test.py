#!/usr/bin/env python3
"""Verify the J2534 server's two safety gates against a live DUT.

  python j2534_gate_test.py <dut_ip> [port]

Reuses the wire-protocol Client from j2534_bench. Assumes:
  * the DUT is reachable at <dut_ip> on an ALLOWED interface (AP or USB),
  * j2534_server.enabled = true.

Checks:
  1. the connection is accepted (interface allowed),
  2. a non-reflash write (10 02 DiagnosticSessionControl) is NOT gated,
  3. reflash writes (34 RequestDownload, 36 TransferData) ARE rejected
     with ERR_NOT_SUPPORTED when allow_reflash is false.

Does NOT need a responding ECU: the reflash gate rejects the WRITE at the
device before it reaches the bus, so we assert on the WRITE ACK status.
"""
import struct
import sys

import j2534_bench as b

NOERROR = 0x00
ERR_NOT_SUPPORTED = 0x01
PROT_ISO15765 = 6

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" + (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    dut = sys.argv[1] if len(sys.argv) > 1 else "192.168.82.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 6809

    try:
        c = b.Client(dut, port)
    except OSError as e:
        print(f"GATE TEST FAIL: cannot connect {dut}:{port} ({e}) — "
              f"interface refused or server disabled?")
        sys.exit(1)
    check("connection accepted (interface allowed)", True, f"{dut}:{port}")

    c.call(b.HELLO)
    c.call(b.OPEN)
    # extended CONNECT binds ISO-TP tx=7E0 rx=7E8 (no filter needed)
    st, ch, _ = c.call(b.CONNECT,
                       payload=struct.pack("<IIIII", PROT_ISO15765, 0, 500000,
                                           0x7E0, 0x7E8))
    check("CONNECT ISO15765", st == NOERROR, f"channel {ch}")

    # non-reflash service passes the gate (10 02 = DiagnosticSessionControl).
    # No ECU needed — we only assert the WRITE ACK, not the response.
    st, _ = c.write_uds(ch, bytes([0x10, 0x02]), collect_ms=200)
    check("10 02 (session) NOT gated", st == NOERROR, f"status 0x{st:02X}")

    # reflash services must be rejected while allow_reflash is false
    st, _ = c.write_uds(ch, bytes([0x34, 0x00, 0x44, 0, 0, 0x04, 0x00]),
                        collect_ms=200)
    check("34 RequestDownload BLOCKED", st == ERR_NOT_SUPPORTED,
          f"status 0x{st:02X}")

    st, _ = c.write_uds(ch, bytes([0x36, 0x01, 0xAA, 0xBB]), collect_ms=200)
    check("36 TransferData BLOCKED", st == ERR_NOT_SUPPORTED,
          f"status 0x{st:02X}")

    st, _ = c.write_uds(ch, bytes([0x37]), collect_ms=200)
    check("37 RequestTransferExit BLOCKED", st == ERR_NOT_SUPPORTED,
          f"status 0x{st:02X}")

    c.call(b.CLOSE)

    if fails:
        print("GATE TEST FAIL:", ", ".join(fails))
        sys.exit(1)
    print("J2534 GATE TEST PASS")


if __name__ == "__main__":
    main()
