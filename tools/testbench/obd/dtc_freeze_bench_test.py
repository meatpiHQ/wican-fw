#!/usr/bin/env python3
"""Freeze-frame (OBD mode 02) end-to-end bench — TASK_dtc §14.

Topology: the ECU-sim box (REST at 192.168.8.1 from the PC over its
NCM link) is the vehicle — its mode 02 serves frame 0 while a DTC is
stored (PID 02 = DTCFRZF, other PIDs = frozen snapshot of the live
mode-01 value). DUT driven over HTTP via rpi001.

Legs:
  1. REST-set P0301 + RPM 3000 → OBD scan → report.freeze present:
     dtc == P0301, ecu == 7E8, params carry EngineRPM == 3000 (decode
     round-trip through the std table) + a plausible value set
  2. sim DTCs cleared → rescan → stored empty AND freeze absent
     (no codes = no frame; the engine never even asks)
  3. dtc_freeze=false + P0301 again → stored present, freeze absent
     (the new knob gates the capture)
  4. restore settings + sim state, faults clean

    python dtc_freeze_bench_test.py
Expected final line: DTC FREEZE BENCH PASS
"""
import json
import subprocess
import sys
import time
import urllib.request

SIM = "http://192.168.8.1"

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def pi(cmd, stdin=None, timeout=120):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "rpi001", cmd],
                       input=stdin, capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout)
    return p.stdout


def dut_ip():
    return pi("sudo cat /var/lib/NetworkManager/dnsmasq-wint0.leases "
              "/var/lib/NetworkManager/dnsmasq-wtest0.leases 2>/dev/null"
              " | sort -n | tail -1 | awk '{print $3}'").strip()


def api(ip, path, method="GET", body=None):
    if body is None:
        out = pi(f"curl -s -m 10 -X {method} http://{ip}{path}")
    else:
        out = pi(f"curl -s -m 10 -X {method} -H 'Content-Type: "
                 f"application/json' -d @- http://{ip}{path}",
                 stdin=json.dumps(body))
    try:
        return json.loads(out)
    except ValueError:
        return {}


def sim_api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(SIM + path, data=data, method=method)
    with urllib.request.urlopen(req, timeout=8) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip().startswith("{") else t


def sim_set_dtcs(stored, mil=True):
    sim_api("/api/ecu/dtcs", "POST",
            {"mil_on": mil,
             "dtcs": {"stored": stored, "pending": [], "permanent": []}})


def submit_and_wait(ip):
    pi(f"curl -s -m 8 -X POST http://{ip}/api/settings/submit")
    time.sleep(8)
    for i in range(40):
        nip = dut_ip()
        if nip and api(nip, "/api/status").get("version"):
            time.sleep(4)
            return nip
        if i % 4 == 3:                    # liveness during the reboot wait
            print(f"  waiting for the DUT to come back… "
                  f"(~{8 + (i + 1) * 3} s)", flush=True)
        time.sleep(3)
    return None


def scan(ip, timeout=60):
    api(ip, "/api/autopid/dtc/scan", "POST", {})
    end = time.time() + timeout
    while time.time() < end:
        g = api(ip, "/api/autopid/dtc")
        if isinstance(g, dict) and g.get("report") and \
                not g.get("scanning"):
            return g.get("report", {})
        time.sleep(0.5)
    return {}


def scan_retry(ip, tries=5):
    r = {}
    for i in range(tries):
        r = scan(ip)
        if r.get("valid") is True:
            return r
        print(f"  (scan attempt {i + 1}: {r.get('error', 'no report')})")
        time.sleep(5)
    return r


