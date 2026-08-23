"""OBD bench check: PCAN-injected frames stream through ATMA (task pytest ext).

Starts an ATMA monitor session through the DUT's USB bridge (COM6),
injects distinctive frames from PCAN, asserts they appear in the monitor
stream, then stops the session with the SPACE byte (never CR) and verifies
the prompt returns.

Usage:
    python tools/testbench/obd_monitor_injection_check.py \
        [--bridge COM6] [--pcan PCAN_USBBUS2]

Needs: the obd_chip test app running on the DUT (bridge up after TEST DONE),
python-can + PCAN drivers, pyserial.

Expected output ends with: MONITOR INJECTION CHECK PASS
"""
import argparse
import sys
import threading
import time

import can
import serial

INJECT_ID = 0x123
INJECT_DATA = [0xDE, 0xAD, 0xBE, 0xEF, 0x11, 0x22, 0x33, 0x44]

serial_buf = bytearray()
serial_lock = threading.Lock()
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


def main():
    global stop
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="COM6")
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    args = ap.parse_args()

    s = serial.Serial(args.bridge, 115200, timeout=0.2)
    bus = can.Bus(interface="pcan", channel=args.pcan, bitrate=500000)
    threading.Thread(target=serial_reader, args=(s,), daemon=True).start()

    # sanity: chip alive through the bridge
    s.write(b"ATI\r")
    time.sleep(1.0)
    if "ELM327" not in snapshot():
        print(f"chip not answering through the bridge: {snapshot()!r}")
        return 1

    # headers on so the monitor lines include the CAN ID (default is ATH0);
    # note arbitrary injected data trips the ELM formatter's "<DATA ERROR"
    # tag (first byte is not a valid ISO-TP PCI) — that is expected output
    s.write(b"ATH1\r")
    time.sleep(0.5)

    with serial_lock:
        serial_buf.clear()
    s.write(b"ATMA\r")
    time.sleep(0.5)

    msg = can.Message(arbitration_id=INJECT_ID, is_extended_id=False,
                      data=INJECT_DATA)
    for _ in range(5):
        bus.send(msg)
        time.sleep(0.1)

    time.sleep(1.0)
    stream = snapshot()

    # stop the monitor with SPACE (never CR), expect the prompt back
    s.write(b" ")
    time.sleep(1.0)
    tail = snapshot()[len(stream):]
    s.write(b"ATH0\r")  # restore default header display
    time.sleep(0.5)

    stop = True
    s.close()
    bus.shutdown()

    flat = stream.replace(" ", "").upper()
    seen = "DEADBEEF11223344" in flat and "123DEADBEEF" in flat
    stopped = ">" in tail

    print(f"monitor stream ({len(stream)} chars): {stream[:400]!r}")
    print(f"injected frame seen: {seen}; stopped by SPACE: {stopped}")

    if seen and stopped:
        print("MONITOR INJECTION CHECK PASS")
        return 0
    print("MONITOR INJECTION CHECK FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
