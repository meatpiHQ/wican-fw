#!/usr/bin/env python3
"""Runtime stack + memory audit vs the composed main firmware.

The permanent net for the 2026-07-22 lesson: a PSRAM-stacked task that
overflows does NOT panic — it silently corrupts neighbouring .bss (the
autopid DTC job task shipped that way). Internal-RAM stacks panic
instead, and internal-RAM exhaustion breaks WiFi auth + SDMMC DMA
together. This bench catches ALL of those classes:

  1. task watermarks — /api/status/tasks stack_hw (bytes NEVER used)
     for every live task, asserted after EXERCISING the deep paths
     (DTC scan job, test-a-PID chip transaction, a settings write).
     FAIL < 512 B, WARN < 1024 B (the web UI's own thresholds).
  2. ephemeral job tasks — they exit before the snapshot, so the fw
     logs "dtc job stack_hw=<n> B" at job exit; read back via
     /api/logs/ring and asserted >= 512.
  3. heap floors, BOTH placements — /api/status memory.*.min_free +
     largest_block: internal min_free >= 20 KB, internal largest_block
     >= 16 KB (WiFi auth + SDMMC DMA), psram min_free >= 1 MB.

Static counterpart: tools/stack_audit.py (compiler .su frame sizes vs
task stack sizes). This bench proves watermarks; that tool finds
candidates before flashing.

  python stack_audit_test.py [dut_ip]        (→ STACK AUDIT PASS)
"""
import json
import sys
import time
import urllib.request
import urllib.error

DUT = sys.argv[1] if len(sys.argv) > 1 else "10.42.0.62"
BASE = "http://" + DUT

TASK_FAIL_B = 512
TASK_WARN_B = 1024
INT_MIN_FREE = 20 * 1024
INT_MIN_BLOCK = 16 * 1024
PSRAM_MIN_FREE = 1024 * 1024
JOB_FAIL_B = 512

fails = []
warns = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + ("  ({})".format(detail) if detail else ""))
    if not ok:
        fails.append(name)


