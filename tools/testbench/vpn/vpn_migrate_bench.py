#!/usr/bin/env python3
"""The VPN settings migration routes the tunnel address's network (issue #988,
2026-10-16): the on-device proof, through the backup restore of a version-1
settings document.

v1 stored `address` + `allowed_ip` + `allowed_ip_mask`; the WireGuard netif
took the address with that netmask, so lwIP routed `address & mask`. The first
v2 migration wrote `allowed_ip & mask` instead, which differs whenever Allowed
IPs is not the tunnel subnet: the reporter's address 10.6.0.2 with Allowed IPs
192.168.86.0/24 lost the peer's own subnet and pulled a LAN into the tunnel.
`settings_manager_restore()` runs `on_migrate` for an object older than the
descriptor, so a version-1 vpn_manager object fed through POST
/api/backup/inspect + restore is the migration as a user meets it.

Legs (every restore is a planned restart):
  m0  as found: GET /api/settings/backup (the version-1 export, secrets
      verbatim) saved under the log dir; the vpn_manager object kept for m3
  m1  the reporter's case: a version-1 document with ONLY vpn_manager at
      version 1, address 10.6.0.2, allowed_ip 192.168.86.0, allowed_ip_mask
      255.255.255.0, enabled false (no tunnel attempt); inspected (not
      refused), restored; routes == "10.6.0.0/24" (the bug: "192.168.86.0/24")
  m2  the common case, allowed_ip = the tunnel subnet 10.6.0.0: "10.6.0.0/24";
      then the 0.0.0.0 idiom: routes "" (nothing but default_route)
  m3  as found put back: the saved object at its own version (no migration),
      every non-secret key equal, the VPN state as found
  close: no unexpected reset, no E line of vpn_manager / config_backup /
      settings_manager, the heap floor

  python tools/testbench/vpn/vpn_migrate_bench.py [--dut localhost:18081]
      [--dut-ip 10.42.1.194] [--pi rpi001] [--only m1,m2] [--logdir DIR]

Prints `VPN MIGRATE PASS`. Secrets are never printed: the as-found document
goes to the log dir only (the backup bench's practice).
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402

OWN_TAGS = ("vpn_manager", "config_backup", "settings_manager")
LEGS = ["m0", "m1", "m2", "m3"]
SECRETS = ("private_key", "peer_public_key", "preshared_key", "ts_auth_key")


def raw(base, path, method="GET", data=None, timeout=60):
    """(status, text) of one request with a raw body."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def wait_boot(dut, b0, limit=150):
    t0 = time.time()
    time.sleep(3)
    while time.time() - t0 < limit:
        b = dut.boot_count()
        if b is not None and b != b0:
            dut.restarts += 1
            return
        time.sleep(0.5)
    raise pcbench.Bench("the DUT did not come back within 150 s" + pcbench.GONE_HINT)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="", help="host:port of the DUT's API (a tunnel); "
                    "default: open one to --dut-ip through --pi")
    ap.add_argument("--dut-ip", default="10.42.1.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--only", default="", help="comma list of legs (m0 and m3 always run)")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS if not only or l in only or l in ("m0", "m3")]
    logdir = a.logdir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "test-reports", "logs", time.strftime("vpn_migrate_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("VPN MIGRATE")

    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18084"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-N", "-L",
                                   f"18084:{a.dut_ip}:80", a.pi])
        time.sleep(2)
    dut = pcbench.Dut(hostport, own_tags=OWN_TAGS)
    base = dut.base

    def save(name, text):
        with open(os.path.join(logdir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def restore_doc(tag, doc):
        """Inspect a version-1 document and restore its settings part."""
        text = json.dumps(doc)
        code, t = raw(base, f"/api/backup/inspect?name={tag}.json", "POST", text.encode())
        man = json.loads(t) if code == 200 else {}
        save(f"{tag}_inspect.json", t)
        ok = code == 200 and man.get("version") == 1 and not man.get("refused") \
            and list(man.get("parts", {})) == ["settings"] and man.get("errors") == {}
        run.check(f"{tag}_inspect_accepts_the_version_1_object", ok,
                  f"{code} {str({k: man.get(k) for k in ('version', 'refused', 'errors', 'parts')})[:200]}")
        if not ok:
            raise pcbench.Bench(f"{tag}: the document was refused")
        b0 = dut.boot_count()
        dut.sweep()
        code, r = dut.api("/api/backup/restore", "POST",
                          {"include": ["settings"], "keep_identity": True,
                           "keep_vpn_keys": False}, timeout=120)
        ok = code == 200 and isinstance(r, dict) and r.get("reboot") is True
        run.check(f"{tag}_restore_accepted", ok, f"{code} {str(r)[:200]}")
        if not ok:
            raise pcbench.Bench(f"{tag}: restore refused")
        wait_boot(dut, b0)
        time.sleep(4)

    def v1_doc(data, version):
        return {"wican_backup": 1, "device_id": info.get("device_id"),
                "components": {"vpn_manager": {"version": version, "data": data}}}

    def legacy(address, allowed_ip, mask):
        """The as-found object as a v1 object: the old pair in, routes out."""
        d = dict(found_data)
        d.pop("routes", None)
        d["address"] = address
        d["allowed_ip"] = allowed_ip
        d["allowed_ip_mask"] = mask
        d["enabled"] = False
        return d

    exit_code = 1
    try:
        # ---- m0: as found ---------------------------------------------------------
        info = dut.get("/api/info")
        st0 = dut.get("/api/status")
        dut.as_found()
        print(f"DUT {info.get('device_id')} {info.get('fw_version')}", flush=True)
        code, text = raw(base, "/api/settings/backup")
        found = json.loads(text) if code == 200 else {}
        found_vpn = found.get("components", {}).get("vpn_manager") or {}
        found_data = found_vpn.get("data") or {}
        found_ver = found_vpn.get("version")
        save("m0_as_found_settings_v1.json", text)   # secrets inside: the log dir only
        run.check("m0_as_found_vpn_object", code == 200 and isinstance(found_data, dict)
                  and "routes" in found_data and isinstance(found_ver, int),
                  f"{code} version {found_ver} keys {len(found_data)}")
        if not found_data:
            raise pcbench.Bench("no vpn_manager object in the settings export")
        vpn0 = dut.get("/api/vpn")
        run.metric("routes_as_found", found_data.get("routes", ""))
        print(f"as found: vpn state {vpn0.get('state')}, routes {found_data.get('routes')!r}",
              flush=True)

        # ---- m1: the reporter's case -------------------------------------------------
        if "m1" in legs:
            restore_doc("m1", v1_doc(legacy("10.6.0.2", "192.168.86.0", "255.255.255.0"), 1))
            s = dut.settings("vpn_manager")
            run.check("m1_routes_is_the_tunnel_address_network",
                      s.get("routes") == "10.6.0.0/24",
                      f"routes {s.get('routes')!r} (the bug gave '192.168.86.0/24')")
            run.check("m1_old_keys_dropped",
                      "allowed_ip" not in s and "allowed_ip_mask" not in s,
                      ",".join(k for k in s if k.startswith("allowed")))
            run.check("m1_address_kept", s.get("address") == "10.6.0.2", repr(s.get("address")))
            run.check("m1_stays_disabled", s.get("enabled") is False
                      and dut.get("/api/vpn").get("state") == "disabled")

        # ---- m2: the common case, then the 0.0.0.0 idiom ---------------------------
        if "m2" in legs:
            restore_doc("m2a", v1_doc(legacy("10.6.0.2", "10.6.0.0", "255.255.255.0"), 1))
            s = dut.settings("vpn_manager")
            run.check("m2_tunnel_subnet_as_allowed_ips_gives_the_same_route",
                      s.get("routes") == "10.6.0.0/24", repr(s.get("routes")))
            restore_doc("m2b", v1_doc(legacy("10.6.0.2", "0.0.0.0", "0.0.0.0"), 1))
            s = dut.settings("vpn_manager")
            run.check("m2_the_0000_idiom_gives_no_route", s.get("routes") == "",
                      repr(s.get("routes")))

        # ---- m3: as found put back ------------------------------------------------------
        restore_doc("m3", v1_doc(found_data, found_ver))
        s = dut.settings("vpn_manager")
        diff = sorted(k for k in found_data if k not in SECRETS
                      and s.get(k) != found_data.get(k))
        run.check("m3_as_found_object_back", not diff, "differs: " + ", ".join(diff))
        vpn3 = dut.get("/api/vpn")
        run.check("m3_vpn_state_as_found", vpn3.get("state") == vpn0.get("state"),
                  f"{vpn3.get('state')} vs {vpn0.get('state')}")

        # ---- close -----------------------------------------------------------------------
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        exit_code = run.verdict("legs: " + " ".join(legs) if only else "")
    except pcbench.Bench as e:
        print("BENCH:", e, flush=True)
        print(f"the as-found settings are in {logdir}/m0_as_found_settings_v1.json "
              "(restore through POST /api/backup/inspect + restore)", flush=True)
        exit_code = 2
    finally:
        if tunnel is not None:
            tunnel.terminate()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
