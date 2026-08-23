#!/usr/bin/env python3
"""End-to-end bench for the DTC feature (TASK_dtc.md §10).

Topology: DUT over USB-NCM (192.168.82.1), MIC3624 on the OBD bus with
the PCAN. The python ECU (pcan_obd_ecu.py, spawned here) answers at
0x7E9 — the DUT targets it with dtc_rxheader "7E9" so the hardware
bench ECU box at 0x7E8 never collides.

Legs:
  0. gates: fresh dtc settings -> scan/clear = 403, no bus traffic
  1. configure: dtc_enabled + allow_clear + init/rxheader, polling OFF
     (proves DTC works without PID polling) + an event rule on
     autopid.dtc -> log.note; submit-reboot
  2. scan #1: stored/pending/permanent/MIL match the ECU state
     (first scan -> new stays empty by design)
  3. clear negative: if_only with a partial list -> cleared=false
  4. clear positive: if_only with the full list -> cleared=true, after=0
  5. scan #2: stored empty, MIL off, permanent SURVIVES the clear
  6. new-code: ECU restarts with a fresh code -> scan -> new=[P0300],
     autopid.dtc event in /api/events/log + the rule fired
  6c. multi-ECU (v2): rxheader cleared, two python responders (7E9+7EA)
     -> merged stored (dedup pinned by a shared P0420), ecus >= 2,
     mil_count summed
  7. restore: original autopid + event_manager settings, submit-reboot

Run on the PC (IDF venv python; needs python-can):
  python dtc_bench_test.py [dut_ip] [pcan_channel]
"""
import json
import subprocess
import os
import sys
import time
import urllib.request
import urllib.error

DUT = sys.argv[1] if len(sys.argv) > 1 else "192.168.82.1"
PCAN = sys.argv[2] if len(sys.argv) > 2 else "PCAN_USBBUS2"
BASE = "http://" + DUT
PY = sys.executable
ACTORS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "actors")