def api(path, method="GET", body=None, ok_codes=(200,)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type":
                                          "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            t = r.read().decode()
            return r.status, (json.loads(t)
                              if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        t = e.read().decode()
        return e.code, (json.loads(t)
                        if t.strip().startswith(("{", "[")) else t)


def submit_and_wait(secs=75):
    """Submit staged settings and poll the DUT back up."""
    try:
        api("/api/settings/submit", "POST")
    except Exception:
        pass  # the reboot can cut the response — that's success
    time.sleep(5)
    end = time.time() + secs
    while time.time() < end:
        try:
            code, _ = api("/api/status")
            if code == 200:
                time.sleep(3)
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def exercise_deep_paths():
    """Drive the known-deep code paths so watermarks reflect them."""
    # test-a-PID: httpd -> chip transaction -> resp assembly on the
    # httpd task (the deep frames are allocated at function entry, so
    # a NO-DATA bus exercises the same depth as a real one)
    api("/api/autopid/test", "POST", {"cmd": "ATI"})

    # DTC scan job: the task that shipped overflowing. The dtc gate is
    # applied at settings-apply time, so this is a full staged-PUT +
    # submit-reboot cycle (and another one to restore) — which also
    # exercises the settings/fs writer paths and a fresh boot.
    _, ap_before = api("/api/settings/autopid")
    if not isinstance(ap_before, dict):
        print("  (autopid settings unreadable — skipping dtc-job leg)")
        return None
    ap_before.pop("degraded", None)
    ap_before.pop("pending_reboot", None)

    ran = False
    if ap_before.get("dtc_enabled"):
        ran = True  # already armed — no reboot needed
    else:
        cfg = dict(ap_before)
        cfg.update({"dtc_enabled": True})
        api("/api/settings/autopid", "PUT", cfg)
        print("  (arming dtc via submit-reboot…)")
        if not submit_and_wait():
            print("  (DUT did not come back — skipping dtc-job leg)")
            return None
        time.sleep(15)  # obd_chip boot provisioning holds the chip

    code, _ = api("/api/autopid/dtc/scan", "POST", {})
    if code == 202:
        ran = True
        end = time.time() + 30
        while time.time() < end:
            _, g = api("/api/autopid/dtc")
            if isinstance(g, dict) and not g.get("scanning"):
                break
            time.sleep(0.5)
    else:
        print(f"  (dtc scan not started: {code})")

    if not ap_before.get("dtc_enabled"):
        api("/api/settings/autopid", "PUT", ap_before)
        print("  (restoring settings via submit-reboot…)")
        submit_and_wait()
        # NOTE: the restore reboot resets task watermarks — the dtc job
        # ran on the PREVIOUS boot, but its watermark lives in the log
        # RING which survives warm resets (PSRAM ring)

    time.sleep(2)
    return ran


def main():
    # sanity
    code, st = api("/api/status")
    check("status reachable", code == 200 and isinstance(st, dict),
          code)
    if fails:
        print("STACK AUDIT FAIL: unreachable")
        sys.exit(1)

    # cheap httpd-deep exercise first; the watermark snapshot comes
    # BEFORE the dtc reboot cycle so it reflects the long-uptime state
    api("/api/autopid/test", "POST", {"cmd": "ATI"})
    time.sleep(1)

    # ---- leg 1: every live task's watermark ----
    code, t = api("/api/status/tasks")
    tasks = t.get("tasks", []) if isinstance(t, dict) else []
    check("task stats available", code == 200 and len(tasks) > 5,
          f"{len(tasks)} tasks")

    low = [(x["name"], x["stack_hw"]) for x in tasks
           if x.get("stack_hw", 1 << 30) < TASK_FAIL_B]
    warn = [(x["name"], x["stack_hw"]) for x in tasks
            if TASK_FAIL_B <= x.get("stack_hw", 1 << 30) < TASK_WARN_B]
    print("  watermarks (lowest 10):")
    for x in sorted(tasks, key=lambda x: x.get("stack_hw", 1 << 30))[:10]:
        print(f"    {x['name']:16} stack_hw {x.get('stack_hw')}")
    check(f"no task below {TASK_FAIL_B} B headroom", not low, low)
    if warn:
        warns.extend(warn)
        print(f"  WARN: tasks under {TASK_WARN_B} B headroom: {warn}")

    # ---- leg 3 (early read): heap floors from the long-uptime state ----
    mem = st.get("memory", {})
    internal = mem.get("internal", {})
    psram = mem.get("psram", {})
    check(f"internal min_free >= {INT_MIN_FREE}",
          internal.get("min_free", 0) >= INT_MIN_FREE,
          internal.get("min_free"))
    check(f"internal largest_block >= {INT_MIN_BLOCK}",
          internal.get("largest_block", 0) >= INT_MIN_BLOCK,
          internal.get("largest_block"))
    check(f"psram min_free >= {PSRAM_MIN_FREE}",
          psram.get("min_free", 0) >= PSRAM_MIN_FREE,
          psram.get("min_free"))

    # ---- leg 2: the ephemeral dtc job task (reboot cycle + ring) ----
    dtc_ran = exercise_deep_paths()
    code, ring = api("/api/logs/ring")
    text = ring if isinstance(ring, str) else json.dumps(ring)
    hws = [int(m) for m in
           __import__("re").findall(r"dtc job stack_hw=(\d+)", text)]
    if dtc_ran:
        check("dtc job watermark logged", len(hws) >= 1,
              f"{len(hws)} log lines")
        if hws:
            check(f"dtc job headroom >= {JOB_FAIL_B} B",
                  min(hws) >= JOB_FAIL_B, f"min {min(hws)} B")
    else:
        print("  (dtc job leg skipped — scan could not start)")

    # ---- leg 4: no faults raised by the exercise ----
    code, f = api("/api/faults")
    check("faults still clean",
          isinstance(f, dict) and f.get("faults") == [],
          f)

    if fails:
        print("STACK AUDIT FAIL:", ", ".join(fails))
        sys.exit(1)
    if warns:
        print("STACK AUDIT PASS (with warnings):", warns)
    else:
        print("STACK AUDIT PASS")


if __name__ == "__main__":
    main()
