#!/usr/bin/env python3
"""ESPNetLink WiFi-modem roaming leg: the drive-away / drive-home story.

Scenario (espnetlink_link in `mode=wifi_modem`, paired, dongle cut):

  at home    -> the DUT sits on its PRIMARY WiFi; espnetlink uplink=wifi,
                the GPS/health polls are stopped (the dongle is unused)
  drive away -> the home AP disappears; wifi_manager falls back to the
                dongle's AP; uplink=espnetlink, polls + GPS fix resume
  drive home -> the home AP is visible again; roam-to-preferred
                (sta_roam_interval_s) migrates the STA home; the polls
                stop again. The dongle is never rebooted: its GPS fix
                survives every transition.

"Home" is played by a TEMPORARY hotspot on the bench Pi's wtest0 using
the DUT's OWN primary SSID + password (read once from
GET /api/settings/backup over the DUT's AP, never printed). wtest0's
normal profile (wican-bench) is parked for the run and restored after.
The DUT is observed over the SERIAL console only — a client on the
DUT's AP would pause its STA reconnects (wifi_manager's AP-client rule)
and falsify the scenario.

Prereqs: DUT console on COM1175 (capture stopped), rpi001 reachable,
espnetlink dongle attached + paired (`espnetlink` -> paired=1
uplink=espnetlink), `wifi_manager.sta_roam_interval_s` <= 120 for a
practical roam-home wait (60 recommended; the bench reads the value and
budgets accordingly).

  python espnetlink_roam_bench.py [COM1175]

Verdict line: ESPNL ROAM PASS / ESPNL ROAM FAIL: <names>.
"""
import json
import re
import subprocess
import sys
import time

import serial

COM = sys.argv[1] if len(sys.argv) > 1 else "COM1175"
BAUD = 2000000
PI = "rpi001"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
OBSERVER_CON = "espnl-wifitest"      # Pi profile joined to the DUT's AP
OBSERVER_IF = "wtest1"
DUT_AP_IP = "192.168.0.10"           # the DUT on its own AP
HOME_CON = "wican-roam-home"         # temp hotspot this bench creates
HOME_IF = "wtest0"
HOME_IF_CON = "wican-bench"          # wtest0's normal profile (restored)

fails = []
e_lines = 0


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""), flush=True)
    if not ok:
        fails.append(name)


def ssh_run(cmd, timeout=30):
    return subprocess.run(SSH + [PI, cmd], capture_output=True, text=True,
                          timeout=timeout)


def open_console():
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = COM, BAUD, 0.2
    s.dtr = False   # pre-set BEFORE open: the CH344 pulses EN otherwise
    s.rts = False
    s.open()
    return s


def console_cmd(con, line, wait=2.5):
    """One console round-trip; also tallies E-lines seen along the way."""
    global e_lines
    con.reset_input_buffer()
    con.write(line.encode() + b"\r\n")
    end = time.time() + wait
    buf = b""
    while time.time() < end:
        b = con.read(4096)
        if b:
            buf += b
    txt = re.sub(r"\x1b\[[0-9;]*m", "", buf.decode("utf-8", "replace"))
    e_lines += sum(1 for l in txt.splitlines() if l.startswith("E ("))
    return txt


def espnl_status(con):
    txt = console_cmd(con, "espnetlink")
    st = {}
    m = re.search(r"ESPNETLINK: enabled=(\d) mode=(\w+) auto_pair=\d "
                  r"paired=(\d) ssid='([^']*)'.* uplink=(\S+)", txt)
    if m:
        st.update(enabled=m.group(1) == "1", mode=m.group(2),
                  paired=m.group(3) == "1", ssid=m.group(4),
                  uplink=m.group(5))
    m = re.search(r"gps: valid=(\d).*?polls=(\d+) fail=(\d+)", txt)
    if m:
        st.update(gps_valid=m.group(1) == "1", polls=int(m.group(2)),
                  poll_fails=int(m.group(3)))
    return st


def wifi_ssid(con):
    txt = console_cmd(con, "wifi -s")
    m = re.search(r"SSID: (.+)", txt)
    return m.group(1).strip() if m else ""


def wait_for(con, what, pred, budget_s, poll_s=5):
    t0 = time.time()
    while time.time() - t0 < budget_s:
        if pred():
            return time.time() - t0
        time.sleep(poll_s)
    print(f"  timeout waiting for {what} ({budget_s:.0f} s)", flush=True)
    return -1.0


