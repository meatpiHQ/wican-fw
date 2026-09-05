#!/usr/bin/env python3
"""WiCAN Pro out-of-the-box scenario (device alone): erase the flash,
flash the build, cold-boot, and do what a new user does — join the
WiCAN's AP with a phone, open the web UI, configure the home WiFi through
the settings API exactly as the UI does, submit, and expect the device to
come up on the home network while the phone is still on its AP.

  erase   esptool erase-flash + write-flash (skip with --no-erase)
  boot    PSU cold cycle, console captured from power-on; first-boot
          defaults asserted from the log + console: wifi_manager mode=ap,
          0 STA networks, derived AP SSID `WiCAN_<id>`, USB host enabled
          (zero-touch ESPNetLink), espnetlink enabled + wifi_modem
  user    the Pi joins `WiCAN_<id>` / @meatpi# (the wifi_manager default)
          and STAYS there; GET / (web UI HTML), GET /api/settings (the
          component list), GET /api/settings/wifi_manager/schema
  config  PUT /api/settings/wifi_manager with sta_ssid/sta_password =
          the bench "home" hotspot (Pi `wican-bench` profile, SSID
          WICAN_TEST_AP) and mode=apsta, then POST /api/settings/submit
          -> ONE planned reboot
  join    after the reboot: STA got an IP on the home network WHILE the
          phone sits on the AP (wifi_manager's first-connect exemption);
          the AP stays up (apsta), the UI still answers on the AP
  health  restart_tracker: planned reboots only; console E lines == 0

A dongle on the USB connector is fine (it pairs in the background now
that the USB host defaults on) — the bench notes it and ignores it.

  python wican_fresh_bench.py --wican COM12 --psu COM17 [--build <dir>]
         [--no-erase]

Verdict: WICAN FRESH PASS / WICAN FRESH FAIL: <names>.
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
WICAN_AP_PSK = "@meatpi#"          # wifi_manager default AP password
HOME_CON = "wican-bench"           # the Pi hotspot playing "home"
HOME_IF = "wtest0"
CURL = (f"curl -s -m 10 --retry 2 --retry-delay 2 --compressed "
        f"--interface {USER_IF}")
E_WHITELIST = ("Invalid MMIE", "select() timeout",
               "Failed to open a new connection",
               "Connection failed, sock < 0",
               # (2026-09-05: "Corrupted dir pair" / "FAULT boot_errors" on
               # the first boot after an erase are NO LONGER whitelisted —
               # filesystem + settings_manager format a blank partition
               # quietly now; seeing them again is a regression)
               # espnetlink's HTTP client racing the dongle's cut/reboot
               # while it pairs in the background (transition noise)
               "tcp_read error", "delayed connect error")

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
    def __init__(self, port, baud):
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
        mark = len(self.snapshot())
        self.s.write(line.encode() + b"\r\n")
        end = time.time() + timeout
        rx = re.compile(pattern)
        while time.time() < end:
            for _, l in self.snapshot()[mark:]:
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
    if r.returncode != 0:
        note(f"esptool {args[0]} failed:\n" + r.stdout[-600:] + r.stderr[-300:])
    return r.returncode == 0


def psu_cycle(port, off_s=8):
    s = serial.Serial(port, 115200, timeout=2)
    s.write(b"*IDN?\n")
    s.readline()
    s.write(b"OUTP 0\n")
    time.sleep(off_s)
    s.write(b"OUTP 1\n")
    time.sleep(0.5)
    s.write(b"OUTP?\n")
    r = s.readline().strip()
    s.close()
    return r == b"1"


def restart_stats(con):
    l = con.cmd("restart_tracker", r"Boots: \d+, unexpected resets: \d+")
    m = re.search(r"Boots: (\d+), unexpected resets: (\d+)", l or "")
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def user_get(path):
    r = ssh_run(f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; "
                f"{CURL} http://{WICAN_AP_IP}{path}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wican", required=True)
    ap.add_argument("--psu", required=True)
    ap.add_argument("--build", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "build"))
    ap.add_argument("--no-erase", action="store_true")
    a = ap.parse_args()

    # the "home" network the user will type in: the Pi's hotspot
    r = ssh_run(f"nmcli -g 802-11-wireless.ssid con show {HOME_CON}; "
                f"sudo -n nmcli -s -g 802-11-wireless-security.psk con show "
                f"{HOME_CON}; sudo -n nmcli con up {HOME_CON} >/dev/null 2>&1; "
                f"nmcli -t dev status | grep '^{HOME_IF}:wifi:connected'")
    parts = r.stdout.split("\n")
    home_ssid = parts[0].strip() if len(parts) > 0 else ""
    home_psk = parts[1].strip() if len(parts) > 1 else ""
    check("setup: home hotspot up on the Pi", bool(home_ssid) and
          bool(home_psk) and "connected" in r.stdout, home_ssid)
    if fails:
        return finish()

    if not a.no_erase:
        check("erase: wican flash", esptool(a.wican, "erase-flash"))
        check("flash: wican build", esptool(a.wican, "write-flash",
                                            "@flash_args", cwd=a.build))
        if fails:
            return finish()

    con = Console(a.wican, 2000000)
    check("boot: PSU cold cycle", psu_cycle(a.psu))
    ssh_run(f"sudo -n nmcli con delete {USER_CON} >/dev/null 2>&1; true")
    try:
        # ---- first-boot defaults ----------------------------------------
        hit = con.wait_for(r"config applied: mode=(\d+) sta_networks=(\d+) "
                           r"ap_ssid=(WiCAN_\w+)", 60)
        m = re.search(r"mode=(\d+) sta_networks=(\d+) ap_ssid=(WiCAN_\w+)",
                      hit[1]) if hit else None
        ap_ssid = m.group(3) if m else ""
        check("defaults: wifi_manager mode=ap, 0 STA networks, derived "
              "AP SSID", m is not None and m.group(1) == "2" and
              m.group(2) == "0", hit[1][-60:] if hit else "not seen")
        started = con.wait_for(r"wifi_manager: started \(mode=", 60)
        check("defaults: wifi_manager started", started is not None)
        con.wait_for(r"usb_host_manager: (started|disabled)", 30)
        usb = ""
        for _ in range(4):
            usb = con.cmd("usb", r"usb host: (enabled|disabled)") or ""
            if usb:
                break
            time.sleep(3)
        check("defaults: USB host enabled (zero-touch ESPNetLink)",
              "usb host: enabled" in usb, usb.strip()[:60])
        nl = con.cmd("espnetlink", r"ESPNETLINK: enabled=") or ""
        check("defaults: espnetlink enabled, mode=wifi_modem, unpaired",
              "enabled=1 mode=wifi_modem" in nl and "paired=0" in nl,
              nl[:80])
        b0, u0 = restart_stats(con)
        check("defaults: restart_tracker reads (planned-only baseline)",
              b0 >= 1 and u0 == 0, f"boots={b0} unexpected={u0}")

        # ---- the user joins the AP and opens the UI ---------------------
        r = ssh_run(
            f"sudo -n nmcli con add type wifi ifname {USER_IF} "
            f"con-name {USER_CON} autoconnect no ssid '{ap_ssid}' "
            f"802-11-wireless-security.key-mgmt wpa-psk "
            f"802-11-wireless-security.psk '{WICAN_AP_PSK}' "
            f"802-11-wireless-security.psk-flags 0 >/dev/null && "
            f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1 && sleep 3 && "
            f"nmcli -t dev status | grep -q '^{USER_IF}:wifi:connected:"
            f"{USER_CON}' && echo ok")
        check("user: joined the fresh WiCAN AP with the default password",
              "ok" in r.stdout, f"{ap_ssid} / {WICAN_AP_PSK}")
        html = user_get("/") or ""
        check("user: web UI served on the AP", "<html" in html.lower() or
              "<!doctype" in html.lower(), f"{len(html)} bytes")
        comps = user_get("/api/settings")
        try:
            names = [c.get("name") for c in
                     json.loads(comps).get("components", [])]
        except (json.JSONDecodeError, AttributeError):
            names = []
        check("user: /api/settings lists the components",
              "wifi_manager" in names and "espnetlink" in names,
              f"{len(names)} components")
        schema = user_get("/api/settings/wifi_manager/schema")
        check("user: wifi_manager schema served", "sta_ssid" in schema)

        # ---- the user configures the home WiFi (as the UI does) -----------
        r = ssh_run(
            f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; "
            f"doc=$({CURL} http://{WICAN_AP_IP}/api/settings/wifi_manager); "
            f"body=$(echo \"$doc\" | python3 -c \"import json,sys;"
            f"d=json.load(sys.stdin);d['sta_ssid']='{home_ssid}';"
            f"d['sta_password']='{home_psk}';d['mode']='apsta';"
            f"print(json.dumps(d))\"); "
            f"{CURL} -X PUT -H 'Content-Type: application/json' -d \"$body\" "
            f"http://{WICAN_AP_IP}/api/settings/wifi_manager; echo; "
            f"back=$({CURL} http://{WICAN_AP_IP}/api/settings/wifi_manager | "
            f"python3 -c \"import json,sys;d=json.load(sys.stdin);"
            f"print(d.get('mode'), d.get('sta_ssid'))\"); echo \"staged: $back\"; "
            f"{CURL} -X POST -d '{{}}' -H 'Content-Type: application/json' "
            f"http://{WICAN_AP_IP}/api/settings/submit; echo")
        check("config: home WiFi + apsta staged (read back)",
              f"staged: apsta {home_ssid}" in r.stdout,
              " ".join(r.stdout.split())[-100:])
        t_sub = time.time() - con.t0
        # sta_networks=2 when a dongle paired in the background (its slot)
        # the submit reboots at once: the line may print while the ssh
        # command is still returning (slack), and a burst can garble one
        # line — either boot marker is proof
        rb = con.wait_for(r"config applied: mode=3 sta_networks=[12]|"
                          r"wifi_manager: started \(mode=3, [12] STA", 90,
                          since=max(0.0, t_sub - 6))
        check("config: ONE planned reboot applies apsta + the home network",
              rb is not None)
        t_boot = rb[0] if rb else t_sub
        ssh_run(f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1")
        note("user: phone re-joined the WiCAN AP after the reboot")

        # ---- join the home network with the phone still on the AP --------
        got = con.wait_for(r"wifi_manager: .*(got ip|STA got IP|connected to)",
                           120, since=t_boot)
        st = con.cmd("wifi", r"STA connected: (yes|no)") or ""
        ip = re.search(r"STA connected: yes \(([0-9.]+)\)", st)
        check("join: STA joined the home network WHILE the phone is on the "
              "AP", ip is not None, f"{st.strip()[:50]}"
              + (f" (+{got[0] - t_boot:.0f} s)" if got else ""))
        apl = con.cmd("wifi", r"AP started: (yes|no)") or ""
        check("join: AP still up (apsta)", "AP started: yes" in apl)
        again = user_get("/api/settings/wifi_manager")
        check("join: UI/API still answers on the AP after the join",
              "sta_ssid" in again)
        nl = con.cmd("espnetlink", r"ESPNETLINK: enabled=") or ""
        bg_pair = "paired=1" in nl
        if bg_pair:
            note("espnetlink: a dongle on the connector paired in the "
                 "background (expected with the USB host on by default; "
                 "that is one more planned reboot)")

        # ---- health -------------------------------------------------------
        b, u = restart_stats(con)
        expect = b0 + 1 + (1 if bg_pair else 0)
        check("health: planned reboots only", u == 0 and b == expect,
              f"boots {b0}->{b} (expected {expect}) unexpected {u}")
        e = con.e_lines()
        check("health: console E lines == 0", len(e) == 0, f"E={len(e)}")
        for l in e[:8]:
            note("E-line: " + l)
    finally:
        ssh_run(f"sudo -n nmcli con down {USER_CON} >/dev/null 2>&1; "
                f"sudo -n nmcli con delete {USER_CON} >/dev/null 2>&1; true")
        con.close()
    return finish()


def finish():
    if fails:
        print("WICAN FRESH FAIL: " + ", ".join(fails))
        return 1
    print("WICAN FRESH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
