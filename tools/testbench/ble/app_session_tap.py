#!/usr/bin/env python3
"""The ELM TCP tap on the bench Pi, for an app on the device's own access point.

For the WiFi legs of the app tests the phone joins the device's access
point and the app talks to 192.168.0.10:35000. To see every byte with its
timing, the bench Pi's second radio (wtest1, profile `wican-dut-ap`) joins
the same access point as 192.168.0.11 and runs `elm_tcp_tap.py` there; the
app is pointed at 192.168.0.11:35000 instead and the tap relays unchanged.
A station on the device's access point stops Bluetooth (ap_ble_exclusive),
so the tap is for the WiFi legs only: `stop` before a Bluetooth leg.

  python tools/testbench/ble/app_session_tap.py start --name carscanner_android_wifi
  python tools/testbench/ble/app_session_tap.py status
  python tools/testbench/ble/app_session_tap.py stop --name carscanner_android_wifi --out DIR

`stop` copies the tap's log and its SUMMARY lines into --out and re-prints
the analysis (`elm_tcp_tap.py --analyse`). The device's own address and
the tap's are printed by `start`.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
TAP = os.path.join(REPO, "tools", "testbench", "obd", "elm_tcp_tap.py")
PI = os.environ.get("WICAN_BENCH_HOST", "rpi001")
PROFILE = "wican-dut-ap"
DUT_AP_IP = "192.168.0.10"


def ssh(cmd, timeout=60):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", PI, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def tap_running():
    rc, out = ssh("pgrep -f 'elm_tcp_ta[p]' || true")
    return out.strip() != ""


def on_ap():
    rc, out = ssh("ip -4 -o addr show wtest1 | awk '{print $4}'")
    return out.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("start", "stop", "status"))
    ap.add_argument("--name", default="tap")
    ap.add_argument("--out", default="")
    ap.add_argument("--port", type=int, default=35000)
    a = ap.parse_args()

    if a.cmd == "status":
        print(f"tap running: {tap_running()}; wtest1 on the device AP: {on_ap() or 'no'}")
        return 0

    if a.cmd == "start":
        if tap_running():
            print("a tap is already running on the Pi: stop it first")
            return 2
        subprocess.run(["ssh", "-o", "BatchMode=yes", PI, "mkdir -p ~/wican/tools/testbench/obd"],
                       check=True)
        subprocess.run(["scp", "-q", TAP, f"{PI}:~/wican/tools/testbench/obd/"], check=True)
        rc, out = ssh(f"sudo -n nmcli -w 30 con up {PROFILE}", timeout=60)
        addr = on_ap()
        if not addr.startswith("192.168.0."):
            print(f"wtest1 did not join the device's access point: {out}")
            return 3
        log = f"/tmp/tap_{a.name}.log"
        ssh(f"setsid nohup python3 -u ~/wican/tools/testbench/obd/elm_tcp_tap.py {DUT_AP_IP} "
            f"--upstream-port {a.port} --port {a.port} --log {log} --quiet "
            f"> /tmp/tap_{a.name}.out 2>&1 < /dev/null &")
        time.sleep(2)
        if not tap_running():
            rc, out = ssh(f"cat /tmp/tap_{a.name}.out")
            print(f"the tap did not start: {out}")
            return 3
        print(f"TAP UP: point the app at {addr.split('/')[0]}:{a.port} "
              f"(the device itself is {DUT_AP_IP}:{a.port}); log {log} on {PI}")
        return 0

    if a.cmd == "stop":
        ssh("pkill -f 'elm_tcp_ta[p]' || true")
        time.sleep(1)
        ssh(f"sudo -n nmcli con down {PROFILE} || true")
        out_dir = a.out or os.path.join(REPO, "test-reports", "logs", "app_sessions")
        os.makedirs(out_dir, exist_ok=True)
        for f in (f"tap_{a.name}.log", f"tap_{a.name}.out"):
            subprocess.run(["scp", "-q", f"{PI}:/tmp/{f}", os.path.join(out_dir, f)])
        local = os.path.join(out_dir, f"tap_{a.name}.log")
        if os.path.exists(local) and os.path.getsize(local) > 0:
            print(f"tap log: {local}")
            subprocess.run([sys.executable, TAP, "--analyse", local])
        else:
            print("no tap log (the app never connected through the tap)")
        print(f"TAP DOWN; wtest1 off the device's access point: {on_ap() or 'yes'}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
