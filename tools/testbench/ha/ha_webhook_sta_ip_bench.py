#!/usr/bin/env python3
"""The HA webhook push carries the station address (issue #977).

The ha-wican integration's station IP diagnostic sensor (and the device's
configuration_url in its device card) read `status.sta_ip` from the push.
The legacy firmware's `/check_status` carried that key for the polling
integration, but neither the legacy webhook nor v6's push ever sent it, so
on a push-based install the sensor stays empty for good. This bench holds
the contract: the first push after a registration (the resync, a full
snapshot) carries `status.sta_ip` equal to the station's address.

Steps / checks:
  1. /api/info (device_id), /api/wifi/status (the station address),
     /api/settings/ha_webhooks (data_mode, interval_s) and GET /api/webhook
     (saved for the restore)
  2. a receiver where BOTH the DUT and this PC reach it (the gate bench's
     options: --pi HOST | --serve PORT --local-ip IP | --receiver URL)
  3. POST /api/webhook {url, enabled, interval 5, manual_override false}
  4. the first push: status.sta_ip present, a dotted quad, == the station
     address of /api/wifi/status
  5. a later push: in `changed` mode an unchanged address is NOT repeated
     (status carries only the identity overlay), in `full` mode every push
     carries it
  6. /api/logs/ring: no E line from ha_webhooks during the run
  7. restore: the saved registration re-POSTed (url, urls, enabled,
     interval, manual_override) or DELETE when there was none; a changed
     `interval_s` goes back through settings PUT + submit (one planned
     reboot)

  python ha_webhook_sta_ip_bench.py --dut localhost:18081 --pi rpi001 --pi-ip 10.42.1.1
  python ha_webhook_sta_ip_bench.py --dut 10.42.1.194 --serve 8199 --local-ip 10.42.1.1

Verdict line: HA WEBHOOK STA IP PASS / FAIL.
"""
import argparse
import ipaddress
import sys
import time

import ha_webhook_gate_bench as gate

check = gate.check


def dotted_quad(s):
    try:
        return isinstance(s, str) and isinstance(ipaddress.ip_address(s), ipaddress.IPv4Address)
    except ValueError:
        return False


