#!/usr/bin/env python3
"""ha_webhooks end-to-end bench (PC orchestrator + a mock HA receiver on
the Pi). Verifies the v6 HA integration port:

  * POST /api/webhook registers a URL (201/200), GET reflects it, the
    poster starts posting {status, autopid_data, config} at the interval;
  * the status section carries device_id (HA identity binding);
  * failover: primary dead + secondary live -> telemetry still arrives;
  * GET stats reflect success; DELETE clears.

Topology: the DUT posts over its STA link to the Pi's AP gateway
(10.42.0.1) where a mock receiver runs; the PC drives the DUT over the
USB-NCM link (192.168.82.1) and the Pi over ssh.

  python ha_webhook_bench.py [usb_ip] [bench_host] [pi_ap_ip]
"""
import json
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

USB = sys.argv[1] if len(sys.argv) > 1 else "192.168.82.1"
BENCH = sys.argv[2] if len(sys.argv) > 2 else "rpi001"
PI_AP = sys.argv[3] if len(sys.argv) > 3 else "10.42.0.1"
PORT = 8199
DEAD_PORT = 8231  # nothing listens here — the failover primary
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

fails = []
_bench_ip = None


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def api(path, method="GET", body=None, retries=4):
    # tolerate transient connection drops (e.g. a live-config apply); a real
    # HTTP response (HTTPError) is returned, not retried.
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for _ in range(retries):
        req = urllib.request.Request("http://" + USB + path, data=data,
                                     method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                t = r.read().decode()
                return r.status, (json.loads(t) if t.strip().startswith(("{", "["))
                                  else t)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(2)
    raise last


def pi(cmd, timeout=30):
    # Windows OpenSSH has no ControlMaster; each call is a cold handshake and
    # one occasionally spikes past the timeout — retry once before giving up.
    last = ""
    for _ in range(2):
        try:
            p = subprocess.run(["ssh", *SSH_OPTS, BENCH, cmd],
                               capture_output=True, text=True, timeout=timeout)
            return p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            last = f"ssh timeout after {timeout}s: {cmd}"
    return last


def bench_ip():
    global _bench_ip
    if _bench_ip is None:
        out = subprocess.run(["ssh", "-G", BENCH],
                             capture_output=True, text=True, timeout=10).stdout
        _bench_ip = BENCH
        for line in out.splitlines():
            if line.startswith("hostname "):
                _bench_ip = line.split()[1]
                break
    return _bench_ip


def port_open(ip, port, timeout=3):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


# The receiver captures each webhook POST in-memory and hands the whole
# list back on GET /__dump. The PC pulls it over PLAIN HTTP (the server
# binds 0.0.0.0, reachable at the bench IP) — NOT ssh — so a flaky ssh
# link can't turn "delivered" into "0 posts" (burned 2026-07-09).
RECEIVER = r'''
import http.server, json, sys
PORT = int(sys.argv[1])
posts = []
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        posts.append(self.rfile.read(n).decode("utf-8", "replace"))
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
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


_recv_proc = None


def start_receiver(port, outfile):
    """Run the receiver in the FOREGROUND of a backgrounded ssh the PC
    holds open — the only reliable way to keep a remote process alive for
    the test (setsid/nohup/disown all die with the one-shot ssh channel)."""
    global _recv_proc
    stop_receiver()
    pi(f"cat > /tmp/ha_recv.py <<'EOF'\n{RECEIVER}\nEOF", timeout=20)
    _recv_proc = subprocess.Popen(
        ["ssh", *SSH_OPTS, BENCH,
         f"python3 -u /tmp/ha_recv.py {port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # probe the port from the PC (server binds 0.0.0.0) instead of an
    # ss-over-ssh poll — one cold ssh per iteration was the flake source.
    ip = bench_ip()
    for _ in range(10):
        if port_open(ip, port):
            return True
        time.sleep(1)
    return False


def stop_receiver():
    global _recv_proc
    if _recv_proc is not None:
        _recv_proc.terminate()
        try:
            _recv_proc.wait(timeout=5)
        except Exception:
            _recv_proc.kill()
        _recv_proc = None
    pi("pkill -f ha_recv.py 2>/dev/null; true", timeout=15)


def received(outfile=None):
    # pull the captured posts over plain HTTP from the PC (no ssh in the
    # verification hot path — ssh flakiness must not read as "0 posts")
    try:
        with urllib.request.urlopen(f"http://{bench_ip()}:{PORT}/__dump",
                                    timeout=5) as r:
            arr = json.loads(r.read().decode())
    except Exception:
        return []
    out = []
    for body in arr:
        body = body.strip()
        if body.startswith("{"):
            try:
                out.append(json.loads(body))
            except Exception:
                pass
    return out


def set_webhook(body):
    return api("/api/webhook", "POST", body)


def main():
    outfile = "/tmp/ha_hook.jsonl"

    # [RIG] fast precondition: this leg drives the DUT over USB-NCM.
    # Without the NCM device role up, the first api() call dies in a raw
    # 48 s urllib traceback — classify and fail in 2 s instead.
    try:
        socket.create_connection((USB, 80), timeout=2).close()
    except OSError:
        check(f"[RIG] USB-NCM {USB} reachable", False,
              "DUT usb role must be device/ncm — usb_host_manager "
              "{enabled:true, role:'device', device_class:'ncm'} + submit; "
              "or skip this leg")
        sys.exit(1)

    dut = api("/api/status")[1]
    autopid_on = dut["bits"].get("autopid_enabled")
    check("DUT autopid enabled (poster gate)", autopid_on,
          "enable autopid on the bench first" if not autopid_on else "")

    start_receiver(PORT, outfile)
    try:
        # 1) register via POST /api/webhook (the HA discovery push)
        url = f"http://{PI_AP}:{PORT}/hook"
        code, resp = set_webhook({"url": url, "enabled": True,
                                  "interval": 5})
        check("POST /api/webhook accepted (201/200)",
              code in (200, 201), f"code={code}")

        # 2) GET reflects it
        code, g = api("/api/webhook")
        check("GET /api/webhook shows url+enabled",
              isinstance(g, dict) and g.get("url") == url and g.get("enabled"),
              g.get("url") if isinstance(g, dict) else g)

        # 3) wait for at least one post (interval 5s + poster 5s settle)
        got = []
        for _ in range(9):
            time.sleep(3)
            got = received(outfile)
            if got:
                break
        check("poster delivered telemetry", len(got) >= 1,
              f"{len(got)} posts")

        if got:
            # first post after registration is a full resync. status +
            # config are always present; autopid_data only when a vehicle
            # is producing PID values (bench has none) -> not required.
            p = got[0]
            check("payload carries status + config sections",
                  "status" in p and "config" in p, ",".join(p.keys()))
            dev = (p.get("status") or {}).get("device_id")
            check("status.device_id present (HA identity)", bool(dev), dev)
            check("config section non-empty",
                  len(p.get("config") or {}) > 0,
                  f"{len(p.get('config') or {})} keys")

        # 4) stats reflect success
        code, g = api("/api/webhook")
        check("GET stats status=ok + success_count>0",
              isinstance(g, dict) and g.get("status") == "ok" and
              g.get("success_count", 0) >= 1,
              f"status={g.get('status')} n={g.get('success_count')}"
              if isinstance(g, dict) else g)

        # 5) failover: dead primary + live secondary
        start_receiver(PORT, outfile)  # fresh capture, same detach fix
        dead = f"http://{PI_AP}:{DEAD_PORT}/hook"
        set_webhook({"url": dead, "urls": [dead, url], "enabled": True,
                     "interval": 5})
        got2 = []
        for _ in range(9):
            time.sleep(3)
            got2 = received(outfile)
            if got2:
                break
        check("failover: dead primary -> secondary delivers",
              len(got2) >= 1, f"{len(got2)} posts")

        # 6) DELETE clears
        code, _ = api("/api/webhook", "DELETE")
        check("DELETE /api/webhook 204", code == 204, f"code={code}")
        code, g = api("/api/webhook")
        check("after DELETE: disabled + no url",
              isinstance(g, dict) and not g.get("enabled") and
              not g.get("url"), g if not isinstance(g, dict) else "")
    finally:
        stop_receiver()
        # leave the DUT with the webhook cleared/disabled
        api("/api/webhook", "DELETE")

    if fails:
        print("HA WEBHOOK FAIL:", ", ".join(fails))
        sys.exit(1)
    print("HA WEBHOOK PASS")


if __name__ == "__main__":
    main()
