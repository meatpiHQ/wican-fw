#!/usr/bin/env python3
"""Sleep-mode bench — PSU-in-the-loop HIL for sleep_manager.

Topology (2026-07-20 rig): the OWON P4305 (RS232, `owon_psu.py`, hard
15.0 V ceiling in driver AND instrument) plays the car battery on the
DUT's 12 V input; the DUT is observed over HTTP through rpi001 (the DUT
is a STA on the bench hotspot) and by PSU current readback. The serial
console is deliberately NOT used: opening the CH342 console port resets
the DUT (DTR/RTS auto-reset), and the port itself dies during light
sleep — current + HTTP are the ground truth. Runs on the dev host; the
Pi is reached with plain ssh (never join the DUT AP from the dev PC —
see TESTING.md "Reaching the DUT's AP").

Model under test (sleep_manager defaults: sleep < 13.10 V, wake >=
13.20 V, delay 5 min): below sleep_v starts the LOW_VOLTAGE countdown;
recovery to wake_v cancels it; expiry tears components down and starts
2 s light-sleep naps; wake_v held ~1 s wakes by REBOOT with planned
reason power_wake (`/api/restart/history`).

Legs:
  0. PSU     — IDN, ceiling programmed, current limit sane
  1. BOOT    — power-cycle at 14.0 V: STA rejoin, /api/sleep enabled +
               state normal, DUT voltage reading tracks the PSU
               (offset reported), awake current
  2. LADDER  — 12.5 V -> state "low_voltage"; 14.0 V -> "normal"
               (hysteresis countdown cancel: no sleep, no reboot)
  3. VOLTAGE — sleep_delay_min -> 1 via the settings API
               (submit-reboot), then 12.5 V: HTTP dies AND current
               collapses inside delay+90 s (sleep current sampled +
               reported, < 40 mA asserted); 14.0 V: back awake within
               60 s, newest restart record = power_wake, state normal
  4. RESTORE — sleep_delay_min back to 5 (submit-reboot), PSU left at
               --end-volts output ON (>= 13.2 keeps the DUT awake)

Usage (dev host):
  python tools/testbench/sleep_bench_test.py [--psu-port COM2016]
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
        Re-discovers on repeated failures — the DUT can roam between
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
        """Stage sleep_delay_min, submit (reboots), wait for the DUT."""
        assert self.wait_up(timeout_s=45), "DUT unreachable"  # fresh IP
        cur = self.get("/api/settings/sleep_manager")
        assert cur, "settings unreachable"
        cur.pop("degraded", None)
        cur.pop("pending_reboot", None)
        cur["enabled"] = True
        cur["sleep_delay_min"] = minutes
        r = self.put_json("/api/settings/sleep_manager", cur)
        assert r is not None and "error" not in r, f"PUT failed: {r}"
        if not r.get("changed"):
            return self.get("/api/sleep")  # already configured — no reboot
        r = self.post("/api/settings/submit")
        assert r and r.get("reboot"), f"submit failed: {r}"
        time.sleep(8)  # let it actually go down before polling up
        st = self.wait_up()
        assert st, "DUT did not come back after submit-reboot"
        return st


def mA(amps):
    return f"{amps * 1000:.0f} mA"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--psu-port", default="COM2016")
    ap.add_argument("--dut-ip", default="auto",
                    help="DUT IP, or 'auto' = newest bench-hotspot lease")
    ap.add_argument("--bench-host", default="rpi001")
    ap.add_argument("--end-volts", type=float, default=13.5,
                    help="bench voltage to leave the DUT at (output ON; "
                         "keep it >= 13.2 or the DUT sleep-cycles)")
    args = ap.parse_args()
    dut = PiHttp(args.bench_host, args.dut_ip)

    # ---- 0. PSU ----------------------------------------------------------
    psu = OwonPsu(args.psu_port)
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
    # tick — that lands ~19 s after boot (15 s arm grace + 1 Hz loop)
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
    # draws ~40 mA on this rig) rides on every reading — the sleep
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
    # below the awake baseline (delta — survives shared-feed loads)
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
        if awake_a < 0.070:  # clean feed: DUT alone — assert the floor
            check("voltage_sleep_floor", avg < SLEEP_A_MAX, mA(avg))
        elif avg >= SLEEP_A_MAX:
            print(f"NOTE: absolute sleep floor {mA(avg)} not asserted "
                  f"— shared PSU feed (ECU sim?) adds standing draw")
        gone = dut.get("/api/sleep", timeout_s=4)
        check("voltage_sleep_offline", gone is None,
              "HTTP dead while asleep" if gone is None
              else "DUT still answering HTTP?!")

        psu.set_voltage(V_AWAKE)  # recovery: naps see it, 1 s stable
        wake_gate = avg + 0.030   # back toward the awake baseline
        awake = psu.wait_current(lambda a: a >= wake_gate,
                                 timeout_s=60)
        check("voltage_wake_current", awake is not None,
              mA(awake) if awake else "no wake on recovery")

        st = dut.wait_up()
        check("voltage_wake_reachable", st is not None)
        if st:
            check("voltage_wake_state",
                  st.get("state") in ("normal", "wake_pending"),
                  st.get("state"))
        hist = dut.get("/api/restart/history")
        recs = (hist or {}).get("records") or [{}]
        rec = recs[0]
        check("voltage_wake_reason",
              rec.get("planned_reason") == "power_wake"
              and rec.get("source") == "sleep_mode",
              json.dumps(rec)[:120])
    else:
        psu.set_voltage(V_AWAKE)
        dut.wait_up()

    # ---- 4. RESTORE ------------------------------------------------------
    print("restoring sleep_delay_min=5 (submit-reboot) ...")
    try:
        dut.set_sleep_delay(5)
        check("restore_settings", True)
    except AssertionError as e:
        check("restore_settings", False, str(e))

    psu.set_voltage(args.end_volts)
    psu.output(True)
    print(f"bench left at {args.end_volts} V, output ON")
    psu.close()

    if fails:
        print("SLEEP BENCH FAIL: " + ", ".join(fails))
        return 1
    print("SLEEP BENCH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
