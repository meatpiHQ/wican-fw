#!/usr/bin/env python3
"""ESPNetLink transport-mode switching bench: the full transition matrix.

Legs (each = stage the `espnetlink.mode` setting over HTTP, submit, WiCAN
reboots, then assert the OBSERVED state over the serial console + the
dongle's health endpoint — HTTP statuses are advisory only: fresh-AP
associations lose response status lines routinely, and in the USB modes
the WiCAN's STA idles into power-save, making HTTP to it lossy — every
HTTP step is therefore verified by reading state back):

  0  preflight        paired, mode=wifi_modem steady (uplink=espnetlink)
  1  wifi -> usb_ncm  the AP-path usb_data restore un-cuts the boot-cut
                      dongle (boot_cut clears, NO dongle reboot — class
                      ncm + ncm_share already right), driver cdc_ncm,
                      uplink espnetlink_usb @192.168.7.1, GPS polls flow
  2  ncm -> usb_rndis exactly the class flip: ONE dongle reboot, re-enum
                      as RNDIS, driver rndis, uplink back @192.168.7.1
  3  rndis -> wifi    key re-read + cut (usb_data=0, boot_cut re-armed),
                      uplink espnetlink (AP), dongle NOT rebooted — a
                      live GPS fix survives
  3b reset class      dongle class back to ncm (its own reboot) so leg 4
                      exercises the COMBINED restore+class single-submit
  4  wifi -> usb_rndis direct: AP restore + class flip in one ensure
                      pass — still only ONE dongle reboot, driver rndis
  5  restore          back to wifi_modem, dongle class ncm; entry state

Cross-cutting: every WiCAN reboot must be planned (restart_tracker
`unexpected resets` may not grow; boot counts are asserted PER LEG with
a baseline taken right before each switch), console E lines == 0 (the
esp_wifi `Invalid MMIE` PMF-noise line is whitelisted; offending lines
are recorded and printed), and the WiCAN's STA address is read live from
the console (the dongle's DHCP hands out a new lease after every WiCAN
reboot — never assume one).

A leg whose ENTRY mode is wrong (a previous leg failed to switch) is
skipped as a cascade instead of producing misleading assertions.

Prereqs: DUT console on COM1175 (capture stopped), rpi001 with the
`espnl-client` profile (dongle AP) on wtest1, dongle paired.

  python espnetlink_mode_bench.py [COM1175]

Verdict: ESPNL MODE PASS / ESPNL MODE FAIL: <names>.
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
DONGLE_CON = "espnl-client"
DONGLE_IF = "wtest1"
DONGLE_IP = "192.168.80.1"
CURL = f"curl -s -m 10 --retry 2 --retry-delay 2 --interface {DONGLE_IF}"

# esp_wifi PMF driver noise + the IDF HTTP client's connect-failure
# triplet: mode transitions reboot the dongle mid-poll by design, so ONE
# failed connect (esp-tls select timeout -> transport_base -> HTTP_CLIENT)
# is inherent; espnetlink_link handles it at W level and retries.
E_WHITELIST = ("Invalid MMIE", "select() timeout",
               "Failed to open a new connection",
               "Connection failed, sock < 0")

fails = []
e_recorded = []


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
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        note(f"ssh timeout ({timeout} s): {cmd[:60]}...")
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")


def open_console():
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = COM, BAUD, 0.2
    s.dtr = False   # pre-set BEFORE open: the CH344 pulses EN otherwise
    s.rts = False
    s.open()
    return s


def console_cmd(con, line, wait=2.5):
    con.reset_input_buffer()
    con.write(line.encode() + b"\r\n")
    end = time.time() + wait
    buf = b""
    while time.time() < end:
        b = con.read(4096)
        if b:
            buf += b
    txt = re.sub(r"\x1b\[[0-9;]*m", "", buf.decode("utf-8", "replace"))
    for l in txt.splitlines():
        if l.startswith("E (") and not any(w in l for w in E_WHITELIST):
            e_recorded.append(l.strip())
    return txt


def espnl_status(con):
    txt = console_cmd(con, "espnetlink")
    st = {}
    m = re.search(r"ESPNETLINK: enabled=(\d) mode=(\w+) auto_pair=\d "
                  r"paired=(\d) ssid='([^']*)'.* uplink=(\S+) host=(\S+)",
                  txt)
    if m:
        st.update(enabled=m.group(1) == "1", mode=m.group(2),
                  paired=m.group(3) == "1", ssid=m.group(4),
                  uplink=m.group(5), host=m.group(6))
    m = re.search(r"usb: attached=(\d) pair_state=(\w+) cuts=(\d+)", txt)
    if m:
        st.update(attached=m.group(1) == "1", pair_state=m.group(2),
                  cuts=int(m.group(3)))
    m = re.search(r"gps: valid=(\d).*?fix=(\d) usb_data=(\d) \| "
                  r"polls=(\d+) fail=(\d+)", txt)
    if m:
        st.update(gps_valid=m.group(1) == "1",
                  dongle_fix=m.group(2) == "1",
                  usb_data=m.group(3) == "1",
                  polls=int(m.group(4)), poll_fails=int(m.group(5)))
    return st


def sta_ip(con):
    m = re.search(r"STA connected: yes \(([0-9.]+)\)",
                  console_cmd(con, "wifi"))
    return m.group(1) if m else ""


def usb_driver(con):
    txt = console_cmd(con, "usb")
    m = re.search(r"ethernet: (up [0-9.]+|down) \(driver (\w+)\)", txt)
    return (m.group(1).split()[0], m.group(2)) if m else ("?", "?")


def restart_stats(con):
    txt = console_cmd(con, "restart_tracker")
    m = re.search(r"Boots: (\d+), unexpected resets: (\d+)", txt)
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def pi_json(url, tries=5):
    """GET a JSON document from the Pi over the dongle AP, re-joining the
    profile each try (the association drops on every dongle reboot)."""
    for i in range(tries):
        r = ssh_run(f"sudo -n nmcli con up {DONGLE_CON} >/dev/null 2>&1; "
                    f"sleep 2; {CURL} {url}")
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            time.sleep(3 + i)
    return None


def dongle_health(tries=5):
    d = pi_json(f"http://{DONGLE_IP}/api/wifi_modem", tries)
    return d if isinstance(d, dict) and "uptime_s" in d else None


def dongle_get_class(tries=5):
    d = pi_json(f"http://{DONGLE_IP}/api/settings/usb_dev_ethernet", tries)
    return d.get("class") if isinstance(d, dict) else None


def dongle_get_share(tries=5):
    """`lte_upstream_pppos.ncm_share` as persisted on the dongle: a freshly
    erased dongle ships it False, so the FIRST USB-mode entry must submit
    it (one dongle reboot) - leg 1 expects a reboot iff this was False."""
    d = pi_json(f"http://{DONGLE_IP}/api/settings/lte_upstream_pppos", tries)
    return d.get("ncm_share") if isinstance(d, dict) else None


def dongle_set_class(cls, budget_s=90):
    """PUT + submit on the DONGLE and WAIT for it: poll until the class
    reads back as `cls` on a fresh boot. Synchronous by observation."""
    body = json.dumps({"class": cls})
    if dongle_get_class() == cls:
        return True
    h0 = dongle_health()
    up0 = h0["uptime_s"] if h0 else 1 << 30
    ssh_run(f"sudo -n nmcli con up {DONGLE_CON} >/dev/null 2>&1; sleep 2; "
            f"{CURL} -X PUT -H 'Content-Type: application/json' "
            f"-d '{body}' http://{DONGLE_IP}/api/settings/usb_dev_ethernet "
            f">/dev/null; sleep 1; "
            f"{CURL} -X POST -d '{{}}' -H 'Content-Type: application/json' "
            f"http://{DONGLE_IP}/api/settings/submit >/dev/null",
            timeout=120)
    t0 = time.time()
    while time.time() - t0 < budget_s:
        time.sleep(6)
        h = dongle_health(tries=2)
        if h is None:
            continue                    # mid-reboot
        if h["uptime_s"] < up0 + (time.time() - t0) - 15:
            return dongle_get_class() == cls
    note(f"dongle_set_class({cls}): no reboot observed in {budget_s} s")
    return dongle_get_class() == cls


def set_mode(con, mode, budget_s=180):
    """Stage espnetlink.mode over HTTP (WiCAN reached at its LIVE STA
    address on the dongle's AP) and wait for the console to show it
    applied. Every HTTP step is verified by reading state back: the PUT
    by re-GETting the persisted document, the submit by the console
    showing the new mode after the reboot. A couple of pings first pull
    the WiCAN's STA out of power-save (idle in the USB modes)."""
    t0 = time.time()
    attempt = 0
    while time.time() - t0 < budget_s:
        if espnl_status(con).get("mode") == mode:
            return True
        ip = sta_ip(con)
        if not ip:
            time.sleep(5)
            continue
        attempt += 1
        note(f"set_mode {mode}: HTTP attempt {attempt} via {ip}")
        script = (
            f"sudo -n nmcli con up {DONGLE_CON} >/dev/null 2>&1; sleep 3; "
            f"ping -c 2 -W 1 -I {DONGLE_IF} {ip} >/dev/null 2>&1; "
            f"staged=''; "
            f"for i in 1 2 3; do "
            f"  doc=$({CURL} http://{ip}/api/settings/espnetlink); "
            f"  body=$(echo \"$doc\" | python3 -c \"import json,sys;"
            f"d=json.load(sys.stdin);d['mode']='{mode}';"
            f"print(json.dumps(d))\" 2>/dev/null) || continue; "
            f"  [ -n \"$body\" ] || continue; "
            f"  {CURL} -X PUT -H 'Content-Type: application/json' "
            f"-d \"$body\" http://{ip}/api/settings/espnetlink >/dev/null; "
            f"  back=$({CURL} http://{ip}/api/settings/espnetlink | "
            f"python3 -c \"import json,sys;"
            f"print(json.load(sys.stdin).get('mode',''))\" 2>/dev/null); "
            f"  if [ \"$back\" = '{mode}' ]; then staged=yes; break; fi; "
            f"  sleep 2; "
            f"done; "
            f"[ -n \"$staged\" ] && {CURL} -X POST -d '{{}}' "
            f"-H 'Content-Type: application/json' "
            f"http://{ip}/api/settings/submit >/dev/null; "
            f"echo staged=$staged")
        r = ssh_run(script, timeout=90)
        if "staged=yes" not in r.stdout:
            note("  staging not confirmed; will retry")
            time.sleep(5)
            continue
        # the submit reboots the WiCAN; give it time to come back
        end = time.time() + 50
        while time.time() < end:
            if espnl_status(con).get("mode") == mode:
                return True
            time.sleep(4)
    return espnl_status(con).get("mode") == mode


def wait_for(con, what, pred, budget_s, poll_s=5):
    t0 = time.time()
    while time.time() - t0 < budget_s:
        if pred():
            return time.time() - t0
        time.sleep(poll_s)
    print(f"  timeout waiting for {what} ({budget_s:.0f} s)", flush=True)
    return -1.0


def usb_steady(con, driver):
    def pred():
        st = espnl_status(con)
        return (st.get("pair_state") == "ncm_up" and
                st.get("uplink") == "espnetlink_usb" and
                st.get("host") == "192.168.7.1" and
                usb_driver(con)[1] == driver)
    return pred


def wifi_steady(con):
    def pred():
        st = espnl_status(con)
        return (st.get("uplink") == "espnetlink" and
                st.get("attached") is False and
                st.get("usb_data") is False)
    return pred


def leg_entry_ok(con, leg, want_mode):
    cur = espnl_status(con).get("mode")
    if cur == want_mode:
        return True
    note(f"{leg}: SKIPPED as a cascade — entry mode is {cur!r}, "
         f"needed {want_mode!r}")
    fails.append(f"{leg}: skipped (cascade)")
    return False


def switch(con, leg, mode):
    """Per-leg switch with its own WiCAN-reboot accounting."""
    b0, u0 = restart_stats(con)
    ok = set_mode(con, mode)
    check(f"{leg}: mode staged + applied ({mode})", ok)
    return ok, b0, u0


def reboot_check(con, leg, b0, u0):
    b, u = restart_stats(con)
    check(f"{leg}: exactly one planned WiCAN reboot",
          b == b0 + 1 and u == u0,
          f"boots {b0}->{b} unexpected {u0}->{u}")


def main():
    con = open_console()

    try:
        # ---- 0: preflight -------------------------------------------------
        st = espnl_status(con)
        check("preflight: paired", st.get("paired") is True, str(st))
        if fails:
            return finish()
        if st.get("mode") != "wifi_modem":
            note("entry mode is not wifi_modem — normalizing first")
            check("preflight: normalize to wifi_modem",
                  set_mode(con, "wifi_modem"))
        dt = wait_for(con, "wifi_modem steady", wifi_steady(con), 120)
        check("preflight: wifi_modem steady (uplink=AP, dongle cut)",
              dt >= 0)
        h = dongle_health()
        check("preflight: dongle health over its AP", h is not None)
        if fails:
            return finish()
        cls0 = dongle_get_class()
        note(f"dongle: uptime {h['uptime_s']} s, boot_cut "
             f"{h.get('boot_cut')}, class {cls0}")
        if cls0 != "ncm":
            check("preflight: dongle class reset to ncm",
                  dongle_set_class("ncm"))

        # ---- 1: wifi -> usb_ncm ------------------------------------------
        if leg_entry_ok(con, "leg1", "wifi_modem"):
            up_before = (dongle_health() or {}).get("uptime_s", 0)
            share_before = dongle_get_share()
            note(f"leg1: dongle ncm_share before the switch: {share_before}"
                 " (False = fresh dongle, the ensure pass submits it once)")
            t_leg = time.time()
            ok, b0, u0 = switch(con, "leg1", "usb_ncm")
            if ok:
                dt = wait_for(con, "usb_ncm steady",
                              usb_steady(con, "cdc_ncm"), 150)
                check("leg1: NCM uplink up (ncm_up, driver cdc_ncm, "
                      "host 7.1)", dt >= 0, f"{dt:.0f} s")
                dt = wait_for(con, "usb_data mirror refresh",
                              lambda: espnl_status(con).get("usb_data")
                              is True, 30)
                check("leg1: dongle data lines restored over the AP",
                      dt >= 0, "health mirror refreshes on the 10 s poll")
                h = dongle_health()
                check("leg1: boot-cut hint cleared by the restore",
                      h is not None and h.get("boot_cut") is False,
                      str(h.get("boot_cut") if h else None))
                survived = (h is not None and up_before > 0 and
                            h["uptime_s"] >= up_before +
                            (time.time() - t_leg) - 30)
                if share_before is False:
                    # bench 2026-09-08 on a freshly erased dongle: the
                    # ensure pass had to set ncm_share=true, ONE reboot
                    check("leg1: fresh dongle: ncm_share submitted once "
                          "(one dongle reboot)", h is not None and
                          not survived and dongle_get_share() is True,
                          f"uptime {up_before} -> "
                          f"{h['uptime_s'] if h else '?'}")
                else:
                    check("leg1: no dongle reboot (ensure pass idempotent)",
                          survived,
                          f"uptime {up_before} -> "
                          f"{h['uptime_s'] if h else '?'}")
                p1 = espnl_status(con)
                time.sleep(8)
                p2 = espnl_status(con)
                check("leg1: GPS polls flowing over the wire",
                      p2.get("polls", 0) > p1.get("polls", 0))
                reboot_check(con, "leg1", b0, u0)

        # ---- 2: usb_ncm -> usb_rndis --------------------------------------
        if leg_entry_ok(con, "leg2", "usb_ncm"):
            up_before = (dongle_health() or {}).get("uptime_s", 0)
            t_leg = time.time()
            ok, b0, u0 = switch(con, "leg2", "usb_rndis")
            if ok:
                dt = wait_for(con, "rndis steady",
                              usb_steady(con, "rndis"), 180)
                check("leg2: RNDIS uplink up (class flip + re-enum, "
                      "driver rndis)", dt >= 0, f"{dt:.0f} s")
                h = dongle_health()
                check("leg2: dongle rebooted once for the class flip",
                      h is not None and up_before > 0 and
                      h["uptime_s"] < up_before +
                      (time.time() - t_leg) - 20,
                      f"uptime {up_before} -> "
                      f"{h['uptime_s'] if h else '?'}")
                p1 = espnl_status(con)
                time.sleep(8)
                p2 = espnl_status(con)
                check("leg2: GPS polls flowing over RNDIS",
                      p2.get("polls", 0) > p1.get("polls", 0))
                reboot_check(con, "leg2", b0, u0)

        # ---- 3: usb_rndis -> wifi_modem -----------------------------------
        if leg_entry_ok(con, "leg3", "usb_rndis"):
            fix_before = espnl_status(con).get("dongle_fix")
            up_before = (dongle_health() or {}).get("uptime_s", 0)
            t_leg = time.time()
            ok, b0, u0 = switch(con, "leg3", "wifi_modem")
            if ok:
                dt = wait_for(con, "wifi steady", wifi_steady(con), 150)
                check("leg3: cut executed, back on the AP uplink", dt >= 0,
                      f"{dt:.0f} s")
                h = dongle_health()
                check("leg3: boot-cut hint re-armed by the cut",
                      h is not None and h.get("boot_cut") is True)
                check("leg3: dongle NOT rebooted by the round trip",
                      h is not None and up_before > 0 and
                      h["uptime_s"] >= up_before +
                      (time.time() - t_leg) - 30,
                      f"uptime {up_before} -> "
                      f"{h['uptime_s'] if h else '?'}")
                if fix_before:
                    check("leg3: GPS fix survived the cut",
                          espnl_status(con).get("dongle_fix") is True)
                else:
                    note("leg3: no fix at leg start — fix-survival "
                         "not assessed")
                reboot_check(con, "leg3", b0, u0)

        # ---- 3b: dongle class back to ncm (so leg 4 flips it again) ------
        check("leg3b: dongle class reset to ncm (dongle reboots)",
              dongle_set_class("ncm"))
        wait_for(con, "AP uplink back after dongle reboot",
                 lambda: espnl_status(con).get("uplink") == "espnetlink",
                 90)

        # ---- 4: wifi_modem -> usb_rndis DIRECT ----------------------------
        if leg_entry_ok(con, "leg4", "wifi_modem"):
            up_before = (dongle_health() or {}).get("uptime_s", 0)
            t_leg = time.time()
            ok, b0, u0 = switch(con, "leg4", "usb_rndis")
            if ok:
                dt = wait_for(con, "rndis steady (direct)",
                              usb_steady(con, "rndis"), 210)
                check("leg4: restore + class flip in ONE pass "
                      "(driver rndis)", dt >= 0, f"{dt:.0f} s")
                h = dongle_health()
                check("leg4: single dongle reboot for the combined change",
                      h is not None and up_before > 0 and
                      h["uptime_s"] < up_before +
                      (time.time() - t_leg) - 20,
                      f"uptime {up_before} -> "
                      f"{h['uptime_s'] if h else '?'}")
                reboot_check(con, "leg4", b0, u0)

        # ---- 5: restore entry state --------------------------------------
        check("leg5: back to wifi_modem",
              set_mode(con, "wifi_modem"))
        dt = wait_for(con, "wifi steady (final)", wifi_steady(con), 150)
        check("leg5: final wifi_modem steady", dt >= 0)
        check("leg5: dongle class restored to ncm", dongle_set_class("ncm"))
        b, u = restart_stats(con)
        check("no unexpected WiCAN resets across the bench", u == 0,
              f"unexpected={u}")
        check("console error lines == 0 (Invalid MMIE whitelisted)",
              len(e_recorded) == 0, f"E={len(e_recorded)}")
        for l in e_recorded[:12]:
            note("E-line: " + l)
    finally:
        ssh_run(f"sudo -n nmcli con down {DONGLE_CON} >/dev/null 2>&1; true")
        con.close()

    return finish()


def finish():
    if fails:
        print("ESPNL MODE FAIL: " + ", ".join(fails))
        return 1
    print("ESPNL MODE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
