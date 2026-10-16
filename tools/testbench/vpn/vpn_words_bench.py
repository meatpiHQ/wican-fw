#!/usr/bin/env python3
"""The HA webhook push carries vpn_manager's own state word (issue #985,
2026-10-16): the on-device proof in every state that matters.

ha_webhooks mapped `vpn_state_t` through a four-entry table after
VPN_STATE_PAUSED joined the enum at index 2: `connected` (4) read past the
table and `paused` pushed "connecting". The push takes
`vpn_manager_state_str()` now. This bench puts the DUT in each state and runs
the station-address webhook bench (`ha_webhook_sta_ip_bench.py`, which checks
`status.vpn_status` against GET /api/vpn and the five words since the same
day) in it:

  w0  as found: the vpn_manager and wifi_manager objects (GET
      /api/settings/backup, the version-1 export, secrets verbatim) saved
      under the log dir, GET /api/vpn
  w1  disabled: the push bench as found (skipped when the VPN is on)
  w2  connected: a device keypair (POST /api/vpn/keygen), the Pi's LOCAL
      wg-bench (10.66.0.1/24, udp 51821) with the DUT as its peer, the DUT
      configured to it (a restart), `connected` within 90 s, the push bench:
      vpn_status "connected" and vpn_ip present
  w3  paused: the bench access point marked Pause VPN (wifi_manager
      sta_vpn_pause, a restart), `paused` within 60 s, the push bench:
      vpn_status "paused", no vpn_ip
  w4  as found: both objects restored through POST /api/backup/inspect +
      restore (the device's own keys come back verbatim; a restart), wg-bench
      stopped, the VPN state as found
  close: no unexpected reset, no E line of vpn_manager / ha_webhooks /
      config_backup, the heap floor

  python tools/testbench/vpn/vpn_words_bench.py [--dut localhost:18081]
      [--dut-ip 10.42.1.194] [--pi rpi001] [--pi-ip 10.42.1.1] [--port 8197]
      [--only w1,w2] [--logdir DIR]

Prints `VPN WORDS PASS`. Secrets are never printed.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import pcbench  # noqa: E402

OWN_TAGS = ("vpn_manager", "ha_webhooks", "config_backup")
LEGS = ["w0", "w1", "w2", "w3", "w4"]
DUT_TUN = "10.66.0.2"
TUN_ROUTE = "10.66.0.0/24"
WG_PORT = 51821
STA_IP_BENCH = os.path.join(HERE, "..", "ha", "ha_webhook_sta_ip_bench.py")


def ssh(host, cmd, timeout=60):
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                            host, cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return 124, "ssh timeout"


def raw(base, path, method="GET", data=None, timeout=60):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="", help="host:port of the DUT's API (a tunnel); "
                    "default: open one to --dut-ip through --pi")
    ap.add_argument("--dut-ip", default="10.42.1.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--pi-ip", default="", help="the Pi as the DUT sees it (default: the "
                    "station's network .1)")
    ap.add_argument("--port", type=int, default=8197, help="the receiver's port on the Pi")
    ap.add_argument("--only", default="")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS if not only or l in only or l in ("w0", "w4")]
    logdir = a.logdir or os.path.join(HERE, "..", "..", "..", "test-reports", "logs",
                                      time.strftime("vpn_words_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("VPN WORDS")

    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18085"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-N", "-L",
                                   f"18085:{a.dut_ip}:80", a.pi])
        time.sleep(2)
    dut = pcbench.Dut(hostport, own_tags=OWN_TAGS)
    base = dut.base

    def save(name, text):
        with open(os.path.join(logdir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def vpn_state(want, timeout):
        t0 = time.time()
        last = None
        while time.time() - t0 < timeout:
            code, v = dut.api("/api/vpn", timeout=5)
            if code == 200 and isinstance(v, dict):
                last = v
                if v.get("state") == want:
                    return v, time.time() - t0
            time.sleep(2)
        return last, time.time() - t0

    def push_bench(tag, want):
        """The station-address bench in the current state: its verdict and the
        vpn lines; its own planned reboot (an interval restore) counted."""
        b0 = dut.boot_count()
        cmd = [sys.executable, "-u", STA_IP_BENCH, "--dut", hostport, "--pi", a.pi,
               "--pi-ip", pi_ip, "--port", str(a.port)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=400)
        out = r.stdout + r.stderr
        save(f"{tag}_push_bench.log", out)
        fails = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
        verdict = "HA WEBHOOK STA IP PASS" in out
        word = re.search(r"status\.vpn_status == GET /api/vpn state.*?\((.*?)\)", out)
        run.check(f"{tag}_push_bench_pass", verdict and not fails,
                  "; ".join(fails)[:300] or (word.group(1) if word else out[-200:]))
        # the push bench prints "PASS: <name>  (<detail>)": two spaces before the detail
        said = re.search(r"PASS: status\.vpn_status is one of vpn_manager's five words\s+\((.*?)\)",
                         out)
        run.check(f"{tag}_push_word_is_{want}",
                  bool(said) and said.group(1) == repr(want),
                  said.group(1) if said else "no word line")
        b1 = dut.boot_count()
        if b0 is not None and b1 is not None and b1 != b0:
            dut.restarts += b1 - b0   # the sub-bench's planned reboot(s)

    def v1_doc(objs):
        return {"wican_backup": 1, "device_id": info.get("device_id"),
                "components": {n: {"version": o["version"], "data": o["data"]}
                               for n, o in objs.items()}}

    wg_up = False
    exit_code = 1
    try:
        # ---- w0: as found ---------------------------------------------------------
        info = dut.get("/api/info")
        st0 = dut.get("/api/status")
        dut.as_found()
        wst = dut.get("/api/wifi/status")
        if not wst.get("sta_connected"):
            raise pcbench.Bench("the station is not connected: the bench needs the DUT "
                                "on the bench access point")
        sta_ip = str(wst.get("ip"))
        pi_ip = a.pi_ip or re.sub(r"\.\d+$", ".1", sta_ip)
        print(f"DUT {info.get('device_id')} {info.get('fw_version')} station on {sta_ip}, "
              f"the Pi at {pi_ip}", flush=True)
        code, text = raw(base, "/api/settings/backup")
        found = json.loads(text) if code == 200 else {}
        comps = found.get("components", {})
        objs = {n: comps.get(n) for n in ("vpn_manager", "wifi_manager")}
        save("w0_as_found_settings_v1.json", text)   # secrets inside: the log dir only
        run.check("w0_as_found_objects", code == 200 and all(isinstance(o, dict) and
                  isinstance(o.get("data"), dict) for o in objs.values()),
                  f"{code} {[n for n, o in objs.items() if not o]}")
        if not all(objs.values()):
            raise pcbench.Bench("the settings export lacks vpn_manager or wifi_manager")
        vpn0 = dut.get("/api/vpn")
        print(f"as found: vpn {vpn0.get('state')}, sta_vpn_pause "
              f"{objs['wifi_manager']['data'].get('sta_vpn_pause')}", flush=True)

        # ---- w1: disabled --------------------------------------------------------------
        if "w1" in legs:
            if vpn0.get("state") == "disabled":
                push_bench("w1", "disabled")
            else:
                print(f"w1: the VPN is {vpn0.get('state')} as found: the disabled leg is SKIPPED",
                      flush=True)

        # ---- w2: connected through the Pi's wg-bench ---------------------------------
        if "w2" in legs or "w3" in legs:
            rc, srv_pub = ssh(a.pi, "sudo -n cat /etc/wireguard/wg-bench-server.pub")
            srv_pub = srv_pub.strip()
            if rc != 0 or len(srv_pub) != 44:
                raise pcbench.Bench(f"wg-bench server key on {a.pi}: {srv_pub[:40]!r}")
            code, kg = dut.api("/api/vpn/keygen", "POST", {})
            if code != 200 or not isinstance(kg, dict) or not kg.get("public_key"):
                raise pcbench.Bench(f"keygen: {code} {str(kg)[:80]}")
            rc, out = ssh(a.pi, f"wg-bench peer {kg['public_key']} && wg-bench start")
            wg_up = rc == 0
            run.check("w2_wg_bench_up", wg_up, out.strip().splitlines()[-1:])
            if not wg_up:
                raise pcbench.Bench("wg-bench did not start on the Pi")
            vpn_cfg = {
                "enabled": True, "type": "wireguard", "peer_public_key": srv_pub,
                "preshared_key": "", "address": DUT_TUN, "routes": TUN_ROUTE,
                "endpoint": pi_ip, "port": WG_PORT, "keepalive_s": 5,
                "default_route": False, "dns": "", "private_key": "",
            }
            dut.restart({"vpn_manager": vpn_cfg, "wifi_manager": {"sta_vpn_pause": False}})
            v, took = vpn_state("connected", 90)
            run.check("w2_connected", bool(v) and v.get("state") == "connected",
                      f"{(v or {}).get('state')} after {took:.0f} s")
            if "w2" in legs:
                push_bench("w2", "connected")

        # ---- w3: paused on the bench access point -------------------------------------
        if "w3" in legs:
            dut.restart({"wifi_manager": {"sta_vpn_pause": True}})
            v, took = vpn_state("paused", 60)
            run.check("w3_paused", bool(v) and v.get("state") == "paused",
                      f"{(v or {}).get('state')} after {took:.0f} s")
            push_bench("w3", "paused")

        # ---- w4: as found ---------------------------------------------------------------
        doc = v1_doc(objs)
        code, t = raw(base, "/api/backup/inspect?name=w4_as_found.json", "POST",
                      json.dumps(doc).encode())
        man = json.loads(t) if code == 200 else {}
        ok = code == 200 and not man.get("refused") and man.get("errors") == {}
        run.check("w4_as_found_inspected", ok, f"{code} {str(man)[:160]}")
        if ok:
            b0 = dut.boot_count()
            dut.sweep()
            code, r = dut.api("/api/backup/restore", "POST",
                              {"include": ["settings"], "keep_identity": True,
                               "keep_vpn_keys": False}, timeout=120)
            run.check("w4_as_found_restored", code == 200 and isinstance(r, dict)
                      and r.get("reboot") is True, f"{code} {str(r)[:160]}")
            t0 = time.time()
            time.sleep(3)
            while time.time() - t0 < 150:
                b = dut.boot_count()
                if b is not None and b != b0:
                    dut.restarts += 1
                    break
                time.sleep(0.5)
            else:
                raise pcbench.Bench("the DUT did not come back after the restore"
                                    + pcbench.GONE_HINT)
            time.sleep(4)
        if wg_up:
            ssh(a.pi, "wg-bench stop >/dev/null 2>&1; true")
            wg_up = False
        v4 = dut.get("/api/vpn")
        run.check("w4_vpn_state_as_found", v4.get("state") == vpn0.get("state"),
                  f"{v4.get('state')} vs {vpn0.get('state')}")
        wm = dut.settings("wifi_manager")
        run.check("w4_pause_flag_as_found",
                  wm.get("sta_vpn_pause") == objs["wifi_manager"]["data"].get("sta_vpn_pause"),
                  str(wm.get("sta_vpn_pause")))

        # ---- close -----------------------------------------------------------------------
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        exit_code = run.verdict("legs: " + " ".join(legs) if only else "")
    except pcbench.Bench as e:
        print("BENCH:", e, flush=True)
        print(f"the as-found objects are in {logdir}/w0_as_found_settings_v1.json "
              "(restore through POST /api/backup/inspect + restore)", flush=True)
        exit_code = 2
    finally:
        if wg_up:
            ssh(a.pi, "wg-bench stop >/dev/null 2>&1; true")
        if tunnel is not None:
            tunnel.terminate()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
