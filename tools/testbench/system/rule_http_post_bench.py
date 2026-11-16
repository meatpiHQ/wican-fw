#!/usr/bin/env python3
"""A rule's http.post, plain and over https, on the event manager's workers.

The claim: a rule whose action is `http.post` delivers to an http:// and to
an https:// URL, the device does not restart, and the two worker tasks the
action runs on keep stack headroom behind the TLS handshake. Issue 1067
(2026-11-15, 6.0.0-beta.10): the workers had a 4 KB stack, the handshake of
the first https post overflowed it and the device reset (`A stack overflow
in task em_worker has been detected`); three such crashes in a row park a
unit. No bench had fired http.post at an https URL until this one.

The trigger is `mqtt.rx`: one message from the Pi's broker fires a rule
exactly once, on demand. (A timer would fire at every boot: on an image
with the defect the unit would crash three times and park.)

Legs:
  p0  as found: the rules, the MQTT settings, the workers' headroom
  p1  the rules stored (planned restart 1), the unit's MQTT on the Pi's
      broker, a plain HTTP receiver started on the Pi
  p2  plain http: one message, the receiver has the rendered body, the
      rule fired, no restart
  p3  https, one URL after the other: one message each; the server
      answered (the rule fired, or the action failed on the HTTP status,
      never on the transport) and the unit did not restart
  p4  https twice at once: two messages 0.3 s apart, so that the second
      post runs on the other worker while the first is in its handshake;
      both workers have run a handshake, both keep --headroom bytes
  p5  as found (planned restart 2): the rules and the MQTT settings back,
      no restart nobody asked for, no E line of the event manager or the
      HTTP client, the internal heap's floor

  python tools/testbench/system/rule_http_post_bench.py --dut localhost:18081
      --pi rpi001 --pi-ip 10.42.1.1 [--https-url URL ...] [--headroom 4096]

Prints `RULE POST PASS`. Written 2026-11-16.
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
RECV_PORT = 8208
RECV_LOG = "/tmp/rule_post_recv.log"
RECV_PY = "/tmp/rule_post_recv.py"
# the bracket keeps pkill -f from matching the shell that runs this very
# command (it then died before its own rm: the files stayed on the Pi);
# for the same reason no command that kills also names RECV_PY
RECV_KILL = "pkill -f '[r]ule_post_recv.py' 2>/dev/null"
PREFIX = "rp_"                      # this bench's rules
TOPIC = "rulepost"                  # this bench's MQTT topics
BODY = '{"state":"bump","axes":"${payload}","ts":"${time.iso}"}'
# an ECDSA chain (Google Trust Services, the cross-signed root of the stage
# tlschain) and an RSA one (Let's Encrypt's own test host for ISRG Root X1):
# the two handshakes do different arithmetic on the stack
HTTPS_URLS = ["https://developers.cloudflare.com/api/webhook/wican-rulepost",
              "https://valid-isrgrootx1.letsencrypt.org/"]

RECV = ('import http.server\n'
        'class H(http.server.BaseHTTPRequestHandler):\n'
        ' def do_POST(self):\n'
        '  n=int(self.headers.get("Content-Length",0)); b=self.rfile.read(n)\n'
        '  open("%s","ab").write(b+b"\\n")\n'
        '  self.send_response(200); self.end_headers(); self.wfile.write(b"ok")\n'
        ' def log_message(self,*a): pass\n'
        'http.server.HTTPServer(("0.0.0.0",%d),H).serve_forever()\n'
        % (RECV_LOG, RECV_PORT))


def pi(host, cmd, timeout=30):
    """One command on the Pi; the text it printed ('' on a timeout)."""
    for _ in range(2):              # a cold ssh handshake stalls now and then
        try:
            p = subprocess.run(SSH + [host, cmd], capture_output=True,
                               text=True, timeout=timeout)
            return (p.stdout + p.stderr).strip()
        except subprocess.TimeoutExpired:
            pass
    return ""


def workers(dut):
    """The stack headroom of the two em_worker tasks, least first."""
    code, t = dut.api("/api/status/tasks")
    if code != 200 or not isinstance(t, dict):
        return []
    return sorted(x["stack_hw"] for x in t.get("tasks", [])
                  if x.get("name") == "em_worker")


def counters(dut, rule):
    """(the rule's fired count, the engine's action_errors)."""
    fired = 0
    for r in dut.get("/api/events/rules"):
        if r.get("name") == rule:
            fired = r.get("fired", 0)
    errors = dut.get("/api/events/log").get("stats", {}).get("action_errors", 0)
    return fired, errors


def post_failures(dut):
    """The worker's W lines of failed posts in the log ring, as a set."""
    return {ln for ln in dut.ring_lines()
            if "event_manager" in ln and "action http.post failed" in ln}


