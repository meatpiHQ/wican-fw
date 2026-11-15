#!/usr/bin/env python3
"""A drive, with or without an ESPNetLink on the WiCAN (Ali, 2026-11-16:
"test ESPNetLink scenarios: away from home, came back home, sleep, wake up,
drive"). The garage bench proves the networks change hands; this one proves
what the car does meanwhile: the engine runs, AutoPID polls the car the
whole way, the parameters reach a server on the internet while the car is
on the road (through the dongle), the drive is recorded on the SD card,
the car comes home, the engine stops, the unit sleeps, and wakes with the
next engine start with the recorded drive readable from the card.

The unit is taken as it is configured. With an uplink on the connector
(the ESPNetLink on the wire, or paired as a WiFi modem) the parameters
must keep arriving at the receiver from the carrier's side during the
drive; without one (ESPNetLink off and nothing else to route through)
nothing may arrive, nothing may count as a failure, and the card holds
the drive: the store and forward case. Store and forward (the logger as
JSON lines or CSV with every AutoPID sample) is switched on for the run
when the unit does not have it.

How it observes. At home the DUT's HTTP API through an ssh tunnel to the
Pi. On the road nothing reaches the DUT (no VPN here: the garage bench has
that), so the road is judged at the receiver on betty (every POST: when,
from which side, how many parameters), on the console when there is one
(reset and crash lines, the dongle repair typed there), and by the unit's
own counters once it is home again: no boot in between, the polls and the
recorded rows grew at the rate measured in the garage. Public addresses
are never printed ("home" / "other"), parameter values never leave the
receiver (names and counts only: the fix is among them).

Legs (--only to pick; d0 and d7 always run):
  d0  the rig: the DUT at home, AutoPID running and the car answering, the
      bench AP's levers, the receiver on betty, the supply
  d1  configure: a destination to the receiver every 5 s, store and
      forward on, sleep after 1 min; one planned restart
  d2  engine running in the garage: the supply at 14.0 V, the polls grow,
      the POSTs arrive from home with the parameters, the rows reach the
      card; the poll rate measured here is the drive's yardstick
  d3  the drive: `ap off` for --drive-min minutes. With an uplink: a POST
      from the carrier's side within 120 s, then POSTs all the way (no gap
      over 60 s, the parameters in them). Without: no POST at all.
  d3r (uplink + console only) halfway: `espnetlink repair` typed on the
      console (the dongle's power cycled): the POSTs return within 150 s
  d4  home again: `ap on`; the DUT back, a POST from home, MQTT back when
      it was there; no boot since d1; the polls and the recorded rows grew
      through the drive, nothing dropped; the destination's counters tell
      the same story as the receiver
  d5  engine off: the supply at 12.5 V: asleep within the delay
  d6  the next start: 14.0 V: awake, home, the uplink back, the polls
      back; the drive is on the card: the export route returns its rows
      from the first minute to the last
  d7  restore: the settings as found (one planned restart), the supply,
      the AP, the receiver; no reset nobody asked for, no E line of the
      components involved, the heap's floor

  python tools/testbench/wifi/espnetlink_drive_bench.py --dut-ip 10.42.1.41
      [--dut localhost:18087] [--pi rpi001] [--server betty]
      [--console auto|COMx|none] [--psu auto|none] [--drive-min 10]
      [--only d2,d3,...] [--keep-log] [--logdir DIR]

Prints `ESPNL DRIVE PASS`, or `ESPNL DRIVE PARTIAL PASS (...)` with --only
or a leg skipped for a rig reason. About 20 min with the default drive.
Written 2026-11-16.
"""
import argparse
import calendar
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402
from ssh_channel import SshChannel  # noqa: E402

LEGS = ("d0", "d1", "d2", "d3", "d3r", "d4", "d5", "d6", "d7")
OWN_TAGS = ("espnetlink", "wifi_manager", "usb_host_manager", "usb_eth_host",
            "autopid", "data_logger", "data_destinations", "sleep_manager",
            "obd_chip")
RX_PORT = 8199
ROW = "drive_pub"
PERIOD_S = 5
V_RUN = 14.0
V_OFF = 12.5
CRASH = re.compile(r"Guru Meditation|wdt timeout|Task watchdog|abort\(\) was called|"
                   r"assert failed|stack overflow|CORRUPT HEAP|WICAN PARK")
