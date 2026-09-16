#!/usr/bin/env python3
"""AutoPID vehicle-profile import bench  ->  AUTOPID PROFILE PASS / FAIL

Every published vehicle profile (vehicle_profiles.json on GitHub main, or a
local copy) must be accepted by `PUT /api/autopid/config` after the web UI's
import conversion, and a PID that decodes MORE parameters than the old
per-PID cap must poll and decode at runtime.

Found 2026-09-16 on the bench DUT: `AP_PARAMS_PER` 16 rejected 11 of the 80
published profiles ("220105: more than 16 parameters" - the Hyundai/Kia
BMS DIDs decode 20-32 values, Xpeng's cell-voltage DID 192); once raised, a
typo in three Hyundai/Kia profiles (cell 158 labelled HV_C_V_168 twice)
tripped the firmware's duplicate-name rule next, so the importer now renames
repeats. This bench mirrors `web_ui_v2/web/index.html` importProfile():
  1. B<n>/S<n> byte indexes shift DOWN by one (published profiles index the
     frame with its PCI byte; the v6 payload starts at the service echo),
  2. a U+FFFD degree sign becomes "°",
  3. a parameter name already in the table gets a _2/_3 suffix.

Legs (in this order - the wide leg needs the chip in its found state):
  wide      one custom PID `wide0100` (cmd 0100, the ECU simulator answers
            6 payload bytes) carrying --wide N parameters (default 24, above
            the old cap): every one must show a value in GET /api/autopid
            within 15 s and the log ring must gain no E line.
  profiles  every car: PUT the converted table (std/custom PIDs of the found
            config kept, vehicle-specific replaced) -> 200 {"ok":true}.
  restore   the config found at the start is PUT back and read back equal,
            then `0100` is tried through POST /api/autopid/test: a profile
            whose PID inits switch protocol/header/CRA (the file's last car,
            Zeekr 001: `ATSP7;ATSH...;ATCRA...`) leaves the chip there, and
            with an empty `std_init` every standard PID answers NO DATA
            from then on (autopid README, importer rule 2). The bench sends
            the restoring prelude `ATSP6;ATSH7DF;ATCRA;ATH0` and reports
            that as a WARN (documented behaviour, rig left usable), FAIL if
            even the prelude does not bring the answers back.

usage: autopid_profile_bench.py <base-url> [--profiles FILE] [--only SUBSTR]
                                [--wide N] [--skip-wide]
  e.g. python tools/testbench/obd/autopid_profile_bench.py http://localhost:8081
       (through `ssh -N -L 8081:<dut-ip>:80 rpi001`, or the DUT's address
        directly from the Pi)
Needs: main firmware on the DUT; the ECU simulator on the bus for the wide
leg (otherwise --skip-wide). No vehicle needed. Runtime ~2 min.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

PROFILES_URL = "https://raw.githubusercontent.com/meatpiHQ/wican-fw/main/vehicle_profiles.json"


def http(base, method, path, body=None, timeout=25):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


# ---- the importer's conversion (keep in step with index.html importProfile) ----
def shift_expr(x):
    return re.sub(r"([BS])(\d+)",
                  lambda m: m.group(1) + str(max(0, int(m.group(2)) - 1)),
                  str(x or ""))


def fix_unit(u):
    return u.replace("�", "°") if isinstance(u, str) else u


def convert(found_cfg, car):
    """The table the UI PUTs when this car is picked; returns (cfg, renamed)."""
    cfg = json.loads(json.dumps(found_cfg))
    cfg["pids"] = [p for p in cfg.get("pids", []) if p.get("type") != "specific"]
    taken = set(pr.get("name") for p in cfg["pids"] for pr in p.get("parameters", []))
    renamed = []

    def uniq(n):
        if not n or n not in taken:
            taken.add(n)
            return n
        k = 2
        while "%s_%d" % (n, k) in taken:
            k += 1
        taken.add("%s_%d" % (n, k))
        renamed.append("%s to %s_%d" % (n, n, k))
        return "%s_%d" % (n, k)

    for pp in car.get("pids", []):
        prm = [dict(x, name=uniq(x.get("name")), unit=fix_unit(x.get("unit")),
                    expression=shift_expr(x.get("expression")))
               for x in pp.get("parameters", [])]
        cfg["pids"].append({
            "name": (prm[0]["name"] if len(prm) == 1 and prm[0].get("name") else pp["pid"]),
            "cmd": pp["pid"], "init": pp.get("pid_init", ""), "group": "default",
            "period_ms": 0, "type": "specific", "parameters": prm})
    return cfg, renamed


def ring_e_count(base):
    st, body = http(base, "GET", "/api/logs/ring")
    if st != 200:
        return None
    noise = ("data_logger", "esp-tls", "transport_base", "HTTP_CLIENT")
    return sum(1 for ln in body.splitlines()
               if "E (" in ln and not any(n in ln for n in noise))


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(__doc__)
        return 2
    base = args[0].rstrip("/")
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    only = opt("--only")
    wide_n = int(opt("--wide", 24))
    fails = []

    st, body = http(base, "GET", "/api/autopid/config")
    if st != 200:
        print("FAIL: GET /api/autopid/config -> %s %s" % (st, body[:200]))
        print("AUTOPID PROFILE FAIL")
        return 1
    found_raw = body
    found = json.loads(body)
    print("found config: %d pids, %d filters" % (len(found.get("pids", [])), len(found.get("filters", []))))

    src = opt("--profiles")
    if src:
        cars = json.load(open(src, encoding="utf-8"))["cars"]
    else:
        with urllib.request.urlopen(PROFILES_URL, timeout=30) as r:
            cars = json.loads(r.read().decode("utf-8"))["cars"]
    if only:
        cars = [c for c in cars if only.lower() in c["car_model"].lower()]
    print("profiles: %d cars from %s" % (len(cars), src or PROFILES_URL))

    # ---- leg 1: wide PID polls + decodes at runtime (chip still as found) --------
    if "--skip-wide" not in args:
        cfg = json.loads(found_raw)
        cfg["pids"] = [p for p in cfg.get("pids", []) if p.get("name") != "wide0100"]
        exprs = ["B0", "B1", "B2", "B3", "B4", "B5"]
        params = [{"name": "w%02d" % i, "expression": "%s+%d" % (exprs[i % 6], i // 6), "unit": ""}
                  for i in range(wide_n)]
        cfg["pids"].append({"name": "wide0100", "cmd": "0100", "init": "", "group": "default",
                            "period_ms": 300, "type": "custom", "parameters": params})
        e0 = ring_e_count(base)
        st, body = http(base, "PUT", "/api/autopid/config", cfg)
        if st != 200:
            fails.append("wide PUT: %s %s" % (st, body[:120]))
            print("wide leg: PUT -> %s %s" % (st, body[:120]))
        else:
            time.sleep(15)
            st, body = http(base, "GET", "/api/autopid")
            d = json.loads(body) if st == 200 else {}
            vals = {p["name"]: p.get("value") for p in d.get("params", []) if p["name"].startswith("w")}
            have = sum(1 for v in vals.values() if v is not None)
            e1 = ring_e_count(base)
            print("wide leg: %d/%d parameters decoded from 0100 (sample %s); E lines %s -> %s"
                  % (have, wide_n, {k: vals[k] for k in sorted(vals)[:3]}, e0, e1))
            if have != wide_n:
                fails.append("wide: %d/%d decoded (ECU simulator on the bus? chip in a foreign init state?)"
                             % (have, wide_n))
            if e0 is not None and e1 is not None and e1 > e0:
                fails.append("wide: log ring gained %d E line(s)" % (e1 - e0))

    # ---- leg 2: every profile is accepted -----------------------------------------
    widest = (0, "")
    renamed_cars = 0
    accepted = 0
    for car in cars:
        cfg, renamed = convert(found, car)
        mx = max((len(p.get("parameters", [])) for p in cfg["pids"]), default=0)
        widest = max(widest, (mx, car["car_model"]))
        t0 = time.time()
        st, body = http(base, "PUT", "/api/autopid/config", cfg)
        ok = st == 200
        if renamed:
            renamed_cars += 1
        print("  %s %-45s %2d pids, max %3d params/pid, %4d ms%s%s" % (
            "ok  " if ok else "FAIL", car["car_model"][:45], len(car.get("pids", [])), mx,
            (time.time() - t0) * 1000,
            (" renamed: " + ", ".join(renamed[:2])) if renamed else "",
            "" if ok else "  -> %s %s" % (st, body[:120])))
        if ok:
            accepted += 1
        else:
            fails.append("%s: %s %s" % (car["car_model"], st, body[:120]))
    print("profiles leg: %d/%d accepted; widest PID %d params (%s); %d profiles needed renames"
          % (accepted, len(cars), widest[0], widest[1], renamed_cars))

    # ---- restore + standard-PID recovery ----------------------------------------
    st, body = http(base, "PUT", "/api/autopid/config", json.loads(found_raw))
    st2, after = http(base, "GET", "/api/autopid/config")
    same = st == 200 and st2 == 200 and json.loads(after) == json.loads(found_raw)
    print("restore: PUT %s, read-back %s" % (st, "equal" if same else "DIFFERENT"))
    if not same:
        fails.append("restore: config differs after the run")

    warns = []
    if cars and "--skip-wide" not in args:
        st, body = http(base, "POST", "/api/autopid/test", {"cmd": "0100"}, timeout=40)
        bare = json.loads(body) if st == 200 else {}
        if not bare.get("payload"):
            st, body = http(base, "POST", "/api/autopid/test",
                            {"cmd": "0100", "init": "ATSP6;ATSH7DF;ATCRA;ATH0"}, timeout=40)
            fixed = json.loads(body) if st == 200 else {}
            st, sb = http(base, "GET", "/api/settings/autopid")
            std_init = (json.loads(sb).get("std_init") if st == 200 else "?")
            if fixed.get("payload"):
                warns.append("standard PIDs answered '%s' after the last profile's inits (%s); the "
                             "restoring prelude brought '%s' back. autopid README importer rule 2: "
                             "std_init (DUT: %r) needs `ATSP6;ATSH7DF;ATCRA;ATH0` when vehicle "
                             "profiles switch protocol/header/CRA"
                             % (bare.get("raw", ""), cars[-1]["car_model"], fixed.get("payload"),
                                std_init))
            else:
                fails.append("standard PIDs dead after the profiles leg and the restoring prelude "
                             "did not recover them (%s / %s)" % (bare.get("raw"), fixed.get("raw")))
        else:
            print("std recovery: 0100 answers %s with the found config" % bare.get("payload"))

    for w in warns:
        print("WARN:", w)
    for f in fails:
        print("FAIL:", f)
    print("AUTOPID PROFILE", "PASS" if not fails else "FAIL",
          "(%d warning%s)" % (len(warns), "" if len(warns) == 1 else "s") if warns else "")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
