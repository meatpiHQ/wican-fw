#!/usr/bin/env python3
"""HA webhook poster with autopid DISABLED - the fresh-device regression.

Root cause found 2026-09-08: a fresh WiCAN ships `autopid.enabled=false`
and the ha_webhooks poster skipped every lap while that bit was clear, so
HA's discovery push got `201 Created` and not one telemetry push ever
left the device (GET /api/webhook stayed status "disabled", 0/0). This
bench replays exactly that: autopid OFF (settings PUT + submit = one
planned reboot), register a receiver, expect status-only pushes.

Steps / checks:
  1. autopid.enabled -> false (skipped when already off), DUT back, bit clear
  2. POST /api/webhook {url, enabled, interval 5} -> 201/200
  3. within OBSERVE s: >= 2 posts at the receiver; the first carries
     status.device_id == /api/info device_id and NO autopid_data;
     GET /api/webhook shows status "ok" + success_count >= 1
  4. restore autopid to what it was (one more planned reboot when it was on),
     DELETE /api/webhook -> 204

Receiver (a tiny HTTP server that stores every POST, GET /__dump lists them)
runs where BOTH the DUT and this PC can reach it:
  --serve PORT --local-ip IP   in this process (e.g. the PC hotspot
                               192.168.137.1 when the bench Pi is down)
  --pi HOST                    on the bench Pi over a held-open ssh
                               (the ha_webhook_bench.py pattern), the DUT
                               posts to the Pi's AP gateway --pi-ip
  --receiver URL               anything already running

  python ha_webhook_gate_bench.py --dut 10.42.1.194 --pi rpi001
  python ha_webhook_gate_bench.py --dut 192.168.137.16 --serve 8199 --local-ip 192.168.137.1

Verdict line: HA WEBHOOK GATE PASS / FAIL.
"""
import argparse
import http.server
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


# ---- DUT API ----------------------------------------------------------------

# --dut takes a comma-separated candidate list: after a reboot leg the DUT may
# re-join either Pi hotspot twin (10.42.1.194 / 10.42.0.194, both broadcast
# WICAN_TEST_AP - the usb_eth_bench.py precedent), so every call tries the
# candidates in turn and pins whichever answers.
DUTS = []
DUT = None


