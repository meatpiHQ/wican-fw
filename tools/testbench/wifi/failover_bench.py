#!/usr/bin/env python3
"""WiFi->LTE failover/failback leg (`live failover`), scripting the
manual 2026-07-13 bench (CHECKLIST): prefer_usb_route=false (wifi-first),
both uplinks up -> probe binds the STA address; hotspot DOWN (the
drive-away moment) -> within ~90 s the probe binds 192.168.7.2 and flows
via the dongle over LTE; hotspot back -> STA reconnects and routes
return to WiFi. All probes run over the SERIAL console (the mgmt path
dies with the AP): `iperf -c <public-server>` and its "local <ip>"
client-report line is the routing oracle.

Needs: DUT console on COM1175 (capture stopped first), rpi001 hotspot,
espnetlink dongle attached, a public iperf2 server (started on the bench
VPS via ssh). The rpi001 beacon watchdog is PARKED (autoconnect off)
during the outage window and restored after. prefer_usb_route restored
to its prior value. LTE transfer per probe ~2 s (BG95-safe).

  python failover_bench.py [COM1175] [10.42.0.62]
"""
import json
import re
import subprocess
import sys
import time
import urllib.request

import serial

COM = sys.argv[1] if len(sys.argv) > 1 else "COM1175"
DUT = sys.argv[2] if len(sys.argv) > 2 else "10.42.0.62"
SRV = "betty"
PI = "rpi001"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
HOTSPOTS = ["wican-bench", "wican-bench-w0"]

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def ssh_out(host, cmd, timeout=30):
    r = subprocess.run(SSH + [host, cmd], capture_output=True, text=True,
                       timeout=timeout)
    return r.stdout.strip()


DUT_CANDIDATES = ["10.42.0.62", "10.42.1.62"]  # one per hotspot radio


def dut_ip():
    """The DUT's STA address moves between the Pi's two hotspot subnets
    depending on which radio the profile came up on — find it."""
    global DUT
    for ip in [DUT] + DUT_CANDIDATES:
        out = ssh_out(PI, f"curl -s -m 4 -o /dev/null -w '%{{http_code}}' "
                          f"http://{ip}/api/status")
        if out == "200":
            DUT = ip
            return ip
    return None


def pi_api(path, method="GET", body=None):
    """DUT api via the Pi (only valid while the hotspot is up)."""
    b = ("-d '" + json.dumps(body) + "' -H 'Content-Type: application/json'"
         if body is not None else "")
    out = ssh_out(PI, f"curl -s -m 8 -X {method} http://{DUT}{path} {b}")
    return json.loads(out) if out.strip().startswith("{") else out


class Console:
    def __init__(self, port):
        self.s = serial.Serial()
        self.s.port = port
        self.s.baudrate = 2000000
        self.s.timeout = 0.2
        self.s.rts = False
        self.s.dtr = False
        self.s.open()

    def cmd_collect(self, cmd, seconds):
        self.s.reset_input_buffer()
        self.s.write((cmd + "\n").encode())
        out = b""
        end = time.time() + seconds
        while time.time() < end:
            out += self.s.read(65536)
        return out.decode(errors="replace")

    def iperf_local_ip(self, server_ip, wait=14):
        """Run a short iperf client; return the 'local <ip>' it bound."""
        out = self.cmd_collect(f"iperf -c {server_ip} -p 5001 -t 2", wait)
        m = re.findall(r"local (\d+\.\d+\.\d+\.\d+)", out)
        return m[-1] if m else None


def watchdog_park(parked):
    onoff = "no" if parked else "yes"
    for h in HOTSPOTS:
        ssh_out(PI, f"sudo nmcli con mod {h} connection.autoconnect {onoff}"
                    " 2>/dev/null || true")


def hotspot(up):
    verb = "up" if up else "down"
    ssh_out(PI, f"sudo nmcli con {verb} wican-bench 2>/dev/null || true", 40)
    if not up:  # make sure the fallback radio is down too
        ssh_out(PI, "sudo nmcli con down wican-bench-w0 2>/dev/null || true",
                40)


