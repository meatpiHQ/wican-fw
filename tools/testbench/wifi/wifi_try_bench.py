#!/usr/bin/env python3
"""The connection TRIAL on hardware (2026-10-06, Quick Setup: "test the
station connect before storing and rebooting", Ali): POST /api/wifi/try
joins a network with credentials that are never saved and GET /api/wifi/try
reports connected or the exact failure. Driven the way the wizard drives it,
from a phone on the DUT's own access point (the Pi's wtest1), with the P4
bench access point as the home network. Prints WIFI TRY PASS.

Legs (the DUT's configured station is on the bench AP throughout; each
trial drops it and the reconnect task re-joins afterwards):

  right    the bench AP's PSK: 202, state running, then done / connected
           with an address on the bench AP's subnet, rssi, channel, took_ms
           under 20 s; the station back on the bench AP within 60 s, the
           page on the AP never lost for more than a few seconds
  wrong    a wrong PSK: done / password, reason 204 (or 15, 2, 202); the
           configured network's attempt memory untouched (no deprioritise
           line); the station back
  nowhere  a made-up name: done / not_found, reason 201, quick
  busy     a second POST while one runs: 409 "a test is already running"
  bad      a 5-character password: 400; no ssid: 400
  console  `wifi --try <ssid> --password <pw>` prints the connected line
  health   0 E lines beyond the whitelist, the trial's I lines present,
           no unplanned restart, the DUT's configured settings unchanged
           (sta_ssid, mode) and the AP still up

usage: python tools/testbench/wifi/wifi_try_bench.py [--wican auto] [--dut 10.42.1.194]
needs: ssh rpi001 (wtest1 free), the DUT's console port, the bench AP up.
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
from wican_fresh_bench import (Console, ssh_run, check, note, fails,  # noqa: E402
                               restart_stats, WICAN_AP_IP, USER_IF,
                               HOME_CON, CURL)
import bench_ports  # noqa: E402

import wican_fresh_bench as _fresh  # noqa: E402

_fresh.E_WHITELIST = _fresh.E_WHITELIST + (
    "mqtt_client: Error transport connect",
    "mqtt_client: esp_mqtt_handle_transport_read_error",
    "mqtt_client: mqtt_process_receive",
    "mqtt_client: esp_mqtt_connect",
    "mqtt_client: MQTT connect failed",
    "mqtt_client: Poll read error",
    "transport_base: poll_read select error",
    # the bench phone (the Pi's wtest1) re-associates after every channel
    # hop; the AP's PMF SA query kicks it (reason 209 in the console) and
    # the IDF driver remarks on the client's replayed frame. A client-side
    # quirk the driver logs at E; nothing of ours
    "wifi:CCMP replay detected")

USER_CON = "wican-try"   # temp Pi profile = the user's phone on the DUT's AP


def b64(obj):
    return base64.b64encode(json.dumps(obj).encode()).decode()


def parse(txt):
    txt = (txt or "").strip()
    if not txt:
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return {"_raw": txt[:200]}


def ap_req(method, path, body=None, tries=3):
    """One request from the phone on the DUT's AP; the AP pauses around a
    trial (the radio moves to the home network's channel), so an empty
    answer is retried like a user whose page keeps asking. Returns
    (status, json)."""
    data = (f"echo {b64(body)} | base64 -d > /tmp/try_body.json && "
            f"{CURL} -o /tmp/try_out.json -w '%{{http_code}}' -X {method} "
            f"-H 'Content-Type: application/json' -d @/tmp/try_body.json") \
        if body is not None else f"{CURL} -o /tmp/try_out.json -w '%{{http_code}}' -X {method}"
    for attempt in range(tries):
        # the AP kicks its client after a station (re)join (PMF SA query,
        # reason 209 in the console) and NetworkManager can hold the profile
        # "active" on a dead link: judge the LINK, re-join when it is down
        r = ssh_run(f"nmcli -t dev status | grep -q '^{USER_IF}:wifi:connected:{USER_CON}' || "
                    f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; "
                    f"{data} http://{WICAN_AP_IP}{path}; echo; cat /tmp/try_out.json")
        lines = (r.stdout or "").strip().split("\n")
        code = lines[0].strip() if lines else ""
        if code.isdigit() and int(code) > 0:
            return int(code), parse("\n".join(lines[1:]))
        if attempt + 1 < tries:
            note(f"phone: no answer to {method} {path} (attempt {attempt + 1}), retrying")
            time.sleep(2)
    return 0, None


def poll_done(ssid, budget=50):
    """GET /api/wifi/try once a second until state done for this ssid. The
    phone loses the AP around every radio event: two misses in a row
    re-join it by hand (what a user's phone does by itself)."""
    t0 = time.time()
    last = None
    misses = 0
    while time.time() - t0 < budget:
        code, st = ap_req("GET", "/api/wifi/try", tries=1)
        if st and st.get("state") == "done" and st.get("ssid") == ssid:
            return st, time.time() - t0
        if st is None:
            misses += 1
            if misses >= 2:
                ssh_run(f"sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; true")
                misses = 0
        else:
            last = st
            misses = 0
        time.sleep(1)
    return last, time.time() - t0


def since(con):
    """The console stamps lines relative to its open time."""
    return time.time() - con.t0


def sta_back(dut_ip, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = ssh_run(f"curl -s -m 3 http://{dut_ip}/api/wifi/status")
        st = parse(r.stdout)
        if st and st.get("sta_connected") and st.get("ip") == dut_ip:
            return True, round(time.time() - t0, 1)
        time.sleep(2)
    return False, timeout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wican", default="auto")
    ap.add_argument("--dut", default="10.42.1.194")
    a = ap.parse_args()
    wican = bench_ports.resolve(a.wican, "wican_console")
    dut = a.dut

    # the home network = the bench AP (the Pi's profile carries its PSK)
    r = ssh_run(f"nmcli -g 802-11-wireless.ssid con show {HOME_CON}; "
                f"sudo -n nmcli -s -g 802-11-wireless-security.psk con show {HOME_CON}")
    parts = (r.stdout or "").strip().split("\n")
    home_ssid = parts[0].strip() if parts else ""
    home_psk = parts[1].strip() if len(parts) > 1 else ""
    check("bench: the home network's name and password are known", bool(home_ssid) and len(home_psk) >= 8, home_ssid)

    r = ssh_run(f"curl -s -m 4 http://{dut}/api/info; echo; curl -s -m 4 http://{dut}/api/settings/wifi_manager")
    lines = (r.stdout or "").strip().split("\n")
    info = parse(lines[0]) if lines else None
    cfg = parse(lines[1]) if len(lines) > 1 else None
    check("dut: answers on the bench AP and has the trial route", bool(info) and bool(cfg),
          (info or {}).get("fw_version", "no answer"))
    did = (info or {}).get("device_id", "")
    ap_ssid = f"WiCAN_{did}"
    ap_psk = os.environ.get("WICAN_AP_PSK", "quick-setup-2026")
    cfg_before = {k: (cfg or {}).get(k) for k in ("mode", "sta_ssid", "sta_trusted")}
    check("dut: configured as apsta on the home network", cfg_before.get("mode") == "apsta" and cfg_before.get("sta_ssid") == home_ssid, cfg_before)

    con = Console(wican, 2000000)
    t_start = time.time()
    try:
        boots0 = restart_stats(con)
        # the phone joins the DUT's access point
        ssh_run(f"sudo -n nmcli con delete {USER_CON} >/dev/null 2>&1; true")
        r = ssh_run(
            f"sudo -n nmcli con add type wifi ifname {USER_IF} con-name {USER_CON} "
            f"autoconnect no ssid '{ap_ssid}' 802-11-wireless-security.key-mgmt wpa-psk "
            f"802-11-wireless-security.psk '{ap_psk}' 802-11-wireless-security.psk-flags 0 "
            f">/dev/null && sudo -n nmcli con up {USER_CON} >/dev/null 2>&1 && sleep 3 && "
            f"nmcli -t dev status | grep -q '^{USER_IF}:wifi:connected:{USER_CON}' && echo ok")
        check("phone: joined the DUT's access point", "ok" in (r.stdout or ""), ap_ssid)
        code, st = ap_req("GET", "/api/wifi/try")
        check("phone: GET /api/wifi/try answers idle before any trial", code == 200 and st and st.get("state") in ("idle", "done"), (code, st))

        # ---- bad input ----
        code, st = ap_req("POST", "/api/wifi/try", {"ssid": home_ssid, "password": "short"})
        check("bad: a 5-character password is refused with 400", code == 400, (code, st))
        code, st = ap_req("POST", "/api/wifi/try", {"password": "letmein-please"})
        check("bad: no ssid is refused with 400", code == 400, (code, st))

        # ---- right (and busy: the second POST rides the same ssh hop, a
        # trial can be over in 4 s and the hop alone can take that) ----
        mark = since(con)
        body = b64({"ssid": home_ssid, "password": home_psk})
        # plain curl, no retries (a retried POST lands after the trial and
        # starts a second one), three in a row with their stamps. The radio
        # action starts 300 ms after the 202 and the AP blinks then, so a
        # POST can get no answer at all (000). Judged: the first 202 (the
        # hop is retried while the phone's link gives nothing), a 409 among
        # the next ones when the link let them through, and a FAIL only when
        # two 202s come within 2.5 s: two trials at once, which the firmware
        # must never allow
        codes, stamps, st, rest = [], [], None, ""
        for hop in range(3):
            r = ssh_run(f"nmcli -t dev status | grep -q '^{USER_IF}:wifi:connected:{USER_CON}' || sudo -n nmcli con up {USER_CON} >/dev/null 2>&1; "
                        f"echo {body} | base64 -d > /tmp/try_body.json; rm -f /tmp/try_out*.json; "
                        f"for i in 1 2 3; do date +%s.%N; curl -s -m 3 -o /tmp/try_out$i.json -w '%{{http_code}}\n' -X POST -H 'Content-Type: application/json' -d @/tmp/try_body.json http://{WICAN_AP_IP}/api/wifi/try; done; "
                        f"for i in 1 2 3; do echo ==$i; cat /tmp/try_out$i.json 2>/dev/null; echo; done")
            out = (r.stdout or "").strip()
            head, _, tail = out.partition("==1")
            hl = [l.strip() for l in head.strip().split("\n") if l.strip()]
            stamps = [float(x) for x in hl[0::2]] if len(hl) >= 2 else []
            codes = hl[1::2]
            bodies = {}
            for chunk in ("==1" + tail).split("=="):
                if chunk[:1].isdigit():
                    bodies[int(chunk[0])] = chunk[1:].strip()
            st = parse(bodies.get(1, ""))
            rest = " ".join(bodies.get(i, "") for i in (2, 3))
            if codes[:1] == ["202"]:
                break
            note(f"right: the phone's link gave {codes} for the POSTs (hop {hop + 1}), again")
            time.sleep(2)
        # the status line is the firmware's word; the body can be cut short by
        # the blink that follows the 202 by 300 ms
        check("right: POST answers 202 running", codes[:1] == ["202"] and (st is None or (st.get("state") == "running" and st.get("ssid") == home_ssid)), (codes, st))
        overlap = any(c == "202" and stamps and i < len(stamps) and stamps[i] - stamps[0] < 2.5 for i, c in enumerate(codes) if i > 0)
        check("busy: never two trials at once (a second 202 within 2.5 s)", not overlap, (codes, [round(x - stamps[0], 2) for x in stamps] if stamps else []))
        if "409" in codes[1:]:
            check("busy: a POST while one runs is refused with 409", "already running" in rest, (codes, rest[:80]))
        else:
            note(f"busy: the refusal was not observed this run (the link gave {codes[1:]} in the blink); the 409 path is pinned by the host suite and by earlier runs")
        st, took = poll_done(home_ssid)
        check("right: done / connected within 20 s with an address, rssi and channel",
              st and st.get("result") == "connected" and st.get("ip", "").startswith("10.42.1.") and st.get("rssi", 0) < 0 and st.get("channel", 0) > 0 and st.get("took_ms", 99999) < 20000,
              st)
        hit = con.wait_for(r"trial: '" + re.escape(home_ssid) + r"' connected, ([0-9.]+), (-?\d+) dBm, channel (\d+), in (\d+) ms", 10, mark)
        check("right: the console's trial line", hit is not None, hit[1] if hit else "no trial line")
        back, secs = sta_back(dut)
        check("right: the configured station is back on the home network within 30 s (no AP-client pause after a trial)", back and secs <= 30, f"{secs} s")

        # ---- wrong ----
        mark = since(con)
        code, st = ap_req("POST", "/api/wifi/try", {"ssid": home_ssid, "password": home_psk[:-1] + ("x" if home_psk[-1] != "x" else "y")})
        check("wrong: POST answers 202", code == 202, (code, st))
        st, took = poll_done(home_ssid)
        check("wrong: done / password with the handshake reason",
              st and st.get("result") == "password" and st.get("reason") in (204, 15, 2, 202), st)
        hit = con.wait_for(r"trial: '" + re.escape(home_ssid) + r"' did not accept the password \(reason (\d+)\)", 10, mark)
        check("wrong: the console's trial line", hit is not None, hit[1] if hit else "no trial line")
        depri = con.find(r"failed \d+ attempts in a row", mark)
        check("wrong: the configured network's attempt memory was not charged", depri is None, depri[1] if depri else "")
        back, secs = sta_back(dut)
        check("wrong: the configured station is back on the home network within 30 s", back and secs <= 30, f"{secs} s")

        # ---- nowhere ----
        mark = since(con)
        code, st = ap_req("POST", "/api/wifi/try", {"ssid": "no-such-net-4e1", "password": "letmein-please"})
        check("nowhere: POST answers 202", code == 202, (code, st))
        st, took = poll_done("no-such-net-4e1")
        check("nowhere: done / not_found, reason 201", st and st.get("result") == "not_found" and st.get("reason") == 201, st)
        hit = con.wait_for(r"trial: 'no-such-net-4e1' not found \(reason 201\)", 10, mark)
        check("nowhere: the console's trial line", hit is not None, hit[1] if hit else "no trial line")
        back, secs = sta_back(dut)
        check("nowhere: the configured station is back on the home network within 30 s", back and secs <= 30, f"{secs} s")

        # ---- console ----
        line = con.cmd(f"wifi --try {home_ssid} --password {home_psk}", r"^(connected: [0-9.]+, -?\d+ dBm|(password|not_found|refused|no_ip|timeout) \(reason|Error)", 30)
        check("console: wifi --try prints the connected line", line is not None and line.startswith("connected:"), line)
        back, secs = sta_back(dut)
        check("console: the configured station is back on the home network within 30 s", back and secs <= 30, f"{secs} s")

        # ---- health ----
        r = ssh_run(f"curl -s -m 4 http://{dut}/api/settings/wifi_manager; echo; curl -s -m 4 http://{dut}/api/wifi/status")
        lines = (r.stdout or "").strip().split("\n")
        cfg2 = parse(lines[0]) if lines else None
        wst = parse(lines[1]) if len(lines) > 1 else None
        cfg_after = {k: (cfg2 or {}).get(k) for k in ("mode", "sta_ssid", "sta_trusted")}
        check("health: the configured settings are unchanged", cfg_after == cfg_before, cfg_after)
        check("health: the access point is still up", bool(wst) and wst.get("ap_started") is True, wst)
        boots1 = restart_stats(con)
        check("health: no restart during the bench", boots0[0] >= 0 and boots0 == boots1, (boots0, boots1))
        errs = con.e_lines()
        check("health: 0 E lines", len(errs) == 0, errs[:3])
    finally:
        ssh_run(f"sudo -n nmcli con down {USER_CON} >/dev/null 2>&1; "
                f"sudo -n nmcli con delete {USER_CON} >/dev/null 2>&1; true")
        con.close()

    print(f"\n{len(fails)} failure(s), {round(time.time() - t_start)} s")
    print("WIFI TRY PASS" if not fails else "WIFI TRY FAIL: " + ", ".join(fails))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
