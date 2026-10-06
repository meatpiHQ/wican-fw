#!/usr/bin/env python3
"""A unit that ran the factory firmware, flashed with v6 without an erase
(2026-10-06): the first boot must be clean.

The factory firmware (legacy v4.5x) keeps ONE `storage` partition at
0x9EE000, 6 MB, formatted as LittleFS (1536 blocks). v6 has `settings`
(256 KB, 64 blocks) at that address and its own `storage` behind it. A
`-Flash` without an erase leaves the factory filesystem there; LittleFS
mounts a superblock whatever block count it claims, so until that day v6
"mounted" the factory filesystem on its settings partition and every access
past 256 KB failed: 83 E lines at boot, a latched `boot_errors` fault, no
setting persisted (Ali's fresh unit showed `1 fault`). The fix probes the
superblock pair before the mount and formats a filesystem that is not ours
with one W line. This bench makes that unit on the bench DUT and holds the
fix against it.

  backup   GET /api/settings/backup from the DUT through rpi001 into the
           run's log folder (--backup none skips it)
  erase    esptool erase-flash
  factory  a factory-style LittleFS image (littlefs-python: 6 MB, 1536
           blocks, the legacy JSON files, two used data blocks where v6's
           storage partition starts) written at 0x9EE000
  flash    write-flash @flash_args (bootloader, table, ota data, app),
           without a reset: the first boot is the cold one below
  boot 1   PSU cold cycle, console from power-on: 0 E lines, the two W lines
           (settings: a LittleFS of 1536 blocks on a 64-block partition;
           storage: no LittleFS superblock), WICAN HEALTH errors=0,
           WICAN FAULTS active=0, `faults` -> no fault codes, the first-boot
           erases within the boot budget, the AP up
  boot 2   PSU cold cycle: 0 E lines, no probe W line (the partitions are
           ours now), WICAN FLASH writes=0 erases=0 (the defaults were
           persisted on boot 1), no fault
  restore  (--restore, needs the backup) the Pi joins `WiCAN_<id>` /
           @meatpi# on wtest1 (TESTING.md "Reaching the DUT's AP"), POSTs
           the backup, deletes the profile; the DUT must come back on its
           bench lease with its own AP password (observed state: the POST's
           answer rides a fresh association and is advisory). Without it the
           DUT is left fresh, and /data is gone either way (not in the
           backup). Host: littlefs-python (pip install littlefs-python).

  python tools/testbench/system/factory_flash_bench.py [--wican auto]
         [--psu auto] [--build build] [--backup auto|<ip>|none] [--restore]
  python tools/testbench/system/factory_flash_bench.py --restore-only BACKUP
         (a fresh DUT back into its bench state; the AP name comes from the
         console's `wifi -i`; prints FACTORY FLASH RESTORE PASS)

Verdict: FACTORY FLASH PASS / FACTORY FLASH FAIL: <names>. Erases the DUT.
"""
import argparse
import json
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

FACTORY_FS_OFFSET = 0x9EE000
FACTORY_FS_BLOCKS = 1536          # 6 MB of 4 KiB blocks
V6_STORAGE_BLOCK = 0x40000 // 4096  # where v6's storage partition starts inside it
BOOT_ERASE_BUDGET = 64            # main_boot.c
DUT_LEASE = "10.42.1.194"


def esptool(port, after, *args, cwd=None):
    cmd = [ESPTOOL_PY, "-m", "esptool", "--chip", "esp32s3", "-p", port, "-b", "460800",
           "--before", "default-reset", "--after", after, *args]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=600)
    if r.returncode != 0:
        note(f"esptool {args[0]} failed:\n" + r.stdout[-600:] + r.stderr[-300:])
    return r.returncode == 0


def ssh(cmd, timeout=60):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "rpi001", cmd], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip()


