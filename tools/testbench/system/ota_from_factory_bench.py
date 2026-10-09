#!/usr/bin/env python3
"""A unit on the factory firmware (legacy v4.5x), updated to v6 the way a
user does it: the v6 image uploaded through the legacy web UI's OTA page.
Field report 2026-10-10 (v4.51p -> v6.00p_alfa-02): "I can not save changes
via setup or manually ... Factory reset also fails", 41 x `open
/settings/cfg/<c>.tmp failed` + `persist failed` at boot, `FAULT boot_errors:
86 error lines at boot`.

An OTA writes the app partition only: the partition table in flash stays the
factory one (nvs, otadata, phy_init, ota_0, ota_1 and ONE 6 MB `storage` at
0x9EE000). v6 keeps its settings on a `settings` partition of its own, which
that table does not have. This bench makes that unit on the bench DUT and
holds the running build of v6 against it:

  backup   GET /api/settings/backup from the DUT through rpi001 (--backup
           none skips it; /data is NOT in it)
  erase    esptool erase-flash
  legacy   the factory flash set (bootloader, partition table, ota data, app)
           from --legacy-dir, written without a reset; PSU cold boot with the
           console from power-on; the legacy AP comes up (WiCAN_<id>,
           @meatpi#)
  ota      rpi001 joins that AP on wtest1 and POSTs --image to
           /upload/ota.bin (multipart, the legacy OTA page's request); the
           legacy firmware answers 303 and reboots into ota_1; the DUT must
           answer /api/info with the image's version on its AP, and its
           restart history must hold a `partition_migrate` record (the
           first v6 boot rewrote the table and restarted: the fix)
  rescue   (--via <stuck image>) the stuck image goes in first through the
           legacy page, then --image through v6's own /api/ota/upload: a
           user already on alfa-02 updates to the fixed build without a PC
  boot     a PSU cold boot with the console from power-on (the legacy
           firmware moves UART0 to 4 Mbaud a few seconds after boot, so the
           OTA itself and the first v6 boot are not readable at 2 Mbaud):
           running from ota_1, 0 E lines, the settings FS mounted, no boot
           fault (2026-10-10 before the fix: `esp_littlefs: partition
           "settings" could not be found`, `settings_manager: littlefs mount
           failed: ESP_ERR_NOT_FOUND`, 41 x persist failed, `FAULT
           boot_errors: 86 error lines at boot`)
  save     the user's first save through the AP: PUT /api/settings/wifi_manager
           with a new `ap_password` (the wizard's forced first change) must
           answer 200 and persist (before the fix: 400 "change could not be
           saved", `open /settings/cfg/wifi_manager.tmp failed`)
  reset    POST /api/settings/factory_reset {"confirm":"factory-reset"} must
           answer 200 and reboot the device (before the fix: 500 "wipe
           failed", nothing on the console)
  down     (--downgrade) the user goes back: the legacy app POSTed to v6's
           /api/ota/upload (multipart `firmware=`, {"ok":true,"reboot":true});
           PSU cold boot with the console: `Running firmware version:
           v4.5x`, its storage mounted (the size it reports tells which
           table it runs under: 6291456 B = the legacy table, 6029312 B =
           v6's), its web root on the factory AP, and the legacy setting
           marked before the update (`sleep_volt` 12.9 through
           /store_config) still there when v6 ran under the legacy table
           (the stuck state), and GONE when the first v6 boot migrated the
           table (the new layout formats the legacy filesystem: the way
           back is a fresh legacy device, by design). --downgrade-only
           runs just this stage on a DUT that runs v6 on its AP with the
           factory password (no mark to find: the value is reported)

  python tools/testbench/system/ota_from_factory_bench.py --image <v6.bin>
         [--legacy-dir <dir with bootloader.bin partition-table.bin
          ota_data_initial.bin wican-fw_obd_pro_v451p.bin>]
         [--wican auto] [--psu auto] [--backup auto|<ip>|none]
         [--downgrade] [--via <stuck v6.bin>] [--skip-flash --ssid WiCAN_<id>]
         [--downgrade-only --ssid WiCAN_<id>]

Verdict: OTA FROM FACTORY PASS / OTA FROM FACTORY FAIL: <names>. Erases the
DUT and LEAVES it in the end state (the reproduction stays on the bench for
a fix); restore with an erase + `build.ps1 -Flash` + factory_flash_bench.py
--restore-only BACKUP, and the /data files by hand.
"""
import argparse
import glob
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

