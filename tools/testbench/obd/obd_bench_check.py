"""OBD bench verification (PC side) — components/obd_chip bench (BENCH.md).

Run AFTER flashing the bench-probe app (it bridges UART2 <-> chip):

    python tools/testbench/obd_bench_check.py [--bridge COM6] [--pcan PCAN_USBBUS1]

Checks:
  1. USB bridge channel B -> ESP32 UART2 -> chip: send ATI, expect ELM + '>'
  2. PCAN @ 500k/11-bit: send OBD broadcast 0x7DF 01 00, expect an ECU
     response 0x7E8..0x7EF (proves PCAN driver + wiring + ECU simulator)
"""
import argparse
import sys
import time

import serial

OK = True


def check(name: str, ok: bool, detail: str) -> None:
    global OK
    OK &= ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


def check_bridge(port: str) -> None:
    try:
        s = serial.Serial(port, 115200, timeout=0.3)
    except Exception as e:
        check("bridge", False, f"cannot open {port}: {e}")
        return

    s.reset_input_buffer()
    s.write(b"\r")           # clear any stale state
    time.sleep(0.2)
    s.reset_input_buffer()
    s.write(b"ATI\r")

    buf = bytearray()
    deadline = time.time() + 3
    while time.time() < deadline and b">" not in buf:
        buf.extend(s.read(256))
    s.close()

    text = buf.decode(errors="replace").replace("\r", ".")
    check("bridge", b">" in buf and b"ELM" in buf,
          f"{port} -> chip replied: {text!r}")


def check_pcan(channel: str) -> None:
    try:
        import can
    except ImportError:
        check("pcan", False, "python-can not installed "
              "(pip install python-can)")
        return

    try:
        bus = can.Bus(interface="pcan", channel=channel, bitrate=500000)
    except Exception as e:
        check("pcan", False, f"cannot open {channel}: {e}")
        return

    try:
        # standard OBD-II functional query: mode 01 PID 00 (supported PIDs)
        req = can.Message(arbitration_id=0x7DF,
                          data=[0x02, 0x01, 0x00, 0, 0, 0, 0, 0],
                          is_extended_id=False)
        bus.send(req)

        deadline = time.time() + 2
        got = None
        while time.time() < deadline:
            msg = bus.recv(timeout=0.5)
            if msg is not None and 0x7E8 <= msg.arbitration_id <= 0x7EF:
                got = msg
                break

        if got is not None:
            check("pcan", True,
                  f"{channel}: ECU sim answered 0x{got.arbitration_id:X} "
                  f"data={got.data.hex(' ')}")
        else:
            check("pcan", False,
                  f"{channel} opened, query sent, but no 0x7E8..0x7EF reply "
                  f"(ECU simulator off / not wired / wrong bitrate?)")
    finally:
        bus.shutdown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="COM6")
    ap.add_argument("--pcan", default="PCAN_USBBUS2")  # BUS2 is on the bench bus
    args = ap.parse_args()

    check_bridge(args.bridge)
    check_pcan(args.pcan)
    sys.exit(0 if OK else 1)
