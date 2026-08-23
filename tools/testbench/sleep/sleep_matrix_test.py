#!/usr/bin/env python3
"""Sleep robustness matrix runner — see SLEEP_MATRIX.md for the map.

Per scenario: configure the subsystem, hold its live condition, drop to
12.5 V (sleep_delay_min=1), prove entry by supply-current delta, soak
asleep watching for spurious wakes, restore 14.0 V, prove the wake, then
run forensics: zero unexpected resets, exactly one new restart record
(power_wake/sleep_mode), /api/faults empty. A failed scenario recovers
the DUT (COM7 reset pulse — that port's open resets it anyway) and the
suite moves on. `wifi_ghost` runs LAST (its restore goes through the
DUT's own AP via the Pi).

Usage:  python tools/testbench/sleep_matrix_test.py
            [--only baseline,mqtt,...] [--psu-port COM2016]
            [--bench-host rpi001] [--elm-port COM6]
Expected final line: SLEEP MATRIX PASS
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))          # sleep_bench_test
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from owon_psu import OwonPsu            # noqa: E402
from sleep_bench_test import PiHttp, mA  # noqa: E402

V_AWAKE = 14.0
V_SLEEPY = 12.5

results = []   # (key, verdict, detail)


def pi_sh(host, cmd, stdin=None, timeout=60):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", host, cmd],
                       input=stdin, capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


class Rig:
    """Everything a scenario needs: PSU, DUT-over-Pi, local serial."""

    def __init__(self, args):
        self.args = args
        self.psu = OwonPsu(args.psu_port)
        self.dut = PiHttp(args.bench_host, "auto")
        self.reboots = 0  # config reboots we caused (forensics budget)

    # ---- DUT config ------------------------------------------------------

    def put_settings(self, comp, patch, wait=True):
        """Merge-PUT one component's settings; submit-reboot if changed.
        wait=False for changes that knowingly take the DUT offline
        (e.g. the ghost-SSID scenario)."""
        cur = self.dut.get(f"/api/settings/{comp}")
        assert cur, f"{comp} settings unreachable"
        cur.pop("degraded", None)
        cur.pop("pending_reboot", None)
        cur.update(patch)
        r = self.dut.put_json(f"/api/settings/{comp}", cur)
        assert r is not None and "error" not in r, f"{comp} PUT: {r}"
        if not r.get("changed"):
            return False
        r = self.dut.post("/api/settings/submit")
        assert r and r.get("reboot"), f"submit: {r}"
        self.reboots += 1
        time.sleep(8)
        if wait:
            assert self.dut.wait_up(120), "DUT lost after submit-reboot"
        return True

    def pi(self, cmd, stdin=None, timeout=60):
        return pi_sh(self.args.bench_host, cmd, stdin, timeout)

    def dut_gw(self):
        """The bench-hotspot gateway serving the DUT (10.42.X.1)."""
        ip = self.dut.base.split("//")[1]
        return ".".join(ip.split(".")[:3]) + ".1"

    def recover(self):
        """Whatever state the DUT is in: hard reset via COM7 + 13.5 V."""
        self.psu.set_voltage(13.5)
        self.psu.output(True)
        try:
            from dut import Dut
            d = Dut("COM7", 2000000)
            d.hard_reset()
            d.expect(r"WICAN BOOT", 40)
            d.close()
        except Exception as e:
            print(f"  (recover: console reset failed: {e})")
        self.dut.wait_up(180)


# ---- the sleep/soak/wake core (shared by every scenario) -----------------

def sleep_cycle(rig, key, soak_s=90, entry_extra_s=90, offline_ok=False,
                wake_http=True, seq_base=None):
    """Drop -> entry -> soak -> wake -> forensics. Raises on failure.
    offline_ok: don't require HTTP to be answering before/after (BLE
    suspends WiFi; the ghost-SSID DUT is off the bench net entirely).
    wake_http=False skips every HTTP assert (current-only cycle);
    seq_base overrides the forensic baseline captured here."""
    dut, psu = rig.dut, rig.psu

    # forensic baseline right before the drop
    hist = (dut.get("/api/restart/history") or {}) if wake_http else {}
    seq0 = seq_base if seq_base is not None else \
        ((hist.get("records") or [{}])[0]).get("seq", -1)
    unexpected0 = hist.get("unexpected_resets", -1)

    awake_a = statistics.mean(psu.sample_current(4))
    gate = awake_a - 0.030
    print(f"  awake {mA(awake_a)}; dropping to {V_SLEEPY} V")
    psu.set_voltage(V_SLEEPY)
    t0 = time.time()

    entry = psu.wait_current(lambda a: a < gate,
                             timeout_s=60 + entry_extra_s)
    took = time.time() - t0
    assert entry is not None, \
        f"no sleep entry (never below {mA(gate)} in {took:.0f} s)"
    sleep_a = statistics.mean(psu.sample_current(6))
    print(f"  asleep after {took:.0f} s, {mA(sleep_a)} "
          f"(delta {mA(awake_a - sleep_a)}); soaking {soak_s} s")

    # soak: watch for spurious wakes (sustained current rise) and for
    # the DUT answering HTTP (it must be gone)
    wake_thresh = sleep_a + 0.025
    high = 0
    t_soak = time.time()
    next_http = t_soak + 45
    while time.time() - t_soak < soak_s:
        a = psu.meas_current()
        high = high + 1 if a > wake_thresh else 0
        assert high < 4, \
            f"spurious wake during soak ({mA(a)} sustained at " \
            f"t+{time.time() - t_soak:.0f}s)"
        if not offline_ok and time.time() >= next_http:
            next_http += 60
            if dut.get("/api/sleep", timeout_s=3) is not None:
                raise AssertionError("DUT answering HTTP mid-soak")
        time.sleep(2)

    print(f"  soak clean; waking at {V_AWAKE} V")
    psu.set_voltage(V_AWAKE)
    awake = psu.wait_current(lambda a: a >= sleep_a + 0.030, timeout_s=60)
    assert awake is not None, "no wake current on voltage recovery"
    if not wake_http:
        print(f"  wake current {mA(awake)} (HTTP checks deferred)")
        return
    st = dut.wait_up(120)
    assert st, "DUT unreachable after wake"
    assert st.get("state") in ("normal", "wake_pending"), st

    # forensics
    hist = dut.get("/api/restart/history") or {}
    recs = hist.get("records") or []
    assert recs, "no restart records after wake"
    newest = recs[0]
    assert newest.get("planned_reason") == "power_wake" \
        and newest.get("source") == "sleep_mode", \
        f"wrong wake record: {json.dumps(newest)[:100]}"
    assert newest.get("seq") == seq0 + 1, \
        f"extra reboots during scenario (seq {seq0} -> " \
        f"{newest.get('seq')}) — crash/recovery in between?"
    assert hist.get("unexpected_resets") == unexpected0, \
        f"unexpected resets grew: {unexpected0} -> " \
        f"{hist.get('unexpected_resets')}"
    for r in recs:
        if r.get("seq", 0) <= seq0:
            continue  # the ring keeps older sessions' records
        assert r.get("planned_reason") != "internal_recovery", \
            "OBD chip refused to stay asleep (internal_recovery)"
    faults = (dut.get("/api/faults") or {}).get("faults")
    assert faults == [], f"latched faults: {faults}"
    print(f"  wake clean ({mA(awake)}), forensics clean")


# ---- scenarios -----------------------------------------------------------

def s_baseline(rig):
    sleep_cycle(rig, "baseline")


def s_mqtt(rig):
    rig.put_settings("mqtt_manager",
                     {"enabled": True,
                      "url": f"mqtt://{rig.dut_gw()}:1883",
                      "client_id": "wican-sleep-matrix"})
    time.sleep(5)  # let it connect
    try:
        sleep_cycle(rig, "mqtt")
    finally:
        rig.put_settings("mqtt_manager", {"enabled": False, "url": ""})


def _start_tcp_poll(rig):
    """Continuous 010C polling over TCP:35000 from the Pi, background.
    Runs until the connection dies (sleep kills it) or 300 s."""
    ip = rig.dut.base.split("//")[1]
    script = (
        "import socket,time\n"
        "s=socket.create_connection(('%s',35000),timeout=5)\n"
        "s.settimeout(2)\n"
        "t=time.time()\n"
        "while time.time()-t<300:\n"
        "    try:\n"
        "        s.sendall(b'010C\\r')\n"
        "        s.recv(256)\n"
        "    except OSError:\n"
        "        break\n"
        "    time.sleep(0.2)\n" % ip)
    rig.pi("cat > /tmp/sleep_poll.py && "
           "nohup python3 /tmp/sleep_poll.py >/dev/null 2>&1 &",
           stdin=script)
    time.sleep(3)


def s_tcp_poll(rig):
    _start_tcp_poll(rig)
    sleep_cycle(rig, "tcp_poll")


def s_net_traffic(rig):
    ip = rig.dut.base.split("//")[1]
    rig.pi(f"nohup sh -c 'for i in $(seq 1 300); do "
           f"curl -s -m 2 http://{ip}/api/status >/dev/null; "
           f"sleep 0.2; done' >/dev/null 2>&1 &")
    _start_tcp_poll(rig)  # held socket too
    sleep_cycle(rig, "net_traffic")


def s_logger_can(rig):
    rig.put_settings("data_logger",
                     {"enabled": True, "format": "sqlite",
                      "can_log": True})
    _start_tcp_poll(rig)  # generate CAN traffic to log
    try:
        sleep_cycle(rig, "logger_can")
    finally:
        rig.put_settings("data_logger",
                         {"enabled": False, "can_log": False})


def s_elm_monitor(rig):
    import serial
    ser = serial.Serial(f"\\\\.\\{rig.args.elm_port}", 2000000,
                        timeout=0.2)
    try:
        ser.write(b"ATZ\r")
        time.sleep(1.5)
        ser.reset_input_buffer()
        ser.write(b"ATMA\r")  # monitor-all: chip streams until stopped
        time.sleep(1)
        sleep_cycle(rig, "elm_monitor")
    finally:
        try:
            ser.write(b" ")  # legacy: SPACE stops a monitor
            ser.close()
        except Exception:
            pass


def s_ble_client(rig):
    rc, _ = rig.pi("python3 -c \"import sys; sys.path.append("
                   "'/home/meatpi/.local/lib/python3.11/site-packages');"
                   " import bleak\"")
    if rc != 0:
        raise SkipScenario("bleak not importable on the Pi")
    rig.put_settings("ble_manager", {"enabled": True})
    script = (
        "import sys,asyncio\n"
        "sys.path.append('/home/meatpi/.local/lib/python3.11/"
        "site-packages')\n"
        "from bleak import BleakScanner, BleakClient\n"
        "async def main():\n"
        "    d=await BleakScanner.find_device_by_filter(\n"
        "        lambda dev,ad: (dev.name or '').startswith('WiC_'),"
        "timeout=20)\n"
        "    if not d: return\n"
        "    async with BleakClient(d) as c:\n"
        "        await asyncio.sleep(240)\n"
        "asyncio.run(main())\n")
    rig.pi("bluetoothctl devices 2>/dev/null | awk '/WiC_/{print $2}' | "
           "xargs -r -n1 bluetoothctl remove >/dev/null 2>&1; "
           "cat > /tmp/sleep_ble.py && "
           "nohup python3 /tmp/sleep_ble.py >/dev/null 2>&1 &",
           stdin=script)
    time.sleep(25)  # scan + connect window
    try:
        # a connected central may suspend WiFi (interface_manager) —
        # verify by current only
        sleep_cycle(rig, "ble_client", offline_ok=True)
    finally:
        rig.pi("pkill -f sleep_ble.py 2>/dev/null; true")
        rig.put_settings("ble_manager", {"enabled": False})


def s_wg_soak(rig):
    kg = rig.dut.post("/api/vpn/keygen")
    assert kg and kg.get("public_key"), f"keygen: {kg}"
    dut_pub = kg["public_key"]
    rc, srv_pub = rig.pi("sudo cat /etc/wireguard/wg-bench-server.pub")
    assert rc == 0 and len(srv_pub) == 44, srv_pub
    rc, base = rig.pi("sudo sed -n '1,/^\\[Peer\\]/p' "
                      "/etc/wireguard/wg-bench.conf")
    base = base.split("[Peer]")[0].rstrip()
    conf = f"{base}\n\n[Peer]\nPublicKey = {dut_pub}\n" \
           f"AllowedIPs = 10.66.0.2/32\n"
    rig.pi("sudo tee /etc/wireguard/wg-bench.conf >/dev/null",
           stdin=conf)
    rig.pi("sudo wg-quick down wg-bench 2>/dev/null; "
           "sudo wg-quick up wg-bench")
    rig.put_settings("vpn_manager",
                     {"enabled": True, "type": "wireguard",
                      "peer_public_key": srv_pub, "private_key": "",
                      "address": "10.66.0.2",
                      "allowed_ip": "10.66.0.0",
                      "allowed_ip_mask": "255.255.255.0",
                      "endpoint": rig.dut_gw(), "port": 51821,
                      "keepalive_s": 5, "default_route": False})
    state = None
    for _ in range(45):
        st = rig.dut.get("/api/vpn") or {}
        state = st.get("state")
        if state == "connected":
            break
        time.sleep(2)
    assert state == "connected", f"WG never connected: {state}"
    print("  WireGuard connected; long soak (the delayed-panic window)")
    try:
        sleep_cycle(rig, "wg_soak", soak_s=600)
    finally:
        rig.put_settings("vpn_manager", {"enabled": False})
        rig.pi("sudo wg-quick down wg-bench 2>/dev/null; true")


def s_ts_soak(rig):
    raise SkipScenario("local headscale leg superseded on this bench "
                       "by ts_betty_soak (public control server)")


def _soak_sh(rig, script, up_args=""):
    """Run a betty public-soak script verb from the repo checkout
    (bash; drives betty + the Pi over ssh). Returns (rc, output)."""
    env = f"DUT_IP={rig.dut.base.split('//')[1]} "
    # GIT Bash explicitly (plain "bash" resolves to WSL's, which can't
    # see c:/ paths), forward slashes for its benefit
    bash = r"C:\Program Files\Git\bin\bash.exe"
    if not Path(bash).exists():
        bash = "bash"
    path = (Path(__file__).resolve().parents[1] / "vpn" / script).as_posix()
    p = subprocess.run([bash, "-c", f"{env}bash '{path}' {up_args}"],
                       capture_output=True, text=True, timeout=420)
    return p.returncode, (p.stdout + p.stderr)[-2000:]


def s_wg_betty_soak(rig):
    """WireGuard against the PUBLIC betty endpoint (real internet path)
    + the 10-min asleep soak — the closest reproduction of the
    historical delayed-panic conditions."""
    rc, out = _soak_sh(rig, "wg_public_soak.sh", "up split")
    if rc != 0:
        raise SkipScenario(f"betty WG up failed: {out[-200:]}")
    try:
        # `up` submits and returns while the DUT reboots — wait for it,
        # then for the handshake
        time.sleep(8)
        assert rig.dut.wait_up(120), "DUT lost after WG submit-reboot"
        st = {}
        for _ in range(45):
            st = rig.dut.get("/api/vpn") or {}
            if st.get("state") == "connected":
                break
            time.sleep(2)
        assert st.get("state") == "connected", f"WG not connected: {st}"
        sleep_cycle(rig, "wg_betty_soak", soak_s=600)
    finally:
        # --wipe also disables/blanks the DUT's vpn config — without it
        # the DUT keeps dialing the (now dead) betty endpoint forever
        _soak_sh(rig, "wg_public_soak.sh", "down --wipe")


def s_ts_betty_soak(rig):
    """Tailscale via the PUBLIC headscale on betty + 10-min asleep
    soak. `up` also runs the register/tailnet/tunnel legs itself."""
    rc, out = _soak_sh(rig, "ts_public_soak.sh", "up")
    if rc != 0:
        raise SkipScenario(f"betty TS up failed: {out[-200:]}")
    try:
        st = rig.dut.get("/api/vpn") or {}
        assert st.get("state") == "connected", f"TS not connected: {st}"
        sleep_cycle(rig, "ts_betty_soak", soak_s=600)
    finally:
        _soak_sh(rig, "ts_public_soak.sh", "down")


def s_kitchen_sink(rig):
    rig.put_settings("mqtt_manager",
                     {"enabled": True,
                      "url": f"mqtt://{rig.dut_gw()}:1883",
                      "client_id": "wican-sleep-matrix"})
    rig.put_settings("data_logger",
                     {"enabled": True, "format": "sqlite",
                      "can_log": True})
    _start_tcp_poll(rig)
    time.sleep(5)
    try:
        sleep_cycle(rig, "kitchen_sink", soak_s=300)
    finally:
        rig.put_settings("data_logger",
                         {"enabled": False, "can_log": False})
        rig.put_settings("mqtt_manager", {"enabled": False, "url": ""})


def s_wifi_ghost(rig):
    """STA aimed at an absent SSID — connect-retry loop live at entry.
    RUNS LAST: the DUT drops off the bench hotspot; the cycle is
    current-only and restore + forensics go through the DUT's own AP."""
    st = rig.dut.get("/api/status") or {}
    dev_id = st.get("device_id") or st.get("id")
    hist = rig.dut.get("/api/restart/history") or {}
    seq0 = ((hist.get("records") or [{}])[0]).get("seq", -1)

    rig.put_settings("wifi_manager", {"sta_ssid": "WICAN_GHOST_AP",
                                      "sta_password": "nosuchnet123"},
                     wait=False)
    # from here the DUT is off the bench net — current is the truth
    time.sleep(25)  # reboot + let the retry loop get going
    try:
        sleep_cycle(rig, "wifi_ghost", offline_ok=True, wake_http=False)
    finally:
        _wifi_ghost_restore(rig, dev_id)
    # deferred forensics now that it is back on the bench hotspot:
    # config reboot + wake reboot + restore reboot = seq0 + 3, zero
    # unexpected, wake record (one below the restore's config_apply)
    hist = rig.dut.get("/api/restart/history") or {}
    recs = hist.get("records") or []
    seqs = {r.get("seq"): r for r in recs}
    wake = seqs.get(seq0 + 2)
    assert wake and wake.get("planned_reason") == "power_wake", \
        f"wake record missing/wrong at seq {seq0 + 2}: " \
        f"{json.dumps(recs)[:200]}"
    for r in recs:
        if r.get("seq", 0) <= seq0:
            continue  # older sessions' records stay in the ring
        assert r.get("planned_reason") != "internal_recovery", \
            "OBD chip refused to stay asleep"
        assert r.get("seq", 0) <= seq0 + 3, \
            f"extra reboot: {json.dumps(r)[:100]}"


