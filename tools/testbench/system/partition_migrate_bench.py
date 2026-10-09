#!/usr/bin/env python3
"""The partition table rewrite on the bench DUT, measured (2026-10-10).

A unit updated by OTA from the factory firmware carries the factory
partition table (one 6 MB `storage` where v6 has `settings` + `storage`).
partition_migrate rewrites that table with the build's own on the first
boot and restarts. This bench makes exactly that table state on a DUT that
runs the fixed v6 (its filesystems stay where they are, so nothing is
formatted and the settings survive: the swap alone is lossless), and
watches the boot that fixes it from power-on.

  table    the legacy partition-table.bin written at 0x8000 with esptool
           (the DUT resets into the legacy table; its app slots are the
           same, so the same v6 image boots)
  boot 1   PSU cold boot, console from power-on: the W line naming the
           change (`storage 6144 KB at 0x9EE000 -> settings 256 KB ...`),
           `partition table rewritten in N ms (erase N ms, write N ms)`,
           the planned restart, then boot #2 in the same capture: no
           partition_migrate line, no `holds a LittleFS` probe line
           (nothing formatted), 0 E lines in the whole capture, `WICAN
           HEALTH errors=0`, the DUT back on its bench lease with its own
           settings, the restart history's latest record
           planned_reason=partition_migrate source=boot
  boot 2   PSU cold boot: no partition_migrate line, 0 E lines, `WICAN
           FLASH writes=0 erases=0`

  python tools/testbench/system/partition_migrate_bench.py
         [--legacy-table <partition-table.bin of the v4.51p release>]
         [--wican auto] [--psu auto] [--dut 10.42.1.194]

Verdict: PARTITION MIGRATE PASS / PARTITION MIGRATE FAIL: <names>. Clears
the DUT's latched faults first (`POST /api/faults/clear`: the field state
latched boot_errors on this DUT earlier). ~2 min, two PSU cycles.
"""
import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", "wifi"))
import bench_ports  # noqa: E402
from owon_psu import OwonPsu  # noqa: E402
from wican_fresh_bench import Console, ESPTOOL_PY, check, note, fails  # noqa: E402

DEFAULT_TABLE = os.path.join(REPO, "test-reports", "logs", "ota_v451p_to_alfa02_20261010",
                             "images", "v451p_zip", "partition-table.bin")


def esptool(port, after, *args):
    cmd = [ESPTOOL_PY, "-m", "esptool", "--chip", "esp32s3", "-p", port, "-b", "460800",
           "--before", "default-reset", "--after", after, *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        note(f"esptool {args[0]} failed:\n" + r.stdout[-600:] + r.stderr[-300:])
    return r.returncode == 0


def ssh(cmd, timeout=60):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "rpi001", cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "").strip()


def dut_get(dut, path, timeout=6):
    rc, out = ssh(f"curl -s -m {timeout} http://{dut}{path}", timeout + 10)
    return out if rc == 0 else ""


