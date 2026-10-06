#!/usr/bin/env python3
"""A config.json with a parameter name twice, on hardware (2026-10-06, Ali's
phone: "WiCAN did not accept the vehicle setup: duplicate parameter name
'OxySensor1_Volt'"). The SAE table gave one name to two PIDs (0x14 and
0x24, 21 pairs) and the detection stored its rows unvalidated, so a car
answering both sets got a file its own loader refused at the next boot
(`E autopid: config file invalid: ..., empty tables`, a boot_errors fault)
and the wizard's PUT of it was refused. Fixed three ways: distinct names
in the table, the scan's rows made unique, and the loader repairing such a
file instead of emptying it. This bench stages the file the old firmware
wrote and expects the repair. Prints AUTOPID NAMES PASS.

usage: python tools/testbench/system/autopid_names_bench.py [--wican auto] [--dut 10.42.1.194]
needs: ssh rpi001, the DUT's console port. Leaves the DUT with empty tables.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "wifi"))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
from wican_fresh_bench import Console, ssh_run, check, note, fails, restart_stats  # noqa: E402
import bench_ports  # noqa: E402

import wican_fresh_bench as _fresh  # noqa: E402

# esp-mqtt's own E lines around a restart (the broker on the Pi is away for
# the seconds the uplink is down): library output, handled by its reconnect
_fresh.E_WHITELIST = _fresh.E_WHITELIST + (
    "mqtt_client: Error transport connect",
    "mqtt_client: esp_mqtt_handle_transport_read_error",
    "mqtt_client: mqtt_process_receive",
    "mqtt_client: esp_mqtt_connect",
    "mqtt_client: MQTT connect failed",
    "mqtt_client: Poll read error",
    "transport_base: poll_read select error",
    "transport_base: tcp_read error")

# the rows the old table produced for a car with both oxygen-sensor sets
# (the firmware's own expressions, read from /api/autopid/std_table)
DUP_CONFIG = {
    "groups": [{"name": "default", "enabled_default": True, "period_ms": 5000}],
    "pids": [
        {"name": "OxySensor1_Volt", "type": "std", "cmd": "0114", "group": "default", "parameters": [
            {"name": "OxySensor1_Volt", "expression": "B2*0.004999999888", "unit": "volts", "class": "voltage", "min": 0, "max": 1},
            {"name": "OxySensor1_STFT", "expression": "B3*0.78125-100", "unit": "%", "class": "none", "min": -100, "max": 99}]},
        {"name": "OxySensor1_FAER", "type": "std", "cmd": "0124", "group": "default", "parameters": [
            {"name": "OxySensor1_FAER", "expression": "[B2:B3]*0.000030517578125", "unit": "ratio", "class": "gas", "min": 0, "max": 2},
            {"name": "OxySensor1_Volt", "expression": "[B4:B5]*0.0001220703125", "unit": "volts", "class": "voltage", "min": 0, "max": 2}]},
        {"name": "EngineRPM", "type": "std", "cmd": "010C", "group": "default", "parameters": [
            {"name": "EngineRPM", "expression": "[B2:B3]*0.25", "unit": "rpm", "class": "speed", "min": 0, "max": 16384}]}],
    "filters": []}
EMPTY_CONFIG = {"groups": [], "pids": [], "filters": []}


def parse(txt):
    try:
        return json.loads((txt or "").strip())
    except json.JSONDecodeError:
        return None


def put_file(dut, obj):
    local = os.path.join(HERE, "_names_cfg.json")
    with open(local, "w") as f:
        f.write(json.dumps(obj))
    subprocess.run(["scp", "-q", local, "rpi001:/tmp/names_cfg.json"], check=True)
    os.remove(local)
    r = ssh_run(f"curl -s -m 8 -o /dev/null -w '%{{http_code}}' -X POST --data-binary @/tmp/names_cfg.json "
                f"'http://{dut}/api/fs/upload?path=/data/autopid/config.json'")
    return (r.stdout or "").strip()


def restart_and_watch(con, dut, pattern_budget=120):
    mark = len(con.snapshot())
    ssh_run(f"curl -s -m 6 -X POST http://{dut}/api/restart")
    hit = con.wait_for(r"WICAN CAPS", pattern_budget)
    time.sleep(3)
    lines = [l for _, l in con.snapshot()[mark:]]
    for _ in range(30):
        r = ssh_run(f"curl -s -m 3 http://{dut}/api/info")
        if (r.stdout or "").strip().startswith("{"):
            break
        time.sleep(2)
    return hit is not None, lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wican", default="auto")
    ap.add_argument("--dut", default="10.42.1.194")
    a = ap.parse_args()
    dut = a.dut
    con = Console(bench_ports.resolve(a.wican, "wican_console"), 2000000)
    t0 = time.time()
    try:
        boots0 = restart_stats(con)
        r = ssh_run(f"curl -s -m 6 http://{dut}/api/autopid/std_table")
        table = parse(r.stdout)
        names = [p.get("name") for e in (table if isinstance(table, list) else []) for p in e.get("parameters", [])]
        rows = [e.get("name") for e in (table if isinstance(table, list) else [])]
        check("table: no parameter name under two PIDs", names and len(set(names)) == len(names),
              sorted({n for n in names if names.count(n) > 1})[:6])
        check("table: no row name twice", rows and len(set(rows)) == len(rows))
        check("table: the wide-range oxygen rows carry their own names",
              "OxySensor1_WR_Volt" in names and "OxySensor1_WR_FAER" in names and "OxySensor1_WRC_FAER" in names and "OxySensor1_Volt" in names)

        # the file the old firmware wrote, then a restart
        check("stage: the duplicate-name config.json uploaded", put_file(dut, DUP_CONFIG) == "200")
        ssh_run(f"curl -s -m 6 -X POST http://{dut}/api/faults/clear")
        booted, lines = restart_and_watch(con, dut)
        check("restart seen on the console", booted)
        w = [l for l in lines if "autopid: config file: 1 repeated parameter name made unique" in l]
        e = [l for l in lines if l.startswith("E (") and "autopid" in l]
        check("boot: the loader repaired the file (one W line) instead of emptying the tables", len(w) == 1, w[:1] or lines[-3:])
        check("boot: no autopid E line", not e, e[:2])
        loaded = [l for l in lines if "autopid: config:" in l]
        check("boot: the tables loaded (3 pids, 5 params)", any("3 pids" in l and "5 params" in l for l in loaded), loaded[:1])
        r = ssh_run(f"curl -s -m 6 http://{dut}/api/status | python3 -c 'import sys,json;s=json.load(sys.stdin);h=s.get(\"health\",{{}});print(h.get(\"faults\"))'; "
                    f"curl -s -m 6 http://{dut}/api/autopid/config")
        out = (r.stdout or "").strip().split("\n")
        cfg = parse(out[-1]) if out else None
        check("boot: no fault latched", out and out[0].strip() == "0", out[:1])
        got = [p["name"] for pid in (cfg or {}).get("pids", []) for p in pid.get("parameters", [])]
        check("GET /api/autopid/config returns the repaired names", got == ["OxySensor1_Volt", "OxySensor1_STFT", "OxySensor1_FAER", "OxySensor1_Volt_2", "EngineRPM"], got)

        # the wizard's round trip: PUT what it read
        local = os.path.join(HERE, "_names_rt.json")
        with open(local, "w") as f:
            f.write(json.dumps(cfg or {}))
        subprocess.run(["scp", "-q", local, "rpi001:/tmp/names_rt.json"], check=True)
        os.remove(local)
        r = ssh_run(f"curl -s -m 8 -w ' %{{http_code}}' -X PUT -H 'Content-Type: application/json' -d @/tmp/names_rt.json http://{dut}/api/autopid/config")
        check("PUT of what was read back is accepted (200)", (r.stdout or "").strip().endswith("200"), (r.stdout or "").strip()[-80:])

        # the repaired file stays repaired: a second restart has nothing to do
        booted, lines = restart_and_watch(con, dut)
        w2 = [l for l in lines if "repeated parameter name" in l]
        check("second boot: the repaired file needs no repair (change-guarded rewrite)", booted and not w2, w2[:1])
        boots1 = restart_stats(con)
        check("two planned restarts, no unexpected reset", boots1[0] == boots0[0] + 2 and boots1[1] == boots0[1], (boots0, boots1))
        errs = con.e_lines()
        check("0 E lines beyond the whitelist", not errs, errs[:3])
    finally:
        put_file(dut, EMPTY_CONFIG)
        ssh_run(f"curl -s -m 6 -X POST http://{dut}/api/faults/clear")
        con.close()
    print(f"\n{len(fails)} failure(s), {round(time.time() - t0)} s")
    print("AUTOPID NAMES PASS" if not fails else "AUTOPID NAMES FAIL: " + ", ".join(fails))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