def _wifi_ghost_restore(rig, dev_id):
    """Join the DUT's AP from the Pi, point STA back at the bench."""
    ap = f"WiCAN_{dev_id}" if dev_id else "WiCAN_14c19f44e349"
    print(f"  restoring wifi via the DUT AP ({ap}) ...")
    # HOLD autoconnect off for the whole join window: a bare `down`
    # lasts seconds before NM re-activates wican-bench, and once wtest0
    # is back in AP (hotspot) mode its scans come up empty — every
    # retry then fails with "No network with SSID ... found" (stranded
    # the DUT 2026-07-22, full matrix run)
    rig.pi("sudo nmcli connection modify wican-bench "
           "connection.autoconnect no; "
           "sudo nmcli connection down wican-bench 2>/dev/null; true")
    # the AP needs time to come up after the wake reboot, and fresh
    # scans come back thin — retry with rescans (cost a stranded DUT
    # 2026-07-21 when a single early scan missed it)
    rc, out = 1, "never scanned"
    try:
        for _ in range(5):
            rig.pi("sudo nmcli device wifi rescan ifname wtest0 "
                   "2>/dev/null; sleep 6; true")
            rc, out = rig.pi(f"sudo nmcli device wifi connect '{ap}' "
                             f"password '@meatpi#' ifname wtest0 "
                             f"name wican-dut", timeout=90)
            if rc == 0:
                break
    finally:
        if rc != 0:  # joined path re-enables in the restore finally
            rig.pi("sudo nmcli connection modify wican-bench "
                   "connection.autoconnect yes; "
                   "sudo nmcli connection up wican-bench "
                   "2>/dev/null; true")
    assert rc == 0, f"could not join DUT AP: {out[-120:]}"
    rig.pi("sudo nmcli connection modify wican-dut "
           "ipv4.never-default yes ipv4.route-metric 4000 "
           "ipv4.routes 192.168.0.10/32")
    # route pinning applies on (re)activation only
    rig.pi("sudo nmcli connection up wican-dut", timeout=60)
    time.sleep(5)
    try:
        cfg_cmd = ("curl -s -m 8 http://192.168.0.10/api/settings/"
                   "wifi_manager")
        rc, cur = rig.pi(cfg_cmd)
        cfg = json.loads(cur)
        cfg.pop("degraded", None)
        cfg.pop("pending_reboot", None)
        # the REAL bench PSK, read live: settings GETs redact secrets
        # (merging "" back would clobber the stored one) and hardcoded
        # constants go stale — the once-documented value was rotated
        # away and stranded the DUT once (2026-07-21)
        rc, psk = rig.pi("sudo nmcli -s -g 802-11-wireless-security.psk"
                         " connection show wican-bench")
        assert rc == 0 and psk, "could not read the bench PSK"
        cfg.update({"sta_ssid": "WICAN_TEST_AP",
                    "sta_password": psk})
        rig.pi("curl -s -m 8 -X PUT -H 'Content-Type: application/json'"
               " -d @- http://192.168.0.10/api/settings/wifi_manager",
               stdin=json.dumps(cfg))
        rig.pi("curl -s -m 15 -X POST "
               "http://192.168.0.10/api/settings/submit")
    finally:
        rig.pi("sudo nmcli connection delete wican-dut 2>/dev/null; "
               "sudo nmcli connection modify wican-bench "
               "connection.autoconnect yes; "
               "sudo nmcli connection up wican-bench 2>/dev/null; true")
    time.sleep(10)
    assert rig.dut.wait_up(180), "DUT never rejoined the bench hotspot"