def wait_lease(dut, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = dut_get(dut, "/api/wifi/status")
        if out.startswith("{"):
            return out
        time.sleep(3)
    return ""


def boots(lines, since=0.0):
    """(boot lines, E lines, partition_migrate lines, probe W lines, health, flash) after a stamp."""
    boot, e, pm, probes, health, flash = [], [], [], [], None, None
    for ts, l in lines:
        if ts < since:
            continue
        if "restart_tracker: boot #" in l:
            boot.append(l)
        if re.match(r"E \(\d+\)", l):
            e.append(l)
        if "partition_migrate:" in l:
            pm.append(l)
        if re.search(r"holds (a LittleFS of \d+ blocks|no LittleFS superblock)", l):
            probes.append(l)
        m = re.search(r"WICAN HEALTH errors=(\d+)", l)
        if m:
            health = int(m.group(1))
        m = re.search(r"WICAN FLASH writes=(\d+) wbytes=\d+ erases=(\d+)", l)
        if m:
            flash = (int(m.group(1)), int(m.group(2)))
    return boot, e, pm, probes, health, flash


def save(path, lines, since=0.0):
    open(path, "w", encoding="utf-8").write("".join(f"{ts:8.3f} {l}\n" for ts, l in lines if ts >= since))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy-table", default=DEFAULT_TABLE)
    ap.add_argument("--wican", default="auto")
    ap.add_argument("--psu", default="auto")
    ap.add_argument("--dut", default="10.42.1.194", help="the DUT's bench lease (settings survival + the restart history)")
    a = ap.parse_args()
    if not os.path.exists(a.legacy_table):
        print(f"PARTITION MIGRATE FAIL: no legacy table at {a.legacy_table}")
        return 1
    run = time.strftime("%Y%m%d_%H%M")
    logdir = os.path.join(REPO, "test-reports", "logs", f"partition_migrate_{run}")
    os.makedirs(logdir, exist_ok=True)
    wican = bench_ports.resolve(a.wican, "wican_console", "COM254")
    psu = OwonPsu(bench_ports.resolve(a.psu, "psu", "COM50"))
    try:
        psu.meas_current()
    except (AssertionError, ValueError):
        pass
    psu.set_voltage(13.5)
    psu.output(True)

    info = dut_get(a.dut, "/api/info")
    fw = re.search(r'"fw_version":"([^"]+)"', info)
    check("dut_on_lease", fw is not None, fw.group(1) if fw else f"no /api/info from {a.dut}")
    if fw is None:
        psu.close()
        print("PARTITION MIGRATE FAIL: " + ", ".join(fails))
        return 1
    rc, out = ssh(f"curl -s -m 6 -X POST http://{a.dut}/api/faults/clear")
    note(f"faults cleared first: {out[:60]}")

    # ---- the legacy table over the running v6 ------------------------------------------
    check("legacy_table_written", esptool(wican, "no-reset", "write-flash", "0x8000", a.legacy_table))
    if fails:
        psu.close()
        print("PARTITION MIGRATE FAIL: " + ", ".join(fails))
        return 1

    # ---- boot 1: the migration from power-on --------------------------------------------
    psu.output(False)
    time.sleep(4)
    con = Console(wican, 2000000)
    psu.output(True)
    hit = con.wait_for(r"partition_migrate: partition table rewritten in (\d+) ms", 60)
    up = con.wait_for(r"main: WiCAN Pro up", 90, hit[0] + 0.001 if hit else 0.0)
    time.sleep(12)
    lines = con.snapshot()
    save(os.path.join(logdir, "boot1_migration.console.log"), lines)
    boot, e, pm, probes, health, flash = boots(lines)
    for l in pm:
        note(l[:170])
    ms = re.search(r"rewritten in (\d+) ms \(erase (\d+) ms, write (\d+) ms\)", hit[1]) if hit else None
    check("migration_announced", any("rewriting it with this build's" in l for l in pm),
          next((l[:150] for l in pm if "rewriting" in l), "no W line"))
    check("table_rewritten", ms is not None,
          f"erase+write {ms.group(1)} ms (erase {ms.group(2)} ms, write {ms.group(3)} ms)" if ms else "no 'rewritten in' line")
    check("restarted_into_new_layout", up is not None and len(boot) >= 2,
          f"{len(boot)} boots in the capture; last: {(boot[-1] if boot else '?')[:110]}")
    check("second_boot_quiet", sum(1 for l in pm if "rewriting" in l) == 1 and
          sum(1 for l in pm if "rewritten" in l) == 1,
          f"{len(pm)} partition_migrate lines in the capture (one rewriting, one rewritten expected)")
    check("nothing_formatted", len(probes) == 0, probes[0][:120] if probes else "no superblock probe line: the filesystems stayed")
    check("no_error_lines", len(e) == 0, f"{len(e)} E lines" + ("; first: " + e[0][:110] if e else ""))
    check("health_errors_0", health == 0, f"WICAN HEALTH errors={health}")
    status = wait_lease(a.dut, 90)
    check("settings_survived", '"sta_connected":true' in status and '"ap_default_password":false' in status,
          (status or "not back on the lease within 90 s")[:100])
    hist = dut_get(a.dut, "/api/restart/history", 8)
    open(os.path.join(logdir, "restart_history.json"), "w", encoding="utf-8").write(hist)
    rec = re.search(r'"planned_reason":"partition_migrate"', hist)
    src = re.search(r'"source":"boot"', hist)
    check("history_records_the_migration", rec is not None and src is not None,
          "planned_reason=partition_migrate source=boot" if rec and src else "no partition_migrate record in /api/restart/history")
    mark = lines[-1][0] if lines else 0.0

    # ---- boot 2: ours, quiet --------------------------------------------------------------
    psu.output(False)
    time.sleep(4)
    psu.output(True)
    con.wait_for(r"main: WiCAN Pro up", 90, mark + 0.001)
    time.sleep(12)
    lines2 = con.snapshot()
    save(os.path.join(logdir, "boot2_quiet.console.log"), lines2, mark + 0.001)
    con.close()
    boot, e, pm, probes, health, flash = boots(lines2, mark + 0.001)
    check("b2_no_migration_line", len(pm) == 0, pm[0][:120] if pm else "no partition_migrate line")
    check("b2_no_error_lines", len(e) == 0, f"{len(e)} E lines" + ("; first: " + e[0][:110] if e else ""))
    check("b2_flash_quiet", flash == (0, 0), f"WICAN FLASH writes/erases = {flash}")
    psu.set_voltage(13.5)
    psu.output(True)
    psu.close()
    if fails:
        print("PARTITION MIGRATE FAIL: " + ", ".join(fails))
        return 1
    print("PARTITION MIGRATE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
