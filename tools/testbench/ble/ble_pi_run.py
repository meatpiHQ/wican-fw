#!/usr/bin/env python3
"""PC wrapper: sync tools/testbench/ble to rpi001 and run one Pi-side BLE
bench under sudo with the user's bleak on PYTHONPATH, streaming its output.

  python tools/testbench/ble/ble_pi_run.py ble_http_pi.py --dut 10.42.1.194
  python tools/testbench/ble/ble_pi_run.py ble_j2534_pi.py --stage ble

Exit code = the Pi script's. test.ps1 kinds `blehttp` / `blej2534` use it.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PP = "/home/meatpi/.local/lib/python3.11/site-packages"
BENCH = os.environ.get("WICAN_BENCH_HOST", "rpi001")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    script, args = sys.argv[1], sys.argv[2:]
    subprocess.run(["ssh", "-o", "BatchMode=yes", BENCH,
                    "mkdir -p ~/wican/tools/testbench/ble"], check=True)
    files = [os.path.join(HERE, f) for f in os.listdir(HERE) if f.endswith(".py")]
    subprocess.run(["scp", "-q"] + files + [f"{BENCH}:~/wican/tools/testbench/ble/"], check=True)
    cmd = (f"cd ~/wican/tools/testbench/ble && sudo env PYTHONPATH={PP} "
           f"python3 -u {script} " + " ".join(args))
    p = subprocess.Popen(["ssh", "-o", "BatchMode=yes", BENCH, cmd],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         errors="replace")
    for line in p.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    return p.wait()


if __name__ == "__main__":
    sys.exit(main())