def s_periodic_critical(rig):
    """Periodic check-in wake + the < 11.90 V CRITICAL floor.
    Control leg at 12.5 V: periodic_wakeup (1 min) MUST fire while
    sleeping (awake current burst). Critical leg at 11.75 V (DUT reads
    ~11.68 < SM_CRITICAL_V): periodic wake MUST stay suppressed for
    3+ intervals — only voltage recovery may touch a critical battery."""
    rig.put_settings("sleep_manager",
                     {"enabled": True, "periodic_wakeup": True,
                      "wakeup_interval_min": 1, "sleep_delay_min": 1})

    def enter_and_measure(v_hold):
        rig.psu.set_voltage(V_AWAKE)
        assert rig.dut.wait_up(120), "DUT not up pre-leg"
        time.sleep(5)
        awake_a = statistics.mean(rig.psu.sample_current(4))
        rig.psu.set_voltage(v_hold)
        entry = rig.psu.wait_current(lambda a: a < awake_a - 0.030,
                                     timeout_s=150)
        assert entry is not None, f"no sleep entry at {v_hold} V"
        sleep_a = statistics.mean(rig.psu.sample_current(6))
        return awake_a, sleep_a

    def burst_watch(sleep_a, secs):
        """True if a sustained awake burst (periodic wake) is seen."""
        high = 0
        t0 = time.time()
        while time.time() - t0 < secs:
            if rig.psu.meas_current() > sleep_a + 0.025:
                high += 1
                if high >= 3:
                    return True
            else:
                high = 0
            time.sleep(2)
        return False

    try:
        # control: periodic wake must fire within ~1 min of entry
        awake_a, sleep_a = enter_and_measure(V_SLEEPY)
        print(f"  control: asleep {mA(sleep_a)} at {V_SLEEPY} V; "
              f"watching for the 1-min periodic wake")
        assert burst_watch(sleep_a, 150), \
            "periodic wake never fired at 12.5 V"
        print("  control: periodic wake fired")

        # critical: below 11.90 V nothing may wake it but voltage
        awake_a, sleep_a = enter_and_measure(11.75)
        print(f"  critical: asleep {mA(sleep_a)} at 11.75 V; "
              f"3+ intervals of silence expected")
        assert not burst_watch(sleep_a, 210), \
            "periodic wake fired BELOW the critical floor"
        print("  critical: no periodic wake (correct)")

        rig.psu.set_voltage(V_AWAKE)
        awake = rig.psu.wait_current(lambda a: a >= sleep_a + 0.030,
                                     timeout_s=60)
        assert awake is not None, "no voltage wake from critical sleep"
        st = rig.dut.wait_up(120)
        assert st, "DUT unreachable after critical wake"
        hist = rig.dut.get("/api/restart/history") or {}
        newest = (hist.get("records") or [{}])[0]
        assert newest.get("planned_reason") == "power_wake", newest
        print("  voltage wake from critical sleep clean")
    finally:
        rig.psu.set_voltage(V_AWAKE)
        rig.dut.wait_up(120)
        rig.put_settings("sleep_manager",
                         {"periodic_wakeup": False,
                          "wakeup_interval_min": 30})


