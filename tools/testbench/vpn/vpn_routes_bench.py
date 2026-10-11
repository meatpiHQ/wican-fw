#!/usr/bin/env python3
"""The split tunnel: only the listed networks go through WireGuard (issue #975).

The claim: vpn_manager `routes` (a comma list of networks in CIDR form) is what
enters the tunnel, through ip4_routes (lwIP's IPv4 route hook wrapped at link
time); everything else leaves over the normal connection; `default_route` still
sends everything through the tunnel.

Rig (TESTING.md): the DUT on the bench AP (the Pi's hotspot, 10.42.0.1 = the
WireGuard endpoint and the receiver's plain address), rpi001 running the LOCAL
WireGuard server `wg-bench` (10.66.0.1, udp 51821) with an ALIAS network behind
it (10.77.0.1/24 added to the wg-bench interface for the run), and
`tools/testbench/actors/dd_receiver.py` on the Pi as the judge: every request
it records carries `peer`, the source address, which tells a request that came
through the tunnel (10.66.0.2) from one that came over the station
(10.42.0.x). The DUT's requests are `POST /api/destinations/test` on two http
rows the bench stages: `vpnroute` -> the alias address, `vpnplain` -> the
hotspot address.

Legs:
  r1  the VPN up with routes = the tunnel network + the alias network:
      connected within 90 s, /api/vpn lists both routes, the boot log names
      them (`ip4_routes: 10.77.0.0/24 via ...`)
  r2  the test to the alias answers 200 and the receiver saw it from 10.66.0.2
  r3  the test to the hotspot address (the station's own LAN) answers 200 from
      the station address, and so does the test to the FAR address (a second
      alias on the Pi that is on no local network and in no route: without
      "route all traffic" it leaves over the station, to the Pi as the
      gateway)
  r4  the validator: `10.66.0.0/24, nonsense` refused 400 naming the token,
      nine networks refused `more than 8`
  r5  default_route on (one restart): the far request now arrives from the
      tunnel, while the LAN one still comes from the station (a directly
      connected network is never pulled into the tunnel: standard IP routing,
      the default route only takes what no netif's own network holds)
  r6  as found: the three settings documents back, the alias and the receiver
      gone, wg-bench down, planned restarts only, no E line of the component
      tags

  python tools/testbench/vpn/vpn_routes_bench.py [--dut-ip 10.42.0.194]
      [--dut localhost:18081] [--pi rpi001] [--pi-ip 10.42.0.1]
      [--alias 10.77.0.1/24] [--far 10.88.0.1/24] [--only r1,r2] [--logdir DIR]

Prints `VPN ROUTES PASS` (or `VPN ROUTES PARTIAL PASS (legs: ...)` with --only).
Written 2026-10-11 against the mock-verified firmware; NOT yet run on hardware.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402

DUT_TUN = "10.66.0.2"
SRV_TUN = "10.66.0.1"
TUN_ROUTE = "10.66.0.0/24"
WG_PORT = 51821
RECV_PORT = 8199
RECV_UNIT = "dd-recv-routes"
OWN_TAGS = ("vpn_manager", "ip4_routes", "data_destinations")
LEGS = ("r1", "r2", "r3", "r4", "r5")


def ssh(pi, cmd, timeout=60):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o",
                        "ConnectTimeout=10", pi, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def receiver_records(pi, since=0):
    rc, out = ssh(pi, f"curl -s -m 8 'http://127.0.0.1:{RECV_PORT}/__dump?since={since}'")
    try:
        return json.loads(out.strip() or "[]")
    except ValueError:
        return []


def dest_row(name, url):
    """One http destination row as the settings table wants it (every key)."""
    return {"name": name, "type": "http", "enabled": True, "url": url,
            "period_s": 86400, "auth": "none", "auth_token": "",
            "auth_name": "", "basic_username": "", "basic_password": "",
            "api_key": "", "query": "", "cert_set": "", "car_model": "",
            "retain": True, "full_first": True}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="", help="host:port of the DUT's API "
                    "(a tunnel); default: open one to --dut-ip through --pi")
    ap.add_argument("--dut-ip", default="10.42.0.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--pi-ip", default="",
                    help="the Pi's address on the DUT's network (the WireGuard "
                    "endpoint and the plain receiver address); default: the "
                    "DUT's address with .1")
    ap.add_argument("--alias", default="10.77.0.1/24",
                    help="the network behind the tunnel, added to wg-bench on "
                    "the Pi for the run")
    ap.add_argument("--far", default="10.88.0.1/24",
                    help="an address on no local network and in no route (a "
                    "second alias on the Pi's wg-bench): the default-route "
                    "witness")
    ap.add_argument("--only", default="", help="comma list of legs")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS if not only or l in only]
    logdir = a.logdir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "test-reports", "logs", time.strftime("vpn_routes_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("VPN ROUTES")

    alias_ip = a.alias.split("/")[0]
    alias_net = re.sub(r"\.\d+/", ".0/", a.alias) if "/" in a.alias else alias_ip + "/32"
    far_ip = a.far.split("/")[0]

    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18081"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-N", "-L",
                                   f"18081:{a.dut_ip}:80", a.pi])
        time.sleep(2)
    dut = pcbench.Dut(hostport, own_tags=OWN_TAGS, tasks=("vpn_state",))

    found = {}
    exit_code = 1
    receiver_up = False
    alias_up = False
    try:
        # ---- r0: as found -----------------------------------------------------
        info = dut.get("/api/info")
        st0 = dut.get("/api/status")
        dut.as_found()
        wst = dut.get("/api/wifi/status")
        print(f"DUT {info.get('device_id')} {info.get('fw_version')} station "
              f"{'on ' + str(wst.get('ip')) if wst.get('sta_connected') else 'off'}",
              flush=True)
        if not wst.get("sta_connected"):
            raise pcbench.Bench("the station is not connected: the bench "
                                "needs the DUT on the bench access point")
        sta_ip = str(wst.get("ip"))
        pi_ip = a.pi_ip or re.sub(r"\.\d+$", ".1", sta_ip)
        for name in ("vpn_manager", "data_destinations"):
            found[name] = dut.settings(name)

        # the server, the alias behind it, the receiver
        rc, srv_pub = ssh(a.pi, "sudo -n cat /etc/wireguard/wg-bench-server.pub")
        srv_pub = srv_pub.strip()
        if rc != 0 or len(srv_pub) != 44:
            raise pcbench.Bench(f"wg-bench server key on {a.pi}: {srv_pub!r}")
        code, kg = dut.api("/api/vpn/keygen", "POST", {})
        if code != 200 or not isinstance(kg, dict):
            raise pcbench.Bench(f"keygen: {code} {kg}")
        dut_pub = kg["public_key"]
        rc, out = ssh(a.pi, f"wg-bench peer {dut_pub} && wg-bench start")
        run.check("r0_wg_bench_up", rc == 0, out.strip().splitlines()[-1:])
        rc, out = ssh(a.pi, f"sudo -n ip addr add {a.alias} dev wg-bench 2>&1 || true; "
                            f"sudo -n ip addr add {a.far} dev wg-bench 2>&1 || true; "
                            f"ip -4 addr show dev wg-bench | grep -c -e '{alias_ip}' -e '{far_ip}'")
        alias_up = out.strip().endswith("2")
        run.check("r0_alias_and_far_behind_the_tunnel", alias_up, out.strip()[-60:])
        rc, out = ssh(a.pi, f"sudo -n systemctl stop {RECV_UNIT} 2>/dev/null; "
                            f"sudo -n systemd-run --unit={RECV_UNIT} --collect -p User=meatpi "
                            f"python3 /home/meatpi/wican/tools/testbench/actors/dd_receiver.py "
                            f"--mqtt none 2>&1 | tail -1; sleep 2; "
                            f"curl -s -m 5 -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{RECV_PORT}/__dump?since=0")
        receiver_up = out.strip().endswith("200")
        run.check("r0_receiver_up", receiver_up, out.strip()[-80:])
        if not (alias_up and receiver_up):
            raise pcbench.Bench("the Pi side is not ready (alias / receiver)")

        vpn_cfg = {
            "enabled": True, "type": "wireguard", "peer_public_key": srv_pub,
            "preshared_key": "", "address": DUT_TUN,
            "routes": f"{TUN_ROUTE}, {alias_net}",
            "endpoint": pi_ip, "port": WG_PORT, "keepalive_s": 5,
            "default_route": False, "dns": "", "private_key": "",
        }
        dd_cfg = {"enabled": True, "destinations": [
            dest_row("vpnroute", f"http://{alias_ip}:{RECV_PORT}/route"),
            dest_row("vpnplain", f"http://{pi_ip}:{RECV_PORT}/plain"),
            dest_row("vpnfar", f"http://{far_ip}:{RECV_PORT}/far"),
        ]}

        def vpn_state(timeout, want):
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

        def test_dest(name):
            """One delivery now; (http status the DUT saw, the receiver's record)."""
            seen = len(receiver_records(a.pi))
            code, r = dut.api("/api/destinations/test", "POST", {"name": name},
                              timeout=30)
            rec = None
            for _ in range(10):
                recs = receiver_records(a.pi, seen)
                recs = [x for x in recs if x.get("path", "").endswith("/" + name.replace("vpn", ""))]
                if recs:
                    rec = recs[-1]
                    break
                time.sleep(1)
            return code, r, rec

        # ---- r1: up with two routes ---------------------------------------------
        if "r1" in legs or "r2" in legs or "r3" in legs or "r5" in legs:
            dut.restart({"vpn_manager": vpn_cfg, "data_destinations": dd_cfg})
            v, took = vpn_state(90, "connected")
            if "r1" in legs:
                run.check("r1_connected", bool(v) and v.get("state") == "connected",
                          f"{v} after {took:.0f} s")
                routes = (v or {}).get("routes") or []
                run.check("r1_routes_listed",
                          TUN_ROUTE in routes and alias_net in routes, routes)
                ring = "\n".join(dut.ring_lines())
                run.check("r1_routes_in_the_log",
                          f"ip4_routes: {alias_net} via" in ring
                          and f"ip4_routes: {TUN_ROUTE} via" in ring)
                run.metric("r1_connect_after_restart_s", round(took, 1))
            elif not v or v.get("state") != "connected":
                raise pcbench.Bench(f"the tunnel did not come up: {v}")

        # ---- r2: the routed request comes through the tunnel ---------------------
        if "r2" in legs:
            code, r, rec = test_dest("vpnroute")
            run.check("r2_test_answers", code == 200 and isinstance(r, dict)
                      and r.get("ok") is True, f"{code} {r}")
            run.check("r2_received_from_the_tunnel",
                      rec is not None and rec.get("peer") == DUT_TUN,
                      rec and {"peer": rec.get("peer"), "path": rec.get("path")})

        # ---- r3: the plain request leaves over the station -----------------------
        if "r3" in legs:
            code, r, rec = test_dest("vpnplain")
            run.check("r3_test_answers", code == 200 and isinstance(r, dict)
                      and r.get("ok") is True, f"{code} {r}")
            run.check("r3_received_from_the_station",
                      rec is not None and rec.get("peer") == sta_ip,
                      rec and {"peer": rec.get("peer"), "path": rec.get("path")})
            code, r, rec = test_dest("vpnfar")
            run.check("r3_far_test_answers", code == 200 and isinstance(r, dict)
                      and r.get("ok") is True, f"{code} {r}")
            run.check("r3_far_leaves_over_the_station_without_default_route",
                      rec is not None and rec.get("peer") == sta_ip,
                      rec and {"peer": rec.get("peer"), "path": rec.get("path")})

        # ---- r4: the validator ----------------------------------------------------
        if "r4" in legs:
            cur = dut.settings("vpn_manager")
            bad = dict(cur)
            bad["routes"] = "10.66.0.0/24, nonsense"
            code, r = dut.api("/api/settings/vpn_manager", "PUT", bad)
            run.check("r4_bad_token_refused", code == 400 and "nonsense" in json.dumps(r),
                      f"{code} {r}")
            bad["routes"] = " ".join(f"10.{i}.0.0/16" for i in range(1, 10))
            code, r = dut.api("/api/settings/vpn_manager", "PUT", bad)
            run.check("r4_too_many_refused", code == 400 and "more than 8" in json.dumps(r),
                      f"{code} {r}")
            run.check("r4_nothing_staged",
                      dut.settings("vpn_manager").get("routes") == cur.get("routes"))

        # ---- r5: default_route sends everything through the tunnel ----------------
        if "r5" in legs:
            dut.restart({"vpn_manager": {"default_route": True}})
            v, took = vpn_state(90, "connected")
            run.check("r5_connected_with_default_route",
                      bool(v) and v.get("state") == "connected", f"{v} after {took:.0f} s")
            code, r, rec = test_dest("vpnfar")
            run.check("r5_far_now_from_the_tunnel",
                      rec is not None and rec.get("peer") == DUT_TUN,
                      rec and {"peer": rec.get("peer"), "path": rec.get("path")})
            code, r, rec = test_dest("vpnplain")
            run.check("r5_lan_still_from_the_station",
                      rec is not None and rec.get("peer") == sta_ip,
                      rec and {"peer": rec.get("peer"), "path": rec.get("path")})

        # ---- r6: as found ------------------------------------------------------------
        back = {}
        for name in ("vpn_manager", "data_destinations"):
            back[name] = dict(found[name])
            for k in ("private_key", "preshared_key", "ts_auth_key"):
                if k in back[name]:
                    back[name][k] = ""   # blank keeps the stored one
        dut.restart(back)
        v = dut.get("/api/vpn")
        run.check("r6_vpn_as_found", (v.get("state") == "disabled") == (not found["vpn_manager"].get("enabled")),
                  v.get("state"))
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        exit_code = run.verdict("legs: " + " ".join(legs) if only else "")
    except pcbench.Bench as e:
        print("BENCH:", e, flush=True)
        run.fails.append(f"bench: {e}")
        run.verdict()
        exit_code = 2
    finally:
        if receiver_up:
            ssh(a.pi, f"sudo -n systemctl stop {RECV_UNIT} 2>/dev/null; true")
        if alias_up:
            ssh(a.pi, f"sudo -n ip addr del {a.alias} dev wg-bench 2>/dev/null; "
                      f"sudo -n ip addr del {a.far} dev wg-bench 2>/dev/null; true")
        ssh(a.pi, "wg-bench stop >/dev/null 2>&1; true")
        if tunnel is not None:
            tunnel.terminate()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
