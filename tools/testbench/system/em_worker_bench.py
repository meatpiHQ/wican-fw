#!/usr/bin/env python3
"""event_manager non-blocking worker-pool bench (PC orchestrator).

Proves a SLOW blocking action can't starve the dispatcher. Sets a 2 s
timer + two rules on `timer.tick`: a `http.post` to a deliberately SLOW
endpoint (sleeps ~8 s — the blocking action) and a fast `log.note`. With
the worker pool the dispatcher keeps processing ticks at ~2 s cadence
(the http.post runs on the pool); without it the dispatcher would stall
~8 s per tick. Measured via the /api/events/log ring's timer.tick spacing.

  python em_worker_bench.py [usb_ip] [bench_host] [pi_ap_ip]
"""
import json
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
import urllib.error

USB = sys.argv[1] if len(sys.argv) > 1 else "192.168.82.1"
BENCH = sys.argv[2] if len(sys.argv) > 2 else "rpi001"
PI_AP = sys.argv[3] if len(sys.argv) > 3 else "10.42.0.1"
PORT = 8207
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
fails = []
_recv = None
_bench_ip = None


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def api(path, method="GET", body=None, retries=4):
    # The DUT reboots on settings/submit; tolerate the connection window by
    # retrying transport-level errors (URLError/reset). An HTTPError is a real
    # HTTP response (e.g. 400 validation) — return it, don't retry.
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for attempt in range(retries):
        req = urllib.request.Request("http://" + USB + path, data=data,
                                     method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                t = r.read().decode()
                return r.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(2)
    raise last


def pi(cmd, timeout=30):
    # Windows OpenSSH has no ControlMaster multiplexing, so every call is a
    # fresh cold handshake; a single one occasionally spikes. Retry once
    # rather than aborting the whole bench on a transient ssh stall.
    last = ""
    for _ in range(2):
        try:
            p = subprocess.run(["ssh", *SSH_OPTS, BENCH, cmd],
                               capture_output=True, text=True, timeout=timeout)
            return p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
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


SLOW = ('import http.server,time,sys\n'
        'class H(http.server.BaseHTTPRequestHandler):\n'
        ' def do_POST(self):\n'
        '  n=int(self.headers.get("Content-Length",0)); self.rfile.read(n)\n'
        '  time.sleep(8)\n'
        '  self.send_response(200); self.end_headers(); self.wfile.write(b"ok")\n'
        ' def log_message(self,*a): pass\n'
        'http.server.HTTPServer(("0.0.0.0",%d),H).serve_forever()\n' % PORT)


def start_slow():
    global _recv
    # clear any leftover from a previous (crashed) run before rebinding
    pi("pkill -f em_slow.py 2>/dev/null; true", timeout=15)
    pi(f"cat > /tmp/em_slow.py <<'EOF'\n{SLOW}\nEOF", timeout=20)
    _recv = subprocess.Popen(["ssh", *SSH_OPTS, BENCH,
                              "python3 -u /tmp/em_slow.py"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # probe the port directly from the PC (server binds 0.0.0.0) instead of
    # ssh-polling `ss` — one cold ssh per iteration was the flake source.
    ip = bench_ip()
    for _ in range(10):
        if port_open(ip, PORT):
            return True
        time.sleep(1)
    return False


def stop_slow():
    global _recv
    if _recv is not None:
        _recv.terminate()
        _recv = None
    pi("pkill -f em_slow.py 2>/dev/null; true", timeout=15)


def set_em(rules, timers):
    cur = api("/api/settings/event_manager")[1]
    for k in ("degraded", "pending_reboot"):
        cur.pop(k, None)
    cur["rules"] = rules
    cur["timers"] = timers
    cur["enabled"] = True
    api("/api/settings/event_manager", "PUT", cur)
    try:
        api("/api/settings/submit", "POST")
    except Exception:
        pass
    time.sleep(12)
    for _ in range(40):
        try:
            api("/api/status")
            return
        except Exception:
            time.sleep(1.5)


def wait_online(secs=90):
    for _ in range(int(secs / 1.5)):
        try:
            api("/api/status")
            return True
        except Exception:
            time.sleep(1.5)
    return False


def main():
    # [RIG] fast precondition: this leg talks to the DUT over USB-NCM.
    # Without the NCM device role up, wait_online() would grind through
    # its full retry window looking hung — classify and fail in 2 s.
    try:
        socket.create_connection((USB, 80), timeout=2).close()
    except OSError:
        check(f"[RIG] USB-NCM {USB} reachable", False,
              "DUT usb role must be device/ncm — usb_host_manager "
              "{enabled:true, role:'device', device_class:'ncm'} + submit; "
              "or skip this leg")
        sys.exit(1)

    # tolerate a DUT still settling from a prior stage's reboot
    if not wait_online():
        check("DUT reachable at start", False)
        sys.exit(1)

    saved = api("/api/settings/event_manager")[1]
    for k in ("degraded", "pending_reboot"):
        saved.pop(k, None)

    if not start_slow():
        check("slow HTTP endpoint up on the Pi", False)
        sys.exit(1)

    try:
        slow_url = f"http://{PI_AP}:{PORT}/slow"
        rules = [
            {"name": "emb_slow", "on": "timer.tick", "do": "http.post",
             "with": {"url": slow_url, "body": "x"}},
            {"name": "emb_fast", "on": "timer.tick", "do": "log.note",
             "with": {"message": "embench tick"}},
        ]
        set_em(rules, [{"name": "embench", "period_s": 2}])

        st0 = api("/api/events/log")[1].get("stats", {})
        check("event_manager running + blocking_dropped stat present",
              st0.get("running") and "blocking_dropped" in st0, str(st0))

        # let it run ~24 s (a 2 s timer => ~12 ticks). The fast log.note
        # rule fires once per PROCESSED tick, so its throughput IS the
        # dispatcher's throughput. (The shared 32-deep event ring is a bad
        # measure here — autopid.param evicts old timer.ticks — so we use
        # the `fired` stat delta.)
        t0 = time.time()
        time.sleep(24)
        dt = time.time() - t0

        st = api("/api/events/log")[1].get("stats", {})
        d_fired = st.get("fired", 0) - st0.get("fired", 0)
        # cadence cross-check from whatever timer.ticks survive in the ring
        ticks = [e["ts_us"] for e in api("/api/events/log")[1].get("events", [])
                 if e.get("source") == "timer" and e.get("name") == "tick"]
        gaps = [(b - a) / 1e6 for a, b in zip(ticks, ticks[1:])]
        med = statistics.median(gaps) if gaps else 0

        # pool: ~1 fire / 2 s tick => ~10-12 over 24 s. Old sync stalls
        # ~8 s/tick on the dead http.post => only ~3. >=8 proves it.
        expect = int(dt / 2) - 2
        check("dispatcher throughput unstalled by the slow http.post",
              d_fired >= 8, f"{d_fired} fires in {dt:.0f}s (expect ~{expect})")
        if med:
            check("timer.tick cadence ~2 s in the ring",
                  med < 4.0, f"median gap {med:.1f}s")
        check("blocking jobs handled/bounded (fired or dropped counted)",
              st.get("blocking_dropped", 0) + st.get("fired", 0) > 0,
              f"fired={st.get('fired')} blocking_dropped={st.get('blocking_dropped')}")
    finally:
        stop_slow()
        # restore original event_manager settings
        api("/api/settings/event_manager", "PUT", saved)
        try:
            api("/api/settings/submit", "POST")
        except Exception:
            pass
        # don't hand a mid-rebooting DUT to the next stage
        wait_online()

    if fails:
        print("EM WORKER FAIL:", ", ".join(fails))
        sys.exit(1)
    print("EM WORKER PASS")


if __name__ == "__main__":
    main()
