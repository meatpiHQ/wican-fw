#!/usr/bin/env python3
"""BLE throughput with the ECU simulator as the central (ble_central_bench).

The simulator (WiCAN-PRO hardware, USB-NCM at 192.168.8.1) runs the
`ble_central_bench` component: a NimBLE central that connects to the DUT,
tunes the link like esp-idf's throughput_app (MTU 517, DLE 251, 7.5 ms,
PHY) and runs timed GATT tests. This PC script only drives it over HTTP
and reads the DUT over WiFi before/after the link (WiFi is handed over
while the central is connected).

Legs (per PHY in --phys, default 1m then 2m; each PHY = a simulator
settings change + reboot):
  connect          scan/connect/pair/discover; peer mtu/itvl/phy/dle
  notify  60 s     the DUT blasts its data pipe (POST /api/ble/blast via
                   the tunnel), the central counts -> kbps (ref ~340)
  write   60 s     write-without-response to FFF2 -> kbps (ref ~500)
  read    10 s     DIS 2A29 loop -> kbps (ref ~200 with 510 B; ours is 6 B)
  tunnel_up 64 KB + 512 KB, tunnel_down 64 KB + 512 KB (byte-exact, notify
  mode with credits, 0 holes) + one 512 KB download on indications
  disconnect
Verdict BLE THROUGHPUT PASS = at 1M: notify >= 300 kbps and write >= 400
kbps (the reference minus margin), every tunnel transfer ok + exact, 0
pairing failures; 2M reported. METRIC lines for the benchboard.

Usage: python tools/testbench/ble/ble_sim_central_bench.py [--sim 192.168.8.1]
           [--dut 10.42.1.194] [--phys 1m,2m] [--seconds 60] [--passkey 421337]
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

results = []
warns = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" + (f"  ({detail})" if detail else ""), flush=True)
    results.append((name, ok))


def warn(name, ok, detail=""):
    print(("PASS" if ok else "WARN") + f": {name}" + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        warns.append(name)


def metric(name, value, unit):
    print(f"METRIC {name}={value:.2f} {unit}", flush=True)


def api(base, path, method="GET", body=None, timeout=8):
    req = urllib.request.Request(base + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            blob = r.read()
            try:
                return r.status, json.loads(blob or b"{}")
            except ValueError:
                return r.status, blob.decode(errors="replace")
    except urllib.error.HTTPError as e:
        blob = e.read()
        try:
            return e.code, json.loads(blob or b"{}")
        except ValueError:
            return e.code, blob.decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def dut_api(dut, path, timeout=5, ap_fallback=True):
    """GET on the DUT: direct, else through the bench Pi (ssh + curl); the PC
    has no route into the Pi's hotspot subnet. ap_fallback=False skips the
    DUT's own AP: a station there STOPS the DUT's BLE stack, and the restart
    that follows is what the second-link failures correlate with."""
    st, b = api(dut, path, timeout=timeout)
    if st != 0:
        return st, b
    def via_pi(url):
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", DUT_VIA,
                              f"curl -s -m {timeout} -w '\\n%{{http_code}}' {url}{path}"],
                             capture_output=True, text=True, timeout=timeout + 15).stdout
        body, _, code = out.strip().rpartition("\n")
        return int(code or 0), json.loads(body or "{}")
    try:
        st, b = via_pi(dut)
        if st != 0 or not ap_fallback:
            return st, b
        # after a BLE session the DUT's STA often falls back to the dongle AP
        # (client-isolated): read it through its own AP, then leave (a
        # station there stops BLE)
        subprocess.run(["ssh", "-o", "BatchMode=yes", DUT_VIA,
                        "sudo nmcli con up wican-bench-ap >/dev/null 2>&1; sleep 6"],
                       capture_output=True, text=True, timeout=40)
        st, b = via_pi("http://192.168.0.10")
        subprocess.run(["ssh", "-o", "BatchMode=yes", DUT_VIA,
                        "sudo nmcli con down wican-bench-ap >/dev/null 2>&1; true"],
                       capture_output=True, text=True, timeout=30)
        return st, b
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


DUT_VIA = "rpi001"


class Sim:
    def __init__(self, base):
        self.base = base

    def status(self):
        st, b = api(self.base, "/api/ble_bench")
        return b if st == 200 and isinstance(b, dict) else {}

    def post(self, body):
        return api(self.base, "/api/ble_bench", "POST", body)

    def wait_state(self, states, timeout):
        t0 = time.time()
        st = {}
        while time.time() - t0 < timeout:
            st = self.status()
            if st.get("state") in states:
                return st
            time.sleep(0.5)
        return st

    def run(self, mode, seconds, size, timeout, out=None):
        st, b = self.post({"action": "run", "mode": mode, "seconds": seconds, "size": size, **({"out": out} if out else {})})
        if st != 200:
            return {"ok": False, "detail": f"run refused {st} {b}"}
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = self.status()
            last = s.get("last", {})
            if last.get("mode") == mode and not last.get("running") and s.get("state") != "running":
                return last
            time.sleep(1)
        return {"ok": False, "detail": "timeout waiting for the result"}


def set_sim(sim, base, **fields):
    """PUT ble_central_bench settings, submit (reboot when changed), wait."""
    st, cfg = api(base, "/api/settings/ble_central_bench")
    if st != 200:
        return False, f"settings GET {st}"
    for k in ("degraded", "pending_reboot"):
        cfg.pop(k, None)
    cfg.update(fields)
    st, b = api(base, "/api/settings/ble_central_bench", "PUT", cfg)
    if st != 200:
        return False, f"settings PUT {st} {b}"
    # the simulator's settings_manager (July snapshot) reports no
    # pending_reboot on GET; POST submit answers {"ok","reboot":bool}
    st, sub = api(base, "/api/settings/submit", "POST", {})
    if st != 200:
        return False, f"submit {st} {sub}"
    if not (isinstance(sub, dict) and sub.get("reboot")):
        return True, "no change"
    time.sleep(12)
    t0 = time.time()
    while time.time() - t0 < 120:
        st, info = api(base, "/api/info", timeout=3)
        if st == 200:
            time.sleep(3)
            return True, f"rebooted ({int(time.time() - t0)} s)"
        time.sleep(2)
    return False, "simulator did not come back"


def dut_set_phy(dut, phy):
    """PUT ble_manager.phy on the DUT through the Pi and submit (reboots when
    changed); returns (ok, detail). The DUT's `1m` pins the link to 1M, so
    the 2M leg needs `auto` (or `2m`) on the DUT."""
    script = (
        "import json,urllib.request,time,subprocess\n"
        f"B='{dut}'\n"
        "def req(p,m='GET',b=None):\n"
        "    r=urllib.request.Request(B+p,method=m,data=None if b is None else json.dumps(b).encode(),headers={'Content-Type':'application/json'})\n"
        "    x=urllib.request.urlopen(r,timeout=8); return json.loads(x.read() or b'{}')\n"
        "ap=False\n"
        "try:\n"
        "    boot=req('/api/status').get('boot_count')\n"
        "except Exception:\n"
        "    # off the hotspot (dongle-AP fallback after a reset): use the DUT's own AP\n"
        "    subprocess.run('sudo nmcli con up wican-bench-ap >/dev/null 2>&1; sleep 6',shell=True)\n"
        "    B='http://192.168.0.10'; ap=True\n"
        "    boot=req('/api/status').get('boot_count')\n"
        "d=req('/api/settings/ble_manager'); d.pop('degraded',None); d.pop('pending_reboot',None)\n"
        f"d['phy']='{phy}'\n"
        "req('/api/settings/ble_manager','PUT',d)\n"
        "pend=req('/api/settings/ble_manager').get('pending_reboot')\n"
        "req('/api/settings/submit','POST',{})\n"
        "if not pend:\n"
        "    if ap: subprocess.run('sudo nmcli con down wican-bench-ap >/dev/null 2>&1',shell=True)\n"
        "    print('OK no change'); raise SystemExit\n"
        "time.sleep(15); t0=time.time(); ok=False\n"
        "while time.time()-t0<200 and not ok:\n"
        "    if not ap and time.time()-t0>60:\n"
        "        # the STA did not rejoin the hotspot after the reboot: the DUT's own AP\n"
        "        subprocess.run('sudo nmcli con up wican-bench-ap >/dev/null 2>&1; sleep 6',shell=True)\n"
        "        B='http://192.168.0.10'; ap=True\n"
        "    elif ap: subprocess.run('sudo nmcli con up wican-bench-ap >/dev/null 2>&1',shell=True); time.sleep(3)\n"
        "    try:\n"
        "        if req('/api/status').get('boot_count')!=boot: ok=True\n"
        "    except Exception: pass\n"
        "    if not ok: time.sleep(3)\n"
        "if ap: subprocess.run('sudo nmcli con down wican-bench-ap >/dev/null 2>&1',shell=True)\n"
        "print('OK rebooted', int(time.time()-t0)) if ok else print('NOT BACK')\n")
    try:
        # the script goes over stdin: ssh + the remote shell mangle quoted -c code
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", DUT_VIA, "python3", "-"],
                           input=script, capture_output=True, text=True, timeout=260)
        out = (r.stdout.strip() or r.stderr.strip()[-160:])
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:80]
    return out.startswith("OK"), out or "no output"


def leg(sim, base, dut, phy, seconds, passkey):
    tag = f"phy{phy}"
    ok, why = set_sim(sim, base, enabled=True, phy=phy, passkey=passkey)
    check(f"{tag}_sim_configured", ok, why)
    if not ok:
        return {}
    st = sim.wait_state(("idle",), 20)
    check(f"{tag}_central_idle", st.get("state") == "idle", f"state {st.get('state')}")
    if st.get("state") != "idle":
        return {}

    s, b = sim.post({"action": "connect"})
    st = sim.wait_state(("connected", "idle"), 60)
    peer = st.get("peer", {})
    want = 2 if phy == "2m" else 1
    check(f"{tag}_connected_secured", st.get("state") == "connected" and peer.get("secured"),
          f"state {st.get('state')} peer {peer.get('name')} {peer.get('addr')} mtu {peer.get('mtu')} "
          f"itvl {peer.get('itvl_ms')} ms phy {peer.get('phy_tx')}/{peer.get('phy_rx')} "
          f"dle {peer.get('dle_tx')}/{peer.get('dle_rx')} rssi {peer.get('rssi')}")
    if st.get("state") != "connected":
        return {}
    check(f"{tag}_mtu_517", peer.get("mtu") == 517, f"mtu {peer.get('mtu')}")
    check(f"{tag}_interval_7_5ms", abs(float(peer.get("itvl_ms", 0)) - 7.5) < 0.01, f"{peer.get('itvl_ms')} ms")
    check(f"{tag}_phy_negotiated", peer.get("phy_tx") == want and peer.get("phy_rx") == want,
          f"phy {peer.get('phy_tx')}/{peer.get('phy_rx')} want {want}")
    warn(f"{tag}_dle_251", peer.get("dle_tx") == 251 and peer.get("dle_rx") == 251,
         f"dle {peer.get('dle_tx')}/{peer.get('dle_rx')}")
    chars = st.get("chars", {})
    check(f"{tag}_chars_found", all(chars.get(k) for k in ("fff1", "fff2", "fff3", "fff4", "dis")), str(chars))

    out = {}
    r = sim.run("notify", seconds, 4 * 1024 * 1024, seconds + 30)
    out["notify_kbps"] = r.get("kbps", 0)
    check(f"{tag}_notify_ran", r.get("count", 0) > 0 and r.get("errors", 0) == 0,
          f"{r.get('bytes')} B {r.get('count')} PDUs in {r.get('ms')} ms = {r.get('kbps')} kbps; {r.get('detail')}")
    metric(f"{tag}_notify", r.get("kbps", 0), "kbps")

    r = sim.run("write", seconds, 1, seconds + 30)
    out["write_kbps"] = r.get("kbps", 0)
    check(f"{tag}_write_ran", r.get("ok") is True, f"{r.get('bytes')} B {r.get('count')} writes {r.get('retries')} "
          f"retries in {r.get('ms')} ms = {r.get('kbps')} kbps; {r.get('detail')}")
    metric(f"{tag}_write", r.get("kbps", 0), "kbps")

    r = sim.run("read", 10, 1, 40)
    out["read_kbps"] = r.get("kbps", 0)
    check(f"{tag}_read_ran", r.get("ok") is True, f"{r.get('count')} reads {r.get('bytes')} B = {r.get('kbps')} kbps")
    metric(f"{tag}_read", r.get("kbps", 0), "kbps")

    for size in (65536, 524288):
        r = sim.run("tunnel_up", 300, size, 330)
        check(f"{tag}_tunnel_up_{size}", r.get("ok") is True,
              f"{r.get('http_status')} {r.get('bytes')} B in {r.get('ms')} ms = {r.get('kbps')} kbps; {r.get('detail')}")
        metric(f"{tag}_tunnel_up_{size}", r.get("kbps", 0), "kbps")
        # v2 (2026-09-22): FFF3 subscribed for NOTIFICATIONS, the client pays
        # CREDITs and the frame counter proves no PDU was lost
        r = sim.run("tunnel_down", 300, size, 330)
        check(f"{tag}_tunnel_down_{size}", r.get("ok") is True and r.get("exact") is True
              and r.get("holes", 1) == 0 and r.get("out") == "notify",
              f"{r.get('http_status')} {r.get('bytes')} B exact={r.get('exact')} holes={r.get('holes')} "
              f"credits={r.get('credits')} out={r.get('out')} in {r.get('ms')} ms = {r.get('kbps')} kbps; {r.get('detail')}")
        check(f"{tag}_tunnel_down_{size}_credited", r.get("credits", 0) > 0, f"{r.get('credits')} CREDIT frames sent")
        metric(f"{tag}_tunnel_down_{size}", r.get("kbps", 0), "kbps")
        out[f"down_{size}"] = r.get("kbps", 0)

    # the same 512 KB download on INDICATIONS: the pre-v2 path, kept for apps
    # that want the confirmed PDU (and as the speed comparison)
    r = sim.run("tunnel_down", 300, 524288, 330, out="indicate")
    check(f"{tag}_tunnel_down_indicate_524288", r.get("ok") is True and r.get("exact") is True
          and r.get("out") == "indicate",
          f"{r.get('http_status')} {r.get('bytes')} B exact={r.get('exact')} holes={r.get('holes')} out={r.get('out')} "
          f"in {r.get('ms')} ms = {r.get('kbps')} kbps; {r.get('detail')}")
    metric(f"{tag}_tunnel_down_indicate_524288", r.get("kbps", 0), "kbps")
    if r.get("kbps") and out.get("down_524288"):
        metric(f"{tag}_notify_vs_indicate_down", round(out["down_524288"] / r["kbps"], 2), "x")

    st = sim.status()
    check(f"{tag}_no_pairing_failures", st.get("counters", {}).get("pair_fail", 0) == 0,
          f"pair ok/fail {st.get('counters', {}).get('pair_ok')}/{st.get('counters', {}).get('pair_fail')}")
    sim.post({"action": "disconnect"})
    sim.wait_state(("idle",), 15)

    # the DUT's view once WiFi is back
    time.sleep(6)
    dst, db = dut_api(dut, "/api/ble", timeout=5, ap_fallback=False)
    if dst == 200:
        http = next((c for c in db.get("channels", []) if c.get("name") == "http"), {})
        warn(f"{tag}_dut_channel_clean", http.get("tx_timeouts", 0) == 0 and http.get("rx_overflow", 0) == 0,
             f"tx_timeouts {http.get('tx_timeouts')} rx_overflow {http.get('rx_overflow')} blast {db.get('blast')}")
        warn(f"{tag}_dut_sent_notifications", http.get("tx_notifications", 0) > 0 and http.get("tx_indications", 0) > 0,
             f"out_modes {http.get('out_modes')} notifications {http.get('tx_notifications')} "
             f"indications {http.get('tx_indications')}")
    else:
        warn(f"{tag}_dut_reachable_after", False,
             f"{dst} (hotspot only; the AP path would restart the DUT's BLE before the next leg)")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sim", default="192.168.8.1")
    ap.add_argument("--dut", default="10.42.1.194")
    ap.add_argument("--phys", default="1m,2m")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--passkey", type=int, default=421337)
    ap.add_argument("--dut-via", default="rpi001", help="ssh host that can reach the DUT when this PC cannot")
    a = ap.parse_args()
    global DUT_VIA
    DUT_VIA = a.dut_via
    base = f"http://{a.sim}"
    dut = f"http://{a.dut}"

    st, info = api(base, "/api/info")
    check("sim_reachable", st == 200 and "Simulator" in str(info.get("product", "")), str(info)[:80])
    if st != 200:
        print("BLE THROUGHPUT FAIL: simulator unreachable")
        return 1
    st, comps = api(base, "/api/settings")
    check("sim_has_ble_central_bench", any(c.get("name") == "ble_central_bench" for c in comps)
          if isinstance(comps, list) else False, "settings list")
    dst, dble = dut_api(dut, "/api/ble", timeout=5)
    check("dut_ble_enabled", dst == 200 and dble.get("enabled") is True,
          f"{dst} enabled={dble.get('enabled') if isinstance(dble, dict) else '?'} "
          f"phy={dble.get('phy') if isinstance(dble, dict) else '?'}")

    sim = Sim(base)
    dut_phy_before = dble.get("phy") if isinstance(dble, dict) else None
    if dut_phy_before and dut_phy_before != "auto":
        ok, why = dut_set_phy(dut, "auto")
        check("dut_phy_auto_for_the_run", ok, why)
    per = {}
    for phy in [p.strip() for p in a.phys.split(",") if p.strip()]:
        per[phy] = leg(sim, base, dut, phy, a.seconds, a.passkey)

    one = per.get("1m", {})
    if one:
        check("ref_notify_ge_300kbps_1m", one.get("notify_kbps", 0) >= 300, f"{one.get('notify_kbps')} kbps (ref ~340)")
        check("ref_write_ge_400kbps_1m", one.get("write_kbps", 0) >= 400, f"{one.get('write_kbps')} kbps (ref ~500)")
    two = per.get("2m", {})
    if one and two:
        for k in ("notify_kbps", "write_kbps", "read_kbps"):
            metric(f"ratio_2m_over_1m_{k.split('_')[0]}", two.get(k, 0) / max(one.get(k, 1), 1), "ratio")

    # leave the simulator's central off (it would otherwise keep scanning for the DUT)
    set_sim(sim, base, enabled=False)
    if dut_phy_before and dut_phy_before != "auto":
        ok, why = dut_set_phy(dut, dut_phy_before)
        check("dut_phy_restored", ok, why)

    fails = [n for n, ok in results if not ok]
    if warns:
        print("WARN: " + ", ".join(warns), flush=True)
    print(("BLE THROUGHPUT FAIL: " + ", ".join(fails)) if fails else "BLE THROUGHPUT PASS", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
