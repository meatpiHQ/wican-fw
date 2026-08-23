"""WiCAN Pro v6 SYSTEM bench — the composed main firmware, end to end.
Runs ON rpi001 (it plays the phone for AP onboarding, then the LAN).

Flow (the real product journey):
  1. ONBOARD  — join the DUT's factory AP (WiCAN_<12-hex id> / @meatpi#),
                talk to http://192.168.0.10: /api/status (bits + MEMORY),
                /api/settings lists every component, /api/status/tasks.
  2. CONFIGURE— PUT wifi (STA to the bench AP) + bridges (obd<->obd0) over
                the API, POST submit -> {"reboot":true} (the real
                submit-then-reboot path on the real firmware).
  3. STA      — swing the bench radios back to hotspot WICAN_TEST_AP; the DUT
                reboots, applies, joins; find its lease.
  4. FUNCTION — /api/status again (sta_connected bit), restart history shows
                the planned config_apply reboot, OBD chip over TCP:35000
                through the configured bridge (ATI + live-ECU 0100).
  5. PERF     — ATI RTT percentiles over TCP; 30 s of 0100 polling as load;
                memory snapshots before/after: internal largest_block must
                not collapse (fragmentation stability, Architecture 12b).
  6. HEALTH   — the silent-failure net (2026-07-19): zero log errors,
                registry headroom, flash-op budgets (boot + idle-quiet),
                task stack headroom, and ZERO latched fault codes
                (/api/faults — the device-DTC store).

  7. WS/STA   — ws_obd ELM answer over the CONFIGURED network (step 1
                proved it on the factory AP only).
  8. DEGRADED — (skip: --no-degraded) a socket server on port 80 collides
                with httpd at start (invisible to settings validation):
                boot must COMPLETE, the API must answer, log_errors +
                the boot_errors fault must latch, the healthy bridges
                must still serve; then restore + clear -> clean reboot.
  9. OTA      — (--ota <bin>) full OTA cycle: upload -> ota_apply restart
                intent in history -> next-target partition flipped ->
                clean health on the new image.

Usage (on rpi001):  python3 system_bench.py [--id 14c19f44e349] [--secs 30]
                    [--ota /tmp/wican-fw.bin] [--no-degraded]
Expected final line: SYSTEM BENCH PASS
"""
import argparse
import json
import os
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import benchlib                          # noqa: E402
from benchlib import (AP_IP, BENCH_SSID, RigFault, bench_psk, find_dut,
                      obd_tcp as obd_transact, sh)

BENCH_PSK = benchlib.BENCH_PSK_FALLBACK  # fallback only — the STANDING
                         # wican-bench profile's real PSK is read at runtime
                         # via bench_psk() (2026-07-19: recreating the
                         # hotspot with this constant silently CHANGED the
                         # bench password and broke every stored DUT config)


def http(method, host, path, body=None, timeout=None, tries=None):
    """Shim onto benchlib.api — ONE timeout policy, rig-classified.
    (timeout/tries args accepted for call-site compatibility; the policy
    constants live in benchlib/TESTING.md, not here.)"""
    return benchlib.api(host, path, method=method, body=body)


