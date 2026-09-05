#!/usr/bin/env python3
"""ESPNetLink out-of-the-box scenario: a user unboxes a WiCAN Pro and an
ESPNetLink, plugs them together, and configures the WiCAN over its own
WiFi AP (phone/laptop joined to `WiCAN_<id>`, password @meatpi#) while
the zero-touch pairing runs. Nothing is pre-configured on either device.

  erase  both devices' flash (esptool erase-flash) and flash both builds
  boot   PSU cold cycle, both consoles captured from power-on
  user   the Pi joins the fresh WiCAN AP as soon as it appears and STAYS
         there (--no-client skips this — the bare from-scratch path)
  pair   identify -> credentials -> store (wifi mode ap -> apsta) -> cut
         -> ONE WiCAN reboot
  join   after that reboot the STA must join the dongle's AP and the
         uplink must come up WHILE the user's client sits on the WiCAN AP
         (field-hit 2026-08-31: wifi_manager's AP-client pause blocked
         the very first association — fixed with a first-connect
         exemption + a bounded pause)
  view   what the user sees: GET /api/espnetlink over the WiCAN AP shows
         paired + uplink; the stored dongle key is read back and pushed
         into the Pi's `espnl-client` profile (the fresh dongle
         provisioned a new AP password on first boot)

Every WiCAN reboot must be planned (restart_tracker), console E lines
== 0 (same whitelist as the mode bench).

  python espnetlink_fresh_bench.py --wican COM12 --dongle COM16 --psu COM17
         [--wican-build <dir>] [--dongle-build <dir>] [--no-erase]
         [--no-client]

Verdict: ESPNL FRESH PASS / ESPNL FRESH FAIL: <names>.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

import serial

PI = "rpi001"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
USER_CON = "wican-fresh"           # temp Pi profile = the user's phone
USER_IF = "wtest1"
WICAN_AP_IP = "192.168.0.10"       # fresh WiCAN AP address (legacy default)
WICAN_AP_PSK = "@meatpi#"          # fresh WiCAN AP password (wifi_manager default)
DONGLE_CON = "espnl-client"        # Pi profile for the dongle's AP
CURL = f"curl -s -m 10 --retry 2 --retry-delay 2 --interface {USER_IF}"
E_WHITELIST = ("Invalid MMIE", "select() timeout",
               "Failed to open a new connection",
               "Connection failed, sock < 0",
               "tcp_read error")   # the cut severs the link mid-read
               # (2026-09-05: the first-boot "Corrupted dir pair" /
               # "FAULT boot_errors" lines are fixed in the WiCAN firmware
               # — blank partitions are formatted before the mount — and
               # are no longer whitelisted here. NOTE the dongle firmware
               # still logs them on ITS first boot after an erase; port
               # the same guard there before running this bench fresh.)

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""), flush=True)
    if not ok:
        fails.append(name)
    return ok


def note(msg):
    print("  " + msg, flush=True)


def ssh_run(cmd, timeout=90):
    try:
        return subprocess.run(SSH + [PI, cmd], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        note(f"ssh timeout: {cmd[:60]}...")
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")


class Console:
    """A serial port with a background reader: everything the device
    prints is kept (with a wall-clock stamp) and commands are answered by
    watching the same stream."""

    def __init__(self, port, baud, name):
        self.name = name
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.timeout = port, baud, 0.2
        self.s.dtr = False    # pre-set BEFORE open (CH344 EN pulse)
        self.s.rts = False
        self.s.open()
        self.lines = []
        self.lock = threading.Lock()
        self.t0 = time.time()
        self._buf = b""
        self._run = True
        self.th = threading.Thread(target=self._reader, daemon=True)
        self.th.start()

    def _reader(self):
        while self._run:
            try:
                b = self.s.read(4096)
            except serial.SerialException:
                break
            if not b:
                continue
            self._buf += b
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                txt = re.sub(r"\x1b\[[0-9;]*m", "",
                             raw.decode("utf-8", "replace")).rstrip("\r")
                with self.lock:
                    self.lines.append((time.time() - self.t0, txt))

    def close(self):
        self._run = False
        self.th.join(1)
        self.s.close()

    def write(self, line):
        self.s.write(line.encode() + b"\r\n")

    def snapshot(self):
        with self.lock:
            return list(self.lines)

    def find(self, pattern, since=0.0):
        rx = re.compile(pattern)
        for ts, l in self.snapshot():
            if ts >= since and rx.search(l):
                return ts, l
        return None

    def wait_for(self, pattern, timeout, since=0.0):
        end = time.time() + timeout
        while time.time() < end:
            hit = self.find(pattern, since)
            if hit:
                return hit
            time.sleep(1)
        return None

    def cmd(self, line, pattern, timeout=4.0):
        """Send a console command; return the first line matching pattern
        that appears after the send."""
        mark = len(self.snapshot())
        self.write(line)
        end = time.time() + timeout
        rx = re.compile(pattern)
        while time.time() < end:
            snap = self.snapshot()
            for _, l in snap[mark:]:
                if rx.search(l):
                    return l
            time.sleep(0.2)
        return None

    def e_lines(self):
        return [l for _, l in self.snapshot()
                if l.startswith("E (") and
                not any(w in l for w in E_WHITELIST)]


# esptool v5 spelling (default-reset / erase-flash) — the IDF v6 venv has
# it; a system python with an old esptool does not. Prefer the venv.
ESPTOOL_PY = next((p for p in (
    os.environ.get("ESPTOOL_PYTHON", ""),
    r"C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe",
    os.path.join(os.environ.get("IDF_PYTHON_ENV_PATH", ""), "Scripts",
                 "python.exe"),
) if p and os.path.exists(p)), sys.executable)


def esptool(port, *args, cwd=None):
    cmd = [ESPTOOL_PY, "-m", "esptool", "--chip", "esp32s3", "-p", port,
           "-b", "460800", "--before", "default-reset", "--after",
           "hard-reset", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                       timeout=400)
    ok = r.returncode == 0
    if not ok:
        note(f"esptool {args[0]} on {port} failed:\n" + r.stdout[-800:] +
             r.stderr[-400:])
    return ok


def psu_cycle(port, off_s=8):
    s = serial.Serial(port, 115200, timeout=2)
    s.write(b"*IDN?\n")
    s.readline()
    s.write(b"OUTP 0\n")
    time.sleep(off_s)
    s.write(b"OUTP 1\n")
    t0 = time.time()
    time.sleep(0.5)
    s.write(b"OUTP?\n")
    r = s.readline().strip()
    s.close()
    return t0, r == b"1"


def espnl_status(con):
    l = con.cmd("espnetlink", r"ESPNETLINK: enabled=")
    if not l:
        return {}
    m = re.search(r"mode=(\w+) auto_pair=\d paired=(\d) ssid='([^']*)'"
                  r".* uplink=(\S+) host=(\S+)", l)
    return dict(mode=m.group(1), paired=m.group(2) == "1", ssid=m.group(3),
                uplink=m.group(4), host=m.group(5)) if m else {}


def restart_stats(con):
    l = con.cmd("restart_tracker", r"Boots: \d+, unexpected resets: \d+")
    m = re.search(r"Boots: (\d+), unexpected resets: (\d+)", l or "")
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wican", required=True)
    ap.add_argument("--dongle", required=True)
    ap.add_argument("--psu", required=True)
    ap.add_argument("--wican-build", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "build"))
    ap.add_argument("--dongle-build", default=r"C:\Users\Ali\Desktop\Projects"
                    r"\ESPNetLink_Project\firmware\espnetlink-fw\build")
    ap.add_argument("--no-erase", action="store_true")
    ap.add_argument("--no-client", action="store_true")
    a = ap.parse_args()

    # ---- erase + flash ------------------------------------------------------
    if not a.no_erase:
        check("erase: dongle flash", esptool(a.dongle, "erase-flash"))
        check("erase: wican flash", esptool(a.wican, "erase-flash"))
        check("flash: dongle build",
              esptool(a.dongle, "write-flash", "@flash_args",
                      cwd=a.dongle_build))
        check("flash: wican build",
              esptool(a.wican, "write-flash", "@flash_args",
                      cwd=a.wican_build))
        if fails:
            return finish()

    # ---- cold boot, both consoles from power-on -----------------------------
    wc = Console(a.wican, 2000000, "wican")
    dc = Console(a.dongle, 115200, "dongle")
    t0, on = psu_cycle(a.psu)
    check("boot: PSU cold cycle", on)
    ssh_run(f"sudo -n nmcli con delete {USER_CON} >/dev/null 2>&1; true")
    try:
        # the fresh WiCAN AP (derived SSID) from its own config log
        hit = wc.wait_for(r"config applied: mode=\d+ sta_networks=\d+ "
                          r"ap_ssid=(WiCAN_\w+)", 60)
        ap_ssid = re.search(r"ap_ssid=(WiCAN_\w+)", hit[1]).group(1) \
            if hit else ""
        check("boot: fresh WiCAN AP SSID observed", bool(ap_ssid), ap_ssid)
        fresh_mode = re.search(r"config applied: mode=(\d+)",
                               hit[1]).group(1) if hit else "?"
        note(f"fresh wifi_manager mode={fresh_mode} (2 = ap)")

        # dongle first boot: per-device AP password provisioning
        prov = dc.wait_for(r"per-device AP password provisioned", 40)
        if a.no_erase:
            note("dongle: provisioning " + ("seen" if prov else "not seen")
                 + " (already provisioned on an earlier boot: --no-erase)")
        else:
            check("dongle: first-boot AP password provisioning + reboot",
                  prov is not None,
                  f"{prov[0]:.0f} s" if prov else "not seen")

        # ---- the user joins the WiCAN AP --------------------------------
        if not a.no_client and ap_ssid:
            r = ssh_run(
                f"sudo -n nmcli con add type wifi ifname {USER_IF} "
                f"con-name {USER_CON} autoconnect yes "
                f"connection.autoconnect-retries 0 "
                f"connection.autoconnect-priority 100 ssid '{ap_ssid}' "
                f"802-11-wireless-security.key-mgmt wpa-psk "
                f"802-11-wireless-security.psk '{WICAN_AP_PSK}' "
                f"802-11-wireless-security.psk-flags 0 "
                f">/dev/null && sudo -n nmcli con up {USER_CON} >/dev/null "
                f"2>&1 && sleep 3 && nmcli -t dev status | "
                f"grep -q '^{USER_IF}:wifi:connected:{USER_CON}' && echo ok")
            check("user: joined the fresh WiCAN AP (stays for the run)",
                  "ok" in r.stdout, ap_ssid)

        # a real phone is back on the AP within seconds of the beacons
        # returning — hammer the re-join so the test is deterministic
        def phone_rejoin(deadline_s):
            end = time.time() + deadline_s
            while time.time() < end:
                r = ssh_run(f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; "
                            f"nmcli -t dev status | grep -q '^{USER_IF}:wifi:"
                            f"connected:{USER_CON}' && echo on", timeout=30)
                if "on" in r.stdout:
                    return True
                time.sleep(1)
            return False

        # ---- USB host must be on out of the box (or the user turns it on)
        usb_line = ""
        for _ in range(4):   # the query can land in the pairing-reboot window
            usb_line = wc.cmd("usb", r"usb host: (enabled|disabled)") or ""
            if usb_line:
                break
            time.sleep(4)
        usb_on = "usb host: enabled" in usb_line
        check("default: USB host enabled out of the box (zero-touch)",
              usb_on, usb_line.strip()[:50])
        t_pair = 0.0     # USB on from boot: pairing may already be done
        if not usb_on and not a.no_client and ap_ssid:
            note("user: enabling the USB host on the USB page "
                 "(settings PUT + submit) — the WiCAN reboots")
            r = ssh_run(
                f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; sleep 2; "
                f"doc=$({CURL} http://{WICAN_AP_IP}/api/settings/"
                f"usb_host_manager); body=$(echo \"$doc\" | python3 -c "
                f"\"import json,sys;d=json.load(sys.stdin);"
                f"d['enabled']=True;print(json.dumps(d))\"); "
                f"{CURL} -X PUT -H 'Content-Type: application/json' "
                f"-d \"$body\" http://{WICAN_AP_IP}/api/settings/"
                f"usb_host_manager; echo; "
                f"{CURL} -X POST -d '{{}}' -H 'Content-Type: application/json' "
                f"http://{WICAN_AP_IP}/api/settings/submit; echo")
            note("user: " + " ".join(r.stdout.split())[-120:])
            rb0 = wc.wait_for(r"config applied: mode=", 90, since=t_pair)
            check("user: WiCAN rebooted with the USB host enabled",
                  rb0 is not None)
            t_pair = rb0[0] if rb0 else time.time() - wc.t0
            note("user: phone re-joining the WiCAN AP after the reboot: "
                 + ("on" if phone_rejoin(40) else "NOT back"))

        # ---- zero-touch pairing -----------------------------------------
        ident = wc.wait_for(r"espnetlink: identified ESPNetLink", 120,
                            since=t_pair)
        check("pair: dongle identified over USB", ident is not None,
              f"{ident[0]:.0f} s" if ident else "not seen")
        creds = wc.wait_for(r"espnetlink: credentials ok|stored; reboot "
                            r"after the cut|paired with", 90, since=t_pair)
        check("pair: credentials read + stored", creds is not None,
              creds[1][:70] if creds else "not seen")
        modechg = wc.find(r"wifi mode ap -> apsta", since=t_pair)
        check("pair: wifi mode ap -> apsta staged (STA needed)",
              modechg is not None)
        # the request line can be lost in the console burst around the cut;
        # the drop itself ("usb gone ... cut #1") is the same proof
        cut = wc.wait_for(r"usb_data off requested|usb gone: cable is power "
                          r"only", 60, since=creds[0] if creds else t_pair)
        check("pair: USB data cut requested", cut is not None)
        rb = wc.wait_for(r"rebooting to apply", 60, since=t_pair)
        check("pair: ONE WiCAN reboot to apply the new key/mode",
              rb is not None, f"{rb[0]:.0f} s" if rb else "not seen")
        t_reboot = rb[0] if rb else time.time() - wc.t0

        # ---- after the reboot: STA joins the dongle AP ------------------
        started = wc.wait_for(r"wifi_manager: started \(mode=(\d+)", 60,
                              since=t_reboot)
        check("join: wifi_manager restarted with STA (mode 1 or 3)",
              started is not None and
              re.search(r"mode=(\d+)", started[1]).group(1) in ("1", "3"),
              started[1][-40:] if started else "not seen")
        if not a.no_client:
            back = phone_rejoin(30)
            t_back = time.time() - wc.t0
            sta_first = wc.find(r"STA got IP|wifi_manager: connecting|"
                                r"connecting to", since=t_reboot)
            note(f"user: phone back on the AP at +{t_back - t_reboot:.0f} s "
                 f"after the reboot ({'on' if back else 'NOT back'}); "
                 f"STA first attempt seen: "
                 f"{'yes' if sta_first else 'not yet'}")
            hop = wc.find(r"AP has \d+ clients but", since=t_reboot)
            if hop:
                note("wifi_manager: " + hop[1][-90:])
        up = wc.wait_for(r"uplink: none -> espnetlink \(AP\)", 150,
                         since=t_reboot)
        check("join: STA joined the dongle AP, uplink=espnetlink "
              + ("WITH a client on the WiCAN AP" if not a.no_client
                 else "(no AP client)"),
              up is not None, f"+{up[0] - t_reboot:.0f} s after reboot"
              if up else "not within 150 s")
        st = espnl_status(wc)
        check("join: espnetlink status paired + uplink", st.get("paired")
              and st.get("uplink") == "espnetlink", str(st))

        # ---- the user's view over the WiCAN AP --------------------------
        if not a.no_client:
            d = {}
            for _ in range(4):
                r = ssh_run(f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; "
                            f"sleep 2; {CURL} http://{WICAN_AP_IP}/api/espnetlink")
                try:
                    d = json.loads(r.stdout)
                    break
                except json.JSONDecodeError:
                    time.sleep(3)
            check("view: GET /api/espnetlink over the WiCAN AP shows "
                  "paired + uplink", d.get("paired") is True and
                  d.get("uplink") == "espnetlink",
                  f"paired={d.get('paired')} uplink={d.get('uplink')}")
            # the stored dongle key -> the Pi's dongle profile (the fresh
            # dongle provisioned a new AP password)
            # secrets are masked on /api/settings/<comp>; the backup
            # document is the complete one (what the UI's Backup button gets)
            wm = {}
            for _ in range(4):
                r = ssh_run(f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; "
                            f"{CURL} http://{WICAN_AP_IP}/api/settings/backup")
                try:
                    doc = json.loads(r.stdout)
                    wm = doc.get("components", {}).get("wifi_manager", {})
                    wm = wm.get("data", wm)
                    break
                except (json.JSONDecodeError, AttributeError):
                    time.sleep(3)
            key = ""
            for i in range(1, 6):
                if wm.get(f"fallback{i}_ssid", "") == st.get("ssid", "-"):
                    key = wm.get(f"fallback{i}_password", "")
            check("view: dongle key stored in a wifi_manager fallback slot",
                  bool(key))
            if key:
                ssh_run(f"sudo -n nmcli con modify {DONGLE_CON} "
                        f"802-11-wireless.ssid '{st['ssid']}' "
                        f"wifi-sec.psk '{key}'")
                note(f"Pi {DONGLE_CON} profile updated for the fresh dongle")

        # ---- the hazard, deterministically: phone parked on the AP, then a
        #      dongle glitch -> the STA must RE-join while the phone stays.
        #      (The first association after the pairing reboot usually wins
        #      the race against a re-associating phone; a re-join with the
        #      phone already parked is where the AP-client pause bites:
        #      unfixed wifi_manager defers indefinitely, the 2026-08-31 fix
        #      bounds it to 60 s.)
        if not a.no_client:
            phone_rejoin(20)
            t_rep = time.time() - wc.t0
            wc.write("espnetlink repair")
            lost = wc.wait_for(r"uplink: espnetlink \(AP\) -> none", 90,
                               since=t_rep)
            note("repair: dongle VBUS-cycled; uplink lost: "
                 + (f"+{lost[0] - t_rep:.0f} s" if lost else "not seen"))
            back = wc.wait_for(r"uplink: none -> espnetlink \(AP\)", 150,
                               since=(lost[0] if lost else t_rep) + 0.1)
            on = ssh_run(f"nmcli -t dev status | grep -q '^{USER_IF}:wifi:"
                         f"connected:{USER_CON}' && echo on").stdout
            check("hazard: STA re-joined the dongle AP within 150 s with the "
                  "phone parked on the WiCAN AP",
                  back is not None,
                  (f"+{back[0] - (lost[0] if lost else t_rep):.0f} s after "
                   f"the drop" if back else "never") +
                  f"; phone {'still on' if 'on' in on else 'NOT on'} the AP")
            hop = wc.find(r"AP has \d+ clients but", since=t_rep)
            if hop:
                note("wifi_manager: " + hop[1][-90:])

        # ---- health -------------------------------------------------------
        b, u = restart_stats(wc)
        check("health: no unexpected WiCAN resets", u == 0,
              f"boots={b} unexpected={u}")
        e = wc.e_lines()
        check("health: WiCAN console E lines == 0", len(e) == 0,
              f"E={len(e)}")
        for l in e[:8]:
            note("E-line: " + l)
        de = dc.e_lines()
        check("health: dongle console E lines == 0", len(de) == 0,
              f"E={len(de)}")
        for l in de[:8]:
            note("dongle E-line: " + l)
    finally:
        ssh_run(f"sudo -n nmcli con down {USER_CON} >/dev/null 2>&1; "
                f"sudo -n nmcli con delete {USER_CON} >/dev/null 2>&1; true")
        for con, fn in ((wc, "fresh_wican_console.log"),
                        (dc, "fresh_dongle_console.log")):
            with open(fn, "w", encoding="utf-8") as f:
                for ts, l in con.snapshot():
                    f.write(f"{ts:8.2f} {l}\n")
        note("raw console logs: fresh_wican_console.log, "
             "fresh_dongle_console.log")
        wc.close()
        dc.close()
    return finish()


def finish():
    if fails:
        print("ESPNL FRESH FAIL: " + ", ".join(fails))
        return 1
    print("ESPNL FRESH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