def main():
    ip = dut_ip()
    print("DUT", ip)

    if not api(ip, "/api/status").get("version"):
        print("FATAL: DUT unreachable")
        return 1

    try:
        sim_api("/api/ecu/dtcs")
    except Exception as e:
        print("FATAL: ECU sim unreachable at", SIM, e)
        return 1

    ap_before = api(ip, "/api/settings/autopid")
    ap_before.pop("degraded", None)
    ap_before.pop("pending_reboot", None)

    # ---- configure: OBD protocol, freeze ON (the default — set
    # explicitly so the leg is self-describing) ----
    sim_set_dtcs(["P0301"], mil=True)
    sim_api("/api/ecu/pids/01/0C", "POST", {"rpm": 3000})
    cfg = dict(ap_before)
    cfg.update({"enabled": False, "dtc_enabled": True,
                "dtc_allow_clear": False, "dtc_pending": False,
                "dtc_permanent": False, "dtc_protocol": "obd",
                "dtc_freeze": True,
                "dtc_init": "ATS1;ATST96;atsp6;ATSH7DF",
                "dtc_rxheader": ""})
    api(ip, "/api/settings/autopid", "PUT", cfg)
    print("configured (freeze on); rebooting…")
    ip = submit_and_wait(ip)
    if ip is None:
        print("FATAL: DUT did not come back")
        return 1
    print("  back; 15 s obd_chip boot provisioning…", flush=True)
    time.sleep(15)  # obd_chip boot provisioning

    # ---- leg 1: freeze frame captured + decoded ----
    print("leg 1/4: scan with freeze ON (mode 03 + mode 02 frame 0)…",
          flush=True)
    r = scan_retry(ip)
    check("leg1 scan valid", r.get("valid") is True, json.dumps(r)[:120])
    check("leg1 stored P0301", r.get("stored") == ["P0301"],
          r.get("stored"))
    fz = r.get("freeze") or {}
    check("leg1 freeze present", bool(fz), json.dumps(r)[:200])
    check("leg1 DTCFRZF == P0301", fz.get("dtc") == "P0301", fz.get("dtc"))
    check("leg1 freeze ecu 7E8", fz.get("ecu") == "7E8", fz.get("ecu"))
    params = fz.get("params") or {}
    rpm = (params.get("EngineRPM") or {}).get("value")
    check("leg1 frozen EngineRPM == 3000",
          rpm is not None and abs(rpm - 3000.0) < 1.0, rpm)
    check("leg1 a plausible value set (>= 4 params)", len(params) >= 4,
          sorted(params.keys()))

    print("PROGRESS 1/4", flush=True)

    # ---- leg 2: no stored codes -> no freeze in the fresh report ----
    print("leg 2/4: DTCs cleared on the sim — rescan must drop the "
          "freeze…", flush=True)
    sim_api("/api/ecu/dtcs", "DELETE")
    r = scan_retry(ip)
    check("leg2 rescan empty", r.get("valid") is True and
          r.get("stored") == [], r.get("stored"))
    check("leg2 freeze absent", "freeze" not in r,
          json.dumps(r.get("freeze", None))[:80])

    print("PROGRESS 2/4", flush=True)

    # ---- leg 3: dtc_freeze=false gates the capture ----
    print("leg 3/4: dtc_freeze=false must gate the capture…", flush=True)
    sim_set_dtcs(["P0301"], mil=True)
    cfg.update({"dtc_freeze": False})
    api(ip, "/api/settings/autopid", "PUT", cfg)
    print("freeze off; rebooting…")
    ip = submit_and_wait(ip)
    print("  back; 15 s obd_chip boot provisioning…", flush=True)
    time.sleep(15)

    r = scan_retry(ip)
    check("leg3 stored P0301 (codes still scanned)",
          r.get("stored") == ["P0301"], r.get("stored"))
    check("leg3 freeze absent (knob off)", "freeze" not in r,
          json.dumps(r.get("freeze", None))[:80])

    print("PROGRESS 3/4", flush=True)

    # ---- leg 4: restore ----
    print("leg 4/4: restore original config…", flush=True)
    sim_api("/api/ecu/dtcs", "DELETE")
    api(ip, "/api/settings/autopid", "PUT", ap_before)
    print("restoring; rebooting…")
    ip = submit_and_wait(ip)
    f = api(ip, "/api/faults")
    check("restore + faults clean", f.get("faults") == [], f)
    print("PROGRESS 4/4", flush=True)

    if fails:
        print("DTC FREEZE BENCH FAIL: " + ", ".join(fails))
        return 1
    print("DTC FREEZE BENCH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