def main():
    try:
        return run()
    except RigFault as e:
        # NOT a firmware failure — the rig broke and the ladder could not
        # recover it. Distinct verdict so the report never blames the DUT.
        print(f"SYSTEM BENCH RIG FAULT: {e}")
        return 2


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="device id = AP SSID suffix, e.g. 14c19f44e349")
    ap.add_argument("--secs", type=int, default=30)
    ap.add_argument("--ota", default=None,
                    help="firmware .bin for the OTA leg (skipped if absent)")
    ap.add_argument("--no-degraded", action="store_true",
                    help="skip the degraded-boot leg (2 extra reboots)")
    args = ap.parse_args()
    fails = []

    def check(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")

        if not ok:
            fails.append(name)

    PHASES = 9                          # BenchBoard progress-bar contract

    def phase(n, title):
        print(f"PROGRESS {n - 1}/{PHASES}", flush=True)
        print(f"== phase {n}/{PHASES}: {title} ==", flush=True)

    phase(1, "ONBOARD — join the factory AP")
    # ---- 1. ONBOARD: join the factory AP ---------------------------------
    dut_ap = f"WiCAN_{args.id}"

    # radio roles (udev-pinned 2026-07-26; benchlib falls back to the old
    # wlanN names on a pre-rename Pi)
    ifs = benchlib.hotspot_ifaces()
    onboard_if = ifs[0] if ifs else "wint0"           # internal radio
    twin_if = ifs[1] if len(ifs) > 1 else "wtest0"    # USB stick twin

    print(f"joining factory AP {dut_ap} ...")
    sh("sudo nmcli connection delete wican-dut 2>/dev/null")
    # Join on the INTERNAL radio (reliable brcmfmac). The USB sticks
    # intermittently wedge both as APs and as scanners (re-bitten twice
    # 2026-07-26: 4-retry scans found a beaconing AP another radio saw at
    # signal 94). The internal radio normally hosts the bench hotspot —
    # during ONBOARD nothing needs it (the DUT is the only bench client
    # and it's about to reboot into STA); step 3 restores it.
    # PARK with autoconnect=no (the HIL park_persistent convention): a
    # plain `down` leaves NM free to re-up the profile mid-onboard AND
    # the beacon watchdog armed — it fought this bench 2026-07-26
    # (declared the SSID off-air during ONBOARD, failed over, left the
    # primary down). autoconnect=no = the watchdog's stand-down signal.
    for con in ("wican-bench-w0", "wican-bench"):
        sh(f"sudo nmcli connection modify {con} "
           f"connection.autoconnect no 2>/dev/null")
        sh(f"sudo nmcli connection down {con} 2>/dev/null")
    time.sleep(2)
    rc, out = 1, ""

    for attempt in range(4):
        rc, out = sh(f"sudo nmcli device wifi connect '{dut_ap}' "
                     f"password '@meatpi#' ifname {onboard_if} "
                     f"name wican-dut", 90)

        if rc == 0:
            break

        sh(f"sudo nmcli device wifi rescan ifname {onboard_if} 2>/dev/null")
        time.sleep(6)

    check("onboard_join_ap", rc == 0, out[-80:])

    # PIN the routes (2026-07-19): the factory AP subnet 192.168.0.0/24
    # COLLIDES with LANs the Pi may already sit on — an unpinned second
    # /24 blackholes the Pi's own uplink (incl. the ssh running this).
    # never-default + high metric + a /32 to the DUT keeps only DUT
    # traffic on this radio.
    sh("sudo nmcli connection modify wican-dut ipv4.never-default yes "
       "ipv4.route-metric 4000 ipv4.routes 192.168.0.10/32")
    sh("sudo nmcli connection up wican-dut", 45)

    # wait for DHCP/ARP on the DUT's AP subnet to settle
    st = 0
    body = ""
    deadline = time.time() + 45

    while time.time() < deadline:
        try:
            st, body = http("GET", AP_IP, "/api/status", timeout=4)
            break
        except OSError:
            time.sleep(2)

    check("onboard_reachable", st == 200,
          f"ip: {sh(f'ip -4 addr show {onboard_if} | grep inet')[1].strip()[:50]}")

    if st != 200:  # can't onboard -> nothing downstream can run
        print("SYSTEM BENCH FAIL: " + ",".join(fails))
        return 1

    status = json.loads(body)
    mem = status.get("memory", {})
    check("onboard_status", st == 200 and status["bits"]["awake"], "")
    check("onboard_memory",
          mem.get("internal", {}).get("largest_block", 0) > 0,
          f"int_free={mem['internal']['free']} "
          f"int_largest={mem['internal']['largest_block']}")

    st, body = http("GET", AP_IP, "/api/settings")
    comps = json.loads(body)["components"]
    # enriched entries since 2026-07-05: [{"name","version","degraded",
    # "pending_reboot"}]; tolerate the old plain-name shape too
    names = {c["name"] if isinstance(c, dict) else c for c in comps}
    expected = {"wifi_manager", "obd_chip", "ble_manager", "socket_manager",
                "bridge_manager", "log_manager"}
    check("onboard_settings_list", expected.issubset(names),
          str(sorted(names)))

    st, body = http("GET", AP_IP, "/api/status/tasks")
    check("onboard_tasks", st == 200 and "main" in body,
          f"{len(body.splitlines())} tasks")

    # out-of-the-box OBD over WebSocket (shipped defaults since 2026-07-05:
    # ws_obd channel + obd<->ws_obd bridge both enabled) — must answer on
    # the factory AP with zero configuration
    try:
        import websocket
        buf = b""

        for attempt in range(2):  # a stale client slot from a prior run
            w = websocket.WebSocket()  # can eat the first try
            w.connect(f"ws://{AP_IP}/ws/obd", timeout=5)
            w.send_binary(b"ATI\r")
            w.settimeout(3)
            try:
                while b">" not in buf:
                    _, d = w.recv_data()
                    buf += d
            except Exception:
                pass
            w.close()

            if b">" in buf:
                break

            time.sleep(2)
        check("onboard_ws_obd_default", b"ELM327" in buf, repr(buf[:40]))
    except Exception as e:
        check("onboard_ws_obd_default", False, str(e))

    phase(2, "CONFIGURE over the API")
    # ---- 2. CONFIGURE over the API ---------------------------------------
    st, body = http("GET", AP_IP, "/api/settings/wifi_manager")
    wifi = json.loads(body)
    wifi.pop("degraded", None)
    wifi.update({"mode": "apsta", "sta_ssid": BENCH_SSID,
                 "sta_password": bench_psk()})
    st, body = http("PUT", AP_IP, "/api/settings/wifi_manager", wifi)
    # changed:false is VALID — a device already carrying this exact
    # config answers idempotently and skips the flash write (the §11
    # change-guard discipline); the sta_join step downstream is the
    # real assertion that the config is in effect
    check("configure_wifi", st == 200, body)

    # the SHIPPED defaults (2026-07-19) already carry the full trio on the
    # obd fan-out — ws_obd (web UI terminal), TCP:35000 and USB. Assert
    # they're present and REPAIR if a previous config replaced them (this
    # script's own pre-trio PUT used to downgrade to one bridge).
    DEFAULT_BRIDGES = {"bridges": [
        {"name": "br_obd", "a": "obd", "b": "ws_obd",
         "translator": "raw", "enabled": True},
        {"name": "br_tcp_obd", "a": "obd0", "b": "obd",
         "translator": "raw", "enabled": True},
        {"name": "br_usb_obd", "a": "usb_obd", "b": "obd",
         "translator": "raw", "enabled": True}]}
    st, body = http("GET", AP_IP, "/api/settings/bridge_manager")
    have = {(b.get("a"), b.get("b"))
            for b in json.loads(body).get("bridges", []) if b.get("enabled")}
    want = {(b["a"], b["b"]) for b in DEFAULT_BRIDGES["bridges"]}

    if not want.issubset(have):
        st, body = http("PUT", AP_IP, "/api/settings/bridge_manager",
                        DEFAULT_BRIDGES)

    check("configure_bridge", st == 200, body[-70:])

    st, body = http("POST", AP_IP, "/api/settings/submit")
    # reboot:false is VALID when nothing was staged (fully idempotent
    # re-run on an already-configured device)
    check("submit_reboot", st == 200, body)
    submit_rebooted = json.loads(body).get("reboot", False)

    phase(3, "hotspot back; DUT reboots into STA")
    # ---- 3. bring the bench hotspot back; DUT reboots into STA ------------
    sh("sudo nmcli connection down wican-dut")
    sh("sudo nmcli connection delete wican-dut")
    # unpark (autoconnect back on — see the ONBOARD park note)
    for con in ("wican-bench-w0", "wican-bench"):
        sh(f"sudo nmcli connection modify {con} "
           f"connection.autoconnect yes 2>/dev/null")
    # REUSE the standing hotspot profiles (never recreate — that would
    # overwrite the bench PSK, see BENCH_PSK note). PREFER the internal (wint0) radio
    # (wican-bench-w0): the USB sticks intermittently WEDGES as an
    # AP — nmcli reports "activated" while nothing beacons (2026-07-17
    # rig note; re-bitten 2026-07-26, stranded the DUT mid-bench).
    rc, out = sh("sudo nmcli connection up wican-bench-w0", 60)

    if rc != 0:
        rc, out = sh("sudo nmcli connection up wican-bench", 60)

    if rc != 0:
        rc, out = sh(f"sudo nmcli device wifi hotspot ifname {twin_if} "
                     f"con-name wican-bench ssid {BENCH_SSID} "
                     f"password {BENCH_PSK}", 60)

    check("bench_ap_up", rc == 0, out[-60:])

    # the bench hotspot has a failover twin (2026-07-17 rig) — the DUT
    # may associate with either radio, so search both; classify=True:
    # an empty window consults bench-health/bench-recover before failing
    dut_ip = find_dut(180, classify=True)
    check("sta_join", dut_ip is not None, f"dut_ip={dut_ip}")

    if dut_ip is None:
        print("SYSTEM BENCH FAIL")
        return 1

    phase(4, "FUNCTION over STA (OBD via ECU sim)")
    # ---- 4. FUNCTION over STA ---------------------------------------------
    st, body = http("GET", dut_ip, "/api/status")
    status = json.loads(body)
    check("sta_status_bit", status["bits"]["sta_connected"], "")

    st, body = http("GET", dut_ip, "/api/restart/history")
    hist = json.loads(body)
    newest = hist["records"][0]

    if submit_rebooted:
        check("history_config_apply",
              newest["planned"] and
              newest["planned_reason"] == "config_apply" and
              newest["source"] == "config_server",
              f"seq={newest['seq']}")
    else:
        # idempotent run: no config reboot happened, so the newest record
        # belongs to whatever rebooted the DUT last — assert it was PLANNED
        # or a plain power-on (fresh flash / bench PSU cycle; first live
        # OTA run 2026-07-26 hit exactly this). A panic/watchdog newest
        # still fails here.
        check("history_config_apply",
              newest["planned"] or newest["reason"] == "poweron",
              f"(idempotent run) newest={newest['reason']}/"
              f"{newest['planned_reason']}")

    r = obd_transact(dut_ip, b"ATI\r")
    check("obd_ati", "ELM327" in r, r.strip()[:40])
    r = obd_transact(dut_ip, b"0100\r")
    check("obd_0100_live_ecu", "41 00" in r, r.strip()[:40])

    phase(5, f"PERF + fragmentation stability ({args.secs} s windows)")
    # ---- 5. PERF + fragmentation stability ---------------------------------
    st, body = http("GET", dut_ip, "/api/status")
    mem_a = json.loads(body)["memory"]

    s = socket.create_connection((dut_ip, 35000), timeout=5)
    s.settimeout(2)
    time.sleep(0.3)
    rtts = []
    polls = 0
    t0 = time.time()

    while time.time() - t0 < args.secs:
        t = time.perf_counter()

        try:
            s.sendall(b"0100\r")
            buf = b""

            while b">" not in buf:
                b = s.recv(256)

                if not b:
                    raise OSError("closed")

                buf += b
        except socket.timeout:
            continue
        except OSError:
            # a real client reconnects on a dropped stream — so does the
            # bench (transient RF resets must not kill the perf leg)
            try:
                s.close()
            except OSError:
                pass
            time.sleep(1)
            s = socket.create_connection((dut_ip, 35000), timeout=5)
            s.settimeout(2)
            continue

        rtts.append((time.perf_counter() - t) * 1000)
        polls += 1

    s.close()
    rtts.sort()
    check("perf_polling", len(rtts) > 50,
          f"{polls} polls in {args.secs}s, rtt p50="
          f"{statistics.median(rtts):.1f}ms "
          f"p95={rtts[int(len(rtts) * 0.95) - 1]:.1f}ms")

    st, body = http("GET", dut_ip, "/api/status")
    mem_b = json.loads(body)["memory"]
    shrink = (mem_a["internal"]["largest_block"] -
              mem_b["internal"]["largest_block"])
    check("fragmentation_stable", shrink < 8192,
          f"int_largest {mem_a['internal']['largest_block']} -> "
          f"{mem_b['internal']['largest_block']} "
          f"free {mem_b['internal']['free']} "
          f"psram_largest {mem_b['psram']['largest_block']}")

    phase(6, "HEALTH nets (faults/log counters/caps)")
    # ---- 6. HEALTH (2026-07-19: the silent-failure net) --------------------
    # log errors, flash-op budgets, registry headroom, task stacks, and the
    # latched fault codes (device DTCs). A healthy run ends with ZERO
    # errors, near-zero idle flash traffic, headroom everywhere, no faults.
    st, body = http("GET", dut_ip, "/api/status")
    health = json.loads(body).get("health", {})
    check("health_present", bool(health), "no health object in /api/status")

    if health:
        check("health_no_log_errors", health.get("log_errors", 1) == 0,
              f"log_errors={health.get('log_errors')} "
              f"warnings={health.get('log_warnings')}")

        for name, c in health.get("caps", {}).items():
            check(f"caps_{name}_headroom", c["cap"] - c["used"] >= 2,
                  f"{c['used']}/{c['cap']}")

        # idle flash traffic: 20 s with nothing running must not erase
        fl_a = health.get("flash")

        if fl_a:
            time.sleep(20)
            st, body = http("GET", dut_ip, "/api/status")
            fl_b = json.loads(body)["health"]["flash"]
            check("flash_idle_quiet",
                  fl_b["erases"] - fl_a["erases"] == 0 and
                  fl_b["writes"] - fl_a["writes"] <= 4,
                  f"idle delta: writes +{fl_b['writes'] - fl_a['writes']} "
                  f"erases +{fl_b['erases'] - fl_a['erases']}")
            check("flash_boot_budget", fl_a["erases"] <= 64,
                  f"erases since boot: {fl_a['erases']} "
                  f"(writes {fl_a['writes']})")

    st, body = http("GET", dut_ip, "/api/status/tasks")
    tasks = json.loads(body).get("tasks", [])
    tight = [(t["name"], t["stack_hw"]) for t in tasks
             if t.get("stack_hw", 99999) < 256]
    check("task_stack_headroom", len(tight) == 0, f"tight: {tight}")

    st, body = http("GET", dut_ip, "/api/faults")
    faults = json.loads(body).get("faults", [])
    check("no_fault_codes", len(faults) == 0,
          "; ".join(f"{f['code']} x{f['count']}" for f in faults) or "clean")

    phase(7, "WS over STA (production remote path)")
    # ---- 7. WS over STA (the production remote path) ------------------------
    # step 1 proved ws_obd on the factory AP; this is the same channel on
    # the CONFIGURED network — route registration + network-trust gate.
    try:
        import websocket
        w = websocket.WebSocket()
        w.connect(f"ws://{dut_ip}/ws/obd", timeout=5)
        w.send_binary(b"ATI\r")
        w.settimeout(3)
        buf = b""
        try:
            while b">" not in buf:
                _, d = w.recv_data()
                buf += d
        except Exception:
            pass
        w.close()
        check("ws_over_sta", b"ELM327" in buf, repr(buf[:40]))
    except Exception as e:
        check("ws_over_sta", False, str(e))

    phase(8, "DEGRADED / GUARDED CONFIG legs")
    # ---- 8. DEGRADED / GUARDED CONFIG (rev 2.3/2.6) --------------------------
    # 8a. the validation net: a tcp:80 socket server would split SYNs with
    #     httpd nondeterministically (live find 2026-07-26 — lwip
    #     SO_REUSEADDR lets both LISTEN) — the PUT must be REJECTED.
    # 8b. never-brick: a bridge to a configured-but-DISABLED server is
    #     schema-valid and jack-valid; the boot must complete CLEAN with
    #     the bridge dormant (subscribe parks, no error storm).
    if not args.no_degraded:
        st, body = http("GET", dut_ip, "/api/settings/socket_manager")
        sock_before = json.loads(body)

        for k in ("degraded", "pending_reboot"):
            sock_before.pop(k, None)

        broken = json.loads(json.dumps(sock_before))  # deep copy
        victim = next((s for s in broken.get("servers", [])
                       if not s.get("enabled")), broken["servers"][-1])
        victim.update({"proto": "tcp", "port": 80, "enabled": True})

        try:
            st, body = http("PUT", dut_ip, "/api/settings/socket_manager",
                            broken)
        except urllib.error.HTTPError as e:
            st, body = e.code, e.read().decode()

        check("guard_httpd_port_rejected",
              st == 400 and "web server" in body, f"{st} {body[-60:]}")

        # 8b: a dead bridge (disabled server's jack) must boot clean
        st, body = http("GET", dut_ip, "/api/settings/bridge_manager")
        bm_before = json.loads(body)

        for k in ("degraded", "pending_reboot"):
            bm_before.pop(k, None)

        disabled_srv = next((s["name"] for s in
                             sock_before.get("servers", [])
                             if not s.get("enabled")), None)
        used = {b.get("a") for b in bm_before.get("bridges", [])} | \
               {b.get("b") for b in bm_before.get("bridges", [])}
        # an endpoint that is registered on every build but free
        free_ep = next((ep for ep in ("usb_obd", "ws_can")
                        if ep not in used), None)

        if disabled_srv and free_ep and \
           len(bm_before.get("bridges", [])) < 6:
            bm = json.loads(json.dumps(bm_before))
            bm["bridges"].append(
                {"name": "br_dead", "a": disabled_srv, "b": free_ep,
                 "translator": "raw", "enabled": True})
            st, body = http("PUT", dut_ip, "/api/settings/bridge_manager",
                            bm)
            check("degraded_bridge_put", st == 200, body[-60:])
            http("POST", dut_ip, "/api/settings/submit")
            time.sleep(8)
            dut_ip = find_dut(180, classify=True)
            check("degraded_boot_completes", dut_ip is not None,
                  f"dut_ip={dut_ip}")

            if dut_ip is None:
                print("SYSTEM BENCH FAIL: " + ",".join(fails))
                return 1

            st, body = http("GET", dut_ip, "/api/status")
            h = json.loads(body).get("health", {})
            check("degraded_boot_clean_dormant",
                  h.get("log_errors", 1) == 0,
                  f"log_errors={h.get('log_errors')}")
            # the healthy bridges must still serve
            r = obd_transact(dut_ip, b"ATI\r")
            check("degraded_obd_still_up", "ELM327" in r, r.strip()[:40])

            # restore
            st, body = http("PUT", dut_ip,
                            "/api/settings/bridge_manager", bm_before)
            check("degraded_restore_put", st == 200, body[-60:])
            http("POST", dut_ip, "/api/settings/submit")
            time.sleep(8)
            dut_ip = find_dut(180, classify=True)
            check("degraded_recovery_boot", dut_ip is not None, "")

            if dut_ip is None:
                print("SYSTEM BENCH FAIL: " + ",".join(fails))
                return 1
        else:
            print(f"(8b skipped: disabled_srv={disabled_srv} "
                  f"free_ep={free_ep} "
                  f"bridges={len(bm_before.get('bridges', []))})")

    phase(9, "OTA leg" + ("" if args.ota else " (skipped — no --ota)"))
    # ---- 9. OTA (optional: --ota <bin>) -------------------------------------
    if args.ota:
        # the RUNNING partition lives in /api/status (ota/status.partition
        # is only populated DURING an upload — first live OTA run
        # 2026-07-26 found the leg asserting on the wrong field)
        st, body = http("GET", dut_ip, "/api/status")
        part_before = json.loads(body).get("partition", "")

        with open(args.ota, "rb") as f:
            img = f.read()

        req = urllib.request.Request(f"http://{dut_ip}/api/ota/upload",
                                     method="POST", data=img)
        req.add_header("Content-Type", "application/octet-stream")

        try:
            with urllib.request.urlopen(req,
                                        timeout=benchlib.OTA_UPLOAD) as resp:
                out = json.loads(resp.read())
        except Exception as e:
            out = {"error": str(e)}

        check("ota_upload", out.get("ok") is True and out.get("reboot"),
              f"{out} ({len(img)} B)")
        time.sleep(8)
        dut_ip = find_dut(180, classify=True)
        check("ota_reboot_back", dut_ip is not None, f"dut_ip={dut_ip}")

        if dut_ip is not None:
            st, body = http("GET", dut_ip, "/api/restart/history")
            newest = json.loads(body)["records"][0]
            check("ota_history_intent",
                  newest["planned"] and
                  newest["planned_reason"] == "ota_apply",
                  f"{newest['planned_reason']}/{newest['source']}")
            st, body = http("GET", dut_ip, "/api/status")
            part_after = json.loads(body).get("partition", "")
            check("ota_partition_flip",
                  part_after and part_after != part_before,
                  f"running {part_before} -> {part_after}")
            st, body = http("GET", dut_ip, "/api/status")
            h = json.loads(body).get("health", {})
            check("ota_health_clean", h.get("log_errors", 1) == 0,
                  f"log_errors={h.get('log_errors')}")

    print(f"PROGRESS {PHASES}/{PHASES}", flush=True)
    print(f"SYSTEM BENCH {'FAIL: ' + ','.join(fails) if fails else 'PASS'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
