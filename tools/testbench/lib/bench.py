"""Bench-side control for WiCAN HIL tests (components/TESTBENCH.md).

Drives the Raspberry Pi bench (APs on wtest0/wint0 role radios via NetworkManager) either
over SSH (running pytest on the dev host) or locally (running pytest on the
Pi itself). All bench NM connections are named "wican-*" so cleanup() can
never touch the Pi's own connectivity.
"""
from __future__ import annotations

import re
import subprocess
import time


class Bench:
    def __init__(self, ssh_host: str | None):
        """ssh_host=None means run commands locally (pytest on the Pi)."""
        self._ssh_host = ssh_host

    # ---- plumbing --------------------------------------------------------

    def sh(self, cmd: str, timeout: int = 30) -> tuple[int, str]:
        if self._ssh_host:
            argv = ["ssh", "-o", "BatchMode=yes", self._ssh_host, cmd]
        else:
            argv = ["bash", "-c", cmd]
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()

    def check(self, cmd: str, timeout: int = 30) -> str:
        rc, out = self.sh(cmd, timeout)
        assert rc == 0, f"bench command failed ({rc}): {cmd}\n{out}"
        return out

    # ---- access points ---------------------------------------------------

    def ap_up(self, con: str, ssid: str, psk: str, ifname: str = "wtest0",
              channel: int | None = None) -> None:
        assert con.startswith("wican-"), "bench connections must be wican-*"
        extra = f" band bg channel {channel}" if channel else ""
        self.check(f"sudo nmcli device wifi hotspot ifname {ifname} "
                   f"con-name {con} ssid {ssid} password {psk}{extra}",
                   timeout=45)

    def ap_down(self, con: str) -> None:
        self.sh(f"sudo nmcli connection down {con}")

    def ap_hidden(self, con: str, hidden: bool) -> None:
        """Toggle SSID-broadcast on a bench AP (hidden-network scenarios).
        Bounces the connection so the beacon change takes effect."""
        val = "yes" if hidden else "no"
        self.check(f"sudo nmcli connection modify {con} "
                   f"802-11-wireless.hidden {val}")
        self.sh(f"sudo nmcli connection down {con}")
        self.check(f"sudo nmcli connection up {con}", timeout=45)

    def ap_resume(self, con: str) -> None:
        self.check(f"sudo nmcli connection up {con}", timeout=45)

    #: persistent Pi infrastructure — NEVER auto-deleted (the wican-bench
    #: hotspot is the standing DUT uplink; deleting it stranded the bench
    #: twice on 2026-07-05/06). wican-bench-w0 = the internal-radio (wint0) failover twin
    #: (2026-07-17 rig: the wican-bench-watchdog swaps between them).
    PERSISTENT = ("wican-bench", "wican-bench-w0")

    def cleanup(self) -> None:
        """Delete every bench-created (wican-*) NM connection EXCEPT the
        persistent infrastructure (see PERSISTENT)."""
        _, out = self.sh("nmcli -t -f NAME connection show")
        for name in out.splitlines():
            if name.startswith("wican-") and name not in self.PERSISTENT:
                self.sh(f"sudo nmcli connection delete '{name}'")

    def park_persistent(self) -> None:
        """Take the persistent hotspots off the radios for the suite:
        autoconnect off + down (else one re-grabs a radio whenever a test
        AP drops — and the bench watchdog stands down on autoconnect=no).
        Remembers which were ACTIVE so unpark restores that exact state
        (only one of the wican-bench twins is normally up)."""
        _, out = self.sh("nmcli -t -f NAME connection show --active")
        self._parked_active = [n for n in out.splitlines()
                               if n in self.PERSISTENT]
        for name in self.PERSISTENT:
            self.sh(f"sudo nmcli connection modify {name} connection.autoconnect no")
            self.sh(f"sudo nmcli connection down {name}")

    def unpark_persistent(self) -> None:
        """Restore the persistent hotspot state captured by park."""
        for name in self.PERSISTENT:
            self.sh(f"sudo nmcli connection modify {name} connection.autoconnect yes")
        for name in getattr(self, "_parked_active", self.PERSISTENT[:1]):
            self.sh(f"sudo nmcli connection up {name}")

    # ---- observations ----------------------------------------------------

    def dut_ip(self, ifname: str = "wtest0") -> str | None:
        """IP the DUT got from the bench AP's DHCP (most recent lease).

        NM's shared-mode dnsmasq writes the lease the moment the client
        joins — unlike the kernel neighbour table, which stays EMPTY for
        an idle client that never sends traffic toward the Pi (this cost
        a full HIL run 2026-07-06). Neighbour table kept as fallback;
        wait_dut_ip() pings to confirm liveness either way."""
        rc, out = self.sh(
            f"sudo cat /var/lib/NetworkManager/dnsmasq-{ifname}.leases")

        if rc == 0:
            leases = re.findall(r"^\d+\s+\S+\s+(\d+\.\d+\.\d+\.\d+)\s",
                                out, re.M)
            if leases:
                return leases[-1]

        _, out = self.sh(f"ip neigh show dev {ifname}")
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s.*(REACHABLE|STALE|DELAY)", out)
        return m.group(1) if m else None

    def ping(self, ip: str, count: int = 2) -> bool:
        rc, _ = self.sh(f"ping -c {count} -W 2 {ip}", timeout=20)
        return rc == 0

    def wait_dut_ip(self, ifname: str = "wtest0", timeout_s: int = 40) -> str:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            ip = self.dut_ip(ifname)
            if ip and self.ping(ip, count=1):
                return ip
            time.sleep(2)
        raise AssertionError(f"DUT never became reachable on {ifname}")

    def visible_ssids(self, ifname: str = "wint0") -> list[str]:
        """Scan from the Pi (default: its uplink radio, scan is safe)."""
        _, out = self.sh(
            f"nmcli -t -f SSID device wifi list ifname {ifname} "
            f"--rescan yes", timeout=45)
        return [s for s in out.splitlines() if s]

    def ssid_channel(self, ssid: str, ifname: str = "wint0") -> int | None:
        """Channel `ssid` is beaconing on, per a fresh scan from a free Pi
        radio (an interface hosting an AP cannot scan). None = not seen."""
        _, out = self.sh(
            f"nmcli -t -f SSID,CHAN device wifi list ifname {ifname} "
            f"--rescan yes", timeout=45)
        for line in out.splitlines():
            name, _, chan = line.rpartition(":")
            if name == ssid and chan.isdigit():
                return int(chan)
        return None

    def wait_ssid_channel(self, ssid: str, ifname: str = "wint0",
                          timeout_s: int = 90) -> int:
        """ssid_channel() with retries — RF scans regularly come back thin."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            ch = self.ssid_channel(ssid, ifname)
            if ch is not None:
                return ch
            time.sleep(3)
        raise AssertionError(f"'{ssid}' never appeared in scans on {ifname}")

    def http_get(self, url: str) -> tuple[int, str]:
        rc, out = self.sh(
            f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 {url}",
            timeout=20)
        code = int(out) if rc == 0 and out.isdigit() else 0
        return code, out