def api(path, method="GET", body=None, timeout=10, retries=3):
    global DUT
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for _ in range(retries):
        order = [DUT] + [d for d in DUTS if d != DUT] if DUT else list(DUTS)
        for cand in order:
            req = urllib.request.Request("http://" + cand + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    t = r.read().decode()
                    if cand != DUT:
                        print(f"  DUT answers at {cand}")
                        DUT = cand
                    return r.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
            except urllib.error.HTTPError as e:
                DUT = cand
                return e.code, e.read().decode()
            except (urllib.error.URLError, OSError) as e:
                last = e
        time.sleep(2)
    raise last


def wait_up(deadline_s=150):
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        try:
            code, _ = api("/api/info", timeout=4, retries=1)
            if code == 200:
                return time.time() - t0
        except Exception:
            pass
        time.sleep(2)
    return None


def set_autopid_enabled(want):
    """PUT the full autopid document with `enabled` changed + submit (reboot)."""
    code, ap = api("/api/settings/autopid")
    if code != 200 or ap.get("enabled") == want:
        return code == 200
    doc = {k: v for k, v in ap.items() if k not in ("degraded", "pending_reboot")}
    doc["enabled"] = want
    code, _ = api("/api/settings/autopid", "PUT", doc)
    if code != 200:
        return False
    code, staged = api("/api/settings/autopid")
    if staged.get("enabled") != want:
        return False
    api("/api/settings/submit", "POST", {})
    time.sleep(8)
    up = wait_up()
    print(f"  DUT back after {up:.0f} s" if up else "  DUT did NOT come back")
    if up is None:
        return False
    time.sleep(3)
    return True


# ---- receiver ------------------------------------------------------------------

RECEIVER_SRC = r'''
import http.server, json, sys, time
PORT = int(sys.argv[1])
posts = []
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        posts.append({"t": time.strftime("%H:%M:%S"), "path": self.path,
                      "body": self.rfile.read(n).decode("utf-8", "replace")})
        self.send_response(204); self.end_headers()
    def do_GET(self):
        payload = json.dumps(posts).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *a): pass
http.server.HTTPServer(("0.0.0.0", PORT), H).serve_forever()
'''

_posts = []


class _LocalRecv(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _posts.append({"t": time.strftime("%H:%M:%S"), "path": self.path,
                       "body": self.rfile.read(n).decode("utf-8", "replace")})
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        payload = json.dumps(_posts).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


_pi_proc = None


def start_receiver(args):
    """Return (base_url_for_the_DUT, dump_url_for_this_PC)."""
    global _pi_proc
    if args.receiver:
        return args.receiver.rstrip("/"), args.receiver.rstrip("/") + "/__dump"
    if args.serve:
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", args.serve), _LocalRecv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://{args.local_ip}:{args.serve}"
        return base, f"http://127.0.0.1:{args.serve}/__dump"
    if args.pi:
        ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", args.pi]
        subprocess.run(ssh + [f"cat > /tmp/ha_gate_recv.py <<'EOF'\n{RECEIVER_SRC}\nEOF"],
                       capture_output=True, text=True, timeout=30)
        subprocess.run(ssh + ["pkill -f '[h]a_gate_recv.py' ; true"],
                       capture_output=True, text=True, timeout=20)
        _pi_proc = subprocess.Popen(ssh + [f"python3 -u /tmp/ha_gate_recv.py {args.port}"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # the dump is pulled over plain HTTP from the PC (ssh flakiness must
        # not read as "0 posts"); resolve the Pi's address for that
        out = subprocess.run(["ssh", "-G", args.pi], capture_output=True, text=True,
                             timeout=10).stdout
        pi_ip = args.pi
        for line in out.splitlines():
            if line.startswith("hostname "):
                pi_ip = line.split()[1]
        for _ in range(10):
            try:
                with socket.create_connection((pi_ip, args.port), timeout=3):
                    break
            except OSError:
                time.sleep(1)
        return f"http://{args.pi_ip}:{args.port}", f"http://{pi_ip}:{args.port}/__dump"
    raise SystemExit("need --serve, --pi or --receiver")


def stop_receiver(args):
    global _pi_proc
    if _pi_proc is not None:
        _pi_proc.terminate()
        try:
            _pi_proc.wait(timeout=5)
        except Exception:
            _pi_proc.kill()
        _pi_proc = None
        subprocess.run(["ssh", "-o", "BatchMode=yes", args.pi,
                        "pkill -f '[h]a_gate_recv.py' ; true"],
                       capture_output=True, text=True, timeout=20)


def received(dump_url):
    try:
        with urllib.request.urlopen(dump_url, timeout=6) as r:
            arr = json.loads(r.read().decode())
    except Exception:
        return []
    out = []
    for p in arr:
        body = p.get("body", "").strip()
        if body.startswith("{"):
            try:
                out.append(json.loads(body))
            except Exception:
                pass
    return out


# ---- main ------------------------------------------------------------------------

def main():
    global DUT, DUTS
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dut", required=True,
                    help="DUT address(es) reachable from here, comma-separated candidates "
                         "(e.g. 10.42.1.194,10.42.0.194 - the DUT may re-join either "
                         "hotspot twin after a reboot leg)")
    ap.add_argument("--serve", type=int, help="run the receiver here on this port")
    ap.add_argument("--local-ip", help="this PC's address as the DUT sees it (with --serve)")
    ap.add_argument("--pi", help="run the receiver on this ssh host")
    ap.add_argument("--pi-ip", default="10.42.1.1", help="the Pi's address as the DUT sees it")
    ap.add_argument("--port", type=int, default=8199, help="receiver port with --pi")
    ap.add_argument("--receiver", help="base URL of an already running receiver")
    ap.add_argument("--observe", type=int, default=40, help="seconds to wait for pushes")
    args = ap.parse_args()
    if args.serve and not args.local_ip:
        ap.error("--serve needs --local-ip")
    DUTS = [d.strip() for d in args.dut.split(",") if d.strip()]
    DUT = DUTS[0]

    code, info = api("/api/info")
    check("[RIG] DUT answers /api/info", code == 200, str(info)[:80])
    if code != 200:
        print("HA WEBHOOK GATE FAIL: rig")
        sys.exit(1)
    device_id = info.get("device_id")
    print(f"DUT {device_id} fw {info.get('fw_version')}")

    code, ap_before = api("/api/settings/autopid")
    was_on = bool(ap_before.get("enabled"))
    print("autopid was", "on" if was_on else "off")
    # the registration below persists interval 5 s (DELETE keeps it) - remember
    # the found value so the DUT is left as found
    code, hw_before = api("/api/settings/ha_webhooks")
    interval_before = hw_before.get("interval_s") if code == 200 else None

    base, dump = start_receiver(args)
    print("receiver:", base, "dump:", dump)
    try:
        # 1) autopid off
        check("autopid.enabled -> false (submit + reboot)", set_autopid_enabled(False))
        code, st = api("/api/status")
        bits = st.get("bits", {})
        check("status bit autopid_enabled clear", bits.get("autopid_enabled") is False,
              str(bits.get("autopid_enabled")))
        check("STA connected", bits.get("sta_connected") is True)

        # 2) register
        n0 = len(received(dump))
        url = base + "/hook"
        code, resp = api("/api/webhook", "POST", {"url": url, "enabled": True, "interval": 5})
        check("POST /api/webhook accepted (201/200)", code in (200, 201), f"code={code}")

        # 3) observe
        got = []
        t0 = time.time()
        while time.time() - t0 < args.observe:
            time.sleep(5)
            got = received(dump)[n0:]
            if len(got) >= 2:
                break
        code, wh = api("/api/webhook")
        check("poster pushed with autopid OFF (>= 2 posts)", len(got) >= 2,
              f"{len(got)} posts in {time.time() - t0:.0f} s; device stats status={wh.get('status')} "
              f"success={wh.get('success_count')} fail={wh.get('fail_count')} "
              f"last_error={wh.get('last_error')!r}")
        if got:
            p = got[0]
            st = p.get("status") or {}
            check("first push carries status.device_id == /api/info",
                  st.get("device_id") == device_id, str(st.get("device_id")))
            check("first push carries fw_version + hw_version",
                  bool(st.get("fw_version")) and bool(st.get("hw_version")))
            check("no autopid_data while autopid is off",
                  not p.get("autopid_data"), ",".join(p.keys()))
            check("schema present", p.get("schema") == 1, str(p.get("schema")))
        check("GET /api/webhook status=ok + success_count>=1",
              wh.get("status") == "ok" and wh.get("success_count", 0) >= 1,
              f"status={wh.get('status')} n={wh.get('success_count')}")
    except Exception as e:  # noqa: BLE001 - the verdict line must still print
        check(f"bench step raised {type(e).__name__}", False, str(e)[:120])
    finally:
        # 4) restore: clear the webhook first (the poster must not keep posting
        # to a receiver that is about to vanish), then autopid back. A DUT that
        # is unreachable here must not turn the FAIL into a traceback.
        try:
            code, _ = api("/api/webhook", "DELETE")
            check("DELETE /api/webhook 204", code == 204, f"code={code}")
            need_submit = False
            code, hw = api("/api/settings/ha_webhooks")
            if interval_before and code == 200 and hw.get("interval_s") != interval_before:
                doc = {k: v for k, v in hw.items() if k not in ("degraded", "pending_reboot")}
                doc["interval_s"] = interval_before
                code, _ = api("/api/settings/ha_webhooks", "PUT", doc)
                check(f"ha_webhooks.interval_s staged back to {interval_before}",
                      code == 200, f"code={code}")
                need_submit = code == 200
            if was_on:
                # its submit also applies the staged interval
                check("autopid restored to enabled (submit + reboot)",
                      set_autopid_enabled(True))
            elif need_submit:
                api("/api/settings/submit", "POST", {})
                time.sleep(8)
                up = wait_up()
                check("interval restore applied (submit + reboot)", up is not None)
        except Exception as e:  # noqa: BLE001
            check("restore (DELETE webhook / autopid back)", False,
                  f"{type(e).__name__}: {str(e)[:100]} - restore by hand")
        stop_receiver(args)

    if fails:
        print("HA WEBHOOK GATE FAIL:", ", ".join(fails))
        sys.exit(1)
    print("HA WEBHOOK GATE PASS")


if __name__ == "__main__":
    main()
