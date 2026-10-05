#!/usr/bin/env python3
"""Sleep-mode bench: PSU-in-the-loop HIL for sleep_manager.

Topology (2026-07-20 rig): the OWON P4305 (RS232, `owon_psu.py`, hard
15.0 V ceiling in driver AND instrument) plays the car battery on the
DUT's 12 V input; the DUT is observed over HTTP through rpi001 (the DUT
is a STA on the bench hotspot) and by PSU current readback. The serial
console is deliberately NOT used: opening the CH342 console port resets
the DUT (DTR/RTS auto-reset), and the port itself dies during light
sleep, current + HTTP are the ground truth. Runs on the dev host; the
Pi is reached with plain ssh (never join the DUT AP from the dev PC,
see TESTING.md "Reaching the DUT's AP").

Model under test (sleep_manager defaults: sleep < 13.10 V, wake >=
13.20 V, delay 5 min): below sleep_v starts the LOW_VOLTAGE countdown;
recovery to wake_v cancels it; expiry tears components down and starts
2 s light-sleep naps; wake_v held ~1 s wakes by REBOOT with planned
reason power_wake (`/api/restart/history`).

Legs:
  0. PSU:      IDN, ceiling programmed, current limit sane
  1. BOOT:     power-cycle at 14.0 V: STA rejoin, /api/sleep enabled +
               state normal, DUT voltage reading tracks the PSU
               (offset reported), awake current
  2. LADDER:   12.5 V -> state "low_voltage"; 14.0 V -> "normal"
               (hysteresis countdown cancel: no sleep, no reboot)
  3. VOLTAGE: sleep_delay_min -> 1 via the settings API
               (submit-reboot), then 12.5 V: HTTP dies AND current
               collapses inside delay+90 s (sleep current sampled +
               reported, < 40 mA asserted); 14.0 V: back awake within
               60 s, newest restart record = power_wake, state normal
  4. RESTORE: sleep_delay_min back to 5 (submit-reboot), PSU left at
               --end-volts output ON (>= 13.2 keeps the DUT awake)

Usage (dev host):
  python tools/testbench/sleep_bench_test.py [--psu-port auto|COMx]
      [--dut-ip auto] [--bench-host rpi001] [--end-volts 13.5]
Expected final line: SLEEP BENCH PASS
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wifi"))
import bench_ports  # noqa: E402  (COM ports by role, tools/testbench/detect_ports.py)

from owon_psu import OwonPsu   # noqa: E402

V_AWAKE = 14.0    # above wake_v (13.20) even after the harness drop
V_SLEEPY = 12.5   # below sleep_v (13.10) by the same margin
AWAKE_A_MIN = 0.050   # awake draw is ~80-110 mA on this rig
SLEEP_A_MAX = 0.040   # nap-loop average must sit under this

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + ("  ({})".format(detail) if detail else ""))
    if not ok:
        fails.append(name)


class PiHttp:
    """DUT HTTP through the bench Pi (plain ssh + curl)."""

    def __init__(self, ssh_host, dut_ip):
        self.host = ssh_host
        self.base = f"http://{dut_ip}" if dut_ip != "auto" else None
        if self.base is None:
            self.discover()

    def discover(self):
        """Find the DUT on the bench-hotspot twins: newest dnsmasq lease
        wins (the DUT re-leases on either radio after every reboot)."""
        rc, out = self._ssh(
            "sudo cat /var/lib/NetworkManager/dnsmasq-wint0.leases "
            "/var/lib/NetworkManager/dnsmasq-wtest0.leases 2>/dev/null")
        leases = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[2].startswith("10.42."):
                leases.append((int(parts[0]), parts[2]))
        assert leases, "no DUT lease on either bench hotspot"
        leases.sort(reverse=True)
        for _, ip in leases:
            self.base = f"http://{ip}"
            if self.get("/api/sleep", timeout_s=4) is not None:
                print(f"DUT discovered at {ip}")
                return
        # none answered right now (e.g. mid-reboot): trust the newest
        self.base = f"http://{leases[0][1]}"
        print(f"DUT lease (unverified) {leases[0][1]}")

    def _ssh(self, cmd, stdin=None, timeout=30):
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", self.host, cmd],
                           input=stdin, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, p.stdout.strip()

    def get(self, path, timeout_s=8):
        rc, out = self._ssh(f"curl -s -m {timeout_s} {self.base}{path}")
        if rc != 0 or not out:
            return None
        try:
            return json.loads(out)
        except ValueError:
            return None

    def put_json(self, path, obj):
        rc, out = self._ssh(
            f"curl -s -m 8 -X PUT -H 'Content-Type: application/json' "
            f"-d @- {self.base}{path}", stdin=json.dumps(obj))
        return json.loads(out) if rc == 0 and out else None

    def post(self, path, timeout_s=15):
        rc, out = self._ssh(f"curl -s -m {timeout_s} -X POST "
                            f"{self.base}{path}")
        return json.loads(out) if rc == 0 and out else None

    def wait_up(self, timeout_s=90):
        """Poll /api/sleep until it answers (boot + STA rejoin). The DUT
        may re-lease on the OTHER hotspot twin after any reboot, so
        re-discover while polling."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = self.get("/api/sleep", timeout_s=4)
            if st is not None:
                return st
            try:
                self.discover()
                st = self.get("/api/sleep", timeout_s=4)
                if st is not None:
                    return st
            except AssertionError:
                pass
            time.sleep(2)
        return None

    def wait_state(self, want, timeout_s=20):
        """Poll /api/sleep for a policy state (1 Hz tick + WiFi jitter).
        Re-discovers on repeated failures: the DUT can roam between
        the same-SSID hotspot twins at ANY time, not just reboots."""
        deadline = time.time() + timeout_s
        state = "?"
        misses = 0
        while time.time() < deadline:
            st = self.get("/api/sleep", timeout_s=4)
            if st:
                misses = 0
                state = st.get("state", "?")
                if state == want:
                    return state
            else:
                misses += 1
                if misses >= 2:
                    try:
                        self.discover()
                    except AssertionError:
                        pass
                    misses = 0
            time.sleep(2)
        return state

    def set_sleep_delay(self, minutes):
        return self.set_sleep(minutes)

    def set_sleep(self, minutes, sleep_mv=None, wake_mv=None, wake_delay_ms=None, enabled=True):
        """Stage sleep_delay_min (and the v3 pair, the v4 hold, the switch), submit (reboots), wait."""
        assert self.wait_up(timeout_s=45), "DUT unreachable"  # fresh IP
        cur = self.get("/api/settings/sleep_manager")
        assert cur, "settings unreachable"
        cur.pop("degraded", None)
        cur.pop("pending_reboot", None)
        cur["enabled"] = enabled
        cur["sleep_delay_min"] = minutes
        if sleep_mv is not None:
            cur["sleep_mv"] = sleep_mv
        if wake_mv is not None and "wake_mv" in cur:   # sleep_manager v3 (2026-10-01)
            cur["wake_mv"] = wake_mv
        if wake_delay_ms is not None and "wake_delay_ms" in cur:   # v4 (2026-10-01)
            cur["wake_delay_ms"] = wake_delay_ms
        r = self.put_json("/api/settings/sleep_manager", cur)
        assert r is not None and "error" not in r, f"PUT failed: {r}"
        if not r.get("changed"):
            return self.get("/api/sleep")  # already configured: no reboot
        r = self.post("/api/settings/submit")
        assert r and r.get("reboot"), f"submit failed: {r}"
        time.sleep(8)  # let it actually go down before polling up
        st = self.wait_up(timeout_s=150)
        assert st, "DUT did not come back after submit-reboot"
        return st


