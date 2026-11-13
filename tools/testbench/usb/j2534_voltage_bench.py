"""READ_VBATT through the wire protocol and Windows DLL: J2534 VOLTAGE PASS.

Run with test.ps1 j2534vbat. No CAN traffic is needed. The simulator supplies
13.5 V and 14.5 V; settings and supply setpoint are restored in finally.
The same file runs under 32-bit Python with --dll-child to call the real DLL.
"""
import argparse
import ctypes
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time

HDR = struct.Struct("<HBBHHI")
IOCTL, READ_VBATT = 0x15, 0x03
NOERROR, NOT_CONNECTED = 0, 8
FAILS = []


def check(name, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'}: {name} ({detail})", flush=True)
    if not ok:
        FAILS.append(name)


def dll_read(args):
    dll = ctypes.WinDLL(args.dll)
    word = ctypes.c_ulong
    dll.PassThruOpen.argtypes = [ctypes.c_void_p, ctypes.POINTER(word)]
    dll.PassThruClose.argtypes = [word]
    dll.PassThruIoctl.argtypes = [word, word, ctypes.c_void_p, ctypes.c_void_p]
    dll.PassThruGetLastError.argtypes = [ctypes.c_void_p]
    for name in ("PassThruOpen", "PassThruClose", "PassThruIoctl"):
        getattr(dll, name).restype = ctypes.c_long
    device = word()
    st = dll.PassThruOpen(None, ctypes.byref(device))
    if st:
        raise RuntimeError(f"PassThruOpen returned 0x{st:x}")
    try:
        value = word(0xDEADBEEF)
        st = dll.PassThruIoctl(device, READ_VBATT, None, ctypes.byref(value))
        check("DLL voltage result", st == args.expected_status and
              (abs(value.value - args.expected_mv) <= 100 if st == 0
               else value.value == 0xDEADBEEF),
              f"status={st}, value={value.value}, expected={args.expected_mv}/{args.expected_status}")
        if st in (1, 7):
            error = ctypes.create_string_buffer(80)
            dll.PassThruGetLastError(error)
            expected_word = b"firmware" if st == 1 else b"battery"
            check("DLL explains voltage failure", expected_word in error.value,
                   error.value.decode(errors="replace"))
        # Some tools pass a channel id where SAE J2534-1 names the device id.
        # Every earlier driver answered them; the reading is device-wide.
        value = word(0xDEADBEEF)
        st = dll.PassThruIoctl(word(2), READ_VBATT, None, ctypes.byref(value))
        check("DLL answers a channel id like the device id",
              st == args.expected_status and
              (abs(value.value - args.expected_mv) <= 100 if st == 0
               else value.value == 0xDEADBEEF),
              f"status={st}, value={value.value}")
        st = dll.PassThruIoctl(device, READ_VBATT, None, None)
        check("DLL rejects a null output", st == 4, f"status={st}")
        value = word(0xDEADBEEF)
        st = dll.PassThruIoctl(word(99), READ_VBATT, None, ctypes.byref(value))
        check("DLL rejects an invalid device id", st == 0x19 and
              value.value == 0xDEADBEEF, f"status={st}")
    finally:
        st = dll.PassThruClose(device)
        check("DLL closes the device", st == 0, f"status={st}")


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EOFError("J2534 connection closed")
        data.extend(chunk)
    return bytes(data)


def request(sock, kind, seq, payload=b"", channel=0):
    sock.sendall(HDR.pack(0x4A35, 1, kind, seq, channel, len(payload)) + payload)
    header = HDR.unpack(recv_exact(sock, HDR.size))
    magic, version, kind, got_seq, got_channel, length = header
    if (magic, version, kind, got_seq, got_channel) != (0x4A35, 1, 0x80, seq, channel):
        raise ValueError(f"unexpected ACK header: {header}")
    if length not in (4, 8):
        raise ValueError(f"unexpected ACK length: {length}")
    return recv_exact(sock, length)


def driver_check(args, host, port, expected, status=0):
    env = dict(os.environ, WICAN_J2534_HOST=host, WICAN_J2534_PORT=str(port))
    child = subprocess.run(
        [args.py32, "-I", os.path.abspath(__file__), "--dll-child",
         "--dll", args.dll, "--expected-mv", str(expected),
         "--expected-status", str(status)],
        env=env, capture_output=True, text=True, timeout=45)
    print(child.stdout, end="", flush=True)
    if child.stderr:
        print(child.stderr, file=sys.stderr, end="", flush=True)
    return child.returncode == 0


def driver_mock_checks(args):
    for label, response, expected_status in (
        ("millivolts", struct.pack("<II", 0, 13890), 0),
        ("old firmware without a result", struct.pack("<I", 0), 1),
        ("ADC unavailable", struct.pack("<I", 7), 7),
    ):
        errors = []
        reads = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(15)
            port = listener.getsockname()[1]

            def serve():
                try:
                    conn, _ = listener.accept()
                    with conn:
                        conn.settimeout(10)
                        while True:
                            header = HDR.unpack(recv_exact(conn, HDR.size))
                            _, _, kind, seq, channel, size = header
                            payload = recv_exact(conn, size)
                            body = struct.pack("<II", 0, 1)
                            if kind == IOCTL:
                                reads.append((channel, payload))
                                body = response
                            conn.sendall(HDR.pack(0x4A35, 1, 0x80, seq, channel,
                                                 len(body)) + body)
                            if kind == 3:
                                break
                except (OSError, EOFError, ValueError) as exc:
                    errors.append(str(exc))

            worker = threading.Thread(target=serve)
            worker.start()
            try:
                ok = driver_check(args, "127.0.0.1", port, 13890, expected_status)
            finally:
                worker.join(20)
            check(f"DLL fake device: {label}",
                  ok and not worker.is_alive() and not errors and
                  reads == [(0, struct.pack("<I", READ_VBATT))] * 2,
                  f"wire reads={reads}, errors={errors}")


def wire_read(host, port, expected):
    with socket.create_connection((host, port), timeout=8) as sock:
        payload = struct.pack("<I", READ_VBATT)
        body = request(sock, IOCTL, 1, payload)
        check("READ_VBATT before OPEN refused",
              body == struct.pack("<I", NOT_CONNECTED), body.hex(" "))
        body = request(sock, 2, 2)
        check("OPEN supplies device id", body == struct.pack("<II", 0, 1), body.hex(" "))
        body = request(sock, IOCTL, 3, payload)
        value = struct.unpack_from("<I", body, 4)[0] if len(body) == 8 else None
        check("wire ACK contains status and measured millivolts",
              len(body) == 8 and body[:4] == bytes(4) and
              value is not None and abs(value - expected) <= 100,
              f"ACK={body.hex(' ')}, expected={expected}")
        request(sock, 3, 4)
        body = request(sock, IOCTL, 5, payload)
        check("READ_VBATT after CLOSE refused",
              body == struct.pack("<I", NOT_CONNECTED), body.hex(" "))


def run(args):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
    from benchlib import api, DUT_REBOOT_WINDOW, HTTP_RETRY_BUDGET
    from bench_psu import open_psu

    def http(path, method="GET", body=None):
        status, text = api(args.dut, path, method, body)
        if status != 200:
            raise RuntimeError(f"{path}: HTTP {status}")
        return json.loads(text)

    def settings():
        result = http("/api/settings/j2534_server")
        result.pop("degraded", None)
        result.pop("pending_reboot", None)
        return result

    planned = 0

    def submit(config):
        nonlocal planned
        http("/api/settings/j2534_server", "PUT", config)
        if http("/api/settings/submit", "POST").get("reboot"):
            planned += 1
            time.sleep(6)
            status, _ = api(args.dut, "/api/status", budget_s=DUT_REBOOT_WINDOW)
            if status != 200:
                raise RuntimeError("DUT did not return after planned restart")

    driver_mock_checks(args)
    before = http("/api/status")
    faults = http("/api/faults")
    found = settings()
    host, port_text = args.j2534.rsplit(":", 1)
    with open_psu("auto") as psu:
        original_voltage = psu.voltage_setpoint()
        try:
            configured = dict(found, enabled=True, allow_lan=True, port=6809)
            submit(configured)
            for volts in (13.5, 14.5):
                psu.set_voltage(volts)
                deadline = time.monotonic() + HTTP_RETRY_BUDGET
                while True:
                    measured = psu.meas_voltage()
                    battery = http("/api/battery")["voltage"]
                    if abs(measured - volts) < 0.15 and abs(battery - measured) < 0.15:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"supply did not settle: {measured} V, ADC {battery} V")
                    time.sleep(1)
                expected = round(battery * 1000)
                print(f"Supply {measured:.3f} V, battery API {battery:.3f} V", flush=True)
                wire_read(host, int(port_text), expected)
                check(f"DLL at {volts} V",
                      driver_check(args, host, int(port_text), expected), expected)
            after = http("/api/status")
            check("no error logs", after["health"]["log_errors"] == 0,
                  after["health"]["log_errors"])
            check("no new faults", http("/api/faults") == faults, "fault records compared")
        finally:
            try:
                psu.set_voltage(original_voltage)
                check("supply setpoint restored",
                      abs(psu.voltage_setpoint() - original_voltage) < 0.01,
                      original_voltage)
            finally:
                submit(found)
                check("settings restored", settings() == found, "full document compared")
        after = http("/api/status")
        check("only planned restarts",
              after["boot_count"] == before["boot_count"] + planned and
              after["unexpected_resets"] == before["unexpected_resets"],
              f"planned={planned}, boots={before['boot_count']}->{after['boot_count']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dut", default="localhost:18081")
    parser.add_argument("--j2534", default="127.0.0.1:18534")
    parser.add_argument("--py32", default=r"C:\Users\Ali\idf_tmp\py32\python.exe")
    parser.add_argument("--dll", default=os.environ.get(
        "WICAN_J2534_DLL", r"C:\Program Files (x86)\WiCAN\wican_j2534.dll"))
    parser.add_argument("--dll-child", action="store_true")
    parser.add_argument("--driver-only", action="store_true")
    parser.add_argument("--expected-mv", type=int, default=0)
    parser.add_argument("--expected-status", type=int, default=0)
    args = parser.parse_args()
    try:
        if args.dll_child:
            dll_read(args)
        elif args.driver_only:
            driver_mock_checks(args)
        else:
            run(args)
    except (OSError, ValueError, KeyError, RuntimeError, EOFError,
            subprocess.SubprocessError) as exc:
        check("voltage bench completed", False, str(exc))
    print("J2534 VOLTAGE " + ("FAIL: " + ", ".join(FAILS) if FAILS else "PASS"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
