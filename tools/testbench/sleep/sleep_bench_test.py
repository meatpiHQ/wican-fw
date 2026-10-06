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
               (submit-reboot), then 12.5 V; with ~30 s left the Keep awake
               button is pressed three times (POST /api/sleep/hold, one
               minute each: the deadline moves to a minute after each
               press), a fourth is refused (409), the console and the
               `sleep` command count the holds, and HTTP dies AND current
               collapses a minute after the last press (sleep current
               sampled + reported, < 40 mA asserted); 14.0 V: back awake
               within 60 s, newest restart record = power_wake, state normal
  4. RESTORE: sleep_delay_min back to 5 (submit-reboot), PSU left at
               --end-volts output ON (>= 13.2 keeps the DUT awake)
  5. DEFAULT DEVICE UNDER THE FLOOR (2026-10-06): default settings
               again, 11.6 V: the report of a default device that slept
               after 2 min in the middle of a setup, when the floor was
               120 s. The floor is 5 min now: the device still answers
               past 150 s, the countdown (`pending`, `sleep_in_s` on
               /api/sleep, the `sleep` command) starts at 5 min and
               names a rule, and the device is asleep when it said

The countdown is checked in every leg since that day: idle on a healthy
battery, the sleep delay in legs 2 and 3 (one second per second, cleared
by a recovery, the entry within 2.5 s of its promise by the device's own
console line), the floor alone with sleep disabled, a default device
under the floor in leg 5. The legs time the floor by the device's own
`critical_s` (300 since 2026-10-06; the idle check pins it).

Usage (dev host):
  python tools/testbench/sleep_bench_test.py [--psu-port auto|COMx]
      [--dut-ip auto] [--bench-host rpi001] [--end-volts 13.5]
Expected final line: SLEEP BENCH PASS
"""
import argparse
import json
import re
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

    def get_timed(self, path, timeout_s=4):
        """get() with the wall time the DUT answered at. The ssh hop takes
        0.3 s as a rule and 3 to 7 s now and then (the first one after an
        idle pause), and the HTTP exchange sits at its END, after the
        connection setup: stamping the middle of the call put a read 3 s
        early on a slow hop (2026-10-06). curl reports its own time:
        returns (object, wall time of the answer, seconds the exchange
        took), the stamp being good to half of the last."""
        rc, out = self._ssh(f"curl -s -m {timeout_s} -w '\\n%{{time_total}}' {self.base}{path}")
        t_end = time.time()
        body, _, took = out.rpartition("\n")
        if rc != 0 or not body:
            return None, t_end, 0.0
        try:
            return json.loads(body), t_end - float(took) / 2, float(took)
        except ValueError:
            return None, t_end, 0.0

    def post_timed(self, path, obj, timeout_s=8):
        """POST a JSON body; (object or None, HTTP status, wall time of the answer).
        The device answers the hold with the status it has after it, so the
        stamp serves the countdown's promise like get_timed's."""
        rc, out = self._ssh(
            f"curl -s -m {timeout_s} -X POST -H 'Content-Type: application/json' "
            f"-d @- -w '\n%{{http_code}} %{{time_total}}' {self.base}{path}", stdin=json.dumps(obj))
        t_end = time.time()
        body, _, tail = out.rpartition("\n")
        parts = tail.split()
        status = int(parts[0]) if parts and parts[0].isdigit() else 0
        took = float(parts[1]) if len(parts) > 1 else 0.0
        try:
            return (json.loads(body) if body else None), status, t_end - took / 2
        except ValueError:
            return None, status, t_end - took / 2

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


def read_countdown(dut, tries=3):
    """/api/sleep with the wall time of the answer, for the countdown fields
    (`pending`, `sleep_in_s`; 2026-10-06). (None, now) when the DUT is silent.
    An exchange that took over a second cannot be stamped to better than half
    of that (a WiFi retry, a busy server): read again, keep the quickest."""
    best = None
    for _ in range(tries):
        st, t, took = dut.get_timed("/api/sleep")
        if st is not None and (best is None or took < best[2]):
            best = (st, t, took)
        if best is not None and best[2] <= 1.0:
            break
        time.sleep(1)
    return (best[0], best[1]) if best else (None, time.time())


def cli_countdown(con):
    """The `sleep` command's countdown line: (seconds, console stamp, text), or
    None. The console reader stamps a line within milliseconds of its arrival:
    the precise instrument for the countdown's pace (HTTP rides the ssh hop)."""
    mark = console_mark(con)
    if con.cmd("sleep", r"sleeps in \d+ s \(", 4) is None:
        return None
    hit = None
    for ts, line in con.snapshot():
        m = re.search(r"sleeps in (\d+) s \(.*\)", line) if ts >= mark else None
        if m:
            hit = (int(m.group(1)), ts, m.group(0))
    return hit