DUT_LEASE = "10.42.1.194"
AP_IP = "192.168.0.10"
FACTORY_PSK = "@meatpi#"
NEW_AP_PSK = "ota-bench-2026"
LEGACY_FILES = ("bootloader.bin", "partition-table.bin", "ota_data_initial.bin")
LEGACY_OFFSETS = ("0x0", "0x8000", "0xd000")
PI_PROFILE = "wican-dut"


def esptool(port, after, *args, cwd=None):
    cmd = [ESPTOOL_PY, "-m", "esptool", "--chip", "esp32s3", "-p", port, "-b", "460800",
           "--before", "default-reset", "--after", after, *args]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=600)
    if r.returncode != 0:
        note(f"esptool {args[0]} failed:\n" + r.stdout[-600:] + r.stderr[-300:])
    return r.returncode == 0


def ssh(cmd, timeout=60):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "rpi001", cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "").strip()   # the legacy web root is not clean UTF-8


def pi_join_ap(ssid, psk):
    """rpi001 onto the DUT's AP (TESTING.md "Reaching the DUT's AP"): wtest1
    with wint0 as the fallback (2026-10-10: wtest1 lost one 3.6 MB upload
    in three and took 417 s for another; wint0 answered "Secrets were
    required" to every scripted join although a join by hand worked), a
    rescan and two tries per radio, the route pinned to the AP address."""
    for iface in ("wtest1", "wint0"):
        for attempt in range(2):
            ssh(f"sudo nmcli connection delete {PI_PROFILE} >/dev/null 2>&1; "
                f"sudo nmcli dev wifi rescan ifname {iface} >/dev/null 2>&1; true")
            time.sleep(6)
            rc, out = ssh(f"sudo nmcli device wifi connect '{ssid}' password '{psk}' ifname {iface} "
                          f"name {PI_PROFILE} 2>&1 | tail -n 1", 90)
            ssh(f"sudo nmcli connection modify {PI_PROFILE} ipv4.never-default yes ipv4.route-metric 4000 "
                f"ipv4.routes {AP_IP}/32 connection.autoconnect no 2>/dev/null; "
                f"sudo nmcli device reapply {iface} >/dev/null 2>&1; true")
            time.sleep(2)
            rc, addr = ssh(f"ip -4 -br addr show {iface}")
            if "192.168.0." in addr:
                return True
            print(f"  join try {attempt + 1} on {iface}: {out[:90]}")
    return False


def pi_leave_ap():
    ssh(f"sudo nmcli connection delete {PI_PROFILE} >/dev/null 2>&1; true")


def pi_http(method, path, body=None, timeout=20):
    """One request from the Pi to the DUT's AP address: (status, body)."""
    data = ""
    if body is not None:
        q = body.replace("'", "'\"'\"'")
        data = f"-H 'Content-Type: application/json' --data-binary '{q}'"
    rc, out = ssh(f"curl -s -m {timeout} -o /tmp/ota_bench_body -w '%{{http_code}}' -X {method} {data} "
                  f"http://{AP_IP}{path}; echo; cat /tmp/ota_bench_body", timeout + 15)
    lines = out.split("\n", 1)
    status = lines[0].strip() if lines else ""
    return (int(status) if status.isdigit() else 0), (lines[1] if len(lines) > 1 else "")


def boot_lines(lines, since):
    """The boot's E lines, the settings mount lines, the persist failures and
    the boot fault after a console stamp."""
    e_lines, mount, persist, fault, ota, boot = [], [], 0, None, None, None
    for ts, l in lines:
        if ts < since:
            continue
        if re.match(r"E \(\d+\)", l):
            e_lines.append(l)
        if "could not be found" in l or "littlefs mount failed" in l:
            mount.append(l)
        if "persist failed" in l:
            persist += 1
        m = re.search(r"FAULT boot_errors: .*", l)
        if m:
            fault = m.group(0)
        m = re.search(r"ota_manager: ready \(running from (\w+)\)", l)
        if m:
            ota = m.group(1)
        m = re.search(r"restart_tracker: boot #\d+.*", l)
        if m:
            boot = m.group(0)
    return e_lines, mount, persist, fault, ota, boot


