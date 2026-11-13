"""Stop Tailscale while its relay misbehaves: TS DERP STOP PASS.

Called by test.ps1 tspublic stop-dial while its disposable tailnet is up.

What is judged is ownership, read from the console capture test.ps1 keeps
beside the stage (--console): the client is never destroyed, and no task of
a new client is started, while a task of the old client still runs. Each of
the client's four tasks logs its start and its end, the client logs
`Destroyed`. A stop may miss its 30-second deadline (one relay dial into a
dead uplink blocks for about a minute): that is counted and reported, the
client must then be kept until the task has ended. How long a stop takes is
reported, never judged.

Leg A, the stalled dial: the DUT's established TCP/443 connections are reset
on the Pi and its new dials dropped, then a Wi-Fi trial takes the uplink
away while the relay task retries. The VPN must come back and nothing may
crash in the 40 s behind it.

Leg B, the uplink lost with the relay up: the same Wi-Fi trial, twice. The
relay's own read error usually closes the connection a moment before the
stop arrives.

Leg C, the control connection lost with the relay up: the DUT's connection
to the control server is reset on the Pi and its new dials dropped. The relay
stays connected, the client reports itself not connected, and after 30 s of
that the VPN manager stops it: a stop that finds the relay connected, every
time. The relay task must close that connection itself as it ends.

After every stop of legs B and C the relay task must have said how much of
its stack it never used, and over those stops no memory may stay behind.

Only this DUT's forwarded TCP traffic to port 443 (leg A) and to the control
server (leg C) is disturbed. Firewall rules are removed in finally; the
Wi-Fi trial does not persist any settings.

The device's log ring cannot stand in for the capture: it holds 16 KB and a
Tailscale reconnect rewrites it within seconds (the relay map alone is
longer), so the client's lines are gone by the time the VPN is back.
"""
import argparse
import ipaddress
import json
import os
import re
import shlex
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from benchlib import pi, DUT_REBOOT_WINDOW

TRIAL = {"ssid": "wican-bench-nowhere", "password": "nothing-to-join-1", "channel": 1}
MISSED = "a task is still running 30000 ms after the stop request"
EXIT_LINE = re.compile(
    r"\((\d+)\) ml_derp: DERP I/O task exiting \(stack: (\d+) B never used\)")
# the client's four tasks: how each says it started, and how it says it ended
TASKS = {
    "net_io": ("ml_net_io: Net I/O task started", ("ml_net_io: Net I/O task exiting",)),
    "derp": ("ml_derp: DERP I/O task started", ("ml_derp: DERP I/O task exiting",)),
    "coord": ("ml_coord: Coord task started",
              ("ml_coord: Coord task exiting",
               "ml_coord: Shutdown requested before WiFi, exiting")),
    "wg_mgr": ("ml_wg_mgr: WG Manager task started",
               ("ml_wg_mgr: WG Manager task exiting",
                "ml_wg_mgr: Shutdown requested before registration, exiting")),
}
BOOT = "main_task: Started on CPU"     # a restart: nothing runs from before it
UPLINK_LOSSES = 2
CONTROL_LOSSES = 2
CONTROL_LOSS_WINDOW = 240  # s; the client's control watchdog (120 s) at worst,
                           # then the VPN manager's 30 s of a silent tunnel
VPN_BACK_WINDOW = 300      # s; a stop behind a dial into a dead uplink takes
                           # two deadlines before the client can go
SETTLE = 8                 # s behind "relay back" before the memory is read
STACK_FLOOR = 1024         # B; under it a PSRAM stack is a bug (the standard)
PSRAM_SLACK = 16384        # B; one relay's TLS context alone is more than this
INTERNAL_SLACK = 4096      # B; the soak's own figure for "flat"


def remote(command):
    rc, text = pi(command)
    if rc:
        raise OSError(f"Pi command failed ({rc}): {text}")
    return text


def ownership(logged):
    """Walk the client's lines in order. Returns (violations, deadlines
    missed, clients destroyed). A violation: `Destroyed` while a task of that
    client has not said it ended, or a task started while its predecessor
    still runs."""
    running = set()
    violations = []
    missed = destroyed = 0
    for line in logged.splitlines():
        if BOOT in line:
            running.clear()
            continue
        for name, (started, ended) in TASKS.items():
            if started in line:
                if name in running:
                    violations.append(f"a second {name} task was started over a live one: "
                                      + line.strip())
                running.add(name)
            elif any(word in line for word in ended):
                running.discard(name)
        if MISSED in line:
            missed += 1
        elif "microlink: Destroyed" in line:
            destroyed += 1
            if running:
                violations.append("the client was destroyed while its "
                                  f"{', '.join(sorted(running))} task still ran: "
                                  + line.strip())
    return violations, missed, destroyed


