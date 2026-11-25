#!/usr/bin/env python3
"""AutoPID group default bench  ->  AUTOPID GROUP DEFAULT PASS / FAIL   (issue #1058)

The report (2026-11-11, a 2024 Nissan Ariya on 6.0.0-beta.7): two groups
stored with `"enabled_default": false`, switched on by rules when the car
starts. At every boot, and 0.5 s after every `PUT /api/autopid/config`, both
groups polled anyway, with the car off: on that car a body module asked while
the car sleeps stores CAN trouble codes. The parser read a group's starting
state from `"enabled"` only; the web UI and the setup page write
`"enabled_default"`. Fixed by meatpi-components pull request #17 (Jeff Munk):
either word `false` keeps the group off.

The bench stores four groups, one row each, every row a request the ECU
simulator answers:
  off_ui    "enabled_default": false   the issue's words: must stay off
  off_doc   "enabled": false           the README's word: off before and after
  on_ui     "enabled_default": true    what the web UI writes: polls
  on_none   neither word               polls
A row is judged by the time of its last value (`ts_us` in `GET /api/autopid`),
so "off" means: not asked once in the watch time, while the rows of the two
groups that are on were asked in that same time.

Legs: L0 preflight (DUT, tables and settings saved, autopid on), L1 the PUT
(the groups' state and the rows, sampled from the first answer), L2 the
switch (`POST /api/autopid/group` turns off_ui on and its row is asked: the
group is whole, a rule can start it; the same PUT again turns it off), L3 the
restart (the groups' state and the rows from the first answer after the
boot), L4 restore (the tables, the settings), then no E line and no
unexpected restart.

usage: autopid_group_default_bench.py [--dut localhost:18081] [--watch 12]
                                      [--boot-watch 45] [--out DIR]
  The DUT through an ssh tunnel (`ssh -N -L 18081:<dut>:80 rpi001`) or by
  address.
Needs: the main firmware on the DUT on the bench AP, the ECU simulator
answering 7DF, the PSU above the sleep voltage. ~2.5 min, one planned
restart (two more when autopid was off at the start).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

# group -> (the words of its entry, its row's name, the request)
GROUPS = [
    ("off_ui", {"enabled_default": False}, "GrpOffUi", "0104", "B3*100/255"),
    ("off_doc", {"enabled": False}, "GrpOffDoc", "0105", "B3-40"),
    ("on_ui", {"enabled_default": True}, "GrpOnUi", "010C", "[B3:B4]*0.25"),
    ("on_none", {}, "GrpOnNone", "010D", "B3"),
]
OFF = ("off_ui", "off_doc")
ON = ("on_ui", "on_none")
ROW = {g: name for g, _, name, _, _ in GROUPS}


def http(base, method, path, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e)


def get_json(base, path, timeout=30):
    st, body = http(base, "GET", path, timeout=timeout)
    try:
        return st, json.loads(body)
    except ValueError:
        return st, None


def table():
    return {"groups": [dict({"name": g, "period_ms": 1000}, **words)
                       for g, words, _, _, _ in GROUPS],
            "pids": [{"name": name, "type": "custom", "cmd": cmd, "group": g,
                      "parameters": [{"name": name, "expression": expr}]}
                     for g, _, name, cmd, expr in GROUPS],
            "filters": []}


def submit_and_wait(base, window=150):
    st, body = http(base, "POST", "/api/settings/submit", {})
    print("  submit -> %s %s" % (st, body.strip()[:80]))
    if not (st == 200 and '"reboot":true' in body.replace(" ", "")):
        return st == 200
    t0 = time.time()
    seen_down = False
    while time.time() - t0 < window:
        time.sleep(2)
        st, _ = http(base, "GET", "/api/info", timeout=5)
        if st != 200:
            seen_down = True
        elif seen_down:
            print("  back after %.0f s" % (time.time() - t0))
            time.sleep(8)
            return True
    print("  the DUT did not come back within %d s" % window)
    return False


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print("  [%s] %s%s" % ("ok" if ok else "FAIL", name,
                               (": " + detail) if detail else ""), flush=True)
        return bool(ok)

    def failed(self):
        return [n for n, ok, _ in self.rows if not ok]


def sample(base):
    """One look at GET /api/autopid: each group's state, each row's last
    update, the poll counters. None when the DUT did not answer."""
    st, ap = get_json(base, "/api/autopid", timeout=6)
    if st != 200 or not isinstance(ap, dict):
        return None
    groups = {g.get("name"): g.get("enabled") for g in ap.get("groups", [])}
    ts = {p.get("name"): p.get("ts_us") for p in ap.get("params", [])
          if p.get("value") is not None}
    return {"t": time.time(), "groups": groups, "ts": ts,
            "polls_ok": (ap.get("stats") or {}).get("polls_ok"),
            "polls_failed": (ap.get("stats") or {}).get("polls_failed")}


def watch(base, seconds, log, tag, step=0.4):
    """Samples for `seconds`, from the first answer on."""
    out = []
    t_end = None
    t_give_up = time.time() + seconds + 120
    while time.time() < t_give_up:
        s = sample(base)
        if s is not None:
            if t_end is None:
                t_end = time.time() + seconds
            out.append(s)
            log.write(json.dumps(dict(s, leg=tag)) + "\n")
        if t_end is not None and time.time() >= t_end:
            break
        time.sleep(step)
    return out


def judge(c, samples, leg, expect_on=ON, expect_off=OFF):
    """The groups' state in every sample, and which rows were asked."""
    if not c.add("%s: the DUT answered through the watch" % leg,
                 len(samples) >= 5, "%d samples" % len(samples)):
        return
    for g in expect_off:
        seen_on = [i for i, s in enumerate(samples)
                   if s["groups"].get(g) is not False]
        c.add("%s: group %s is off in every sample" % (leg, g), not seen_on,
              "on or missing in %d of %d samples, first at %.1f s"
              % (len(seen_on), len(samples),
                 samples[seen_on[0]]["t"] - samples[0]["t"])
              if seen_on else "%d samples" % len(samples))
    for g in expect_on:
        c.add("%s: group %s is on" % (leg, g),
              samples[-1]["groups"].get(g) is True,
              "reported %r" % samples[-1]["groups"].get(g))
    first, last = samples[0]["ts"], samples[-1]["ts"]
    for g in expect_off:
        moved = [s["ts"].get(ROW[g]) for s in samples
                 if s["ts"].get(ROW[g]) != first.get(ROW[g])]
        c.add("%s: the row of %s was not asked once" % (leg, g), not moved,
              "%s updated (ts_us %s -> %s)" % (ROW[g], first.get(ROW[g]),
                                                moved[-1])
              if moved else "%s: no value in the watch" % ROW[g])
    for g in expect_on:
        c.add("%s: the row of %s was asked" % (leg, g),
              last.get(ROW[g]) is not None and
              last.get(ROW[g]) != first.get(ROW[g]),
              "%s ts_us %s -> %s" % (ROW[g], first.get(ROW[g]),
                                     last.get(ROW[g])))