def save_console(path, lines, since=0.0):
    open(path, "w", encoding="utf-8").write(
        "".join(f"{ts:8.3f} {l}\n" for ts, l in lines if ts >= since))


def flash_legacy(wican, psu, legacy_dir, legacy_app, logdir):
    """Erase, write the factory flash set, cold-boot it with the console:
    (console, the legacy AP's SSID); None, None when a check failed."""
    # ---- erase + the factory flash set ------------------------------------------
    check("erase", esptool(wican, "no-reset", "erase-flash"))
    args = ["write-flash", "--flash-mode", "dio", "--flash-freq", "80m", "--flash-size", "16MB"]
    for off, f in zip(LEGACY_OFFSETS, LEGACY_FILES):
        args += [off, os.path.join(legacy_dir, f)]
    args += ["0x10000", legacy_app]
    check("legacy_flashed", esptool(wican, "no-reset", *args))
    if fails:
        return None, None

    # ---- the legacy firmware's cold boot -------------------------------------------
    psu.output(False)
    time.sleep(4)
    con = Console(wican, 2000000)
    psu.output(True)
    hit = con.wait_for(r"Running firmware version: (\S+)", 60)
    version = re.search(r"Running firmware version: (\S+)", hit[1]).group(1) if hit else None
    check("legacy_boots", version is not None and version.startswith("v4"), version or "no version line in 60 s")
    hit = con.wait_for(r"AP SSID: (WiCAN_[0-9a-fA-F]{12})", 30)
    ssid = re.search(r"WiCAN_[0-9a-fA-F]{12}", hit[1]).group(0) if hit else None
    check("legacy_ap_ssid", ssid is not None, ssid or "no AP SSID line")
    time.sleep(25)                                 # the chip bring-up, the AP beacons
    legacy_lines = con.snapshot()
    save_console(os.path.join(logdir, "legacy_boot.console.log"), legacy_lines)
    chip = next((l for _, l in legacy_lines if re.search(r"V2\.3\.\d+", l)), None)
    note(f"legacy chip line: {(chip or 'none')[:110]}")
    if fails:
        con.close()
        return None, None
    return con, ssid


MARK_SLEEP_VOLT = "12.9"


def legacy_mark():
    """A legacy setting changed the way the legacy UI does it (the Pi is on
    the legacy AP): GET /load_config, sleep_volt -> 12.9, POST /store_config
    (written to the legacy config.json; read at the next boot)."""
    import json
    st, cfg = pi_http("GET", "/load_config")
    if st != 200 or not cfg.startswith("{"):
        return False, f"/load_config {st}"
    obj = json.loads(cfg)
    before = obj.get("sleep_volt")
    obj["sleep_volt"] = MARK_SLEEP_VOLT
    st, ans = pi_http("POST", "/store_config", json.dumps(obj), 30)
    return st == 200, f"sleep_volt {before} -> {MARK_SLEEP_VOLT}: /store_config {st} {ans[:60]!r}"