BOOT = re.compile(r"restart_tracker: boot #")
ANSI = re.compile(r"\x1b\[[0-9;]*m")

# the receiver: one line per POST, names and counts only (the fix is among
# the parameters: no value is kept)
RX_SERVER = r'''
import http.server, json, time, sys
LOG = "/tmp/drive_rx.jsonl"
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(n) if n else b""
        rec = {"t": time.time(), "path": self.path,
               "peer": self.client_address[0], "bytes": len(body)}
        try:
            doc = json.loads(body.decode("utf-8", "replace"))
            data = doc.get("autopid_data", doc) if isinstance(doc, dict) else {}
            names = sorted(k for k in data if isinstance(data[k], (int, float)))
            rec["n"] = len(names)
            rec["gps"] = sum(1 for k in names if k.startswith("gps_"))
            rec["full"] = isinstance(doc, dict) and "status" in doc
            rec["names"] = [k for k in names if not k.startswith("gps_")][:12]
        except ValueError:
            rec["n"] = -1
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
    do_PUT = do_POST
    def log_message(self, *a):
        pass
http.server.ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
'''


class Console:
    """The DUT's console beside the run (console_tail.py: DTR and RTS off),
    with a command file to type through."""

    def __init__(self, port, log):
        self.port, self.log, self.proc = port, log, None
        self.stop_file = log + ".stop"
        self.cmd_file = log + ".cmd"

    def start(self):
        if not self.port or self.port == "none":
            return False
        tail = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "lib", "console_tail.py")
        for f in (self.stop_file, self.cmd_file):
            if os.path.exists(f):
                os.remove(f)
        self.proc = subprocess.Popen(
            [sys.executable, tail, self.port, self.log, "--secs", "7200",
             "--stop-file", self.stop_file, "--cmd-file", self.cmd_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        return self.proc.poll() is None

    def lines(self):
        try:
            with open(self.log, encoding="utf-8", errors="replace") as f:
                return [ANSI.sub("", ln) for ln in f.read().splitlines()]
        except OSError:
            return []

    def type(self, line, wait=4.0):
        """Type one line; the console's lines that followed it."""
        mark = len(self.lines())
        with open(self.cmd_file, "w", encoding="utf-8") as f:
            f.write(line + "\n")
        t0 = time.time()
        while time.time() - t0 < 5 and os.path.exists(self.cmd_file):
            time.sleep(0.1)
        time.sleep(wait)
        return self.lines()[mark:]

    def stop(self):
        if self.proc is None:
            return
        try:
            open(self.stop_file, "w").close()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        self.proc = None


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


def side(ip, home_ip):
    """A public address as a word: never the address itself."""
    if not ip:
        return "none"
    return "home" if ip == home_ip else "other"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="")
    ap.add_argument("--dut-ip", default="10.42.1.41")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--server", default="betty")
    ap.add_argument("--console", default="auto")
    ap.add_argument("--psu", default="auto")
    ap.add_argument("--drive-min", type=float, default=10.0)
    ap.add_argument("--only", default="")
    ap.add_argument("--keep-log", action="store_true",
                    help="leave store and forward on and the run's log files on the "
                         "card (a Home Assistant pull follows)")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS if not only or l in only or l in ("d0", "d7")]
    logdir = a.logdir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "test-reports", "logs", time.strftime("drive_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("ESPNL DRIVE")
    skipped = []
    drive_s = a.drive_min * 60.0
    print(f"drive {a.drive_min:g} min, legs {' '.join(legs)}, logs {logdir}",
          flush=True)

    pi_ch = SshChannel(a.pi)
    srv_ch = SshChannel(a.server)

    def pi(cmd, timeout=60):
        return pi_ch.run(cmd, timeout)

    def srv(cmd, timeout=40):
        return srv_ch.run(cmd, timeout)

    def p4(cmd, wait=1.5):
        return pi("cd ~/wican/tools/testbench/pi && timeout 25 "
                  f"python3 bench_ap_cli.py '{cmd}' --wait {wait}")

    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18087"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o",
                                   "ExitOnForwardFailure=yes", "-N", "-L",
                                   f"18087:{a.dut_ip}:80", a.pi])
        time.sleep(3)
        if tunnel.poll() is not None:
            print("no tunnel to the DUT through the Pi (port 18087 busy?)")
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
    have_console = console.start()
    # the probe is a command whose answer carries no position (`espnetlink`
    # prints the fix): the prompt behind the answer is the proof
    if have_console and not any("wican>" in ln for ln in console.type("restart_tracker")):
        print(f"console: {port} opens but does not answer at the prompt: "
              "treated as absent (d3r needs it)", flush=True)
        console.stop()
        have_console = False
    print("console: " + ("on " + port if have_console else "off"), flush=True)

    psu = None
    psu_setpoint = None
    if a.psu != "none":
        try:
            from bench_psu import open_psu  # noqa: E402
            psu = open_psu(a.psu)
            psu_setpoint = psu.voltage_setpoint()
        except (Exception, SystemExit) as e:  # noqa: BLE001
            print("psu: unavailable:", e)
    print("psu: " + (f"on, setpoint {psu_setpoint:.2f} V" if psu
                     else "off (d5 and d6 skip)"), flush=True)

    def rx_records():
        rc, out = srv("cat /tmp/drive_rx.jsonl 2>/dev/null")
        recs = []
        for ln in out.splitlines():
            try:
                recs.append(json.loads(ln))
            except ValueError:
                continue
        return [r for r in recs if r.get("path") == "/pub"]

    def station_home():
        code, w = dut.api("/api/wifi/status")
        return code == 200 and isinstance(w, dict) and \
            w.get("sta_connected") is True and str(w.get("ip", "")).startswith("10.42.")

    def home_up():
        return dut.boot_count() is not None

    def autopid_stats():
        doc = dut.get("/api/autopid")
        return doc.get("stats") or {}

    def dest_row():
        doc = dut.get("/api/destinations")
        for r in doc.get("destinations", []):
            if r.get("name") == ROW:
                return r
        return {}

    def dut_epoch():
        rtc = dut.get("/api/rtc")
        return calendar.timegm(time.strptime(rtc["time"], "%Y-%m-%dT%H:%M:%SZ"))

    def uplink_now():
        """The uplink the unit has beside its station: the wire, the
        dongle's AP, or none."""
        code, n = dut.api("/api/espnetlink")
        code2, u = dut.api("/api/usb")
        n = n if code == 200 and isinstance(n, dict) else {}
        u = u if code2 == 200 and isinstance(u, dict) else {}
        if u.get("eth_connected"):
            return "wire"
        if n.get("enabled") and n.get("paired"):
            return "dongle_ap"
        return "none"

    found = {}
    exit_code = 1
    betty_ip = ""
    home_pub = ""
    host_route_added = False
    rx_started = False
    files_before = []
    uplink = "none"
    rate = 0.0
    clock_off = 0.0             # DUT epoch minus PC epoch
    t_away = t_home = 0.0
    st0 = None
    snap_d2 = {}
    drive_ok = False

    try:
        # ---- d0: the rig ---------------------------------------------------------
        print("== d0 the rig", flush=True)
        info = dut.get("/api/info")
        st0 = dut.get("/api/status")
        dut.as_found()
        print(f"DUT {info.get('device_id')} {info.get('fw_version')} "
              f"{info.get('partition')}", flush=True)
        run.check("d0_station_on_home", station_home())
        for name in ("data_destinations", "data_logger", "sleep_manager", "espnetlink",
                     "usb_host_manager", "autopid"):
            found[name] = dut.settings(name)
        uplink = uplink_now()
        print(f"uplink beside the station: {uplink} (espnetlink.enabled="
              f"{found['espnetlink'].get('enabled')}, usb host="
              f"{found['usb_host_manager'].get('enabled')})", flush=True)
        s = autopid_stats()
        ok0 = s.get("polls_ok", 0)
        time.sleep(10)
        s = autopid_stats()
        run.check("d0_autopid_runs_and_the_car_answers",
                  s.get("running") is True and s.get("polls_ok", 0) > ok0,
                  f"running={s.get('running')} polls_ok {ok0} -> {s.get('polls_ok')} "
                  f"pids={s.get('pids')}")
        rc, out = p4("ap")
        if "ap: on" not in out:
            raise pcbench.Bench("the P4 daemon does not answer `ap` (start it: "
                                "bench_ap_daemon.py on the Pi): " + out.strip()[-80:])
        run.check("d0_p4_daemon_answers", True)
        rc, out = srv("curl -s -m 8 https://api.ipify.org")
        betty_ip = out.strip()
        run.check("d0_server_public_address",
                  bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", betty_ip)))
        rc, out = pi("ip route show default | head -1")
        if "default" not in out:
            pi(f"sudo -n ip route replace {betty_ip}/32 via 192.168.8.1 dev eth0")
            host_route_added = True
        srv("pkill -f 'drive_r[x].py' 2>/dev/null; true")
        srv_ch.put("/tmp/drive_rx.py", RX_SERVER)
        srv(f"rm -f /tmp/drive_rx.jsonl; (setsid nohup python3 /tmp/drive_rx.py "
            f"{RX_PORT} >/tmp/drive_rx.out 2>&1 </dev/null &) ; sleep 1")
        rx_started = True
        rc, out = srv(f"curl -s -m 5 -o /dev/null -w '%{{http_code}}' -X POST "
                      f"-d x http://127.0.0.1:{RX_PORT}/self")
        run.check("d0_receiver_up_on_betty", out.strip() == "200", out.strip())
        code, fl = dut.api("/api/fs/list?path=/sd/logs")
        files_before = sorted(e.get("name", "") for e in (fl.get("entries", [])
                              if code == 200 and isinstance(fl, dict) else []))
        sd = (st0.get("sd") or {}).get("state")
        run.check("d0_sd_card_mounted", sd == "mounted", str(sd))

        # ---- d1: configure -------------------------------------------------------
        print("== d1 configure", flush=True)
        rows = [r for r in found["data_destinations"].get("destinations", [])
                if r.get("name") != ROW]
        rows.append({"name": ROW, "type": "http", "enabled": True,
                     "url": f"http://{betty_ip}:{RX_PORT}/pub", "period_s": PERIOD_S,
                     "auth": "none", "retain": False, "full_first": True})
        changes = {"data_destinations": {"enabled": True, "destinations": rows},
                   "sleep_manager": {"enabled": True, "sleep_delay_min": 1},
                   "autopid": {"enabled": True}}
        dl = found["data_logger"]
        sf_on = bool(dl.get("enabled")) and dl.get("format") in ("jsonl", "csv") \
            and dl.get("autopid_log") != "off"
        if not sf_on:
            changes["data_logger"] = {"enabled": True, "format": "jsonl",
                                      "autopid_log": "all"}
        print("store and forward: " + ("on as found" if sf_on else "switched on for the run"),
              flush=True)
        if psu:
            psu.set_voltage(V_RUN)
        dut.restart(changes)
        dt = wait_until("station on home after the restart", station_home, 90)
        run.check("d1_restart_and_home", dt >= 0, f"{dt:.0f} s")
        st0 = dut.get("/api/status")        # the boots are counted from here
        dut.restarts = 0
        lg = dut.get("/api/logger")
        run.check("d1_logger_records_every_sample",
                  lg.get("enabled") and lg.get("storage_ok")
                  and lg.get("format") in ("jsonl", "csv") and lg.get("autopid_log") != "off",
                  json.dumps({k: lg.get(k) for k in ("enabled", "storage_ok", "format",
                                                      "autopid_log")}))
        clock_off = dut_epoch() - time.time()
        run.check("d1_clock_set", (dut.get("/api/status").get("bits") or {})
                  .get("time_synced") is True, f"DUT minus PC {clock_off:+.0f} s")

        # ---- d2: the engine runs in the garage -----------------------------------
        if "d2" in legs:
            print("== d2 engine running in the garage", flush=True)
            wait_until("the uplink beside the station as found",
                       lambda: uplink_now() == uplink, 120)
            run.check("d2_uplink_as_found", uplink_now() == uplink, uplink_now())
            n0 = len(rx_records())
            dt = wait_until("three POSTs from home",
                            lambda: len(rx_records()) - n0 >= 3, 90)
            recs = rx_records()[n0:]
            peers = [r.get("peer", "") for r in recs]
            home_pub = max(set(peers), key=peers.count) if peers else ""
            run.check("d2_posts_arrive_from_home", dt >= 0 and bool(home_pub),
                      f"{dt:.0f} s, {len(recs)} POSTs")
            s1 = autopid_stats()
            l1 = dut.get("/api/logger")
            t1 = time.time()
            time.sleep(30)
            s2 = autopid_stats()
            l2 = dut.get("/api/logger")
            span = time.time() - t1
            rate = (s2.get("polls_ok", 0) - s1.get("polls_ok", 0)) / span
            failed = s2.get("polls_failed", 0) - s1.get("polls_failed", 0)
            run.check("d2_polls_grow_and_none_fail", rate > 0.5 and failed == 0,
                      f"{rate:.1f} polls/s, failed +{failed}")
            run.metric("d2_poll_rate_per_s", round(rate, 2))
            wrate = (l2.get("written", 0) - l1.get("written", 0)) / span
            run.check("d2_rows_reach_the_card", wrate > 0.5 * rate
                      and l2.get("dropped", 0) == l1.get("dropped", 0),
                      f"{wrate:.1f} rows/s written, dropped {l2.get('dropped')}")
            run.metric("d2_rows_written_per_s", round(wrate, 2))
            last = rx_records()[-1] if rx_records() else {}
            want_n = min(10, int(s2.get("pids", 0)))
            run.check("d2_post_carries_the_parameters", last.get("n", 0) >= want_n,
                      f"{last.get('n')} parameters (want {want_n}), "
                      f"{last.get('gps')} of the fix, {last.get('bytes')} B")
            snap_d2 = {"autopid": s2, "logger": l2, "dest": dest_row(), "t": time.time(),
                       "nl": dut.get("/api/espnetlink"), "want_n": want_n}

        # ---- d3: the drive --------------------------------------------------------
        if "d3" in legs:
            print(f"== d3 the drive ({a.drive_min:g} min, uplink {uplink})", flush=True)
            con_mark = len(console.lines()) if have_console else 0
            n_away = len(rx_records())
            rc, out = p4("ap off")
            run.check("d3_home_ap_off", rc == 0 and "off" in out, out.strip()[-60:])
            t_away = time.time()
            repaired = False
            n_rep = 0
            if uplink != "none":
                dt = wait_until("a POST from the carrier's side",
                                lambda: any(r.get("peer") not in ("", home_pub)
                                            for r in rx_records()[n_away:]), 120)
                run.check("d3_first_post_from_the_road", dt >= 0, f"{dt:.0f} s after ap off")
                run.metric("d3_ap_off_to_first_road_post_s", round(dt, 1) if dt >= 0 else -1)
            while time.time() - t_away < drive_s:
                left = drive_s - (time.time() - t_away)
                time.sleep(min(30, max(1, left)))
                recs = rx_records()[n_away:]
                road = [r for r in recs if r.get("peer") not in ("", home_pub)]
                print(f"  {time.time() - t_away:5.0f} s: {len(recs)} POSTs since ap off, "
                      f"{len(road)} from the road, last {recs[-1].get('n') if recs else '-'} "
                      "parameters", flush=True)
                if ("d3r" in legs and uplink != "none" and have_console and not repaired
                        and time.time() - t_away >= drive_s / 2):
                    repaired = True
                    n_rep = len(rx_records())
                    ans = console.type("espnetlink repair", wait=3)
                    print("  d3r: `espnetlink repair` typed: "
                          + (ans[-1][:100] if ans else "(no answer line)"), flush=True)
            # the road's record at the receiver
            recs = rx_records()[n_away:]
            road = [r for r in recs if r.get("peer") not in ("", home_pub)]
            if uplink != "none":
                ts = [r["t"] for r in road]
                gaps = [b - x for x, b in zip(ts, ts[1:])]
                worst = max(gaps) if gaps else 9999.0
                want = drive_s / (PERIOD_S + 1.0) * 0.6
                gap_limit = 150.0 if repaired else 60.0
                run.check("d3_posts_all_the_way", len(road) >= want and worst <= gap_limit,
                          f"{len(road)} POSTs from the road (want {want:.0f}), longest gap "
                          f"{worst:.0f} s (limit {gap_limit:.0f})")
                run.metric("d3_road_posts", len(road))
                run.metric("d3_longest_gap_s", round(worst, 1))
                thin = [r for r in road if r.get("n", 0) < snap_d2.get("want_n", 1)]
                run.check("d3_every_post_carries_the_parameters",
                          len(thin) <= max(1, len(road) // 20),
                          f"{len(thin)} of {len(road)} with fewer than "
                          f"{snap_d2.get('want_n', 1)} parameters")
                fix = sum(1 for r in road if r.get("gps", 0) >= 2)
                run.metric("d3_posts_with_the_fix", fix)
                print(f"  the fix was in {fix} of {len(road)} POSTs (reported, not "
                      "judged: a cold fix with the wire live is slow at the bench window)",
                      flush=True)
                if repaired:
                    # by position in the receiver's own list: its clock and
                    # the PC's are not compared
                    after = [r for r in rx_records()[n_rep:]
                             if r.get("peer") not in ("", home_pub)]
                    run.check("d3r_posts_return_after_the_repair", len(after) >= 3,
                              f"{len(after)} POSTs behind the repair, longest gap {worst:.0f} s")
                elif "d3r" in legs:
                    skipped.append("d3r (no console)" if not have_console
                                   else "d3r (no uplink)")
            else:
                run.check("d3_nothing_leaves_the_car", len(recs) == 0,
                          f"{len(recs)} POSTs while away")
            if have_console:
                lines = console.lines()[con_mark:]
                crash = [ln for ln in lines if CRASH.search(ln)]
                boots = [ln for ln in lines if BOOT.search(ln)]
                run.check("d3_console_no_crash_no_boot", not crash and not boots,
                          (crash + boots)[0][:120] if (crash or boots) else
                          f"{len(lines)} console lines")
            drive_ok = True

        # ---- d4: home again ---------------------------------------------------------
        if "d4" in legs:
            print("== d4 home again", flush=True)
            n_home = len(rx_records())
            rc, out = p4("ap on")
            t_home = time.time()
            dt = wait_until("the DUT over the bench AP", home_up, 180)
            run.check("d4_home_within_180s", dt >= 0, f"{dt:.0f} s")
            run.metric("d4_ap_on_to_home_s", round(dt, 1) if dt >= 0 else -1)
            dt = wait_until("a POST from home",
                            lambda: any(r.get("peer") == home_pub
                                        for r in rx_records()[n_home:]), 90)
            run.check("d4_post_from_home", dt >= 0 and bool(home_pub), f"{dt:.0f} s")
            if (snap_d2.get("dest") or {}) and dut.get("/api/destinations").get("mqtt") is not None:
                was = (st0.get("bits") or {}).get("mqtt_connected")
                if was:
                    dt = wait_until("MQTT connected again", lambda: (dut.get("/api/status")
                                    .get("bits") or {}).get("mqtt_connected") is True, 60)
                    run.check("d4_mqtt_back", dt >= 0, f"{dt:.0f} s")
            st = dut.get("/api/status")
            run.check("d4_no_boot_since_the_garage",
                      st.get("boot_count") == st0.get("boot_count")
                      and st.get("unexpected_resets") == st0.get("unexpected_resets"),
                      f"boot_count {st0.get('boot_count')} -> {st.get('boot_count')}, "
                      f"unexpected {st.get('unexpected_resets')}")
            if drive_ok and snap_d2:
                s3 = autopid_stats()
                l3 = dut.get("/api/logger")
                d3 = dest_row()
                span = time.time() - snap_d2["t"]
                polls = s3.get("polls_ok", 0) - snap_d2["autopid"].get("polls_ok", 0)
                failed = s3.get("polls_failed", 0) - snap_d2["autopid"].get("polls_failed", 0)
                run.check("d4_polls_ran_through_the_drive",
                          polls >= 0.8 * rate * span and failed <= max(2, 0.02 * polls),
                          f"+{polls} in {span:.0f} s ({polls / span:.1f}/s, garage "
                          f"{rate:.1f}/s), failed +{failed}")
                rows_w = l3.get("written", 0) - snap_d2["logger"].get("written", 0)
                run.check("d4_drive_recorded_nothing_dropped",
                          rows_w >= 0.8 * polls
                          and l3.get("dropped", 0) == snap_d2["logger"].get("dropped", 0)
                          and l3.get("errors", 0) == snap_d2["logger"].get("errors", 0),
                          f"+{rows_w} rows written, dropped {l3.get('dropped')}, "
                          f"errors {l3.get('errors')}, file {l3.get('file')}")
                d2 = snap_d2["dest"]
                ok_d = d3.get("success", 0) - d2.get("success", 0)
                fail_d = d3.get("fail", 0) - d2.get("fail", 0)
                off_d = d3.get("skipped_offline", 0) - d2.get("skipped_offline", 0)
                if uplink != "none":
                    run.check("d4_destination_delivered_on_the_road",
                              ok_d >= 0.6 * span / (PERIOD_S + 1.0)
                              and fail_d <= max(3, 0.1 * ok_d),
                              f"success +{ok_d}, fail +{fail_d}, skipped offline +{off_d}, "
                              f"last_error '{d3.get('last_error')}'")
                else:
                    run.check("d4_destination_waited_offline_without_failures",
                              off_d > 0 and fail_d <= 2,
                              f"success +{ok_d}, fail +{fail_d}, skipped offline +{off_d}")
                nl = dut.get("/api/espnetlink")
                u0 = (snap_d2["nl"].get("usb") or {})
                u1 = (nl.get("usb") or {})
                print(f"  espnetlink: polls {snap_d2['nl'].get('polls')} -> {nl.get('polls')}, "
                      f"failures {snap_d2['nl'].get('failures')} -> {nl.get('failures')}, "
                      f"vbus_cycles {u0.get('vbus_cycles')} -> {u1.get('vbus_cycles')}, "
                      f"errors {u0.get('errors')} -> {u1.get('errors')}, uplink "
                      f"{nl.get('uplink')}, last_error '{nl.get('last_error')}'", flush=True)

        # ---- d5: engine off ---------------------------------------------------------
        slept = False
        if "d5" in legs:
            print("== d5 engine off", flush=True)
            if not psu:
                skipped.append("d5 (no supply)")
            else:
                samples = psu.sample_current(4)
                awake_a = sum(samples) / max(1, len(samples))
                psu.set_voltage(V_OFF)
                t0 = time.time()
                asleep = psu.wait_current(lambda i: i < awake_a * 0.5, timeout_s=240)
                run.check("d5_asleep_after_the_engine_stops", asleep is not None,
                          f"awake {awake_a * 1000:.0f} mA -> {(asleep or 0) * 1000:.0f} mA "
                          f"after {time.time() - t0:.0f} s")
                run.metric("d5_sleep_entry_s", round(time.time() - t0, 1))
                slept = asleep is not None
                if slept:
                    time.sleep(20)
                    settled = sorted(psu.sample_current(6))
                    med = settled[len(settled) // 2] if settled else 0
                    run.metric("d5_asleep_ma", round(med * 1000, 1))
                    print(f"  asleep at {med * 1000:.1f} mA (the dongle's rail is off with "
                          "the unit)", flush=True)

        # ---- d6: the next start -----------------------------------------------------
        if "d6" in legs:
            print("== d6 the next start", flush=True)
            if not psu:
                skipped.append("d6 (no supply)")
            else:
                psu.set_voltage(V_RUN)
                t1 = time.time()
                dt = wait_until("the DUT over the bench AP after the wake", home_up, 150)
                if slept:
                    dut.restarts += 1
                run.check("d6_awake_and_home", dt >= 0, f"{dt:.0f} s")
                run.metric("d6_wake_to_home_s", round(time.time() - t1, 1))
                dt = wait_until("the uplink beside the station back",
                                lambda: uplink_now() == uplink, 180)
                run.check("d6_uplink_back_as_before", dt >= 0, f"{uplink}: {dt:.0f} s")
                s = autopid_stats()
                ok1 = s.get("polls_ok", 0)
                time.sleep(15)
                s = autopid_stats()
                run.check("d6_polls_back", s.get("running") is True
                          and s.get("polls_ok", 0) > ok1,
                          f"polls_ok {ok1} -> {s.get('polls_ok')}")
                if slept:
                    code, h = dut.api("/api/restart/history", timeout=15)
                    recs = h.get("records", []) if code == 200 and isinstance(h, dict) else []
                    newest = max(recs, key=lambda r: r.get("seq", 0)) if recs else {}
                    run.check("d6_the_wake_is_a_planned_boot", bool(newest.get("planned")),
                              pcbench.reset_text(newest)[:120] if newest else "no record")
            # the drive on the card, read the way a consumer reads it
            if drive_ok and t_away and t_home:
                lo = int((t_away + clock_off) * 1000)
                hi = int((t_home + clock_off) * 1000)
                cursor = str(int(t_away + clock_off) - 60)
                n_rows = 0
                names = set()
                first = last = None
                pages = 0
                more = True
                while more and pages < 200:
                    # the cursor goes out as it came, its colon bare: the
                    # route reads `since` without percent-decoding it, and a
                    # cursor sent as `<epoch>%3A<offset>` is taken for a plain
                    # time, which starts at the file's first row again (the
                    # first run of this bench read one page 60 times,
                    # 2026-11-16; the web UI and the Home Assistant
                    # integration send the bare colon too)
                    code, text = dut.api("/api/logger/export?stream=params&limit=4000&since="
                                         + cursor, timeout=40)
                    pages += 1
                    if code != 200:
                        print(f"  export: http {code} {str(text)[:100]}", flush=True)
                        break
                    more = False
                    body = text if isinstance(text, str) else json.dumps(text)
                    for ln in body.splitlines():
                        try:
                            row = json.loads(ln)
                        except ValueError:
                            continue
                        if "_cursor" in row:
                            cursor = row["_cursor"]
                            more = bool(row.get("more"))
                            continue
                        ts = row.get("ts")
                        if not isinstance(ts, (int, float)) or not lo <= ts <= hi:
                            continue
                        n_rows += 1
                        names.add(str(row.get("param", "")))
                        first = ts if first is None else min(first, ts)
                        last = ts if last is None else max(last, ts)
                span = (hi - lo) / 1000.0
                head = (first - lo) / 1000.0 if first is not None else -1
                tail = (hi - last) / 1000.0 if last is not None else -1
                run.check("d6_the_drive_is_on_the_card",
                          n_rows >= 0.7 * rate * span and len(names) >= snap_d2.get("want_n", 1)
                          and 0 <= head <= 30 and 0 <= tail <= 30,
                          f"{n_rows} rows of {len(names)} parameters over {span:.0f} s in "
                          f"{pages} page(s); first {head:.0f} s after leaving, last "
                          f"{tail:.0f} s before home")
                run.metric("d6_drive_rows_on_the_card", n_rows)

        # ---- closing ---------------------------------------------------------------
        wait_until("the DUT home for the closing checks", home_up, 120)
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        if have_console:
            crash = [ln for ln in console.lines() if CRASH.search(ln)]
            run.check("console_no_crash_line", not crash, crash[0][:120] if crash else "")
        exit_code = 0
    except pcbench.Bench as e:
        run.check("rig", False, str(e))
    finally:
        # ---- d7: as found ----------------------------------------------------------
        try:
            p4("ap on")
            if psu:
                try:
                    psu.set_voltage(psu_setpoint)
                except Exception:  # noqa: BLE001
                    pass
            wait_until("the DUT home for the restore", home_up, 240)
            if rx_started:
                srv("pkill -f 'drive_r[x].py' 2>/dev/null; true")
                srv(f"cp /tmp/drive_rx.jsonl /tmp/drive_rx.last 2>/dev/null; "
                    "rm -f /tmp/drive_rx.py /tmp/drive_rx.jsonl /tmp/drive_rx.out; true")
            back = {}
            for name in ("data_destinations", "sleep_manager", "autopid", "data_logger"):
                if name not in found:
                    continue
                if name == "data_logger" and a.keep_log:
                    continue
                back[name] = dict(found[name])
            if back:
                try:
                    dut.restart(back)
                    wait_until("station on home after the restore", station_home, 90)
                except pcbench.Bench as e:
                    print("restore:", e, flush=True)
            if not a.keep_log:
                gone = 0
                code, fl = dut.api("/api/fs/list?path=/sd/logs")
                for e in (fl.get("entries", []) if code == 200 and isinstance(fl, dict) else []):
                    name = e.get("name", "")
                    if name.startswith("dl_") and name not in files_before and not e.get("dir"):
                        code, _ = dut.api("/api/fs/file?path="
                                          + urllib.parse.quote("/sd/logs/" + name), "DELETE")
                        gone += code in (200, 204)
                print(f"  {gone} log file(s) of this run deleted", flush=True)
            if host_route_added and betty_ip:
                pi(f"sudo -n ip route del {betty_ip}/32 2>/dev/null; true")
        finally:
            if psu:
                try:
                    psu.close()
                except Exception:  # noqa: BLE001
                    pass
            console.stop()
            pi_ch.close()
            srv_ch.close()
            if tunnel:
                tunnel.terminate()
    partial = ""
    if only or skipped:
        partial = "legs: " + " ".join(l for l in legs if l != "d7")
        if skipped:
            partial += "; skipped " + ", ".join(skipped)
    code = run.verdict(partial)
    return code if exit_code == 0 else max(code, 1)


if __name__ == "__main__":
    sys.exit(main())
