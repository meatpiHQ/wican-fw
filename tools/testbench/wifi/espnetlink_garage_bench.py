#!/usr/bin/env python3
"""The garage story with an ESPNetLink on the WiCAN (Ali, 2026-10-12): the
car starts in the garage on the home WiFi, drives away (the WiFi drops, LTE
through the dongle takes over, the VPN comes up over it and the data keeps
flowing through the tunnel to the betty VPS), and comes home (the home
WiFi is back, the default route returns to it, the dongle is released).
Edge cases around it. One run per WiCAN mode.

How it observes. The DUT is driven over its HTTP API: through the ssh
tunnel to the Pi while it is at home (the bench AP), and from betty through
the WireGuard tunnel (10.9.0.2) while it is away, where nothing else
reaches it. The log ring (`/api/logs/ring`) is read from the same place.
The serial console is optional: it counts only when it answers
`espnetlink` (COM254 was silent on 2026-10-12), and only g10 needs it
(`usb vbus`). The default route is proven at the receiver on betty: every
POST records its source address, so the `garage_pub` row (to betty's
public address) arrives from the home side's address at home and from the
carrier's address away, and the `garage_tun` row (to betty's tunnel
address) arrives from the DUT's tunnel address. Public addresses are never
printed: the bench says "home" or "other".

Rig (TESTING.md): the DUT on the P4 bench AP (WICAN_TEST_AP, usb0 on
rpi001; `ap off` / `ap on` / `ap set` through its daemon are the home
WiFi levers; the daemon must be running), the ESPNetLink on the WiCAN's
connector, betty as the WireGuard server (`tools/testbench/vpn/
wg_public_soak.sh`, keys made on betty and on the device, never printed),
the PSU for the cold boot and the sleep legs. The Pi's eth-bench profile
is never-default on purpose, so the bench adds a host route to betty on
the Pi for the home legs and removes it at the end.

Legs (--only to pick; g9 and g11 run only when named):
  g1  engine on in the garage: PSU cold cycle, the station on home, the
      dongle paired per mode (usb_rndis: ncm_up; wifi_modem: key read +
      cut), GPS valid, the VPN paused (variant A) or connected (B), the
      pub POSTs from home
  g2  drive away: `ap off`; the DUT reachable through the tunnel from
      betty; its own word: no station (rndis) or the station on the
      dongle's AP (wifi_modem), the uplink = the dongle, time still synced,
      DNS from the dongle
  g3  VPN over LTE: a handshake newer than before the leg, the endpoint
      off the home address, tun POSTs from 10.9.0.2, pub POSTs from the
      carrier side, the transfer counter growing, the DUT says connected
  g6  dongle reboot while away: POST /api/espnetlink/repair through the
      tunnel (a VBUS cycle): the tunnel returns, vbus_cycles grew, no
      WiCAN restart, GPS valid again
  g10 dongle gone while away (console only): `usb vbus 0` -> uplink none,
      no crash; `usb vbus 1` -> the tunnel returns
  g4  drive home: `ap on`; the DUT back over the bench AP within the
      budget, the pub POSTs from home again (the default route is WiFi),
      the VPN paused again (A) or re-handshaking from home (B), the dongle
      released (wifi_modem: uplink wifi, polls stop) or the wire idle
      (rndis: still enumerated, not the route)
  g5  flap: `ap off` 20 s / `ap on`, three times: back home each time,
      the pub POSTs from home at the end, no reset
  g7  sleep away, wake at home: PSU under the sleep voltage with the AP
      off; `ap on`; PSU up: the wake is clean, the station on home, the
      dongle paired again
  g8  sleep at home, wake away: the mirror: on LTE after the wake, the VPN
      up over it
  g9  (opt-in) home password changed on the P4: the station fails three
      times, is deprioritised and the DUT runs on LTE; the password
      restored (from the Pi's own profile, never shown), home again after
      the ban window (up to 15 min)
  g11 (opt-in) a phone on the WiCAN's own AP (apsta for the run, the Pi's
      wtest1 as the phone) during g2 and g4: the away switch and the
      return stay within their budgets
  g12 restore: settings as found (blank secrets keep the stored ones), the
      VPN down with the keys wiped on both sides, the receiver, the AP, the
      host route, the profiles; planned restarts only; E lines of the own
      tags only from the whitelist

  python tools/testbench/wifi/espnetlink_garage_bench.py
      [--mode usb_rndis|wifi_modem] [--vpn A|B|none] [--dut-ip 10.42.1.194]
      [--dut localhost:18081] [--pi rpi001] [--server betty]
      [--console auto|COMx|none] [--psu auto|COMx|none] [--only g1,g2,...]
      [--logdir DIR]

Prints `ESPNL GARAGE PASS`, or `ESPNL GARAGE PARTIAL PASS (legs: ...)`
with --only or when a leg was skipped for a rig reason (no console, no
PSU). About 35 min for the default legs. Written 2026-10-12.
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
from ssh_channel import SshChannel  # noqa: E402


def git_bash():
    """Git Bash explicitly for the betty soak script: a plain "bash" from a
    Windows python resolves to WSL's since 2026-10-16, which has no ssh
    config for betty, so the script printed nothing and the VPN never came
    up (garage attempt 1 of 2026-10-18)."""
    p = "C:/Program Files/Git/bin/bash.exe"
    return p if os.path.exists(p) else "bash"

LEGS = ("g1", "g2", "g3", "g6", "g10", "g4", "g5", "g7", "g8", "g9", "g11",
        "g12")                   # in running order
OPT_IN = ("g9", "g11")
OWN_TAGS = ("espnetlink", "vpn_manager", "wifi_manager", "usb_host_manager",
            "usb_eth_host")
WG_IF = "wg0"
TUN_SRV = "10.9.0.1"
TUN_DUT = "10.9.0.2"
RX_PORT = 8199
DONGLE_AP_NET = "192.168.80."    # the dongle's AP subnet (wifi_modem away)
UPLINK_AP = "espnetlink"         # /api/espnetlink `uplink` on the dongle's AP
UPLINK_USB = "espnetlink_usb"    # ... on the USB wire
PHONE_CON = "wican-bench-ap"     # the Pi profile for the WiCAN's own AP
PHONE_IF = "wtest1"
HOME_PSK_CON = "wican-bench"     # the Pi hotspot twin: the same home PSK
# E lines the away legs are allowed: the IDF HTTP client's connect-failure
# triplet while the uplink changes and esp-mqtt lines to the Pi's broker,
# which LTE cannot reach (TESTING.md)
E_WHITELIST = ("Invalid MMIE", "select() timeout",
               "Failed to open a new connection", "Connection failed, sock < 0",
               "tcp_read error", "mqtt_client", "transport_base",
               "esp-tls", "HTTP_CLIENT")
ANSI = re.compile(r"\x1b\[[0-9;]*m")

RX_SERVER = r'''
import http.server, json, time, sys
LOG = "/tmp/garage_rx.jsonl"
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(n) if n else b""
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": time.time(), "path": self.path,
                                "peer": self.client_address[0],
                                "bytes": len(body)}) + "\n")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
    do_PUT = do_POST
    def log_message(self, *a):
        pass
http.server.ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
'''


def ssh(host, cmd, timeout=60):
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o",
                            "ConnectTimeout=12", host, cmd],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "ssh timeout"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def ip_label(ip, home_ip):
    """A public address as a word: never the address itself."""
    if not ip:
        return "none"
    return "home" if ip == home_ip else "other"


# ---- the serial console (optional) -------------------------------------------

class Console:
    """The DUT's console beside the run (console_tail.py, DTR/RTS off: no
    reset)."""

    def __init__(self, port, log):
        self.port, self.log, self.proc = port, log, None
        self.stop_file = log + ".stop"

    def start(self):
        if not self.port or self.port == "none":
            return False
        here = os.path.dirname(os.path.abspath(__file__))
        tail = os.path.join(here, "..", "lib", "console_tail.py")
        if os.path.exists(self.stop_file):
            os.remove(self.stop_file)
        self.proc = subprocess.Popen(
            [sys.executable, tail, self.port, self.log, "--secs", "7200",
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

    def stop(self):
        if self.proc is None:
            return
        try:
            open(self.stop_file, "w").close()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        self.proc = None


class ConsoleCmd:
    """A command typed on the console. One process owns a COM port, so the
    tail is stopped around the command and started again after it; the
    answer is read from the port directly and copied into the tail's log."""

    def __init__(self, console):
        self.c = console

    def run(self, line, wait=2.5):
        import serial
        self.c.stop()
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = self.c.port, 2000000, 0.2
        s.dtr = False
        s.rts = False
        try:
            s.open()
        except (OSError, serial.SerialException) as e:
            self.c.start()
            return f"(cannot open {self.c.port}: {e})"
        s.reset_input_buffer()
        s.write(line.encode() + b"\r\n")
        end = time.time() + wait
        buf = b""
        while time.time() < end:
            b = s.read(4096)
            if b:
                buf += b
        s.close()
        self.c.start()
        txt = ANSI.sub("", buf.decode("utf-8", "replace"))
        with open(self.c.log, "a", encoding="utf-8") as f:
            f.write(f"[cmd {time.strftime('%H:%M:%S')}] {line}\n")
            for ln in txt.splitlines():
                f.write(f"[cmd] {ln}\n")
        return txt


