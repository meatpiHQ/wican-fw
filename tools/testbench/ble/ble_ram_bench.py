#!/usr/bin/env python3
"""The Bluetooth memory profile on the bench unit  ->  BLE RAM PASS / FAIL

ble_manager settings v5 (2026-10-31): `ble_ram_profile` full | lean | custom
sizes the ESP32-S3 controller at the next boot. This bench boots the DUT once
per row, reads the internal heap the way heap_probe.py does (/api/status
memory.internal at a fixed uptime, twice), checks that the boot stayed clean,
that the device still advertises (the Pi's UB500 scans for its name), that
/api/ble echoes the profile and the sizing applied, and that the settings
rules hold:

  off      Bluetooth off: the baseline of this boot type
  full     ble_ram_profile = full (the build's Kconfig sizing)
  lean     ble_ram_profile = lean (one phone, legacy advertising on 1M, no
           scanning)
  --measure adds the knob rows through the custom profile, one knob at a
  time against full: act2 (ble_max_act 2), acl6 and acl0 (ble_acl_tx_bufs),
  scan0 (ble_scan_cache 0)

Checks: every Bluetooth-on row boots with 0 E lines and advertises, /api/ble
reports the profile and its numbers, Lean frees at least --lean-min-kb
against Full (the free reading at the same uptime), the Lean profile refuses
extended advertising and a 2M PHY with a reason that names the way out, a
custom scan cache of 5 is refused, the schema carries the four v5 keys. The
found ble_manager settings are restored with one more reboot. One row is
about two minutes (a reboot, the settle, two readings, an 8 s scan).

The first run (2026-10-31) also had a `ble5off` row (the controller's BLE 5
feature set off under the NimBLE host built with it): the device crashed at
the stack bring-up, three times, and parked itself; that knob is gone.

  python tools/testbench/ble/ble_ram_bench.py --dut localhost:18081 [--pi rpi001]
         [--settle 60] [--rows off,full,lean] [--measure] [--lean-min-kb 2.5]
         [--logdir DIR]
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
from pcbench import Dut, Bench, Run  # noqa: E402

# the tags whose E lines fail a row (the BT stack's own, the HTTP server's
# event posts that starve first, ours)
OWN_TAGS = ("ble_manager", "BLE_INIT", "BT_INIT", "NimBLE", "ble_http", "ble_j2534",
            "httpd", "nimble_port")

FULL_KNOBS = {"ble_max_act": 4, "ble_acl_tx_bufs": 12, "ble_scan_cache": 100}

# the 4.2 words every row shares (Lean refuses the others)
BASE = {"enabled": True, "advertising": "legacy", "phy": "1m"}

ROWS = {
    "off": {"enabled": False},
    "full": dict(BASE, ble_ram_profile="full"),
    "lean": dict(BASE, ble_ram_profile="lean"),
    "act2": dict(BASE, ble_ram_profile="custom", **dict(FULL_KNOBS, ble_max_act=2)),
    "acl6": dict(BASE, ble_ram_profile="custom", **dict(FULL_KNOBS, ble_acl_tx_bufs=6)),
    "acl0": dict(BASE, ble_ram_profile="custom", **dict(FULL_KNOBS, ble_acl_tx_bufs=0)),
    "scan0": dict(BASE, ble_ram_profile="custom", **dict(FULL_KNOBS, ble_scan_cache=0)),
}
MEASURE_ROWS = ["off", "full", "act2", "acl6", "acl0", "scan0", "lean"]


def mem(dut):
    st = dut.get("/api/status")
    m = st.get("memory", {}).get("internal", {})
    return {"uptime": st.get("uptime"), "free": m.get("free"),
            "min_free": m.get("min_free"), "largest": m.get("largest_block"),
            "log_errors": st.get("health", {}).get("log_errors")}


def pi_scan(pi, name, seconds):
    """ADV SEEN / NOT SEEN from the Pi's adapter (ble_adv_scan_pi.py)."""
    env = dict(os.environ, WICAN_BENCH_HOST=pi)
    p = subprocess.run([sys.executable, "-u", os.path.join(HERE, "ble_pi_run.py"),
                        "ble_adv_scan_pi.py", "--name", name, "--seconds", str(seconds)],
                       capture_output=True, text=True, timeout=seconds + 60, env=env)
    out = (p.stdout or "") + (p.stderr or "")
    line = next((ln for ln in out.splitlines() if ln.startswith("ADV ")), out.strip()[-200:])
    return "ADV SEEN" in out, line


