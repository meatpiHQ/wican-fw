#!/usr/bin/env python3
"""End-to-end bench for the DTC databases (TASK_dtc_db.md).

Legs:
  1. upload every format family (CSV+header+quotes, JSON map, JSON
     array w/ aliases, semicolon) as four databases
  2. list reflects all four with entry counts
  3. lookup: name-order priority ("0_override" beats "generic"),
     unknown code -> null
  4. search: code prefix + description substring + paging
  5. rejects: garbage file 400, bad name 400
  6. persistence + enrichment: enable dtc + reboot (dbs reload from
     flash), ECU-sim scan -> report carries the "desc" map
  7. Berry dtc_desc()
  8. delete all -> list empty, lookup null; settings restored

Run on the PC (IDF venv python; needs python-can for leg 6):
  python dtc_db_bench_test.py [dut_ip] [pcan_channel]
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

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + ("  ({})".format(detail) if detail else ""))
    if not ok:
        fails.append(name)


def api(path, method="GET", body=None, raw=None):
    data = raw if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type":
                                          "application/octet-stream"
                                          if raw is not None
                                          else "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            t = r.read().decode()
            return r.status, (json.loads(t)
                              if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        t = e.read().decode()
        return e.code, (json.loads(t)
                        if t.strip().startswith(("{", "[")) else t)


def wait_up(secs=60):
    start = time.time()
    down_seen = False
    while time.time() - start < secs:
        try:
            api("/api/status")
            if down_seen or time.time() - start > 12:
                return True
        except Exception:
            down_seen = True
        time.sleep(1.5)
    return False


def main():
    for n in ("generic", "0_override", "mfr", "eu"):
        api("/api/autopid/dtc/db?name=" + n, "DELETE")

    # ---- leg 1: every format family ----
    csv = (b"code,description\r\n"
           b'P0420,"Catalyst System Efficiency Below Threshold, Bank 1"\r\n'
           b"P0171,System Too Lean Bank 1\r\n"
           b"P0301,Cylinder 1 Misfire Detected\r\n")
    code, r = api("/api/autopid/dtc/db?name=generic", "POST", raw=csv)
    check("leg1 CSV upload", code == 200 and r.get("entries") == 3
          and r.get("format") == "csv", r)

    jmap = json.dumps({"P0420": "KAT-Wirkungsgrad zu niedrig"}).encode()
    code, r = api("/api/autopid/dtc/db?name=0_override", "POST", raw=jmap)
    check("leg1 JSON-map upload", code == 200 and r.get("entries") == 1
          and r.get("format") == "json-map", r)

    jarr = json.dumps([
        {"dtc": "P0555", "desc": "Brake Booster Pressure Sensor"},
        {"code": "P0300-00", "description": "Random Misfire",
         "severity": 2},
    ]).encode()
    code, r = api("/api/autopid/dtc/db?name=mfr", "POST", raw=jarr)
    check("leg1 JSON-array upload (aliases + suffix)",
          code == 200 and r.get("entries") == 2
          and r.get("format") == "json-array", r)

    semi = b"U0100;Lost Communication With ECM\nC0035;Wheel Speed FL\n"
    code, r = api("/api/autopid/dtc/db?name=eu", "POST", raw=semi)
    check("leg1 semicolon upload", code == 200 and r.get("entries") == 2
          and r.get("format") == "semicolon", r)

    # ---- leg 2: list ----
    code, r = api("/api/autopid/dtc/db")
    names = sorted(d["name"] for d in r.get("dbs", []))
    check("leg2 list shows 4 dbs",
          names == ["0_override", "eu", "generic", "mfr"], names)

    # ---- leg 3: lookup + priority ----
    code, r = api("/api/autopid/dtc/lookup?codes=P0420,P0171,P9999")
    check("leg3 priority: 0_override wins for P0420",
          r.get("P0420") == "KAT-Wirkungsgrad zu niedrig", r.get("P0420"))
    check("leg3 fallthrough + unknown null",
          r.get("P0171", "").startswith("System Too Lean")
          and r.get("P9999", "x") is None, r)

    # ---- leg 4: search ----
    code, r = api("/api/autopid/dtc/db/search?q=P03")
    check("leg4 code-prefix search",
          r.get("total") == 2 and
          {i["code"] for i in r["items"]} == {"P0300", "P0301"}, r)

    code, r = api("/api/autopid/dtc/db/search?q=misfire")
    check("leg4 description substring", r.get("total") == 2, r)

    code, r = api("/api/autopid/dtc/db/search?q=&limit=3")
    total_all = r.get("total")
    code, r2 = api("/api/autopid/dtc/db/search?q=&offset=3&limit=3")
    check("leg4 paging consistent",
          total_all == 8 and len(r["items"]) == 3
          and len(r2["items"]) == 3
          and r["items"][0]["code"] != r2["items"][0]["code"],
          (total_all, len(r.get("items", [])), len(r2.get("items", []))))

    # ---- leg 5: rejects ----
    code, r = api("/api/autopid/dtc/db?name=bad", "POST",
                  raw=b"hello world\nno codes here\n")
    check("leg5 garbage file -> 400 with reason",
          code == 400 and "no valid entries" in str(r), r)
    code, r = api("/api/autopid/dtc/db?name=../evil", "POST",
                  raw=b"P0001,x\n")
    check("leg5 bad name -> 400", code == 400, code)

    # ---- leg 6: persistence (reboot) + enriched scan ----
    _, ap_before = api("/api/settings/autopid")
    ap_before.pop("degraded", None)
    ap_before.pop("pending_reboot", None)
    ap_cfg = dict(ap_before)
    ap_cfg.update({"enabled": False, "dtc_enabled": True,
                   "dtc_allow_clear": False, "dtc_pending": True,
                   "dtc_permanent": False,
                   "dtc_init": "ATS1;ATH0;ATST96;ATTP6;ATSH7DF",
                   "dtc_rxheader": "7E9"})
    api("/api/settings/autopid", "PUT", ap_cfg)
    api("/api/settings/submit", "POST")
    print("rebooting for dtc_enabled…")
    time.sleep(3)
    if not wait_up():
        print("FATAL: DUT did not come back")
        sys.exit(1)
    time.sleep(2)

    code, r = api("/api/autopid/dtc/db")
    check("leg6 dbs survive reboot (flash reload)",
          len(r.get("dbs", [])) == 4, [d["name"] for d in r.get("dbs", [])])

    ecu = subprocess.Popen(
        [PY, "-u",
         os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "actors", "pcan_obd_ecu.py"),
         "180", "--stored", "P0420,P0171",
         "--pending", "P0301", "--pcan", PCAN],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.5)
    try:
        report = {}
        for attempt in range(5):
            code, r = api("/api/autopid/dtc/scan", "POST", {})
            if code != 202:
                time.sleep(5)
                continue
            for _ in range(30):
                _, g = api("/api/autopid/dtc")
                if not g.get("scanning"):
                    break
                time.sleep(0.5)
            report = g.get("report", {})
            if report.get("valid"):
                break
            print(f"  (scan attempt {attempt + 1}: not ready)")
            time.sleep(5)

        desc = report.get("desc", {})
        check("leg6 enriched report: override desc for P0420",
              desc.get("P0420") == "KAT-Wirkungsgrad zu niedrig",
              desc.get("P0420"))
        check("leg6 enriched report: pending code described too",
              desc.get("P0301", "").startswith("Cylinder 1"),
              desc.get("P0301"))
    finally:
        ecu.terminate()
        try:
            ecu.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ecu.kill()

    # ---- leg 7: Berry ----
    code, r = api("/api/scripts/run", "POST",
                  {"src": "log(str(dtc_desc('p0171'))) "
                          "log(str(dtc_desc('P9999')))"})
    out = r.get("output", "")
    check("leg7 Berry dtc_desc hit + miss",
          "System Too Lean" in out and "nil" in out, out.replace("\n", "|"))

    # ---- leg 8: delete + restore ----
    for n in ("generic", "0_override", "mfr", "eu"):
        api("/api/autopid/dtc/db?name=" + n, "DELETE")

    code, r = api("/api/autopid/dtc/db")
    check("leg8 all deleted", r.get("dbs") == [], r.get("dbs"))
    code, r = api("/api/autopid/dtc/lookup?codes=P0420")
    check("leg8 lookup null after delete", r.get("P0420", "x") is None, r)

    api("/api/settings/autopid", "PUT", ap_before)
    api("/api/settings/submit", "POST")
    print("restoring settings…")
    time.sleep(3)
    wait_up()

    if fails:
        print("DTC DB BENCH FAIL:", ", ".join(fails))
        sys.exit(1)
    print("DTC DB BENCH PASS")


if __name__ == "__main__":
    main()
