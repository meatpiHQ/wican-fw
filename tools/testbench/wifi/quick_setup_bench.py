#!/usr/bin/env python3
"""Quick Setup end-to-end scenario (TASK_quick_setup.md, 2026-10-01): a
fresh WiCAN Pro goes through exactly the request sequence the Quick Setup
wizard sends, and the bench asserts the OBSERVED state afterwards (console
+ read-backs, never HTTP statuses).

  erase    esptool erase-flash + write-flash (skip with --no-erase)
  boot     PSU cold cycle, console captured from power-on, first-boot
           markers (AP SSID), restart_tracker baseline
  phone    the Pi joins `WiCAN_<id>` / @meatpi# on wtest1 and reads what
           the wizard reads: /api/info, /api/wifi/status
           (ap_default_password), /api/wifi/scan (the home hotspot listed
           with auth_mode), the wifi_manager / mqtt_manager /
           data_destinations documents
  review   the ONE save of the wizard's Review screen: PUT wifi_manager
           {mode apsta, sta_ssid/sta_password = the Pi hotspot, sta_trusted,
           ap_password = a new one}, PUT mqtt_manager {enabled, url = the
           Pi's mosquitto}, PUT data_destinations {enabled, one mqtt row
           `~/autopid`}, POST submit -> ONE planned reboot
  checks   what the wizard's Checks screen shows, from the home side: the
           STA joined the hotspot, `wican_<id>.local` resolves on the Pi,
           ap_default_password false, bits.mqtt_connected, the broker saw
           `wican/<id>/status` online, /api/destinations lists the row
  vehicle  (needs the ECU simulator on the bus) POST /api/autopid/std_scan,
           the phases, the result (protocol_detected, vin), GET
           /api/autopid/vehicle, PUT /api/autopid/config with the found
           rows, PUT autopid {enabled, std_protocol "0"} + submit -> the
           second planned reboot; afterwards polls succeed, the vehicle
           file is unchanged and the broker receives `wican/<id>/autopid`
  health   restart_tracker planned only; console E lines == 0

  python quick_setup_bench.py [--wican auto] [--psu auto] [--build <dir>]
         [--no-erase] [--no-vehicle]

Verdict: QUICK SETUP PASS / QUICK SETUP FAIL: <names>.
"""
import argparse
import base64
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
from wican_fresh_bench import (Console, esptool, psu_cycle, restart_stats,   # noqa: E402
                               ssh_run, check, note, fails, WICAN_AP_IP,
                               WICAN_AP_PSK, USER_IF, HOME_CON, HOME_IF, CURL)
import bench_ports  # noqa: E402

import wican_fresh_bench as _fresh  # noqa: E402

# esp-mqtt's own E lines while the broker is unreachable (the STA is away
# from the home network, e.g. parked on the dongle's AP after a hotspot
# stall): library output, handled by its reconnect; the bench reports the
# WiFi side instead
_fresh.E_WHITELIST = _fresh.E_WHITELIST + (
    "mqtt_client: Error transport connect",
    "mqtt_client: esp_mqtt_handle_transport_read_error",
    "mqtt_client: mqtt_process_receive",
    "mqtt_client: esp_mqtt_connect",        # the first CONNECT after the
    "mqtt_client: MQTT connect failed",     # join can time out, then retries
    "mqtt_client: Poll read error",         # the station left mid-session
    "transport_base: poll_read select error")  # (hotspot stall -> dongle AP)

USER_CON = "wican-qs"              # temp Pi profile = the user's phone
NEW_AP_PSK = "quick-setup-2026"    # the wizard's new access point password
TWIN_CON = "wican-bench-w0"        # the internal-radio twin hotspot: parked
BROKER_HOST = "10.42.1.1"          # the Pi on its hotspot = the user's broker
SIM_VIN = "1WCAN0FW0P0000001"      # the ECU simulator's VIN (0902 / 22F190)
HOME_CURL = "curl -s -m 10 --retry 2 --retry-delay 2"


def b64(obj):
    return base64.b64encode(json.dumps(obj).encode()).decode()