def _sim_rxlog_new(since_seq, want_id="7DF"):
    """Frames the ECU sim sniffed since seq (the DUT's own requests are
    id 7DF). The sim rides the PC's Ethernet at 192.168.8.1."""
    import urllib.request
    with urllib.request.urlopen(
            "http://192.168.8.1/api/ecu/rxlog", timeout=5) as r:
        d = json.loads(r.read().decode())
    frames = [f for f in d.get("frames", [])
              if f.get("seq", 0) > since_seq
              and f.get("id", "").upper() == want_id]
    return d.get("seq", 0), len(frames)


def s_autopid_poll(rig):
    """REAL autopid polling (the configured vehicle profile + ECU sim):
    proves (1) polling live on the wire, (2) pause_follow_sleep stops
    REQUESTS below sleep_mv (legacy disable_pid_requests parity, via
    the sim's rxlog), (3) clean sleep entry mid-poll."""
    rig.put_settings("autopid", {"enabled": True})
    time.sleep(10)

    seq, _ = _sim_rxlog_new(0)
    time.sleep(6)
    seq, n_active = _sim_rxlog_new(seq)
    print(f"  polling live: {n_active} requests on the wire in 6 s")
    assert n_active >= 3, "autopid not polling the ECU"

    # pause_follow_sleep: disable sleep so the countdown can't race the
    # pause window, drop below sleep_mv, requests must stop
    rig.put_settings("sleep_manager", {"enabled": False})
    time.sleep(8)
    rig.psu.set_voltage(V_SLEEPY)
    time.sleep(12)  # watch: below_v 5 s hold + margin
    seq, _ = _sim_rxlog_new(seq)
    time.sleep(8)
    seq, n_paused = _sim_rxlog_new(seq)
    print(f"  below sleep_mv: {n_paused} requests in 8 s "
          f"(pause_follow_sleep)")
    assert n_paused == 0, \
        f"requests continued below sleep voltage ({n_paused} in 8 s)"

    rig.psu.set_voltage(V_AWAKE)
    time.sleep(12)  # +0.3 V hysteresis + resume
    seq, _ = _sim_rxlog_new(seq)
    time.sleep(6)
    seq, n_resumed = _sim_rxlog_new(seq)
    print(f"  recovered: {n_resumed} requests in 6 s (resumed)")
    assert n_resumed >= 3, "polling did not resume after recovery"

    # sleep entry mid-poll (the legacy 'sleeps while polling' breaker)
    rig.put_settings("sleep_manager", {"enabled": True,
                                       "sleep_delay_min": 1})
    try:
        sleep_cycle(rig, "autopid_poll")
    finally:
        rig.put_settings("autopid", {"enabled": False})