def main():
    args = sys.argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    dut = opt("--dut", "localhost:18081")
    base = "http://" + dut.rstrip("/")
    seconds = float(opt("--watch", 12))
    boot_seconds = float(opt("--boot-watch", 45))
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = opt("--out", os.path.join(REPO, "test-reports", "logs",
                                    "autopid_group_default_" + ts))
    os.makedirs(out, exist_ok=True)
    print("AUTOPID GROUP DEFAULT bench: dut=%s watch=%.0f s boot watch=%.0f s "
          "out=%s" % (dut, seconds, boot_seconds, out), flush=True)
    c = Checks()
    saved_cfg = saved_settings = None
    settings_changed = False
    status0 = {}
    log = open(os.path.join(out, "samples.jsonl"), "w")

    try:
        print("L0 preflight")
        st, info = get_json(base, "/api/info")
        if not c.add("dut answers", st == 200 and info,
                     "%s %s %s" % (st, (info or {}).get("fw_build"),
                                   (info or {}).get("partition"))):
            raise SystemExit(1)
        st, status0 = get_json(base, "/api/status")
        status0 = status0 or {}
        c.add("status read", st == 200 and "boot_count" in status0,
              "boot_count %s, unexpected_resets %s" % (
                  status0.get("boot_count"), status0.get("unexpected_resets")))
        st, saved_cfg = get_json(base, "/api/autopid/config")
        c.add("tables saved", st == 200 and saved_cfg is not None)
        st, saved_settings = get_json(base, "/api/settings/autopid")
        c.add("settings saved", st == 200 and saved_settings is not None)
        with open(os.path.join(out, "saved_config.json"), "w") as f:
            json.dump(saved_cfg, f)
        with open(os.path.join(out, "saved_settings.json"), "w") as f:
            json.dump(saved_settings, f)
        if c.failed():
            raise SystemExit(1)
        new_settings = dict(saved_settings)
        for k in ("degraded", "pending_reboot"):
            new_settings.pop(k, None)
        want = {"enabled": True, "custom_enabled": True}
        if any(new_settings.get(k) != v for k, v in want.items()):
            new_settings.update(want)
            st, body = http(base, "PUT", "/api/settings/autopid", new_settings)
            c.add("autopid switched on (settings PUT)", st == 200,
                  "%s %s" % (st, body.strip()[:100]))
            settings_changed = True
            c.add("the DUT rebooted and is back", submit_and_wait(base))
            st, status0 = get_json(base, "/api/status")
            status0 = status0 or {}
        if c.failed():
            raise SystemExit(1)

        print("L1 the PUT: four groups, two of them stored off")
        st, body = http(base, "PUT", "/api/autopid/config", table())
        if not c.add("tables accepted", st == 200,
                     "%s %s" % (st, body.strip()[:100])):
            raise SystemExit(1)
        judge(c, watch(base, seconds, log, "put"), "PUT")

        print("L2 the switch: off_ui turned on at runtime, then the PUT again")
        st, body = http(base, "POST", "/api/autopid/group",
                        {"name": "off_ui", "enabled": True})
        c.add("POST /api/autopid/group off_ui on", st == 200,
              "%s %s" % (st, body.strip()[:80]))
        judge(c, watch(base, seconds, log, "switch"), "switched on",
              expect_on=ON + ("off_ui",), expect_off=("off_doc",))
        st, body = http(base, "PUT", "/api/autopid/config", table())
        c.add("the same tables accepted again", st == 200,
              "%s %s" % (st, body.strip()[:100]))
        judge(c, watch(base, seconds, log, "put2"), "PUT again")

        print("L3 the restart")
        st, body = http(base, "POST", "/api/restart", {})
        c.add("restart asked", st in (200, 0), "%s %s" % (st, body.strip()[:60]))
        t0 = time.time()
        back = None
        while time.time() - t0 < 150:
            time.sleep(1.0)
            st, now = get_json(base, "/api/status", timeout=4)
            if st == 200 and now and \
                    now.get("boot_count") != status0.get("boot_count"):
                back = now
                break
        if not c.add("the DUT restarted and answers", back is not None,
                     "boot_count %s -> %s after %.0f s, uptime %s" % (
                         status0.get("boot_count"),
                         (back or {}).get("boot_count"), time.time() - t0,
                         (back or {}).get("uptime"))):
            raise SystemExit(1)
        judge(c, watch(base, boot_seconds, log, "boot"), "after the restart")
    finally:
        print("L4 restore")
        if saved_cfg is not None:
            st, body = http(base, "PUT", "/api/autopid/config", saved_cfg)
            c.add("tables restored", st == 200,
                  "%s %s" % (st, body.strip()[:80]))
        if settings_changed and saved_settings is not None:
            restore = dict(saved_settings)
            for k in ("degraded", "pending_reboot"):
                restore.pop(k, None)
            st, body = http(base, "PUT", "/api/settings/autopid", restore)
            c.add("settings restored", st == 200,
                  "%s %s" % (st, body.strip()[:80]))
            c.add("the DUT rebooted and is back (restore)",
                  submit_and_wait(base))
        st, end = get_json(base, "/api/status")
        end = end or {}
        health = end.get("health") or {}
        c.add("no E line since the boot, no fault, no unexpected restart",
              st == 200 and health.get("log_errors") == 0 and
              health.get("faults") == 0 and
              end.get("unexpected_resets") == status0.get("unexpected_resets"),
              "log_errors %s, faults %s, unexpected_resets %s -> %s" % (
                  health.get("log_errors"), health.get("faults"),
                  status0.get("unexpected_resets"),
                  end.get("unexpected_resets")))
        log.close()

    failed = c.failed()
    print("checks: %d, failed: %d" % (len(c.rows), len(failed)))
    if failed:
        print("AUTOPID GROUP DEFAULT FAIL: " + "; ".join(failed))
        return 1
    print("AUTOPID GROUP DEFAULT PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