def ap_api(method, path, body=None, tries=3):
    """One request from the phone on the WiCAN AP (the Pi's wtest1). The
    AP drops its client for a few seconds around a scan (the radio flips
    to AP+STA and back), so an empty answer is retried like a user who
    presses the button again; a PUT is retried too (full-object replace,
    idempotent)."""
    data = (f"echo {b64(body)} | base64 -d > /tmp/qs_body.json && "
            f"{CURL} -X {method} -H 'Content-Type: application/json' "
            f"-d @/tmp/qs_body.json") if body is not None else f"{CURL} -X {method}"
    for attempt in range(tries):
        # re-activating an ALREADY active profile bounces the association
        # (the fresh bench's user_get did that on every request); only join
        # when the phone is actually off the AP
        r = ssh_run(f"nmcli -t -f NAME con show --active | grep -qx {USER_CON} || "
                    f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; "
                    f"{data} http://{WICAN_AP_IP}{path}")
        out = parse(r.stdout)
        if out is not None:
            return out
        if attempt + 1 < tries:
            note(f"phone: no answer to {method} {path} (attempt {attempt + 1}), retrying")
            time.sleep(3)
    return None


def home_api(ip, method, path, body=None):
    """One request from the home network (the Pi hosting the hotspot)."""
    data = (f"echo {b64(body)} | base64 -d > /tmp/qs_body.json && "
            f"{HOME_CURL} -X {method} -H 'Content-Type: application/json' "
            f"-d @/tmp/qs_body.json") if body is not None else f"{HOME_CURL} -X {method}"
    r = ssh_run(f"{data} http://{ip}{path}")
    return parse(r.stdout)


def parse(txt):
    txt = (txt or "").strip()
    if not txt:
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return {"_raw": txt[:200]}


def strip(doc):
    d = dict(doc or {})
    d.pop("degraded", None)
    d.pop("pending_reboot", None)
    return d


def sta_ip(con):
    st = con.cmd("wifi", r"STA connected: (yes|no)") or ""
    m = re.search(r"STA connected: yes \(([0-9.]+)\)", st)
    return m.group(1) if m else ""