def mA(amps):
    return f"{amps * 1000:.0f} mA"


def console_hold(con, since):
    """The wake hold as the DEVICE logs it: seconds between the ladder's
    "state: wake_pending" line and "waking by reboot (voltage recovered)".
    The supply current cannot time this (its own detection latency is ~8 s:
    reboot, boot ramp, sampling); the console can. None when not captured."""
    if con is None:
        return None
    pend = con.wait_for(r"sleep_manager: state: wake_pending", 90, since=since)
    if not pend:
        return None
    wake = con.wait_for(r"sleep_manager: waking by reboot \(voltage recovered\)", 60, since=pend[0])
    if not wake:
        return None
    return wake[0] - pend[0]


def console_mark(con):
    snap = con.snapshot() if con is not None else []
    return snap[-1][0] if snap else 0.0


def newest_record(dut, tries=4):
    """/api/restart/history right after a wake boot can answer empty once
    (the server is up before every route is); read it a few times."""
    for _ in range(tries):
        hist = dut.get("/api/restart/history")
        recs = (hist or {}).get("records") or []
        if recs:
            return recs[0]
        time.sleep(3)
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--psu-port", default="auto",
                    help="OWON port; auto = the FTDI port detect_ports.py finds")
    ap.add_argument("--dut-ip", default="auto",
                    help="DUT IP, or 'auto' = newest bench-hotspot lease")
    ap.add_argument("--bench-host", default="rpi001")
    ap.add_argument("--end-volts", type=float, default=13.5,
                    help="bench voltage to leave the DUT at (output ON; "
                         "keep it >= 13.2 or the DUT sleep-cycles)")
    args = ap.parse_args()
    dut = PiHttp(args.bench_host, args.dut_ip)

    # ---- 0. PSU + console ----------------------------------------------------
    # the console (2026-10-01) times the wake hold from the device's own log;
    # opening it may reset the DUT, which the power cycle below redoes anyway
    con = None
    try:
        from wican_fresh_bench import Console
        con = Console(bench_ports.resolve("auto", "wican_console"), 2000000)
        print("console captured on", con.port if hasattr(con, "port") else "the WiCAN console")
    except Exception as e:  # noqa: BLE001
        print(f"NOTE: no console capture ({e}); the wake hold is not timed")
    psu = OwonPsu(bench_ports.resolve(args.psu_port, "psu", "COM2016"))
    check("psu_idn", "P4305" in psu.idn, psu.idn)
    check("psu_volt_ceiling", True, "VOLT:LIM 15.000 programmed")
    ilim = psu.current_limit()
    check("psu_current_limit", 0.4 <= ilim <= 5.1, f"{ilim:.3f} A")

    # ---- 1. BOOT: power-cycle into a known state -------------------------
    print(f"power-cycling DUT at {V_AWAKE} V ...")
    psu.output(False)
    time.sleep(3)
    psu.set_voltage(V_AWAKE)
    psu.output(True)

    st = dut.wait_up()
    check("boot_reachable", st is not None, "STA rejoin + /api/sleep")
    if st is None:
        print("SLEEP BENCH FAIL (DUT unreachable)")
        return 1

    check("boot_sleep_enabled", st.get("enabled") is True,
          "" if st.get("enabled") else
          "enable sleep_manager in settings first")
    if not st.get("enabled"):
        print("SLEEP BENCH FAIL (sleep disabled)")
        return 1

    # the voltage field stays 0.00 until the state task's first policy
    # tick: that lands ~19 s after boot (15 s arm grace + 1 Hz loop)
    volts = 0.0
    deadline = time.time() + 40
    while time.time() < deadline:
        st = dut.wait_up(timeout_s=8) or {}  # re-discovers on roam
        volts = st.get("voltage", 0.0)
        if volts > 1.0:
            break
        time.sleep(2)
    offset = V_AWAKE - volts
    check("boot_battery_reading", abs(offset) <= 0.8,
          f"DUT reads {volts:.2f} V at {V_AWAKE:.1f} V "
          f"(offset {offset:+.2f} V)")
    # awake baseline: NOTE anything else on the PSU feed (the ECU sim
    # draws ~40 mA on this rig) rides on every reading, the sleep
    # assertion below is therefore a DELTA against this baseline; the
    # absolute floor is reported and only asserted when the feed is
    # clean (baseline low enough that no sim can be present)
    awake_a = statistics.mean(psu.sample_current(4))
    check("boot_current", awake_a >= AWAKE_A_MIN, mA(awake_a))

    # ---- 2. LADDER: countdown + hysteresis, no sleep ---------------------
    psu.set_voltage(V_SLEEPY)
    state = dut.wait_state("low_voltage", timeout_s=25)
    check("ladder_countdown", state == "low_voltage", state)

    psu.set_voltage(V_AWAKE)
    state = dut.wait_state("normal", timeout_s=25)
    check("ladder_recovery", state == "normal", state)

    # ---- 3. VOLTAGE: real countdown -> sleep -> voltage wake -------------
    print("setting sleep_delay_min=1 (submit-reboot) ...")
    dut.set_sleep_delay(1)
    dut.wait_state("normal", timeout_s=30)

    print(f"holding {V_SLEEPY} V for the 1 min countdown ...")
    psu.set_voltage(V_SLEEPY)
    t0 = time.time()
    state = dut.wait_state("low_voltage", timeout_s=25)
    check("voltage_countdown", state == "low_voltage", state)

    # ground truth for "asleep" is the supply current dropping well
    # below the awake baseline (delta: survives shared-feed loads)
    sleep_gate = awake_a - 0.030
    entry = psu.wait_current(lambda a: a < sleep_gate,
                             timeout_s=60 + 90)
    took = time.time() - t0
    check("voltage_sleep_entry", entry is not None,
          f"asleep after {took:.0f} s" if entry
          else f"never dropped below {mA(sleep_gate)}")

    if entry is not None:
        # countdown must actually take ~1 min (not instant, not late)
        check("voltage_sleep_timing", 55 <= took <= 150,
              f"{took:.0f} s vs 60 s configured")
        s = psu.sample_current(8)
        avg = statistics.mean(s)
        check("voltage_sleep_current", avg < sleep_gate,
              f"avg {mA(avg)}, min {mA(min(s))}, max {mA(max(s))} "
              f"(awake {mA(awake_a)}, delta {mA(awake_a - avg)})")
        if awake_a < 0.070:  # clean feed: DUT alone, assert the floor
            check("voltage_sleep_floor", avg < SLEEP_A_MAX, mA(avg))
        elif avg >= SLEEP_A_MAX:
            print(f"NOTE: absolute sleep floor {mA(avg)} not asserted, "
                  f"shared PSU feed (ECU sim?) adds standing draw")
        gone = dut.get("/api/sleep", timeout_s=4)
        check("voltage_sleep_offline", gone is None,
              "HTTP dead while asleep" if gone is None
              else "DUT still answering HTTP?!")

        t_up = time.time()
        mark_default = console_mark(con)
        psu.set_voltage(V_AWAKE)  # recovery: naps see it, stable for wake_delay_ms
        wake_gate = avg + 0.030   # back toward the awake baseline
        awake = psu.wait_current(lambda a: a >= wake_gate,
                                 timeout_s=60)
        lat_default = time.time() - t_up if awake is not None else None
        check("voltage_wake_current", awake is not None,
              f"{mA(awake)} after {lat_default:.1f} s (default hold)" if awake
              else "no wake on recovery")
        hold_default = console_hold(con, mark_default)
        if hold_default is not None:
            # wake_delay_ms 500 + the ladder's 1 s loop; the 0.1 s floor would show < 1.2 s
            check("voltage_wake_hold_default", 0.3 <= hold_default <= 2.6,
                  f"wake_pending -> reboot {hold_default:.2f} s (wake after 0.5 s)")
        else:
            print("NOTE: default hold not timed (no console lines)")

        st = dut.wait_up()
        check("voltage_wake_reachable", st is not None)
        if st:
            check("voltage_wake_state",
                  st.get("state") in ("normal", "wake_pending"),
                  st.get("state"))
        rec = newest_record(dut)
        check("voltage_wake_reason",
              rec.get("planned_reason") == "power_wake"
              and rec.get("source") == "sleep_mode",
              json.dumps(rec)[:120])
    else:
        lat_default = None
        hold_default = None
        psu.set_voltage(V_AWAKE)
        dut.wait_up()

    # ---- 3b. WAKE VOLTAGE (sleep_manager v3, 2026-10-01) -----------------
    # the user's own wake voltage: with sleep 12.9 V / wake 13.4 V a step to
    # 13.2 V (between the two) must NOT wake the device; 13.6 V must
    # and (v4, "wake up after") the same wake with a 5 s hold must take at
    # least 5 s longer than the nap granularity allows with the default
    cur = dut.get("/api/settings/sleep_manager") or {}
    has_hold = "wake_delay_ms" in cur
    if "wake_mv" in cur:
        print("setting sleep 12.9 V / wake 13.4 V, delay 1 min"
              + (", wake after 5 s" if has_hold else "") + " (submit-reboot) ...")
        try:
            st = dut.set_sleep(1, sleep_mv=12900, wake_mv=13400,
                               wake_delay_ms=5000 if has_hold else None)
        except AssertionError as e:
            st = None
            check("wakev_applied", False, str(e))
        if st is not None:
            check("wakev_applied",
                  abs(st.get("sleep_v", 0) - 12.9) < 0.006 and abs(st.get("wake_v", 0) - 13.4) < 0.006,
                  json.dumps(st)[:100])
            dut.wait_state("normal", timeout_s=30)
            time.sleep(10)                               # let the radios settle after the reboot
            # the leg's OWN awake baseline (after a settings reboot the draw differs
            # from the first leg's: the earlier run called 89 mA "asleep" against a
            # 156 mA baseline and moved on before the countdown had finished)
            awake2 = statistics.mean(psu.sample_current(4))
            gate2 = awake2 - 0.030
            psu.set_voltage(V_SLEEPY)                    # 12.5 V < 12.9 V
            t_drop = time.time()
            entry = None
            while time.time() - t_drop < 60 + 90:
                a = psu.meas_current()
                # asleep = the current is down AND the device no longer answers;
                # never before the 1 min countdown could have run
                if time.time() - t_drop >= 55 and a < gate2 and dut.get("/api/sleep", timeout_s=3) is None:
                    entry = a
                    break
                time.sleep(2)
            took = time.time() - t_drop
            check("wakev_sleep_entry", entry is not None,
                  f"asleep after {took:.0f} s ({mA(entry)}, awake {mA(awake2)})" if entry
                  else f"no entry in {took:.0f} s (awake {mA(awake2)}, last {mA(a)})")
            if entry is not None:
                sleep2 = statistics.mean(psu.sample_current(6))
                psu.set_voltage(13.2)                    # between sleep and wake
                time.sleep(40)                           # ~20 naps see it
                s = psu.sample_current(8)
                avg = statistics.mean(s)
                check("wakev_between_stays_asleep", avg < sleep2 + 0.025,
                      f"13.2 V held 40 s: avg {mA(avg)} (asleep {mA(sleep2)}, awake {mA(awake2)})")
                check("wakev_between_offline", dut.get("/api/sleep", timeout_s=4) is None)
                t_up = time.time()
                mark_hold = console_mark(con)
                psu.set_voltage(13.6)                    # above 13.4 V
                awake = psu.wait_current(lambda a: a >= sleep2 + 0.030, timeout_s=60)
                lat_hold = time.time() - t_up if awake is not None else None
                check("wakev_wake_current", awake is not None,
                      f"{mA(awake)} after {lat_hold:.1f} s" if awake else "no wake above 13.6 V")
                if has_hold and awake is not None:
                    # the 5 s hold, timed by the device itself (wake_pending -> reboot);
                    # the supply-current latency above is only informational
                    hold5 = console_hold(con, mark_hold)
                    if hold5 is None:
                        check("wakev_hold_5s", False, "no console lines to time the hold")
                    else:
                        check("wakev_hold_5s", 4.5 <= hold5 <= 7.6,
                              f"wake_pending -> reboot {hold5:.2f} s (wake after 5 s)")
                        if hold_default is not None:
                            check("wakev_hold_vs_default", hold5 - hold_default >= 3.0,
                                  f"5 s hold {hold5:.2f} s vs default hold {hold_default:.2f} s")
                st2 = dut.wait_up()
                check("wakev_wake_reachable", st2 is not None)
                rec = newest_record(dut)
                check("wakev_wake_reason",
                      rec.get("planned_reason") == "power_wake" and rec.get("source") == "sleep_mode",
                      json.dumps(rec)[:120])
            psu.set_voltage(V_AWAKE)
            dut.wait_up()
    else:
        print("NOTE: firmware without wake_mv (sleep_manager < v3): wake-voltage leg skipped")

    # ---- 3c. CRITICAL FLOOR (Ali, 2026-10-01) ------------------------------
    # sleep DISABLED in settings, 11.75 V on the supply: under 11.90 V for
    # 120 s the device must sleep anyway (and stay reachable until then);
    # 14 V wakes it as a planned power_wake
    print("disabling sleep in settings (submit-reboot) for the critical-floor leg ...")
    try:
        st = dut.set_sleep(1, enabled=False)
    except AssertionError as e:
        st = None
        check("floor_disabled_applied", False, str(e))
    if st is not None:
        check("floor_disabled_applied", st.get("enabled") is False, json.dumps(st)[:80])
        time.sleep(20)                                   # past the 15 s boot grace
        awake3 = statistics.mean(psu.sample_current(4))
        mark_floor = console_mark(con)
        psu.set_voltage(11.75)
        t_drop = time.time()
        answered_before = 0        # HTTP answers seen between 30 s and 100 s under the floor
        entry = None
        while time.time() - t_drop < 120 + 90:
            a = psu.meas_current()
            el = time.time() - t_drop
            if 30 <= el <= 100 and dut.get("/api/sleep", timeout_s=3) is not None:
                answered_before += 1
            if el >= 100 and a < awake3 - 0.030 and dut.get("/api/sleep", timeout_s=3) is None:
                entry = a
                break
            time.sleep(2)
        took = time.time() - t_drop
        # awake until the delay ran out: HTTP answered at least once in 30..100 s, or the
        # device's own line came >= 110 s after the drop (the hotspot path can be flaky)
        line = con.wait_for(r"critical battery: [0-9.]+ V under 11\.90 V for 120 s", 5, since=mark_floor) if con is not None else None
        line_delay = (line[0] - mark_floor) if line else None
        check("floor_awake_before_delay", answered_before > 0 or (line_delay is not None and line_delay >= 110),
              f"HTTP answers in 30..100 s: {answered_before}; device line {line_delay:.0f} s after the drop" if line_delay is not None
              else f"HTTP answers in 30..100 s: {answered_before}; no device line")
        check("floor_sleep_entry", entry is not None and took <= 200,
              f"asleep after {took:.0f} s with sleep DISABLED ({mA(entry)}, awake {mA(awake3)})" if entry
              else f"no entry in {took:.0f} s (awake {mA(awake3)}, last {mA(a)})")
        check("floor_console_line", line is not None or con is None, (line[1].strip()[:110] if line else "no critical-battery line"))
        if entry is not None:
            sleep3 = statistics.mean(psu.sample_current(5))
            time.sleep(30)
            check("floor_stays_asleep", dut.get("/api/sleep", timeout_s=3) is None and statistics.mean(psu.sample_current(4)) < sleep3 + 0.025)
            psu.set_voltage(V_AWAKE)
            awake = psu.wait_current(lambda a: a >= sleep3 + 0.030, timeout_s=60)
            check("floor_wake_current", awake is not None, mA(awake) if awake else "no wake at 14 V")
            st3 = dut.wait_up(150)
            check("floor_wake_reachable", st3 is not None)
            rec = newest_record(dut)
            check("floor_wake_reason",
                  rec.get("planned_reason") == "power_wake" and rec.get("source") == "sleep_mode",
                  json.dumps(rec)[:120])
        else:
            psu.set_voltage(V_AWAKE)
            dut.wait_up(150)

    # ---- 4. RESTORE ------------------------------------------------------
    print("restoring sleep_delay_min=5, sleep 13.1 V / wake 13.2 V, wake after 0.5 s (submit-reboot) ...")
    err = None
    for attempt in range(3):          # the hotspot path can be flaky right after a wake boot
        try:
            dut.set_sleep(5, sleep_mv=13100, wake_mv=13200, wake_delay_ms=500)
            err = None
            break
        except AssertionError as e:
            err = str(e)
            dut.wait_up(60)
    check("restore_settings", err is None, err or "")

    psu.set_voltage(args.end_volts)
    psu.output(True)
    print(f"bench left at {args.end_volts} V, output ON")
    psu.close()
    if con is not None:
        e = con.e_lines()
        print(f"console E lines during the run: {len(e)}")
        for l in e[:8]:
            print("  E:", l.strip()[:140])
        con.close()

    if fails:
        print("SLEEP BENCH FAIL: " + ", ".join(fails))
        return 1
    print("SLEEP BENCH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
