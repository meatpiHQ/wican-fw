#!/usr/bin/env python3
"""WiFi RAM-profile A/B: `full` vs `lean`, decision-grade numbers.

The `wifi_ram_profile` setting exists since 2026-07-08 (lean = static
RX 10->6, static TX 8->4, cache TX 32->16, ~14.4 KB internal freed);
the open ruling is whether LEAN becomes the DEFAULT. The original A/B
predates iperf_manager, so its "no throughput cost" verdict was
app-capped (~900 KB/s) and never probed the buffer-limited ceiling.
This bench measures, per profile (apsta, canonical bench state):

  - internal RAM: free / min_free / largest_block (plus PSRAM floor)
  - iperf2 TCP, BOTH directions x N runs (Pi 2.1.8 <-> iperf_manager;
    DUT self-reports its client runs, Pi reports the rest)
  - iperf2 UDP Pi->DUT at -b 20M (loss visible on the DUT report)
  - HTTP RTT p50/p95 (20x GET /api/status from the Pi)

Prints an A/B table; restores the profile the device started with.
Needs: rpi001 (iperf installed), DUT with ws_cli + iperf cli enabled.

    python tools/testbench/wifi_profile_ab_test.py
Expected final line: WIFI PROFILE AB DONE
"""
import json
import re
import subprocess
import sys
import time

RUNS = 2
SECS = 10

fails = []


def pi(cmd, stdin=None, timeout=240):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "rpi001", cmd],
                       input=stdin, capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout)
    return p.stdout


def pi_retry(cmd, stdin=None, timeout=240, tries=3):
    for i in range(tries):
        try:
            out = pi(cmd, stdin, timeout)
            if out.strip():
                return out
        except subprocess.TimeoutExpired:
            pass
        time.sleep(3)
    return ""


def dut_ip():
    return pi_retry(
        "sudo cat /var/lib/NetworkManager/dnsmasq-wint0.leases "
        "/var/lib/NetworkManager/dnsmasq-wtest0.leases 2>/dev/null"
        " | sort -n | tail -1 | awk '{print $3}'").strip()


def api_get(ip, path):
    out = pi_retry(f"curl -s -m 8 http://{ip}{path}")
    try:
        return json.loads(out)
    except ValueError:
        return {}


def set_profile(ip, profile):
    cfg = api_get(ip, "/api/settings/wifi_manager")
    if not cfg:
        return None
    cfg.pop("degraded", None)
    cfg.pop("pending_reboot", None)
    prev = cfg.get("wifi_ram_profile", "full")
    if prev == profile:
        return prev
    cfg["wifi_ram_profile"] = profile
    body = json.dumps(cfg)
    pi(f"curl -s -m 8 -X PUT -H 'Content-Type: application/json' "
       f"-d @- http://{ip}/api/settings/wifi_manager", stdin=body)
    pi(f"curl -s -m 8 -X POST http://{ip}/api/settings/submit")
    time.sleep(8)
    # the DUT reboots and may roam between hotspot twins
    for _ in range(40):
        nip = dut_ip()
        if nip and api_get(nip, "/api/status").get("version"):
            time.sleep(5)
            return prev
        time.sleep(3)
    return None