def ring_errors(tag):
    """E lines of one component in the device's log ring (the ring survives
    warm resets; a fresh boot starts it empty)."""
    try:
        code, ring = gate.api("/api/logs/ring", timeout=8, retries=2)
    except Exception:
        return None
    if code != 200:
        return None
    lines = ring.get("lines") if isinstance(ring, dict) else None
    if lines is None:
        lines = ring if isinstance(ring, list) else str(ring).splitlines()
    out = []
    for ln in lines:
        text = ln if isinstance(ln, str) else ln.get("text") or ln.get("line") or str(ln)
        if "E (" in text and tag + ":" in text:
            out.append(text.strip())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dut", required=True,
                    help="DUT address(es) as reached from here, comma-separated candidates "
                         "(e.g. localhost:18081 through an ssh tunnel on the Pi)")
    ap.add_argument("--serve", type=int, help="run the receiver here on this port")
    ap.add_argument("--local-ip", help="this PC's address as the DUT sees it (with --serve)")
    ap.add_argument("--pi", help="run the receiver on this ssh host")
    ap.add_argument("--pi-ip", default="10.42.1.1", help="the Pi's address as the DUT sees it")
    ap.add_argument("--port", type=int, default=8199, help="receiver port with --pi")
    ap.add_argument("--receiver", help="base URL of an already running receiver")
    ap.add_argument("--observe", type=int, default=45, help="seconds to wait for two pushes")
    args = ap.parse_args()
    if args.serve and not args.local_ip:
        ap.error("--serve needs --local-ip")
    gate.DUTS = [d.strip() for d in args.dut.split(",") if d.strip()]
    gate.DUT = gate.DUTS[0]

    code, info = gate.api("/api/info")
    check("[RIG] DUT answers /api/info", code == 200, str(info)[:80])
    if code != 200:
        print("HA WEBHOOK STA IP FAIL: rig")
        sys.exit(1)
    device_id = info.get("device_id")
    print(f"DUT {device_id} fw {info.get('fw_version')}")

    code, wifi = gate.api("/api/wifi/status")
    sta_ip = wifi.get("ip") if code == 200 and isinstance(wifi, dict) else None
    check("[RIG] the station holds an address (/api/wifi/status ip)",
          bool(wifi.get("sta_connected")) and dotted_quad(sta_ip), str(sta_ip))
    if not dotted_quad(sta_ip):
        print("HA WEBHOOK STA IP FAIL: rig")
        sys.exit(1)

    code, hw_before = gate.api("/api/settings/ha_webhooks")
    data_mode = hw_before.get("data_mode", "changed") if code == 200 else "changed"
    interval_before = hw_before.get("interval_s") if code == 200 else None
    code, reg_before = gate.api("/api/webhook")
    had_url = code == 200 and bool(reg_before.get("url"))
    print(f"data_mode {data_mode}, interval_s {interval_before}, "
          f"registered before: {reg_before.get('url') or '(none)'}")
    errors_before = ring_errors("ha_webhooks") or []

    base, dump = gate.start_receiver(args)
    print("receiver:", base, "dump:", dump)
    try:
        # 3) register
        n0 = len(gate.received(dump))
        url = base + "/hook"
        code, resp = gate.api("/api/webhook", "POST",
                              {"url": url, "enabled": True, "interval": 5,
                               "manual_override": False})
        check("POST /api/webhook accepted (201/200)", code in (200, 201), f"code={code}")

        # 4 + 5) observe two pushes
        got = []
        t0 = time.time()
        while time.time() - t0 < args.observe:
            time.sleep(3)
            got = gate.received(dump)[n0:]
            if len(got) >= 2:
                break
        code, wh = gate.api("/api/webhook")
        check("two pushes arrived", len(got) >= 2,
              f"{len(got)} posts in {time.time() - t0:.0f} s; device stats status={wh.get('status')} "
              f"success={wh.get('success_count')} fail={wh.get('fail_count')} "
              f"last_error={wh.get('last_error')!r}")
        if got:
            st = got[0].get("status") or {}
            check("first push (the resync) carries status.device_id == /api/info",
                  st.get("device_id") == device_id, str(st.get("device_id")))
            check("first push carries status.sta_ip", "sta_ip" in st,
                  "keys: " + ",".join(sorted(st.keys())))
            check("status.sta_ip is a dotted quad", dotted_quad(st.get("sta_ip")),
                  repr(st.get("sta_ip")))
            check("status.sta_ip == the station address of /api/wifi/status",
                  st.get("sta_ip") == sta_ip, f"{st.get('sta_ip')!r} vs {sta_ip!r}")
        if len(got) >= 2:
            st2 = got[1].get("status") or {}
            if data_mode == "changed":
                check("second push (changed mode) does not repeat an unchanged sta_ip",
                      "sta_ip" not in st2, "keys: " + ",".join(sorted(st2.keys())))
            else:
                check("second push (full mode) carries sta_ip again",
                      st2.get("sta_ip") == sta_ip, repr(st2.get("sta_ip")))
        check("GET /api/webhook status=ok + success_count>=1",
              wh.get("status") == "ok" and wh.get("success_count", 0) >= 1,
              f"status={wh.get('status')} n={wh.get('success_count')}")

        # 6) the log ring: nothing at ERROR from the poster
        errors_after = ring_errors("ha_webhooks")
        if errors_after is None:
            print("INFO: /api/logs/ring not readable, E-line check skipped")
        else:
            new_errors = [e for e in errors_after if e not in errors_before]
            check("no ha_webhooks E line during the run", not new_errors,
                  "; ".join(new_errors)[:200])
    except Exception as e:  # noqa: BLE001 - the verdict line must still print
        check(f"bench step raised {type(e).__name__}", False, str(e)[:120])
    finally:
        # 7) restore what was found: the registration (live + persisted by the
        # POST) or a DELETE, then the interval the registration overwrote
        try:
            if had_url:
                body = {"url": reg_before["url"], "enabled": bool(reg_before.get("enabled")),
                        "interval": int(reg_before.get("interval") or 60),
                        "manual_override": bool(reg_before.get("manual_override"))}
                if reg_before.get("urls"):
                    body["urls"] = reg_before["urls"]
                code, _ = gate.api("/api/webhook", "POST", body)
                check("registration found before put back (POST 200)", code == 200,
                      f"code={code} url={body['url']}")
            else:
                code, _ = gate.api("/api/webhook", "DELETE")
                check("DELETE /api/webhook 204 (none was registered before)", code == 204,
                      f"code={code}")
            code, hw = gate.api("/api/settings/ha_webhooks")
            if interval_before and code == 200 and hw.get("interval_s") != interval_before:
                doc = {k: v for k, v in hw.items() if k not in ("degraded", "pending_reboot")}
                doc["interval_s"] = interval_before
                code, _ = gate.api("/api/settings/ha_webhooks", "PUT", doc)
                check(f"ha_webhooks.interval_s staged back to {interval_before}",
                      code == 200, f"code={code}")
                if code == 200:
                    gate.api("/api/settings/submit", "POST", {})
                    time.sleep(8)
                    up = gate.wait_up()
                    check("interval restore applied (submit + one planned reboot)",
                          up is not None, f"back after {up:.0f} s" if up else "did not come back")
        except Exception as e:  # noqa: BLE001
            check("restore (registration / interval back)", False,
                  f"{type(e).__name__}: {str(e)[:100]} - restore by hand")
        gate.stop_receiver(args)

    if gate.fails:
        print("HA WEBHOOK STA IP FAIL:", ", ".join(gate.fails))
        sys.exit(1)
    print("HA WEBHOOK STA IP PASS")


if __name__ == "__main__":
    main()