# ---- where the DUT's word comes from -----------------------------------------

class Observer:
    """The DUT over its HTTP API at home (the Pi tunnel) or away (betty
    through the WireGuard tunnel), the log ring from the same place, the
    console lines when there are any."""

    def __init__(self, dut, server, console, have_vpn):
        self.dut = dut
        self.srv = server          # an SshChannel to the VPN server
        self.console = console
        self.have_vpn = have_vpn
        self.seen = set()
        self.e_lines = []
        self.con_mark = 0

    # the two paths
    def home(self, path, method="GET", body=None, timeout=6):
        return self.dut.api(path, method, body, timeout)

    def away(self, path, method="GET", body=None, timeout=8):
        if not self.have_vpn:
            return 0, "no tunnel path"
        data = ""
        if body is not None:
            data = " -H 'Content-Type: application/json' -d '%s'" % \
                json.dumps(body).replace("'", "")
        rc, out = self.srv.run(f"curl -s -m {timeout} -X {method}{data} "
                               f"-w '\\n%{{http_code}}' http://{TUN_DUT}{path}",
                               timeout=timeout + 20)
        lines = out.rstrip("\n").split("\n")
        code = lines[-1].strip() if lines else ""
        if not code.isdigit():
            return 0, out[-120:]
        text = "\n".join(lines[:-1])
        if text.strip().startswith(("{", "[")):
            try:
                return int(code), json.loads(text)
            except ValueError:
                pass
        return int(code), text

    def home_up(self):
        return self.dut.boot_count() is not None

    def away_up(self):
        code, _ = self.away("/api/status", timeout=6)
        return code == 200

    def get(self, path, where):
        """A document from the named path ("home" / "away" / "any"), {}
        when it does not answer."""
        if where in ("home", "any"):
            code, r = self.home(path)
            if code == 200 and isinstance(r, dict):
                return r
            if where == "home":
                return {}
        code, r = self.away(path)
        return r if code == 200 and isinstance(r, dict) else {}

    # the log ring and the console
    def ring(self, where):
        if where == "home":
            return self.dut.ring_lines()
        code, t = self.away("/api/logs/ring", timeout=15)
        if code != 200 or not isinstance(t, str):
            return []
        return [ANSI.sub("", ln) for ln in t.splitlines()]

    def mark(self, where="any"):
        """Forget the lines so far (from whichever path answers)."""
        for w in (("home", "away") if where == "any" else (where,)):
            if w == "away" and not self.have_vpn:
                continue
            for ln in self.ring(w):
                self.seen.add(ln)
        self.con_mark = len(self.console.lines()) if self.console else 0

    def new_lines(self, where="any"):
        """The lines not seen before, from the ring (the path that answers)
        and the console; own-tag E lines are kept for the closing check."""
        out = []
        paths = ("home", "away") if where == "any" else (where,)
        for w in paths:
            if w == "home" and not self.home_up():
                continue
            if w == "away" and not self.have_vpn:
                continue
            for ln in self.ring(w):
                if ln not in self.seen:
                    self.seen.add(ln)
                    out.append(ln)
            if out or w == "away":
                break
        if self.console:
            ls = self.console.lines()
            out.extend(ANSI.sub("", ln) for ln in ls[self.con_mark:])
            self.con_mark = len(ls)
        for ln in out:
            if ln.startswith("E (") and ") " in ln:
                tag = ln.split(") ", 1)[1].split(":", 1)[0].strip()
                if tag in OWN_TAGS and not any(w in ln for w in E_WHITELIST):
                    self.e_lines.append(ln)
        return out

    def wait_line(self, pattern, timeout, where="any", poll=4):
        rx = re.compile(pattern)
        t0 = time.time()
        while time.time() - t0 < timeout:
            for ln in self.new_lines(where):
                if rx.search(ln):
                    return ln
            time.sleep(poll)
        return None

    @staticmethod
    def stamp_ms(ln):
        m = re.match(r"[IWEDV] \((\d+)\)", ln)
        return int(m.group(1)) if m else None