def downgrade_stage(wican, psu, legacy_app, ssid, logdir, marked, migrated=False):
    """The user goes back to the legacy firmware through v6's OTA page."""
    joined = pi_join_ap(ssid, FACTORY_PSK)
    check("down_pi_on_v6_ap", joined, ssid)
    accepted = False
    if joined:
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", legacy_app, "rpi001:/tmp/ota_bench_legacy.bin"], check=False)
        t0 = time.time()
        rc, out = ssh("rm -f /tmp/ota_bench_body; curl -s -m 600 -o /tmp/ota_bench_body -w '%{http_code}' "
                      f"-F 'firmware=@/tmp/ota_bench_legacy.bin;filename={os.path.basename(legacy_app)}' "
                      f"http://{AP_IP}/api/ota/upload; echo; head -c 200 /tmp/ota_bench_body 2>/dev/null", 660)
        first = out.split("\n", 1)[0].strip() if out else ""
        accepted = first == "200" and '"ok":true' in out
        note(f"v6 OTA upload of the legacy image: http {first or '0'} after {time.time() - t0:.0f} s: {out[:140]!r}")
    pi_leave_ap()
    check("down_upload_accepted", accepted, "v6 answered ok + reboot" if accepted else "no ok answer from /api/ota/upload")
    if not accepted:
        return
    time.sleep(20)
    psu.output(False)
    time.sleep(4)
    con = Console(wican, 2000000)
    psu.output(True)
    hit = con.wait_for(r"Running firmware version: (\S+)", 60)
    time.sleep(4)                                  # the legacy moves UART0 to 4 Mbaud at ~5 s
    lines = con.snapshot()
    con.close()
    save_console(os.path.join(logdir, "downgrade_legacy_boot.console.log"), lines)
    version = re.search(r"Running firmware version: (\S+)", hit[1]).group(1) if hit else None
    check("down_legacy_boots", version is not None and version.startswith("v4"), version or "no version line in 60 s")
    fs = [l for _, l in lines if re.search(r"LittleFS|littlefs|Partition size", l)]
    for l in fs[:6]:
        note("fs: " + l[:120])
    size = next((re.search(r"total: (\d+)", l) for l in fs if "Partition size" in l), None)
    table = {0x600000: "the legacy table", 0x5C0000: "v6's table"}.get(int(size.group(1)), "an unknown size") if size else "no size line"
    check("down_storage_mounted", any("mounted successfully" in l for l in fs) and not any(l.startswith("E (") for l in fs),
          (f"total {size.group(1)} B = {table}" if size else "no 'Partition size' line") + ("; " + fs[-1][:80] if fs else ""))
    e = [l for _, l in lines if l.startswith("E (")]
    note(f"legacy E lines in the readable window: {len(e)}" + ("; first: " + e[0][:100] if e else ""))
    legacy_ssid = next((m.group(0) for _, l in lines for m in [re.search(r"WiCAN_[0-9a-fA-F]{12}", l)] if m), ssid)
    cfg = None
    if pi_join_ap(legacy_ssid, FACTORY_PSK):
        deadline = time.time() + 60
        while time.time() < deadline and cfg is None:
            st, body = pi_http("GET", "/load_config", timeout=8)
            if st == 200 and body.startswith("{"):
                cfg = body
            else:
                time.sleep(4)
    pi_leave_ap()
    check("down_legacy_web_up", cfg is not None, "/load_config answers on the factory AP" if cfg else "no /load_config answer on the AP with @meatpi#")
    if cfg is not None:
        open(os.path.join(logdir, "downgrade_load_config.json"), "w", encoding="utf-8").write(cfg)
        m = re.search(r'"sleep_volt"\s*:\s*"([^"]*)"', cfg)   # the legacy echoes the stored JSON, spaces and all
        value = m.group(1) if m else "?"
        if marked and migrated:
            # the new layout formatted the legacy 6 MB filesystem (its config.json
            # with it): the way back is a fresh legacy device, by design
            check("down_legacy_fresh_after_migration", value != MARK_SLEEP_VOLT,
                  f"sleep_volt {value}: the legacy defaults (the mark {MARK_SLEEP_VOLT} went with the formatted filesystem)")
        elif marked:
            check("down_legacy_settings_kept", value == MARK_SLEEP_VOLT, f"sleep_volt {value} (marked {MARK_SLEEP_VOLT} before the update)")
        else:
            note(f"legacy sleep_volt after the downgrade: {value} (no mark set in this run)")

def upload_through_legacy(image, ssid):
    """The image through the legacy firmware's /upload/ota.bin from the Pi
    on the legacy AP: (the v6 /api/info answer or None, tries, last http).
    The legacy answers 303 and reboots right after it, so the answer can be
    lost with the association (run 2: http 0 after 134 s, the legacy still
    running): the device coming back with a v6 version is the judge and
    the upload is retried, up to three times."""
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", image, "rpi001:/tmp/ota_bench_image.bin"], check=False)
    joined = True
    tries = 0
    status = 0
    back = None
    while joined and back is None and tries < 3:
        tries += 1
        t0 = time.time()
        rc, out = ssh("rm -f /tmp/ota_bench_body; curl -s -m 600 -o /tmp/ota_bench_body -w '%{http_code}' "
                      f"-F 'file=@/tmp/ota_bench_image.bin;filename={os.path.basename(image)}' "
                      f"http://{AP_IP}/upload/ota.bin; echo; head -c 200 /tmp/ota_bench_body 2>/dev/null", 660)
        first = out.split("\n", 1)[0].strip() if out else ""
        status = int(first) if first.isdigit() else 0
        note(f"legacy OTA upload try {tries}: http {status} after {time.time() - t0:.0f} s: {out[:120]!r}")
        pi_leave_ap()
        time.sleep(40)                             # the legacy reboot, the v6 first boot (+ a migration restart)
        joined = pi_join_ap(ssid, FACTORY_PSK)
        deadline = time.time() + 60
        while joined and time.time() < deadline and back is None:
            st, info = pi_http("GET", "/api/info", timeout=5)
            if st == 200 and '"fw_version"' in info:
                back = info
            elif st == 404:
                break                              # the legacy firmware still answers: upload again
            else:
                time.sleep(4)
    return back, tries, status


