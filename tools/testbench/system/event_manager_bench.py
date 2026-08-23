#!/usr/bin/env python3
"""event_manager comprehensive integration bench — runs ON rpi001 (paho to
the local broker + the DUT's HTTP API). Complements the pure host suite
(rule match/when/cooldown/templates) and the worker-pool bench
(em_worker_bench.py). Proves the wired paths on real hardware:

  1. discovery   — /api/events/{sources,actions,values} expose the
                   registered registries (timer/mqtt/... , log.note/
                   mqtt.publish/http.post/... , time./... ).
  2. validation  — a rule with an unknown action/event is REJECTED at PUT.
  3. timer->act  — a settings timer fires a rule whose mqtt.publish action
                   lands at the broker at cadence, with the ${..} template
                   RENDERED (not literal).
  4. cooldown    — a cooldown_ms rule is throttled + `suppressed` climbs.

Preconditions are SELF-HEALED (rig-standardization rule: a leg verifies
its own rig, 2026-07-26 after a silent mqtt_connected:false FAIL):
  [RIG] mosquitto must be active on this Pi — started if not.
  [DUT] mqtt must be connected to the bench broker — if not, mqtt_manager
        is pointed at <dut_subnet>.1 for the run and RESTORED afterwards
        (GET->PUT round-trip is safe: api_http unredacts _password fields
        on PUT). Ends with EVENT MGR PASS.

  sudo/plain: python3 event_manager_bench.py [dut_ip] [device_id]
"""
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error

import paho.mqtt.client as mqtt

DUT = sys.argv[1] if len(sys.argv) > 1 else "10.42.0.62"
DEVID = sys.argv[2] if len(sys.argv) > 2 else "14c19f44e349"
BROKER = "localhost"
PREFIX = f"wican/{DEVID}"
fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{DUT}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            t = r.read().decode()
            return r.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def wait_online(secs=90):
    for _ in range(int(secs / 1.5)):
        try:
            api("/api/status")
            return True
        except Exception:
            time.sleep(1.5)
    return False


# ---- preconditions (self-healing; see docstring) ---------------------------

def rig_broker_up():
    """[RIG] mosquitto active on this Pi (we run locally on rpi001)."""
    def active():
        return subprocess.run(["systemctl", "is-active", "--quiet",
                               "mosquitto"]).returncode == 0
    if active():
        return True
    print("[RIG] mosquitto inactive — starting it")
    subprocess.run(["sudo", "systemctl", "start", "mosquitto"], check=False)
    for _ in range(10):
        if active():
            return True
        time.sleep(0.5)
    return False


def dut_mqtt_connected():
    """mqtt_connected is a dev-status BIT (status["bits"], like
    autopid_enabled) — NOT a top-level status field. Reading the top
    level polls a key that never exists (false-FAILed 2× 2026-07-26)."""
    try:
        st = api("/api/status")[1]
        return bool(st.get("bits", {}).get("mqtt_connected",
                                           st.get("mqtt_connected")))
    except Exception:
        return False


def wait_mqtt(secs):
    """Poll mqtt_connected — reconnect after a settings reboot takes a
    while (WiFi rejoin + broker connect landed ~40 s post-submit on the
    2026-07-26 run; a 30 s poll false-FAILed a functionally green leg)."""
    deadline = time.time() + secs
    while time.time() < deadline:
        if dut_mqtt_connected():
            return True
        time.sleep(1.5)
    return False


def ensure_dut_mqtt():
    """[DUT] mqtt connected, else point mqtt_manager at the bench broker
    (the Pi = .1 on the DUT's subnet). Returns the settings to restore
    (None when nothing was changed)."""
    if dut_mqtt_connected():
        return None
    bench_broker = DUT.rsplit(".", 1)[0] + ".1"
    print(f"[SETUP] DUT mqtt not connected — pointing it at "
          f"mqtt://{bench_broker} for this run")
    cur = api("/api/settings/mqtt_manager")[1]
    for k in ("degraded", "pending_reboot"):
        cur.pop(k, None)
    saved = dict(cur)
    cur.update({"enabled": True, "url": f"mqtt://{bench_broker}"})
    api("/api/settings/mqtt_manager", "PUT", cur)
    try:
        api("/api/settings/submit", "POST")
    except Exception:
        pass
    time.sleep(12)
    wait_online()
    if not wait_mqtt(90):
        print("[SETUP] WARNING: DUT still not connected to the bench "
              "broker — the timer->mqtt legs will fail "
              "(RIG/DUT network issue?)")
    return saved


