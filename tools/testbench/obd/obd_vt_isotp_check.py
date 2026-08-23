"""OBD bench check: 4 KB ISO-TP transmit via VTFullyRequestCk (task 11.3),
verified with a REAL ISO-TP stack (python-can + can-isotp), per the task
spec: the PC side is a standards-compliant ISO 15765-2 receiver, not a
hand-rolled parser — it owns flow control (BS/STmin), sequence-number
checking and reassembly, and reports any protocol violation by the chip.

Command syntax (bench-verified 2026-07-03 on MIC3624 V2.3.22):

    VTFullyRequestCk<LLLL><data hex><CCCC>[1]\r

    LLLL  payload byte count, 4 hex digits (max seen: 0FFF = 4095)
    data  payload as uppercase hex (2 chars per byte)
    CCCC  16-bit additive checksum of the payload bytes, 4 hex digits
    [1]   optional trailing '1' (meatpi's example has it; both forms accepted)

Chip behavior: transmits the ISO-TP First Frame on the current header (0x7DF
by default) and expects Flow Control from 0x7E8 promptly (it ignores the ECU
simulator's FC from request-8 = 0x7D7; answers "FC RX TIMEOUT" if none
arrives in time). After the transfer it prints the ECU's response ("NO DATA"
if none) and the prompt.

Usage:
    python tools/testbench/obd_vt_isotp_check.py \
        [--bridge COM6] [--pcan PCAN_USBBUS2] [--size 4095]

Needs: the obd_chip test app running on the DUT (bridge up after TEST DONE),
python-can + PCAN drivers, can-isotp, pyserial.

Expected output ends with: VT ISO-TP CHECK PASS
"""
import argparse
import sys
import threading
import time

import can
import isotp
import serial

serial_buf = bytearray()
serial_lock = threading.Lock()
isotp_errors = []
stop = False


def serial_reader(s):
    while not stop:
        chunk = s.read(4096)
        if chunk:
            with serial_lock:
                serial_buf.extend(chunk)


def snapshot():
    with serial_lock:
        return bytes(serial_buf).decode(errors="replace")


def on_isotp_error(error):
    # any deviation the stack sees (bad sequence number, overflow, timeout)
    # is a finding about the chip's ISO-TP implementation — record them all
    isotp_errors.append(f"{error.__class__.__name__}: {error}")


def main():
    global stop
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="COM6")
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--size", type=int, default=4095,
                    help="payload bytes (max 4095)")
    args = ap.parse_args()

    payload = bytes(i % 256 for i in range(args.size))
    cmd = ("VTFullyRequestCk" + format(len(payload), "04X") +
           payload.hex().upper() + format(sum(payload) & 0xFFFF, "04X"))

    s = serial.Serial(args.bridge, 115200, timeout=0.2)
    bus = can.Bus(interface="pcan", channel=args.pcan, bitrate=500000)
    threading.Thread(target=serial_reader, args=(s,), daemon=True).start()

    # ISO-TP receiver: the chip transmits from 0x7DF, we flow-control from
    # 0x7E8 (the one FC source it accepts). BS=0/STmin=0 = one FC, full speed.
    addr = isotp.Address(isotp.AddressingMode.Normal_11bits,
                         rxid=0x7DF, txid=0x7E8)
    stack = isotp.CanStack(bus=bus, address=addr,
                           error_handler=on_isotp_error,
                           params={
                               "blocksize": 0,
                               "stmin": 0,
                               "rx_consecutive_frame_timeout": 1000,
                               "max_frame_size": 4095,
                           })
    stack.start()

    # sanity: chip alive through the bridge
    s.write(b"ATI\r")
    time.sleep(1.0)
    if "ELM327" not in snapshot():
        print(f"chip not answering through the bridge: {snapshot()!r}")
        return 1
    with serial_lock:
        serial_buf.clear()

    s.write(cmd.encode() + b"\r")

    got = stack.recv(block=True, timeout=20.0)

    time.sleep(1.5)  # let the chip print the ECU's response
    reply = snapshot()
    stack.stop()
    stop = True
    s.close()
    bus.shutdown()

    print(f"chip reply: {reply!r}")
    print(f"isotp stack received: "
          f"{len(got) if got is not None else 0}/{len(payload)} bytes")

    for e in isotp_errors:
        print(f"isotp stack error: {e}")

    if (got is not None and bytes(got) == payload and
            not isotp_errors and
            "FC RX TIMEOUT" not in reply and "?" not in reply):
        print("VT ISO-TP CHECK PASS")
        return 0

    print("VT ISO-TP CHECK FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