def s_forced_sleep(rig):
    """`sleep test 30` over ws_cli: the forced-entry bench hook — full
    entry sequence + exactly ~30 s of naps + wake-by-test-timer reboot
    (closes bench follow-up (a): console input is dead on this rig,
    ws_cli is the input path)."""
    ip = rig.dut.base.split("//")[1]
    script = "\n".join([
        "import websocket,time",
        "ws=websocket.create_connection('ws://%s/ws/cli',timeout=5)" % ip,
        "ws.settimeout(3)",
        "ws.send('sleep test 30\\n')",
        "out=''",
        "t0=time.time()",
        "while time.time()-t0<4:",
        "    try: out+=ws.recv()",
        "    except Exception: break",
        "print(out[:200])",
    ]) + "\n"
    rc, out = rig.pi("python3 -", stdin=script)
    assert "entering test sleep" in out, f"CLI refused: {out[:150]}"

    awake_a = statistics.mean(rig.psu.sample_current(3))
    entry = rig.psu.wait_current(lambda a: a < awake_a - 0.030,
                                 timeout_s=60)
    assert entry is not None, "forced entry never slept"
    t_entry = time.time()
    print(f"  forced entry ({mA(entry)}); timed wake expected ~30 s")
    sleep_a = statistics.mean(rig.psu.sample_current(5))
    awake = rig.psu.wait_current(lambda a: a >= sleep_a + 0.030,
                                 timeout_s=75)
    assert awake is not None, "no timed wake"
    took = time.time() - t_entry
    assert 20 <= took <= 70, f"wake after {took:.0f} s (expected ~30)"
    st = rig.dut.wait_up(120)
    assert st, "DUT unreachable after timed wake"
    hist = rig.dut.get("/api/restart/history") or {}
    newest = (hist.get("records") or [{}])[0]
    assert newest.get("planned_reason") == "power_wake", newest
    print(f"  timed wake after {took:.0f} s, record clean")