def apply_em(cfg):
    cur = api("/api/settings/event_manager")[1]
    for k in ("degraded", "pending_reboot"):
        cur.pop(k, None)
    cur.update(cfg)
    api("/api/settings/event_manager", "PUT", cur)
    try:
        api("/api/settings/submit", "POST")
    except Exception:
        pass
    time.sleep(12)
    wait_online()


msgs = []


def on_msg(c, u, m):
    msgs.append((m.topic, m.payload.decode("utf-8", "replace")))


def main():
    check("[RIG] mosquitto broker active", rig_broker_up(), "")
    saved_mqtt = ensure_dut_mqtt()
    check("[DUT] mqtt connected to the bench broker",
          dut_mqtt_connected() or wait_mqtt(30), "")

    saved = api("/api/settings/event_manager")[1]
    for k in ("degraded", "pending_reboot"):
        saved.pop(k, None)

    try:  # paho v2 (VERSION2 is fine: only on_message is used and its
        # signature is unchanged); paho v1 lacks the enum
        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        cli = mqtt.Client()
    cli.on_message = on_msg
    cli.connect(BROKER)
    cli.subscribe(f"{PREFIX}/#")
    cli.loop_start()

    try:
        # 1. discovery
        srcs = api("/api/events/sources")[1]
        acts = api("/api/events/actions")[1]
        vals = api("/api/events/values")[1]
        src_names = json.dumps(srcs)
        act_names = json.dumps(acts)
        check("discovery: sources include timer.tick",
              "timer" in src_names, "")
        check("discovery: actions include mqtt.publish + log.note + http.post",
              all(a in act_names for a in ("mqtt.publish", "log.note", "http.post")), "")
        check("discovery: values non-empty (pull-value providers)",
              len(json.dumps(vals)) > 20, "")

        # 2. validation — unknown action must be rejected at PUT
        bad = dict(saved)
        bad.pop("degraded", None); bad.pop("pending_reboot", None)
        bad["rules"] = [{"name": "bad", "on": "timer.tick", "do": "does.not.exist",
                         "with": {}}]
        code, resp = api("/api/settings/event_manager", "PUT", bad)
        check("validation: unknown action rejected (4xx)",
              code >= 400, f"code={code}")

        # 3 + 4. timer -> mqtt.publish (rendered template) + cooldown
        apply_em({
            "enabled": True,
            "timers": [{"name": "emt", "period_s": 1}],
            "rules": [
                {"name": "emt_pub", "on": "timer.tick", "do": "mqtt.publish",
                 "with": {"topic": "~/emt",
                          "payload": "tick|${timer}|${time.iso}"}},
                {"name": "emt_cool", "on": "timer.tick", "do": "mqtt.publish",
                 "cooldown_ms": 4000,
                 "with": {"topic": "~/emc", "payload": "c"}},
            ],
        })

        st0 = api("/api/events/log")[1].get("stats", {})
        msgs.clear()
        time.sleep(11)
        st1 = api("/api/events/log")[1].get("stats", {})

        emt = [p for (t, p) in msgs if t.endswith("/emt")]
        emc = [p for (t, p) in msgs if t.endswith("/emc")]

        check("timer->mqtt.publish delivered at ~1 s cadence",
              len(emt) >= 7, f"{len(emt)} msgs in 11 s")
        rendered = emt and emt[-1].startswith("tick|emt|") and \
            "${" not in emt[-1] and "T" in emt[-1]
        check("template rendered (${timer}/${time.iso} resolved)",
              bool(rendered), emt[-1] if emt else "no msg")
        check("cooldown throttled the 2nd rule (~1 per 4 s)",
              1 <= len(emc) <= 4, f"{len(emc)} msgs in 11 s")
        check("suppressed counter climbed under cooldown",
              st1.get("suppressed", 0) > st0.get("suppressed", 0),
              f"{st0.get('suppressed')}->{st1.get('suppressed')}")
        check("fired counter climbed",
              st1.get("fired", 0) > st0.get("fired", 0),
              f"{st0.get('fired')}->{st1.get('fired')}")
    finally:
        cli.loop_stop()
        api("/api/settings/event_manager", "PUT", saved)
        if saved_mqtt is not None:
            api("/api/settings/mqtt_manager", "PUT", saved_mqtt)
        try:
            api("/api/settings/submit", "POST")
        except Exception:
            pass
        wait_online()

    if fails:
        print("EVENT MGR FAIL:", ", ".join(fails))
        sys.exit(1)
    print("EVENT MGR PASS")


if __name__ == "__main__":
    main()