def main():
    con = open_console()

    # ---- phase 0: preflight + read the home credentials ------------------
    st = espnl_status(con)
    check("preflight: espnetlink paired + wifi_modem",
          st.get("enabled") and st.get("paired") and
          st.get("mode") == "wifi_modem", str(st))
    if fails:
        return finish()

    home_ssid = home_psk = ""
    roam_interval = 300
    detail = ""
    for attempt in range(3):
        r = ssh_run(f"sudo -n nmcli con up {OBSERVER_CON} >/dev/null 2>&1; "
                    f"sleep {4 + 3 * attempt}; "
                    f"curl -s -m 8 --interface {OBSERVER_IF} "
                    f"http://{DUT_AP_IP}/api/settings/backup; "
                    f"sudo -n nmcli con down {OBSERVER_CON} >/dev/null 2>&1",
                    timeout=60)
        try:
            doc = json.loads(r.stdout)
            wm = doc.get("components", {}).get("wifi_manager", {})
            wm = wm.get("data", wm)  # backup wraps objects in {version,data}
            home_ssid = wm.get("sta_ssid", "")
            home_psk = wm.get("sta_password", "")
            roam_interval = int(wm.get("sta_roam_interval_s", 300))
            break
        except (json.JSONDecodeError, AttributeError, ValueError):
            detail = f"attempt {attempt + 1}: {r.stdout[:80]!r}"
    check("preflight: primary WiFi credentials from the DUT backup",
          bool(home_ssid) and bool(home_psk),
          f"ssid={home_ssid!r}" + (f"; {detail}" if not home_ssid else ""))
    if fails:
        return finish()
    print(f"  home='{home_ssid}' roam_interval={roam_interval}s "
          f"dongle='{st['ssid']}'", flush=True)

    # temp hotspot profile on wtest0 (down; parked wican-bench first)
    ssh_run(f"sudo -n nmcli con down {HOME_IF_CON} >/dev/null 2>&1; "
            f"sudo -n nmcli con delete {HOME_CON} >/dev/null 2>&1; true")
    # a DISTINCT shared subnet: NM's default (10.42.0.1) collides with the
    # Pi's other hotspot and rolls the fresh AP back moments after "up"
    r = ssh_run("sudo -n nmcli con add type wifi ifname " + HOME_IF +
                f" con-name {HOME_CON} autoconnect no ssid '{home_ssid}' "
                f"802-11-wireless.mode ap 802-11-wireless.band bg "
                f"ipv4.method shared ipv4.addresses 10.42.9.1/24 "
                f"wifi-sec.key-mgmt wpa-psk "
                f"wifi-sec.psk '{home_psk}' >/dev/null && echo ok")
    check("setup: temp home hotspot profile", "ok" in r.stdout, HOME_CON)
    if fails:
        return finish()

    try:
        # ---- phase 1: away baseline (on the dongle) ----------------------
        dt = wait_for(con, "uplink=espnetlink",
                      lambda: espnl_status(con).get("uplink") ==
                      "espnetlink", 90)
        check("away: uplink is the dongle", dt >= 0)
        p1 = espnl_status(con)
        time.sleep(8)
        p2 = espnl_status(con)
        check("away: GPS polls flowing",
              p2.get("polls", 0) > p1.get("polls", 0),
              f"{p1.get('polls')} -> {p2.get('polls')}")
        check("away: GPS fix live", p2.get("gps_valid") is True)

        # ---- phase 2: drive home -----------------------------------------
        r = ssh_run(f"sudo -n nmcli con up {HOME_CON} >/dev/null && sleep 4 "
                    f"&& nmcli -t dev status | grep -q '^{HOME_IF}:wifi:"
                    f"connected:{HOME_CON}' && echo ok", timeout=60)
        check("home: hotspot up and stable", "ok" in r.stdout)
        budget = roam_interval + 120
        dt = wait_for(con, "roam to home",
                      lambda: wifi_ssid(con) == home_ssid, budget, 10)
        check("home: STA roamed to the primary", dt >= 0,
              f"{dt:.0f} s (interval {roam_interval} s)")
        st = espnl_status(con)
        check("home: espnetlink uplink=wifi", st.get("uplink") == "wifi")
        p1 = espnl_status(con)
        time.sleep(8)
        p2 = espnl_status(con)
        check("home: dongle polls stopped",
              p2.get("polls") == p1.get("polls"),
              f"polls held at {p1.get('polls')}")

        # ---- phase 3: drive away ------------------------------------------
        r = ssh_run(f"sudo -n nmcli con down {HOME_CON} && echo ok")
        check("away2: hotspot down", "ok" in r.stdout)
        dt = wait_for(con, "fallback to the dongle",
                      lambda: espnl_status(con).get("uplink") ==
                      "espnetlink", 150, 5)
        check("away2: fell back to the dongle", dt >= 0, f"{dt:.0f} s")
        dt = wait_for(con, "gps valid again",
                      lambda: espnl_status(con).get("gps_valid") is True,
                      45, 5)
        check("away2: GPS fix survived the round trip (dongle never "
              "rebooted)", dt >= 0, f"{dt:.0f} s after fallback")

        # ---- phase 4: drive home again (full cycle) -----------------------
        r = ssh_run(f"sudo -n nmcli con up {HOME_CON} >/dev/null && sleep 4 "
                    f"&& nmcli -t dev status | grep -q '^{HOME_IF}:wifi:"
                    f"connected:{HOME_CON}' && echo ok", timeout=60)
        check("home2: hotspot up and stable", "ok" in r.stdout)
        dt = wait_for(con, "roam home again",
                      lambda: wifi_ssid(con) == home_ssid, budget, 10)
        check("home2: roamed home again", dt >= 0, f"{dt:.0f} s")

        check("console error lines == 0", e_lines == 0, f"E={e_lines}")
    finally:
        # ---- restore: temp hotspot gone, wtest0's normal profile back ----
        ssh_run(f"sudo -n nmcli con down {HOME_CON} >/dev/null 2>&1; "
                f"sudo -n nmcli con delete {HOME_CON} >/dev/null 2>&1; "
                f"sudo -n nmcli con up {HOME_IF_CON} >/dev/null 2>&1; true")
        con.close()

    return finish()


def finish():
    if fails:
        print("ESPNL ROAM FAIL: " + ", ".join(fails))
        return 1
    print("ESPNL ROAM PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
