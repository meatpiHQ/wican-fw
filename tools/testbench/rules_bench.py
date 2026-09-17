#!/usr/bin/env python3
"""Rules-engine bench against the composed firmware (2026-09-17).

Phase A  the registry surfaces: the wifi.sta source, the wifi.* live values,
         `undoable` on autopid.group / led.indicate only, GET /api/events/rules.
Phase B  a wifi.sta while-rule (undo) fires on the STA connect at boot and
         overrides a group's rate (Submit = restart; skip with --skip-wifi).
Phase C  a timer rule with a LIVE `${time.epoch}` condition + undo: the
         dispatcher's 1 s re-check undoes it once the device's clock passes
         the deadline -> the group is back at its configured rate.
Phase D  two while-rules on ONE group (the WiFi rule + the epoch rule) plus a
         plain timer rule with a cooldown: both while-rules arm, the epoch
         undo restores the CONFIGURED rate (not the other rule's override,
         the documented semantic), and the cooldown holds the plain rule to
         one run per window (fired count + stats.suppressed).
Restores the device's event_manager settings at the end (one more restart).

The DEVICE's clock is used for the deadline (restart_tracker boot_time +
uptime): the bench DUT's wall clock differs from the PC's by minutes.

    python tools/testbench/rules_bench.py --base http://localhost:8081
    python tools/testbench/rules_bench.py --skip-wifi       # phases A + C only
"""
import argparse
import json
import sys
import time
import urllib.request

fails = []
BASE = "http://localhost:8081"


def _api(path, method="GET", body=None, timeout=8):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def api(path, method="GET", body=None, timeout=8):
    try:
        return _api(path, method, body, timeout)
    except Exception:
        if method != "GET":
            raise
        time.sleep(2)
        return _api(path, method, body, timeout)


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + extra if extra else ""))
    if not ok:
        fails.append(name)


def up_s(st):
    try:
        h, m, sec = [int(x) for x in str(st.get("uptime", "0:0:0")).split(":")[-3:]]
        return h * 3600 + m * 60 + sec
    except Exception:
        return 0


def dev_now():
    """The device's epoch: the latest valid restart record's boot_time + uptime."""
    hist = api("/api/restart/history")
    recs = hist.get("records", []) if isinstance(hist, dict) else []
    boot_time = max((r.get("boot_time", 0) for r in recs if r.get("time_valid")), default=0)
    return boot_time + up_s(api("/api/status")) if boot_time else int(time.time())


def wait_restart(bc0, max_s=300):
    """Back once /api/status reports a HIGHER boot_count than before the submit
    (an uptime comparison misfires when the previous restart was seconds ago)."""
    t0 = time.time()
    time.sleep(3)
    while time.time() - t0 < max_s:
        try:
            st = api("/api/status", timeout=3)
            if isinstance(st, dict) and int(st.get("boot_count", 0) or 0) > bc0:
                return st
        except Exception:
            pass
        time.sleep(2)
    return None


def submit(em):
    st0 = api("/api/status")
    bc0 = int(st0.get("boot_count", 0) or 0)
    api("/api/settings/event_manager", "PUT", em)
    r = api("/api/settings/submit", "POST", {})
    t0 = time.time()
    print("  submit ->", r, "(boot_count before", bc0, ")")
    st = wait_restart(bc0)
    check("device back after the restart", st is not None,
          "%s s, uptime %s" % (int(time.time() - t0), st and st.get("uptime")))
    time.sleep(5)


def groups():
    return {g["name"]: g for g in api("/api/autopid").get("groups", [])}


def rules_rt():
    return {r["name"]: r for r in api("/api/events/rules")}