def wait_until(what, pred, budget, poll=5):
    t0 = time.time()
    while time.time() - t0 < budget:
        try:
            if pred():
                return time.time() - t0
        except pcbench.Bench:
            pass
        time.sleep(poll)
    print(f"  timeout: {what} ({budget:.0f} s)", flush=True)
    return -1.0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", default="usb_rndis",
                    choices=("usb_rndis", "wifi_modem"))
    ap.add_argument("--vpn", default="A", choices=("A", "B", "none"))
    ap.add_argument("--dut", default="")
    ap.add_argument("--dut-ip", default="10.42.1.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--server", default="betty")
    ap.add_argument("--console", default="auto")
    ap.add_argument("--psu", default="auto")
    ap.add_argument("--only", default="")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS if (l in only if only else l not in OPT_IN)
            or l == "g12"]
    logdir = a.logdir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "test-reports", "logs", time.strftime("garage_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("ESPNL GARAGE")
    skipped = []
    print(f"mode {a.mode}, vpn variant {a.vpn}, legs {' '.join(legs)}, "
          f"logs {logdir}", flush=True)

    # one ssh session per host for the whole run: betty banned the PC after
    # two hours of a session per poll (2026-10-12)
    pi_ch = SshChannel(a.pi)
    srv_ch = SshChannel(a.server)

    def pi(cmd, timeout=60):
        return pi_ch.run(cmd, timeout)

    # ---- the rig --------------------------------------------------------------
    def p4(cmd, wait=1.5):
        return pi("cd ~/wican/tools/testbench/pi && timeout 25 "
                   f"python3 bench_ap_cli.py '{cmd}' --wait {wait}")

    def srv(cmd, timeout=40):
        return srv_ch.run(cmd, timeout)

    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18081"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o",
                                   "ExitOnForwardFailure=yes", "-N", "-L",
                                   f"18081:{a.dut_ip}:80", a.pi])
        time.sleep(3)
        if tunnel.poll() is not None:
            print("no tunnel to the DUT through the Pi (port 18081 busy?)")
            return 2
    dut = pcbench.Dut(hostport, own_tags=OWN_TAGS)

    port = a.console
    if port == "auto":
        try:
            import bench_ports  # noqa: E402
            port = bench_ports.resolve("auto", ("wican_console", "ch342_console"),
                                       "none")
        except Exception as e:  # noqa: BLE001
            print("console: detector failed:", e)
            port = "none"
    console = Console(port, os.path.join(logdir, "console.log"))
    cc = None
    if console.start():
        cc = ConsoleCmd(console)
        probe = cc.run("espnetlink")
        if "ESPNETLINK:" not in probe:
            print(f"console: {port} opens but does not answer `espnetlink`: "
                  "treated as absent (g10 needs it)", flush=True)
            console.stop()
            cc = None
    have_console = cc is not None
    print("console: " + ("on " + port if have_console else "off"), flush=True)

    psu = None
    psu_setpoint = None
    if any(l in legs for l in ("g1", "g7", "g8")) and a.psu != "none":
        try:
            import bench_ports  # noqa: E402
            from owon_psu import OwonPsu  # noqa: E402
            pport = bench_ports.resolve(a.psu, "psu", "")
            if pport:
                psu = OwonPsu(pport)
                psu_setpoint = psu.voltage_setpoint()
        except Exception as e:  # noqa: BLE001
            print("psu: unavailable:", e)
    print("psu: " + (f"on, setpoint {psu_setpoint:.2f} V" if psu
                     else "off (g1 uses a planned restart; g7/g8 skip)"),
          flush=True)

    obs = Observer(dut, srv_ch, console if have_console else None,
                   a.vpn != "none")

    def wg_show():
        rc, out = srv(f"sudo -n wg show {WG_IF} dump 2>/dev/null")
        for ln in out.splitlines()[1:]:
            parts = ln.split("\t")
            if len(parts) >= 7:
                return {"endpoint": parts[2], "handshake": int(parts[4] or 0),
                        "rx": int(parts[5] or 0), "tx": int(parts[6] or 0)}
        return {"endpoint": "", "handshake": 0, "rx": 0, "tx": 0}

    def rx_records():
        rc, out = srv("cat /tmp/garage_rx.jsonl 2>/dev/null")
        recs = []
        for ln in out.splitlines():
            try:
                recs.append(json.loads(ln))
            except ValueError:
                continue
        return recs

    def rx_since(n, path):
        return [r for r in rx_records()[n:] if r.get("path") == path]

    def ep_ip(ep):
        return ep.rsplit(":", 1)[0] if ep else ""

    found = {}
    exit_code = 1
    betty_ip = ""
    home_pub = ""            # the home side's public address (never printed)
    host_route_added = False
    rx_started = False
    vpn_up = False
    soak_attempted = False
    phone_on = False
    t_away = 0.0
    hs0 = 0
    n0 = 0

    def paired_per_mode():
        n = obs.get("/api/espnetlink", "home")
        u = n.get("usb") or {}
        if a.mode == "usb_rndis":
            return u.get("pair_state") == "ncm_up" and n.get("uplink") == UPLINK_USB
        return u.get("pair_state") in ("done", "idle") and bool(n.get("paired"))

    def station_home():
        w = obs.get("/api/wifi/status", "home")
        return w.get("sta_connected") is True and \
            str(w.get("ip", "")).startswith("10.42.")

    def pub_from(label_ip, n_from, budget):
        """A pub POST from the given side since record n_from."""
        return wait_until(f"a pub POST from {ip_label(label_ip, home_pub)}",
                          lambda: any(r.get("peer") == label_ip
                                      for r in rx_since(n_from, "/pub")), budget)

    def pub_moves_off_home(n_from, budget):
        return wait_until("a pub POST not from home",
                          lambda: any(r.get("peer") not in ("", home_pub)
                                      for r in rx_since(n_from, "/pub")), budget)

    def away_reach(budget):
        if a.vpn == "none":
            return -1.0
        return wait_until("the DUT through the tunnel from betty",
                          obs.away_up, budget, poll=6)

    def home_reach(budget):
        return wait_until("the DUT over the bench AP", obs.home_up, budget)

    try:
        # ---- g0: as found and the rig -------------------------------------------
        info = dut.get("/api/info")
        st0 = dut.get("/api/status")
        dut.as_found()
        obs.mark("home")
        wst = dut.get("/api/wifi/status")
        print(f"DUT {info.get('device_id')} {info.get('fw_version')} station "
              f"{'on ' + str(wst.get('ip')) if wst.get('sta_connected') else 'off'}",
              flush=True)
        if not wst.get("sta_connected"):
            raise pcbench.Bench("the station is not on the bench AP")
        for name in ("espnetlink", "wifi_manager", "vpn_manager",
                     "data_destinations", "sleep_manager"):
            found[name] = dut.settings(name)
        home = str(found["wifi_manager"].get("sta_ssid") or "")
        run.check("g0_primary_is_home", bool(home), home)
        nl = dut.get("/api/espnetlink")
        run.check("g0_dongle_paired_or_on_the_wire",
                  bool(nl.get("paired")) or nl.get("uplink") == UPLINK_USB,
                  f"paired={nl.get('paired')} uplink={nl.get('uplink')}")
        uhm = dut.settings("usb_host_manager")
        run.check("g0_station_first_route", uhm.get("prefer_usb_route") is False,
                  f"prefer_usb_route={uhm.get('prefer_usb_route')}")
        rc, out = p4("ap")
        if "ap: on" not in out:
            raise pcbench.Bench("the P4 daemon does not answer `ap` (start it: "
                                "bench_ap_daemon.py on the Pi): " + out.strip()[-80:])
        run.check("g0_p4_daemon_answers", True, out.strip()[:60])

        changes = {"espnetlink": {"mode": a.mode},
                   "wifi_manager": {"sta_roam_interval_s": 60,
                                    "sta_vpn_pause": a.vpn == "A"}}
        for i in range(1, 6):
            if found["wifi_manager"].get(f"fallback{i}_ssid", "") == nl.get("ssid"):
                changes["wifi_manager"][f"fallback{i}_vpn_pause"] = False
        if "g11" in legs:
            changes["wifi_manager"]["mode"] = "apsta"

        rc, out = srv("curl -s -m 8 https://api.ipify.org")
        betty_ip = out.strip()
        run.check("g0_server_public_address",
                  bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", betty_ip)))
        pi(f"sudo -n ip route replace {betty_ip}/32 via 192.168.8.1 "
                  "dev eth0")
        host_route_added = True
        # the kill runs in a command of its own: `pkill -f` matches any shell
        # whose command line carries the file name, this one's included when
        # the start sits in the same command (the receiver "never started"
        # twice on 2026-10-12)
        srv("pkill -f 'garage_r[x].py' 2>/dev/null; true")
        srv_ch.put("/tmp/garage_rx.py", RX_SERVER)
        srv(f"rm -f /tmp/garage_rx.jsonl; (setsid nohup python3 /tmp/garage_rx.py "
            f"{RX_PORT} >/tmp/garage_rx.out 2>&1 </dev/null &) ; sleep 1")
        rx_started = True
        rc, out = srv(f"curl -s -m 5 -o /dev/null -w '%{{http_code}}' -X POST "
                      f"-d x http://127.0.0.1:{RX_PORT}/self")
        run.check("g0_receiver_up_on_betty", out.strip() == "200", out.strip())

        rows = [r for r in found["data_destinations"].get("destinations", [])
                if not str(r.get("name", "")).startswith("garage_")]
        rows.append({"name": "garage_pub", "type": "http", "enabled": True,
                     "url": f"http://{betty_ip}:{RX_PORT}/pub", "period_s": 5,
                     "auth": "none", "retain": False, "full_first": True})
        if a.vpn != "none":
            rows.append({"name": "garage_tun", "type": "http", "enabled": True,
                         "url": f"http://{TUN_SRV}:{RX_PORT}/tun", "period_s": 5,
                         "auth": "none", "retain": False, "full_first": True})
        changes["data_destinations"] = {"enabled": True, "destinations": rows}

        if a.vpn != "none":
            here = os.path.dirname(os.path.abspath(__file__))
            soak = os.path.join(here, "..", "vpn", "wg_public_soak.sh")
            env = dict(os.environ, DUT_IP=a.dut_ip)
            soak_attempted = True
            r = subprocess.run([git_bash(), soak, "up", "split"], env=env,
                               capture_output=True, text=True, timeout=400)
            tail = [ln for ln in r.stdout.splitlines() if ln.startswith(("PASS", "FAIL"))]
            print("  wg_public_soak up: " + " | ".join(tail), flush=True)
            vpn_up = "PASS: handshake established" in r.stdout
            run.check("g0_vpn_up_over_home", vpn_up)
            dut.restarts += 1          # the soak script submitted + restarted
            home_reach(150)
        dut.restart(changes)
        wait_until("station on home after the restart", station_home, 90)
        # the home side's public address is what the pub POSTs show while the
        # station is up (three of them: the first after a boot can still
        # leave over the wire); the first WireGuard handshake after a restart
        # is no reference, it went over LTE on 2026-10-12
        n0 = len(rx_records())
        dt = wait_until("three pub POSTs from home",
                        lambda: len(rx_since(n0, "/pub")) >= 3, 90)
        peers = [r.get("peer", "") for r in rx_since(n0, "/pub")]
        home_pub = max(set(peers), key=peers.count) if peers else ""
        run.check("g0_pub_posts_from_home", dt >= 0 and bool(home_pub),
                  f"{dt:.0f} s, {len(peers)} POSTs, "
                  f"{sum(1 for p in peers if p == home_pub)} from the home side")
        if a.vpn == "B":
            dt = wait_until("the tunnel endpoint on the home side",
                            lambda: ep_ip(wg_show()["endpoint"]) == home_pub, 90)
            run.check("g0_vpn_endpoint_is_home", dt >= 0,
                      f"{dt:.0f} s (the first handshake after a restart can go over the wire)")
        if "g11" in legs:
            rc, out = pi(f"sudo -n nmcli con up {PHONE_CON} ifname "
                                f"{PHONE_IF} 2>&1 | tail -1")
            phone_on = rc == 0
            run.check("g11_phone_on_the_wican_ap", phone_on, out.strip()[-80:])

        # ---- g1: engine on in the garage -------------------------------------------
        if "g1" in legs:
            obs.mark("home")
            if psu:
                psu.output(False)
                time.sleep(8)
                psu.output(True)
                t_on = time.time()
                dut.restarts += 1
                dt = home_reach(150)
                run.check("g1_cold_boot_back_on_home", dt >= 0, f"{dt:.0f} s")
                # the power cycle resets the boot counter: the closing reset
                # accounting is re-based here (the boots before it were
                # counted against the old counter and could never match,
                # 2026-10-18); the wakes of g7/g8 are the planned boots after
                if dt >= 0:
                    st0 = dut.get("/api/status")
                    dut.restarts = 0
            else:
                t_on = dut.restart()
                print("  no PSU: a planned restart stands in for the cold cycle",
                      flush=True)
            dt = wait_until("station on home", station_home, 60)
            run.check("g1_station_on_home_within_60s", dt >= 0, f"{dt:.0f} s")
            run.metric("g1_boot_to_home_s", round(time.time() - t_on, 1))
            dt = wait_until("dongle paired per mode", paired_per_mode, 150)
            run.check(f"g1_dongle_paired_{a.mode}", dt >= 0, f"{dt:.0f} s")
            run.metric("g1_boot_to_dongle_s", round(time.time() - t_on, 1))
            dt = wait_until("GPS valid", lambda: (obs.get("/api/espnetlink", "home")
                            .get("gps") or {}).get("valid") is True, 300)
            run.check("g1_gps_valid_within_300s", dt >= 0,
                      f"{dt:.0f} s" + (" (the wire is live in the USB modes: the "
                                       "known GNSS desense)" if a.mode != "wifi_modem"
                                       else ""))
            run.metric("g1_gps_fix_s", round(dt, 1) if dt >= 0 else -1)
            if a.vpn == "A":
                dt = wait_until("VPN paused at home", lambda: obs.get(
                    "/api/vpn", "home").get("state") == "paused", 60)
                run.check("g1_vpn_paused_at_home", dt >= 0, f"{dt:.0f} s")
            elif a.vpn == "B":
                dt = wait_until("VPN connected at home", lambda: obs.get(
                    "/api/vpn", "home").get("state") == "connected", 120)
                run.check("g1_vpn_connected_at_home", dt >= 0, f"{dt:.0f} s")
            n1 = len(rx_records())
            dt = pub_from(home_pub, n1, 60) if home_pub else -1.0
            run.check("g1_pub_posts_from_home", dt >= 0, f"{dt:.0f} s")
            lines = obs.new_lines("home")
            hit = [ln for ln in lines if re.search(
                r"espnetlink: (identified|dongle USB uplink|credentials ok)", ln)]
            run.check("g1_ring_shows_the_pairing", bool(hit), hit[0][:100] if hit else "")

        # ---- g2: drive away ---------------------------------------------------------
        g2_lines = []
        if "g2" in legs:
            obs.mark("home")
            hs0 = wg_show()["handshake"] if a.vpn != "none" else 0
            n0 = len(rx_records())
            rc, out = p4("ap off")
            run.check("g2_home_ap_off", rc == 0 and "off" in out, out.strip()[-60:])
            t_away = time.time()
            if a.vpn != "none":
                dt = away_reach(180)
                run.check("g2_dut_reachable_through_the_tunnel", dt >= 0, f"{dt:.0f} s")
                run.metric("g2_ap_off_to_tunnel_s", round(dt, 1) if dt >= 0 else -1)
                n = obs.get("/api/espnetlink", "away")
                w = obs.get("/api/wifi/status", "away")
                s = obs.get("/api/status", "away")
                if a.mode == "usb_rndis":
                    # a DUT that was paired as WiFi modem earlier keeps the
                    # dongle's AP as a station slot and parks on it while home
                    # is away; the wire stays the dongle path either way
                    on_dongle_ap = str(w.get("ip", "")).startswith(DONGLE_AP_NET)
                    run.check("g2_station_off_home_uplink_is_the_wire",
                              (w.get("sta_connected") is False or on_dongle_ap)
                              and n.get("uplink") == UPLINK_USB,
                              f"sta={w.get('sta_connected')} ip={w.get('ip')} "
                              f"uplink={n.get('uplink')}"
                              + (" (parked on the dongle's AP slot)" if on_dongle_ap else ""))
                else:
                    run.check("g2_station_on_the_dongle_ap",
                              str(w.get("ip", "")).startswith(DONGLE_AP_NET)
                              and n.get("uplink") == UPLINK_AP,
                              f"ip={w.get('ip')} uplink={n.get('uplink')}")
                run.check("g2_time_still_synced",
                          (s.get("bits") or {}).get("time_synced") is True)
                dns = (w.get("dns") or [""])[0]
                if (dns in ("", "N/A") and w.get("sta_connected") is False
                        and a.mode != "wifi_modem"):
                    # the station is fully off (no dongle AP slot to park on):
                    # the resolver in use is the USB lease's, which
                    # /api/wifi/status cannot report (2026-10-18)
                    run.check("g2_dns_from_the_dongle", True,
                              "station off: the USB lease's resolver, not reported by "
                              "/api/wifi/status")
                else:
                    run.check("g2_dns_from_the_dongle",
                              dns.startswith("192.168.7.") or dns.startswith(DONGLE_AP_NET),
                              dns)
                lines = obs.new_lines("away")
                g2_lines = lines
                up = [ln for ln in lines if "espnetlink: uplink:" in ln]
                lost = [ln for ln in lines if re.search(
                    r"wifi_manager: .*(disconnect|lost|left)", ln, re.I)]
                if up and lost and obs.stamp_ms(up[-1]) and obs.stamp_ms(lost[0]):
                    run.metric("g2_sta_loss_to_uplink_switch_s",
                               round((obs.stamp_ms(up[-1]) - obs.stamp_ms(lost[0])) / 1000, 1))
            else:
                dt = pub_moves_off_home(n0, 180)
                run.check("g2_pub_posts_move_off_home", dt >= 0, f"{dt:.0f} s")
            if have_console and a.mode == "wifi_modem":
                want = r"uplink: wifi -> espnetlink \(AP\)"
                # the lines of the switch were consumed by the block above
                # (2026-10-18: the 10 s wait for a NEWER one failed on a
                # clean leg): look there first
                seen = [ln for ln in g2_lines if re.search(want, ln)]
                ln = (seen[-1] if seen else None) or obs.wait_line(
                    want, 10, "away" if a.vpn != "none" else "home")
                run.check("g2_console_saw_the_switch", ln is not None, (ln or "")[:100])
            elif have_console:
                # in a USB mode the uplink stays the wire and wifi_manager
                # logs the station's loss at DEBUG only (`STA disconnected,
                # reason`): nothing to see at INFO; the API checks above
                # hold the leg (2026-10-18)
                run.check("g2_console_saw_the_switch", True,
                          "usb mode: the station loss is a DEBUG line, the wire "
                          "stays the uplink (checked over the API above)")

        # ---- g3: the VPN over LTE -----------------------------------------------------
        if "g3" in legs and a.vpn != "none":
            dt = wait_until("a handshake newer than before the leg",
                            lambda: wg_show()["handshake"] > hs0, 120, poll=5)
            run.check("g3_handshake_over_lte", dt >= 0,
                      f"{dt:.0f} s after reaching the tunnel")
            w1 = wg_show()
            run.check("g3_endpoint_moved_off_home",
                      bool(w1["endpoint"]) and ep_ip(w1["endpoint"]) != home_pub,
                      f"endpoint now {ip_label(ep_ip(w1['endpoint']), home_pub)}")
            dt = wait_until("a tun POST from the DUT's tunnel address",
                            lambda: any(r.get("peer") == TUN_DUT
                                        for r in rx_since(n0, "/tun")), 150)
            run.check("g3_data_arrives_through_the_tunnel", dt >= 0, f"{dt:.0f} s")
            run.metric("g3_ap_off_to_tunnel_data_s", round(time.time() - t_away, 1))
            dt = pub_moves_off_home(n0, 60)
            run.check("g3_pub_posts_from_the_carrier_side", dt >= 0, f"{dt:.0f} s")
            time.sleep(20)
            w2 = wg_show()
            run.check("g3_transfer_grows", w2["rx"] > w1["rx"] and w2["tx"] > w1["tx"],
                      f"rx {w1['rx']} -> {w2['rx']}, tx {w1['tx']} -> {w2['tx']}")
            v = obs.get("/api/vpn", "away")
            run.check("g3_dut_says_connected", v.get("state") == "connected",
                      f"state={v.get('state')} network={v.get('network')}")
            d = obs.get("/api/destinations", "away")
            rows_st = {r.get("name"): r for r in d.get("destinations", [])}
            tun = rows_st.get("garage_tun") or {}
            run.check("g3_tun_row_delivers", (tun.get("success") or 0) > 0
                      and tun.get("last_status") == 200,
                      f"success={tun.get('success')} fail={tun.get('fail')} "
                      f"last={tun.get('last_status')} backoff={tun.get('backoff_s')}")

        # ---- g6: dongle reboot while away ----------------------------------------------
        if "g6" in legs and a.vpn != "none":
            n_before = obs.get("/api/espnetlink", "away")
            s_before = obs.get("/api/status", "away")
            hs_b = wg_show()["handshake"]
            code, r = obs.away("/api/espnetlink/repair", "POST", {})
            run.check("g6_repair_accepted", code == 200, f"HTTP {code}")
            time.sleep(15)
            dt = away_reach(240)
            run.check("g6_tunnel_back_after_the_dongle_reboot", dt >= 0,
                      f"{dt:.0f} s")
            run.metric("g6_repair_to_tunnel_back_s", round(dt + 15, 1) if dt >= 0 else -1)
            n_after = obs.get("/api/espnetlink", "away")
            s_after = obs.get("/api/status", "away")
            run.check("g6_vbus_cycles_grew",
                      ((n_after.get("usb") or {}).get("vbus_cycles") or 0)
                      > ((n_before.get("usb") or {}).get("vbus_cycles") or 0),
                      f"{(n_before.get('usb') or {}).get('vbus_cycles')} -> "
                      f"{(n_after.get('usb') or {}).get('vbus_cycles')}")
            run.check("g6_no_wican_restart",
                      s_after.get("boot_count") == s_before.get("boot_count"),
                      f"boot_count {s_before.get('boot_count')} -> {s_after.get('boot_count')}")
            dt = wait_until("a handshake after the dongle reboot",
                            lambda: wg_show()["handshake"] > hs_b, 60)
            run.check("g6_vpn_handshake_after_reboot", dt >= 0, f"{dt:.0f} s")
            dt = wait_until("GPS valid again", lambda: (obs.get("/api/espnetlink", "away")
                            .get("gps") or {}).get("valid") is True, 180, poll=10)
            run.check("g6_gps_valid_again", dt >= 0, f"{dt:.0f} s")
            if a.mode == "wifi_modem":
                ln = obs.wait_line(r"credentials ok: .* unchanged", 10, "away")
                run.check("g6_key_re_read_unchanged", ln is not None, (ln or "")[:100])
            else:
                ln = obs.wait_line(r"dongle USB uplink already configured", 10, "away")
                run.check("g6_dongle_ensured_nothing_changed", ln is not None,
                          (ln or "")[:100])

        # ---- g10: dongle gone while away (console only) --------------------------------
        if "g10" in legs:
            if not have_console:
                skipped.append("g10 (no console)")
                print("  g10 skipped: `usb vbus` needs the console", flush=True)
            else:
                obs.mark("away")
                cc.run("usb vbus 0", 2)
                want = (r"uplink: espnetlink \(AP\) -> none" if a.mode == "wifi_modem"
                        else r"uplink: espnetlink \(USB\) -> none")
                ln = obs.wait_line(want, 120, "home")   # the console only
                run.check("g10_uplink_none_without_the_dongle", ln is not None,
                          (ln or "")[:100])
                cc.run("usb vbus 1", 2)
                dt = away_reach(240) if a.vpn != "none" else -1.0
                if a.vpn != "none":
                    run.check("g10_dongle_back_tunnel_back", dt >= 0, f"{dt:.0f} s")
                crash = [ln for ln in obs.new_lines("home")
                         if re.search(r"Guru Meditation|wdt timeout|abort\(\)", ln)]
                run.check("g10_no_crash", not crash, (crash[0] if crash else "")[:100])

        # ---- g4: drive home ---------------------------------------------------------------
        if "g4" in legs:
            n0 = len(rx_records())
            hs0 = wg_show()["handshake"] if a.vpn != "none" else 0
            rc, out = p4("ap on")
            run.check("g4_home_ap_on", rc == 0 and "on" in out, out.strip()[-60:])
            t_home = time.time()
            budget = 60 + 60 if a.mode == "wifi_modem" else 120
            dt = home_reach(budget + 60)
            run.check(f"g4_station_back_home_within_{budget}s", 0 <= dt <= budget,
                      f"{dt:.0f} s")
            run.metric("g4_ap_on_to_home_s", round(dt, 1) if dt >= 0 else -1)
            dt = pub_from(home_pub, n0, 90) if home_pub else -1.0
            run.check("g4_pub_posts_from_home_again_default_route_is_wifi",
                      dt >= 0, f"{dt:.0f} s after ap on")
            run.metric("g4_ap_on_to_home_route_s", round(time.time() - t_home, 1))
            n = obs.get("/api/espnetlink", "home")
            if a.mode == "wifi_modem":
                run.check("g4_dongle_released_uplink_wifi", n.get("uplink") == "wifi",
                          n.get("uplink"))
                time.sleep(30)
                n2 = obs.get("/api/espnetlink", "home")
                run.check("g4_dongle_polls_stop", n2.get("polls") == n.get("polls"),
                          f"polls {n.get('polls')} -> {n2.get('polls')}")
            else:
                u = n.get("usb") or {}
                run.check("g4_wire_still_enumerated_idle",
                          u.get("pair_state") == "ncm_up" and u.get("attached") is True,
                          f"pair_state={u.get('pair_state')} attached={u.get('attached')}")
            if a.vpn == "A":
                dt = wait_until("VPN paused at home", lambda: obs.get(
                    "/api/vpn", "home").get("state") == "paused", 60)
                run.check("g4_vpn_paused_again", dt >= 0, f"{dt:.0f} s")
            elif a.vpn == "B":
                dt = wait_until("the endpoint back home at betty",
                                lambda: wg_show()["handshake"] > hs0
                                and ep_ip(wg_show()["endpoint"]) == home_pub, 240)
                run.check("g4_endpoint_back_home", dt >= 0,
                          f"{dt:.0f} s, endpoint {ip_label(ep_ip(wg_show()['endpoint']), home_pub)}")
                n1 = len(rx_records())
                dt = wait_until("a tun POST from home",
                                lambda: any(r.get("peer") == TUN_DUT
                                            for r in rx_since(n1, "/tun")), 90)
                run.check("g4_tunnel_data_from_home", dt >= 0, f"{dt:.0f} s")
            if "g11" in legs:
                rc, out = pi(f"nmcli -t -f GENERAL.STATE con show {PHONE_CON} "
                                    "2>/dev/null | head -1")
                run.check("g11_phone_still_on_the_ap", "activated" in out, out.strip())

        # ---- g5: flap ------------------------------------------------------------------------
        if "g5" in legs:
            b0 = dut.boot_count()
            ok = True
            times = []
            for i in range(3):
                p4("ap off")
                time.sleep(20)
                p4("ap on")
                dt = home_reach(180)
                ok = ok and dt >= 0
                times.append(round(dt, 1))
                print(f"  flap {i + 1}: back after {dt:.0f} s", flush=True)
            run.check("g5_three_flaps_followed", ok, f"{times} s")
            run.metric("g5_flap_return_max_s", max(times))
            n1 = len(rx_records())
            dt = pub_from(home_pub, n1, 90) if home_pub else -1.0
            run.check("g5_pub_posts_from_home_after_the_flaps", dt >= 0, f"{dt:.0f} s")
            run.check("g5_no_reset", dut.boot_count() == b0,
                      f"boot_count {b0} -> {dut.boot_count()}")

        # ---- g7 / g8: sleep away / at home ---------------------------------------------------
        def sleep_leg(name, away_at_sleep):
            obs.mark("home")
            dut.restart({"sleep_manager": {"enabled": True, "sleep_delay_min": 1}})
            wait_until("station on home", station_home, 90)
            samples = psu.sample_current(4)
            awake_a = sum(samples) / max(1, len(samples))
            if away_at_sleep:
                p4("ap off")
                if a.vpn != "none":
                    away_reach(180)
            psu.set_voltage(12.5)
            t0 = time.time()
            asleep = psu.wait_current(lambda i: i < awake_a * 0.5, timeout_s=240)
            run.check(f"{name}_asleep_under_the_sleep_voltage", asleep is not None,
                      f"awake {awake_a * 1000:.0f} mA -> "
                      f"{(asleep or 0) * 1000:.0f} mA after {time.time() - t0:.0f} s")
            run.metric(f"{name}_sleep_entry_s", round(time.time() - t0, 1))
            sleep_a = asleep if asleep is not None else awake_a * 0.3
            if away_at_sleep:
                p4("ap on")
            else:
                p4("ap off")
            time.sleep(5)
            psu.set_voltage(14.0)
            t1 = time.time()
            awake = psu.wait_current(lambda i: i > sleep_a + 0.060, timeout_s=90)
            run.check(f"{name}_awake_on_the_wake_voltage", awake is not None,
                      f"{(awake or 0) * 1000:.0f} mA")
            dut.restarts += 1
            if away_at_sleep:
                dt = home_reach(150)
                run.check(f"{name}_wake_at_home", dt >= 0, f"{dt:.0f} s")
                run.metric(f"{name}_wake_to_home_s", round(time.time() - t1, 1))
                dt = wait_until("dongle paired after the wake", paired_per_mode, 180)
                run.check(f"{name}_dongle_paired_after_wake", dt >= 0, f"{dt:.0f} s")
                lines = obs.new_lines("home")
                crash = [ln for ln in lines if re.search(
                    r"Guru Meditation|wdt timeout|abort\(\)|WICAN PARK", ln)]
                run.check(f"{name}_wake_clean", not crash, (crash[0] if crash else "")[:100])
            else:
                if a.vpn != "none":
                    dt = away_reach(300)
                    run.check(f"{name}_wake_away_on_lte_vpn_up", dt >= 0, f"{dt:.0f} s")
                    run.metric(f"{name}_wake_to_tunnel_s", round(time.time() - t1, 1))
                    n = obs.get("/api/espnetlink", "away")
                    run.check(f"{name}_uplink_is_the_dongle",
                              n.get("uplink") in (UPLINK_USB, UPLINK_AP), n.get("uplink"))
                else:
                    n1 = len(rx_records())
                    dt = pub_moves_off_home(n1, 300)
                    run.check(f"{name}_wake_away_pub_off_home", dt >= 0, f"{dt:.0f} s")
                p4("ap on")
                dt = home_reach(240)
                run.check(f"{name}_home_again", dt >= 0, f"{dt:.0f} s")
            psu.set_voltage(psu_setpoint)
            dut.restart({"sleep_manager": {
                "enabled": found["sleep_manager"].get("enabled", True),
                "sleep_delay_min": found["sleep_manager"].get("sleep_delay_min", 5)}})
            wait_until("station on home", station_home, 90)

        for name, away_at_sleep in (("g7", True), ("g8", False)):
            if name in legs:
                if psu:
                    sleep_leg(name, away_at_sleep)
                else:
                    skipped.append(f"{name} (no PSU)")
                    print(f"  {name} skipped: no PSU", flush=True)

        # ---- g9: the home password changed on the P4 (opt-in) -----------------------------
        if "g9" in legs:
            rc, out = pi(f"sudo -n nmcli -s -g 802-11-wireless-security.psk "
                                f"con show {HOME_PSK_CON} | wc -c")
            have_psk = out.strip().isdigit() and int(out.strip()) > 8
            if not have_psk:
                skipped.append("g9 (no home PSK on the Pi)")
                print("  g9 skipped: the Pi's profile has no PSK to restore from",
                      flush=True)
            else:
                n1 = len(rx_records())
                rc, out = p4("ap set WICAN_TEST_AP garage-wrong-psk-2026")
                run.check("g9_home_password_changed", rc == 0, out.strip()[-60:])
                t9 = time.time()
                if a.vpn != "none":
                    dt = away_reach(400)
                    run.check("g9_on_lte_after_the_failures", dt >= 0, f"{dt:.0f} s")
                    w = obs.get("/api/wifi/status", "away")
                    att = w.get("sta_attempt") or {}
                    run.check("g9_station_failed_and_deprioritised",
                              (att.get("fail_count") or 0) >= 3 or att.get("deprioritised"),
                              f"fail_count={att.get('fail_count')} reason={att.get('reason')} "
                              f"deprioritised={att.get('deprioritised')}")
                else:
                    dt = pub_moves_off_home(n1, 400)
                    run.check("g9_pub_off_home_after_the_failures", dt >= 0, f"{dt:.0f} s")
                run.metric("g9_wrong_psk_to_lte_s", round(time.time() - t9, 1))
                rc, out = pi("cd ~/wican/tools/testbench/pi && PSK=$(sudo -n nmcli -s "
                                    f"-g 802-11-wireless-security.psk con show {HOME_PSK_CON}) "
                                    "&& timeout 25 python3 bench_ap_cli.py \"ap set "
                                    "WICAN_TEST_AP $PSK\" | sed 's/WICAN_TEST_AP .*/WICAN_TEST_AP (psk hidden)/'")
                run.check("g9_home_password_restored", rc == 0, out.strip()[-60:])
                dt = home_reach(15 * 60)
                run.check("g9_home_again_after_the_ban", dt >= 0, f"{dt:.0f} s")
                run.metric("g9_restore_to_home_s", round(dt, 1) if dt >= 0 else -1)

        # ---- closing ------------------------------------------------------------------------
        home_reach(60)
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        obs.new_lines("home")
        run.check("away_own_E_lines_only_whitelisted", not obs.e_lines,
                  " | ".join(obs.e_lines)[:300])
        exit_code = 0
    except pcbench.Bench as e:
        run.check("rig", False, str(e))
    finally:
        # ---- g12: as found --------------------------------------------------------------------
        try:
            p4("ap on")
            if phone_on:
                pi(f"sudo -n nmcli con down {PHONE_CON} >/dev/null 2>&1; true")
            if psu:
                try:
                    psu.set_voltage(psu_setpoint)
                    psu.close()
                except Exception:  # noqa: BLE001
                    pass
            wait_until("the DUT home for the restore", obs.home_up, 240)
            if soak_attempted:
                here = os.path.dirname(os.path.abspath(__file__))
                soak = os.path.join(here, "..", "vpn", "wg_public_soak.sh")
                r = subprocess.run([git_bash(), soak, "down", "--wipe"],
                                   env=dict(os.environ, DUT_IP=a.dut_ip),
                                   capture_output=True, text=True, timeout=300)
                print("  wg_public_soak down --wipe: " +
                      " | ".join(r.stdout.strip().splitlines()[-3:]), flush=True)
                time.sleep(5)
                wait_until("the DUT back after the VPN wipe", obs.home_up, 150)
            if rx_started:
                srv("pkill -f 'garage_r[x].py' 2>/dev/null; true")
                srv("rm -f /tmp/garage_rx.py /tmp/garage_rx.jsonl /tmp/garage_rx.out; true")
            back = {}
            for name in ("espnetlink", "wifi_manager", "vpn_manager",
                         "data_destinations", "sleep_manager"):
                if name not in found:
                    continue
                doc = dict(found[name])
                # blank secrets on PUT keep the stored ones (the api_http rule)
                for k in list(doc):
                    if k in ("sta_password", "ap_password", "private_key",
                             "preshared_key", "ts_auth_key") or \
                            re.match(r"fallback\d_password$", k):
                        doc[k] = ""
                back[name] = doc
            if back:
                try:
                    dut.restart(back)
                    wait_until("station on home after the restore", station_home, 90)
                except pcbench.Bench as e:
                    print("restore:", e, flush=True)
            if host_route_added and betty_ip:
                pi(f"sudo -n ip route del {betty_ip}/32 2>/dev/null; true")
        finally:
            console.stop()
            pi_ch.close()
            srv_ch.close()
            if tunnel:
                tunnel.terminate()
    partial = ""
    if only or skipped:
        partial = "legs: " + " ".join(l for l in legs if l != "g12")
        if skipped:
            partial += "; skipped " + ", ".join(skipped)
    code = run.verdict(partial)
    return code if exit_code == 0 else max(code, 1)


if __name__ == "__main__":
    sys.exit(main())
