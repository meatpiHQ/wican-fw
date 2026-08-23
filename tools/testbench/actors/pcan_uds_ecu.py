#!/usr/bin/env python3
"""A minimal UDS ECU on the PCAN (11-bit, 500k) for the uds_manager
bench. Answers physical requests on 0x7E0, replies on 0x7E8, with full
ISO-TP (single + multi frame + flow control). Runs until Ctrl-C or the
optional duration arg.

  0x22 F1 90  -> read DID: 17-byte "WICANBENCHVIN01234" (forces multi-frame)
  0x22 F1 A0  -> a DID that answers 0x78 pending twice, then the value
  0x22 12 34  -> NRC 0x31 requestOutOfRange
  0x10 03     -> session control OK (50 03 00 32 01 F4)
  0x3E 80     -> tester present, suppressed (no reply)
  else        -> NRC 0x11 serviceNotSupported
"""
import sys
import time

import can

REQ = 0x7E0
RESP = 0x7E8
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 120

bus = can.Bus(interface="pcan", channel="PCAN_USBBUS2", bitrate=500000)


def send_frame(data):
    bus.send(can.Message(arbitration_id=RESP, is_extended_id=False,
                         data=data + b"\x00" * (8 - len(data))))


def send_isotp(payload):
    """Send a UDS payload as ISO-TP (SF or FF+CF, honoring one FC)."""
    n = len(payload)
    if n <= 7:
        send_frame(bytes([n]) + payload)
        return
    # First Frame: 1 nibble 0x1, 12-bit length
    ff = bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF]) + payload[:6]
    send_frame(ff)
    # wait for Flow Control from the tester
    rest = payload[6:]
    t0 = time.time()
    while time.time() - t0 < 1.0:
        m = bus.recv(timeout=0.5)
        if m and m.arbitration_id == REQ and (m.data[0] & 0xF0) == 0x30:
            break
    # Consecutive Frames
    sn = 1
    while rest:
        cf = bytes([0x20 | (sn & 0x0F)]) + rest[:7]
        send_frame(cf)
        rest = rest[7:]
        sn = (sn + 1) & 0x0F
        time.sleep(0.003)


def handle(payload):
    if payload[:3] == b"\x22\xF1\x90":
        send_isotp(b"\x62\xF1\x90" + b"WICANBENCHVIN01234")
    elif payload[:3] == b"\x22\xF1\xA0":
        send_isotp(b"\x7F\x22\x78")   # pending
        time.sleep(0.15)
        send_isotp(b"\x7F\x22\x78")   # pending again
        time.sleep(0.15)
        send_isotp(b"\x62\xF1\xA0\x11\x22\x33\x44")
    elif payload[:1] == b"\x22":
        send_isotp(b"\x7F\x22\x31")   # requestOutOfRange
    elif payload[:2] == b"\x10\x03":
        send_isotp(b"\x50\x03\x00\x32\x01\xF4")
    elif payload[:1] == b"\x3E":
        pass  # tester present, suppressed
    else:
        send_isotp(bytes([0x7F, payload[0], 0x11]))


def main():
    print(f"UDS ECU on PCAN_USBBUS2: req 0x{REQ:03X} -> resp 0x{RESP:03X}")
    rx = {}  # reassembly per (sf/ff)
    end = time.time() + DUR
    ff_buf = b""
    ff_left = 0
    ff_sn = 0
    try:
        while time.time() < end:
            m = bus.recv(timeout=0.5)
            if m is None or m.arbitration_id != REQ:
                continue
            d = bytes(m.data)
            pci = d[0] & 0xF0
            if pci == 0x00:  # single frame
                n = d[0] & 0x0F
                handle(d[1:1 + n])
            elif pci == 0x10:  # first frame
                ff_left = ((d[0] & 0x0F) << 8) | d[1]
                ff_buf = d[2:8]
                ff_left -= len(ff_buf)
                ff_sn = 1
                # send Flow Control: clear to send
                send_frame(bytes([0x30, 0x00, 0x00]))
            elif pci == 0x20:  # consecutive
                take = min(7, ff_left)
                ff_buf += d[1:1 + take]
                ff_left -= take
                if ff_left <= 0:
                    handle(ff_buf)
    finally:
        bus.shutdown()
    print("ECU stopped")
    (void := rx)


if __name__ == "__main__":
    main()
