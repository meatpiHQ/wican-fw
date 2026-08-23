#!/usr/bin/env python3
"""Flash-port presence probe — ENUMERATION ONLY, never opens a port.

Opening COM7 resets the DUT (and any open can steal the port from a
flash in progress), so this probe only checks that the configured port
EXISTS: pyserial enumeration when available, Windows registry
(HARDWARE\\DEVICEMAP\\SERIALCOMM) fallback. BenchBoard health contract:
exit 0 = healthy, last stdout line = the chip status.
"""
import argparse
import sys


def list_ports():
    try:
        from serial.tools import list_ports as lp
        return {p.device: (p.description or "") for p in lp.comports()}
    except ImportError:
        pass
    if sys.platform == "win32":
        import winreg
        ports = {}
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DEVICEMAP\SERIALCOMM")
        except OSError:
            return ports
        i = 0
        while True:
            try:
                name, val, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            ports[val] = name
            i += 1
        return ports
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    args = ap.parse_args()
    ports = list_ports()
    if args.port in ports:
        desc = ports[args.port]
        print(f"{args.port} present" + (f" ({desc})" if desc else ""))
        return 0
    have = ", ".join(sorted(ports)) or "none"
    print(f"{args.port} ABSENT (enumerated: {have})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
