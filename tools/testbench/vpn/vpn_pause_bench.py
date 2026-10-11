#!/usr/bin/env python3
"""Pause the VPN on marked networks (issue #976, 2026-10-11): the bench.

The claim: while the station is connected to a network marked "Pause VPN"
(wifi_manager `sta_vpn_pause` / `fallbackN_vpn_pause`) the tunnel is down on
purpose and no handshake reaches the server; the moment the station leaves
that network the tunnel comes back, and it goes down again within seconds of
the station landing on a marked network.

Rig (TESTING.md): the DUT's primary network is the bench access point
(WICAN_TEST_AP: the P4 bench_ap instrument bridged to the Pi's usb0, 10.42.1.1,
whose daemon gives the `ap off` / `ap on` levers; or the Pi's wint0 hotspot
`wican-bench-w0`, 10.42.0.1, taken down and up with nmcli; `--lever`), the
ESPNetLink dongle's AP sits in a backup slot (the station falls back to it
when the bench AP is gone), and rpi001 runs the LOCAL WireGuard server
`wg-bench` (udp 51821, 10.66.0.1, `tools/testbench/actors/wg_bench_server.sh`).
The firmware does not care which network is "home", so the legs mark whichever
network they need. The DUT is driven over HTTP through an ssh tunnel while it
is on the bench AP, and watched over its console (console_tail.py, DTR/RTS
off: no reset) while it is away on the dongle's AP.

Legs:
  p1  the primary marked: after the restart `state paused`, `network` = the
      bench AP, `paused` true, the `vpn_enabled` bit clear, the console line
      `paused on <ssid> (marked Pause VPN)`, no handshake at the server in 30 s
  p2  the primary unmarked (the dongle's slot marked instead): connected
      within 90 s, a handshake at the server, a ping through the tunnel
  p3  the hotspot down: the station joins the dongle's AP and the VPN pauses
      there (console), the server sees no newer handshake while away
  p4  the hotspot up: the station roams home (`sta_roam_interval_s` 60 for the
      run), the tunnel comes back, `connects` grew, a fresh handshake, a ping
  p5  restore: wifi_manager + vpn_manager as found, `wg-bench` down, planned
      restarts only, no E line of the component tags

  python tools/testbench/vpn/vpn_pause_bench.py [--dut-ip 10.42.0.194]
      [--dut localhost:18081] [--pi rpi001] [--lever auto|nmcli|p4]
      [--hotspot wican-bench-w0] [--pi-ip 10.42.0.1]
      [--console auto|COMx|none] [--console-log FILE] [--only p1,p2]
      [--logdir DIR]

Prints `VPN PAUSE PASS` (or `VPN PAUSE PARTIAL PASS (legs: ...)` with --only).
Written 2026-10-11 against the mock-verified firmware; the rig was down that
day (the DUT off the hotspot), so the first run on hardware is still owed.
"""
import argparse
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
TUN_NET = "10.66.0.0"
WG_PORT = 51821
OWN_TAGS = ("vpn_manager", "wifi_manager")
LEGS = ("p1", "p2", "p3", "p4")


def ssh(pi, cmd, timeout=60):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o",
                        "ConnectTimeout=10", pi, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def wg_handshake_epoch(pi, pub):
    """The server's latest handshake with our peer (epoch seconds, 0 = none)."""
    rc, out = ssh(pi, "sudo -n wg show wg-bench latest-handshakes")
    key = pub.split("=")[0]
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[0].startswith(key):
            try:
                return int(parts[1])
            except ValueError:
                return 0
    return 0


def pi_ping_tunnel(pi):
    rc, out = ssh(pi, f"ping -c 3 -W 2 -I {SRV_TUN} {DUT_TUN}", timeout=30)
    return rc == 0 and " 0% packet loss" in out, out.strip().splitlines()[-1:]


