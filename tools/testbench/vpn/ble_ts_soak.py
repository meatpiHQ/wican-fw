#!/usr/bin/env python3
"""Bluetooth Lean + Tailscale + the webhook poster, 30 minutes: the lowest
internal-RAM point (the acceptance of internal/TASK_ble_tailscale_margin.md
section 3, built 2026-11-01 after levers 2 and 3), on the bench rig.

Phases: stage the DUT (Bluetooth on, Lean, passkey 421337; sta_ble_handover
off so the station and the tunnel stay up beside the link; the HA webhook
poster at betty's receiver through the tailnet's subnet route), Tailscale up
on the public rig (ts_public_soak.sh up: it reboots the DUT once more), the
simulator's central connected and writing to the DUT's data pipe in 60 s
legs for the whole soak, a sample every 10 s, then the checks, the teardown
(down) and the found documents back (every settings document compared with
a snapshot taken by the caller: --snapshot DIR of <component>.json files).

Prints BLE TS SOAK PASS. Runs on the PC: the DUT through rpi001 (the bench
AP's management path), the simulator on its USB-NCM link (sim_net), betty
over ssh. Rig: betty provisioned for ts_public_soak.sh, the simulator's
ble_central_bench component, the DUT in station mode on the bench AP.

  python tools/testbench/vpn/ble_ts_soak.py --snapshot DIR --out DIR [--minutes 30]
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools", "testbench", "lib"))
import sim_net  # noqa: E402

DUT_IP = os.environ.get("DUT_IP", "10.42.1.194")
PI = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "rpi001"]
BETTY = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "betty"]
GITBASH = [p for p in (r"C:\Program Files\Git\bin\bash.exe",
                       os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"))
           if os.path.exists(p)]
SOAK_SH = os.path.join(HERE, "ts_public_soak.sh").replace("\\", "/")
WEBHOOK_URL = "http://10.99.0.1:8199/webhook"   # betty's receiver through the subnet route
PASSKEY = 421337
# E lines of these tags fail the soak; the HTTP client's connect triplet
# (esp-tls / transport_base / HTTP_CLIENT) of a destination unreachable at
# boot is pre-existing logging, reported as a WARN
OWN_TAGS = ("ble_manager", "ble_http", "NimBLE", "BT_", "BLE_", "nimble", "flash_jobs",
            "vpn_manager", "microlink", "ml_", "lwip_guard", "wireguard", "ip4_routes",
            "ha_webhooks", "webhook")
fails, warns = [], []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        fails.append(name)


def warn(name, ok, detail=""):
    print(("PASS" if ok else "WARN") + ": " + name + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        warns.append(name)


def dut(path, method="GET", body=None, timeout=10):
    """The DUT through the Pi (the bench AP's management path)."""
    cmd = f"curl -s -m {timeout} -X {method} -H 'Content-Type: application/json' "
    if body is not None:
        cmd += "-d @- "
    cmd += f"http://{DUT_IP}{path}"
    r = subprocess.run(PI + [cmd], input=json.dumps(body) if body is not None else None,
                       capture_output=True, text=True, timeout=timeout + 15)
    t = r.stdout.strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except ValueError:
        return t


def dut_put(comp, doc):
    doc = {k: v for k, v in doc.items() if k not in ("degraded", "pending_reboot")}
    return dut(f"/api/settings/{comp}", "PUT", doc)


def wait_dut(boot_before, secs=150):
    t0 = time.time()
    while time.time() - t0 < secs:
        st = dut("/api/status", timeout=5)
        if isinstance(st, dict) and st.get("boot_count", -1) != boot_before \
                and st.get("bits", {}).get("sta_connected"):
            return st
        time.sleep(3)
    return None


def sim_api(path, method="GET", body=None, timeout=8):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://" + sim_net.SIM_HOST + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode(errors="replace")
            return r.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


def sim_status():
    st, b = sim_api("/api/ble_bench")
    return b if st == 200 and isinstance(b, dict) else {}


def sim_set(**fields):
    st, cfg = sim_api("/api/settings/ble_central_bench")
    if st != 200:
        return False, f"settings GET {st}"
    for k in ("degraded", "pending_reboot"):
        cfg.pop(k, None)
    cfg.update(fields)
    st, b = sim_api("/api/settings/ble_central_bench", "PUT", cfg)
    if st != 200:
        return False, f"PUT {st} {b}"
    st, sub = sim_api("/api/settings/submit", "POST", {})
    if not (isinstance(sub, dict) and sub.get("reboot")):
        return True, "no change"
    time.sleep(12)
    t0 = time.time()
    while time.time() - t0 < 120:
        st, info = sim_api("/api/info", timeout=3)
        if st == 200:
            time.sleep(3)
            return True, f"rebooted ({int(time.time() - t0)} s)"
        time.sleep(2)
    return False, "the simulator did not come back"


def betty_records():
    r = subprocess.run(BETTY + ["curl -s -m 5 'http://127.0.0.1:8199/__dump?since=0'"],
                       capture_output=True, text=True, timeout=30)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def ring_errors():
    r = subprocess.run(PI + [f"curl -s -m 20 http://{DUT_IP}/api/logs/ring"],
                       capture_output=True, text=True, timeout=40, errors="replace")
    lines = r.stdout.splitlines()
    errs = [l for l in lines if "E (" in l]
    own = [l for l in errs if any(t in l for t in OWN_TAGS)]
    guard = [l for l in lines if "lwip_guard" in l]
    return errs, own, guard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--snapshot", required=True, help="a dir of <component>.json settings documents to restore to")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not GITBASH:
        print("Git Bash not found (ts_public_soak.sh needs it)")
        sys.exit(2)
    os.makedirs(a.out, exist_ok=True)
    sim_net.install()
    print(f"soak: {a.minutes} min, DUT {DUT_IP} via rpi001, simulator {sim_net.SIM_HOST}, out {a.out}", flush=True)

    # ---- phase 0: the found state
    st = dut("/api/status")
    check("p0_dut_reachable", isinstance(st, dict), str(st)[:80])
    if not isinstance(st, dict):
        sys.exit(1)
    boot0 = st["boot_count"]
    found = {c: dut(f"/api/settings/{c}") for c in ("ble_manager", "interface_manager", "ha_webhooks")}
    json.dump(found, open(os.path.join(a.out, "found_docs.json"), "w"), indent=1)
    sim_found = sim_api("/api/settings/ble_central_bench")[1]
    json.dump(sim_found, open(os.path.join(a.out, "sim_found.json"), "w"), indent=1)
    print(f"  found: BLE {'on' if found['ble_manager'].get('enabled') else 'off'} "
          f"{found['ble_manager'].get('ble_ram_profile')}, handover {found['interface_manager'].get('sta_ble_handover')}, "
          f"webhook {'on' if found['ha_webhooks'].get('enabled') else 'off'}, boot {boot0}, "
          f"free {st['memory']['internal']['free']} B, unexpected {st.get('unexpected_resets')}", flush=True)

    rows = []
    link_drops = 0
    try:
        # ---- phase 1: stage the DUT
        b = dict(found["ble_manager"]); b.update({"enabled": True, "passkey": PASSKEY, "ble_ram_profile": "lean",
                                                 "phy": "1m", "advertising": "legacy", "bonding": True})
        i = dict(found["interface_manager"]); i["sta_ble_handover"] = False
        h = dict(found["ha_webhooks"]); h.update({"enabled": True, "url": WEBHOOK_URL, "interval_s": 15})
        check("p1_put_ble", dut_put("ble_manager", b) is not None)
        check("p1_put_ifm", dut_put("interface_manager", i) is not None)
        check("p1_put_webhook", dut_put("ha_webhooks", h) is not None)
        print("  submit (reboot) ...", flush=True)
        dut("/api/settings/submit", "POST")
        time.sleep(15)
        st = wait_dut(boot0)
        check("p1_dut_back_ble_on", st is not None and st["bits"].get("ble_enabled") is True,
              f"boot {st and st.get('boot_count')} free {st and st['memory']['internal']['free']} B")
        if st is None:
            raise RuntimeError("the DUT did not come back after the staging reboot")
        free_ble_on = st["memory"]["internal"]["free"]

        # ---- phase 2: Tailscale up on the public rig (it reboots the DUT once
        # for the VPN settings: the soak's boot baseline is taken after it)
        print("  ts_public_soak.sh up ...", flush=True)
        env = dict(os.environ, DUT_IP=DUT_IP)
        r = subprocess.run([GITBASH[0], SOAK_SH, "up"], env=env, capture_output=True, text=True, timeout=900)
        open(os.path.join(a.out, "ts_up.log"), "w").write(r.stdout + r.stderr)
        up_fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL:")]
        for l in r.stdout.splitlines():
            if l.startswith(("PASS:", "FAIL:")):
                print("    up: " + l[:150], flush=True)
        check("p2_ts_up_legs", r.returncode == 0 and not up_fails, f"rc {r.returncode}, {len(up_fails)} FAIL lines")
        v = dut("/api/vpn")
        check("p2_vpn_connected", isinstance(v, dict) and v.get("state") == "connected",
              f"state {v and v.get('state')} derp {v and v.get('derp')} routes {v and v.get('routes')} reason {v and v.get('reason')}")
        st = dut("/api/status")
        boot1 = st["boot_count"]
        print(f"  with Tailscale up (boot {boot1}): free {st['memory']['internal']['free']} B, min_free since boot "
              f"{st['memory']['internal']['min_free']} B (was {free_ble_on} B before the join)", flush=True)

        # ---- phase 3: the simulator's central
        ok, why = sim_set(enabled=True, phy="1m", passkey=PASSKEY)
        check("p3_sim_configured", ok, why)
        s = sim_status()
        if s.get("state") != "idle":
            sim_api("/api/ble_bench", "POST", {"action": "disconnect"})
            time.sleep(5)
        sim_api("/api/ble_bench", "POST", {"action": "connect"})
        t0 = time.time()
        while time.time() - t0 < 60 and sim_status().get("state") not in ("connected", "idle"):
            time.sleep(1)
        s = sim_status()
        peer = s.get("peer", {})
        check("p3_central_connected_secured", s.get("state") == "connected" and peer.get("secured"),
              f"state {s.get('state')} peer {peer.get('name')} mtu {peer.get('mtu')} itvl {peer.get('itvl_ms')} "
              f"phy {peer.get('phy_tx')}/{peer.get('phy_rx')} rssi {peer.get('rssi')}")
        if s.get("state") != "connected":
            raise RuntimeError("the central did not connect")
        wh0 = dut("/api/webhook") or {}
        rec0 = betty_records()
        print(f"  webhook at start: ok {wh0.get('success_count')} fail {wh0.get('fail_count')}; "
              f"betty receiver records {len(rec0) if rec0 is not None else '?'}", flush=True)

        # ---- phase 4: the soak
        print(f"  soak {a.minutes} min: the central writes in 60 s legs, a sample every 10 s", flush=True)
        start = time.time()
        end = start + a.minutes * 60
        leg_t0 = 0
        leg_n = 0
        while time.time() < end:
            s = sim_status()
            if s.get("state") == "idle":
                link_drops += 1
                print(f"  !! the central is idle (link drop {link_drops}); reconnecting", flush=True)
                sim_api("/api/ble_bench", "POST", {"action": "connect"})
                time.sleep(8)
                s = sim_status()
            if s.get("state") == "connected" and not s.get("last", {}).get("running") and time.time() - leg_t0 > 62:
                sim_api("/api/ble_bench", "POST", {"action": "run", "mode": "write", "seconds": 60, "size": 1})
                leg_t0 = time.time()
                leg_n += 1
            st = dut("/api/status", timeout=5) or {}
            v = dut("/api/vpn", timeout=5) or {}
            wh = dut("/api/webhook", timeout=5) or {}
            m = st.get("memory", {}).get("internal", {})
            row = {"t": round(time.time() - start, 1), "free": m.get("free"), "min_free": m.get("min_free"),
                   "largest": m.get("largest_block"), "ble_connected": st.get("bits", {}).get("ble_connected"),
                   "vpn": v.get("state"), "derp": v.get("derp"), "peers": v.get("ts_peers"),
                   "wh_ok": wh.get("success_count"), "wh_fail": wh.get("fail_count"),
                   "errors": st.get("health", {}).get("log_errors"), "unexpected": st.get("unexpected_resets"),
                   "boot": st.get("boot_count"), "central": s.get("state"), "leg": leg_n,
                   "last_kbps": s.get("last", {}).get("kbps")}
            rows.append(row)
            if len(rows) % 6 == 1:
                print(f"  {row['t']:6.0f} s  free {row['free']} min {row['min_free']} largest {row['largest']}  "
                      f"ble {row['ble_connected']} central {row['central']} leg {leg_n} ({row['last_kbps']} kbps)  "
                      f"vpn {row['vpn']} derp {row['derp']} peers {row['peers']}  wh {row['wh_ok']}/{row['wh_fail']}  "
                      f"E {row['errors']} resets {row['unexpected']}", flush=True)
            time.sleep(10)
        with open(os.path.join(a.out, "samples.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

        # ---- phase 5: the checks
        frees = [r["free"] for r in rows if isinstance(r.get("free"), int)]
        mins = [r["min_free"] for r in rows if isinstance(r.get("min_free"), int)]
        print(f"  samples {len(rows)}: free min {min(frees)} / median {sorted(frees)[len(frees)//2]} / max {max(frees)} B; "
              f"min_free since boot at the end {mins[-1] if mins else '?'} B; write legs {leg_n}", flush=True)
        check("p5_lowest_point_at_least_25_kb_since_boot", bool(mins) and min(mins) >= 25 * 1024,
              f"min_free since the boot that joined {min(mins) if mins else '?'} B (the proposal of section 3)")
        check("p5_sampled_free_at_least_25_kb", bool(frees) and min(frees) >= 25 * 1024, f"lowest sample {min(frees) if frees else '?'} B")
        check("p5_no_unexpected_reset", rows[-1].get("unexpected") == 0, f"unexpected_resets {rows[-1].get('unexpected')}")
        check("p5_no_reboot_during_the_soak", all(r.get("boot") == boot1 for r in rows if r.get("boot") is not None),
              f"boot {rows[-1].get('boot')} vs {boot1} at the start")
        check("p5_no_ble_link_drop", link_drops == 0 and all(r.get("ble_connected") for r in rows[2:]),
              f"{link_drops} reconnects, ble_connected false in {sum(1 for r in rows[2:] if not r.get('ble_connected'))} samples")
        check("p5_relay_up_at_the_end", rows[-1].get("derp") is True and rows[-1].get("vpn") == "connected",
              f"vpn {rows[-1].get('vpn')} derp {rows[-1].get('derp')}")
        check("p5_vpn_connected_throughout", all(r.get("vpn") == "connected" for r in rows[1:]),
              f"{sum(1 for r in rows[1:] if r.get('vpn') != 'connected')} samples not connected")
        wh = dut("/api/webhook") or {}
        grew = (wh.get("success_count") or 0) - (wh0.get("success_count") or 0)
        check("p5_webhook_posts_grew", grew >= a.minutes * 2, f"success {wh0.get('success_count')} -> {wh.get('success_count')} (+{grew}), "
              f"fail {wh0.get('fail_count')} -> {wh.get('fail_count')}")
        check("p5_no_webhook_failure_during_the_soak", (wh.get("fail_count") or 0) == (wh0.get("fail_count") or 0),
              f"fail {wh0.get('fail_count')} -> {wh.get('fail_count')} (the failures before the start are the posts before the route existed)")
        rec = betty_records()
        warn("p5_betty_received_the_posts", rec is not None and rec0 is not None and len(rec) - len(rec0) >= a.minutes,
             f"receiver records {len(rec0) if rec0 is not None else '?'} -> {len(rec) if rec is not None else '?'}")
        errs, own, guard = ring_errors()
        check("p5_no_E_line_of_the_feature_tags", not own, f"{len(errs)} E lines, {len(own)} own" + (f": {own[0][:120]}" if own else ""))
        warn("p5_other_E_lines", not errs, f"{len(errs)} E line(s) of other tags (health.log_errors {rows[-1].get('errors')}: "
             f"the HTTP client's connect triplet of a post before the route existed)" + (f": {errs[0][:100]}" if errs else ""))
        check("p5_no_lwip_guard_refusal", not guard, f"{len(guard)} lwip_guard lines" + (f": {guard[0][:120]}" if guard else ""))
        s = sim_status()
        c = s.get("counters", {})
        check("p5_no_pairing_failure", c.get("pair_fail", 0) == 0, f"pair ok/fail {c.get('pair_ok')}/{c.get('pair_fail')}")
    finally:
        # ---- phase 6: teardown and the found documents back
        print("  teardown: central disconnect, ts down, the found documents", flush=True)
        try:
            sim_api("/api/ble_bench", "POST", {"action": "disconnect"})
            time.sleep(3)
            sim_set(enabled=bool(sim_found.get("enabled")) if isinstance(sim_found, dict) else False)
        except Exception as e:
            print("  sim restore:", e)
        env = dict(os.environ, DUT_IP=DUT_IP)
        r = subprocess.run([GITBASH[0], SOAK_SH, "down"], env=env, capture_output=True, text=True, timeout=600)
        open(os.path.join(a.out, "ts_down.log"), "w").write(r.stdout + r.stderr)
        print("  down rc", r.returncode, flush=True)
        boot = (dut("/api/status") or {}).get("boot_count", -1)
        for c, d in found.items():
            if isinstance(d, dict):
                dut_put(c, d)
        dut("/api/settings/submit", "POST")
        time.sleep(15)
        st = wait_dut(boot)
        diffs = {}
        names = [f[:-5] for f in os.listdir(a.snapshot) if f.endswith(".json") and not f.startswith("_")]
        for n in names:
            cur = dut(f"/api/settings/{n}")
            ref = json.load(open(os.path.join(a.snapshot, n + ".json")))
            if not isinstance(cur, dict):
                diffs[n] = "no answer"; continue
            d = {k: (ref.get(k), cur.get(k)) for k in set(cur) | set(ref) if cur.get(k) != ref.get(k)}
            if d:
                diffs[n] = d
        check("p6_found_documents_back", st is not None and not diffs, f"{len(names)} compared; differences {diffs if diffs else 'none'}")
        if st:
            print(f"  DUT back: boot {st.get('boot_count')} free {st['memory']['internal']['free']} B unexpected {st.get('unexpected_resets')} "
                  f"bits {[k for k, v in st['bits'].items() if v]}", flush=True)
    if fails:
        print("BLE TS SOAK FAIL:", ", ".join(fails))
        sys.exit(1)
    print("BLE TS SOAK PASS" + (f" (warns: {', '.join(warns)})" if warns else ""))


if __name__ == "__main__":
    main()