def kb(b):
    return "-" if b is None else f"{b / 1024:.1f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--pi", default="rpi001", help="the Pi whose adapter scans")
    ap.add_argument("--settle", type=int, default=60,
                    help="seconds after the boot before the first reading")
    ap.add_argument("--rows", default="", help="comma list (default off,full,lean)")
    ap.add_argument("--measure", action="store_true",
                    help="the knob rows too (one knob at a time against full)")
    ap.add_argument("--lean-min-kb", type=float, default=2.5,
                    help="Lean must free at least this much against Full (measured "
                         "3.2 KB on the bench unit, 2026-10-31: two activities and "
                         "the scan caches; the ACL buffers are kept on purpose)")
    ap.add_argument("--scan-seconds", type=float, default=8.0)
    ap.add_argument("--no-scan", action="store_true", help="skip the Pi's scan witness")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    rows = [r.strip() for r in a.rows.split(",") if r.strip()] or \
        (MEASURE_ROWS if a.measure else ["off", "full", "lean"])
    unknown = [r for r in rows if r not in ROWS]
    if unknown:
        print(f"unknown rows: {unknown} (known: {', '.join(ROWS)})")
        return 2

    stamp = time.strftime("%Y%m%d_%H%M")
    logdir = a.logdir or os.path.join(HERE, "..", "..", "..", "test-reports", "logs",
                                      "ble_ram_" + stamp)
    os.makedirs(logdir, exist_ok=True)

    run = Run("BLE RAM")
    dut = Dut(a.dut, own_tags=OWN_TAGS)
    st0 = dut.get("/api/status")
    info = dut.get("/api/info")
    name = "WiC_" + str(info.get("device_id", "")).lower()
    found = dut.settings("ble_manager")
    dut.as_found()
    print(f"DUT {st0.get('version')} on {st0.get('partition')} ({name}), found: "
          f"enabled={found.get('enabled')} ble_ram_profile={found.get('ble_ram_profile')} "
          f"advertising={found.get('advertising')} phy={found.get('phy')}; "
          f"internal free {st0['memory']['internal']['free']} B", flush=True)

    # ---- the settings contract (no reboot) ---------------------------------------
    schema = dut.get("/api/settings/ble_manager/schema").get("properties", {})
    keys = ["ble_ram_profile", "ble_max_act", "ble_acl_tx_bufs", "ble_scan_cache"]
    run.check("schema_has_the_v5_keys", all(k in schema for k in keys),
              ", ".join(k for k in keys if k not in schema) or "all four")
    run.check("schema_profile_words",
              sorted(schema.get("ble_ram_profile", {}).get("enum", [])) ==
              ["custom", "full", "lean"], schema.get("ble_ram_profile"))

    def refused(label, changes, word):
        bad = dict(found)
        bad.update(changes)
        code, body = dut.api("/api/settings/ble_manager", "PUT", bad)
        text = json.dumps(body) if not isinstance(body, str) else body
        run.check(f"refused_{label}", code == 400 and word in text,
                  f"HTTP {code} {text[:160]}")

    refused("lean_with_extended_advertising",
            {"ble_ram_profile": "lean", "advertising": "extended", "phy": "1m"}, "legacy")
    refused("lean_with_2m_phy",
            {"ble_ram_profile": "lean", "advertising": "legacy", "phy": "2m"}, "legacy")
    refused("custom_scan_cache_5",
            {"ble_ram_profile": "custom", "advertising": "legacy", "phy": "1m",
             "ble_scan_cache": 5}, "ble_scan_cache")
    run.check("refusals_left_nothing_pending",
              not dut.get("/api/settings/ble_manager").get("pending_reboot"))

    # ---- one boot per row --------------------------------------------------------
    results = []
    try:
        for row in rows:
            vals = ROWS[row]
            print(f"\n== row {row}: {vals}", flush=True)
            t_back = dut.restart({"ble_manager": vals})
            time.sleep(max(0, a.settle - (time.time() - t_back)))
            r1 = mem(dut)
            time.sleep(15)
            r2 = mem(dut)
            ble = dut.get("/api/ble")
            rec = {"row": row, "vals": vals, "first": r1, "second": r2,
                   "ram_profile": ble.get("ram_profile"), "ram": ble.get("ram"),
                   "adv": None, "adv_line": "", "e_lines": []}
            dut.sweep()
            rec["e_lines"] = sorted(dut.seen_e)
            on = vals.get("enabled", False)
            if on and not a.no_scan:
                rec["adv"], rec["adv_line"] = pi_scan(a.pi, name, a.scan_seconds)
            results.append(rec)
            print(f"{row:8s} uptime {r1['uptime']} free {r1['free']} / {r2['free']}  "
                  f"min_free {r2['min_free']}  largest {r2['largest']}  "
                  f"log_errors {r2['log_errors']}  ram_profile={rec['ram_profile']} "
                  f"ram={rec['ram']}  adv={rec['adv']}", flush=True)
            run.metric(f"int_free_{row}_b", r2["free"], "B")
            run.metric(f"int_min_free_{row}_b", r2["min_free"], "B")
            run.check(f"{row}_boot_clean", r2["log_errors"] == 0 and
                      not [e for e in rec["e_lines"]],
                      f"log_errors {r2['log_errors']}" +
                      (" | " + " | ".join(rec["e_lines"])[:300] if rec["e_lines"] else ""))
            if on:
                want = vals.get("ble_ram_profile")
                run.check(f"{row}_api_ble_reports_the_profile",
                          ble.get("ram_profile") == want and isinstance(ble.get("ram"), dict),
                          f"ram_profile={ble.get('ram_profile')} ram={ble.get('ram')}")
                ram = ble.get("ram") or {}
                if want == "custom":
                    run.check(f"{row}_api_ble_reports_the_knobs",
                              ram.get("max_act") == vals["ble_max_act"] and
                              ram.get("acl_tx_bufs") == vals["ble_acl_tx_bufs"] and
                              ram.get("scan_cache") == vals["ble_scan_cache"] and
                              ram.get("ble5") is True, ram)
                elif want == "lean":
                    run.check(f"{row}_lean_sizing", ram.get("max_act") == 2 and
                              ram.get("scan_cache") == 0, ram)
                if not a.no_scan:
                    run.check(f"{row}_advertises", rec["adv"] is True, rec["adv_line"])
    except Bench as e:
        print(f"BENCH ABORTED: {e}", flush=True)
        run.check("rows_completed", False, str(e))
    finally:
        with open(os.path.join(logdir, "rows.json"), "w") as f:
            json.dump(results, f, indent=1)
        # the found settings back (a whole-object stage: the v5 keys the
        # device added at the migration stay as the device filled them)
        try:
            print("\n== restore: the found ble_manager settings", flush=True)
            dut.restart({"ble_manager": found})
            back = dut.settings("ble_manager")
            run.check("restored_as_found",
                      all(back.get(k) == v for k, v in found.items()),
                      {k: (found.get(k), back.get(k)) for k in found if back.get(k) != found[k]})
        except Bench as e:
            run.check("restored_as_found", False, str(e))

    # ---- the table and the comparisons ---------------------------------------------
    by = {r["row"]: r for r in results}
    print("\n| row | free at settle | free +15 s | min free | largest block | E lines | "
          "advertises | applied |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in results:
        print(f"| {r['row']} | {kb(r['first']['free'])} KB | {kb(r['second']['free'])} KB | "
              f"{kb(r['second']['min_free'])} KB | {kb(r['second']['largest'])} KB | "
              f"{r['second']['log_errors']} | "
              f"{'-' if r['adv'] is None else ('yes' if r['adv'] else 'NO')} | "
              f"{r['ram_profile']} {json.dumps(r['ram']) if r['ram'] else ''} |")
    if "off" in by and "full" in by:
        cost = by["off"]["second"]["free"] - by["full"]["second"]["free"]
        run.metric("ble_full_cost_kb", round(cost / 1024, 1), "KB")
    if "off" in by and "lean" in by:
        cost = by["off"]["second"]["free"] - by["lean"]["second"]["free"]
        run.metric("ble_lean_cost_kb", round(cost / 1024, 1), "KB")
    if "full" in by and "lean" in by:
        saves = by["lean"]["second"]["free"] - by["full"]["second"]["free"]
        run.metric("lean_saves_kb", round(saves / 1024, 1), "KB")
        run.check(f"lean_frees_at_least_{a.lean_min_kb:g}_kb",
                  saves >= a.lean_min_kb * 1024,
                  f"full {by['full']['second']['free']} B, lean {by['lean']['second']['free']} B")
    for row in ("act2", "acl6", "acl0", "scan0"):
        if row in by and "full" in by:
            d = by[row]["second"]["free"] - by["full"]["second"]["free"]
            run.metric(f"{row}_saves_kb", round(d / 1024, 1), "KB")

    try:
        dut.reset_check(run, st0)
    except Bench as e:
        run.check("no_unexpected_reset", False, str(e))
    print(f"rows: {os.path.join(logdir, 'rows.json')}")
    return run.verdict()


if __name__ == "__main__":
    sys.exit(main())
