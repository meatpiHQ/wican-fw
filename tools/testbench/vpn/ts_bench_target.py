#!/usr/bin/env python3
"""vpn_manager type=tailscale target bench (run ON rpi001 with sudo,
LOCAL-ONLY per meatpi's bench-safety rules).

Prereqs on the Pi (see components/vpn_manager/TASK_tailscale.md):
  - /tmp/headscale        headscale arm64 binary (0.23.0 verified)
  - /etc/headscale/       config.yaml bound to 10.42.0.1:8080 + keys
  - /tmp/tailscale_*/     OPTIONAL static tailscale build; when present
                          the bench also proves the DATA PLANE with a
                          real peer (ping + HTTP through the tunnel)

Flow: headscale up -> disposable preauth key (never printed) -> DUT
type=tailscale via settings -> reboot -> CONNECTED + tailnet IP ->
[optional peer legs] -> teardown (DUT vpn disabled, headscale down,
key file removed).

Expected final line: TS TARGET PASS
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

DUT = sys.argv[1] if len(sys.argv) > 1 else "10.42.0.62"
CTRL = "10.42.0.1:8080"
KEYFILE = "/tmp/ts_bench_key.txt"

fails = []
skips = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def skip(name, why):
    print(f"SKIP: {name} ({why})")
    skips.append(name)


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True,
                          text=True, **kw)


def api(path, method="GET", body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{DUT}{path}", data=data,
                                 method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def wait_online(timeout=240):
    end = time.time() + timeout
    while time.time() < end:
        try:
            return api("/api/vpn", timeout=3)
        except Exception:
            time.sleep(2)
    raise SystemExit("FAIL: DUT offline")


def put_vpn(mutate):
    cfg = api("/api/settings/vpn_manager")
    cfg.pop("degraded", None)
    cfg.pop("pending_reboot", None)
    mutate(cfg)
    api("/api/settings/vpn_manager", "PUT", cfg)


def teardown(ts_dir):
    print("teardown...")
    try:
        put_vpn(lambda c: c.update({"enabled": False,
                                    "type": "wireguard",
                                    "ts_auth_key": "",
                                    "ts_control_url": ""}))
        api("/api/restart", "POST", {})
    except Exception as e:
        print(f"(dut teardown: {e})")
    if ts_dir:
        sh(f"cd {ts_dir} && sudo ./tailscale --socket=/tmp/tsb.sock down"
           " 2>/dev/null; sudo pkill -f 'tailscaled.*tsb.sock'")
        sh("sudo rm -f /tmp/tsb_state")
    sh("sudo pkill -x headscale")
    sh(f"sudo rm -f {KEYFILE}")
    time.sleep(2)
    up = sh("pgrep -x headscale").stdout.strip()
    print("headscale down" if not up else "WARNING: headscale still up")


def main():
    if not os.path.exists("/tmp/headscale"):
        raise SystemExit("FAIL: /tmp/headscale binary missing (see "
                         "TASK_tailscale.md bench setup)")

    ts_dirs = sorted(glob.glob("/tmp/tailscale_*_arm64"))
    ts_dir = ts_dirs[-1] if ts_dirs else None

    # 1. headscale up (local-only bind per its /etc config) from FRESH
    # state — BUG_TS_PHANTOM_PEERS.md: stale server state let NVS-cached
    # phantom peers hide; with a wiped db a DUT carrying phantoms fails
    # the peer legs before the fix and passes after (the first full map
    # prunes them).
    sh("sudo pkill -x headscale; sleep 1")
    r = sh("sudo rm -f /var/lib/headscale/db.sqlite* && echo WIPED")
    if "WIPED" not in r.stdout:
        raise SystemExit("FAIL: headscale state wipe did not run: "
                         + (r.stderr or r.stdout).strip())
    subprocess.Popen("sudo nohup /tmp/headscale serve > /tmp/hs_bench.log "
                     "2>&1 &", shell=True)
    time.sleep(4)
    check("headscale serving", bool(sh("pgrep -x headscale").stdout.strip()))
    stale = sh("sudo /tmp/headscale nodes list 2>/dev/null | "
               "grep -c -e wican -e pi-bench").stdout.strip()
    check("headscale state fresh", stale in ("", "0"),
          f"stale nodes: {stale}")
    sh("sudo /tmp/headscale users create wican 2>/dev/null")
    r = sh("sudo /tmp/headscale preauthkeys create --user wican "
           f"--reusable --expiration 4h 2>/dev/null | tail -1 > {KEYFILE}"
           f" && wc -c < {KEYFILE}")
    check("preauth key created", int(r.stdout.strip() or 0) > 20)
    key = open(KEYFILE).read().strip()

    try:
        # 2. DUT -> tailscale
        put_vpn(lambda c: c.update({"enabled": True, "type": "tailscale",
                                    "ts_auth_key": key,
                                    "ts_device_name": "wican-bench",
                                    "ts_control_url": CTRL}))
        api("/api/restart", "POST", {})
        time.sleep(8)
        wait_online()

        # 3. CONNECTED + tailnet IP
        end = time.time() + 90
        v = {}
        while time.time() < end:
            try:
                v = api("/api/vpn", timeout=5)
                if v.get("state") == "connected" and v.get("ts_ip"):
                    break
            except Exception:
                pass
            time.sleep(3)
        check("tailscale CONNECTED", v.get("state") == "connected",
              json.dumps(v))
        check("tailnet IP assigned", str(v.get("ts_ip", "")).
              startswith("100."), v.get("ts_ip", ""))
        node = sh("sudo /tmp/headscale nodes list 2>/dev/null | "
                  "grep -c wican-bench").stdout.strip()
        check("node registered in headscale", node not in ("", "0"))

        # 4. optional data-plane legs with a real tailscale peer
        if ts_dir is None:
            skip("data plane (peer ping + HTTP through tunnel)",
                 "no /tmp/tailscale_*_arm64 static build on the Pi")
        else:
            subprocess.Popen(
                f"cd {ts_dir} && sudo nohup ./tailscaled "
                "--state=/tmp/tsb_state --socket=/tmp/tsb.sock "
                "> /tmp/tsdb.log 2>&1 &", shell=True)
            time.sleep(4)
            r = sh(f"cd {ts_dir} && sudo ./tailscale --socket=/tmp/tsb.sock"
                   f" up --login-server http://{CTRL} --authkey {key}"
                   " --hostname pi-bench --accept-dns=false")
            check("pi peer joined", r.returncode == 0,
                  (r.stderr or r.stdout).strip()[:60])
            dut_ip = v.get("ts_ip")
            # first traffic triggers the WG handshake (passive mode)
            sh(f"cd {ts_dir} && sudo ./tailscale --socket=/tmp/tsb.sock"
               f" ping -c 3 {dut_ip}")
            time.sleep(1)
            p = sh(f"ping -c 10 -i 0.3 -q {dut_ip}")
            check("ICMP through the tunnel", " 0% packet loss" in p.stdout,
                  p.stdout.strip().splitlines()[-2:][0] if p.stdout else "")
            h = sh(f"curl -s -m 10 -o /dev/null -w '%{{http_code}}' "
                   f"http://{dut_ip}/api/status")
            check("HTTP through the tunnel", h.stdout.strip() == "200",
                  f"code={h.stdout.strip()}")
            # peer visible from the DUT side
            v2 = api("/api/vpn")
            check("DUT sees the peer", v2.get("ts_peers", 0) >= 1,
                  f"ts_peers={v2.get('ts_peers')}")
            # peer detail surface: hostname/ip/online/path per peer
            pl = v2.get("peers", [])
            check("peer detail exposed (pi-bench online)",
                  any(p.get("hostname", "").startswith("pi-bench") and
                      p.get("online") for p in pl),
                  json.dumps(pl))
    finally:
        teardown(ts_dir)

    if fails:
        print("TS TARGET FAIL:", ", ".join(fails))
        sys.exit(1)
    tail = f" ({len(skips)} skipped)" if skips else ""
    print(f"TS TARGET PASS{tail}")


if __name__ == "__main__":
    main()
