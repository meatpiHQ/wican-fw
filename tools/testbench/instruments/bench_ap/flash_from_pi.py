"""Push the bench_ap build to rpi001 and flash the P4 through its USB-UART port.
The board hangs off the Pi, so esptool runs there (the IDF python env's esptool).
usage: python flash_from_pi.py [--port /dev/ttyUSB0] [--host rpi001]"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_DEFAULT = os.path.join(HERE, "build")
FILES = ["bootloader/bootloader.bin", "partition_table/partition-table.bin", "bench_ap.bin", "flash_args"]
BUILD = None
REMOTE = "~/bench_ap_build"
PI_PY = "~/.espressif/python_env/idf6.0_py3.11_env/bin/python"


def main():
    global BUILD
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--host", default="rpi001")
    ap.add_argument("--baud", default="460800")
    ap.add_argument("--build", default=BUILD_DEFAULT, help="build directory (idf.py -B)")
    a = ap.parse_args()
    BUILD = a.build
    for f in FILES:
        if not os.path.exists(os.path.join(BUILD, f)):
            print("missing", f, "(build first)")
            return 1
    subprocess.run(["ssh", a.host, f"mkdir -p {REMOTE}/bootloader {REMOTE}/partition_table"], check=True)
    for f in FILES:
        subprocess.run(["scp", "-q", os.path.join(BUILD, f), f"{a.host}:{REMOTE}/{f}"], check=True)
    cmd = (f"cd {REMOTE} && {PI_PY} -m esptool --chip esp32p4 -p {a.port} -b {a.baud} "
           f"--before default-reset --after hard-reset write-flash @flash_args")
    r = subprocess.run(["ssh", a.host, cmd], capture_output=True, text=True, timeout=600)
    print("\n".join(r.stdout.strip().splitlines()[-6:]))
    if r.returncode != 0:
        print(r.stderr[-800:])
        return 1
    print("flashed; the board resets into bench_ap")
    return 0


if __name__ == "__main__":
    sys.exit(main())
