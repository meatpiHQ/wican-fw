"""BLE 5 as settings: the `phy` / `advertising` bench, Pi side (settings,
reboots, advertising sets, restore). The PHY transfer legs run from the
PC's own adapter (ble_phy_pc.py): the bench Pi's UB500 (RTL8761BU) cannot
hold a 2M link with the ESP32-S3 (both sides time out on the first 2M
connection event, 2026-09-21), the PC's Intel adapter can.

Runs ON rpi001 (UB500, bleak, btmon). State in /tmp/ble_phy_bench.json (a
baseline already there is KEPT; note /tmp is wiped by a Pi reboot). The DUT
is reached over the hotspot when it has a lease, else through its own AP
(the Pi joins `--nm-profile` for the HTTP calls and leaves it before any
BLE work: a station on that AP stops BLE). Stages:

  configure : snapshot; BLE on (passkey), ble_http on, sta_ble_handover ON
              (the product default: WiFi suspends while a central is
              connected, BLE owns the radio), phy=1m advertising=legacy;
              submit-reboot. `--no-handover` keeps the STA up instead.
  adv       : drop the bond, fresh scan under btmon: the legacy PDU set must
              be on air (`legacy`); with `both` the extended (non-legacy)
              report must appear too, with its secondary PHY. Also connects
              once (1M link on the UB500) and reads phy_tx/phy_rx through
              the tunnel: with `phy=1m` the link stays 1M; with `phy=2m`
              the UB500 drops the link at the switch (reported as
              `WARN: adv_..._ub500_2m_drop`, the known rig limit).
  switch    : phy=2m advertising=both; submit-reboot.
  restore   : settings back, submit-reboot, no new faults, 0 unexpected
              resets, 0 own-tag E lines.

test.ps1 `blephy` = configure -> adv -> PC leg 1m -> switch -> adv -> PC leg
2m -> restore; verdict BLE PHY PASS when every stage passed and both PC
legs negotiated their PHY.

Usage (on rpi001):
  sudo env PYTHONPATH=/home/meatpi/.local/lib/python3.11/site-packages \
      python3 -u ble_phy_bench_pi.py --dut 10.42.1.194 --stage configure|adv|switch|restore
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bleak import BleakScanner  # noqa: E402

from ble_http_pi import Tunnel, rand_blob  # noqa: E402  (rand_blob: transfer_leg, used by ble_phy_pc.py)
from ble_link import (UUID_HTTP_IN, UUID_HTTP_OUT, Stream, api, bt_identity,  # noqa: E402
                      clean, connect, find_dut, prep_adapter, ring_errors, sh)

STATE = "/tmp/ble_phy_bench.json"
BTMON_LOG = "/tmp/ble_phy_btmon.log"
DIR = "/blephy"
SIZE = 65536
PHY_NAME = {0: "none", 1: "1M", 2: "2M", 3: "coded"}

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


def load():
    return json.load(open(STATE))


AP_IP = "192.168.0.10"


def reach(saved, nm_profile, window=60):
    """An address the DUT answers on: the hotspot lease (find_dut), else
    its own AP (the Pi joins nm_profile; the BLE stages leave it again).
    Returns (ip, status) or (None, {})."""
    hint = saved.get("ip") if saved.get("ip") != AP_IP else None
    nip, st = find_dut(hint, saved.get("device_id"), window=window)
    if nip is None and nm_profile:
        print(f"  (no hotspot lease; joining the DUT's AP {nm_profile})", flush=True)
        sh(f"sudo nmcli con up {nm_profile}")
        time.sleep(6)
        st2, body = api(AP_IP, "/api/status", timeout=5)
        if st2 == 200 and isinstance(body, dict):
            nip, st = AP_IP, body
    if nip and saved.get("device_id"):
        saved["ip"] = nip
        save(saved)
    return nip, st


def save(saved):
    json.dump(saved, open(STATE, "w"))


def submit_reboot(ip, saved, nm_profile, boot_before, comps):
    """POST submit; it reboots only when a PUT changed something
    (standard 4.2), so wait for a new boot_count only when a component
    reports pending_reboot. Returns (ip, status)."""
    pending = [c for c in comps if api(ip, f"/api/settings/{c}", timeout=5)[1].get("pending_reboot")]
    if not pending:
        print("  (nothing pending: submit will not reboot)", flush=True)
        api(ip, "/api/settings/submit", "POST", timeout=8)
        return ip, api(ip, "/api/status", timeout=5)[1]
    print(f"  submit + reboot ({', '.join(pending)} pending)...", flush=True)
    api(ip, "/api/settings/submit", "POST", timeout=8)
    time.sleep(15)
    t0 = time.time()
    while time.time() - t0 < 180:
        cand, st = reach(saved, nm_profile, window=20)
        if cand and st.get("boot_count") != boot_before:
            return cand, st
    return None, {}


# ------------------------------------------------------------------ configure
def stage_configure(ip, passkey, handover, nm_profile):
    st, status = api(ip, "/api/status")
    _, info = api(ip, "/api/info")
    device_id = info.get("device_id", "")
    if os.path.exists(STATE):
        saved = load()
        print(f"  baseline kept from {STATE}", flush=True)
    else:
        saved = {"device_id": device_id, "sta_mac": info.get("mac", ""),
                 "ble": clean(api(ip, "/api/settings/ble_manager")[1]),
                 "ble_http": clean(api(ip, "/api/settings/ble_http")[1]),
                 "ifm": clean(api(ip, "/api/settings/interface_manager")[1]),
                 "faults": api(ip, "/api/faults")[1].get("faults", []),
                 "legs": {}, "handover": handover}
        save(saved)
    check("configure_baseline", "phy" in saved["ble"] and device_id != "",
          f"device {device_id} phy={saved['ble'].get('phy')} advertising={saved['ble'].get('advertising')}")
    return apply_phy(ip, saved, passkey, "1m", "legacy", status.get("boot_count"), "configure", nm_profile)


def apply_phy(ip, saved, passkey, phy, adv, boot_before, tag, nm_profile=None):
    ble = dict(saved["ble"])
    ble.update({"enabled": True, "passkey": passkey, "phy": phy, "advertising": adv})
    check(f"{tag}_put_ble", api(ip, "/api/settings/ble_manager", "PUT", ble)[0] == 200, f"phy={phy} adv={adv}")
    bh = dict(saved["ble_http"]); bh["enabled"] = True
    check(f"{tag}_put_ble_http", api(ip, "/api/settings/ble_http", "PUT", bh)[0] == 200)
    ifm = dict(saved["ifm"]); ifm["sta_ble_handover"] = bool(saved.get("handover"))
    check(f"{tag}_put_ifm", api(ip, "/api/settings/interface_manager", "PUT", ifm)[0] == 200)
    nip, st2 = submit_reboot(ip, saved, nm_profile, boot_before,
                             ("ble_manager", "ble_http", "interface_manager"))
    check(f"{tag}_dut_back", nip is not None, f"ip={nip}")
    if nip:
        st3, b = api(nip, "/api/ble")
        check(f"{tag}_api_ble_reports_setting", st3 == 200 and b.get("phy") == phy and b.get("advertising") == adv,
              f"{st3} phy={b.get('phy')} advertising={b.get('advertising')}")
        saved["ip"] = nip
        saved["phy"] = phy
        saved["adv"] = adv
        save(saved)
    return nip


# ------------------------------------------------------------- transfer legs
async def transfer_leg(t, ip, phy, tun=None):
    """The PHY-sensitive transfers through the HTTP tunnel (run from the PC
    adapter by ble_phy_pc.py): 1 x SIZE upload (write-without-response),
    3 x SIZE download (indications, byte-exact), 10 x GET /api/status RTT.
    ip = the WiFi witness for the channel counters (None: through the
    tunnel `tun`)."""
    leg = {}
    base = "/data" + DIR
    await t.request("POST", f"/api/fs/mkdir?path={base}")
    blob = rand_blob(SIZE, 77)
    path = f"{base}/p{phy}.bin"

    t0 = time.monotonic()
    st, rsp, body = await t.request("POST", f"/api/fs/upload?path={path}", blob,
                                    ct="application/octet-stream", timeout=180)
    dt = time.monotonic() - t0
    check(f"{phy}_upload_64k", st == 200, f"{st} {body[:60]} {dt:.1f}s")
    leg["upload_kbs"] = SIZE / 1024 / max(dt, 1e-3)
    metric(f"phy{phy}_upload_64k", leg["upload_kbs"], "KB/s")

    rates = []
    for i in range(3):
        t0 = time.monotonic()
        st, rsp, back = await t.request("GET", f"/api/fs/download?path={path}", timeout=300)
        dt = time.monotonic() - t0
        check(f"{phy}_download_64k_{i + 1}_exact", st == 200 and back == blob, f"{st} len {len(back)} {dt:.1f}s")
        rates.append(SIZE / 1024 / max(dt, 1e-3))
    leg["download_kbs"] = sum(rates) / len(rates)
    metric(f"phy{phy}_download_64k", leg["download_kbs"], "KB/s")

    rtts = []
    for _ in range(10):
        t0 = time.monotonic()
        st, rsp, status = await t.get("/api/status")
        rtts.append((time.monotonic() - t0) * 1000)
        if st != 200:
            break
    rtts.sort()
    check(f"{phy}_status_rtt_10x", st == 200 and len(rtts) == 10, f"p50 {rtts[len(rtts) // 2]:.0f} ms")
    leg["rtt_p50_ms"] = rtts[len(rtts) // 2]
    metric(f"phy{phy}_status_rtt_p50", leg["rtt_p50_ms"], "ms")

    await t.request("DELETE", f"/api/fs/file?path={path}")
    await t.request("DELETE", f"/api/fs/file?path={base}")
    stats = {}
    if ip:
        st, b = api(ip, "/api/ble", timeout=5)
    else:
        st, rsp, b = await tun.get("/api/ble")
        b = b if isinstance(b, dict) else {}
    if st == 200:
        for c in b.get("channels", []):
            if c.get("name") == "http":
                stats = c
    check(f"{phy}_channel_clean", stats.get("tx_timeouts", 0) == 0 and stats.get("rx_overflow", 0) == 0,
          f"tx_timeouts {stats.get('tx_timeouts')} rx_overflow {stats.get('rx_overflow')}")
    return leg


def classify_adv_reports(txt, name):
    """(legacy, extended, secondary PHYs) report counts for the DUT out of a
    btmon dump: the DUT's advertising addresses are the ones whose reports
    carry its name or the FFF0 UUID; every report from those addresses is
    then legacy (Use legacy advertising PDUs / LE Advertising Report) or
    extended."""
    entries = re.split(r"\n(?=\s+Entry \d+|> HCI Event)", txt)
    addrs = set()
    for e in entries:
        if name in e or "0000fff0-0000-1000-8000-00805f9b34fb" in e:
            m = re.search(r"Address: ([0-9A-F:]{17})", e)
            if m:
                addrs.add(m.group(1))
    legacy = extended = 0
    sec = set()
    for e in entries:
        m = re.search(r"Address: ([0-9A-F:]{17})", e)
        if not m or m.group(1) not in addrs or "Event type:" not in e:
            continue
        if "legacy advertising PDUs" in e or "LE Advertising Report" in e:
            legacy += 1
        else:
            extended += 1
            ms = re.search(r"Secondary PHY: (.+)", e)
            if ms:
                sec.add(ms.group(1).strip())
    return legacy, extended, sorted(sec)


# ------------------------------------------------------------------------ adv
async def adv_leg(saved, identity, name):
    adv = saved["adv"]
    print(f"  advertising leg ({adv}): dropping the bond, fresh scan under btmon", flush=True)
    prep_adapter(identity, None, drop_bond=True)
    subprocess.run(f"pkill -x btmon; rm -f {BTMON_LOG}", shell=True)
    mon = subprocess.Popen(["btmon", "-w", BTMON_LOG], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(0.5)
    seen = None
    for _ in range(3):
        found = await BleakScanner.discover(timeout=8.0, return_adv=True)
        for d, a in found.values():
            if a.local_name == name or d.name == name:
                seen = (d.address, a.rssi, sorted(a.service_uuids), a.manufacturer_data)
                break
        if seen:
            break
    mon.terminate()
    await asyncio.sleep(0.5)
    txt = sh(f"btmon -r {BTMON_LOG} 2>/dev/null")
    legacy, extended, sec = classify_adv_reports(txt, name)
    check(f"adv_{adv}_scan_finds_name", seen is not None,
          f"{seen[0]} rssi {seen[1]} uuids {seen[2]} mfg {seen[3]}" if seen else "not found in 3 x 8 s")
    check(f"adv_{adv}_legacy_set_on_air", legacy > 0, f"{legacy} legacy reports, {extended} extended")
    if adv == "both":
        check("adv_both_extended_set_on_air", extended > 0,
              f"{extended} extended reports (secondary PHY {sec}), {legacy} legacy")
    else:
        check(f"adv_{adv}_no_extended_set", extended == 0, f"{extended} extended reports")
    metric(f"adv_{adv}_legacy_reports", legacy, "count")
    metric(f"adv_{adv}_extended_reports", extended, "count")


async def link_leg(saved, passkey, name, identity):
    """One connect on the UB500: pair fresh, read the live PHY through the
    tunnel. phy=1m must stay 1M (the explicit 1M-only preference); phy=2m
    is the known UB500 limit (the link drops at the switch) = WARN."""
    phy = saved["phy"]
    try:
        client, cap = await connect(name, identity, passkey, bonded=False, tries=2)
    except Exception as e:  # noqa: BLE001
        if phy == "2m":
            warn(f"adv_{phy}_ub500_2m_drop", False, f"UB500 cannot hold the 2M link: {str(e)[:80]}")
            return
        check(f"adv_{phy}_connect", False, str(e)[:100])
        return
    s = Stream(client, UUID_HTTP_OUT, UUID_HTTP_IN, cap)
    await s.start()
    t = Tunnel(s)
    await asyncio.sleep(2.0)
    st, rsp, b = await t.get("/api/ble")
    b = b if isinstance(b, dict) else {}
    check(f"adv_{phy}_tunnel_reads_phy", st == 200 and b.get("phy") == phy, f"{st} phy={b.get('phy')}")
    if phy == "1m":
        check("adv_1m_link_stays_1m", b.get("phy_tx") == 1 and b.get("phy_rx") == 1,
              f"phy_tx {PHY_NAME.get(b.get('phy_tx'))} phy_rx {PHY_NAME.get(b.get('phy_rx'))}")
    else:
        warn(f"adv_{phy}_ub500_link", b.get("phy_tx") == 2, f"phy_tx {PHY_NAME.get(b.get('phy_tx'))}")
    await s.stop()
    await client.disconnect()
    await asyncio.sleep(2)


async def adv_stage_async(saved, passkey, nm_profile):
    device_id = saved["device_id"]
    name = f"WiC_{device_id}"
    identity = bt_identity(saved["sta_mac"])
    prep_adapter(identity, nm_profile)  # leaves the DUT's AP
    await asyncio.sleep(4)             # BLE restarts after the station left
    await adv_leg(saved, identity, name)
    await link_leg(saved, passkey, name, identity)


def stage_adv(passkey, nm_profile):
    saved = load()
    asyncio.run(adv_stage_async(saved, passkey, nm_profile))


# --------------------------------------------------------------------- switch
def stage_switch(passkey, nm_profile):
    saved = load()
    nip, st = reach(saved, nm_profile)
    check("switch_dut_reachable", nip is not None, f"ip={nip}")
    if nip:
        apply_phy(nip, saved, passkey, "2m", "both", st.get("boot_count"), "switch", nm_profile)


# --------------------------------------------------------------------- restore
def stage_restore(hint, nm_profile):
    saved = load()
    device_id = saved["device_id"]
    nip, st = reach(saved, nm_profile, window=90)
    check("restore_dut_reachable", nip is not None, f"ip={nip}")
    if nip is None:
        print(f"RESTORE SKIPPED: DUT unreachable; saved settings in {STATE}", flush=True)
        return
    own, other, errs = ring_errors(nip)
    check("restore_zero_E_lines_own_tags", own is not None and not own,
          f"{len(errs) if errs is not None else '?'} E lines total" + (f": {own[0][:100]}" if own else ""))
    warn("restore_other_E_lines", not other, f"{len(other) if other else 0}: {other[0][:90] if other else ''}")
    for name, path in (("ble", "ble_manager"), ("ble_http", "ble_http"), ("ifm", "interface_manager")):
        check(f"restore_put_{name}", api(nip, f"/api/settings/{path}", "PUT", saved[name])[0] == 200)
    print("  restoring...", flush=True)
    nip2, st2 = submit_reboot(nip, saved, nm_profile, st.get("boot_count"),
                              ("ble_manager", "ble_http", "interface_manager"))
    check("restore_dut_back", nip2 is not None, f"ip={nip2}")
    if nip2:
        b = api(nip2, "/api/settings/ble_manager")[1]
        check("restore_ble_setting", b.get("enabled") == saved["ble"].get("enabled") and
              b.get("phy") == saved["ble"].get("phy"), f"phy={b.get('phy')} advertising={b.get('advertising')}")
        before = {f["code"] for f in saved["faults"]}
        new = [f for f in api(nip2, "/api/faults")[1].get("faults", []) if f["code"] not in before]
        check("restore_no_new_faults", not new, str(new)[:200])
        check("restore_no_unexpected_resets", st2.get("unexpected_resets", 0) == 0,
              f"unexpected_resets={st2.get('unexpected_resets')}")
        os.replace(STATE, STATE + ".done")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="10.42.1.194")
    ap.add_argument("--stage", choices=["configure", "adv", "switch", "restore"], required=True)
    ap.add_argument("--passkey", type=int, default=421337)
    ap.add_argument("--nm-profile", default="wican-bench-ap")
    ap.add_argument("--no-handover", action="store_true",
                    help="keep the STA up during BLE sessions (sta_ble_handover off) instead of the product default")
    a = ap.parse_args()
    if a.stage == "configure":
        saved = load() if os.path.exists(STATE) else {"device_id": None, "ip": a.dut}
        ip, _ = reach(saved, a.nm_profile, window=30)
        if not ip:
            print("FATAL: DUT unreachable")
            return 1
        stage_configure(ip, a.passkey, not a.no_handover, a.nm_profile)
    elif a.stage == "adv":
        stage_adv(a.passkey, a.nm_profile)
    elif a.stage == "switch":
        stage_switch(a.passkey, a.nm_profile)
    else:
        stage_restore(a.dut, a.nm_profile)
    print(f"PROGRESS {a.stage} done", flush=True)

    fails = [n for n, ok in results if not ok]
    if warns:
        print("WARN: " + ", ".join(warns), flush=True)
    print(f"STAGE {a.stage} " + ("FAIL: " + ", ".join(fails) if fails else "OK"), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
