#!/usr/bin/env python3
"""The networks a sender is active on, on the real device (2026-11-08, Ali:
"let the user select on which WiFi SSID the destination is active; the same
for the HA webhook").

Both senders carry a `networks` setting ("" = every network, else station
network names separated by commas); the poster sends only while the station
is on one of them (wifi_manager_sta_on_network). This bench puts the device
through the four states against the Pi's receiver (actors/dd_receiver.py on
rpi001, port 8199, reached from here over ssh):

  dest-other    one HTTP destination to the receiver, active on a network the
                station is not on: GET /api/destinations says on_network false
                and counts skipped_network, nothing arrives, the ring has the
                "not on a selected network" line, the status says which
                network the station IS on (ssid)
  dest-this     the same row active on the station's own network: deliveries
                arrive, on_network true, skipped_network stops
  hook-other    the Home Assistant webhook to the receiver, active on another
                network (manual_override on, so the bench HA's registration
                cannot move the URL): GET /api/webhook on_network false, no
                push, the ring's "pushes wait" line
  hook-this     active on the station's network: pushes arrive, on_network
                true

Every state is a settings PUT + submit (reboot-to-apply): five planned
restarts with the restore. The destination and webhook settings go back as
found at the end (the restore runs in the finally). Verdict line:
DEST NETWORKS PASS / FAIL.

python tools/testbench/system/dest_networks_bench.py --dut localhost:18081 --pi rpi001 --pi-ip 10.42.1.1 [--observe 25]
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

A = None
FAILS = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)
    return ok


def note(msg):
    print("  .. " + msg, flush=True)


def api(path, method="GET", body=None, timeout=10, retries=6):
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for _ in range(retries):
        req = urllib.request.Request(f"http://{A.dut}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                t = r.read().decode()
                return r.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
        except urllib.error.HTTPError as e:
            t = e.read().decode()
            return e.code, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(2)
    raise last


def pi(cmd, timeout=60):
    for _ in range(3):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", A.pi, cmd],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 255:
            return r.returncode, (r.stdout + r.stderr).strip()
        time.sleep(2)
    return 255, (r.stdout + r.stderr).strip()


def wait_up(deadline_s=180):
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        try:
            code, _ = api("/api/info", timeout=4, retries=1)
            if code == 200:
                return time.time() - t0
        except Exception:
            pass
        time.sleep(3)
    return None


def clean(d):
    d = dict(d.get("values", d)) if isinstance(d, dict) else d
    for k in ("degraded", "pending_reboot"):
        d.pop(k, None)
    return d


def put_and_restart(label, comp, doc):
    code, r = api(f"/api/settings/{comp}", "PUT", doc)
    check(f"{label}: PUT {comp} accepted ({code})", code == 200, str(r)[:120])
    code, r = api("/api/settings/submit", "POST", {})
    note(f"{label}: submit {code} {str(r)[:60]}")
    time.sleep(8)
    t = wait_up()
    check(f"{label}: the device is back after the restart", t is not None, f"{t:.0f} s" if t else "")
    time.sleep(3)


# ---- the receiver on the Pi ----------------------------------------------------
def dump(since=0):
    rc, out = pi(f"curl -s -m 6 'http://127.0.0.1:8199/__dump?since={since}'")
    if rc != 0 or not out.strip().startswith(("{", "[")):
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def records(d):
    if d is None:
        return []
    return d.get("records", d) if isinstance(d, dict) else d


def receiver_start():
    if dump(0) is not None:
        note("a receiver already answers on the Pi's 8199: using it")
        return True, False
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "..", "actors", "dd_receiver.py")
    subprocess.run(["scp", "-q", src, f"{A.pi}:/tmp/dd_receiver_nets.py"], check=True, timeout=30)
    pi("sudo -n systemctl stop dd_receiver_nets 2>/dev/null; "
       "sudo -n systemd-run --unit=dd_receiver_nets --collect -p User=meatpi -p WorkingDirectory=/home/meatpi "
       "python3 -u /tmp/dd_receiver_nets.py --no-tls")
    for _ in range(10):
        if dump(0) is not None:
            return True, True
        time.sleep(1)
    rc, out = pi("systemctl is-active dd_receiver_nets; sudo -n journalctl -u dd_receiver_nets --no-pager -n 3")
    note("receiver not reachable: " + " | ".join(out.splitlines())[:300])
    return False, True


def receiver_stop():
    pi("sudo -n systemctl stop dd_receiver_nets 2>/dev/null; true")


def paths_since(since, path):
    return [r for r in records(dump(since)) if r.get("path") == path]


def ring():
    try:
        code, t = api("/api/logs/ring", timeout=8, retries=2)
        return t if isinstance(t, str) else ""
    except Exception:
        return ""


def e_lines(text):
    return [l.strip() for l in text.split("\n") if l.strip().startswith("E (")]


# ---- the legs ---------------------------------------------------------------------
def dest_row(networks):
    return {"name": "nets", "type": "http", "enabled": True, "url": f"http://{A.pi_ip}:8199/nets", "period_s": 2,
            "auth": "none", "auth_token": "", "auth_name": "", "basic_username": "", "basic_password": "",
            "api_key": "", "query": "", "cert_set": "", "car_model": "", "retain": True, "full_first": False,
            "networks": networks}


def leg_dest(label, networks, expect_on, ssid):
    doc = {"enabled": True, "cli": True, "destinations": [dest_row(networks)]}
    put_and_restart(label, "data_destinations", doc)
    n0 = len(records(dump(0)))
    time.sleep(A.observe)
    code, st = api("/api/destinations")
    row = next((d for d in (st.get("destinations") or []) if d.get("name") == "nets"), None) if code == 200 else None
    check(f"{label}: the row is applied and reports its networks", row is not None and row.get("networks") == networks, str(row)[:200])
    check(f"{label}: the status names the station's network ({ssid})", code == 200 and st.get("ssid") == ssid, str(st.get("ssid")))
    got = paths_since(n0, "/nets")
    if expect_on:
        check(f"{label}: on_network true", row is not None and row.get("on_network") is True)
        check(f"{label}: deliveries arrive at the receiver ({len(got)} in {A.observe} s)", len(got) >= 3)
        check(f"{label}: success counted, no network skips after the restart", row is not None and row.get("success", 0) >= 3 and row.get("skipped_network", 0) == 0, f"success={row and row.get('success')} skipped_network={row and row.get('skipped_network')}")
    else:
        check(f"{label}: on_network false", row is not None and row.get("on_network") is False)
        check(f"{label}: nothing arrives at the receiver ({len(got)} in {A.observe} s)", len(got) == 0)
        check(f"{label}: the laps are counted as network skips, never failures", row is not None and row.get("skipped_network", 0) >= 3 and row.get("fail", 0) == 0 and row.get("success", 0) == 0, f"skipped_network={row and row.get('skipped_network')} fail={row and row.get('fail')}")
        check(f"{label}: the ring says why, once", "not on a selected network" in ring())
    code, t = api("/api/destinations/test", "POST", {"name": "nets"})
    if expect_on:
        check(f"{label}: Test delivers", code == 200 and isinstance(t, dict) and t.get("ok") is True, str(t)[:120])
    else:
        check(f"{label}: Test answers 'not on a selected network'", code == 200 and isinstance(t, dict) and t.get("ok") is False and "selected network" in str(t.get("error")), str(t)[:120])


def leg_hook(label, networks, expect_on, base_doc):
    doc = dict(base_doc)
    doc.update({"enabled": True, "url": f"http://{A.pi_ip}:8199/hook", "url2": "", "interval_s": 2,
                "manual_override": True, "networks": networks})
    put_and_restart(label, "ha_webhooks", doc)
    n0 = len(records(dump(0)))
    time.sleep(A.observe)
    code, wh = api("/api/webhook")
    check(f"{label}: GET /api/webhook carries networks", code == 200 and wh.get("networks") == networks, str(wh.get("networks")))
    got = paths_since(n0, "/hook")
    if expect_on:
        check(f"{label}: on_network true", wh.get("on_network") is True)
        check(f"{label}: pushes arrive at the receiver ({len(got)} in {A.observe} s)", len(got) >= 2)
        check(f"{label}: success counted", wh.get("success_count", 0) >= 2, str(wh.get("success_count")))
    else:
        check(f"{label}: on_network false", wh.get("on_network") is False)
        check(f"{label}: no push arrives ({len(got)} in {A.observe} s)", len(got) == 0)
        check(f"{label}: nothing counted as a failure", wh.get("fail_count", 0) == 0 and wh.get("success_count", 0) == 0, f"fail={wh.get('fail_count')} ok={wh.get('success_count')}")
        check(f"{label}: the ring says the pushes wait", "not on a selected network: pushes wait" in ring())


def main():
    global A
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dut", required=True, help="the DUT (through the tunnel: localhost:18081)")
    ap.add_argument("--pi", default="rpi001", help="ssh host running the receiver")
    ap.add_argument("--pi-ip", default="10.42.1.1", help="the Pi as the DUT sees it")
    ap.add_argument("--observe", type=int, default=25, help="seconds to watch each state")
    ap.add_argument("--ignore-tag", action="append", default=[], help="E-line tags to ignore (unrelated known faults)")
    A = ap.parse_args()

    t_up = wait_up(30)
    if t_up is None:
        print("DEST NETWORKS FAIL: the DUT does not answer")
        return 2
    code, info = api("/api/info")
    print("DUT:", info.get("device_id"), info.get("fw_build"), flush=True)
    code, ws = api("/api/wifi/status")
    ssid = ws.get("ssid") or (ws.get("sta_attempt") or {}).get("ssid") or ""
    check("preflight: the station is on a network the status names", bool(ssid) and ws.get("sta_connected") is True, ssid)
    if not ssid:
        print("DEST NETWORKS FAIL: no station network")
        return 2
    other = "NotThisNetwork" if ssid != "NotThisNetwork" else "SomeOtherNetwork"
    code, dd0 = api("/api/settings/data_destinations")
    code, hw0 = api("/api/settings/ha_webhooks")
    dd0, hw0 = clean(dd0), clean(hw0)
    note(f"as found: {len(dd0.get('destinations') or [])} destination(s), webhook enabled={hw0.get('enabled')} url={hw0.get('url') or '(none)'}")
    e0 = e_lines(ring())
    up, started = receiver_start()
    check("the receiver answers on the Pi", up)
    try:
        leg_dest("dest-other", other, False, ssid)
        leg_dest("dest-this", ssid, True, ssid)
        leg_hook("hook-other", other, False, hw0)
        leg_hook("hook-this", ssid, True, hw0)
        e1 = e_lines(ring())
        new = [l for l in e1 if l not in e0 and not any(t in l for t in A.ignore_tag)]
        check(f"no new E line in the ring ({len(new)})", not new, " | ".join(x[:100] for x in new[:4]))
    finally:
        print("restoring the destinations and the webhook as found", flush=True)
        try:
            code, r = api("/api/settings/data_destinations", "PUT", dd0)
            code2, r2 = api("/api/settings/ha_webhooks", "PUT", hw0)
            api("/api/settings/submit", "POST", {})
            time.sleep(8)
            t = wait_up()
            check("restore: both settings back as found and the device up", code == 200 and code2 == 200 and t is not None, f"{code} {code2}")
            code, dd1 = api("/api/settings/data_destinations")
            code, hw1 = api("/api/settings/ha_webhooks")
            check("restore: the stored documents match what was found", clean(dd1) == dd0 and clean(hw1) == hw0)
        except Exception as e:  # noqa: BLE001
            check("restore ran", False, str(e))
        if started:
            receiver_stop()
    print("DEST NETWORKS " + ("PASS" if not FAILS else "FAIL: " + "; ".join(FAILS)), flush=True)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
