#!/usr/bin/env python3
"""WireGuard torn down while connections run through it: no crash (the beta.5 reports).

The claim: a WireGuard tunnel that carries live TCP connections (MQTT to a broker
behind the tunnel) can be torn down and brought back any number of times without a
crash. Two beta.5 crash reports of 2026-11-01 (task `vpn_state`, a double free in
`tlsf_free` and an InstructionFetchError in `tcp_abandon`) were this teardown:
`esp_wireguard_disconnect()` cleared the tunnel address from the `vpn_state` task,
and lwIP aborted and freed every TCP connection on that address off its own thread
(`internal/TASK_wg_teardown_race.md`). Since the fix every lwIP core call of the
tunnel runs inside the tcpip thread; on a build with `CONFIG_LWIP_CHECK_THREAD_SAFETY=y`
lwIP asserts the rule at the first off-thread call, so this bench is also the
diagnostic build's witness.

Rig (TESTING.md): the DUT on the bench AP (the Pi's hotspot = the WireGuard
endpoint), rpi001 running the LOCAL WireGuard server `wg-bench` (10.66.0.1, udp 51821)
and its mosquitto on 1883. The bench points the DUT's MQTT at the broker's TUNNEL
address, so the broker connection is a TCP connection on the tunnel address: the
one the teardown aborts.

Legs:
  c0  as found: the station on the bench AP, wg-bench up with the DUT's key
  c1  the VPN up (routes = the tunnel network) and MQTT connected through it
  c2  N teardown cycles. The trigger (--trigger):
        wifi  (default) a station trial to a network that does not exist
              (`POST /api/wifi/try`): the station leaves the bench AP for a few
              seconds, `network_up()` goes false and the state task tears the
              tunnel down at once, the uplink-lost path of the field; the station
              re-joins and the tunnel reconnects (about 30 s per cycle)
        peer  the DUT's peer removed from wg-bench on the Pi: the keys expire,
              "peer silent for 30 s; reconnecting" (the field's other path, 2 to
              4 min per cycle); the peer is put back when the teardown shows.
              With --hold-s N (2026-11-09) the tunnel first stays up for N
              seconds, so WireGuard has rekeyed (every 120 s) before the peer
              goes: until that day the key the rekey replaced never expired,
              the peer read as up for ever and the teardown never came for a
              tunnel older than two minutes (N = 150 is the witness)
      Each cycle: `connects` on /api/vpn grows by one, MQTT is back through the
      tunnel, the DUT did not reboot (boot_count), no crash note.
  c3  as found: vpn_manager and mqtt_manager back (one restart), wg-bench down,
      no unexpected reset, no E line of the own tags, the state task's headroom

  python tools/testbench/vpn/wg_teardown_cycle_bench.py [--dut-ip 10.42.1.194]
      [--dut localhost:18083] [--pi rpi001] [--pi-ip 10.42.1.1] [--cycles 10]
      [--trigger wifi|peer] [--hold-s N] [--logdir DIR]

Prints `WG TEARDOWN CYCLE PASS`. Written 2026-11-02.
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
OWN_TAGS = ("vpn_manager", "ip4_routes", "esp_wireguard", "mqtt_manager")
TRIAL_SSID = "wican-bench-nowhere"
TRIAL_PASSWORD = "nothing-to-join-1"


def ssh(pi, cmd, timeout=60):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o",
                        "ConnectTimeout=10", pi, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="", help="host:port of the DUT's API "
                    "(a tunnel); default: open one to --dut-ip through --pi")
    ap.add_argument("--dut-ip", default="10.42.1.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--pi-ip", default="",
                    help="the Pi's address on the DUT's network (the WireGuard "
                    "endpoint); default: the DUT's address with .1")
    ap.add_argument("--cycles", type=int, default=10)
    ap.add_argument("--trigger", choices=("wifi", "peer"), default="wifi")
    ap.add_argument("--hold-s", type=int, default=0,
                    help="peer trigger: seconds the tunnel stays up before the "
                    "peer is removed (150 = past the first rekey)")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    logdir = a.logdir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "test-reports", "logs", time.strftime("wg_cycle_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("WG TEARDOWN CYCLE")

    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18083"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-N", "-L",
                                   f"18083:{a.dut_ip}:80", a.pi])
        time.sleep(2)
    dut = pcbench.Dut(hostport, own_tags=OWN_TAGS, tasks=("vpn_state",))

    found = {}
    exit_code = 1
    dut_pub = ""
    try:
        # ---- c0: as found ----------------------------------------------------------
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
        for name in ("vpn_manager", "mqtt_manager"):
            found[name] = dut.settings(name)

        rc, srv_pub = ssh(a.pi, "sudo -n cat /etc/wireguard/wg-bench-server.pub")
        srv_pub = srv_pub.strip()
        if rc != 0 or len(srv_pub) != 44:
            raise pcbench.Bench(f"wg-bench server key on {a.pi}: {srv_pub!r}")
        code, kg = dut.api("/api/vpn/keygen", "POST", {})
        if code != 200 or not isinstance(kg, dict):
            raise pcbench.Bench(f"keygen: {code} {kg}")
        dut_pub = kg["public_key"]
        rc, out = ssh(a.pi, f"wg-bench peer {dut_pub} && wg-bench start")
        run.check("c0_wg_bench_up", rc == 0, out.strip().splitlines()[-1:])
        rc, out = ssh(a.pi, "ss -ltn | grep -c ':1883 '")
        run.check("c0_broker_listening", out.strip().endswith("1"), out.strip()[-40:])

        # ---- c1: the tunnel up, MQTT through it ------------------------------------
        vpn_cfg = {
            "enabled": True, "type": "wireguard", "peer_public_key": srv_pub,
            "preshared_key": "", "address": DUT_TUN, "routes": TUN_ROUTE,
            "endpoint": pi_ip, "port": WG_PORT, "keepalive_s": 5,
            "default_route": False, "dns": "", "private_key": "",
        }
        mqtt_cfg = {"enabled": True, "url": f"mqtt://{SRV_TUN}:1883",
                    "connect_on": "wifi"}

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

        def mqtt_up(timeout):
            t0 = time.time()
            while time.time() - t0 < timeout:
                code, st = dut.api("/api/status", timeout=5)
                if code == 200 and isinstance(st, dict) \
                        and st.get("bits", {}).get("mqtt_connected"):
                    return True, time.time() - t0
                time.sleep(2)
            return False, time.time() - t0

        dut.restart({"vpn_manager": vpn_cfg, "mqtt_manager": mqtt_cfg})
        v, took = vpn_state(90, "connected")
        run.check("c1_connected", bool(v) and v.get("state") == "connected",
                  f"{v} after {took:.0f} s")
        ok, took = mqtt_up(90)
        run.check("c1_mqtt_through_the_tunnel", ok, f"after {took:.0f} s")
        if not (v and v.get("state") == "connected" and ok):
            raise pcbench.Bench("the tunnel or the broker connection did not come up")

        # ---- c2: the cycles ----------------------------------------------------------
        boots0 = dut.get("/api/status").get("boot_count")
        connects = int(v.get("connects") or 0)
        done = 0
        worst_s = 0.0
        for i in range(1, a.cycles + 1):
            t0 = time.time()
            if a.trigger == "wifi":
                code, r = dut.api("/api/wifi/try", "POST",
                                  {"ssid": TRIAL_SSID, "password": TRIAL_PASSWORD,
                                   "channel": 1}, timeout=10)
                if code != 202:
                    run.check(f"c2_{i}_trigger", False, f"{code} {r}")
                    break
            else:
                if a.hold_s > 0:
                    # the tunnel through its rekey first: the state the field
                    # is in whenever a server goes away
                    print(f"cycle {i}: holding the tunnel {a.hold_s} s", flush=True)
                    time.sleep(a.hold_s)
                    code, vv = dut.api("/api/vpn", timeout=5)
                    held = code == 200 and isinstance(vv, dict) \
                        and vv.get("state") == "connected" \
                        and int(vv.get("connects") or 0) == connects
                    run.check(f"c2_{i}_held_through_rekey", held,
                              f"{(vv or {}).get('state') if isinstance(vv, dict) else code} "
                              f"connects {(vv or {}).get('connects') if isinstance(vv, dict) else '?'}")
                    t0 = time.time()
                rc, out = ssh(a.pi, f"sudo -n wg set wg-bench peer {dut_pub} remove")
                if rc != 0:
                    run.check(f"c2_{i}_trigger", False, out.strip()[-80:])
                    break
            # the teardown: the tunnel leaves `connected` (the station is away for a
            # moment under the wifi trigger: unanswered polls are expected then)
            left = False
            deadline = time.time() + (60 if a.trigger == "wifi" else 300)
            while time.time() < deadline:
                code, vv = dut.api("/api/vpn", timeout=5)
                if code == 200 and isinstance(vv, dict) and vv.get("state") != "connected":
                    left = True
                    break
                if code == 200 and isinstance(vv, dict) \
                        and int(vv.get("connects") or 0) > connects:
                    left = True      # it went down and came back between two polls
                    break
                time.sleep(1)
            if a.trigger == "peer":
                ssh(a.pi, f"sudo -n wg set wg-bench peer {dut_pub} allowed-ips {DUT_TUN}/32")
            vv, took = vpn_state(120, "connected")
            back = bool(vv) and vv.get("state") == "connected" \
                and int(vv.get("connects") or 0) > connects
            ok, took_m = mqtt_up(120)
            st = dut.get("/api/status")
            same_boot = st.get("boot_count") == boots0
            cycle_s = time.time() - t0
            worst_s = max(worst_s, cycle_s)
            run.check(f"c2_{i}_down_and_back", left and back and ok and same_boot,
                      f"left={left} connects {connects}->{(vv or {}).get('connects')} "
                      f"mqtt={ok} boot_count {boots0}->{st.get('boot_count')} "
                      f"{cycle_s:.0f} s")
            if not same_boot:
                code, h = dut.api("/api/restart/history", timeout=15)
                recs = h.get("records", []) if code == 200 and isinstance(h, dict) else []
                crash = next((r.get("crash") for r in recs if r.get("crash")), None)
                run.check(f"c2_{i}_no_crash", False,
                          (crash or {}).get("summary", "rebooted, no crash note"))
                break
            connects = int((vv or {}).get("connects") or connects)
            done += 1
        run.metric("cycles_done", done)
        run.metric("worst_cycle_s", round(worst_s))
        run.check("c2_all_cycles", done == a.cycles, f"{done} of {a.cycles}")

        # ---- c3: as found ---------------------------------------------------------------
        back = {}
        for name in ("vpn_manager", "mqtt_manager"):
            back[name] = dict(found[name])
            for k in ("private_key", "preshared_key", "ts_auth_key", "broker_password"):
                if k in back[name]:
                    back[name][k] = ""   # blank keeps the stored one
        dut.restart(back)
        v = dut.get("/api/vpn")
        run.check("c3_vpn_as_found",
                  (v.get("state") == "disabled") == (not found["vpn_manager"].get("enabled")),
                  v.get("state"))
        ok, took = mqtt_up(90)
        run.check("c3_mqtt_as_found", ok == bool(found["mqtt_manager"].get("enabled")),
                  f"mqtt_connected={ok} after {took:.0f} s")
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        exit_code = run.verdict()
    except pcbench.Bench as e:
        print("BENCH:", e, flush=True)
        run.fails.append(f"bench: {e}")
        run.verdict()
        exit_code = 2
    finally:
        ssh(a.pi, "wg-bench stop >/dev/null 2>&1; true")
        with open(os.path.join(logdir, "ring.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(dut.ring_lines()))
        if tunnel is not None:
            tunnel.terminate()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