def upload_through_v6(image, ssid):
    """The image through v6's own /api/ota/upload (the stuck user's rescue):
    the /api/info answer of the device back on its AP, or None."""
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", image, "rpi001:/tmp/ota_bench_image2.bin"], check=False)
    if not pi_join_ap(ssid, FACTORY_PSK):
        return None
    t0 = time.time()
    rc, out = ssh("rm -f /tmp/ota_bench_body; curl -s -m 600 -o /tmp/ota_bench_body -w '%{http_code}' "
                  f"-F 'firmware=@/tmp/ota_bench_image2.bin;filename={os.path.basename(image)}' "
                  f"http://{AP_IP}/api/ota/upload; echo; head -c 200 /tmp/ota_bench_body 2>/dev/null", 660)
    note(f"v6 OTA upload: {out[:140]!r} after {time.time() - t0:.0f} s")
    pi_leave_ap()
    time.sleep(40)
    if not pi_join_ap(ssid, FACTORY_PSK):
        return None
    deadline = time.time() + 60
    while time.time() < deadline:
        st, info = pi_http("GET", "/api/info", timeout=5)
        if st == 200 and '"fw_version"' in info:
            return info
        time.sleep(4)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="the v6 app image to upload through the legacy OTA page")
    ap.add_argument("--legacy-dir", default="", help="dir with the factory flash set (the release zip, extracted)")
    ap.add_argument("--wican", default="auto")
    ap.add_argument("--psu", default="auto")
    ap.add_argument("--backup", default="auto", help="DUT address for the settings backup, auto = the bench lease, none = skip")
    ap.add_argument("--skip-flash", action="store_true",
                    help="the DUT already runs the legacy firmware with its AP up: start at the OTA (needs --ssid)")
    ap.add_argument("--ssid", default="", help="the legacy AP name with --skip-flash / --downgrade-only (WiCAN_<12 hex>)")
    ap.add_argument("--downgrade", action="store_true", help="then the user goes back to the legacy firmware through v6's OTA page")
    ap.add_argument("--via", default="", help="the stuck image (alfa-02) through the legacy page first, then --image through v6's OTA page: the rescue path")
    ap.add_argument("--downgrade-only", action="store_true",
                    help="only that stage, on a DUT that runs v6 on its AP with the factory password (needs --ssid)")
    a = ap.parse_args()
    if (a.skip_flash or a.downgrade_only) and not re.match(r"WiCAN_[0-9a-fA-F]{12}$", a.ssid):
        print("OTA FROM FACTORY FAIL: --skip-flash / --downgrade-only need --ssid WiCAN_<12 hex>")
        return 1

    legacy_dir = a.legacy_dir or os.path.join(REPO, "..", "..", "..", "wican-fw", "build")
    apps = sorted(glob.glob(os.path.join(legacy_dir, "wican-fw_obd_pro_v4*.bin")))
    missing = [f for f in LEGACY_FILES if not os.path.exists(os.path.join(legacy_dir, f))]
    if missing or not apps:
        print(f"OTA FROM FACTORY FAIL: legacy set incomplete in {legacy_dir}: missing {missing or 'the app'}")
        return 1
    legacy_app = apps[-1]
    if not os.path.exists(a.image):
        print(f"OTA FROM FACTORY FAIL: no image {a.image}")
        return 1

    run = time.strftime("%Y%m%d_%H%M")
    logdir = os.path.join(REPO, "test-reports", "logs", f"ota_from_factory_{run}")
    os.makedirs(logdir, exist_ok=True)
    note(f"legacy set: {legacy_dir} ({os.path.basename(legacy_app)}); image: {a.image}; logs: {logdir}")
    wican = bench_ports.resolve(a.wican, "wican_console", "COM254")
    psu = OwonPsu(bench_ports.resolve(a.psu, "psu", "COM50"))
    try:
        psu.meas_current()
    except (AssertionError, ValueError):
        pass
    psu.set_voltage(13.5)
    psu.output(True)

    if a.downgrade_only:
        downgrade_stage(wican, psu, legacy_app, a.ssid, logdir, False)
        psu.set_voltage(13.5)
        psu.output(True)
        psu.close()
        print("OTA FROM FACTORY FAIL: " + ", ".join(fails) if fails else "OTA FROM FACTORY DOWNGRADE PASS")
        return 1 if fails else 0

    # ---- backup --------------------------------------------------------------
    if a.backup != "none":
        ip = DUT_LEASE if a.backup == "auto" else a.backup
        rc, out = ssh(f"curl -s -m 20 http://{ip}/api/settings/backup")
        ok = rc == 0 and out.startswith("{") and '"wifi_manager"' in out
        check("backup_taken", ok, f"{len(out)} bytes from {ip}" if ok else f"no backup from {ip}")
        if ok:
            open(os.path.join(logdir, "settings_backup.json"), "w", encoding="utf-8").write(out)

    if a.skip_flash:
        con, ssid = None, a.ssid
        note(f"--skip-flash: the DUT runs the legacy firmware already, AP {ssid}")
    else:
        con, ssid = flash_legacy(wican, psu, legacy_dir, legacy_app, logdir)
        if con is None:
            psu.close()
            print("OTA FROM FACTORY FAIL: " + ", ".join(fails))
            return 1

    # ---- the OTA through the legacy web UI's route ----------------------------------
    joined = pi_join_ap(ssid, FACTORY_PSK)
    check("pi_on_legacy_ap", joined, ssid)
    ota_status = 0
    tries = 0
    back = None
    marked = False
    if joined:
        if a.downgrade:
            marked, detail = legacy_mark()
            check("legacy_marked", marked, detail)
    if joined:
        back, tries, ota_status = upload_through_legacy(a.via or a.image, ssid)
    stuck = re.search(r'"fw_version":"([^"]+)"', back or "")
    if a.via:
        check("stuck_image_running", stuck is not None, stuck.group(1) if stuck else "the stuck image never came back on the AP")
        if stuck is not None:
            note(f"the rescue: {os.path.basename(a.image)} through v6's own /api/ota/upload")
            back = upload_through_v6(a.image, ssid)
    history = ""
    if back is not None and pi_join_ap(ssid, FACTORY_PSK):
        st, history = pi_http("GET", "/api/restart/history", timeout=8)
        open(os.path.join(logdir, "v6_restart_history_after_ota.json"), "w", encoding="utf-8").write(history)
    pi_leave_ap()
    fw = re.search(r'"fw_version":"([^"]+)"', back or "")
    check("ota_accepted", fw is not None, f"{tries} upload(s) through the legacy page, last http {ota_status}")
    check("v6_back_on_its_ap", fw is not None and fw.group(1).startswith("v6"),
          fw.group(1) if fw else "no /api/info answer on the AP after the upload(s)")
    check("v6_migrated_on_first_boot", '"planned_reason":"partition_migrate"' in history,
          "the restart history holds a partition_migrate record" if '"planned_reason":"partition_migrate"' in history
          else "no partition_migrate record in /api/restart/history (read before the cold boot wipes the ring)")
    if con is not None:
        con.close()
    if not joined or fw is None:
        psu.close()
        print("OTA FROM FACTORY FAIL: " + ", ".join(fails))
        return 1

    # ---- the v6 boot, from power-on ------------------------------------------------------
    psu.output(False)
    time.sleep(4)
    con = Console(wican, 2000000)
    psu.output(True)
    since = 0.0
    up = con.wait_for(r"main: WiCAN Pro up", 90)
    check("v6_up", up is not None, "WiCAN Pro up" if up else "no 'WiCAN Pro up' within 90 s of power-on")
    time.sleep(10)
    lines = con.snapshot()
    e_lines, mount, persist, fault, ota, boot = boot_lines(lines, since)
    save_console(os.path.join(logdir, "v6_boot.console.log"), lines, since)
    note(f"boot: {boot or '?'}")
    slot = "ota_0" if a.via else "ota_1"     # two updates in a row land back in ota_0
    check(f"v6_running_from_{slot}", ota == slot, f"running from {ota}")
    check("v6_settings_mounted", not mount, mount[0][:120] if mount else "no mount error")
    check("v6_no_persist_failures", persist == 0, f"{persist} persist failed lines")
    check("v6_no_error_lines", len(e_lines) == 0,
          f"{len(e_lines)} E lines" + ("; first: " + e_lines[0][:110] if e_lines else ""))
    check("v6_no_boot_fault", fault is None, fault or "no boot_errors fault")

    # ---- the user's first save and the factory reset, through the AP -------------------
    joined = pi_join_ap(ssid, FACTORY_PSK)
    check("pi_on_v6_ap", joined, ssid)
    if joined:
        st, info = pi_http("GET", "/api/info")
        note(f"/api/info {st}: {info[:140]}")
        st, faults = pi_http("GET", "/api/faults")
        note(f"/api/faults {st}: {faults[:200]}")
        open(os.path.join(logdir, "v6_faults.json"), "w", encoding="utf-8").write(faults)
        check("v6_no_latched_faults", st == 200 and '"faults":[]' in faults,
              "no latched fault code" if '"faults":[]' in faults else faults[:120])
        st, wifi = pi_http("GET", "/api/settings/wifi_manager")
        body = None
        if st == 200 and wifi.startswith("{"):
            import json
            obj = json.loads(wifi)
            obj.pop("degraded", None)
            obj.pop("pending_reboot", None)
            obj["ap_password"] = NEW_AP_PSK      # flat keys; GET redacts passwords to ""
            body = json.dumps(obj)
        mark = len(con.snapshot())
        st, ans = pi_http("PUT", "/api/settings/wifi_manager", body) if body else (0, "no wifi_manager object to edit")
        time.sleep(2)
        after = [l for _, l in con.snapshot()[mark:] if "settings_manager" in l]
        open(os.path.join(logdir, "v6_save.txt"), "w", encoding="utf-8").write(
            f"PUT /api/settings/wifi_manager (ap_password changed) -> {st}\n{ans}\n\nconsole:\n" + "\n".join(after))
        check("save_persists", st == 200 and not any("persist failed" in l for l in after),
              f"http {st}; " + (after[0][:110] if after else "console quiet"))

        mark = len(con.snapshot())
        since2 = con.snapshot()[-1][0] + 0.001
        st, ans = pi_http("POST", "/api/settings/factory_reset", '{"confirm":"factory-reset"}', 30)
        time.sleep(3)
        after = [l for _, l in con.snapshot()[mark:] if "settings_manager" in l or "factory" in l]
        reboot = con.wait_for(r"restart_tracker: boot #\d+", 60, since2)
        open(os.path.join(logdir, "v6_factory_reset.txt"), "w", encoding="utf-8").write(
            f"POST /api/settings/factory_reset -> {st}\n{ans}\n\nconsole:\n" + "\n".join(after)
            + f"\n\nreboot: {reboot[1] if reboot else 'none within 60 s'}")
        check("factory_reset_works", st == 200 and reboot is not None,
              f"http {st} {ans[:80]!r}; " + (after[0][:100] if after else "console quiet")
              + ("; rebooted" if reboot else "; no reboot"))
        if reboot:
            time.sleep(30)
            e2, mount2, persist2, fault2, _, _ = boot_lines(con.snapshot(), since2)
            check("after_reset_clean", len(e2) == 0 and persist2 == 0 and fault2 is None,
                  f"{len(e2)} E lines, {persist2} persist failed, fault: {fault2 or 'none'}")
    pi_leave_ap()
    save_console(os.path.join(logdir, "v6_full.console.log"), con.snapshot(), since)
    con.close()
    if a.downgrade:
        downgrade_stage(wican, psu, legacy_app, ssid, logdir, marked,
                        '"planned_reason":"partition_migrate"' in history)
    psu.set_voltage(13.5)
    psu.output(True)
    psu.close()
    note("the DUT is left in this state on purpose (the reproduction for a fix); restore: erase, "
         "build.ps1 -Flash, factory_flash_bench.py --restore-only <backup>")
    if fails:
        print("OTA FROM FACTORY FAIL: " + ", ".join(fails))
        return 1
    print("OTA FROM FACTORY PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
