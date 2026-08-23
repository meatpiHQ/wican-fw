#!/usr/bin/env python3
"""USB-Ethernet target bench (run ON rpi001).

Physical setup required: the DUT's USB connector in HOST mode with the
USB-Ethernet adapter plugged, cabled to the Pi's eth0 (the `eth-bench`
NetworkManager shared profile serves DHCP 10.42.1.x). If the connector
is wired to a PC instead (CH342 device role), this bench cannot run —
test.ps1's bench preflight detects that and SKIPs the leg.

Flow: verify /api/usb reports the adapter up -> HTTP over the usb-eth
netif -> throughput sample -> reboot -> re-enumeration + single clean
attach -> HTTP over usb-eth again.

Expected final line: USB ETH TARGET PASS
"""
import json
import sys
import time
import urllib.request

DUT = sys.argv[1] if len(sys.argv) > 1 else "10.42.0.62"


def api(host, path, method="GET", body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{host}{path}", data=data,
                                 method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def wait_online(timeout=240):
    end = time.time() + timeout
    while time.time() < end:
        try:
            return api(DUT, "/api/status", timeout=3)
        except Exception:
            time.sleep(2)
    raise SystemExit(f"FAIL: DUT {DUT} offline after {timeout}s")


fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    # 1. Baseline USB status over WiFi
    usb = api(DUT, "/api/usb")
    print("usb status:", json.dumps(usb))
    check("enabled+present+host_active",
          usb.get("enabled") and usb.get("device_present") and
          usb.get("host_active"))
    check("eth_connected", usb.get("eth_connected"))
    check("driver reported", bool(usb.get("driver")), usb.get("driver", "?"))
    eth_ip = usb.get("ip", "")
    check("dhcp ip", eth_ip.startswith("10.42.1."), eth_ip or "?")
    if not eth_ip:
        raise SystemExit("USB ETH TARGET FAIL: no eth ip")

    # 2. HTTP over the USB-eth interface itself
    st = api(eth_ip, "/api/status", timeout=5)
    check("http over usb-eth", st.get("bits", {}).get("eth_connected") is True)

    # 3. Throughput sample over usb-eth
    total = 0
    t0 = time.time()
    while time.time() - t0 < 5:
        with urllib.request.urlopen(f"http://{eth_ip}/api/logs/ring",
                                    timeout=10) as r:
            total += len(r.read())
    kbps = total / (time.time() - t0) / 1024
    print(f"throughput sample: {kbps:.0f} KB/s")
    check("throughput > 50 KB/s", kbps > 50, f"{kbps:.0f} KB/s")

    # 4. Reboot -> must re-enumerate without help
    print("rebooting DUT...")
    try:
        api(DUT, "/api/restart", "POST", {})
    except Exception:
        pass  # connection may drop mid-response
    time.sleep(8)
    wait_online()

    deadline = time.time() + 90
    usb2 = {}
    while time.time() < deadline:
        try:
            usb2 = api(DUT, "/api/usb", timeout=5)
            if usb2.get("eth_connected") and \
               usb2.get("ip", "").startswith("10.42.1."):
                break
        except Exception:
            pass
        time.sleep(3)
    print("usb status after reboot:", json.dumps(usb2))
    check("re-enumerated after reboot", usb2.get("eth_connected") is True)
    check("single clean attach post-boot", usb2.get("attaches") == 1,
          f"attaches={usb2.get('attaches')}")

    # 5. Traffic over usb-eth again post-reboot (IP may have changed)
    ip2 = usb2.get("ip", eth_ip)
    st2 = api(ip2, "/api/status", timeout=5)
    check("http over usb-eth post-reboot",
          st2.get("bits", {}).get("eth_connected") is True)

    if fails:
        print("USB ETH TARGET FAIL:", ", ".join(fails))
        sys.exit(1)
    print("USB ETH TARGET PASS")


if __name__ == "__main__":
    main()
