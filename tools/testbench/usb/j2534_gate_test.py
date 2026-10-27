#!/usr/bin/env python3
"""The J2534 server forwards every write (no content gate since 2026-10-27).

  python j2534_gate_test.py <dut_ip> [port]

Reuses the wire-protocol Client from j2534_bench. Assumes:
  * the DUT is reachable at <dut_ip> on an ALLOWED interface (AP or USB),
  * j2534_server.enabled = true (its web API on port 80 at the same ip).

Checks:
  1. the connection is accepted (the interface gate, allow_lan),
  2. 10 02 DiagnosticSessionControl is forwarded (ACK NOERROR),
  3. the UDS memory-transfer services 34 RequestDownload, 36 TransferData
     and 37 RequestTransferExit are forwarded too (ACK NOERROR; until
     2026-10-27 a closed allow_reflash gate answered ERR_NOT_SUPPORTED),
  4. /api/j2534 reflash_seen grew by exactly 3 (counted, never refused).

Does NOT need a responding ECU: the ACK of a WRITE says whether the device
forwarded it; the ECU's answer is not asserted.
"""
import json
import struct
import sys
import urllib.request

import j2534_bench as b

NOERROR = 0x00
PROT_ISO15765 = 6

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" + (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def reflash_seen(dut):
    try:
        with urllib.request.urlopen(f"http://{dut}/api/j2534", timeout=5) as r:
            return json.load(r).get("reflash_seen")
    except Exception:
        return None


def main():
    dut = sys.argv[1] if len(sys.argv) > 1 else "192.168.82.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 6809

    try:
        c = b.Client(dut, port)
    except OSError as e:
        print(f"GATE TEST FAIL: cannot connect {dut}:{port} ({e}), "
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

    seen0 = reflash_seen(dut)
    st, _ = c.write_uds(ch, bytes([0x10, 0x02]), collect_ms=200)
    check("10 02 (session) forwarded", st == NOERROR, f"status 0x{st:02X}")

    for name, pdu in (("34 RequestDownload", bytes([0x34, 0x00, 0x44, 0, 0, 0x04, 0x00])),
                      ("36 TransferData", bytes([0x36, 0x01, 0xAA, 0xBB])),
                      ("37 RequestTransferExit", bytes([0x37]))):
        st, _ = c.write_uds(ch, pdu, collect_ms=200)
        check(f"{name} forwarded (no content gate)", st == NOERROR, f"status 0x{st:02X}")

    seen1 = reflash_seen(dut)
    check("reflash_seen counted the three", seen0 is not None and seen1 == seen0 + 3, f"{seen0} -> {seen1}")

    c.call(b.CLOSE)

    if fails:
        print("GATE TEST FAIL:", ", ".join(fails))
        sys.exit(1)
    print("J2534 GATE TEST PASS")


if __name__ == "__main__":
    main()