# one Pi-side driver runs a whole measurement pass (ws_cli + local iperf)
LEG = r"""
import websocket, subprocess, time, re, sys, urllib.request

DUT = '%DUT%'
PI = DUT.rsplit('.', 1)[0] + '.1'   # the hotspot gateway = the Pi
SECS = %SECS%

def cli(cmd, wait=1.0):
    ws = websocket.create_connection('ws://%s/ws/cli' % DUT, timeout=5)
    ws.settimeout(2)
    ws.send(cmd + '\n')
    out = ''
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            out += ws.recv()
        except Exception:
            break
    ws.close()
    return out

# --- TCP DUT RX (Pi -> DUT): DUT serves, Pi client reports ---
cli('iperf -s', 0.3)
time.sleep(0.5)
r = subprocess.run(['iperf', '-c', DUT, '-t', str(SECS), '-i', '60',
                    '-f', 'm'], capture_output=True, text=True,
                   timeout=SECS + 25)
m = re.findall(r'([0-9.]+) Mbits/sec', r.stdout)
print('TCP_RX_MBPS', m[-1] if m else 'NA', flush=True)
time.sleep(2)

# --- TCP DUT TX (DUT -> Pi): Pi serves, DUT client self-reports ---
srv = subprocess.Popen(['iperf', '-s', '-f', 'm'],
                       stdout=subprocess.PIPE, text=True)
time.sleep(0.5)
cli('iperf -c %s -t %d' % (PI, SECS), 0.3)
time.sleep(SECS + 4)
srv.terminate()
try:
    so = srv.communicate(timeout=5)[0]
except Exception:
    so = ''
m = re.findall(r'([0-9.]+) Mbits/sec', so)
print('TCP_TX_MBPS', m[-1] if m else 'NA', flush=True)
time.sleep(1)

# --- UDP DUT RX (Pi -> DUT at 20M): DUT serves ---
cli('iperf -s -u', 0.3)
time.sleep(0.5)
r = subprocess.run(['iperf', '-c', DUT, '-u', '-b', '20M', '-t',
                    str(SECS), '-i', '60', '-f', 'm'],
                   capture_output=True, text=True, timeout=SECS + 25)
rep = cli('iperf -r', 1.5)
m = re.findall(r'([0-9.]+) Mbits/sec', rep)
loss = re.search(r'([0-9.]+)%', rep)
print('UDP_RX_MBPS', m[-1] if m else 'NA',
      'LOSS', loss.group(1) if loss else 'NA', flush=True)

# --- HTTP RTT ---
ts = []
for i in range(20):
    t0 = time.time()
    try:
        urllib.request.urlopen('http://%s/api/status' % DUT,
                               timeout=5).read()
        ts.append((time.time() - t0) * 1000)
    except Exception:
        pass
ts.sort()
if ts:
    print('HTTP_RTT_MS p50 %.1f p95 %.1f' %
          (ts[len(ts) // 2], ts[int(len(ts) * 0.95) - 1]), flush=True)
"""


def measure(ip):
    res = {"tcp_rx": [], "tcp_tx": [], "udp_rx": [], "loss": [],
           "rtt": ""}
    st = api_get(ip, "/api/status")
    res["mem"] = st.get("memory", {})
    for _ in range(RUNS):
        out = pi_retry("python3 -",
                       stdin=LEG.replace("%DUT%", ip)
                                .replace("%SECS%", str(SECS)),
                       timeout=SECS * 3 + 120)
        for key, tag in (("tcp_rx", "TCP_RX_MBPS"),
                         ("tcp_tx", "TCP_TX_MBPS"),
                         ("udp_rx", "UDP_RX_MBPS")):
            m = re.search(tag + r" ([0-9.]+)", out)
            if m:
                res[key].append(float(m.group(1)))
        m = re.search(r"LOSS ([0-9.]+)", out)
        if m:
            res["loss"].append(float(m.group(1)))
        m = re.search(r"HTTP_RTT_MS (.+)", out)
        if m:
            res["rtt"] = m.group(1)
        time.sleep(3)
    return res


def fmt(vals):
    return "/".join(f"{v:g}" for v in vals) if vals else "NA"


def main():
    ip = dut_ip()
    print("DUT", ip)
    orig = api_get(ip, "/api/settings/wifi_manager") \
        .get("wifi_ram_profile", "full")
    print("starting profile:", orig)

    results = {}
    for profile in ("full", "lean"):
        print(f"\n=== profile {profile} ===")
        if set_profile(ip, profile) is None:
            print("FATAL: DUT did not come back after profile switch")
            return 1
        ip = dut_ip()
        time.sleep(20)  # settle: mDNS/associations/boot transients
        results[profile] = measure(ip)
        mem = results[profile]["mem"].get("internal", {})
        print(f"  internal free {mem.get('free')} min {mem.get('min_free')}"
              f" largest {mem.get('largest_block')}")
        print(f"  tcp_rx {fmt(results[profile]['tcp_rx'])}  "
              f"tcp_tx {fmt(results[profile]['tcp_tx'])}  "
              f"udp_rx {fmt(results[profile]['udp_rx'])} "
              f"loss {fmt(results[profile]['loss'])}%")
        print(f"  http_rtt {results[profile]['rtt']}")

    # restore what the device shipped into the bench with
    set_profile(ip, orig)
    ip = dut_ip()

    print("\n=== A/B SUMMARY (full vs lean) ===")
    for p in ("full", "lean"):
        r = results[p]
        mi = r["mem"].get("internal", {})
        print(f"{p:5}: int free {mi.get('free', 0):6} "
              f"min {mi.get('min_free', 0):6} "
              f"largest {mi.get('largest_block', 0):6} | "
              f"TCP rx {fmt(r['tcp_rx'])} tx {fmt(r['tcp_tx'])} | "
              f"UDP rx {fmt(r['udp_rx'])} loss {fmt(r['loss'])}% | "
              f"rtt {r['rtt']}")
    print("WIFI PROFILE AB DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
