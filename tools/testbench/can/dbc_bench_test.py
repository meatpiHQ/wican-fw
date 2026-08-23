#!/usr/bin/env python3
"""End-to-end bench for DBC → autopid filters (TASK_dbc.md).

Legs (mux-aware since 2026-07-22 — simple mux SUPPORTED, extended not):
  1. upload the fixture DBC (counts + format), garbage/bad-name rejects
  2. list + signal search: supported expressions incl. the m1 signal
     (mux_expr + mux_val emitted), extended-mux listed w/ reason
  3. add: all 5 signals of msg 0x123 (incl. mux switch + m1 signal) →
     added=5; the extended-mux signal skips w/ reason
  4. LIVE capture: broadcast BOTH mux pages of 0x123 from PCAN; the
     filter window decodes → BenchSpeed 2500, BenchTemp 60,
     BenchTorque -200, and BenchMux == 42 (page-1 value; page 0
     carries a 119 decoy that MUST NOT leak through the gate)
  5. cleanup: delete DBC, restore the original config.json verbatim

Assumes the canonical bench state (autopid settings enabled=true — the
filter runner needs the poller). No reboot: config applies LIVE.

  python dbc_bench_test.py [dut_ip] [pcan_channel]
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

FIXTURE = b"""VERSION ""

BU_ ECU Vector__XXX

BO_ 291 BenchMsg: 8 ECU
 SG_ BenchSpeed : 24|16@1+ (0.25,0) [0|16000] "rpm" Vector__XXX
 SG_ BenchTemp : 16|8@1+ (1,-40) [-40|215] "degC" Vector__XXX
 SG_ BenchTorque : 7|16@0- (1,0) [-32768|32767] "Nm" Vector__XXX
 SG_ BenchMuxSw M : 48|4@1+ (1,0) [0|15] "" Vector__XXX
 SG_ BenchMux m1 : 40|8@1+ (1,0) [0|255] "" Vector__XXX

BO_ 292 BenchExtMsg: 8 ECU
 SG_ BenchESw M : 0|4@1+ (1,0) [0|15] "" Vector__XXX
 SG_ BenchEVal m1M : 8|8@1+ (1,0) [0|255] "" Vector__XXX
