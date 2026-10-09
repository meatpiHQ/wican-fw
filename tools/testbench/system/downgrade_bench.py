#!/usr/bin/env python3
"""A unit on v6 (its own partition table) taken back to the legacy firmware
through v6's OTA page, and forward again through the legacy page
(2026-10-10, Ali: "lets test what happens if we downgrade"). The question
behind it: once v6 migrates a unit's partition table, can the user still go
back, and what does the legacy firmware make of v6's layout (its `storage`
is 5888 KB at 0xA2E000 and holds v6's /data LittleFS; the legacy mounts by
the name with format-if-mount-fails on).

  flash    erase; the v6 set (bootloader, v6 partition table, ota data) from
           --build and --image at 0x10000, without a reset; PSU cold boot
           with the console: a clean first boot (0 E lines), the AP up
  mark     rpi001 joins the AP (@meatpi#) and sets a new AP password through
           /api/settings/wifi_manager + submit (the device reboots on it):
           a persisted v6 setting to find again after the round trip
  down     the legacy image (--legacy-dir) POSTed to v6's /api/ota/upload
           (multipart `firmware=`), {"ok":true,...,"reboot":true}; PSU cold
           boot with the console: `Running firmware version: v4.5x`, the
           storage mount lines (mounted or formatted, the partition size the
           legacy reports), its AP up with the factory password, the web
           root answering
  up       the v6 image POSTed to the legacy /upload/ota.bin (303, up to
           three tries: the USB radio loses long uploads); PSU cold boot:
           v6 again, 0 E lines, no boot fault, and the AP password set in
           `mark` still in force (the settings partition survived the
           legacy firmware)

  python tools/testbench/system/downgrade_bench.py --image <v6.bin>
         [--build build] [--legacy-dir <the v4.51p zip, extracted>]
         [--wican auto] [--psu auto] [--resume-up --ssid WiCAN_<id>]

Verdict: DOWNGRADE PASS / DOWNGRADE FAIL: <names>. Erases the DUT; leaves it
on v6 with the AP password `ota-bench-2026` and no STA (restore as in
ota_from_factory_bench.py).
"""
import argparse
import glob
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
from wican_fresh_bench import Console, check, note, fails  # noqa: E402
import ota_from_factory_bench as F  # noqa: E402

NEW_AP_PSK = "ota-bench-2026"


def cold_boot(wican, psu, pattern, timeout):
    """PSU off, a fresh console, PSU on: (console, the line matching pattern)."""
    psu.output(False)
    time.sleep(4)
    con = Console(wican, 2000000)
    psu.output(True)
    hit = con.wait_for(pattern, timeout)
    return con, hit