fails = []


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
        with urllib.request.urlopen(req, timeout=10) as r:
            t = r.read().decode()
            return r.status, (json.loads(t)
                              if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def wait_up(secs=60):
    """Poll DOWN then UP (the pre-reboot-instance race, TESTING.md)."""
    start = time.time()
    down_seen = False
    while time.time() - start < secs:
        try:
            api("/api/status")
            if down_seen:
                return True
            if time.time() - start > 12:
                return True  # rebooted during the pre-poll sleep
        except Exception:
            down_seen = True
        time.sleep(1.5)
    return False


def submit_and_wait():
    try:
        api("/api/settings/submit", "POST")
    except Exception:
        pass  # the reboot can cut the submit response — that's success
    time.sleep(3)
    if not wait_up():
        print("FATAL: DUT did not come back after submit")
        sys.exit(1)
    time.sleep(2)


def scan_and_wait(timeout=30):
    code, r = api("/api/autopid/dtc/scan", "POST", {})
    if code != 202:
        return code, None
    end = time.time() + timeout
    while time.time() < end:
        _, g = api("/api/autopid/dtc")
        if not g.get("scanning"):
            return code, g
        time.sleep(0.5)
    return code, None


def spawn_ecu(stored, pending="", permanent="", secs=300, extra=None):
    p = subprocess.Popen(
        [PY, "-u", os.path.join(ACTORS, "pcan_obd_ecu.py"),
         str(secs), "--stored", stored,
         "--pending", pending, "--permanent", permanent, "--pcan", PCAN]
        + (extra or []),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.5)  # let the PCAN channel come up
    if p.poll() is not None:
        print("FATAL: ECU sim died:", p.stdout.read())
        sys.exit(1)
    return p


def stop_ecu(p):
    # NEVER force-kill mid-transaction (wedges the PEAK driver) — the
    # sim exits on its own timer; terminate + wait is the gentle path.
    p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
    time.sleep(1.0)


def main():
    # snapshot config for restore
    _, ap_before = api("/api/settings/autopid")
    _, em_before = api("/api/settings/event_manager")
    for cfg in (ap_before, em_before):
        cfg.pop("degraded", None)
        cfg.pop("pending_reboot", None)

    # enforce the leg-0 precondition: dtc at factory defaults
    if ap_before.get("dtc_enabled") or ap_before.get("dtc_allow_clear"):
        print("dtc not at defaults — resetting first…")
        clean = dict(ap_before)
        clean.update({"dtc_enabled": False, "dtc_allow_clear": False,
                      "dtc_init": "", "dtc_rxheader": ""})
        ap_before = clean
        api("/api/settings/autopid", "PUT", clean)
        submit_and_wait()

    # ---- leg 0: default-off gates (fresh v3 defaults) ----
    _, g = api("/api/autopid/dtc")
    check("leg0 GET works while disabled",
          g.get("enabled") is False and g["report"]["valid"] is False,
          json.dumps({k: g[k] for k in ("enabled", "allow_clear")}))
    code, r = api("/api/autopid/dtc/scan", "POST", {})
    check("leg0 scan refused 403 while disabled", code == 403, code)
    code, r = api("/api/autopid/dtc/clear", "POST",
                  {"confirm": True, "mode": "always"})
    check("leg0 clear refused 403 while disabled", code == 403, code)

    # ---- leg 1: configure (polling OFF — DTC-without-polling proof) ----
    ap_cfg = dict(ap_before)
    ap_cfg.update({
        "enabled": False,
        "dtc_enabled": True,
        "dtc_allow_clear": True,
        "dtc_pending": True,
        "dtc_permanent": True,
        # atsp6/ATM1 on purpose: the EEPROM guard must rewrite them to
        # ATTP6/ATM0 before the chip — leg 2's real scan results prove
        # the sanitized init still lands the protocol/header state
        "dtc_init": "ATS1;ATH0;ATST96;atsp6;ATM1;ATSH7DF",
        "dtc_rxheader": "7E9",
    })
    api("/api/settings/autopid", "PUT", ap_cfg)

    em_cfg = dict(em_before)
    em_cfg["enabled"] = True
    rules = [x for x in em_cfg.get("rules", [])
             if x.get("name") != "bench_dtc"]
    rules.append({"name": "bench_dtc", "on": "autopid.dtc",
                  "do": "log.note",
                  "with": {"message": "new DTC ${code} (${status})"}})
    em_cfg["rules"] = rules
    api("/api/settings/event_manager", "PUT", em_cfg)
    print("configured (dtc on, polling off, bench_dtc rule); rebooting…")
    submit_and_wait()

    ecu = spawn_ecu(stored="P0420,P0171", pending="P0301",
                    permanent="P0555")
    try:
        # ---- leg 2: scan #1 ----
        # obd_chip boot provisioning holds the chip ~15 s after reboot
        # (log: "autopid: started" at t=16 s) — retry until it's ready
        code, g = 202, None
        for attempt in range(5):
            code, g = scan_and_wait()
            r = (g or {}).get("report", {})
            if code == 202 and r.get("valid"):
                break
            print(f"  (scan attempt {attempt + 1}: chip not ready yet — "
                  f"{r.get('error', 'no report')})")
            time.sleep(5)
        r = (g or {}).get("report", {})
        check("leg2 scan 202 + finished", code == 202 and g is not None,
              json.dumps(r)[:160])
        check("leg2 stored = P0420+P0171",
              sorted(r.get("stored", [])) == ["P0171", "P0420"],
              r.get("stored"))
        check("leg2 pending = P0301", r.get("pending") == ["P0301"],
              r.get("pending"))
        check("leg2 permanent = P0555", r.get("permanent") == ["P0555"],
              r.get("permanent"))
        check("leg2 MIL on + count 2",
              r.get("mil") is True and r.get("mil_count") == 2,
              (r.get("mil"), r.get("mil_count")))
        check("leg2 first scan emits no new codes", r.get("new") == [],
              r.get("new"))

        # ---- leg 3: clear negative (if_only, partial list) ----
        code, r = api("/api/autopid/dtc/clear", "POST",
                      {"confirm": True, "codes": "P0420",
                       "mode": "if_only"})
        check("leg3 if_only partial -> NOT cleared",
              code == 200 and r.get("ok") and r.get("cleared") is False
              and r.get("before") == 2, r)

        # ---- leg 4: clear positive (if_only, full list) ----
        code, r = api("/api/autopid/dtc/clear", "POST",
                      {"confirm": True, "codes": "P0420,P0171",
                       "mode": "if_only"})
        check("leg4 if_only full -> CLEARED, after=0",
              code == 200 and r.get("cleared") is True
              and r.get("before") == 2 and r.get("after") == 0, r)

        # ---- leg 5: scan #2 (post-clear) ----
        code, g = scan_and_wait()
        r = (g or {}).get("report", {})
        check("leg5 stored empty + MIL off after clear",
              r.get("stored") == [] and r.get("mil") is False,
              (r.get("stored"), r.get("mil")))
        check("leg5 permanent survives mode 04",
              r.get("permanent") == ["P0555"], r.get("permanent"))
    finally:
        stop_ecu(ecu)

    # ---- leg 6: new-code detection + event + rule ----
    ecu = spawn_ecu(stored="P0300", pending="", permanent="")
    try:
        code, g = scan_and_wait()
        r = (g or {}).get("report", {})
        check("leg6 new code detected", r.get("new") == ["P0300"],
              (r.get("stored"), r.get("new")))

        _, log = api("/api/events/log")
        events = log.get("events", [])
        dtc_ev = [e for e in events
                  if e.get("source") == "autopid" and e.get("name") == "dtc"
                  and e.get("data", {}).get("code") == "P0300"]
        check("leg6 autopid.dtc event in the log", len(dtc_ev) >= 1,
              f"{len(events)} events")
        check("leg6 bench_dtc rule fired",
              any("bench_dtc" in e.get("fired", []) for e in dtc_ev),
              dtc_ev[-1].get("fired") if dtc_ev else "no event")
        scans = [e for e in events if e.get("name") == "dtc_scan"]
        check("leg6 dtc_scan events in the log", len(scans) >= 1,
              f"{len(scans)} scan events")
    finally:
        stop_ecu(ecu)

    # ---- leg 6b: test-a-PID one-shot with an AT cmd (EEPROM guard on
    # the cmd path: firmware sends ATM0, chip answers OK either way —
    # the leg proves the sanitized-copy path runs end to end) ----
    code, r = api("/api/autopid/test", "POST", {"cmd": "ATM1"})
    check("leg6b test-a-PID AT cmd ok",
          code == 200 and r.get("ok") is True and "OK" in r.get("raw", ""),
          r)

    # ---- leg 6c: multi-ECU functional scan (v2 merge, 2026-07-22) ----
    # rxheader CLEARED -> receive-all: two python responders (7E9 + 7EA)
    # answer every 7DF request. The hardware ECU-sim box at 7E8 may
    # answer too, so list assertions are SUPERSETS — but the P0420
    # overlap between the two python responders pins the dedup exactly.
    ap_multi = dict(ap_cfg)
    ap_multi["dtc_rxheader"] = ""
    api("/api/settings/autopid", "PUT", ap_multi)
    print("reconfiguring for multi-ECU (rxheader off); rebooting…")
    submit_and_wait()

    ecu = spawn_ecu(stored="P0420,P0171", pending="P0301", permanent="",
                    extra=["--resp2", "0x7EA",
                           "--stored2", "U0100,P0420"])
    try:
        code, g = 202, None
        for attempt in range(5):
            code, g = scan_and_wait()
            r = (g or {}).get("report", {})
            if code == 202 and r.get("valid"):
                break
            print(f"  (scan attempt {attempt + 1}: chip not ready yet — "
                  f"{r.get('error', 'no report')})")
            time.sleep(5)
        r = (g or {}).get("report", {})
        stored = r.get("stored", [])
        check("leg6c merged stored covers BOTH responders",
              {"P0420", "P0171", "U0100"} <= set(stored), stored)
        check("leg6c duplicate P0420 merged once",
              stored.count("P0420") == 1, stored)
        check("leg6c >= 2 ECUs in the report", r.get("ecus", 0) >= 2,
              r.get("ecus"))
        check("leg6c MIL on + counts summed",
              r.get("mil") is True and r.get("mil_count", 0) >= 4,
              (r.get("mil"), r.get("mil_count")))
        check("leg6c pending merged", "P0301" in r.get("pending", []),
              r.get("pending"))
    finally:
        stop_ecu(ecu)

    # ---- leg 7: restore ----
    api("/api/settings/autopid", "PUT", ap_before)
    api("/api/settings/event_manager", "PUT", em_before)
    print("restoring original settings; rebooting…")
    submit_and_wait()
    _, g = api("/api/autopid/dtc")
    check("leg7 dtc disabled again after restore",
          g.get("enabled") is False, g.get("enabled"))

    if fails:
        print("DTC BENCH FAIL:", ", ".join(fails))
        sys.exit(1)
    print("DTC BENCH PASS")


if __name__ == "__main__":
    main()
