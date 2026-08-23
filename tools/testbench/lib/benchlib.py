"""benchlib — ONE bench library, ONE timeout policy (bench plan phase 3).

Every timeout/retry constant the bench uses lives HERE (documented in
TESTING.md); scripts must not invent their own. The core contract (the
classification rule): when a step exhausts its budget, benchlib asks the
rig itself (`bench-health` on rpi001) BEFORE failing —

  RIG FAULT -> run `bench-recover`, retry the operation once; a second
               rig fault raises RigFault — the run is reported as
               RIG FAULT <detail>, never blamed on firmware.
  RIG OK    -> the failure is real: the original DUT-side error surfaces.

Works both ON rpi001 (system_bench et al.) and on the dev PC (ssh'd Pi
commands via the `rpi001` alias — which is the ethernet control plane).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# THE timeout table (single source of truth; mirror: TESTING.md)
# ---------------------------------------------------------------------------
SSH_CONNECT = 10          # s; + 1 retry (Windows OpenSSH cold-handshake flake)
SSH_RETRIES = 1
HTTP_TRY = 8              # s; one API call on a healthy link
HTTP_RETRY_BUDGET = 30    # s; rides a route flap — longer means rig fault
HTTP_RETRY_GAP = 3        # s between transport retries
DUT_REBOOT_WINDOW = 180   # s; healthy ~20-40 s, degraded rig 2-3 min
OTA_UPLOAD = 300          # s; 3.4 MB at worst-case RF
BLE_SCAN_TRIES = 3        # existing ble_bench practice
BLE_SCAN_WINDOW = 8       # s per try

AP_IP = "192.168.0.10"    # the legacy factory-AP address (apps hardcode it)
BENCH_SSID = "WICAN_TEST_AP"
# no literal fallback PSK in the tree: set WICAN_BENCH_PSK for rigs
# whose Pi profile cannot be read; bench_psk() is always preferred
BENCH_PSK_FALLBACK = os.environ.get("WICAN_BENCH_PSK", "")

# radio roles (udev-pinned on rpi001 since 2026-07-26); legacy fallbacks
# let this library run against a Pi that predates the rename
ROLE_HOTSPOT = ("wint0", "wlan0")    # wican-bench-w0 (internal radio)
ROLE_TWIN = ("wtest0", "wlan1")      # wican-bench (USB stick twin)


class RigFault(Exception):
    """The RIG (Pi/radios/services) is at fault — not the firmware."""


def on_pi() -> bool:
    return socket.gethostname() == "rpi001"


# ---------------------------------------------------------------------------
# shell plumbing
# ---------------------------------------------------------------------------
def sh(cmd: str, timeout: int = 60) -> tuple[int, str]:
    """Local shell (used on the Pi by benches that run there)."""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def pi(cmd: str, timeout: int = 60) -> tuple[int, str]:
    """Run a command on rpi001: locally when we ARE the Pi, else over the
    ssh control plane with ConnectTimeout + one retry (the documented
    Windows-OpenSSH cold-handshake gotcha)."""
    if on_pi():
        return sh(cmd, timeout)

    argv = ["ssh", "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={SSH_CONNECT}", "rpi001", cmd]

    for attempt in range(SSH_RETRIES + 1):
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            if attempt == SSH_RETRIES:
                raise
            continue

        if r.returncode != 255:   # 255 = ssh transport failure -> retry
            return r.returncode, (r.stdout + r.stderr).strip()

    return 255, (r.stdout + r.stderr).strip()


def bench_psk() -> str:
    """The standing wican-bench profile's REAL PSK (never recreate a
    hotspot with the fallback constant — 2026-07-19 lesson)."""
    rc, out = pi("sudo nmcli -s -g 802-11-wireless-security.psk "
                 "connection show wican-bench")
    return out.strip() if rc == 0 and out.strip() else BENCH_PSK_FALLBACK


# ---------------------------------------------------------------------------
# rig verdict + recovery (the classification rule)
# ---------------------------------------------------------------------------
def rig_verdict() -> tuple[bool, str]:
    """Run bench-health on the Pi. (ok, verdict_text)."""
    rc, out = pi("bench-health", timeout=60)
    return rc == 0, out


def recover(timeout: int = 300) -> str:
    """Run the recovery ladder. Blocks through a level-1 Pi reboot."""
    rc, out = pi("bench-recover", timeout=120)

    if "RIG REBOOTING" in out:
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(10)
            rc2, probe = pi("bench-health", timeout=60)
            if rc2 == 0:
                return probe
        return out + "\n(reboot did not restore the rig in time)"

    return out


def _classified(op, what: str):
    """Budget exhausted -> verdict -> recover+retry once -> RigFault, or
    re-raise the original (DUT-side) failure when the rig is clean."""
    try:
        return op()
    except RigFault:
        raise
    except Exception as first_err:
        ok, verdict = rig_verdict()

        if ok:
            raise   # rig clean -> real DUT failure, surface the evidence

        print(f"(benchlib) {what}: budget exhausted and {verdict.splitlines()[0] if verdict else 'rig probe failed'} -> bench-recover")
        recover()

        try:
            return op()
        except Exception:
            # retry failed too — re-probe decides who is to blame now
            ok2, verdict2 = rig_verdict()
            if ok2:
                raise   # rig healed but the DUT still fails: DUT's fault
            raise RigFault(
                f"{what} failed twice; rig verdict:\n{verdict2}") from first_err


# ---------------------------------------------------------------------------
# DUT HTTP / discovery / OBD
# ---------------------------------------------------------------------------
def api(host: str, path: str, method: str = "GET", body=None,
        try_s: int = HTTP_TRY, budget_s: int = HTTP_RETRY_BUDGET,
        classify: bool = True):
    """One API call with the standard retry policy. Transport errors
    retry inside budget_s; HTTP status errors surface IMMEDIATELY (they
    are answers — the leg-8a lesson). Returns (status, text)."""
    def attempt_loop():
        deadline = time.time() + budget_s
        while True:
            req = urllib.request.Request(f"http://{host}{path}",
                                         method=method)
            if body is not None:
                req.add_header("Content-Type", "application/json")
                req.data = json.dumps(body).encode()
            try:
                with urllib.request.urlopen(req, timeout=try_s) as resp:
                    return resp.status, resp.read().decode()
            except urllib.error.HTTPError:
                raise                      # an answer, not a transport fault
            except (urllib.error.URLError, OSError):
                if time.time() + HTTP_RETRY_GAP > deadline:
                    raise
                time.sleep(HTTP_RETRY_GAP)

    if not classify:
        return attempt_loop()
    return _classified(attempt_loop, f"api {method} {host}{path}")


def hotspot_ifaces() -> list[str]:
    """Live interface names of the DUT-hotspot radios (role names with
    pre-rename fallback), primary first."""
    names = []
    for cands in (ROLE_HOTSPOT, ROLE_TWIN):
        for n in cands:
            rc, _ = pi(f"test -e /sys/class/net/{n}")
            if rc == 0:
                names.append(n)
                break
    return names


def find_dut(window: int = DUT_REBOOT_WINDOW, classify: bool = False):
    """Search dnsmasq leases + neighbor tables on every hotspot radio for
    a live DUT (leases-before-ARP lesson). Returns ip or None; with
    classify=True a None triggers the rig verdict/recover path first."""
    ifaces = hotspot_ifaces()

    def scan_window(deadline_s):
        deadline = time.time() + deadline_s
        while time.time() < deadline:
            time.sleep(3)
            cands = []

            for dev in ifaces:
                rc, out = pi(f"ip neigh show dev {dev} | awk '{{print $1}}'")
                cands += out.split()

            lease_files = " ".join(
                f"/var/lib/NetworkManager/dnsmasq-{d}.leases" for d in ifaces)
            rc, out = pi(f"sudo sh -c 'cat {lease_files} 2>/dev/null' "
                         "| awk '{print $3}'")
            cands += out.split()

            for ip in dict.fromkeys(cands):
                try:
                    st, _ = api(ip, "/api/status", try_s=3, budget_s=3,
                                classify=False)
                    if st == 200:
                        return ip
                except Exception:
                    pass
        return None

    ip = scan_window(window)

    if ip is None and classify:
        ok, verdict = rig_verdict()
        if not ok:
            print(f"(benchlib) find_dut: window empty and rig faulty -> "
                  f"bench-recover\n{verdict}")
            recover()
            ip = scan_window(window)
            if ip is None:
                raise RigFault(f"find_dut empty twice; verdict:\n{verdict}")

    return ip


def submit_and_wait(ip: str, window: int = DUT_REBOOT_WINDOW):
    """POST /api/settings/submit and ride through the apply reboot.
    Returns (rebooted, new_ip) — new_ip == ip when nothing was staged."""
    st, body = api(ip, "/api/settings/submit", method="POST")
    rebooted = json.loads(body).get("reboot", False)

    if not rebooted:
        return False, ip

    time.sleep(8)
    new_ip = find_dut(window, classify=True)
    return True, new_ip


def obd_tcp(ip: str, cmd: bytes, secs: int = 5, port: int = 35000) -> str:
    """One ELM transaction over TCP with reconnect-on-RST."""
    for attempt in range(2):
        try:
            s = socket.create_connection((ip, port), timeout=5)
            s.settimeout(2)
            time.sleep(0.2)
            s.sendall(cmd)
            buf = b""
            t0 = time.time()

            while time.time() - t0 < secs and b">" not in buf:
                try:
                    b = s.recv(256)
                except socket.timeout:
                    break
                if not b:
                    break
                buf += b

            s.close()
            return buf.decode(errors="replace")
        except OSError:
            if attempt:
                raise
            time.sleep(1)