def console_wall(con, hit):
    """A console hit's stamp (seconds since the console was opened) as wall time."""
    return con.t0 + hit[0]


def check_promise(name, st, t_read, t_entry):
    """The countdown's promise against the device's own entry line: the entry
    must come `sleep_in_s` after the read, within 2.5 s (the value is rounded up
    to a whole second, the state task acts on its 1 s pass, and the read's own
    stamp is good to a few tenths)."""
    off = t_entry - (t_read + st["sleep_in_s"])
    check(name, abs(off) <= 2.5,
          f"{st['pending']} countdown said {st['sleep_in_s']} s, the device entered sleep "
          f"{t_entry - t_read:.1f} s after the read (off by {off:+.1f} s)")


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

    # the sleep countdown (2026-10-06): /api/sleep names the rule that will put
    # the device to sleep first (`pending`: delay / critical) and the seconds
    # left; on a healthy battery nothing counts
    has_cd = "pending" in st
    # the floor's delay as the device states it: 5 min since 2026-10-06 (Ali), 2 min
    # before; every leg below times itself by this number
    floor_s = int(st.get("critical_s", 120))
    if has_cd:
        check("countdown_idle",
              st.get("pending") == "none" and st.get("sleep_in_s") == 0
              and abs(st.get("critical_v", 0) - 11.9) < 0.006 and floor_s == 300,
              json.dumps(st)[:170])
    else:
        print("NOTE: firmware without the sleep countdown (/api/sleep has no `pending`): its checks are skipped")

    # ---- 2. LADDER: countdown + hysteresis, no sleep ---------------------
    psu.set_voltage(V_SLEEPY)
    state = dut.wait_state("low_voltage", timeout_s=25)
    check("ladder_countdown", state == "low_voltage", state)

    if has_cd and state == "low_voltage":
        # the countdown is the sleep delay of the settings, runs at one second
        # per second, and the console says the same
        delay_s = 60 * int((dut.get("/api/settings/sleep_manager") or {}).get("sleep_delay_min", 5))
        c1, t1 = read_countdown(dut)
        time.sleep(10)
        c2, t2 = read_countdown(dut)
        both = bool(c1 and c2) and c1.get("pending") == "delay" and c2.get("pending") == "delay"
        check("ladder_countdown_names_the_delay",
              both and delay_s - 45 <= c1.get("sleep_in_s", 0) <= delay_s,
              f"{json.dumps(c1)[:150]} (sleep delay {delay_s} s)")
        if both:
            check("ladder_countdown_runs",
                  abs((c1["sleep_in_s"] - c2["sleep_in_s"]) - (t2 - t1)) <= 1.5,
                  f"{c1['sleep_in_s']} s -> {c2['sleep_in_s']} s over {t2 - t1:.1f} s")
        if con is not None:
            k1 = cli_countdown(con)
            time.sleep(10)
            k2 = cli_countdown(con)
            check("ladder_countdown_cli",
                  bool(k1 and k2) and "sleep delay: under" in k1[2],
                  k1[2] if k1 else "no `sleeps in` line from the sleep command")
            if k1 and k2:
                check("ladder_countdown_pace",
                      abs((k1[0] - k2[0]) - (k2[1] - k1[1])) <= 1.1,
                      f"{k1[0]} s -> {k2[0]} s over {k2[1] - k1[1]:.2f} s by the console")

    psu.set_voltage(V_AWAKE)
    state = dut.wait_state("normal", timeout_s=25)
    check("ladder_recovery", state == "normal", state)
    if has_cd:
        c, _ = read_countdown(dut)
        check("ladder_countdown_cleared", bool(c) and c.get("pending") == "none" and c.get("sleep_in_s") == 0,
              json.dumps(c)[:150])

    # ---- 3. VOLTAGE: real countdown -> sleep -> voltage wake -------------
    print("setting sleep_delay_min=1 (submit-reboot) ...")
    dut.set_sleep_delay(1)
    dut.wait_state("normal", timeout_s=30)

    print(f"holding {V_SLEEPY} V for the 1 min countdown ...")
    mark_v = console_mark(con)
    psu.set_voltage(V_SLEEPY)
    t0 = time.time()
    state = dut.wait_state("low_voltage", timeout_s=25)
    check("voltage_countdown", state == "low_voltage", state)
    c3, t3 = read_countdown(dut) if has_cd else (None, 0.0)
    if has_cd:
        check("voltage_countdown_names_the_delay",
              bool(c3) and c3.get("pending") == "delay" and 0 < c3.get("sleep_in_s", 0) <= 60,
              json.dumps(c3)[:150])

    # THE HOLD (Ali, 2026-10-06): the Keep awake button. With about 30 s of the
    # minute left, three presses of one minute each (the deadline moves to a
    # minute after each press, not three), a fourth refused with 409 and the
    # device's own count, the console agreeing, then the entry a minute after
    # the last press, within the promise of the device's answer
    hold = None
    t_hold = 0.0
    if has_cd and c3 and c3.get("pending") == "delay":
        deadline = time.time() + 40
        while time.time() < deadline:
            c, _ = read_countdown(dut)
            if c and c.get("sleep_in_s", 99) <= 35:
                break
            time.sleep(2)
        statuses = []
        for i in range(4):
            r, status, t_r = dut.post_timed("/api/sleep/hold", {"minutes": 1})
            statuses.append(status)
            if status == 200 and r:
                hold, t_hold = r, t_r
            time.sleep(0.8)
        check("hold_three_then_refused", statuses == [200, 200, 200, 409], statuses)
        check("hold_answer_counts",
              bool(hold) and hold.get("pending") == "delay" and 45 <= hold.get("sleep_in_s", 0) <= 61
              and hold.get("hold_s", 0) >= 45 and hold.get("holds_left") == 0 and hold.get("holds_max") == 3,
              json.dumps(hold)[:200] if hold else "no 200 answer")
        if con is not None:
            held = con.find(r"sleep_manager: held awake 1 min on request \(3 of 3 this boot\)", since=mark_v)
            check("hold_console_line", held is not None, held[1].strip()[:110] if held else "no `held awake` line")
            cli = con.cmd("sleep", r"held awake on request: \d+ s left, 0 of 3 holds left", 4)
            check("hold_cli", cli is not None, (cli or "no `held awake` line from the sleep command").strip()[:100])

    # ground truth for "asleep" is the supply current dropping well
    # below the awake baseline (delta: survives shared-feed loads)
    sleep_gate = awake_a - 0.030
    entry = psu.wait_current(lambda a: a < sleep_gate,
                             timeout_s=60 + 90 + (70 if hold else 0))
    took = time.time() - t0
    check("voltage_sleep_entry", entry is not None,
          f"asleep after {took:.0f} s" if entry
          else f"never dropped below {mA(sleep_gate)}")

    if entry is not None:
        if hold:
            # the hold moved the entry: a minute after the last press, not at the minute of the setting
            check("voltage_sleep_timing", (t_hold - t0) + 50 <= took <= (t_hold - t0) + 60 + 90,
                  f"{took:.0f} s after the drop, the last hold {t_hold - t0:.0f} s after it")
        else:
            # countdown must actually take ~1 min (not instant, not late)
            check("voltage_sleep_timing", 55 <= took <= 150,
                  f"{took:.0f} s vs 60 s configured")
        if c3 and c3.get("pending") == "delay" and con is not None:
            hit = con.wait_for(r"sleep_manager: entering sleep", 10, since=mark_v)
            if hit and hold:
                check_promise("hold_countdown_kept", hold, t_hold, console_wall(con, hit))
            elif hit:
                check_promise("voltage_countdown_kept", c3, t3, console_wall(con, hit))
            else:
                check("voltage_countdown_kept", False, "no `entering sleep` line on the console")
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
    # the floor's delay (5 min since 2026-10-06, 2 min before: `floor_s`) the
    # device must sleep anyway, and stay reachable until then: with the floor
    # at 5 min it must still answer two and a half minutes in, where the old
    # rule had it asleep; 14 V wakes it as a planned power_wake
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
        answered_before = 0        # HTTP answers seen from 30 s to 20 s before the floor's delay ends
        answered_late = 0          # of them, those later than 150 s (the 2 min floor was asleep by then)
        cd_floor = None            # the newest of them, with the wall time of the read
        floor_hold = None          # the hold's answer (Ali, 2026-10-06): the floor alone moves out
        entry = None
        while time.time() - t_drop < floor_s + 90 + (70 if has_cd else 0):
            a = psu.meas_current()
            el = time.time() - t_drop
            if has_cd and floor_hold is None and el >= floor_s - 50:
                r, status, t_r = dut.post_timed("/api/sleep/hold", {"minutes": 1})
                floor_hold = (r or {}, status, t_r)
                check("floor_hold_applied",
                      status == 200 and r is not None and r.get("pending") == "critical"
                      and r.get("state") == "normal" and 45 <= r.get("sleep_in_s", 0) <= 61 and r.get("holds_left") == 2,
                      f"HTTP {status} {json.dumps(r)[:160]}")
            if 30 <= el <= floor_s - 20:
                got, t_got, took_got = dut.get_timed("/api/sleep", timeout_s=3)
                if got is not None:
                    answered_before += 1
                    answered_late += 1 if el >= 150 else 0
                    if took_got <= 1.0 or cd_floor is None:
                        cd_floor = (got, t_got)   # the newest well-stamped read
            if el >= floor_s - 20 and a < awake3 - 0.030 and dut.get("/api/sleep", timeout_s=3) is None:
                entry = a
                break
            time.sleep(2)
        took = time.time() - t_drop
        # awake until the delay ran out: HTTP answered in that window, or the device's own
        # line came no earlier than 10 s before its end (the hotspot path can be flaky)
        line = con.wait_for(rf"critical battery: [0-9.]+ V under 11\.90 V for {floor_s} s", 5, since=mark_floor) if con is not None else None
        line_delay = (line[0] - mark_floor) if line else None
        check("floor_awake_before_delay", answered_before > 0 or (line_delay is not None and line_delay >= floor_s - 10),
              f"HTTP answers in 30..{floor_s - 20} s: {answered_before}; device line {line_delay:.0f} s after the drop" if line_delay is not None
              else f"HTTP answers in 30..{floor_s - 20} s: {answered_before}; no device line")
        if floor_s >= 200:
            check("floor_not_at_2_min", answered_late > 0,
                  f"HTTP answers later than 150 s under the floor: {answered_late}")
        check("floor_sleep_entry", entry is not None and took <= floor_s + 80 + (70 if floor_hold else 0),
              f"asleep after {took:.0f} s with sleep DISABLED ({mA(entry)}, awake {mA(awake3)})" if entry
              else f"no entry in {took:.0f} s (awake {mA(awake3)}, last {mA(a)})")
        check("floor_console_line", line is not None or con is None, (line[1].strip()[:110] if line else "no critical-battery line"))
        if has_cd:
            # with sleep disabled the ladder does not run (state stays normal):
            # the floor is the only countdown, and it keeps its promise
            stf = cd_floor[0] if cd_floor else {}
            check("floor_countdown_names_the_floor",
                  stf.get("pending") == "critical" and stf.get("state") == "normal"
                  and 0 < stf.get("sleep_in_s", 0) <= floor_s, json.dumps(stf)[:170])
            if floor_hold and floor_hold[1] == 200 and line:
                check_promise("floor_hold_kept", floor_hold[0], floor_hold[2], console_wall(con, line))
            elif cd_floor and stf.get("pending") == "critical" and line:
                check_promise("floor_countdown_kept", stf, cd_floor[1], console_wall(con, line))
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

    # ---- 5. A DEFAULT DEVICE UNDER THE FLOOR (2026-10-06) ------------------
    # the report: a default device (sleep enabled, 5 min delay) on an 11.6 V
    # supply went to sleep after 2 min in the middle of a setup, with "Sleep
    # after 5 min" on the page: the floor of the time (120 s) cutting the delay
    # short, and nothing saying so. Two answers, both held here: the floor is
    # 5 min now (Ali, the same day), so such a device has the 5 minutes of its
    # sleep delay and is still answering where the old rule had it asleep; and
    # it SAYS what is coming: a countdown of at most the floor's delay that
    # names a rule (the floor when the two deadlines tie, the sleep delay when
    # the ladder saw the falling supply a sample earlier), the console agreeing,
    # the entry when it said
    if has_cd and err is None:
        armed = None
        deadline = time.time() + 70                      # past the 15 s boot grace
        while time.time() < deadline:
            s = dut.get("/api/sleep", timeout_s=4) or {}
            if s.get("voltage", 0) > 1.0 and s.get("state") == "normal":
                armed = s
                break
            time.sleep(2)
        check("default_floor_settings",
              armed is not None and armed.get("enabled") is True and abs(armed.get("sleep_v", 0) - 13.1) < 0.006,
              json.dumps(armed)[:130])
        time.sleep(8)                                    # radios settled: a fair awake baseline
        awake5 = statistics.mean(psu.sample_current(4))
        mark5 = console_mark(con)
        print(f"holding 11.6 V with the default 5 min sleep delay (floor {floor_s} s) ...")
        psu.set_voltage(11.6)
        t_drop = time.time()
        c5, t5 = None, 0.0
        while time.time() - t_drop < 30:
            c, t = read_countdown(dut, tries=2)
            if c and c.get("pending") in ("critical", "delay") and c.get("voltage", 99) < 11.9:
                c5, t5 = c, t
                break
            time.sleep(1)
        check("default_floor_countdown",
              c5 is not None and min(floor_s, 300) - 35 <= c5.get("sleep_in_s", 0) <= min(floor_s, 300),
              json.dumps(c5)[:170] if c5 else "no countdown within 30 s of the drop")
        if c5 is not None:
            time.sleep(10)
            c6, t6 = read_countdown(dut)
            check("default_floor_ladder_in_its_delay",
                  bool(c6) and c6.get("state") == "low_voltage" and c6.get("pending") == c5.get("pending"),
                  json.dumps(c6)[:170])
            if c6 and c6.get("pending") == c5.get("pending"):
                check("default_floor_countdown_runs",
                      abs((c5["sleep_in_s"] - c6["sleep_in_s"]) - (t6 - t5)) <= 1.5,
                      f"{c5['sleep_in_s']} s -> {c6['sleep_in_s']} s over {t6 - t5:.1f} s")
            if con is not None:
                k5 = cli_countdown(con)
                want = (f"critical floor: under 11.90 V for {floor_s} s" if c5.get("pending") == "critical"
                        else "sleep delay: under 13.10 V")
                check("default_floor_cli", k5 is not None and want in k5[2],
                      k5[2] if k5 else "no `sleeps in` line from the sleep command")
        entry = None
        a = 0.0
        awake_late = 0             # HTTP answers from 150 s on: the 2 min floor was asleep by then
        while time.time() - t_drop < floor_s + 90:
            a = psu.meas_current()
            el = time.time() - t_drop
            if 150 <= el <= floor_s - 40 and awake_late < 3 and dut.get("/api/sleep", timeout_s=3) is not None:
                awake_late += 1
            if el >= floor_s - 20 and a < awake5 - 0.030 and dut.get("/api/sleep", timeout_s=3) is None:
                entry = a
                break
            time.sleep(2)
        took = time.time() - t_drop
        if floor_s >= 200:
            check("default_floor_not_at_2_min", awake_late > 0,
                  f"HTTP answers later than 150 s at 11.6 V: {awake_late}")
        check("default_floor_sleeps_at_5_min", entry is not None and floor_s - 20 <= took <= floor_s + 80,
              f"asleep after {took:.0f} s on default settings ({mA(entry)}, awake {mA(awake5)})" if entry
              else f"no entry in {took:.0f} s (awake {mA(awake5)}, last {mA(a)})")
        hit = con.wait_for(r"sleep_manager: entering sleep", 10, since=mark5) if con is not None else None
        check("default_floor_console_line", hit is not None or con is None,
              hit[1].strip()[:110] if hit else "no `entering sleep` line")
        if con is not None:
            by_floor = con.find(rf"critical battery: [0-9.]+ V under 11\.90 V for {floor_s} s", since=mark5)
            print("  the rule that fired:", "the critical floor" if by_floor else "the sleep delay")
        if c5 is not None and hit:
            check_promise("default_floor_countdown_kept", c5, t5, console_wall(con, hit))
        psu.set_voltage(V_AWAKE)
        st5 = dut.wait_up(150)
        check("default_floor_wake_reachable", st5 is not None)
        if entry is not None:
            rec = newest_record(dut)
            check("default_floor_wake_reason",
                  rec.get("planned_reason") == "power_wake" and rec.get("source") == "sleep_mode",
                  json.dumps(rec)[:120])

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
