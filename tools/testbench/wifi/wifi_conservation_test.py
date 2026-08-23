#!/usr/bin/env python3
"""WiFi byte-conservation bench (standard §7 performance-truth).

Pi = standard iperf2 UDP client (prints its exact sent total);
DUT = iperf server (our firmware's RX path), self-reporting via
`iperf -r` over ws_cli. Our fw's iperf CLIENT lacks iperf2's
end-of-stream handshake, so this direction is the one where both
sides account. Assert sent-vs-received delta under a WiFi-sane bound.

Run on the PC:  python tools/testbench/wifi_conservation_test.py
Expected final line: WIFI CONSERVATION PASS
"""
import re
import subprocess
import sys
import threading
import time

MBITS = 20
SECS = 10
LOSS_MAX_PCT = 5.0


def pi(cmd, stdin=None, timeout=120):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "rpi001", cmd],
                       input=stdin, capture_output=True, text=True,
                       timeout=timeout)
    return p.stdout


def main():
    ip = pi("sudo cat /var/lib/NetworkManager/dnsmasq-wint0.leases "
            "/var/lib/NetworkManager/dnsmasq-wtest0.leases 2>/dev/null | "
            "sort -n | tail -1 | awk '{print $3}'").strip()
    print(f"Pi client -> DUT server {ip}:5001, UDP {MBITS} Mbit {SECS}s")

    driver = (
        "import websocket, time\n"
        f"ws = websocket.create_connection('ws://{ip}/ws/cli', timeout=5)\n"
        "ws.settimeout(3)\n"
        f"ws.send('iperf -s -u -p 5001 -t {SECS + 8}\\n')\n"
        f"time.sleep({SECS + 6})\n"
        "ws.send('iperf -r\\n')\n"
        "out = ''\n"
        "t0 = time.time()\n"
        "while time.time() - t0 < 5:\n"
        "    try:\n"
        "        out += ws.recv()\n"
        "    except Exception:\n"
        "        break\n"
        "ws.send('iperf -a\\n')\n"
        "print(out)\n"
        "ws.close()\n")

    box = {}

    def run_dut():
        box["out"] = pi("python3 -", stdin=driver, timeout=SECS + 30)

    t = threading.Thread(target=run_dut)
    t.start()
    time.sleep(3)  # DUT server comes up
    cli_out = pi(f"iperf -c {ip} -u -p 5001 -b {MBITS}M -t {SECS} -i 60"
                 " 2>&1")
    t.join()
    dut_out = box.get("out", "")
    print("--- Pi client:", cli_out[-300:])
    print("--- DUT server:", dut_out[-400:])

    sent_dg = re.search(r"Sent\s+(\d+)\s+datagrams", cli_out)
    sent_mb = re.search(r"([\d.]+)\s+MBytes", cli_out)
    got_kb = re.search(r"total\s+(\d+)\s+KBytes", dut_out)

    ok = True
    if not (sent_mb and got_kb):
        print("FAIL: missing accounting on one side")
        ok = False
    else:
        tx_kb = float(sent_mb.group(1)) * 1024
        rx_kb = float(got_kb.group(1))
        loss = 100.0 * (tx_kb - rx_kb) / tx_kb
        extra = f" ({sent_dg.group(1)} datagrams)" if sent_dg else ""
        print(f"accounting: Pi sent {tx_kb:.0f} KB{extra}, DUT "
              f"received {rx_kb:.0f} KB, delta {loss:.2f}%")
        if abs(loss) > LOSS_MAX_PCT:
            print(f"FAIL: loss {loss:.2f}% > {LOSS_MAX_PCT}%")
            ok = False

    print("WIFI CONSERVATION " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