def main():
    global BASE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--skip-wifi", action="store_true", help="skip phase B (the wifi.sta while-rule)")
    ap.add_argument("--group", default="default", help="the PID group the rules override")
    ap.add_argument("--hold", type=int, default=150, help="phase C: seconds the epoch condition holds")
    ap.add_argument("--skip-stack", action="store_true", help="skip phase D (stacked rules + cooldown)")
    args = ap.parse_args()
    BASE = args.base.rstrip("/")

    # ---- phase A
    srcs = api("/api/events/sources")
    acts = api("/api/events/actions")
    vals = api("/api/events/values")
    wifi = [s for s in srcs if s.get("event") == "wifi.sta"]
    check("wifi.sta source declared with connected + ssid",
          bool(wifi) and sorted(k["key"] for k in wifi[0]["keys"]) == ["connected", "ssid"])
    check("wifi.ssid + wifi.connected live values", "wifi.ssid" in vals and "wifi.connected" in vals, str(vals))
    und = {a["name"]: a.get("undoable") for a in acts}
    check("undoable flags: autopid.group + led.indicate only",
          und.get("autopid.group") is True and und.get("led.indicate") is True and
          not any(v for k, v in und.items() if k not in ("autopid.group", "led.indicate")), str(und))
    st = api("/api/status")
    caps = (st.get("health") or {}).get("caps") or {}
    check("registry headroom >= 2 for events_src / events_act (health.caps)",
          all(caps.get(k) and caps[k]["cap"] - caps[k]["used"] >= 2 for k in ("events_src", "events_act")), str(caps))
    ws = api("/api/wifi/status")
    ssid = ((ws.get("sta_attempt") or {}).get("ssid")) or ws.get("ssid") or ""
    before = api("/api/settings/event_manager")
    for k in ("degraded", "pending_reboot"):
        before.pop(k, None)
    print("  device rules:", [r["name"] for r in before.get("rules", [])], "ssid", repr(ssid))
    rt0 = rules_rt()
    check("GET /api/events/rules lists the configured rules with counters",
          set(rt0) == {r["name"] for r in before.get("rules", [])} and
          all("fired" in r and "active" in r for r in rt0.values()), str(rt0)[:160])
    g0 = groups()
    conf_ms = g0.get(args.group, {}).get("period_ms")
    check("group '%s' present at its configured rate" % args.group, conf_ms is not None, str(g0))

    MINE = ("home_slow", "epoch_slow", "beat_cd")

    def clean(em):
        em = json.loads(json.dumps(em))
        em["rules"] = [r for r in em["rules"] if r["name"] not in MINE]
        em["timers"] = [t for t in em.get("timers", []) if t["name"] not in MINE]
        return em

    def wifi_rule(rate_ms):
        return {"name": "home_slow", "on": "wifi.sta",
                "when": [{"key": "connected", "op": "==", "val": True},
                         {"key": "ssid", "op": "==", "val": ssid}],
                "do": "autopid.group", "with": {"group": args.group, "enabled": True, "period_ms": rate_ms},
                "undo": True}

    def epoch_rule(deadline, rate_ms):
        return {"name": "epoch_slow", "on": "timer.tick", "match": {"timer": "epoch_slow"},
                "when": [{"value": "${time.epoch}", "op": "<", "val": deadline}],
                "do": "autopid.group", "with": {"group": args.group, "enabled": True, "period_ms": rate_ms},
                "undo": True}

    # ---- phase B
    if ssid and not args.skip_wifi:
        em = clean(before)
        em["rules"].append(wifi_rule(9000))
        print("phase B: while-rule home_slow on ssid", repr(ssid))
        submit(em)
        rt = rules_rt()
        g = groups()
        check("home_slow fired once at boot and is active",
              rt.get("home_slow", {}).get("fired") == 1 and rt["home_slow"].get("active") is True, str(rt.get("home_slow")))
        check("group polls every 9 s while connected", g.get(args.group, {}).get("period_ms") == 9000, str(g))
    else:
        print("phase B skipped")

    # ---- phase C
    now = dev_now()
    deadline = now + args.hold
    em = clean(before)
    em["timers"].append({"name": "epoch_slow", "period_s": 3})
    em["rules"].append(epoch_rule(deadline, 7000))
    print("phase C: rule epoch_slow, device epoch", now, "deadline", deadline)
    submit(em)
    time.sleep(5)
    rt = rules_rt()
    g = groups()
    check("epoch_slow fired on the first ticks and is active",
          rt.get("epoch_slow", {}).get("fired", 0) >= 1 and rt["epoch_slow"].get("active") is True, str(rt.get("epoch_slow")))
    check("group polls every 7 s while the epoch condition holds", g.get(args.group, {}).get("period_ms") == 7000, str(g))
    remaining = deadline - dev_now() + 8
    print("  waiting", max(0, remaining), "s for the device clock to pass the deadline")
    time.sleep(max(0, remaining))
    rt = rules_rt()
    g = groups()
    check("after the deadline the re-check undid it (inactive, undo counted)",
          rt.get("epoch_slow", {}).get("active") is False and rt["epoch_slow"].get("fired", 0) >= 2, str(rt.get("epoch_slow")))
    check("group back to its configured rate", g.get(args.group, {}).get("period_ms") == conf_ms, str(g))

    # ---- phase D: stacked while-rules on one group + a cooldown-gated plain rule
    if ssid and not args.skip_stack:
        hold = 45
        now = dev_now()
        deadline = now + hold
        em = clean(before)
        em["timers"] += [{"name": "epoch_slow", "period_s": 3}, {"name": "beat_cd", "period_s": 3}]
        em["rules"] += [wifi_rule(9000), epoch_rule(deadline, 7000),
                        {"name": "beat_cd", "on": "timer.tick", "match": {"timer": "beat_cd"},
                         "do": "log.note", "with": {"message": "beat"}, "cooldown_ms": 10000}]
        print("phase D: home_slow (9 s) + epoch_slow (7 s, %d s) + beat_cd (3 s tick, 10 s cooldown)" % hold)
        stats0 = api("/api/events/log").get("stats", {})
        submit(em)
        time.sleep(6)
        rt = rules_rt()
        g = groups()
        check("both while-rules armed after boot",
              rt.get("home_slow", {}).get("active") is True and rt.get("epoch_slow", {}).get("active") is True, str(rt))
        check("the group carries one of the two overrides", g.get(args.group, {}).get("period_ms") in (7000, 9000), str(g))
        remaining = deadline - dev_now() + 8
        print("  waiting", max(0, remaining), "s for the epoch deadline")
        time.sleep(max(0, remaining))
        rt = rules_rt()
        g = groups()
        check("epoch undo restored the CONFIGURED rate, not the WiFi rule's override (documented)",
              rt.get("epoch_slow", {}).get("active") is False and g.get(args.group, {}).get("period_ms") == conf_ms, str((rt.get("epoch_slow"), g)))
        check("the WiFi rule stays armed (its own undo needs a WiFi drop)", rt.get("home_slow", {}).get("active") is True, str(rt.get("home_slow")))
        up = up_s(api("/api/status"))
        beat = rt.get("beat_cd", {}).get("fired", 0)
        expect_max = up // 10 + 2
        stats = api("/api/events/log").get("stats", {})
        check("cooldown held the 3 s beat to about one run per 10 s (%d fired in %d s, suppressed grew)" % (beat, up),
              1 <= beat <= expect_max and stats.get("suppressed", 0) >= 1, str(stats))
    else:
        print("phase D skipped")

    # ---- restore
    print("restoring the device's event_manager settings")
    submit(before)
    after = api("/api/settings/event_manager")
    check("rules restored", [r["name"] for r in after.get("rules", [])] == [r["name"] for r in before.get("rules", [])] and
          after.get("timers", []) == before.get("timers", []))
    check("group at its configured rate again", groups().get(args.group, {}).get("period_ms") == conf_ms)
    print("RULES BENCH " + ("FAIL: " + ", ".join(fails) if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