"""

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


def f123_params(cfg):
    """Param count of the 0x123 filter, or None when absent."""
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    flts = [f for f in cfg.get("filters", [])
            if f.get("frame_id") == 0x123]
    return len(flts[0].get("parameters", [])) if flts else None


def main():
    # snapshot config for verbatim restore; assertions are DELTA-based
    # so pre-existing 0x123 debris (an earlier bench that died before
    # its restore) can't fail the run
    _, cfg_before = api("/api/autopid/config")
    pre = f123_params(cfg_before)
    if pre is not None:
        print(f"note: pre-existing 0x123 filter with {pre} params "
              "(earlier bench debris) — delta assertions")
    api("/api/autopid/dbc?name=benchdbc", "DELETE")

    # ---- leg 1: upload + rejects ----
    code, r = api("/api/autopid/dbc?name=benchdbc", "POST", raw=FIXTURE)
    check("leg1 fixture upload", code == 200 and r.get("signals") == 7
          and r.get("messages") == 2, r)

    code, r = api("/api/autopid/dbc?name=junk", "POST",
                  raw=b"not a dbc at all\n")
    check("leg1 garbage -> 400", code == 400
          and "no signals" in str(r), r)
    code, r = api("/api/autopid/dbc?name=../evil", "POST", raw=FIXTURE)
    check("leg1 bad name -> 400", code == 400, code)

    # ---- leg 2: list + search ----
    code, r = api("/api/autopid/dbc")
    check("leg2 list", len(r.get("dbcs", [])) == 1
          and r["dbcs"][0]["signals"] == 7, r)

    code, r = api("/api/autopid/dbc/signals?q=bench&limit=50")
    items = {x["name"]: x for x in r.get("items", [])}
    check("leg2 search finds all 7", r.get("total") == 7,
          sorted(items.keys()))
    check("leg2 intel factor expression",
          items.get("BenchSpeed", {}).get("expression")
          == "(B3+B4*256)*0.25", items.get("BenchSpeed", {}))
    check("leg2 motorola signed span",
          items.get("BenchTorque", {}).get("expression") == "[S0:S1]",
          items.get("BenchTorque", {}))
    check("leg2 m1 signal SUPPORTED with mux gate",
          items.get("BenchMux", {}).get("supported") is True
          and items.get("BenchMux", {}).get("mux_expr") == "B6&15"
          and items.get("BenchMux", {}).get("mux_val") == 1,
          items.get("BenchMux", {}))
    check("leg2 mux switch supported plain",
          items.get("BenchMuxSw", {}).get("supported") is True
          and "mux_expr" not in items.get("BenchMuxSw", {}),
          items.get("BenchMuxSw", {}))
    check("leg2 extended mux listed with reason",
          items.get("BenchEVal", {}).get("supported") is False
          and "multiplex" in items.get("BenchEVal", {}).get("reason", ""),
          items.get("BenchEVal", {}))

    # ---- leg 3: add to filters ----
    code, r = api("/api/autopid/dbc/add", "POST",
                  {"db": "benchdbc",
                   "signals": ["BenchSpeed", "BenchTemp", "BenchTorque",
                               "BenchMuxSw", "BenchMux", "BenchEVal"],
                   "group": "dbcbench", "monitor_ms": 1500})
    check("leg3 add 5 (incl. mux) + skip extended",
          code == 200 and r.get("ok") and r.get("added") == 5
          and len(r.get("skipped", [])) == 1, r)

    code, cfg = api("/api/autopid/config")
    flts = [f for f in cfg.get("filters", [])
            if f.get("frame_id") == 0x123]
    prms = flts[0].get("parameters", []) if flts else []
    muxp = [p for p in prms if p.get("name") == "BenchMux"]
    check("leg3 config gained 5 params on the 0x123 filter",
          len(flts) == 1 and len(prms) == (pre or 0) + 5,
          f"params {len(prms)} (pre {pre})")
    check("leg3 BenchMux param carries the mux gate",
          len(muxp) == 1 and muxp[0].get("mux_expr") == "B6&15"
          and muxp[0].get("mux_val") == 1,
          json.dumps(muxp)[:120])

    # ---- leg 4: live capture (both mux pages on the wire) ----
    # page 0: switch=0, BenchMux byte = 119 (the DECOY — must not leak);
    # page 1: switch=1, BenchMux byte = 42 (the real value)
    bc = subprocess.Popen(
        [PY, "-u",
         os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "actors", "pcan_dbc_broadcast.py"),
         "60", "--pcan", PCAN,
         "--data", "FF38641027770000", "--alt", "FF386410272A0100"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        want = {"BenchSpeed": 2500.0, "BenchTemp": 60.0,
                "BenchTorque": -200.0, "BenchMux": 42.0}
        got = {}
        seen_mux = set()
        end = time.time() + 50
        while time.time() < end and (len(got) < len(want) + 1
                                     or time.time() < end - 35):
            _, d = api("/api/autopid/data")
            if isinstance(d, dict):
                for k in list(want) + ["BenchMuxSw"]:
                    if k in d and d[k] is not None:
                        got[k] = d[k]
                if d.get("BenchMux") is not None:
                    seen_mux.add(d["BenchMux"])
            time.sleep(1.5)
        for k, v in want.items():
            check(f"leg4 live {k} == {v}",
                  k in got and abs(got[k] - v) < 1e-6, got.get(k))
        check("leg4 decoy page NEVER leaks through the mux gate",
              119.0 not in seen_mux and seen_mux <= {42.0},
              sorted(seen_mux))
        check("leg4 mux switch decodes plain (either page)",
              got.get("BenchMuxSw") in (0.0, 1.0), got.get("BenchMuxSw"))
    finally:
        bc.terminate()
        try:
            bc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bc.kill()

    # ---- leg 5: cleanup + restore ----
    api("/api/autopid/dbc?name=benchdbc", "DELETE")
    code, r = api("/api/autopid/dbc")
    check("leg5 dbc deleted", r.get("dbcs") == [], r.get("dbcs"))

    body = (cfg_before if isinstance(cfg_before, str)
            else json.dumps(cfg_before)).encode()
    req = urllib.request.Request(BASE + "/api/autopid/config", data=body,
                                 method="PUT",
                                 headers={"Content-Type":
                                          "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        check("leg5 config restored verbatim", resp.status == 200,
              resp.status)

    _, cfg = api("/api/autopid/config")
    check("leg5 0x123 filter back to its pre-bench shape",
          f123_params(cfg) == pre, f"{f123_params(cfg)} != {pre}")

    if fails:
        print("DBC BENCH FAIL:", ", ".join(fails))
        sys.exit(1)
    print("DBC BENCH PASS")


if __name__ == "__main__":
    main()