def wait_for(fn, timeout, step=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def recommend_pair(charging, resting):
    """The wizard's rule (setup.js pwrRecommend): a little above resting for
    sleep, a little above sleep but well below charging for wake."""
    def r1(x):
        return round(x * 10 + 1e-6) / 10
    gap = charging - resting
    sleep_v = r1(resting + (0.2 if gap < 0.5 else 0.3))
    wake_v = r1(sleep_v + 0.1) if gap < 0.5 else min(r1(sleep_v + 0.2), r1(charging - 0.4))
    if wake_v < sleep_v + 0.1:
        wake_v = r1(sleep_v + 0.1)
    sleep_v = min(14.0, max(12.0, sleep_v))
    wake_v = min(15.0, max(12.1, wake_v))
    return sleep_v, wake_v


def battery_leg(ip, psu_port):
    """The wizard's Battery and sleep step with the bench supply as the car:
    14.3 V = engine running, 12.75 V = key-off and resting. Returns
    (charging, resting, sleep_v, wake_v) from the DEVICE's readings, or None.
    The supply goes back to its setpoint before the sleep countdown (5 min)
    could matter."""
    from owon_psu import OwonPsu   # noqa: E402 (lib on sys.path)

    def readings(stop, timeout):
        vals, t0 = [], time.time()
        while time.time() - t0 < timeout:
            b = home_api(ip, "GET", "/api/battery") or {}
            v = b.get("voltage")
            if isinstance(v, (int, float)):
                vals.append(float(v))
                if stop(vals):
                    return vals
            time.sleep(1.0)
        return None

    def spread(a):
        return max(a) - min(a)

    def mean(a):
        return sum(a) / len(a)

    with OwonPsu(psu_port) as ps:
        v0 = ps.voltage_setpoint()
        note(f"battery: supply setpoint {v0:.2f} V; playing engine running (14.3 V)")
        try:
            ps.set_voltage(14.3)
            vals = readings(lambda a: len(a) >= 6 and spread(a[-6:]) < 0.15 and mean(a[-6:]) >= 13.3, 45)
            charging = mean(vals[-6:]) if vals else None
            check("battery: 14.3 V on the supply reads as charging on the device (stable, >= 13.3 V)",
                  charging is not None, f"{charging:.2f} V" if charging else "no stable reading above 13.3 V")
            if charging is None:
                return None
            note("battery: playing key-off (12.75 V), waiting for the rest")
            ps.set_voltage(12.75)
            t_drop = [None]

            def settled(a):
                if t_drop[0] is None:
                    if len(a) >= 2 and max(a[-2:]) < charging - 0.5:
                        t_drop[0] = len(a)
                    return False
                tail = a[-10:]
                return len(a) - t_drop[0] >= 10 and ((spread(tail) < 0.04 and mean(tail) < charging - 0.4)
                                                      or len(a) - t_drop[0] >= 60)
            vals = readings(settled, 90)
            resting = mean(vals[-5:]) if vals else None
            check("battery: the drop to 12.75 V is noticed and settles as resting",
                  resting is not None and resting < charging - 0.4,
                  f"{resting:.2f} V (charging {charging:.2f} V)" if resting else "never settled")
        finally:
            ps.set_voltage(v0)
            note(f"battery: supply back at {v0:.2f} V")
    if resting is None:
        return None
    sleep_v, wake_v = recommend_pair(charging, resting)
    check("battery: the recommended pair sits between resting and charging with a 0.1 V band or more",
          resting < sleep_v < wake_v < charging and wake_v - sleep_v >= 0.1 - 1e-6,
          f"sleep {sleep_v:.1f} V, wake {wake_v:.1f} V")
    return charging, resting, sleep_v, wake_v


def wait_home_ip(con, timeout=150):
    """The STA address on the HOME network, once the device answers there.
    With a dongle on the connector the STA can land on the dongle's AP
    (192.168.80.x) after a hotspot association stall; that address is not
    reachable from the Pi, so keep waiting for the hotspot lease."""
    t0 = time.time()
    seen_dongle = False
    while time.time() - t0 < timeout:
        ip = sta_ip(con)
        if ip.startswith("192.168.80.") and not seen_dongle:
            seen_dongle = True
            note(f"RIG: the STA is on the dongle AP ({ip}) instead of the hotspot "
                 "(hotspot association stall); waiting for it to come home")
        if ip and not ip.startswith("192.168.80.") and home_api(ip, "GET", "/api/info"):
            return ip
        time.sleep(5)
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wican", default="auto")
    ap.add_argument("--psu", default="auto")
    ap.add_argument("--build", default=os.path.join(HERE, "..", "..", "..", "build"))
    ap.add_argument("--no-erase", action="store_true")
    ap.add_argument("--no-vehicle", action="store_true")
    a = ap.parse_args()
    wican = bench_ports.resolve(a.wican, "wican_console")
    psu = bench_ports.resolve(a.psu, "psu")
    note(f"ports: wican={wican} psu={psu}")

    # ---- the rig: ONE home hotspot (the twin roams the DUT), broker up ----
    r = ssh_run(f"sudo -n nmcli con modify {TWIN_CON} connection.autoconnect no; "
                f"sudo -n nmcli con down {TWIN_CON} >/dev/null 2>&1; "
                f"nmcli -g 802-11-wireless.ssid con show {HOME_CON}; "
                f"sudo -n nmcli -s -g 802-11-wireless-security.psk con show {HOME_CON}; "
                f"sudo -n nmcli con up {HOME_CON} >/dev/null 2>&1; "
                f"nmcli -t dev status | grep '^{HOME_IF}:wifi:connected'; "
                f"systemctl is-active mosquitto")
    parts = r.stdout.split("\n")
    home_ssid = parts[0].strip() if parts else ""
    home_psk = parts[1].strip() if len(parts) > 1 else ""
    check("setup: home hotspot up on the Pi, twin parked, mosquitto active",
          bool(home_ssid) and bool(home_psk) and "connected" in r.stdout
          and "\nactive" in "\n" + r.stdout, home_ssid)
    if fails:
        return finish()
    # retained messages from an earlier run would satisfy the broker checks
    # on their own: clear them so this run has to publish
    ssh_run("for t in status autopid; do mosquitto_pub -h 127.0.0.1 -t \"wican/+/$t\" -r -n 2>/dev/null; done; "
            "mosquitto_sub -h 127.0.0.1 -t 'wican/#' -W 2 -F '%t' 2>/dev/null | sort -u | "
            "while read t; do mosquitto_pub -h 127.0.0.1 -t \"$t\" -r -n; done; true", timeout=30)
    note("setup: retained wican/* topics cleared on the broker")

    if not a.no_erase:
        check("erase: wican flash", esptool(wican, "erase-flash"))
        check("flash: wican build", esptool(wican, "write-flash", "@flash_args", cwd=a.build))
        if fails:
            return finish()

    con = Console(wican, 2000000)
    check("boot: PSU cold cycle", psu_cycle(psu))
    ssh_run(f"sudo -n nmcli con delete {USER_CON} >/dev/null 2>&1; true")
    try:
        hit = con.wait_for(r"config applied: mode=(\d+) sta_networks=(\d+) ap_ssid=(WiCAN_\w+)", 60)
        m = re.search(r"ap_ssid=(WiCAN_\w+)", hit[1]) if hit else None
        ap_ssid = m.group(1) if m else ""
        check("boot: fresh device is an access point", m is not None, ap_ssid)
        con.wait_for(r"wifi_manager: started \(mode=", 60)
        time.sleep(4)
        mem = con.find(r"WICAN MEM internal_free=\d+")   # (ts, line) or None
        note("boot: " + (mem[1].strip() if mem else "no WICAN MEM line yet")
             + "  (AP+STA default costs the station interface: compare with the AP-only runs)")
        b0, u0 = restart_stats(con)
        check("boot: restart_tracker baseline", b0 >= 1 and u0 == 0, f"boots={b0} unexpected={u0}")
        if fails:
            return finish()

        # ---- the phone joins the AP (first visit = the wizard) -------------
        r = ssh_run(
            f"sudo -n nmcli con add type wifi ifname {USER_IF} con-name {USER_CON} "
            f"autoconnect no ssid '{ap_ssid}' 802-11-wireless-security.key-mgmt wpa-psk "
            f"802-11-wireless-security.psk '{WICAN_AP_PSK}' 802-11-wireless-security.psk-flags 0 "
            f">/dev/null && sudo -n nmcli con up {USER_CON} >/dev/null 2>&1 && sleep 3 && "
            f"nmcli -t dev status | grep -q '^{USER_IF}:wifi:connected:{USER_CON}' && echo ok")
        check("phone: joined the fresh WiCAN AP with the factory password", "ok" in r.stdout, ap_ssid)
        info = ap_api("GET", "/api/info") or {}
        dev_id = str(info.get("device_id", ""))
        check("phone: /api/info gives the device id", bool(dev_id), dev_id)
        ws = ap_api("GET", "/api/wifi/status") or {}
        check("phone: /api/wifi/status reports the factory AP password (the wizard's first-visit signal)",
              ws.get("ap_default_password") is True)
        # scanning from AP-only mode flips the radio to AP+STA for the
        # duration and can drop the phone's connection for a moment (one
        # answer in three came back empty on 2026-10-01): the wizard retries
        # twice by itself, so the bench allows the same
        nets, tries = [], 0
        for tries in range(1, 5):
            scan = ap_api("GET", "/api/wifi/scan") or {}
            nets = scan.get("networks") or []
            if nets:
                break
            note(f"phone: scan attempt {tries} came back empty (AP+STA flip), retrying")
            time.sleep(3)
        home = [n for n in nets if n.get("ssid") == home_ssid]
        check("phone: the scan lists the home network with auth_mode",
              bool(home) and bool(home[0].get("auth_mode")) and home[0].get("auth_mode") != "OPEN",
              f"{len(nets)} networks on attempt {tries}, home={home[0].get('auth_mode') if home else 'absent'}")
        check("phone: the scan answered within the wizard's two retries", tries <= 3, f"attempt {tries}")

        # ---- the Review screen's single save -------------------------------
        n_before_review = len(fails)
        wf = strip(ap_api("GET", "/api/settings/wifi_manager"))
        wf.update(mode="apsta", sta_ssid=home_ssid, sta_password=home_psk,
                  sta_trusted=True, ap_password=NEW_AP_PSK)
        r1 = ap_api("PUT", "/api/settings/wifi_manager", wf) or {}
        back = ap_api("GET", "/api/settings/wifi_manager") or {}
        check("review: wifi_manager staged (read back: apsta + the home SSID)",
              back.get("mode") == "apsta" and back.get("sta_ssid") == home_ssid
              and back.get("sta_trusted") is True, json.dumps(r1)[:80])
        mq = strip(ap_api("GET", "/api/settings/mqtt_manager"))
        mq.update(enabled=True, url=f"mqtt://{BROKER_HOST}:1883", username="",
                  topic_prefix="")
        ap_api("PUT", "/api/settings/mqtt_manager", mq)
        back = ap_api("GET", "/api/settings/mqtt_manager") or {}
        check("review: mqtt_manager staged (enabled, the Pi broker)",
              back.get("enabled") is True and back.get("url") == f"mqtt://{BROKER_HOST}:1883")
        dd = strip(ap_api("GET", "/api/settings/data_destinations"))
        rows = [dict(x) for x in (dd.get("destinations") or [])]
        row = next((x for x in rows if x.get("type") == "mqtt"
                    and str(x.get("url", "")).replace(" ", "") == "~/autopid"), None)
        if row:
            row.update(enabled=True, period_s=5)
        else:
            rows.append({"name": "autopid", "type": "mqtt", "enabled": True, "url": "~/autopid",
                         "period_s": 5, "auth": "none", "auth_token": "", "auth_name": "",
                         "basic_username": "", "basic_password": "", "api_key": "", "query": "",
                         "cert_set": "", "car_model": "", "retain": True, "full_first": True})
        dd.update(enabled=True, destinations=rows)
        r3 = ap_api("PUT", "/api/settings/data_destinations", dd) or {}
        back = ap_api("GET", "/api/settings/data_destinations") or {}
        has_row = any(x.get("type") == "mqtt" and x.get("url") == "~/autopid" and x.get("enabled")
                      for x in (back.get("destinations") or []))
        check("review: data_destinations staged (enabled + the ~/autopid mqtt row)",
              back.get("enabled") is True and has_row, json.dumps(r3)[:80])
        if len(fails) > n_before_review:
            return finish()   # a refused PUT: nothing to submit
        t_sub = time.time() - con.t0
        sub = ap_api("POST", "/api/settings/submit", {}) or {}
        rb = con.wait_for(r"config applied: mode=3 sta_networks=[12]|"
                          r"wifi_manager: started \(mode=3, [12] STA", 90, since=max(0.0, t_sub - 6))
        check("review: ONE planned reboot applies everything", rb is not None, json.dumps(sub)[:60])
        t_boot = rb[0] if rb else t_sub
        # the AP password changed: the phone profile follows (recovery path)
        ssh_run(f"sudo -n nmcli con modify {USER_CON} 802-11-wireless-security.psk '{NEW_AP_PSK}'; "
                f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1")

        # ---- the Checks screen, from the home network ------------------------
        con.wait_for(r"wifi_manager: .*(got ip|STA got IP|connected to)", 120, since=t_boot)
        ip = wait_home_ip(con, 150)
        check("checks: STA joined the home network (a hotspot lease, not the dongle AP)", bool(ip),
              ip or (sta_ip(con) + " (dongle AP: rig hotspot stall)" if sta_ip(con) else "no STA address"))
        if not ip:
            return finish()
        r = ssh_run(f"avahi-resolve -4 -n wican_{dev_id}.local 2>/dev/null | awk '{{print $2}}'", timeout=40)
        check("checks: wican_<id>.local resolves on the home network (the wizard's link)",
              r.stdout.strip() == ip, r.stdout.strip() or "no answer")
        ws = home_api(ip, "GET", "/api/wifi/status") or {}
        check("checks: AP secured (ap_default_password false), station connected",
              ws.get("ap_default_password") is False and ws.get("sta_connected") is True)
        st = wait_for(lambda: (lambda s: s if (s.get("bits") or {}).get("mqtt_connected") else None)(
            home_api(ip, "GET", "/api/status") or {}), 45, 3)
        check("checks: bits.mqtt_connected within 45 s", st is not None)
        r = ssh_run(f"mosquitto_sub -h 127.0.0.1 -t 'wican/{dev_id}/status' -C 1 -W 15 2>&1", timeout=40)
        check("checks: the broker holds the retained online status", '"online"' in r.stdout, r.stdout.strip()[:60])
        ds = home_api(ip, "GET", "/api/destinations") or {}
        check("checks: /api/destinations lists the enabled mqtt row",
              any(d.get("type") == "mqtt" and d.get("enabled") for d in (ds.get("destinations") or [])))
        ws_ap = ap_api("GET", "/api/wifi/status") or {}
        check("checks: the UI still answers on the AP with the new password (recovery path)",
              ws_ap.get("sta_connected") is True)
        # the real user is on the home network from here on; a phone parked
        # on the AP would also block the STA's roam back to the primary
        ssh_run(f"sudo -n nmcli con down {USER_CON} >/dev/null 2>&1; true")
        note("phone: left the WiCAN AP (the user is on the home network now)")

        boots_expected = b0 + 1
        # ---- the vehicle screen ----------------------------------------------
        if not a.no_vehicle:
            saves0 = ((home_api(ip, "GET", "/api/obd_chip") or {}).get("eeprom_guard") or {}).get("protocol_saves", 0)
            s0 = home_api(ip, "POST", "/api/autopid/vehicles/detect", {}) or {}
            phases, last = [], None
            t0 = time.time()
            while time.time() - t0 < 120:
                s = home_api(ip, "GET", "/api/autopid/std_scan") or {}
                if s.get("phase") and s.get("phase") not in phases:
                    phases.append(s["phase"])
                last = s
                if s.get("status") in ("done", "failed", "idle"):
                    break
                time.sleep(1.5)
            check("vehicle: the scan ran to done", (last or {}).get("status") == "done",
                  f"status={(last or {}).get('status')} error={(last or {}).get('error', '')} start={json.dumps(s0)[:40]}")
            # the simulator answers fast: a 1.5 s poll sees a subset of the
            # phases (run 3 saw ['vin', 'idle']); only unknown names fail
            check("vehicle: the scan reported known phases only",
                  bool(phases) and set(phases) <= {"protocol", "vin", "pids", "idle"}, phases)
            res = home_api(ip, "GET", "/api/autopid/std_scan/result") or {}
            sup = res.get("supported") or []
            check("vehicle: result carries the detected protocol 6 (the simulator is 500k / 11-bit)",
                  str(res.get("protocol_detected", "")) == "6", str(res.get("protocol_detected")))
            vin = str(res.get("vin", ""))
            if vin:
                check("vehicle: result carries the simulator's VIN", vin == SIM_VIN, vin)
            else:
                note("WARN vehicle: no VIN in the result (the simulator answers 0902 on some days only)")
            check("vehicle: standard PIDs found", len(sup) > 0, f"{len(sup)} rows")
            # ---- the store (second pass): the detection created the car's entry
            vs = home_api(ip, "GET", "/api/autopid/vehicles") or {}
            ents = vs.get("vehicles") or []
            key = res.get("key") or vin
            ent = next((e for e in ents if e.get("key") == key), None)
            check("vehicle: the store holds the detected car as current, profile pending",
                  ent is not None and ent.get("current") is True and ent.get("pending_profile") is True
                  and str(ent.get("protocol", "")) == "6" and (not vin or ent.get("vin") == vin)
                  and vs.get("current") == key, json.dumps(ent or vs)[:160])
            check("vehicle: the result names the entry (key, known false on a fresh store)",
                  res.get("key") == key and res.get("known") is False, {"key": res.get("key"), "known": res.get("known")})
            back = home_api(ip, "GET", "/api/autopid/config") or {}
            check("vehicle: the job wrote the standard rows into the active config by itself",
                  len([p for p in (back.get("pids") or []) if p.get("type") == "std"]) >= len(sup),
                  f"{len(back.get('pids') or [])} pids")
            saves1 = ((home_api(ip, "GET", "/api/obd_chip") or {}).get("eeprom_guard") or {}).get("protocol_saves", 0)
            check("vehicle: the chip learned the base protocol ONCE (one real ATSP this boot)",
                  saves1 == saves0 + 1 and str(ent.get("chip_protocol", "")) == "6" if ent else False,
                  f"protocol_saves {saves0}->{saves1}, chip_protocol={ent.get('chip_protocol') if ent else None}")
            # the wizard's Finish: name + "standard PIDs only" (empty profile clears pending)
            put = home_api(ip, "PUT", "/api/autopid/vehicles/" + key, {"name": "Bench car", "profile": ""}) or {}
            check("vehicle: PUT name + empty profile clears pending_profile",
                  put.get("name") == "Bench car" and put.get("pending_profile") is False, json.dumps(put)[:120])
            # the wizard's "Reading the car" step: the default group at 5 s with the
            # rows inheriting it (live config), the rules as settings
            cfg = home_api(ip, "GET", "/api/autopid/config") or {}
            groups = cfg.get("groups") or [{"name": "default", "enabled_default": True, "period_ms": 1000}]
            old_period = groups[0].get("period_ms") or 1000
            groups[0]["period_ms"] = 5000
            stored = [p for p in (cfg.get("pids") or []) if p.get("type") == "std"]
            check("vehicle: the job stored the rows OFF (the user ticks the ones to read, 2026-10-09)",
                  bool(stored) and all(p.get("enabled") is False for p in stored),
                  f"{sum(1 for p in stored if p.get('enabled') is False)} of {len(stored)} off")
            for pid in cfg.get("pids") or []:
                if pid.get("period_ms") in (old_period, 1000):
                    pid["period_ms"] = 0
                if pid.get("type") == "std":
                    pid.pop("enabled", None)      # the wizard's tick: every row chosen here
            cfg["groups"] = groups
            cfg.setdefault("filters", [])
            home_api(ip, "PUT", "/api/autopid/config", cfg)
            back_cfg = home_api(ip, "GET", "/api/autopid/config") or {}
            check("reading: the default group runs at 5 s and the standard rows inherit it",
                  (back_cfg.get("groups") or [{}])[0].get("period_ms") == 5000
                  and all(not p.get("period_ms") for p in (back_cfg.get("pids") or []) if p.get("type") == "std"),
                  json.dumps((back_cfg.get("groups") or [{}])[0])[:80])
            # the wizard's "Battery and sleep" step (sleep_manager v3): the supply
            # plays the car; the measured pair is staged with Power Saving on
            pair = battery_leg(ip, psu) if psu else None
            if pair:
                sm = strip(home_api(ip, "GET", "/api/settings/sleep_manager"))
                sm.update(enabled=True, sleep_mv=int(round(pair[2] * 1000)), wake_mv=int(round(pair[3] * 1000)))
                home_api(ip, "PUT", "/api/settings/sleep_manager", sm)
                back_sm = home_api(ip, "GET", "/api/settings/sleep_manager") or {}
                check("battery: sleep_manager staged (enabled, the measured sleep_mv and wake_mv)",
                      back_sm.get("enabled") is True and back_sm.get("sleep_mv") == sm["sleep_mv"]
                      and back_sm.get("wake_mv") == sm["wake_mv"], json.dumps({k: back_sm.get(k) for k in ("enabled", "sleep_mv", "wake_mv")}))
            av = strip(home_api(ip, "GET", "/api/settings/autopid"))
            av.update(enabled=True, std_enabled=True, custom_enabled=True, std_protocol="0",
                      pause_below_mv=0, pause_follow_sleep=True, pause_mode="requests_only",
                      min_event_interval_ms=1000, dtc_enabled=False, dtc_scan_period_min=0)
            home_api(ip, "PUT", "/api/settings/autopid", av)
            back = home_api(ip, "GET", "/api/settings/autopid") or {}
            check("vehicle: autopid staged (enabled, protocol Automatic, the reading rules)",
                  back.get("enabled") is True and str(back.get("std_protocol")) == "0"
                  and back.get("pause_follow_sleep") is True and back.get("pause_below_mv") == 0
                  and back.get("pause_mode") == "requests_only")
            t_sub2 = time.time() - con.t0
            home_api(ip, "POST", "/api/settings/submit", {})
            rb2 = con.wait_for(r"wifi_manager: started \(mode=3", 90, since=max(0.0, t_sub2 - 6))
            check("vehicle: the second planned reboot", rb2 is not None)
            boots_expected += 1
            ip2 = wait_home_ip(con, 150)
            check("vehicle: the device is back on the home network after the restart", bool(ip2), ip2 or sta_ip(con) or "no STA address")
            ip2 = ip2 or ip
            polls = wait_for(lambda: (lambda d: d if ((d.get("stats") or {}).get("polls_ok") or 0) > 0 else None)(
                home_api(ip2, "GET", "/api/autopid") or {}), 90, 3)
            check("vehicle: polling runs after the restart (polls_ok > 0)", polls is not None,
                  json.dumps((polls or {}).get("stats", {}))[:100])
            grp = next((g for g in ((polls or {}).get("groups") or []) if g.get("name") == "default"), {})
            check("reading: the default group polls every 5 s after the restart", grp.get("period_ms") == 5000, json.dumps(grp)[:80])
            if pair:
                sl = home_api(ip2, "GET", "/api/sleep") or {}
                check("battery: the measured pair is live after the restart (GET /api/sleep)",
                      sl.get("enabled") is True and abs((sl.get("sleep_v") or 0) - pair[2]) < 0.006
                      and abs((sl.get("wake_v") or 0) - pair[3]) < 0.006, json.dumps(sl)[:120])
                armed = con.wait_for(r"sleep_manager: armed \(sleep %.2f V, wake %.2f V" % (pair[2], pair[3]), 30,
                                     since=max(0.0, t_sub2 - 6))
                check("battery: the console arms the ladder with the measured pair", armed is not None,
                      (armed or "no armed line with that pair")[:100])
            vs2 = home_api(ip2, "GET", "/api/autopid/vehicles") or {}
            ent2 = next((e for e in (vs2.get("vehicles") or []) if e.get("key") == key), None)
            check("vehicle: the store survives the restart (same car current, named, profile settled)",
                  ent2 is not None and ent2.get("current") is True and ent2.get("name") == "Bench car"
                  and ent2.get("pending_profile") is False and str(ent2.get("protocol", "")) == "6"
                  and (not vin or ent2.get("vin") == vin), json.dumps(ent2 or vs2)[:160])
            saves2 = ((home_api(ip2, "GET", "/api/obd_chip") or {}).get("eeprom_guard") or {}).get("protocol_saves", -1)
            check("vehicle: no second ATSP after the restart (the chip already holds the protocol)",
                  saves2 == 0, f"protocol_saves this boot = {saves2}")
            r = ssh_run(f"mosquitto_sub -h 127.0.0.1 -t 'wican/{dev_id}/autopid' -C 1 -W 25 2>&1", timeout=50)
            check("vehicle: the broker receives the autopid payload", r.stdout.strip().startswith("{"),
                  r.stdout.strip()[:80])

        # ---- health ------------------------------------------------------------
        nl = con.cmd("espnetlink", r"ESPNETLINK: enabled=") or ""
        if "paired=1" in nl:
            boots_expected += 1
            note("espnetlink: a dongle paired in the background (one more planned reboot)")
        b, u = restart_stats(con)
        check("health: planned reboots only", u == 0 and b == boots_expected,
              f"boots {b0}->{b} (expected {boots_expected}) unexpected {u}")
        e = con.e_lines()
        check("health: console E lines == 0", len(e) == 0, f"E={len(e)}")
        for l in e[:8]:
            note("E-line: " + l)
    finally:
        ssh_run(f"sudo -n nmcli con down {USER_CON} >/dev/null 2>&1; "
                f"sudo -n nmcli con delete {USER_CON} >/dev/null 2>&1; true")
        # keep the console for the post-mortem (the verdict lines alone do
        # not say where the STA went or which boot printed what)
        try:
            logdir = os.path.join(HERE, "..", "..", "..", "test-reports", "logs")
            os.makedirs(logdir, exist_ok=True)
            logp = os.path.join(logdir, time.strftime("quick_setup_%Y%m%d_%H%M.console.log"))
            with open(logp, "w", encoding="utf-8") as f:
                for t, txt in con.snapshot():
                    f.write(f"{t:9.3f} {txt}\n")
            note(f"console saved: {os.path.normpath(logp)}")
        except OSError as exc:
            note(f"console not saved: {exc}")
        con.close()
    return finish()


def finish():
    if fails:
        print("QUICK SETUP FAIL: " + ", ".join(fails))
        return 1
    print("QUICK SETUP PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