def s_bootloop_guard(rig):
    """The LAST-LINE battery defense: >=3 unexpected resets below
    12.10 V must park the device asleep — with sleep DISABLED in
    settings (the guard is setting-independent, legacy parity).
    Panics are injected via `restart_tracker --panic` over ws_cli."""
    rig.put_settings("sleep_manager", {"enabled": False})
    rig.psu.set_voltage(12.05)  # DUT reads ~11.98: < 12.10 guard gate,
    time.sleep(4)               # > 11.90 critical floor

    def count():
        h = rig.dut.get("/api/restart/history") or {}
        return h.get("unexpected_resets", -1)

    def panic():
        ip = rig.dut.base.split("//")[1]
        script = (
            "import websocket,time\n"
            "ws=websocket.create_connection('ws://%s/ws/cli',timeout=4)\n"
            "ws.send('restart_tracker --panic\\n')\n"
            "time.sleep(1.5)\n" % ip)
        rig.pi("python3 - 2>/dev/null || true", stdin=script)

    c0 = count()
    assert c0 >= 0, "restart history unreachable"
    awake_a = statistics.mean(rig.psu.sample_current(4))
    print(f"  unexpected_resets={c0}, awake {mA(awake_a)}; "
          f"injecting panics at 12.05 V")
    tries = 0
    while count() < 3 and tries < 5:
        tries += 1
        panic()
        time.sleep(10)
        if tries + c0 < 3:  # guard not due yet — device comes back
            assert rig.dut.wait_up(120), \
                f"DUT lost after panic #{tries}"
    # the boot that reaches count>=3 must trip the guard (~20 s after
    # boot: 15 s grace + eval) and go to sleep
    entry = rig.psu.wait_current(lambda a: a < awake_a - 0.030,
                                 timeout_s=120)
    assert entry is not None, "guard never slept the device"
    print(f"  guard slept the device ({mA(entry)}); soaking 60 s")
    t0 = time.time()
    while time.time() - t0 < 60:
        assert rig.psu.meas_current() < awake_a - 0.020, \
            "device woke during guard sleep"
        time.sleep(2)
    assert rig.dut.get("/api/sleep", timeout_s=3) is None, \
        "DUT answering HTTP while guard-asleep"

    print("  waking at 14.0 V")
    rig.psu.set_voltage(V_AWAKE)
    awake = rig.psu.wait_current(lambda a: a >= entry + 0.030,
                                 timeout_s=60)
    assert awake is not None, "no wake on voltage recovery"
    st = rig.dut.wait_up(120)
    assert st, "DUT unreachable after guard wake"
    hist = rig.dut.get("/api/restart/history") or {}
    newest = (hist.get("records") or [{}])[0]
    assert newest.get("planned_reason") == "power_wake", newest
    print(f"  wake clean; unexpected_resets="
          f"{hist.get('unexpected_resets')}")

    # cleanup: re-enable sleep, then a REAL power cycle to wipe the
    # PSRAM-retained counter (warm resets keep it by design)
    rig.put_settings("sleep_manager", {"enabled": True})
    rig.psu.output(False)
    time.sleep(3)
    rig.psu.set_voltage(V_AWAKE)
    rig.psu.output(True)
    assert rig.dut.wait_up(180), "DUT lost after counter power-cycle"
    assert (rig.dut.get("/api/restart/history") or {}).get(
        "unexpected_resets") == 0, "counter did not clear"