def factory_image(path):
    """The factory firmware's filesystem as littlefs-python writes it, plus two
    used data blocks exactly where v6's storage partition begins (its probe
    must call them 'no LittleFS superblock', not mount them)."""
    try:
        from littlefs import LittleFS
    except ImportError:
        note("littlefs-python is not installed (pip install littlefs-python)")
        return False
    fs = LittleFS(block_size=4096, block_count=FACTORY_FS_BLOCKS, read_size=128, prog_size=128,
                  lookahead_size=128, name_max=64)
    files = {
        "config.json": json.dumps({"wifi_mode": "AP", "ap_ch": "6", "sta_ssid": "", "can_datarate": "500K",
                                   "protocol": "auto_pid", "sleep_status": "enable", "sleep_volt": "13.1",
                                   "batt_alert": "disable", "mqtt_en": "disable"}, indent=1),
        "auto_pid.json": json.dumps({"car_specific": "disable", "grouping": "disable", "pids": []}),
        "car_data.json": json.dumps({"car_model": "", "init": "", "pids": []}),
        "mqtt_canfilt.json": "{}",
    }
    for name, body in files.items():
        with fs.open(name, "w") as f:
            f.write(body)
    img = bytearray(fs.context.buffer)
    used = bytes((i * 7 + 3) & 0xFF for i in range(4096))
    for blk in (V6_STORAGE_BLOCK, V6_STORAGE_BLOCK + 1):
        img[blk * 4096:(blk + 1) * 4096] = used
    open(path, "wb").write(img)
    return True


def health(lines, since=0.0):
    """The boot health lines after a console stamp: (errors, erases, writes, faults, E lines, W probe lines)."""
    errors = erases = writes = faults = None
    e_lines = []
    probes = []
    for ts, l in lines:
        if ts < since:
            continue
        m = re.search(r"WICAN HEALTH errors=(\d+)", l)
        if m:
            errors = int(m.group(1))
        m = re.search(r"WICAN FLASH writes=(\d+) wbytes=\d+ erases=(\d+)", l)
        if m:
            writes, erases = int(m.group(1)), int(m.group(2))
        m = re.search(r"WICAN FAULTS active=(\d+)", l)
        if m:
            faults = int(m.group(1))
        if re.match(r"E \(\d+\)", l):
            e_lines.append(l)
        if re.search(r"holds (a LittleFS of \d+ blocks|no LittleFS superblock)", l):
            probes.append(l)
    return errors, erases, writes, faults, e_lines, probes


