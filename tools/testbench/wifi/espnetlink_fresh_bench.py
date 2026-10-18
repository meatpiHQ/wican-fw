#!/usr/bin/env python3
"""ESPNetLink out-of-the-box scenario: a user unboxes a WiCAN Pro and an
ESPNetLink, plugs them together, and configures the WiCAN over its own
WiFi AP (phone/laptop joined to `WiCAN_<id>`, password @meatpi#) while
the zero-touch pairing runs. Nothing is pre-configured on either device.

Two paths since 2026-10-12 (the WiCAN's default `espnetlink.mode` is
`usb_rndis`; the dongle ships `usb_mode=auto`, class rndis, LTE shared
over USB, and nothing about a cut ever persists on it):

  --mode usb_rndis (default)
  erase  both devices' flash (esptool erase-flash) and flash both builds
  boot   PSU cold cycle, both consoles captured from power-on
  user   the Pi joins the fresh WiCAN AP as soon as it appears and STAYS
         there (--no-client skips this)
  pair   identify (api >= 9) -> the ensure-share pass: since 2026-10-15
         the dongle ships USB sharing OFF (a PC on the cable keeps its
         own internet), so the WiCAN turns it on = ONE planned dongle
         restart, then "dongle USB uplink already configured" on the
         re-enumerated wire; no credentials, no cut, no WiCAN restart;
         the dongle boots three times after an erase (its own AP password
         provisioning, the sharing restart) and never unexpectedly. A
         dongle that already shares skips the restart (boots twice).
  link   uplink = espnetlink (USB), pair_state ncm_up, GPS polls flow over
         the wire; the view over the WiCAN AP says so; `espnetlink repair`
         (a VBUS cycle) brings the wire back by itself

  --mode wifi_modem
  the usb_rndis path first (that is what a fresh pair does), then the
  user picks WiFi modem on the USB page (one planned restart), the
  pairing HOLDS behind the factory AP password, the user changes it (one
  restart), identify -> credentials -> store -> cut -> ONE more restart,
  the STA joins the dongle's AP WHILE the user's client sits on the WiCAN
  AP (field-hit 2026-08-31), the stored key is read back into the Pi's
  `espnl-client` profile, and the AP-client hazard (repair with the phone
  parked) must re-join within 150 s

Every WiCAN restart must be planned (restart_tracker), console E lines
== 0 on both consoles (same whitelist as the mode bench; the dongle's
first boot after an erase still logs littlefs "Corrupted dir pair" twice,
whitelisted on the dongle console only until its filesystem gets the
WiCAN's blank-partition probe).

  python espnetlink_fresh_bench.py --wican COM254 --dongle COM252 --psu COM50
         [--mode usb_rndis|wifi_modem] [--wican-build <dir>]
         [--dongle-build <dir>] [--no-erase] [--no-client]

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
NEW_AP_PSK = "bench-pass-1"        # what "the user" changes it to (the gate demands it)
DONGLE_CON = "espnl-client"        # Pi profile for the dongle's AP
CURL = f"curl -s -m 10 --retry 2 --retry-delay 2 --interface {USER_IF}"
E_WHITELIST = ("Invalid MMIE", "select() timeout",
               "Failed to open a new connection",
               "Connection failed, sock < 0",
               "tcp_read error")   # the cut severs the link mid-read
# the dongle firmware still logs littlefs' "Corrupted dir pair" twice on
# its first boot after an erase (the WiCAN got the blank-partition probe
# on 2026-09-05, the dongle has not): whitelisted on the dongle console
DONGLE_E_WHITELIST = E_WHITELIST + ("Corrupted dir pair",)

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

    def __init__(self, port, baud, name, whitelist=E_WHITELIST):
        self.name = name
        self.whitelist = whitelist
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

    def count(self, pattern, since=0.0):
        rx = re.compile(pattern)
        return sum(1 for ts, l in self.snapshot()
                   if ts >= since and rx.search(l))

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

    def cmd_text(self, line, wait=2.5):
        """Send a console command; return everything printed within wait."""
        mark = len(self.snapshot())
        self.write(line)
        time.sleep(wait)
        return "\n".join(l for _, l in self.snapshot()[mark:])

    def e_lines(self):
        return [l for _, l in self.snapshot()
                if l.startswith("E (") and
                not any(w in l for w in self.whitelist)]


# esptool v5 spelling (default-reset / erase-flash): the IDF v6 venv has
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
    """The `espnetlink` console command, parsed (the usb line carries
    `console_only=` between attached and pair_state since 2026-10-12)."""
    txt = con.cmd_text("espnetlink")
    st = {}
    m = re.search(r"ESPNETLINK: enabled=(\d) mode=(\w+) auto_pair=\d "
                  r"paired=(\d) ssid='([^']*)'.* uplink=(\S+) host=(\S+)",
                  txt)
    if m:
        st.update(enabled=m.group(1) == "1", mode=m.group(2),
                  paired=m.group(3) == "1", ssid=m.group(4),
                  uplink=m.group(5), host=m.group(6))
    m = re.search(r"usb: attached=(\d)(?: console_only=(\d))? "
                  r"pair_state=(\w+) cuts=(\d+)", txt)
    if m:
        st.update(attached=m.group(1) == "1",
                  console_only=m.group(2) == "1",
                  pair_state=m.group(3), cuts=int(m.group(4)))
    m = re.search(r"gps: valid=(\d).*?\| polls=(\d+) fail=(\d+)", txt)
    if m:
        st.update(gps_valid=m.group(1) == "1", polls=int(m.group(2)),
                  poll_fails=int(m.group(3)))
    return st


def restart_stats(con):
    l = con.cmd("restart_tracker", r"Boots: \d+, unexpected resets: \d+")
    m = re.search(r"Boots: (\d+), unexpected resets: (\d+)", l or "")
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wican", required=True)
    ap.add_argument("--dongle", required=True)
    ap.add_argument("--psu", required=True)
    ap.add_argument("--mode", choices=("usb_rndis", "wifi_modem"),
                    default="usb_rndis")
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
    dc = Console(a.dongle, 115200, "dongle", DONGLE_E_WHITELIST)
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
        note(f"fresh wifi_manager mode={fresh_mode} (3 = apsta)")

        # the WiCAN's fresh dongle mode (2026-10-12: usb_rndis)
        started = wc.wait_for(r"espnetlink: started \((\w+),", 60)
        check("default: espnetlink mode usb_rndis out of the box",
              started is not None and
              re.search(r"started \((\w+),", started[1]).group(1)
              == "usb_rndis", started[1][-70:] if started else "not seen")

        # dongle first boot: per-device AP password provisioning
        prov = dc.wait_for(r"per-device AP password provisioned", 40)
        if a.no_erase:
            note("dongle: provisioning " + ("seen" if prov else "not seen")
                 + " (already provisioned on an earlier boot: --no-erase)")
        else:
            check("dongle: first-boot AP password provisioning + reboot",
                  prov is not None,
                  f"{prov[0]:.0f} s" if prov else "not seen")
        mode_line = dc.wait_for(r"wifi_modem: usb_mode=(\w+) ", 60)
        check("dongle: ships usb_mode=auto (USB on at every boot)",
              mode_line is not None and
              re.search(r"usb_mode=(\w+) ", mode_line[1]).group(1) == "auto",
              mode_line[1][-60:] if mode_line else "not seen")
        composite = dc.wait_for(r"Starting CherryUSB (\S+)", 60)
        check("dongle: ships RNDIS + the CDC console",
              composite is not None and "RNDIS+CDC" in composite[1],
              composite[1][-70:] if composite else "not seen")

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
        # returning: hammer the re-join so the test is deterministic
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

        def user_put_submit(component, edit, what):
            """The user's settings change over the WiCAN AP: GET, edit,
            PUT, submit (one planned restart)."""
            r = ssh_run(
                f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; sleep 2; "
                f"doc=$({CURL} http://{WICAN_AP_IP}/api/settings/{component}); "
                f"body=$(echo \"$doc\" | python3 -c \"import json,sys;"
                f"d=json.load(sys.stdin);{edit};print(json.dumps(d))\"); "
                f"{CURL} -X PUT -H 'Content-Type: application/json' "
                f"-d \"$body\" http://{WICAN_AP_IP}/api/settings/{component}; "
                f"echo; {CURL} -X POST -d '{{}}' -H 'Content-Type: "
                f"application/json' http://{WICAN_AP_IP}/api/settings/submit; "
                f"echo")
            note(f"user: {what}: " + " ".join(r.stdout.split())[-90:])

        # ---- USB host must be on out of the box (or the user turns it on)
        usb_line = ""
        for _ in range(4):   # the query can land in a restart window
            usb_line = wc.cmd("usb", r"usb host: (enabled|disabled)") or ""
            if usb_line:
                break
            time.sleep(4)
        usb_on = "usb host: enabled" in usb_line
        check("default: USB host enabled out of the box (zero-touch)",
              usb_on, usb_line.strip()[:50])
        t_pair = 0.0
        if not usb_on and not a.no_client and ap_ssid:
            user_put_submit("usb_host_manager", "d['enabled']=True",
                            "enabling the USB host (one restart)")
            rb0 = wc.wait_for(r"config applied: mode=", 90, since=t_pair)
            check("user: WiCAN restarted with the USB host enabled",
                  rb0 is not None)
            t_pair = rb0[0] if rb0 else time.time() - wc.t0
            note("user: phone re-joining the WiCAN AP after the restart: "
                 + ("on" if phone_rejoin(40) else "NOT back"))

        # ---- the usb_rndis zero-touch (what every fresh pair does) -----
        ident = wc.wait_for(r"espnetlink: identified ESPNetLink", 120,
                            since=t_pair)
        check("rndis: dongle identified over USB", ident is not None,
              f"{ident[0]:.0f} s" if ident else "not seen")
        m_api = re.search(r"api (\d+)\)", ident[1]) if ident else None
        check("rndis: dongle firmware is current (api >= 9: rndis + sharing "
              "shipped)", m_api is not None and int(m_api.group(1)) >= 9,
              ident[1][-70:] if ident else "not seen")
        # 2026-10-18: the dongle ships `lte_upstream_pppos.ncm_share` OFF
        # again (2026-10-15: a PC on the cable keeps its own internet), so
        # the first contact of a fresh pair is the WiCAN's ensure-share pass
        # turning sharing on: ONE planned dongle restart, then the
        # re-identify over the re-enumerated wire finds nothing to change.
        # A dongle that already shares (--no-erase on a paired pair, or a
        # 2026-10-12 build) skips that restart.
        recfg = wc.wait_for(r"dongle USB uplink reconfigured \(class rndis, "
                            r"ncm_share on\)", 30, since=t_pair)
        ready = wc.wait_for(r"dongle USB uplink already configured", 60,
                            since=(recfg[0] if recfg else t_pair))
        if recfg:
            check("rndis: the ensure-share pass turned the dongle's USB "
                  "sharing on (class rndis, ncm_share on): one planned "
                  "dongle restart, then nothing to change",
                  ready is not None,
                  f"reconfigured {recfg[0]:.0f} s, already configured "
                  + (f"{ready[0]:.0f} s" if ready else "not seen"))
            check("rndis: the sharing restart was planned on the dongle "
                  "(reset=software planned=config_apply)",
                  dc.find(r"restart_tracker: boot #\d+ .*reset=software "
                          r"planned=config_apply", since=recfg[0]) is not None)
        else:
            check("rndis: nothing to change on the dongle (class rndis, "
                  "ncm_share already on): no dongle restart",
                  ready is not None,
                  f"{ready[0]:.0f} s" if ready else "not seen")
        share_restarts = 1 if recfg else 0
        check("rndis: no key read, no cut, no WiCAN restart",
              wc.find(r"credentials ok|usb_data off requested|rebooting to "
                      r"apply|pairing is ON HOLD", since=t_pair) is None)
        up = wc.wait_for(r"uplink: none -> espnetlink \(USB\)", 90,
                         since=t_pair)
        check("rndis: uplink = espnetlink (USB) at 192.168.7.1",
              up is not None, f"{up[0]:.0f} s" if up else "not seen")
        st = espnl_status(wc)
        check("rndis: espnetlink status ncm_up over the wire",
              st.get("pair_state") == "ncm_up" and
              st.get("uplink") == "espnetlink_usb" and
              st.get("host") == "192.168.7.1", str(st)[:120])
        p1 = st.get("polls", 0)
        time.sleep(8)
        p2 = espnl_status(wc).get("polls", 0)
        check("rndis: GPS/health polls flowing over the wire", p2 > p1,
              f"{p1} -> {p2}")
        boots = dc.count(r"restart_tracker: boot #")
        want = (2 if not a.no_erase else 1) + share_restarts
        check(f"rndis: the dongle booted exactly {want} times (its "
              "provisioning restart"
              + (", the sharing restart" if share_restarts else "")
              + " and nothing else)", boots == want, f"boots={boots}")

        # the view over the WiCAN AP
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
            check("rndis view: GET /api/espnetlink over the WiCAN AP shows "
                  "mode usb_rndis + uplink espnetlink_usb",
                  d.get("mode") == "usb_rndis" and
                  d.get("uplink") == "espnetlink_usb",
                  f"mode={d.get('mode')} uplink={d.get('uplink')}")

        # a VBUS cycle: the dongle re-enumerates and the wire returns by itself
        t_rep = time.time() - wc.t0
        wc.write("espnetlink repair")
        lost = wc.wait_for(r"uplink: espnetlink \(USB\) -> none", 60,
                           since=t_rep)
        back = wc.wait_for(r"uplink: none -> espnetlink \(USB\)", 120,
                           since=(lost[0] if lost else t_rep) + 0.1)
        check("rndis: the wire returns by itself after a VBUS cycle",
              lost is not None and back is not None,
              f"+{back[0] - lost[0]:.0f} s" if lost and back else "not seen")

        if a.mode == "wifi_modem":
            run_wifi_modem_path(a, wc, dc, ap_ssid, phone_rejoin,
                                user_put_submit)

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
        check("health: dongle console E lines == 0 (first-boot littlefs "
              "lines whitelisted)", len(de) == 0, f"E={len(de)}")
        for l in de[:8]:
            note("dongle E-line: " + l)
    finally:
        ssh_run(f"sudo -n nmcli con down {USER_CON} >/dev/null 2>&1; "
                f"sudo -n nmcli con delete {USER_CON} >/dev/null 2>&1; true")
        # under test-reports/logs (gitignored): a dongle console can carry
        # a cached GPS position, which never goes into a tracked file
        logdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "..", "..", "test-reports", "logs",
                              "espnetlink_fresh_" + a.mode + "_"
                              + time.strftime("%Y%m%d_%H%M"))
        os.makedirs(logdir, exist_ok=True)
        for con, fn in ((wc, "fresh_wican_console.log"),
                        (dc, "fresh_dongle_console.log")):
            with open(os.path.join(logdir, fn), "w", encoding="utf-8") as f:
                for ts, l in con.snapshot():
                    f.write(f"{ts:8.2f} {l}\n")
        note("raw console logs: " + os.path.relpath(logdir)
             + " (fresh_wican_console.log, fresh_dongle_console.log)")
        wc.close()
        dc.close()
    return finish()


def run_wifi_modem_path(a, wc, dc, ap_ssid, phone_rejoin, user_put_submit):
    """The user picks WiFi modem after the fresh USB pairing: the hold
    behind the factory AP password, the password change, the key store
    and cut, the STA on the dongle's AP with the phone parked, the key
    read back, the AP-client hazard."""
    if a.no_client or not ap_ssid:
        note("wifi_modem path needs the user's client on the WiCAN AP: "
             "skipped")
        fails.append("wifi_modem: skipped (no client)")
        return

    # ---- the user picks WiFi modem (one planned restart) -----------------
    t_sw = time.time() - wc.t0
    user_put_submit("espnetlink", "d['mode']='wifi_modem'",
                    "WiFi modem selected (one restart)")
    rb = wc.wait_for(r"espnetlink: started \(wifi_modem,", 90, since=t_sw)
    check("wifi: WiCAN restarted in wifi_modem mode", rb is not None)
    t_pair = rb[0] if rb else time.time() - wc.t0
    note("user: phone re-joining the WiCAN AP after the restart: "
         + ("on" if phone_rejoin(40) else "NOT back"))

    # ---- the hold: pairing waits for a non-factory AP password ----------
    # (2026-09-07 field-hit: the key store is refused while the AP has
    # the factory password; the firmware HOLDS pairing and warns instead
    # of churning into "Not an ESPNetLink / gave up")
    hold = wc.wait_for(r"pairing is ON HOLD|on hold \(factory AP "
                       r"password\)", 60, since=t_pair)
    check("hold: zero-touch held while the AP has the factory password",
          hold is not None, hold[1][-70:] if hold else "not seen")
    st_h = espnl_status(wc)
    check("hold: no pairing churn (machine idle or parked in hold)",
          st_h.get("pair_state") in (None, "idle", "hold") and
          not st_h.get("paired"), str(st_h)[:90])

    # the user sets a new AP password over the AP (as the banner asks).
    # The anchor is taken BEFORE the submit: when the submit's answer is
    # lost over the fresh association, curl's timeout and retries return
    # the helper 10 to 34 s later, after the restart, and an anchor taken
    # then lands on the NEXT boot (the pairing restart) and hides the whole
    # pairing sequence (2026-10-18 run 1 of this path).
    t_pw = time.time() - wc.t0
    user_put_submit("wifi_manager", f"d['ap_password']='{NEW_AP_PSK}'",
                    "new AP password + submit")
    rbpw = wc.wait_for(r"config applied: mode=", 90, since=t_pw)
    check("user: WiCAN restarted with the new AP password", rbpw is not None)
    t_pair = rbpw[0] if rbpw else time.time() - wc.t0
    ssh_run(f"sudo -n nmcli con modify {USER_CON} "
            f"802-11-wireless-security.psk '{NEW_AP_PSK}'")
    note("user: phone re-joining with the new password: "
         + ("on" if phone_rejoin(40) else "NOT back"))

    # ---- zero-touch pairing (runs by itself after that restart) ---------
    ident = wc.wait_for(r"espnetlink: identified ESPNetLink", 120,
                        since=t_pair)
    check("pair: dongle identified over USB", ident is not None,
          f"{ident[0]:.0f} s" if ident else "not seen")
    creds = wc.wait_for(r"espnetlink: credentials ok|stored; reboot "
                        r"after the cut|paired with", 90, since=t_pair)
    check("pair: credentials read + stored", creds is not None,
          creds[1][:70] if creds else "not seen")
    # the request line can be lost in the console burst around the cut;
    # the drop itself ("usb gone ... cut #1") is the same proof
    cut = wc.wait_for(r"usb_data off requested|usb gone: cable is power "
                      r"only", 60, since=creds[0] if creds else t_pair)
    check("pair: USB data cut requested", cut is not None)
    dcut = dc.wait_for(r"USB data CUT at the mux by the host", 60,
                       since=t_pair)
    check("pair: the dongle reports the cut, until its next boot",
          dcut is not None and "until the next boot" in dcut[1])
    rb = wc.wait_for(r"rebooting to apply", 60, since=t_pair)
    check("pair: ONE WiCAN restart to apply the new key",
          rb is not None, f"{rb[0]:.0f} s" if rb else "not seen")
    t_reboot = rb[0] if rb else time.time() - wc.t0

    # ---- after the restart: STA joins the dongle AP ---------------------
    started = wc.wait_for(r"wifi_manager: started \(mode=(\d+)", 60,
                          since=t_reboot)
    check("join: wifi_manager restarted with STA (mode 1 or 3)",
          started is not None and
          re.search(r"mode=(\d+)", started[1]).group(1) in ("1", "3"),
          started[1][-40:] if started else "not seen")
    back = phone_rejoin(30)
    t_back = time.time() - wc.t0
    note(f"user: phone back on the AP at +{t_back - t_reboot:.0f} s "
         f"after the restart ({'on' if back else 'NOT back'})")
    hop = wc.find(r"AP has \d+ clients but", since=t_reboot)
    if hop:
        note("wifi_manager: " + hop[1][-90:])
    up = wc.wait_for(r"uplink: none -> espnetlink \(AP\)", 150,
                     since=t_reboot)
    check("join: STA joined the dongle AP, uplink=espnetlink WITH a client "
          "on the WiCAN AP", up is not None,
          f"+{up[0] - t_reboot:.0f} s after restart" if up
          else "not within 150 s")
    st = espnl_status(wc)
    check("join: espnetlink status paired + uplink", st.get("paired")
          and st.get("uplink") == "espnetlink", str(st)[:120])

    # ---- the user's view over the WiCAN AP ------------------------------
    d = {}
    for _ in range(4):
        r = ssh_run(f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; "
                    f"sleep 2; {CURL} http://{WICAN_AP_IP}/api/espnetlink")
        try:
            d = json.loads(r.stdout)
            break
        except json.JSONDecodeError:
            time.sleep(3)
    check("view: GET /api/espnetlink over the WiCAN AP shows paired + "
          "uplink", d.get("paired") is True and
          d.get("uplink") == "espnetlink",
          f"paired={d.get('paired')} uplink={d.get('uplink')}")
    # the stored dongle key -> the Pi's dongle profile (the fresh dongle
    # provisioned a new AP password); secrets are masked on
    # /api/settings/<comp>, the backup document is the complete one
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
    #      (The 2026-08-31 fix bounds the AP-client pause to 60 s.) Since
    #      2026-10-12 the rebooted dongle comes back with USB on, so the
    #      WiCAN re-reads the key (unchanged) and cuts again on the way.
    phone_rejoin(20)
    t_rep = time.time() - wc.t0
    wc.write("espnetlink repair")
    lost = wc.wait_for(r"uplink: espnetlink \(AP\) -> none", 90,
                       since=t_rep)
    note("repair: dongle VBUS-cycled; uplink lost: "
         + (f"+{lost[0] - t_rep:.0f} s" if lost else "not seen"))
    recut = wc.wait_for(r"credentials ok: .* unchanged", 90, since=t_rep)
    check("hazard: the rebooted dongle was re-read (key unchanged) and cut "
          "again, no WiCAN restart",
          recut is not None and
          wc.find(r"rebooting to apply", since=t_rep) is None)
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


def finish():
    if fails:
        print("ESPNL FRESH FAIL: " + ", ".join(fails))
        return 1
    print("ESPNL FRESH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
