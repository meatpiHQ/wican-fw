#!/usr/bin/env python3
"""The repair hammer: `POST /api/espnetlink/repair` (a VBUS cycle of the
ESPNetLink) again and again while the wire carries traffic, and the WiCAN
must never reset. Written 2026-10-12 for the crash the garage bench's g6
leg found (`abort: assert failed: spinlock_acquire ... task
"usbh_rndis_rx"`: the class rx thread's control transfer against the hub
releasing the port, fixed in the vendored net class drivers).

The DUT sits at home on the bench AP (the ssh tunnel to the Pi) in a USB
mode, and `usb_host_manager.prefer_usb_route` is set for the run so the
default route is the wire: a data destination row to a receiver on betty
(`--server betty`, the receiver bound on 0.0.0.0:8199 on betty; the Pi
gets a host route to betty) carries one POST every 5 s over LTE. Each
cycle: the repair, the wire gone (`pair_state` leaves `ncm_up`), the wire
back (`ncm_up` again within 150 s), the receiver's count grown since the
previous cycle, `boot_count` and `unexpected_resets` unchanged. The run
stops at the first reset (three quick crashes would park the device) and
prints the crash note the device filed. Everything is put back at the end.

  python tools/testbench/usb/espnetlink_repair_hammer.py [--n 8]
      [--spacing 20] [--dut localhost:18081] [--dut-ip 10.42.1.194]
      [--pi rpi001] [--server betty|none] [--logdir DIR]

Prints `ESPNL REPAIR HAMMER PASS` (or FAIL with the first failed check).
Each cycle waits for the dongle's LTE to re-attach before it expects the
POSTs again (30 to 100 s after a VBUS cycle), so a run of 8 takes about
15 min with the receiver, 3 min without (`--server none`: the repairs and
the resets only).
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

RX_PORT = 8199
OWN_TAGS = ("espnetlink", "usb_host_manager", "usb_eth_host")
RX_SERVER = r'''
import http.server, json, time, sys
LOG = "/tmp/hammer_rx.jsonl"
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


def wait_until(what, pred, budget, poll=2):
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
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--spacing", type=float, default=20.0)
    ap.add_argument("--dut", default="")
    ap.add_argument("--dut-ip", default="10.42.1.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--server", default="betty")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    run = pcbench.Run("ESPNL REPAIR HAMMER")
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
    use_rx = a.server not in ("", "none")
    pi_ch = SshChannel(a.pi)                   # one session per host per run
    srv_ch = SshChannel(a.server) if use_rx else None

    def pi(cmd, timeout=60):
        return pi_ch.run(cmd, timeout)

    def srv(cmd, timeout=40):
        return srv_ch.run(cmd, timeout)

    def rx_count():
        rc, out = srv("wc -l < /tmp/hammer_rx.jsonl 2>/dev/null || echo 0")
        try:
            return int(out.strip().split()[-1])
        except (ValueError, IndexError):
            return 0

    def st():
        return dut.get("/api/status")

    def nl():
        return dut.get("/api/espnetlink")

    found = {}
    betty_ip = ""
    route_added = False
    rx_started = False
    exit_code = 1
    try:
        info = dut.get("/api/info")
        st0 = st()
        dut.as_found()
        n0 = nl()
        print(f"DUT {info.get('device_id')} {info.get('fw_version')} mode "
              f"{n0.get('mode')} pair_state {(n0.get('usb') or {}).get('pair_state')} "
              f"boot {st0.get('boot_count')} unexpected {st0.get('unexpected_resets')}",
              flush=True)
        if (n0.get("usb") or {}).get("pair_state") != "ncm_up":
            raise pcbench.Bench("the wire is not up (pair_state != ncm_up): "
                                "the hammer needs a USB mode with the dongle paired")
        for name in ("usb_host_manager", "data_destinations"):
            found[name] = dut.settings(name)
        changes = {"usb_host_manager": {"prefer_usb_route": True}}
        if use_rx:
            rc, out = srv("curl -s -m 8 https://api.ipify.org")
            betty_ip = out.strip()
            run.check("server_public_address",
                      bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", betty_ip)))
            pi(f"sudo -n ip route replace {betty_ip}/32 via 192.168.8.1 dev eth0")
            route_added = True
            # the kill runs in a command of its own: `pkill -f` matches any
            # shell whose command line carries the file name, this one's
            # included when the start sits in the same command
            srv("pkill -f 'hammer_r[x].py' 2>/dev/null; true")
            srv_ch.put("/tmp/hammer_rx.py", RX_SERVER)
            srv(f"rm -f /tmp/hammer_rx.jsonl; (setsid nohup python3 /tmp/hammer_rx.py "
                f"{RX_PORT} >/tmp/hammer_rx.out 2>&1 </dev/null &) ; sleep 1")
            rx_started = True
            rc, out = srv(f"curl -s -m 5 -o /dev/null -w '%{{http_code}}' -X POST "
                          f"-d x http://127.0.0.1:{RX_PORT}/self")
            run.check("receiver_up_on_betty", out.strip() == "200", out.strip())
            rows = [r for r in found["data_destinations"].get("destinations", [])
                    if r.get("name") != "hammer_pub"]
            rows.append({"name": "hammer_pub", "type": "http", "enabled": True,
                         "url": f"http://{betty_ip}:{RX_PORT}/pub", "period_s": 5,
                         "auth": "none", "retain": False, "full_first": True})
            changes["data_destinations"] = {"enabled": True, "destinations": rows}
        dut.restart(changes)
        dt = wait_until("the wire up after the restart",
                        lambda: (nl().get("usb") or {}).get("pair_state") == "ncm_up", 120)
        run.check("wire_up_after_the_restart", dt >= 0, f"{dt:.0f} s")
        if use_rx:
            c0 = rx_count()
            dt = wait_until("POSTs arriving over the wire", lambda: rx_count() > c0 + 1, 60)
            run.check("traffic_on_the_wire", dt >= 0, f"{dt:.0f} s")
        base = st()
        b0, u0 = base.get("boot_count"), base.get("unexpected_resets")
        times = []
        for i in range(1, a.n + 1):
            before = nl()
            cyc0 = (before.get("usb") or {}).get("vbus_cycles") or 0
            c_before = rx_count() if use_rx else 0
            code, r = dut.api("/api/espnetlink/repair", "POST", {})
            ok = code == 200
            t0 = time.time()
            gone = wait_until(f"cycle {i}: the wire gone",
                              lambda: (nl().get("usb") or {}).get("pair_state") != "ncm_up",
                              30, poll=1)
            back = wait_until(f"cycle {i}: the wire back",
                              lambda: (nl().get("usb") or {}).get("pair_state") == "ncm_up",
                              150, poll=2)
            s = st()
            after = nl()
            cyc1 = (after.get("usb") or {}).get("vbus_cycles") or 0
            reset = s.get("boot_count") != b0 or s.get("unexpected_resets") != u0
            times.append(round(time.time() - t0, 1))
            detail = (f"HTTP {code}, gone {gone:.0f} s, back {back:.0f} s, vbus_cycles "
                      f"{cyc0} -> {cyc1}, boot {s.get('boot_count')} unexpected "
                      f"{s.get('unexpected_resets')}")
            run.check(f"cycle_{i}_repair_without_a_reset",
                      ok and gone >= 0 and back >= 0 and not reset and cyc1 > cyc0, detail)
            if reset:
                code, h = dut.api("/api/restart/history", timeout=15)
                recs = h.get("records", []) if isinstance(h, dict) else []
                for rec in recs[:1]:
                    print("  the device says: " + pcbench.reset_text(rec), flush=True)
                print("  stopping: a reset (three quick crashes park the device)",
                      flush=True)
                break
            if use_rx:
                # the dongle rebooted with the VBUS cycle: its LTE needs 30 to
                # 100 s to re-attach, and the POSTs leave over the wire (the
                # route is pinned there for the run), so the traffic check
                # waits for the dongle's link first; a spacing shorter than
                # that recovery starves the row (the first run, 20 s spacing)
                dt_lte = wait_until(f"cycle {i}: the dongle's LTE back",
                                    lambda: (nl().get("dongle") or {}).get("stage")
                                    == "connected", 150, poll=5)
                dt = wait_until(f"cycle {i}: POSTs flowing again",
                                lambda: rx_count() > c_before + 1, 90)
                run.check(f"cycle_{i}_traffic_resumes", dt >= 0,
                          f"LTE back {dt_lte:.0f} s, POSTs {dt:.0f} s later")
            time.sleep(a.spacing)
        run.metric("repair_cycle_max_s", max(times) if times else -1)
        run.metric("repairs_done", len(times))
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        exit_code = 0
    except pcbench.Bench as e:
        run.check("rig", False, str(e))
    finally:
        try:
            wait_until("the DUT for the restore", lambda: dut.boot_count() is not None, 120)
            back = {k: dict(v) for k, v in found.items()}
            if back:
                try:
                    dut.restart(back)
                except pcbench.Bench as e:
                    print("restore:", e, flush=True)
            if rx_started:
                srv("pkill -f 'hammer_r[x].py' 2>/dev/null; true")
                srv("rm -f /tmp/hammer_rx.py /tmp/hammer_rx.jsonl /tmp/hammer_rx.out; true")
            if route_added and betty_ip:
                pi(f"sudo -n ip route del {betty_ip}/32 2>/dev/null; true")
        finally:
            pi_ch.close()
            if srv_ch:
                srv_ch.close()
            if tunnel:
                tunnel.terminate()
    code = run.verdict()
    return code if exit_code == 0 else max(code, 1)


if __name__ == "__main__":
    sys.exit(main())