def fire(dut, pi_host, broker, topics, rule, want, watch_s, payload="xyz"):
    """Publish one message per topic (0.3 s apart) and watch until @p rule
    has run @p want times (fired or failed) or the unit restarted.
    Returns (kind, detail): kind is 'ran', 'restart' or 'nothing'."""
    b0 = dut.boot_count()
    f0, e0 = counters(dut, rule)
    w0 = post_failures(dut)
    pi(pi_host, "; sleep 0.3; ".join(
        f"mosquitto_pub -h {broker} -t {t} -m {payload}" for t in topics))
    t0 = time.time()
    while time.time() - t0 < watch_s:
        time.sleep(2)
        b = dut.boot_count()
        if b is None:
            continue                # away: restarting, or a WiFi hiccup
        if b != b0:
            return "restart", {"boot_count": f"{b0} -> {b}"}
        f, e = counters(dut, rule)
        if (f - f0) + (e - e0) >= want:
            time.sleep(3)           # a canary panic comes at a task switch
            if dut.boot_count() != b0:
                continue
            new_w = sorted(post_failures(dut) - w0)
            # ESP_FAIL = the server's status was not 2xx (it answered);
            # anything else is the transport (DNS, TCP, the handshake)
            transport = [ln for ln in new_w if "(ESP_FAIL)" not in ln]
            return "ran", {"fired": f - f0, "http_status_errors": e - e0,
                           "transport": transport,
                           "s": round(time.time() - t0, 1)}
    return "nothing", {"waited_s": watch_s}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081",
                    help="host:port of the DUT's API (a tunnel)")
    ap.add_argument("--pi", default="rpi001", help="the Pi over ssh")
    ap.add_argument("--pi-ip", default="10.42.1.1",
                    help="the Pi as the DUT sees it (the broker, the receiver)")
    ap.add_argument("--https-url", action="append",
                    help="an https URL to post to (may repeat; the default: "
                         "an ECDSA chain and an RSA chain)")
    ap.add_argument("--headroom", type=int, default=4096,
                    help="bytes of stack each worker must keep")
    ap.add_argument("--watch", type=int, default=45,
                    help="seconds to watch a fired rule")
    args = ap.parse_args()
    urls = args.https_url or HTTPS_URLS

    run = pcbench.Run("RULE POST")
    dut = pcbench.Dut(args.dut, own_tags=("event_manager", "http_client_manager"),
                      tasks=("em_worker", "em_dispatch"))
    broker_url = f"mqtt://{args.pi_ip}:1883"
    recv = None
    restarted = False

    # ---- p0: as found
    st0 = dut.get("/api/status")
    dut.as_found()
    em0 = dut.settings("event_manager")
    mq0 = dut.settings("mqtt_manager")
    idle = workers(dut)
    run.check("p0 the DUT answers, two em_worker tasks", len(idle) == 2,
              f"{st0.get('version')}, rules "
              f"{[r['name'] for r in em0.get('rules', [])]}, headroom {idle}")

    try:
        # ---- p1: the rules, the broker, the receiver
        pi(args.pi, f"{RECV_KILL}; true")
        pi(args.pi, f"rm -f {RECV_LOG}")
        pi(args.pi, f"cat > {RECV_PY} <<'EOF'\n{RECV}\nEOF")
        recv = subprocess.Popen(SSH + [args.pi, f"python3 -u {RECV_PY}"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)

        def rule(name, topic, url):
            return {"name": name, "on": "mqtt.rx", "match": {"topic": topic},
                    "do": "http.post",
                    "with": {"url": url, "content_type": "application/json",
                             "body": BODY}}

        rules = [r for r in em0.get("rules", [])
                 if not r["name"].startswith(PREFIX)]
        rules.append(rule(PREFIX + "http", f"{TOPIC}/http",
                          f"http://{args.pi_ip}:{RECV_PORT}/api/webhook/rulepost"))
        for i, url in enumerate(urls):
            rules.append(rule(f"{PREFIX}https{i}", f"{TOPIC}/https{i}", url))
        # the pair for p4: two rules on the first https URL, a topic each
        rules.append(rule(PREFIX + "pair_a", f"{TOPIC}/pair_a", urls[0]))
        rules.append(rule(PREFIX + "pair_b", f"{TOPIC}/pair_b", urls[0]))
        # of the MQTT settings only the two keys the trigger needs are
        # staged (no change on a bench unit that is on the Pi's broker)
        dut.restart({"event_manager": {"enabled": True, "rules": rules},
                     "mqtt_manager": {"enabled": True, "url": broker_url}})
        t0 = time.time()
        mqtt = False
        while time.time() - t0 < 90 and not mqtt:
            time.sleep(2)
            code, st = dut.api("/api/status")
            mqtt = code == 200 and st.get("bits", {}).get("mqtt_connected")
        time.sleep(4)               # the rule topics are subscribed on connect
        have = [r["name"] for r in dut.settings("event_manager")["rules"]]
        run.check("p1 the rules stored, the unit's MQTT on the Pi's broker",
                  mqtt and all(r["name"] in have for r in rules),
                  f"rules {have}, headroom {workers(dut)}")

        # ---- p2: plain http
        mark = "rp%d" % (int(time.time()) % 1000000)   # this run's message
        kind, d = fire(dut, args.pi, args.pi_ip, [f"{TOPIC}/http"],
                       PREFIX + "http", 1, args.watch, payload=mark)
        got = pi(args.pi, f"tail -1 {RECV_LOG} 2>/dev/null")
        run.check("p2 plain http: the rule fired, the receiver has the body",
                  kind == "ran" and d.get("fired") == 1
                  and '"state":"bump"' in got and f'"axes":"{mark}"' in got,
                  f"{kind} {d}; received {got[:90]!r}; headroom {workers(dut)}")
        restarted = kind == "restart"

        # ---- p3: https, one URL after the other
        for i, url in enumerate(urls):
            if restarted:
                break
            kind, d = fire(dut, args.pi, args.pi_ip, [f"{TOPIC}/https{i}"],
                           f"{PREFIX}https{i}", 1, args.watch)
            restarted = kind == "restart"
            run.check(f"p3 https {url.split('/')[2]}: no restart",
                      not restarted, f"{kind} {d}")
            if restarted:
                break
            run.check(f"p3 https {url.split('/')[2]}: the server answered "
                      "(no transport error)",
                      kind == "ran" and not d.get("transport"),
                      f"{kind} {d}; headroom {workers(dut)}")
            dut.sweep()

        # ---- p4: two posts at once, one per worker
        if not restarted:
            b0 = dut.boot_count()
            fa0, e0 = counters(dut, PREFIX + "pair_a")
            fb0, _ = counters(dut, PREFIX + "pair_b")
            pi(args.pi, f"mosquitto_pub -h {args.pi_ip} -t {TOPIC}/pair_a -m xyz; "
                        f"sleep 0.3; mosquitto_pub -h {args.pi_ip} -t "
                        f"{TOPIC}/pair_b -m xyz")
            t0 = time.time()
            done = 0
            while time.time() - t0 < args.watch and done < 2 and not restarted:
                time.sleep(2)
                b = dut.boot_count()
                if b is None:
                    continue
                restarted = b != b0
                if not restarted:
                    fa, e = counters(dut, PREFIX + "pair_a")
                    fb, _ = counters(dut, PREFIX + "pair_b")
                    done = (fa - fa0) + (fb - fb0) + (e - e0)
            time.sleep(3)
            restarted = restarted or dut.boot_count() != b0
            hw = workers(dut)
            run.check("p4 two https posts at once: both ran, no restart",
                      done >= 2 and not restarted,
                      f"{done} of 2 after {time.time() - t0:.0f} s")
            # a worker that has run a handshake has used far more of its
            # stack than an idle one or one that posted plain http
            run.check("p4 both workers have run a handshake",
                      len(hw) == 2 and len(idle) == 2
                      and all(h < min(idle) - 2048 for h in hw),
                      f"headroom {hw}, idle {idle}")
            run.check(f"p4 each worker keeps {args.headroom} B of stack",
                      len(hw) == 2 and min(hw) >= args.headroom, f"{hw}")
            if hw and idle:
                run.metric("em_worker_headroom_b", min(hw))
                run.metric("em_worker_https_cost_b", min(idle) - min(hw))
        if restarted:
            code, h = dut.api("/api/restart/history", timeout=15)
            recs = h.get("records", []) if isinstance(h, dict) else []
            crash = next((r for r in recs if r.get("crash")), None)
            run.check("the unit did not restart on a post", False,
                      pcbench.reset_text(crash) if crash else "no crash note")
    finally:
        # ---- p5: as found
        if recv is not None:
            recv.terminate()
        pi(args.pi, f"{RECV_KILL}; true")
        pi(args.pi, f"rm -f {RECV_PY} {RECV_LOG}")
        if restarted:
            time.sleep(15)          # let the crashed unit finish its boot
        dut.restart({"event_manager": em0,
                     "mqtt_manager": {"enabled": mq0.get("enabled"),
                                      "url": mq0.get("url")}})
        time.sleep(6)
        em1 = dut.settings("event_manager")
        mq1 = dut.settings("mqtt_manager")
        run.check("p5 the rules and the MQTT settings as found",
                  em1 == em0 and mq1 == mq0,
                  f"rules {[r['name'] for r in em1.get('rules', [])]}")
        dut.reset_check(run, st0)
        dut.passing_checks(run)

    return run.verdict()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except pcbench.Bench as e:
        print(f"BENCH ERROR: {e}")
        print("RULE POST FAIL: bench error")
        sys.exit(2)