def main():
    betty_ip = ssh_out(SRV, "curl -s -m 8 https://api.ipify.org")
    print(f"public iperf server: (bench VPS):5001")
    ssh_out(SRV, "command -v iperf >/dev/null || "
                 "(apt-get update -qq && apt-get install -y -qq iperf)", 120)
    ssh_out(SRV, "pkill -x iperf 2>/dev/null; "
                 "nohup iperf -s -p 5001 > /tmp/iperf_bench.log 2>&1 "
                 "< /dev/null & sleep 1; pgrep -x iperf | head -1")

    if dut_ip() is None:
        sys.exit("FAIL: DUT unreachable on any hotspot subnet")

    # wifi-first preference (restore after)
    cfg = pi_api("/api/settings/usb_host_manager")
    prev_pref = bool(cfg.get("prefer_usb_route", True))
    for k in ("degraded", "pending_reboot"):
        cfg.pop(k, None)
    cfg["prefer_usb_route"] = False
    pi_api("/api/settings/usb_host_manager", "PUT", cfg)
    pi_api("/api/settings/submit", "POST", {})
    print("DUT set wifi-first, rebooting...")
    time.sleep(25)

    for _ in range(24):
        st = pi_api("/api/status")
        if isinstance(st, dict) and st.get("bits", {}).get("sta_connected"):
            break
        time.sleep(5)

    con = Console(COM)

    try:
        # A: both uplinks up -> STA route (any hotspot subnet)
        ip = con.iperf_local_ip(betty_ip)
        check("baseline routes via STA",
              ip is not None and ip.startswith("10.42."), f"local={ip}")

        # B: drive-away — hotspot down, watchdog parked
        watchdog_park(True)
        hotspot(False)
        t0 = time.time()
        ip = None
        while time.time() - t0 < 90:
            ip = con.iperf_local_ip(betty_ip)
            if ip == "192.168.7.2":
                break
            time.sleep(4)
        check("failover to LTE (binds 192.168.7.2)", ip == "192.168.7.2",
              f"local={ip} after {int(time.time() - t0)}s")

        # C: home again — hotspot back, STA route returns
        hotspot(True)
        watchdog_park(False)
        sta = False
        t0 = time.time()
        while time.time() - t0 < 150:
            if dut_ip() is not None:
                st = pi_api("/api/status")
                if isinstance(st, dict) and                         st.get("bits", {}).get("sta_connected"):
                    sta = True
                    break
            time.sleep(5)
        check("STA reconnects after AP returns", sta,
              f"{int(time.time() - t0)}s at {DUT}")

        ip = None
        t0 = time.time()
        while time.time() - t0 < 60:
            ip = con.iperf_local_ip(betty_ip)
            if ip is not None and ip.startswith("10.42."):
                break
            time.sleep(4)
        check("failback routes via STA",
              ip is not None and ip.startswith("10.42."), f"local={ip}")
    finally:
        con.s.close()
        watchdog_park(False)
        hotspot(True)
        # restore preference
        try:
            if dut_ip() is None:
                raise RuntimeError("DUT unreachable for restore")
            cfg = pi_api("/api/settings/usb_host_manager")
            if not isinstance(cfg, dict):
                raise RuntimeError(f"bad settings response: {cfg!r}")
            for k in ("degraded", "pending_reboot"):
                cfg.pop(k, None)
            cfg["prefer_usb_route"] = prev_pref
            pi_api("/api/settings/usb_host_manager", "PUT", cfg)
            pi_api("/api/settings/submit", "POST", {})
            print(f"prefer_usb_route restored to {prev_pref} (rebooting)")
        except Exception as e:
            print(f"(restore: {e})")
        ssh_out(SRV, "pkill -x iperf 2>/dev/null; rm -f /tmp/iperf_bench.log;"
                     " echo server down")

    print("FAILOVER " + ("FAIL: " + ", ".join(fails) if fails else "PASS"))
    sys.exit(1 if fails else 0)


main()
