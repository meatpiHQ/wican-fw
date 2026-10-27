#!/usr/bin/env python3
"""A planned restart with the MQTT broker connected leaves no error line
(2026-10-27). PC orchestrator, the DUT through an ssh tunnel to rpi001.

Before the fix esp_restart()'s shutdown handlers stopped the WiFi stack while
the broker socket was open, and esp-mqtt's task printed two E lines at every
planned restart (`transport_read() error: errno=113`,
`mqtt_message_receive() returned -2`, with a `transport_base` line beside
them) that every bench had to whitelist. mqtt_manager now registers a
pre-restart hook (restart_tracker's table) that stops the client while a
session is up, so the DISCONNECT packet goes out and the socket closes before
the restart.

Legs (each restart is planned: /api/restart, USER_REQUEST from the web UI):
  r0  the DUT answers, the broker session is up (`mqtt_connected` in
      /api/status), the ring's E lines are counted (the library's MQTT and
      transport lines on their own, and every E line minus the e2e noise:
      data_logger, esp-tls, HTTP_CLIENT)
  r1..rN  POST /api/restart, the DUT back, the session up again; the ring
      (it survives a warm restart) holds no new MQTT or transport E line and
      no new E line at all, one more `mqtt_manager: stopped before the
      restart`, the restart history grew by one PLANNED record, the
      unexpected count did not move, the health counters say no fault

  python tools/testbench/system/restart_mqtt_quiet_bench.py --dut localhost:18081 [--restarts 2]

Verdict: RESTART MQTT QUIET PASS / RESTART MQTT QUIET FAIL: <names>.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

NOISE = ("data_logger", "esp-tls", "HTTP_CLIENT")        # the e2e benches' list, without the MQTT entries
MQTT_TAGS = ("mqtt_client", "transport_base")             # the lines the fix must silence
STOP_LINE = "mqtt_manager: stopped before the restart"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def http(base, method, path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def get_json(base, path, timeout=10):
    st, body = http(base, "GET", path, timeout=timeout)
    if st != 200:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def ring_lines(base):
    st, body = http(base, "GET", "/api/logs/ring")
    if st != 200:
        return None
    return [ANSI.sub("", ln) for ln in body.splitlines()]


def e_counts(lines):
    """(the MQTT/transport E lines, every E line minus the noise, the stop lines)"""
    e_all = [ln for ln in lines if "E (" in ln]
    mqtt = sum(1 for ln in e_all if any(t in ln for t in MQTT_TAGS))
    own = sum(1 for ln in e_all if not any(n in ln for n in NOISE))
    stops = sum(1 for ln in lines if STOP_LINE in ln)
    return mqtt, own, stops


def since_last_restart(lines):
    """(the index of the last planned-restart marker, the lines from it on); the
    ring rotates, so a count over the whole ring can stay equal while new lines
    replace old ones: the slice is what the restart itself logged"""
    at = -1
    for i, ln in enumerate(lines):
        if "restart_tracker: restarting:" in ln:
            at = i
    return at, (lines[at:] if at >= 0 else [])


def mqtt_up(status):
    if not status:
        return False
    bits = status.get("bits") or {}
    if "mqtt_connected" in bits:
        return bool(bits["mqtt_connected"])
    return bool(status.get("mqtt_connected"))


def wait_back(base, label, tries=45):
    for _ in range(tries):
        time.sleep(2)
        if get_json(base, "/api/info", timeout=3):
            print("   %s: the DUT is back" % label)
            return True
    return False


def wait_mqtt(base, tries=40):
    for _ in range(tries):
        if mqtt_up(get_json(base, "/api/status", timeout=5)):
            return True
        time.sleep(2)
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dut", default="localhost:18081", help="host:port of the DUT (default: the tunnel on localhost:18081)")
    ap.add_argument("--restarts", type=int, default=2)
    args = ap.parse_args()
    base = "http://" + args.dut
    fails = []

    def check(name, ok, extra=None):
        print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  [%s]" % json.dumps(extra)[:300]) if extra is not None else ""))
        if not ok:
            fails.append(name)
        return ok

    # ---- r0 ----
    info = get_json(base, "/api/info")
    if not check("r0: the DUT answers", bool(info), None):
        print("RESTART MQTT QUIET FAIL: no DUT at %s" % base)
        return 2
    status = get_json(base, "/api/status")
    if not check("r0: the broker session is up (mqtt_connected)", mqtt_up(status), (status or {}).get("bits")):
        print("RESTART MQTT QUIET FAIL: the broker session is not up, nothing to prove (check the Pi's broker and the DUT's mqtt_manager settings)")
        return 2
    lines = ring_lines(base)
    if not check("r0: the ring log is readable", lines is not None):
        print("RESTART MQTT QUIET FAIL: no ring")
        return 2
    mqtt0, own0, stops0 = e_counts(lines)
    hist0 = get_json(base, "/api/restart/history") or {}
    print("   baseline: MQTT/transport E lines %d, own E lines %d, stop lines %d, boot %s, unexpected %s"
          % (mqtt0, own0, stops0, hist0.get("boot_count"), hist0.get("unexpected_resets")))

    # ---- r1..rN ----
    for n in range(1, args.restarts + 1):
        label = "r%d" % n
        st, _ = http(base, "POST", "/api/restart", {})
        check("%s: POST /api/restart accepted" % label, st == 200, st)
        time.sleep(8)
        if not check("%s: the DUT came back" % label, wait_back(base, label)):
            break
        check("%s: the broker session is up again" % label, wait_mqtt(base))
        lines = ring_lines(base) or []
        mqtt1, own1, stops1 = e_counts(lines)
        at, tail = since_last_restart(lines)
        tail_mqtt = [ln for ln in tail if "E (" in ln and any(t in ln for t in MQTT_TAGS)]
        tail_own = [ln for ln in tail if "E (" in ln and not any(n in ln for n in NOISE)]
        check("%s: the ring holds the restart marker" % label, at >= 0, at)
        check("%s: no MQTT or transport E line from the restart on (the client stopped first)" % label, at >= 0 and not tail_mqtt, tail_mqtt[:3])
        check("%s: no E line at all from the restart on" % label, at >= 0 and not tail_own, tail_own[:3])
        check("%s: the manager's stop line sits right before the marker" % label, at >= 0 and any(STOP_LINE in ln for ln in lines[max(0, at - 12):at]), lines[max(0, at - 4):at + 1])
        check("%s: whole ring: no MQTT or transport E line added" % label, mqtt1 <= mqtt0, {"before": mqtt0, "after": mqtt1})
        hist1 = get_json(base, "/api/restart/history") or {}
        recs = hist1.get("records") or []
        last = max(recs, key=lambda r: r.get("seq") or 0) if recs else {}   # the newest record (the list is newest first)
        check("%s: the restart is a planned record, the unexpected count unchanged" % label,
              hist1.get("boot_count") == (hist0.get("boot_count") or 0) + 1 and hist1.get("unexpected_resets") == hist0.get("unexpected_resets") and last.get("planned") is True,
              {"boot": hist1.get("boot_count"), "unexpected": hist1.get("unexpected_resets"), "last": {k: last.get(k) for k in ("reason", "planned", "planned_reason", "source")}})
        health = (get_json(base, "/api/status") or {}).get("health") or {}
        check("%s: no fault, no error line counted this boot" % label, health.get("faults") == 0 and health.get("log_errors") == 0, {"faults": health.get("faults"), "log_errors": health.get("log_errors")})
        mqtt0, own0, stops0, hist0 = mqtt1, own1, stops1, hist1

    if fails:
        print("RESTART MQTT QUIET FAIL: " + "; ".join(fails))
        return 1
    print("RESTART MQTT QUIET PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
