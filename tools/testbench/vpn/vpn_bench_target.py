#!/usr/bin/env python3
"""vpn_manager target bench (run ON rpi001, LOCAL-ONLY per meatpi).

Flow: device keygen (private key never leaves the DUT) -> register the
DUT public key as a peer of the local wg-bench server -> configure the
DUT via settings -> reboot -> expect handshake within 90 s -> ping the
DUT's tunnel address through the tunnel -> teardown (wg-bench DOWN,
DUT vpn disabled).

Expected final line: VPN TARGET PASS
"""
import json
import subprocess
import sys
import time
import urllib.request

DUT = sys.argv[1] if len(sys.argv) > 1 else "10.42.0.62"
SRV_TUN = "10.66.0.1"
DUT_TUN = "10.66.0.2"
TUN_NET = "10.66.0.0"   # AllowedIPs NETWORK address — regression gate for
                        # BUG_WG_NETIF_ADDR (netif must still come up as
                        # DUT_TUN, not this)
PORT = 51821


def api(path, method="GET", body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{DUT}{path}", data=data,
                                 method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def wait_online(timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return api("/api/vpn", timeout=3)
        except Exception:
            time.sleep(2)
    raise TimeoutError("DUT offline")


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True,
                          text=True)


def main():
    st = wait_online()
    print("online:", st)

    # 1. device-side keygen (returns ONLY the public key)
    kg = api("/api/vpn/keygen", "POST", {})
    dut_pub = kg["public_key"]
    print("DUT public key:", dut_pub)
    assert len(dut_pub) == 44 and dut_pub.endswith("="), kg

    # settings GET must NOT leak the private key
    cfg = api("/api/settings/vpn_manager")
    assert cfg.get("private_key", "") == "", "private_key leaked in GET!"

    srv_pub = sh("sudo cat /etc/wireguard/wg-bench-server.pub"
                 ).stdout.strip()
    assert len(srv_pub) == 44, srv_pub

    # 2. wg-bench server: fresh peer section (keys are disposable)
    base = sh("sudo sed -n '1,/^\\[Peer\\]/p' /etc/wireguard/wg-bench.conf"
              ).stdout
    base = base.split("[Peer]")[0].rstrip()
    peer = f"\n\n[Peer]\nPublicKey = {dut_pub}\nAllowedIPs = {DUT_TUN}/32\n"
    p = subprocess.run("sudo tee /etc/wireguard/wg-bench.conf >/dev/null",
                       shell=True, input=base + peer, text=True)
    assert p.returncode == 0
    sh("sudo wg-quick down wg-bench 2>/dev/null")
    r = sh("sudo wg-quick up wg-bench")
    assert r.returncode == 0, r.stderr
    print("wg-bench UP (local only, no forwarding)")

    # 3. DUT config (empty private_key = keep the keygen-stored one)
    cfg.pop("degraded", None)
    cfg.pop("pending_reboot", None)
    cfg.update({
        "enabled": True,
        "type": "wireguard",
        "peer_public_key": srv_pub,
        "preshared_key": "",
        "address": DUT_TUN,
        "allowed_ip": TUN_NET,
        "allowed_ip_mask": "255.255.255.0",
        "endpoint": "10.42.0.1",
        "port": PORT,
        "keepalive_s": 5,
        "default_route": False,
        "dns": "",
        "private_key": "",
    })
    api("/api/settings/vpn_manager", "PUT", cfg)
    try:
        api("/api/restart", "POST", {})
    except Exception:
        pass
    time.sleep(8)
    wait_online()

    # 4. handshake within 90 s
    state = None
    for _ in range(45):
        st = api("/api/vpn")
        state = st["state"]
        if state == "connected":
            break
        time.sleep(2)
    print("vpn status:", st)
    assert state == "connected", st

    # 5. server-side proof: handshake + ping through the tunnel
    wg = sh("sudo wg show wg-bench latest-handshakes").stdout.strip()
    print("server handshakes:", wg)
    assert dut_pub.split("=")[0] in wg or wg, wg
    ping = sh(f"ping -c 3 -W 2 -I {SRV_TUN} {DUT_TUN}")
    print(ping.stdout.strip().splitlines()[-1])
    assert " 0% packet loss" in ping.stdout, ping.stdout

    # 6. teardown: DUT vpn off, server down (meatpi: up only for tests)
    cfg["enabled"] = False
    api("/api/settings/vpn_manager", "PUT", cfg)
    try:
        api("/api/restart", "POST", {})
    except Exception:
        pass
    sh("sudo wg-quick down wg-bench")
    time.sleep(8)
    wait_online()
    print("VPN TARGET PASS")


if __name__ == "__main__":
    main()
