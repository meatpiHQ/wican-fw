#!/usr/bin/env python3
"""HA push over LTE via the ESPNetLink dongle - the car-on-the-road leg.

Runs ON the bench Pi (rpi001). Topology: the DUT sits on the Pi hotspot
(`wican-bench`, its primary network) with the paired dongle as its fallback
network (`ESPNetLink_<id6>`, WiFi-modem mode, LTE + GPS). The leg:

  1. registers on the DUT what the HA integration registers for a PRO
     device: `url` = HA's local http URL and `urls` = [local, external
     HTTPS (Nabu Casa / reverse proxy)] for the given webhook id;
  2. downs `wican-bench` so the DUT roams to the dongle AP; the Pi's free
     radio joins that AP (`espnl-client`) so this script keeps reaching the
     DUT at 192.168.80.x;
  3. asserts: uplink == espnetlink, the dongle reports LTE connected + a GPS
     fix, and the pushes reach HA: with a MATCHING HA entry `success_count`
     grows (the local URL is unreachable from LTE, so every success went
     through the HTTPS failover URL); with a foreign webhook id HA answers
     403 identity-rejected - also proof the push arrived through LTE - and
     the poster parks itself `rejected` after 3 cycles (contract);
  4. restores: DELETE the webhook, interval as found, `wican-bench` back up,
     `espnl-client` down (+ the profile that was on the free radio), waits
     for the DUT back on the bench address.

The HA side is checked from the PC afterwards (home-assistant.log:
"Received MeatPi device webhook: <id>" + the payload's `gps` block).

  python3 ha_lte_push_bench.py --webhook-id <id> \
      --ha-local http://192.168.0.197:8123 \
      --ha-remote https://<x>.ui.nabu.casa [--expect-403]

Verdict line: HA LTE PUSH PASS / FAIL.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def sh(cmd, timeout=30):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def api(host, path, method="GET", body=None, timeout=8, iface=None):
    data = json.dumps(body).encode() if body is not None else None
    if iface:
        # bind to the free radio for the dongle-AP side (the Pi also has a
        # hotspot on 10.42.x; source routing keeps this deterministic)
        cmd = ["curl", "-s", "-m", str(timeout), "--interface", iface, "-X", method,
               "-H", "Content-Type: application/json", "-w", "\n%{http_code}",
               f"http://{host}{path}"]
        if data is not None:
            cmd += ["-d", data.decode()]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        if p.returncode != 0 or not p.stdout.strip():
            raise OSError(f"curl rc={p.returncode}")
        body_txt, code = p.stdout.rsplit("\n", 1)
        return int(code), (json.loads(body_txt) if body_txt.strip().startswith(("{", "[")) else body_txt)
    req = urllib.request.Request(f"http://{host}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode()
            return r.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def find_dut(candidates, device_id, iface=None, deadline_s=180):
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        for h in candidates:
            try:
                code, info = api(h, "/api/info", timeout=4, iface=iface)
                if code == 200 and info.get("device_id") == device_id:
                    return h, time.time() - t0
            except Exception:
                pass
        time.sleep(3)
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dut", default="10.42.1.194,10.42.0.194",
                    help="DUT candidates on the bench hotspot")
    ap.add_argument("--webhook-id", help="HA webhook id to push to")
    ap.add_argument("--ha-local", help="HA local base, e.g. http://192.168.0.197:8123")
    ap.add_argument("--ha-remote", help="HA external HTTPS base (Nabu Casa)")
    ap.add_argument("--expect-403", action="store_true",
                    help="the webhook id belongs to ANOTHER device: expect identity rejection")
    ap.add_argument("--dut-vpn", metavar="IP",
                    help="the DUT's WireGuard tunnel address (wg_ha_route.sh: 10.9.0.2): reach it "
                         "THROUGH the tunnel after the roam instead of joining the dongle AP with "
                         "the free radio - the HA-over-VPN scenario, HA registered at its own "
                         "tunnel address")
    ap.add_argument("--capture", type=int, metavar="PORT",
                    help="payload-capture mode: no HA - a receiver on this Pi's free radio "
                         "(joined to the dongle AP) takes the pushes; asserts the contract "
                         "`gps` block + the gps_* sensors while the fix is live")
    ap.add_argument("--bench-con", default="wican-bench")
    ap.add_argument("--dongle-con", default="espnl-client")
    ap.add_argument("--free-radio", default="wtest1")
    ap.add_argument("--dongle-subnet", default="192.168.80")
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--observe", type=int, default=90)
    args = ap.parse_args()
    if args.dut_vpn and args.capture:
        ap.error("--dut-vpn is the HA-over-VPN mode (no local capture)")
    if not args.capture and not args.dut_vpn and not (args.webhook_id and args.ha_local and args.ha_remote):
        ap.error("either --capture PORT, --dut-vpn IP, or --webhook-id + --ha-local + --ha-remote")

    bench = [d.strip() for d in args.dut.split(",") if d.strip()]
    dut = None
    info = {}
    # rig: the DUT on the bench hotspot
    for h in bench:
        try:
            code, info = api(h, "/api/info", timeout=4)
            if code == 200:
                dut = h
                break
        except Exception:
            pass
    check("[RIG] DUT answers on the bench hotspot", dut is not None, args.dut)
    if dut is None:
        print("HA LTE PUSH FAIL: rig")
        sys.exit(1)
    device_id = info["device_id"]
    print(f"DUT {device_id} fw {info.get('fw_version')} at {dut}")
    code, es = api(dut, "/api/espnetlink")
    check("[RIG] espnetlink paired (wifi_modem)", es.get("paired") and es.get("mode") == "wifi_modem",
          f"paired={es.get('paired')} mode={es.get('mode')} ssid={es.get('ssid')}")
    code, hw = api(dut, "/api/settings/ha_webhooks")
    interval_before = hw.get("interval_s")
    rc, was_on_radio = sh(f"nmcli -t -f NAME,DEVICE con show --active | grep ':{args.free_radio}$' | cut -d: -f1")

    posts = []
    if args.capture:
        # payload-capture mode: the receiver runs here; the DUT reaches it on
        # the free radio's address once both sit on the dongle AP, so the
        # registration happens AFTER the roam (below)
        import http.server
        import threading

        class Recv(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n)
                try:
                    posts.append(json.loads(raw.decode("utf-8", "replace")))
                except Exception:
                    posts.append({"_raw": raw[:200].decode("utf-8", "replace")})
                self.send_response(204)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("0.0.0.0", args.capture), Recv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    elif args.dut_vpn:
        # HA-over-VPN: the integration itself registered HA's tunnel URL on
        # the device (nothing to register here); just note the config
        code, wh = api(dut, "/api/webhook")
        check("[RIG] webhook registered by HA (enabled + url)",
              isinstance(wh, dict) and wh.get("enabled") and wh.get("url"),
              str(wh.get("url") if isinstance(wh, dict) else wh)[:100])
        code, vs = api(dut, "/api/vpn")
        check("[RIG] DUT VPN connected on the bench hotspot", vs.get("state") == "connected",
              str(vs)[:100])
    else:
        # 1) the PRO dual-URL registration, exactly what the integration sends
        local = f"{args.ha_local.rstrip('/')}/api/webhook/{args.webhook_id}"
        remote = f"{args.ha_remote.rstrip('/')}/api/webhook/{args.webhook_id}"
        code, resp = api(dut, "/api/webhook", "POST",
                         {"url": local, "urls": [local, remote], "enabled": True,
                          "interval": args.interval})
        check("POST /api/webhook with urls[local, https] accepted", code in (200, 201), f"code={code}")
        code, wh = api(dut, "/api/webhook")
        check("GET /api/webhook reflects the HTTPS failover URL",
              isinstance(wh, dict) and (wh.get("urls") or [None, None])[1] == remote,
              str(wh.get("urls") if isinstance(wh, dict) else wh)[:120])

    dut_lte = None
    iface_for_dut = None
    # successes counted BEFORE the roam (pushes on the hotspot) are not LTE proof
    code, wh0 = api(dut, "/api/webhook")
    ok_before = wh0.get("success_count", 0) if isinstance(wh0, dict) else 0
    try:
        # 2) take the bench hotspot away -> the DUT roams to the dongle AP
        rc, out = sh(f"sudo -n nmcli con down {args.bench_con}")
        check(f"nmcli con down {args.bench_con}", rc == 0, out[:80])
        if args.dut_vpn:
            # reach the DUT through the WireGuard tunnel (betty relays between
            # peers); the free radio keeps the Pi's internet uplink
            myip = None
            dut_lte, took = find_dut([args.dut_vpn], device_id, deadline_s=300)
            check("DUT reachable at its tunnel address after the roam (LTE -> betty -> rpi001)",
                  dut_lte is not None,
                  f"{dut_lte} after {took:.0f} s" if dut_lte else f"{args.dut_vpn} silent for 300 s")
            iface_for_dut = None
        else:
            rc, out = sh(f"sudo -n nmcli con up {args.dongle_con}", timeout=60)
            check(f"nmcli con up {args.dongle_con} (free radio on the dongle AP)", rc == 0, out[:80])
            time.sleep(3)
            cands = [f"{args.dongle_subnet}.{i}" for i in range(2, 12)]
            rc, myip = sh(f"ip -4 -br addr show {args.free_radio} | awk '{{print $3}}' | cut -d/ -f1")
            cands = [c for c in cands if c != myip]
            dut_lte, took = find_dut(cands, device_id, iface=args.free_radio, deadline_s=240)
            check("DUT roamed to the dongle AP", dut_lte is not None,
                  f"{dut_lte} after {took:.0f} s" if dut_lte else "not found on the dongle AP in 240 s")
            iface_for_dut = args.free_radio
        if dut_lte is None:
            return
        if args.capture:
            url = f"http://{myip}:{args.capture}/hook"
            code, resp = api(dut_lte, "/api/webhook", "POST",
                             {"url": url, "enabled": True, "interval": args.interval},
                             iface=iface_for_dut)
            check("POST /api/webhook (capture receiver on the dongle AP) accepted",
                  code in (200, 201), f"code={code} url={url}")

        # 3) observe the LTE leg
        t0 = time.time()
        last = None
        final_es = final_wh = None
        while time.time() - t0 < args.observe:
            time.sleep(5)
            try:
                _, es = api(dut_lte, "/api/espnetlink", iface=iface_for_dut)
                _, wh = api(dut_lte, "/api/webhook", iface=iface_for_dut)
            except Exception as e:
                print(f"  t={time.time() - t0:3.0f}s poll failed: {e}")
                continue
            final_es, final_wh = es, wh
            line = ("  t=%3.0fs uplink=%s lte=%s gps=%s | poster status=%s ok=%s fail=%s err=%r" % (
                time.time() - t0, es.get("uplink"), (es.get("dongle") or {}).get("lte_connected"),
                (es.get("gps") or {}).get("valid"), wh.get("status"), wh.get("success_count"),
                wh.get("fail_count"), wh.get("last_error")))
            if line != last:
                print(line)
                last = line
            if args.capture:
                if any(p.get("gps") for p in posts) and len(posts) >= 3:
                    break
                continue
            if args.expect_403 and wh.get("status") == "rejected":
                break
            if not args.expect_403 and wh.get("success_count", 0) >= ok_before + 3:
                break
        es, wh = final_es or {}, final_wh or {}
        check("uplink == espnetlink (STA on the dongle AP)", es.get("uplink") == "espnetlink", str(es.get("uplink")))
        check("dongle reports LTE connected", (es.get("dongle") or {}).get("lte_connected") is True,
              str(es.get("dongle"))[:100])
        check("dongle GPS fix valid on the WiCAN side", (es.get("gps") or {}).get("valid") is True,
              str(es.get("gps"))[:100])
        if args.capture:
            check("pushes captured on the dongle AP (>= 2)", len(posts) >= 2, f"{len(posts)} posts")
            withgps = [p for p in posts if isinstance(p.get("gps"), dict)]
            check("a push carries the contract `gps` block", bool(withgps),
                  "sections of the last post: " + ",".join(posts[-1].keys()) if posts else "no posts")
            if withgps:
                g = withgps[0]["gps"]
                check("gps block has latitude/longitude/accuracy/altitude/speed/heading/satellites",
                      all(k in g for k in ("latitude", "longitude", "accuracy", "altitude",
                                           "speed", "heading", "satellites")),
                      json.dumps(g))
                lat, lon = g.get("latitude"), g.get("longitude")
                check("gps latitude/longitude within range",
                      isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and
                      -90 <= lat <= 90 and -180 <= lon <= 180, f"{lat}, {lon}")
                ad = withgps[0].get("autopid_data") or {}
                check("the same fix rides autopid_data as gps_latitude/gps_longitude",
                      "gps_latitude" in ad and "gps_longitude" in ad,
                      ",".join(k for k in ad if k.startswith("gps_")) or "no gps_* keys")
            check("device stats status=ok, success_count>=2",
                  wh.get("status") == "ok" and wh.get("success_count", 0) >= 2,
                  f"status={wh.get('status')} ok={wh.get('success_count')} fail={wh.get('fail_count')}")
        elif args.expect_403:
            check("push reached HA through LTE (HTTP 403 identity-rejected via the HTTPS URL)",
                  wh.get("last_error") == "http=403 identity rejected",
                  f"status={wh.get('status')} fail={wh.get('fail_count')} err={wh.get('last_error')!r}")
            check("poster parked 'rejected' after the 403 streak (contract)",
                  wh.get("status") == "rejected", str(wh.get("status")))
        else:
            check("pushes delivered through LTE (success_count grew after the roam)",
                  wh.get("success_count", 0) > ok_before,
                  f"status={wh.get('status')} ok={wh.get('success_count')} fail={wh.get('fail_count')} "
                  f"err={wh.get('last_error')!r}")
    finally:
        # 4) restore
        target = dut_lte or dut
        iface = iface_for_dut if dut_lte else None
        try:
            if args.dut_vpn:
                # the registration is HA's own (the integration owns url +
                # interval); deleting it would silence the device until the
                # entry is reloaded - leave it exactly as HA set it
                print("INFO: webhook left as registered by HA (VPN mode)")
                raise StopIteration
            code, _ = api(target, "/api/webhook", "DELETE", iface=iface)
            check("DELETE /api/webhook 204", code == 204, f"code={code}")
            code, hw = api(target, "/api/settings/ha_webhooks", iface=iface)
            if interval_before and code == 200 and hw.get("interval_s") != interval_before:
                doc = {k: v for k, v in hw.items() if k not in ("degraded", "pending_reboot")}
                doc["interval_s"] = interval_before
                code, _ = api(target, "/api/settings/ha_webhooks", "PUT", doc, iface=iface)
                check(f"ha_webhooks.interval_s staged back to {interval_before}", code == 200)
                api(target, "/api/settings/submit", "POST", {}, iface=iface)
        except StopIteration:
            pass
        except Exception as e:  # noqa: BLE001
            check("restore on the DUT", False, f"{type(e).__name__}: {str(e)[:80]} - restore by hand")
        sh(f"sudo -n nmcli con down {args.dongle_con}")
        sh(f"sudo -n nmcli con up {args.bench_con}", timeout=60)
        if was_on_radio and was_on_radio != args.dongle_con:
            sh(f"sudo -n nmcli con up '{was_on_radio}'", timeout=60)
        back, took = find_dut(bench, device_id, deadline_s=300)
        check("DUT back on the bench hotspot", back is not None,
              f"{back} after {took:.0f} s" if back else "not back in 300 s")

    if fails:
        print("HA LTE PUSH FAIL:", ", ".join(fails))
        sys.exit(1)
    print("HA LTE PUSH PASS")


if __name__ == "__main__":
    main()
