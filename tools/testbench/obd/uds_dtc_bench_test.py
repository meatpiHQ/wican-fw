#!/usr/bin/env python3
"""UDS DTC (0x19/0x14) end-to-end bench — TASK_dtc §12.

Topology: the ECU-sim box (REST at 192.168.8.1 from the PC over its
NCM link) is the vehicle — it speaks UDS 0x19/0x14 on the shared
500k bus. DUT driven over HTTP via rpi001.

Legs:
  1. forced `uds`: REST-set DTCs → scan → report protocol=="uds",
     stored exact (sim FTB=0 → no suffix; suffix rules host-proven)
  2. UDS clear (group FFFFFF; the sim ignores groupOfDTC — selective
     per-code 0x14 encode is host-proven, sim-selective is on the sim
     TODO) → cleared, rescan empty
  3. `auto`: sim answers OBD mode 03 too → auto stays on OBD
     (protocol=="obd"), codes still exact
  4. restore settings + sim state

    python uds_dtc_bench_test.py
Expected final line: UDS DTC BENCH PASS
"""
import json
import subprocess
import sys
import time
import urllib.request

SIM = "http://192.168.8.1"

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def pi(cmd, stdin=None, timeout=120):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "rpi001", cmd],
                       input=stdin, capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout)
    return p.stdout


def dut_ip():
    return pi("sudo cat /var/lib/NetworkManager/dnsmasq-wint0.leases "
              "/var/lib/NetworkManager/dnsmasq-wtest0.leases 2>/dev/null"
              " | sort -n | tail -1 | awk '{print $3}'").strip()


def api(ip, path, method="GET", body=None):
    if body is None:
        out = pi(f"curl -s -m 10 -X {method} http://{ip}{path}")
    else:
        out = pi(f"curl -s -m 10 -X {method} -H 'Content-Type: "
                 f"application/json' -d @- http://{ip}{path}",
                 stdin=json.dumps(body))
    try:
        return json.loads(out)
    except ValueError:
        return {}


def sim_api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(SIM + path, data=data, method=method)
    with urllib.request.urlopen(req, timeout=8) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip().startswith("{") else t


def sim_set_dtcs(stored, mil=True):
    sim_api("/api/ecu/dtcs", "POST",
            {"mil_on": mil,
             "dtcs": {"stored": stored, "pending": [], "permanent": []}})


def submit_and_wait(ip):
    pi(f"curl -s -m 8 -X POST http://{ip}/api/settings/submit")
    time.sleep(8)
    for _ in range(40):
        nip = dut_ip()
        if nip and api(nip, "/api/status").get("version"):
            time.sleep(4)
            return nip
        time.sleep(3)
    return None


def scan(ip, timeout=40):
    code = api(ip, "/api/autopid/dtc/scan", "POST", {})
    end = time.time() + timeout
    while time.time() < end:
        g = api(ip, "/api/autopid/dtc")
        if isinstance(g, dict) and g.get("report") and \
                not g.get("scanning"):
            return g.get("report", {})
        time.sleep(0.5)
    return {}


def scan_retry(ip, want_valid=True, tries=5):
    r = {}
    for i in range(tries):
        r = scan(ip)
        if r.get("valid") == want_valid and (not want_valid or
                                             r.get("stored") is not None):
            if r.get("valid"):
                return r
        print(f"  (scan attempt {i + 1}: {r.get('error', 'no report')})")
        time.sleep(5)
    return r


def main():
    ip = dut_ip()
    print("DUT", ip)

    if not api(ip, "/api/status").get("version"):
        print("FATAL: DUT unreachable")
        return 1

    try:
        sim_api("/api/ecu/dtcs")
    except Exception as e:
        print("FATAL: ECU sim unreachable at", SIM, e)
        return 1

    ap_before = api(ip, "/api/settings/autopid")
    ap_before.pop("degraded", None)
    ap_before.pop("pending_reboot", None)

    # ---- configure: forced uds, pending OFF (the sim's 19 02 ignores
    # the status mask and returns the stored list for any mask) ----
    sim_set_dtcs(["P0420", "P0171"], mil=True)
    cfg = dict(ap_before)
    cfg.update({"enabled": False, "dtc_enabled": True,
                "dtc_allow_clear": True, "dtc_pending": False,
                "dtc_permanent": False, "dtc_protocol": "uds",
                "dtc_uds_txid": "7E0", "dtc_uds_rxid": "7E8",
                "dtc_uds_ext": False, "dtc_uds_mask": 8,
                "dtc_init": "", "dtc_rxheader": ""})
    api(ip, "/api/settings/autopid", "PUT", cfg)
    print("configured (protocol=uds); rebooting…")
    ip = submit_and_wait(ip)
    if ip is None:
        print("FATAL: DUT did not come back")
        return 1
    time.sleep(15)  # obd_chip boot provisioning

    # ---- leg 1: UDS scan ----
    r = scan_retry(ip)
    check("leg1 scan valid over UDS", r.get("valid") is True,
          json.dumps(r)[:120])
    check("leg1 protocol == uds", r.get("protocol") == "uds",
          r.get("protocol"))
    check("leg1 stored exact (FTB 0 -> no suffix)",
          sorted(r.get("stored", [])) == ["P0171", "P0420"],
          r.get("stored"))

    # ---- leg 2: UDS clear (group FFFFFF) + rescan ----
    c = api(ip, "/api/autopid/dtc/clear", "POST",
            {"confirm": True, "mode": "always"})
    check("leg2 UDS clear confirmed",
          c.get("ok") is True and c.get("cleared") is True
          and c.get("before") == 2 and c.get("after") == 0, c)

    r = scan_retry(ip)
    check("leg2 rescan empty over UDS",
          r.get("valid") is True and r.get("stored") == [],
          r.get("stored"))

    # ---- leg 3: auto prefers OBD when mode 03 answers ----
    sim_set_dtcs(["P0300"], mil=True)
    cfg.update({"dtc_protocol": "auto",
                "dtc_init": "ATS1;ATH0;ATST96;atsp6;ATSH7DF"})
    api(ip, "/api/settings/autopid", "PUT", cfg)
    print("switching to auto; rebooting…")
    ip = submit_and_wait(ip)
    time.sleep(15)

    r = scan_retry(ip)
    check("leg3 auto picked OBD (mode 03 answered)",
          r.get("valid") is True and r.get("protocol") == "obd",
          r.get("protocol"))
    check("leg3 codes exact via OBD", r.get("stored") == ["P0300"],
          r.get("stored"))
    check("leg3 MIL from OBD 0101", r.get("mil") is True, r.get("mil"))

    # ---- leg 4: restore ----
    sim_api("/api/ecu/dtcs", "DELETE")
    api(ip, "/api/settings/autopid", "PUT", ap_before)
    print("restoring; rebooting…")
    ip = submit_and_wait(ip)
    f = api(ip, "/api/faults")
    check("restore + faults clean", f.get("faults") == [], f)

    if fails:
        print("UDS DTC BENCH FAIL: " + ", ".join(fails))
        return 1
    print("UDS DTC BENCH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