def restore(backup, ssid):
    """The settings backup back into a fresh DUT through its AP from rpi001
    (TESTING.md, "Reaching the DUT's AP"): the Pi's wtest1 joins the AP (a
    rescan and up to four tries: a fresh AP is not always in the scan cache
    yet), the POST goes to the AP address, the profile is deleted. Judged by
    the observed state: the DUT back on its bench lease with its own AP
    password, which a fresh device cannot do; the POST's answer rides a fresh
    association and the device reboots on it, so it is advisory."""
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", backup, "rpi001:/tmp/factory_flash_backup.json"], check=False)
    joined = False
    for attempt in range(4):
        ssh("sudo nmcli connection delete wican-dut >/dev/null 2>&1; sudo nmcli dev wifi rescan ifname wtest1 >/dev/null 2>&1; true")
        time.sleep(6)
        rc, out = ssh(f"sudo nmcli device wifi connect '{ssid}' password '@meatpi#' ifname wtest1 name wican-dut 2>&1 | tail -n 1", 90)
        ssh("sudo nmcli connection modify wican-dut ipv4.never-default yes ipv4.route-metric 4000 "
            "ipv4.routes 192.168.0.10/32 connection.autoconnect no 2>/dev/null; sudo nmcli device reapply wtest1 >/dev/null 2>&1; true")
        time.sleep(2)
        rc, addr = ssh("ip -4 -br addr show wtest1")
        if "192.168.0." in addr:
            joined = True
            break
        print(f"  join try {attempt + 1}: {out[:90]}")
    check("restore_joined_ap", joined, ssid if joined else f"wtest1 never got an address on {ssid}")
    if joined:
        rc, out = ssh("curl -s -m 40 -X POST -H 'Content-Type: application/json' "
                      "--data-binary @/tmp/factory_flash_backup.json http://192.168.0.10/api/settings/backup", 90)
        print(f"  restore answer: {out[:100] or 'none (the device reboots on it)'}")
    ssh("sudo nmcli connection delete wican-dut >/dev/null 2>&1; rm -f /tmp/factory_flash_backup.json; true")
    back = None
    deadline = time.time() + 120
    while joined and time.time() < deadline and back is None:
        rc, out = ssh(f"curl -s -m 5 http://{DUT_LEASE}/api/wifi/status")
        if rc == 0 and out.startswith("{"):
            back = out
        else:
            time.sleep(5)
    check("restore_back_on_bench", back is not None and '"ap_default_password":false' in back,
          (back or "not on the bench lease within 120 s")[:100])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wican", default="auto")
    ap.add_argument("--psu", default="auto")
    ap.add_argument("--build", default=os.path.join(REPO, "build"))
    ap.add_argument("--backup", default="auto", help="DUT address for the settings backup, auto = the bench lease, none = skip")
    ap.add_argument("--restore", action="store_true", help="restore the backup through the DUT's AP from rpi001 afterwards")
    ap.add_argument("--restore-only", default="", metavar="BACKUP", help="only restore this backup into a fresh DUT (its AP name from the console)")
    a = ap.parse_args()
    if a.restore_only:
        con = Console(bench_ports.resolve(a.wican, "wican_console", "COM254"), 2000000)
        time.sleep(3)                              # the open may reset the DUT
        line = con.cmd("wifi -i", r"SSID: WiCAN_[0-9a-fA-F]{12}", 20)
        con.close()
        m = re.search(r"WiCAN_[0-9a-fA-F]{12}", line or "")
        check("restore_only_ap_name", m is not None, m.group(0) if m else "no AP name from `wifi -i`")
        if m:
            restore(a.restore_only, m.group(0))
        print("FACTORY FLASH FAIL: " + ", ".join(fails) if fails else "FACTORY FLASH RESTORE PASS")
        return 1 if fails else 0
    run = time.strftime("%Y%m%d_%H%M")
    logdir = os.path.join(REPO, "test-reports", "logs", f"factory_flash_{run}")
    os.makedirs(logdir, exist_ok=True)
    wican = bench_ports.resolve(a.wican, "wican_console", "COM254")
    psu = OwonPsu(bench_ports.resolve(a.psu, "psu", "COM50"))
    try:
        psu.meas_current()
    except (AssertionError, ValueError):
        pass
    psu.set_voltage(13.5)
    psu.output(True)

    # ---- backup --------------------------------------------------------------
    backup = None
    if a.backup != "none":
        ip = DUT_LEASE if a.backup == "auto" else a.backup
        rc, out = ssh(f"curl -s -m 20 http://{ip}/api/settings/backup")
        ok = rc == 0 and out.startswith("{") and '"wifi_manager"' in out
        check("backup_taken", ok, f"{len(out)} bytes from {ip}" if ok else f"no backup from {ip}")
        if ok:
            backup = os.path.join(logdir, "settings_backup.json")
            open(backup, "w", encoding="utf-8").write(out)
    if a.restore and backup is None:
        print("FACTORY FLASH FAIL: --restore without a backup; stopping before the erase")
        return 1

    # ---- erase, factory image, flash -----------------------------------------
    check("erase", esptool(wican, "no-reset", "erase-flash"))
    img = os.path.join(logdir, "factory_storage_6m.bin")
    check("factory_image", factory_image(img), f"{FACTORY_FS_BLOCKS} blocks at 0x{FACTORY_FS_OFFSET:X}")
    check("factory_image_written", esptool(wican, "no-reset", "write-flash", f"0x{FACTORY_FS_OFFSET:x}", img))
    check("flash", esptool(wican, "no-reset", "write-flash", "@flash_args", cwd=a.build))
    if fails:
        psu.close()
        print("FACTORY FLASH FAIL: " + ", ".join(fails))
        return 1

    # ---- boot 1: the first v6 boot over the factory filesystem ----------------
    psu.output(False)
    time.sleep(4)
    con = Console(wican, 2000000)
    psu.output(True)
    t1 = time.time()
    time.sleep(32)
    ans1 = con.cmd("faults", r"fault code|no fault codes", 5)
    time.sleep(3)
    lines = con.snapshot()
    errors, erases, writes, faults, e_lines, probes = health(lines)
    check("b1_no_error_lines", len(e_lines) == 0, f"{len(e_lines)} E lines" + ("; first: " + e_lines[0][:120] if e_lines else ""))
    check("b1_settings_probe_line",
          any("settings_manager: 'settings' holds a LittleFS of 1536 blocks on a 64-block partition" in l for l in probes),
          next((l[:120] for l in probes if "settings" in l), "no settings probe line"))
    check("b1_storage_probe_line",
          any("filesystem: 'storage' holds no LittleFS superblock" in l for l in probes),
          next((l[:120] for l in probes if "storage" in l), "no storage probe line"))
    check("b1_health_errors_0", errors == 0, f"WICAN HEALTH errors={errors}")
    check("b1_faults_0", faults == 0 and ans1 is not None and "no fault codes" in ans1,
          f"WICAN FAULTS active={faults}; faults: {(ans1 or 'no answer').strip()[:60]}")
    check("b1_flash_budget", erases is not None and erases <= BOOT_ERASE_BUDGET, f"writes={writes} erases={erases} (budget {BOOT_ERASE_BUDGET})")
    ssid = next((m.group(0) for _, l in lines for m in [re.search(r"WiCAN_[0-9a-fA-F]{12}", l)] if m), None)
    check("b1_ap_up", ssid is not None, ssid or "no AP name on the console")
    open(os.path.join(logdir, "boot1.console.log"), "w", encoding="utf-8").write(
        "".join(f"{ts:8.3f} {l}\n" for ts, l in lines))

    # ---- boot 2: the partitions are ours now ----------------------------------
    mark = lines[-1][0] if lines else 0.0
    psu.output(False)
    time.sleep(4)
    psu.output(True)
    time.sleep(26)
    ans2 = con.cmd("faults", r"fault code|no fault codes", 5)
    time.sleep(2)
    lines2 = con.snapshot()
    errors, erases, writes, faults, e_lines, probes = health(lines2, since=mark + 0.001)
    check("b2_no_error_lines", len(e_lines) == 0, f"{len(e_lines)} E lines" + ("; first: " + e_lines[0][:120] if e_lines else ""))
    check("b2_no_probe_line", len(probes) == 0, probes[0][:120] if probes else "the partitions are ours")
    check("b2_flash_quiet", writes == 0 and erases == 0, f"writes={writes} erases={erases}")
    check("b2_faults_0", faults == 0 and ans2 is not None and "no fault codes" in ans2,
          f"WICAN FAULTS active={faults}; faults: {(ans2 or 'no answer').strip()[:60]}")
    open(os.path.join(logdir, "boot2.console.log"), "w", encoding="utf-8").write(
        "".join(f"{ts:8.3f} {l}\n" for ts, l in lines2 if ts > mark))
    con.close()

    # ---- restore ---------------------------------------------------------------
    if a.restore and backup and ssid:
        restore(backup, ssid)
    elif backup:
        print(f"NOTE: the DUT is left fresh; restore it with --restore-only {backup}")

    psu.set_voltage(13.5)
    psu.output(True)
    psu.close()
    if fails:
        print("FACTORY FLASH FAIL: " + ", ".join(fails))
        return 1
    print("FACTORY FLASH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