class Bench:
    def __init__(self, host, console):
        self.host = str(ipaddress.IPv4Address(host))
        self.console = console
        self.after_stop = []    # (PSRAM, internal) free behind each stop of B and C

    def api(self, path, body=None, raw=False):
        command = f"curl -fsS -m 5 http://{self.host}{path}"
        if body is not None:
            command += " -H Content-Type:application/json -d " + shlex.quote(json.dumps(body))
        text = remote(command)
        return text if raw else json.loads(text)

    @staticmethod
    def wait_for(label, predicate, limit):
        deadline = time.monotonic() + limit
        last = "condition not met"
        while time.monotonic() < deadline:
            try:
                value = predicate()
                if value:
                    return value
            except (OSError, ValueError) as exc:
                last = str(exc)
            time.sleep(1)
        raise RuntimeError(f"{label}: {last}")

    def device_log(self):
        with open(self.console, encoding="utf-8", errors="replace") as capture:
            return capture.read()

    def last_exit(self):
        """(log time in ms, unused stack in B) of the relay task's last end."""
        found = EXIT_LINE.findall(self.device_log())
        return (int(found[-1][0]), int(found[-1][1])) if found else (0, None)

    def owned(self, label):
        """The ownership rule over everything logged so far."""
        time.sleep(2)   # the capture is a line or two behind the device
        violations, missed, destroyed = ownership(self.device_log())
        if violations:
            raise RuntimeError(f"{label}: " + "; ".join(violations))
        return missed, destroyed

    def memory_free(self):
        """(PSRAM, internal) free: the largest of three samples each, a
        request in flight holds buffers of its own."""
        samples = []
        for _ in range(3):
            memory = self.api("/api/status")["memory"]
            samples.append((memory["psram"]["free"], memory["internal"]["free"]))
            time.sleep(1)
        return max(s[0] for s in samples), max(s[1] for s in samples)

    def task_headroom(self, name):
        """A task's unused stack. The task walk can miss a task the other
        core moves meanwhile: asked again, never read as zero."""
        for _ in range(3):
            for task in self.api("/api/status/tasks")["tasks"]:
                if task["name"] == name:
                    return task["stack_hw"]
            time.sleep(1)
        raise RuntimeError(f"{name} absent from three task snapshots")

    def firewall(self, match):
        """A private nft table on the Pi: the DUT's established connections
        that match are reset, its new ones dropped. Returns the table name."""
        table = "wican_derp_" + uuid.uuid4().hex[:12]
        rules = (
            f"add table inet {table}\n"
            f"add chain inet {table} forward "
            "{ type filter hook forward priority -1; policy accept; }\n"
            f"add rule inet {table} forward ip saddr {self.host} {match} "
            "ct state established reject with tcp reset\n"
            f"add rule inet {table} forward ip saddr {self.host} {match} drop\n"
        )
        print(f"Firewall scope: DUT {self.host}, {match}, nft table {table}", flush=True)
        remote("printf %s " + shlex.quote(rules) + " | sudo -n nft -f -")
        return table

    def back_up(self, connects_before, boots, what):
        def returned():
            status = self.api("/api/status")
            if status["boot_count"] != boots:
                raise RuntimeError(f"DUT restarted behind {what}: "
                                   f"{boots} -> {status['boot_count']}")
            return self.api("/api/vpn")["connects"] > connects_before

        started = time.monotonic()
        self.wait_for(f"VPN back after {what}", returned, VPN_BACK_WINDOW)
        self.wait_for(f"relay back after {what}",
                      lambda: self.api("/api/vpn")["derp"], DUT_REBOOT_WINDOW)
        return time.monotonic() - started

    def relay_task_ended(self, stamp_before, label):
        missed, _ = self.owned(label)
        stamp, unused = self.last_exit()
        if stamp <= stamp_before:
            raise RuntimeError(f"{label}: the relay task's end and its stack figure "
                               "are not in the console capture")
        print(f"{label}: the relay task ended with {unused} B of its stack never used "
              f"(stop deadlines missed so far: {missed})", flush=True)
        if unused < STACK_FLOOR:
            raise RuntimeError(f"the relay task ended with {unused} B of stack left")
        time.sleep(SETTLE)
        psram, internal = self.memory_free()
        self.after_stop.append((psram, internal))
        print(f"{label}: PSRAM free {psram} B, internal {internal} B with the new "
              "client up", flush=True)

    # ---- leg A: the uplink goes while the relay task retries a dropped dial
    def stalled_dial(self, before):
        initial = self.api("/api/vpn")
        table = None
        failure = None
        try:
            table = self.firewall("tcp dport 443")
            print("Waiting for the existing DERP connection to reset; new dials are dropped",
                  flush=True)
            self.wait_for("DERP disconnect", lambda: not self.api("/api/vpn")["derp"], 100)
            print("DERP disconnected; triggering the uplink-loss teardown during its retry",
                  flush=True)
            self.api("/api/wifi/try", TRIAL)
            stop_start = time.monotonic()
            time.sleep(6)

            def stopped_and_returned():
                status = self.api("/api/status")
                if status["boot_count"] != before["boot_count"]:
                    raise RuntimeError("DUT restarted during DERP teardown: "
                                       f"{before['boot_count']} -> {status['boot_count']}")
                return self.api("/api/vpn")["connects"] > initial["connects"]

            self.wait_for("VPN teardown and restart", stopped_and_returned, VPN_BACK_WINDOW)
            print(f"VPN returned after {time.monotonic() - stop_start:.1f} s", flush=True)
            missed, _ = self.owned("the stalled dial")
            print("The client's stops so far, from the console capture "
                  f"(stop deadlines missed: {missed}):", flush=True)
            for line in self.device_log().splitlines():
                if ("microlink:" in line and any(word in line for word in
                        ("Stopping", "Stopped", "Destroyed", "joined", "still running",
                         "deferred"))) or "DERP I/O task exiting" in line:
                    print("  " + line.strip(), flush=True)
        except (OSError, ValueError, RuntimeError) as exc:
            failure = str(exc)
        finally:
            if table:
                remote(f"sudo -n nft delete table inet {table}")

        self.wait_for("DERP recovery after removing the drop",
                      lambda: self.api("/api/vpn")["derp"], DUT_REBOOT_WINDOW)
        print("Watching for delayed callbacks into the old task for 40 s", flush=True)
        end = time.monotonic() + 40
        while time.monotonic() < end:
            try:
                status = self.api("/api/status")
            except OSError as exc:
                self.wait_for("DUT recovery after interrupted status",
                              lambda: self.api("/api/status"), DUT_REBOOT_WINDOW)
                raise RuntimeError("DUT link failed during the delayed-callback watch") from exc
            if status["boot_count"] != before["boot_count"]:
                raise RuntimeError("DUT restarted after DERP recovery")
            time.sleep(2)
        if failure:
            raise RuntimeError(failure)

    # ---- leg B: the uplink goes while the relay is up
    def uplink_losses(self, boots):
        for cycle in range(1, UPLINK_LOSSES + 1):
            vpn = self.api("/api/vpn")
            if not vpn["derp"]:
                raise RuntimeError("the relay is not connected before an uplink loss")
            stamp_before, _ = self.last_exit()
            self.api("/api/wifi/try", TRIAL)
            time.sleep(6)
            took = self.back_up(vpn["connects"], boots, "the uplink loss")
            print(f"Uplink loss {cycle} of {UPLINK_LOSSES}: VPN and relay back after "
                  f"{took + 6:.0f} s", flush=True)
            self.relay_task_ended(stamp_before, f"Uplink loss {cycle} of {UPLINK_LOSSES}")

    # ---- leg C: the control connection goes, the relay stays up
    def control_losses(self, boots):
        address, port = self.api("/api/vpn")["endpoint"].rsplit(":", 1)
        match = f"ip daddr {ipaddress.IPv4Address(address)} tcp dport {int(port)}"
        for cycle in range(1, CONTROL_LOSSES + 1):
            vpn = self.api("/api/vpn")
            if vpn["state"] != "connected" or not vpn["derp"]:
                raise RuntimeError("the VPN and its relay are not up before a control loss")
            stamp_before, _ = self.last_exit()
            table = self.firewall(match)
            try:
                started = time.monotonic()
                self.wait_for("the client's stop behind the lost control connection",
                              lambda: self.last_exit()[0] > stamp_before,
                              CONTROL_LOSS_WINDOW)
                print(f"Control loss {cycle} of {CONTROL_LOSSES}: the client was stopped "
                      f"{time.monotonic() - started:.0f} s after its control connection went",
                      flush=True)
            finally:
                remote(f"sudo -n nft delete table inet {table}")
            self.back_up(vpn["connects"], boots, "the control loss")
            self.relay_task_ended(stamp_before, f"Control loss {cycle} of {CONTROL_LOSSES}")

    def run(self, legs):
        before = self.api("/api/status")
        initial = self.api("/api/vpn")
        if initial["type"] != "tailscale" or not initial["derp"]:
            raise RuntimeError("the public Tailscale rig must be connected first")
        boots = before["boot_count"]

        if "a" in legs:
            self.stalled_dial(before)

        if "b" in legs or "c" in legs:
            if "b" in legs:
                self.uplink_losses(boots)
            if "c" in legs:
                self.control_losses(boots)
            # Like with like: every sample was taken the same few seconds
            # behind "relay back", with a client of the same age. A sample
            # of an older client (before the first stop, say) is not
            # comparable: the connection to a peer's region is opened on
            # demand and closed when idle, and it is 50 to 70 KB of PSRAM.
            first, last = self.after_stop[0], self.after_stop[-1]
            print(f"Memory behind the first and the last of {len(self.after_stop)} stops: "
                  f"PSRAM free {first[0]} -> {last[0]} B, internal {first[1]} -> "
                  f"{last[1]} B", flush=True)
            if len(self.after_stop) < 2:
                raise RuntimeError("one stop alone says nothing about memory left behind")
            if first[0] - last[0] > PSRAM_SLACK:
                raise RuntimeError("PSRAM stayed allocated behind the stops: "
                                   f"{first[0] - last[0]} B")
            if first[1] - last[1] > INTERNAL_SLACK:
                raise RuntimeError("internal RAM stayed allocated behind the stops: "
                                   f"{first[1] - last[1]} B")
            state_stack = self.task_headroom("vpn_state")
            print(f"vpn_state stack: {state_stack} B never used", flush=True)
            if state_stack < STACK_FLOOR:
                raise RuntimeError(f"vpn_state has {state_stack} B of stack left")

        missed, destroyed = self.owned("the whole run")
        print(f"Ownership kept over {destroyed} destroyed client(s); stop deadlines "
              f"missed: {missed} (each one kept the client until its task had ended)",
              flush=True)
        after = self.api("/api/status")
        failures = []
        if after["boot_count"] != boots:
            failures.append(f"DUT restarted: {boots} -> {after['boot_count']}")
        if after["unexpected_resets"] != before["unexpected_resets"]:
            failures.append("unexpected reset")
        if after["health"]["log_errors"] != 0:
            failures.append(f"{after['health']['log_errors']} error log line(s)")
        if after["health"]["faults"] != before["health"]["faults"]:
            failures.append("a new fault code")
        if failures:
            raise RuntimeError("; ".join(failures))
        print("TS DERP STOP PASS", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dut", required=True)
    parser.add_argument("--console", default="",
                        help="the stage's console capture (test.ps1 passes it)")
    parser.add_argument("--legs", default="abc",
                        help="the legs to run, letters of abc (default: all)")
    parser.add_argument("--judge", default="",
                        help="judge ownership in a console capture of an earlier run "
                             "and exit: no bench, no device")
    args = parser.parse_args()
    if args.judge:
        with open(args.judge, encoding="utf-8", errors="replace") as capture:
            violations, missed, destroyed = ownership(capture.read())
        print(f"{destroyed} client(s) destroyed, {missed} stop deadline(s) missed, "
              f"{len(violations)} ownership violation(s)")
        for violation in violations:
            print("  " + violation)
        return 1 if violations else 0
    if not args.console or not os.path.exists(args.console):
        print("TS DERP STOP FAIL: no console capture (--console): the client's lines "
              "are judged there; run through test.ps1 without -NoConsole", flush=True)
        return 1
    try:
        Bench(args.dut, args.console).run(args.legs.lower())
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"TS DERP STOP FAIL: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
