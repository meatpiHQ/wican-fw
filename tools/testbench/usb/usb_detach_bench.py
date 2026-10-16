#!/usr/bin/env python3
"""A warm restart in the USB device role drops off the bus first: the bench.

The claim (ported from the ESPNetLink bench of 2026-10-16): when the WiCAN
is a USB device to a PC (usb_host_manager role=device, class ncm or rndis)
and restarts for any planned reason (a settings submit, an OTA apply,
POST /api/restart), the PC sees the device LEAVE within a few seconds of
the request and come back as a fresh enumeration, and the next boot is a
clean `software` reset. Without the detach the host is never told the
device went away, a transfer stays in flight across the reset, and the
next image can take the stale completion before its class is ready (the
dongle crashed in ISR context that way, LoadProhibited at 0). On WiCAN
the device role carries the J2534 server and the web UI over the cable.

Rig: the DUT's USB-C cabled to THIS PC (the preflight says `connector ->
PC`), the DUT on the bench access point for the control path (an ssh
tunnel through the Pi: `--dut`; or the USB link itself once the DUT is in
the device role), and the USB link (`--usb`, the DUT's address on the
NCM/RNDIS netif) as the witness. Two views of the PC side: a TCP connect
to the DUT's USB address sampled at 10 Hz from a socket bound to the PC's
own address on that link (the bind fails at once while the adapter or
its lease is gone; a timeout = the device is there but not answering; 0
= the link answers: the detach timing comes from here, free of PnP
polling), and Windows PnP through one long-lived PowerShell (about
0.25 s a poll, the re-enumeration). A
console tail on COM254 beside the run is test.ps1's job (`.\test.ps1
usbdetach`).

Legs:
  d0  as found: usb_host_manager settings, the PC's view of the device
  d1  the device role (class --class, one planned restart when the DUT
      was not in it): the PC enumerates the DUT (its USB serial), a NET
      adapter binds, the link answers /api/info at --usb, the DUT's own
      /api/usb names the device driver; the internal heap at --settle
  d2  --restarts warm restarts (POST /api/restart) with the link up: per
      restart the path to the DUT is gone within --detach-max seconds of
      the request (the fix's signature: the shutdown handler drops D+
      about a second after the request, Windows takes another one to two
      seconds to process the removal; without the detach the device
      stays attached until the next image re-initialises USB, 7 to 9 s
      later), the device is back, the link answers within --link-max,
      the new record is `software`, planned `user_request`, no crash
      note, no unexpected reset
  d3  the internal heap at --settle after the last boot against d1, the
      E lines of the USB tags, the bridge task's stack headroom, the
      heap floor (planned restarts only)
  d4  --restore-host: role host (and the class as found) staged and
      submitted, then a PSU power cycle: the connector mux only returns
      to the CH342 at power-on (TESTING.md), so a bench that leaves the
      DUT in the device role says so

  python tools/testbench/usb/usb_detach_bench.py --dut localhost:18081
      [--usb 192.168.82.1] [--class ncm] [--restarts 10] [--settle 150]
      [--detach-max 5] [--restore-host] [--only d1,d2] [--logdir DIR]

Prints `USB DETACH PASS` (or `USB DETACH PARTIAL PASS (legs: ...)` with
--only). A class Windows cannot bind (RNDIS on 24H2 and later: the inbox
driver only takes the MS OS compat id) ends d1 with a SKIP line and the
run restores the class as found.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import pcbench  # noqa: E402

OWN_TAGS = ("usb_net_device", "usb_host_manager", "usb_cdc_device")
LEGS = ("d1", "d2", "d3")
VID = 0x16D0
PID_NET = 0x1262          # CONFIG_WICAN_USB_DEV_PID (ncm and rndis)
POLL_S = 0.05             # between PnP polls (each poll is ~0.25 s)
# Winsock WSAEADDRNOTAVAIL: binding to the PC's own address on the USB link
# fails at once while the adapter (or its lease) is gone. A plain connect
# would not tell: the PC's default route absorbs the DUT's address and the
# connect merely times out, as it does while the DUT boots.
NO_PATH = (10049,)


class Pnp:
    """Windows PnP presence through one long-lived PowerShell: a spawn per
    poll would cost 1 s, a CIM query inside a running shell 0.25 s."""

    def __init__(self):
        self.p = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)

    def _query(self, ps):
        self.p.stdin.write(ps + "; Write-Output 'END-OF-QUERY'\n")
        self.p.stdin.flush()
        out = []
        while True:
            ln = self.p.stdout.readline()
            if not ln or ln.strip() == "END-OF-QUERY":
                break
            if ln.strip():
                out.append(ln.strip())
        return out

    def instances(self, vid, pid):
        """Every present PnP instance id of vid:pid (the composite and its
        interfaces)."""
        return self._query(
            "Get-CimInstance Win32_PnPEntity -Filter \"PNPDeviceID LIKE "
            f"'USB%VID_{vid:04X}&PID_{pid:04X}%'\" | ForEach-Object "
            "{ $_.PNPDeviceID }")

    def serials(self, vid, pid):
        """The composite devices' serial strings (the last path element of
        an instance id without an interface number)."""
        out = set()
        for inst in self.instances(vid, pid):
            head, _, tail = inst.rpartition("\\")
            if "&MI_" not in head:
                out.add(tail.upper())
        return out

    def net_adapters(self, serial):
        """Name, status, description and PARENT instance id (the composite
        device, whose id ends in the serial; the adapter's own interface id
        does not carry it) of every NET adapter on a WiCAN USB device."""
        return self._query(
            "Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | "
            "Where-Object { $_.PnPDeviceID -like 'USB*VID_16D0*' } | "
            "ForEach-Object { $par = (Get-PnpDeviceProperty -InstanceId "
            "$_.PnPDeviceID -KeyName 'DEVPKEY_Device_Parent' -ErrorAction "
            "SilentlyContinue).Data; $_.Name + '|' + $_.Status + '|' + "
            "$_.InterfaceDescription + '|' + $par }")

    def close(self):
        try:
            self.p.stdin.close()
            self.p.terminate()
        except OSError:
            pass


class LinkProbe(threading.Thread):
    """Every 0.1 s a TCP socket bound to the PC's own address on the USB
    link (`pc_ip`) connects to the DUT, each sample stamped: `NO_PATH` =
    the bind failed, the adapter or its lease is gone; a timeout or a
    refusal = the device is there but not answering; 0 = the link answers.
    The detach timing comes from here: a PnP query that blocks for seconds
    cannot hide an absence."""

    def __init__(self, usb, pc_ip):
        super().__init__(daemon=True)
        self.addr = (usb, 80)
        self.pc_ip = pc_ip
        self.samples = []            # (time, code)
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()

    def run(self):
        while not self.stop_flag.is_set():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.3)
            t = time.time()
            try:
                s.bind((self.pc_ip, 0))
                code = s.connect_ex(self.addr)
            except OSError as e:
                code = e.errno if e.errno else 10060
            finally:
                s.close()
            with self.lock:
                self.samples.append((t, code))
                del self.samples[:-6000]
            time.sleep(0.1)

    def first(self, after, pred):
        """The time of the first sample at or after `after` whose code
        satisfies pred, or None."""
        with self.lock:
            for t, code in self.samples:
                if t >= after and pred(code):
                    return t
        return None

    def stop(self):
        self.stop_flag.set()


def pc_usb_ip(pnp, usb):
    """The PC's IPv4 address on the DUT's USB subnet (its lease from the
    DUT's DHCP server), or None."""
    prefix = usb.rsplit(".", 1)[0] + "."
    out = pnp._query(
        "(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue "
        f"| Where-Object {{ $_.IPAddress -like '{prefix}*' }}).IPAddress")
    return out[0] if out else None


def usb_serial(mac):
    """The USB serial string the bridge derives from the base MAC: the
    ETH-derived local address (first octet with the local bit, last octet
    plus 3), printed as 12 upper-case hex digits (usb_net_device.c)."""
    b = [int(x, 16) for x in mac.split(":")]
    b[0] |= 0x02
    b[5] = (b[5] + 3) & 0xFF
    return "".join(f"{x:02X}" for x in b)


def link_info(usb, timeout=3):
    """/api/info over the USB link, or None."""
    try:
        with urllib.request.urlopen(f"http://{usb}/api/info",
                                    timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", errors="replace"))
            return d if isinstance(d, dict) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def uptime_s(st):
    try:
        h, m, s = st.get("uptime", "0:0:0").split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except ValueError:
        return 0


def mem_row(dut):
    st = dut.get("/api/status")
    m = st.get("memory", {}).get("internal", {})
    return {"uptime": st.get("uptime"), "free": m.get("free"),
            "min_free": m.get("min_free"), "largest": m.get("largest_block"),
            "log_errors": st.get("health", {}).get("log_errors")}


def settle_then_read(dut, t_boot, settle, run, leg):
    wait = settle - (time.time() - t_boot)
    if wait > 0:
        print(f"{leg}: settling {wait:.0f} s before the heap reading",
              flush=True)
        time.sleep(wait)
    r = mem_row(dut)
    run.metric(f"{leg}_internal_free_b", r["free"])
    run.metric(f"{leg}_internal_min_free_b", r["min_free"])
    run.metric(f"{leg}_internal_largest_b", r["largest"])
    print(f"{leg}: internal free {r['free']} min_free {r['min_free']} "
          f"largest {r['largest']} at uptime {r['uptime']}", flush=True)
    return r


def wait_present(pnp, serial, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if serial in pnp.serials(VID, PID_NET):
            return time.time() - t0
        time.sleep(POLL_S)
    return None


def wait_link(usb, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if link_info(usb) is not None:
            return time.time() - t0
        time.sleep(0.3)
    return None


def enter_device_role(dut, run, pnp, a, cls):
    """d1: the device role with class `cls`; True when the PC bound it."""
    found = dut.settings("usb_host_manager")
    want = {"enabled": True, "role": "device", "device_class": cls}
    changed = any(found.get(k) != v for k, v in want.items())
    serial = usb_serial(dut.get("/api/info").get("mac", "00:00:00:00:00:00"))
    before = pnp.serials(VID, PID_NET)
    if changed:
        print(f"d1: staging usb_host_manager {want} (was role "
              f"{found.get('role')} class {found.get('device_class')}): "
              "one planned restart", flush=True)
        t_boot = dut.restart({"usb_host_manager": want})
    else:
        print(f"d1: the DUT is in the device role already (class {cls})",
              flush=True)
        t_boot = time.time() - uptime_s(dut.get("/api/status"))
    t_seen = wait_present(pnp, serial, 40)
    if t_seen is None:
        new = sorted(pnp.serials(VID, PID_NET) - before)
        if len(new) == 1:
            print(f"d1: the computed serial {serial} did not show; one new "
                  f"device did: {new[0]} (adopted)", flush=True)
            serial = new[0]
            t_seen = 0.0
    run.check("d1_pc_enumerates_the_dut", t_seen is not None,
              f"serial {serial}, instances {sorted(pnp.serials(VID, PID_NET))}")
    if t_seen is None:
        return None, serial, t_boot
    adapters = []
    t0 = time.time()
    while time.time() - t0 < 25:
        adapters = [x for x in pnp.net_adapters(serial) if serial in x.upper()]
        if any("|Up|" in x for x in adapters):
            break
        time.sleep(1)
    bound = any("|Up|" in x for x in adapters)
    if not bound and cls == "rndis":
        print("SKIP: d1_rndis_adapter_bound (no NET adapter came up for the "
              "RNDIS device: Windows 24H2 and later ship no driver for the "
              "CDC/wireless RNDIS class; the leg needs a Linux host)",
              flush=True)
        return False, serial, t_boot
    run.check(f"d1_{cls}_adapter_bound", bound, " ; ".join(adapters)[:300])
    t_link = wait_link(a.usb, 40)
    run.check("d1_link_answers_over_usb", t_link is not None,
              f"{a.usb} after {t_link if t_link is None else round(t_link, 1)} s")
    code, u = dut.api("/api/usb")
    drv = u.get("driver") if code == 200 and isinstance(u, dict) else None
    run.check("d1_dut_reports_device_driver", drv == f"{cls}-dev",
              f"driver {drv}")
    return bound, serial, t_boot


def fmt(t, digits=1):
    return "never" if t is None else f"{t:.{digits}f}"


def restart_soak(dut, run, pnp, a, serial, probe, leg="d2"):
    """`a.restarts` warm restarts with the link up; the path to the DUT
    must be gone within a.detach_max s of each request (the probe), the
    device back (PnP, or the link as the proof), the link answering."""
    gone, back, links, bad = [], [], [], []
    st0 = dut.get("/api/status")
    b_prev = st0.get("boot_count")
    for n in range(1, a.restarts + 1):
        if link_info(a.usb) is None:
            run.check(f"{leg}_link_up_before_restart_{n}", False,
                      f"{a.usb} does not answer")
            break
        dut.sweep()
        b0 = b_prev
        t0 = time.time()
        dut.api("/api/restart", "POST", {}, timeout=4)
        # the detach: the first sample with no path to the DUT
        t_gone = None
        while time.time() - t0 < a.back_max:
            t = probe.first(t0, lambda c: c in NO_PATH)
            if t is not None:
                t_gone = t - t0
                break
            time.sleep(0.05)
        # the PC's PnP view: absent, then present again (a CIM query can
        # block across the whole absence: then only the link proves it back)
        p_back = None
        was_gone = False
        lower = t0 + t_gone if t_gone is not None else t0 + 3.0
        while time.time() - t0 < a.back_max:
            present = serial in pnp.serials(VID, PID_NET)
            if not present and not was_gone:
                was_gone = True
            elif present and was_gone:
                p_back = time.time() - t0
                break
            elif present and probe.first(lower, lambda c: c == 0) is not None:
                break   # the link is back: the device is, whatever PnP saw
            time.sleep(POLL_S)
        # the new boot through the control path: an answer over the USB
        # link before the reset would be the old run's
        t1 = time.time()
        b = None
        while time.time() - t1 < 120:
            b = dut.boot_count()
            if b is not None and b0 is not None and b > b0:
                break
            time.sleep(1)
        dut.restarts += 1
        b_prev = b
        t_link = None
        while time.time() - t0 < a.link_max:
            t = probe.first(lower, lambda c: c == 0)
            if t is not None:
                t_link = t - t0
                break
            time.sleep(0.1)
        code, h = dut.api("/api/restart/history", timeout=15)
        rec = (h.get("records") or [{}])[0] if code == 200 and \
            isinstance(h, dict) else {}
        words = (rec.get("reason"), rec.get("planned"),
                 rec.get("planned_reason"), bool(rec.get("crash")))
        ok_rec = words == ("software", True, "user_request", False)
        gone.append(t_gone)
        back.append(p_back if p_back is not None else t_link)
        links.append(t_link)
        if not ok_rec:
            bad.append(f"#{n} {words}")
        print(f"{leg}: restart {n}: path gone {fmt(t_gone, 2)} s, pnp back "
              f"{fmt(p_back)} s, link {fmt(t_link)} s, boot {b0} -> {b}, "
              f"record {words}", flush=True)
    n_done = len(gone)
    run.check(f"{leg}_restarts_done", n_done == a.restarts,
              f"{n_done} of {a.restarts}")
    seen = [t for t in gone if t is not None]
    run.metric(f"{leg}_detach_s_max", round(max(seen, default=-1), 2))
    run.metric(f"{leg}_detach_s_min", round(min(seen, default=-1), 2))
    if seen:
        run.metric(f"{leg}_detach_s_median",
                   round(sorted(seen)[len(seen) // 2], 2))
    run.check(f"{leg}_path_gone_within_{a.detach_max:g}_s_every_restart",
              n_done > 0 and len(seen) == n_done
              and all(t <= a.detach_max for t in seen),
              "gone at " + " ".join(fmt(t, 2) for t in gone))
    run.check(f"{leg}_device_back_every_restart",
              n_done > 0 and all(t is not None for t in back),
              "back at " + " ".join(fmt(t) for t in back))
    run.metric(f"{leg}_back_s_max",
               round(max((t for t in back if t is not None), default=-1), 1))
    run.check(f"{leg}_link_answers_every_restart",
              n_done > 0 and all(t is not None for t in links),
              "link at " + " ".join(fmt(t) for t in links))
    run.metric(f"{leg}_link_s_max",
               round(max((t for t in links if t is not None), default=-1), 1))
    absence = [lk - g for g, lk in zip(gone, links)
               if g is not None and lk is not None]
    if absence:
        run.metric(f"{leg}_absence_s_min", round(min(absence), 1))
    run.check(f"{leg}_every_record_software_planned_no_crash", not bad,
              "; ".join(bad)[:300])
    return st0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081",
                    help="the control path: the DUT over the Pi tunnel, or "
                         "its USB address once in the device role")
    ap.add_argument("--usb", default="192.168.82.1",
                    help="the DUT on the USB link (the NCM/RNDIS netif)")
    ap.add_argument("--pc-ip", default="auto",
                    help="the PC's own address on that link (auto = its "
                         "lease on the --usb subnet)")
    ap.add_argument("--class", dest="cls", default="ncm",
                    help="device class for the run: ncm or rndis")
    ap.add_argument("--restarts", type=int, default=10)
    ap.add_argument("--settle", type=int, default=150,
                    help="seconds after a boot before a heap reading")
    ap.add_argument("--detach-max", type=float, default=5.0,
                    help="the path to the DUT must be gone within this many "
                         "seconds of POST /api/restart (measured 2.6 to 3.2 s "
                         "with the fix; 7 to 9 s is the next image's own "
                         "re-enumeration, i.e. no detach)")
    ap.add_argument("--back-max", type=float, default=30.0)
    ap.add_argument("--link-max", type=float, default=40.0)
    ap.add_argument("--restore-host", action="store_true",
                    help="d4: role host as found + a PSU power cycle")
    ap.add_argument("--psu", default="auto",
                    help="the OWON's COM port for --restore-host")
    ap.add_argument("--only", default="", help="comma list of legs")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [s.strip() for s in a.only.split(",") if s.strip()]
    legs = only or list(LEGS)
    stamp = time.strftime("%Y%m%d_%H%M")
    logdir = a.logdir or os.path.join(HERE, "..", "..", "..", "test-reports",
                                      "logs", "usb_detach_" + stamp)
    os.makedirs(logdir, exist_ok=True)

    run = pcbench.Run("USB DETACH")
    dut = pcbench.Dut(a.dut, own_tags=OWN_TAGS, tasks=("usb_net_dev",))
    pnp = Pnp()
    probe = None
    exit_code = 1
    found = None
    try:
        st = dut.get("/api/status")
        found = dut.settings("usb_host_manager")
        dut.as_found()
        print(f"d0: DUT {st.get('version')} on {st.get('partition')}, "
              f"boot_count {st.get('boot_count')}, unexpected "
              f"{st.get('unexpected_resets')}; usb_host_manager as found: "
              f"enabled {found.get('enabled')} role {found.get('role')} "
              f"class {found.get('device_class')}; the PC sees "
              f"{sorted(pnp.serials(VID, PID_NET)) or 'no'} WiCAN USB "
              "device(s)", flush=True)
        run.check("d0_dut_answers", True,
                  f"{st.get('version')} elf {dut.get('/api/restart/history').get('elf_sha')}")

        bound, serial, t_boot = enter_device_role(dut, run, pnp, a, a.cls)
        first = None
        if bound and "d1" in legs:
            first = settle_then_read(dut, t_boot, a.settle, run, "d1")
        if bound and "d2" in legs:
            pc_ip = a.pc_ip if a.pc_ip != "auto" else pc_usb_ip(pnp, a.usb)
            run.check("d2_pc_has_a_lease_on_the_usb_link", bool(pc_ip),
                      f"{pc_ip}")
            if pc_ip:
                probe = LinkProbe(a.usb, pc_ip)
                probe.start()
                time.sleep(1)
                restart_soak(dut, run, pnp, a, serial, probe)
                probe.stop()
        if bound and "d3" in legs:
            t_last = time.time() - uptime_s(dut.get("/api/status"))
            last = settle_then_read(dut, t_last, a.settle, run, "d3")
            if first is not None:
                run.metric("d3_minus_d1_free_b", last["free"] - first["free"])
                run.check("d3_heap_within_4_kb_of_d1",
                          abs(last["free"] - first["free"]) <= 4096,
                          f"{first['free']} -> {last['free']}")
        if bound is False and a.cls != "ncm":
            # the class the PC could not bind: back to ncm for the rig
            dut.restart({"usb_host_manager": {"device_class": "ncm"}})
        dut.reset_check(run, st)
        dut.passing_checks(run)

        if a.restore_host:
            back = {"role": "host", "device_class":
                    found.get("device_class", "ncm")}
            print(f"d4: restoring usb_host_manager {back} + a PSU power "
                  "cycle (the mux returns to the CH342 at power-on only)",
                  flush=True)
            dut.restart({"usb_host_manager": back})
            psu = subprocess.run(
                [sys.executable, os.path.join(HERE, "..", "lib",
                                              "owon_psu.py"), "cycle"]
                + ([] if a.psu == "auto" else ["--port", a.psu]),
                capture_output=True, text=True, timeout=60)
            print("d4: " + (psu.stdout or "").strip().replace("\n", " | "),
                  flush=True)
            time.sleep(20)
            t0 = time.time()
            ok = False
            while time.time() - t0 < 120:
                code, s2 = dut.api("/api/settings/usb_host_manager")
                if code == 200 and isinstance(s2, dict):
                    ok = s2.get("role") == "host"
                    break
                time.sleep(3)
            run.check("d4_role_host_as_found", ok)
            run.check("d4_pc_sees_the_ch342_again",
                      bool(pnp.instances(0x1A86, 0x55D2)),
                      "the DUT's USB-C back on its serial bridge")
        exit_code = run.verdict(partial=("legs: " + " ".join(legs))
                                if only else "")
    except pcbench.Bench as e:
        print(f"BENCH ABORTED: {e}", flush=True)
        if found is not None:
            print(f"restore by hand: usb_host_manager role {found.get('role')} "
                  f"class {found.get('device_class')} (+ a PSU power cycle "
                  "for the CH342)", flush=True)
        exit_code = 2
    finally:
        if probe is not None:
            probe.stop()
        pnp.close()
        with open(os.path.join(logdir, "metrics.json"), "w") as f:
            json.dump([{"name": n, "value": v, "unit": u}
                       for n, v, u in run.metrics], f, indent=1)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