def wait_back(ssid, psk, want, timeout=100):
    """rpi001 joins the AP and waits for /api/info (v6) or the web root
    (legacy) to answer: the info JSON, 'legacy', or None."""
    deadline = time.time() + timeout
    if not F.pi_join_ap(ssid, psk):
        return None
    while time.time() < deadline:
        st, info = F.pi_http("GET", "/api/info", timeout=5)
        if st == 200 and '"fw_version"' in info and want == "v6":
            return info
        if st == 404 and want == "legacy":
            st2, root = F.pi_http("GET", "/", timeout=8)
            if st2 == 200:
                return "legacy"
        time.sleep(4)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="the v6 app image")
    ap.add_argument("--build", default=os.path.join(REPO, "build"), help="the v6 build dir (bootloader, table, ota data)")
    ap.add_argument("--legacy-dir", default="", help="dir with the legacy app wican-fw_obd_pro_v4*.bin")
    ap.add_argument("--wican", default="auto")
    ap.add_argument("--psu", default="auto")
    ap.add_argument("--resume-up", action="store_true",
                    help="the DUT already runs the legacy firmware under v6's table (after `down`): only the web check and `up`; needs --ssid")
    ap.add_argument("--ssid", default="", help="the AP name with --resume-up (WiCAN_<12 hex>)")
    a = ap.parse_args()
    if a.resume_up and not re.match(r"WiCAN_[0-9a-fA-F]{12}$", a.ssid):
        print("DOWNGRADE FAIL: --resume-up needs --ssid WiCAN_<12 hex>")
        return 1
    legacy_dir = a.legacy_dir or os.path.join(REPO, "..", "..", "..", "wican-fw", "build")
    apps = sorted(glob.glob(os.path.join(legacy_dir, "wican-fw_obd_pro_v4*.bin")))
    if not apps or not os.path.exists(a.image):
        print("DOWNGRADE FAIL: need the legacy app and --image")
        return 1
    legacy_app = apps[-1]
    run = time.strftime("%Y%m%d_%H%M")
    logdir = os.path.join(REPO, "test-reports", "logs", f"downgrade_{run}")
    os.makedirs(logdir, exist_ok=True)
    note(f"v6: {a.image} with {a.build}; legacy: {legacy_app}; logs: {logdir}")
    wican = bench_ports.resolve(a.wican, "wican_console", "COM254")
    psu = OwonPsu(bench_ports.resolve(a.psu, "psu", "COM50"))
    try:
        psu.meas_current()
    except (AssertionError, ValueError):
        pass
    psu.set_voltage(13.5)
    psu.output(True)

    if a.resume_up:
        ssid = legacy_ssid = a.ssid
        note(f"--resume-up: the legacy firmware runs under v6's table already, AP {ssid}")
    else:
        # ---- flash: v6 on its own table -------------------------------------------------
        check("erase", F.esptool(wican, "no-reset", "erase-flash"))
        args = ["write-flash", "--flash-mode", "dio", "--flash-freq", "80m", "--flash-size", "16MB",
                "0x0", os.path.join(a.build, "bootloader", "bootloader.bin"),
                "0x8000", os.path.join(a.build, "partition_table", "partition-table.bin"),
                "0xd000", os.path.join(a.build, "ota_data_initial.bin"),
                "0x10000", a.image]
        check("v6_flashed", F.esptool(wican, "no-reset", *args))
        if fails:
            psu.close()
            print("DOWNGRADE FAIL: " + ", ".join(fails))
            return 1
        con, up = cold_boot(wican, psu, r"main: WiCAN Pro up", 90)
        time.sleep(8)
        lines = con.snapshot()
        e, mount, persist, fault, ota, boot = F.boot_lines(lines, 0)
        F.save_console(os.path.join(logdir, "1_v6_fresh.console.log"), lines)
        check("v6_fresh_clean", up is not None and len(e) == 0 and fault is None,
              f"up={up is not None}, {len(e)} E lines, fault {fault or 'none'}, running from {ota}")
        ssid = next((m.group(0) for _, l in lines for m in [re.search(r"WiCAN_[0-9a-fA-F]{12}", l)] if m), None)
        check("v6_ap_up", ssid is not None, ssid or "no AP name on the console")
        con.close()
        if fails:
            psu.close()
            print("DOWNGRADE FAIL: " + ", ".join(fails))
            return 1

        # ---- mark: a persisted v6 setting -------------------------------------------------
        marked = False
        if F.pi_join_ap(ssid, F.FACTORY_PSK):
            st, wifi = F.pi_http("GET", "/api/settings/wifi_manager")
            if st == 200 and wifi.startswith("{"):
                obj = json.loads(wifi)
                obj.pop("degraded", None)
                obj.pop("pending_reboot", None)
                obj["ap_password"] = NEW_AP_PSK
                st, ans = F.pi_http("PUT", "/api/settings/wifi_manager", json.dumps(obj))
                note(f"PUT wifi_manager -> {st} {ans[:80]}")
                if st == 200:
                    st2, sub = F.pi_http("POST", "/api/settings/submit", "{}", 30)
                    note(f"submit -> {st2} {sub[:60]}")
                    marked = st2 == 200 or st2 == 0
        F.pi_leave_ap()
        check("mark_ap_password_set", marked, NEW_AP_PSK if marked else "the PUT or the submit failed")
        time.sleep(30)                                         # the reboot on submit
        info = wait_back(ssid, NEW_AP_PSK, "v6")
        check("mark_in_force", info is not None, "the AP takes the new password" if info else "no v6 on the AP with the new password")
        if fails:
            F.pi_leave_ap()
            psu.close()
            print("DOWNGRADE FAIL: " + ", ".join(fails))
            return 1

        # ---- down: the legacy image through v6's OTA page ---------------------------------
        F.ssh(f"rm -f /tmp/ota_bench_image.bin")
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", legacy_app, "rpi001:/tmp/ota_bench_legacy.bin"], check=False)
        t0 = time.time()
        rc, out = F.ssh("rm -f /tmp/ota_bench_body; curl -s -m 600 -o /tmp/ota_bench_body -w '%{http_code}' "
                        f"-F 'firmware=@/tmp/ota_bench_legacy.bin;filename={os.path.basename(legacy_app)}' "
                        f"http://{F.AP_IP}/api/ota/upload; echo; head -c 200 /tmp/ota_bench_body 2>/dev/null", 660)
        note(f"v6 OTA upload of the legacy image: {out[:160]!r} after {time.time() - t0:.0f} s")
        first = out.split("\n", 1)[0].strip() if out else ""
        check("down_upload_accepted", first == "200" and '"ok":true' in out, f"http {first or '0'}")
        F.pi_leave_ap()
        time.sleep(20)
        con, ver = cold_boot(wican, psu, r"Running firmware version: (\S+)", 60)
        time.sleep(4)                                          # the legacy moves UART0 to 4 Mbaud at ~5 s
        lines = con.snapshot()
        F.save_console(os.path.join(logdir, "2_legacy_under_v6_table.console.log"), lines)
        con.close()
        version = re.search(r"Running firmware version: (\S+)", ver[1]).group(1) if ver else None
        check("down_legacy_boots", version is not None and version.startswith("v4"), version or "no version line")
        fs = [l for _, l in lines if re.search(r"LittleFS|littlefs|Partition size", l)]
        for l in fs[:6]:
            note("fs: " + l[:120])
        check("down_storage_mounted", any("mounted successfully" in l for l in fs) and
              not any(re.match(r"E \(", l) for l in fs), fs[-1][:100] if fs else "no filesystem lines")
        size = next((re.search(r"total: (\d+)", l) for l in fs if "Partition size" in l), None)
        check("down_storage_is_v6_partition", size is not None and int(size.group(1)) == 0x5C0000,
              f"total {size.group(1)} B" if size else "no 'Partition size' line")
        e = [l for _, l in lines if re.match(r"E \(", l)]
        note(f"legacy E lines in the readable window: {len(e)}" + ("; first: " + e[0][:100] if e else ""))
        legacy_ssid = next((m.group(0) for _, l in lines for m in [re.search(r"WiCAN_[0-9a-fA-F]{12}", l)] if m), ssid)
    back = wait_back(legacy_ssid, F.FACTORY_PSK, "legacy", 90)
    check("down_legacy_web_up", back == "legacy", "the legacy web root answers on the factory AP" if back else "no legacy web root on the AP with @meatpi#")
    if fails:
        F.pi_leave_ap()
        psu.close()
        print("DOWNGRADE FAIL: " + ", ".join(fails))
        return 1

    # ---- up: v6 again through the legacy page ----------------------------------------------
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", a.image, "rpi001:/tmp/ota_bench_image.bin"], check=False)
    tries = 0
    info = None
    joined = True
    while joined and info is None and tries < 3:
        tries += 1
        t0 = time.time()
        rc, out = F.ssh("rm -f /tmp/ota_bench_body; curl -s -m 600 -o /tmp/ota_bench_body -w '%{http_code}' "
                        f"-F 'file=@/tmp/ota_bench_image.bin;filename={os.path.basename(a.image)}' "
                        f"http://{F.AP_IP}/upload/ota.bin; echo; head -c 100 /tmp/ota_bench_body 2>/dev/null", 660)
        note(f"legacy OTA upload try {tries}: {out[:100]!r} after {time.time() - t0:.0f} s")
        F.pi_leave_ap()
        time.sleep(40)
        info = wait_back(legacy_ssid, NEW_AP_PSK, "v6", 60)     # v6's own AP password again
        if info is None:
            joined = F.pi_join_ap(legacy_ssid, F.FACTORY_PSK)   # the legacy still up? upload again
            if joined:
                st, _ = F.pi_http("GET", "/api/info", timeout=5)
                joined = st == 404
    F.pi_leave_ap()
    fw = re.search(r'"fw_version":"([^"]+)"', info or "")
    check("up_v6_back_with_its_password", fw is not None, f"{tries} upload(s); " + (fw.group(1) if fw else "no v6 on the AP with the marked password"))
    con, up = cold_boot(wican, psu, r"main: WiCAN Pro up", 90)
    time.sleep(8)
    lines = con.snapshot()
    e, mount, persist, fault, ota, boot = F.boot_lines(lines, 0)
    F.save_console(os.path.join(logdir, "3_v6_back.console.log"), lines)
    con.close()
    check("up_v6_clean", up is not None and len(e) == 0 and persist == 0 and fault is None,
          f"up={up is not None}, {len(e)} E lines, {persist} persist failed, fault {fault or 'none'}, from {ota}")
    psu.set_voltage(13.5)
    psu.output(True)
    psu.close()
    if fails:
        print("DOWNGRADE FAIL: " + ", ".join(fails))
        return 1
    print("DOWNGRADE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