class SkipScenario(Exception):
    pass


SCENARIOS = [
    ("baseline", s_baseline),
    ("mqtt", s_mqtt),
    ("net_traffic", s_net_traffic),
    ("tcp_poll", s_tcp_poll),
    ("logger_can", s_logger_can),
    ("elm_monitor", s_elm_monitor),
    ("ble_client", s_ble_client),
    ("wg_soak", s_wg_soak),
    ("ts_soak", s_ts_soak),
    ("wg_betty_soak", s_wg_betty_soak),
    ("ts_betty_soak", s_ts_betty_soak),
    ("kitchen_sink", s_kitchen_sink),
    ("autopid_poll", s_autopid_poll),
    ("forced_sleep", s_forced_sleep),
    ("periodic_critical", s_periodic_critical),
    ("bootloop_guard", s_bootloop_guard),
    ("wifi_ghost", s_wifi_ghost),   # keep LAST (offline restore path)
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--psu-port", default="COM2016")
    ap.add_argument("--bench-host", default="rpi001")
    ap.add_argument("--elm-port", default="COM6")
    ap.add_argument("--only", default="",
                    help="comma-separated scenario keys")
    args = ap.parse_args()
    only = {k.strip() for k in args.only.split(",") if k.strip()}

    rig = Rig(args)
    rig.psu.set_voltage(V_AWAKE)
    rig.psu.output(True)
    assert rig.dut.wait_up(120), "DUT offline at suite start"

    print("setting sleep_delay_min=1 for the suite ...")
    rig.put_settings("sleep_manager", {"enabled": True,
                                       "sleep_delay_min": 1})

    for key, fn in SCENARIOS:
        if only and key not in only:
            continue
        print(f"\n=== {key} ===")
        try:
            fn(rig)
            results.append((key, "PASS", ""))
        except SkipScenario as e:
            print(f"  SKIP: {e}")
            results.append((key, "SKIP", str(e)))
        except Exception as e:
            print(f"  FAIL: {e}")
            results.append((key, "FAIL", str(e)[:120]))
            rig.recover()

    print("\nrestoring sleep_delay_min=5 ...")
    try:
        rig.put_settings("sleep_manager", {"sleep_delay_min": 5})
    except Exception as e:
        print(f"restore failed: {e}")
    rig.psu.set_voltage(13.5)
    rig.psu.output(True)
    rig.psu.close()

    print("\n==== SLEEP MATRIX ====")
    for key, verdict, detail in results:
        print(f"{verdict:4s} {key:14s} {detail}")
    failed = [r for r in results if r[1] == "FAIL"]
    print("SLEEP MATRIX " + ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
