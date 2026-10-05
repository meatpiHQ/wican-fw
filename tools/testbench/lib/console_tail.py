#!/usr/bin/env python3
"""The DUT's console beside a bench run, without resetting the DUT.

A reset nobody asked for says why on the console only (the panic handler
prints there, not into the log ring), and nobody watches the console of a
bench that runs over HTTP: the EU truck bench's first run (2026-10-03) lost
the device to such a reset and the reason with it. test.ps1 runs this beside
its PC bench stages; the file lands in the run's log folder.

The port is opened with DTR and RTS held off (the auto-reset circuit of the
USB-serial adapter stays quiet), every line gets a wall-clock stamp and is
flushed at once, and the lines a crash leaves are echoed to stdout as they
come. Stops after --secs, or when --stop-file appears.

  python console_tail.py PORT OUT.log [--baud 2000000] [--secs 3600]
                         [--stop-file PATH]

Exit code: 0, or 3 when the port cannot be opened (in use, unplugged).
"""
import argparse
import os
import re
import sys
import time

# also what the restart tracker says about a crash at the next boot, and the
# crash park (2026-10-05): a DUT that parked prints `WICAN PARK ...` once and
# is silent from then on, so that one line must not be missed
CRASH = re.compile(r"Guru Meditation|wdt timeout|Task watchdog|Backtrace:|abort\(\) was called|"
                   r"assert failed|stack overflow|CORRUPT HEAP|Rebooting\.\.\.|rst:0x|"
                   r"restart_tracker: boot|previous run crashed|crash-loop brake|"
                   r"WICAN PARK|park ends", re.I)
ANSI = re.compile(chr(27) + r"\[[0-9;]*m")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("port")
    ap.add_argument("out")
    ap.add_argument("--baud", type=int, default=2000000)
    ap.add_argument("--secs", type=float, default=3600)
    ap.add_argument("--stop-file", default="")
    a = ap.parse_args()

    import serial   # pyserial: late, so --help works without it

    s = serial.Serial()
    s.port = a.port
    s.baudrate = a.baud
    s.timeout = 0.2
    s.dtr = False
    s.rts = False
    try:
        s.open()
    except (OSError, serial.SerialException) as e:
        print(f"console_tail: cannot open {a.port}: {e}", flush=True)
        return 3

    lines = crash = 0
    buf = b""
    t0 = time.time()
    with open(a.out, "w", encoding="utf-8") as f:
        while time.time() - t0 < a.secs:
            if a.stop_file and os.path.exists(a.stop_file):
                break
            try:
                # what is there, at once (a line's stamp is its arrival to
                # some milliseconds: a bench lines the DUT's clock up with
                # the wall clock through it)
                chunk = s.read(s.in_waiting or 1)
            except (OSError, serial.SerialException) as e:
                f.write(f"{time.strftime('%H:%M:%S')} console_tail: {e}\n")
                break
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = ANSI.sub("", raw.decode("utf-8", errors="replace")
                                .rstrip("\r")).replace("\x00", "")
                now = time.time()
                stamp = time.strftime("%H:%M:%S", time.localtime(now)) \
                    + f".{int((now % 1) * 1000):03d}"
                f.write(f"{stamp} {text}\n")
                f.flush()
                lines += 1
                if CRASH.search(text):
                    crash += 1
                    print(f"{stamp} {text[:240]}", flush=True)
    s.close()
    print(f"console_tail: {lines} lines, {crash} boot/crash lines", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