class Console:
    """The DUT's console beside the run (console_tail.py, no reset)."""

    def __init__(self, port, log):
        self.port, self.log, self.proc = port, log, None
        self.stop_file = log + ".stop"

    def start(self):
        if not self.port or self.port == "none":
            return False  # a log somebody else writes, or no console at all
        here = os.path.dirname(os.path.abspath(__file__))
        tail = os.path.join(here, "..", "lib", "console_tail.py")
        if os.path.exists(self.stop_file):
            os.remove(self.stop_file)
        self.proc = subprocess.Popen(
            [sys.executable, tail, self.port, self.log, "--secs", "3600",
             "--stop-file", self.stop_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        return self.proc.poll() is None

    def lines(self):
        try:
            with open(self.log, encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()
        except OSError:
            return []

    def wait_for(self, pattern, since, timeout):
        """The first NEW line matching `pattern` after index `since`."""
        rx = re.compile(pattern)
        t0 = time.time()
        while time.time() - t0 < timeout:
            ls = self.lines()
            for ln in ls[since:]:
                if rx.search(ln):
                    return ln, len(ls)
            time.sleep(2)
        return None, len(self.lines())

    def mark(self):
        return len(self.lines())

    def stop(self):
        if self.proc is None:
            return
        try:
            open(self.stop_file, "w").close()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="", help="host:port of the DUT's API "
                    "(a tunnel); default: open one to --dut-ip through --pi")
    ap.add_argument("--dut-ip", default="10.42.0.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--hotspot", default="wican-bench-w0",
                    help="the NetworkManager profile of the bench AP on the Pi "
                    "(the nmcli lever)")
    ap.add_argument("--lever", default="auto", choices=("auto", "nmcli", "p4"),
                    help="how the bench AP goes away and comes back: nmcli = the "
                    "profile down/up; p4 = the bench_ap instrument's ap off/on "
                    "through its daemon; auto = p4 when wican-bench-usb is the "
                    "active profile on the Pi")
    ap.add_argument("--pi-ip", default="",
                    help="the Pi's address on the DUT's network (the WireGuard "
                    "endpoint); default: the DUT's address with .1")
    ap.add_argument("--console", default="auto",
                    help="the DUT console port (auto = the bench detector), "
                    "none = no console (p3 then only judges the server)")
    ap.add_argument("--console-log", default="",
                    help="read this console log instead of opening the port "
                    "(test.ps1 holds the console beside the stage)")
    ap.add_argument("--only", default="", help="comma list of legs")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS if not only or l in only]
    logdir = a.logdir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "test-reports", "logs", time.strftime("vpn_pause_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("VPN PAUSE")

    lever = a.lever
    if lever == "auto":
        rc, out = ssh(a.pi, "nmcli -t -f NAME,DEVICE con show --active")
        lever = "p4" if "wican-bench-usb:" in out else "nmcli"
    print(f"bench AP lever: {lever}", flush=True)

    def ap_away():
        """The bench AP gone (the station must fall back to the dongle)."""
        if lever == "p4":
            return ssh(a.pi, "cd ~/wican/tools/testbench/pi && timeout 15 python3 bench_ap_cli.py 'ap off'")
        return ssh(a.pi, f"sudo -n nmcli con down {a.hotspot}")

    def ap_home():
        if lever == "p4":
            return ssh(a.pi, "cd ~/wican/tools/testbench/pi && timeout 15 python3 bench_ap_cli.py 'ap on'")
        return ssh(a.pi, f"sudo -n nmcli con up {a.hotspot}")

    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18081"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-N", "-L",
                                   f"18081:{a.dut_ip}:80", a.pi])
        time.sleep(2)
    dut = pcbench.Dut(hostport, own_tags=OWN_TAGS, tasks=("vpn_state",))

    port = "none" if a.console_log else a.console
    if port == "auto":
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(
                os.path.abspath(__file__)), "..", "lib"))
            import bench_ports  # noqa: E402
            port = bench_ports.resolve("auto", "wican_console", "none")
        except Exception as e:  # noqa: BLE001
            print("console: detector failed:", e)
            port = "none"
    console = Console(port, a.console_log or os.path.join(logdir, "console.log"))
    have_console = bool(a.console_log) or console.start()
    print("console: " + ("reading " + a.console_log if a.console_log
                         else "on " + port if have_console else "off"), flush=True)

    found = {}
    exit_code = 1
    try:
        # ---- p0: as found -----------------------------------------------------
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
        found["wifi_manager"] = dut.settings("wifi_manager")
        found["vpn_manager"] = dut.settings("vpn_manager")
        wifi = found["wifi_manager"]
        primary = str(wifi.get("sta_ssid") or "")
        dongle_slot = None
        code, nl = dut.api("/api/espnetlink")
        paired = str((nl or {}).get("ssid") or "") if code == 200 and isinstance(nl, dict) else ""
        for i in range(1, 6):
            name = str(wifi.get(f"fallback{i}_ssid") or "")
            if paired and name == paired:
                dongle_slot = i            # the dongle the station will fall back to
                break
            if name.startswith("ESPNetLink_"):
                dongle_slot = i            # else the last ESPNetLink_ slot (a stale one may sit before it)
        print(f"dongle: paired {paired or '(none)'}, slot {dongle_slot}", flush=True)
        run.check("p0_primary_is_the_bench_ap", bool(primary), primary)
        if dongle_slot is None:
            print("no ESPNetLink backup slot: p3/p4 skipped", flush=True)
            legs = [l for l in legs if l not in ("p3", "p4")]
        pi_ip = a.pi_ip or re.sub(r"\.\d+$", ".1", str(wst.get("ip")))

        # the WireGuard server on the Pi, the DUT's key registered there
        rc, srv_pub = ssh(a.pi, "sudo -n cat /etc/wireguard/wg-bench-server.pub")
        srv_pub = srv_pub.strip()
        if rc != 0 or len(srv_pub) != 44:
            raise pcbench.Bench(f"wg-bench server key on {a.pi}: {srv_pub!r}")
        code, kg = dut.api("/api/vpn/keygen", "POST", {})
        if code != 200 or not isinstance(kg, dict):
            raise pcbench.Bench(f"keygen: {code} {kg}")
        dut_pub = kg["public_key"]
        run.check("p0_keygen_public_key_only", len(dut_pub) == 44
                  and dut.settings("vpn_manager").get("private_key", "") == "")
        rc, out = ssh(a.pi, f"wg-bench peer {dut_pub} && wg-bench start")
        run.check("p0_wg_bench_up", rc == 0, out.strip().splitlines()[-1:])

        vpn_cfg = {
            "enabled": True, "type": "wireguard", "peer_public_key": srv_pub,
            "preshared_key": "", "address": DUT_TUN, "routes": TUN_NET + "/24",
            "endpoint": pi_ip,
            "port": WG_PORT, "keepalive_s": 5, "default_route": False,
            "dns": "", "private_key": "",
        }

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

        # ---- p1: the primary marked -> paused on the bench AP --------------------
        if "p1" in legs:
            m = console.mark()
            dut.restart({"vpn_manager": vpn_cfg,
                         "wifi_manager": {"sta_vpn_pause": True,
                                          "sta_roam_interval_s": 60}})
            v, took = vpn_state(60, "paused")
            run.check("p1_state_paused", bool(v) and v.get("state") == "paused",
                      f"{v} after {took:.0f} s")
            run.check("p1_network_named", bool(v) and v.get("network") == primary
                      and v.get("paused") is True, v and v.get("network"))
            st = dut.get("/api/status")
            run.check("p1_vpn_bit_clear",
                      not (st.get("bits") or {}).get("vpn_enabled"))
            ring = "\n".join(dut.ring_lines())
            run.check("p1_log_line",
                      f"paused on {primary} (marked Pause VPN)" in ring)
            if have_console:
                ln, _ = console.wait_for(r"vpn_manager: paused on .*marked Pause VPN", m, 5)
                run.check("p1_console_line", ln is not None, ln)
            t_mark = time.time()
            time.sleep(30)
            hs = wg_handshake_epoch(a.pi, dut_pub)
            run.check("p1_no_handshake_30s", hs == 0, hs)
            run.metric("p1_pause_after_restart_s", round(took, 1))

        # ---- p2: unmarked -> connected -------------------------------------------
        if "p2" in legs:
            changes = {"wifi_manager": {"sta_vpn_pause": False}}
            if dongle_slot is not None:
                changes["wifi_manager"][f"fallback{dongle_slot}_vpn_pause"] = True
            if "p1" not in legs:
                changes["vpn_manager"] = vpn_cfg
                changes["wifi_manager"]["sta_roam_interval_s"] = 60
            hs0 = wg_handshake_epoch(a.pi, dut_pub)
            dut.restart(changes)
            v, took = vpn_state(90, "connected")
            run.check("p2_connected", bool(v) and v.get("state") == "connected",
                      f"{v} after {took:.0f} s")
            run.check("p2_network_is_the_bench_ap", bool(v) and v.get("network") == primary)
            hs = wg_handshake_epoch(a.pi, dut_pub)
            run.check("p2_server_handshake", hs > hs0, f"{hs0} -> {hs}")
            ok, tail = pi_ping_tunnel(a.pi)
            run.check("p2_ping_through_tunnel", ok, tail)
            run.metric("p2_connect_after_restart_s", round(took, 1))
            found["connects_p2"] = (v or {}).get("connects", 0)

        # ---- p3: away on the dongle's AP (marked) -> paused -----------------------
        if "p3" in legs:
            m = console.mark()
            t_down = time.time()
            rc, out = ap_away()
            run.check("p3_hotspot_down", rc == 0, out.strip()[-80:])
            if have_console:
                ln, m2 = console.wait_for(r"STA got IP 192\.168\.80\.", m, 180)
                run.check("p3_station_joined_the_dongle_ap", ln is not None, ln)
                ln2, _ = console.wait_for(r"vpn_manager: paused on ESPNetLink_", m, 120)
                run.check("p3_paused_on_the_dongle_ap", ln2 is not None, ln2)
            else:
                print("p3: no console, waiting 150 s for the fallback", flush=True)
                time.sleep(150)
            hs_away = wg_handshake_epoch(a.pi, dut_pub)
            time.sleep(45)
            hs = wg_handshake_epoch(a.pi, dut_pub)
            run.check("p3_no_handshake_while_away", hs == hs_away,
                      f"last handshake {hs_away} -> {hs} over 45 s away")
            found["hs_away"] = hs

        # ---- p4: home again -> connected -----------------------------------------
        if "p4" in legs:
            m = console.mark()
            t_up = time.time()
            rc, out = ap_home()
            run.check("p4_hotspot_up", rc == 0, out.strip()[-80:])
            if have_console:
                ln, _ = console.wait_for(r"STA got IP 10\.42\.", m, 240)
                run.check("p4_station_back_home", ln is not None, ln)
            v, took = vpn_state(300, "connected")
            run.check("p4_connected_again", bool(v) and v.get("state") == "connected",
                      f"{v} after {took:.0f} s")
            run.check("p4_connects_grew",
                      bool(v) and v.get("connects", 0) > found.get("connects_p2", 0),
                      v and v.get("connects"))
            hs = wg_handshake_epoch(a.pi, dut_pub)
            run.check("p4_fresh_handshake", hs > found.get("hs_away", 0),
                      f"{found.get('hs_away', 0)} -> {hs}")
            ok, tail = pi_ping_tunnel(a.pi)
            run.check("p4_ping_through_tunnel", ok, tail)
            run.metric("p4_home_to_connected_s", round(time.time() - t_up, 1))

        # ---- p5: as found ------------------------------------------------------------
        back = {}
        for name in ("wifi_manager", "vpn_manager"):
            back[name] = {k: v for k, v in found[name].items()
                          if k not in ("sta_password", "ap_password",
                                       "private_key", "preshared_key",
                                       "ts_auth_key")
                          or v != ""}
            # blank secrets on PUT keep the stored ones (the api_http rule)
            for k in ("sta_password", "ap_password", "private_key",
                      "preshared_key", "ts_auth_key"):
                if k in back[name]:
                    back[name][k] = ""
            for i in range(1, 6):
                k = f"fallback{i}_password"
                if k in back[name]:
                    back[name][k] = ""
        dut.restart(back)
        rc, out = ssh(a.pi, "wg-bench stop")
        run.check("p5_wg_bench_down", rc == 0, out.strip()[-60:])
        v = dut.get("/api/vpn")
        run.check("p5_vpn_as_found", v.get("state") == (
            "disabled" if not found["vpn_manager"].get("enabled") else v.get("state")),
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
        console.stop()
        if tunnel is not None:
            tunnel.terminate()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
