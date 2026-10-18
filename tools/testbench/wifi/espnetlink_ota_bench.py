#!/usr/bin/env python3
"""The ESPNetLink dongle updated through the WiCAN PRO (2026-10-19,
internal/TASK_fw_update.md sections 6 and 9.3): the bench.

The claim: the WiCAN reads the dongle's own release list, offers what its
channel allows, stages the image in PSRAM, verifies it (size, sha256, an
application image whose project name is the dongle's, the version the list
names), pushes it into the dongle's upload route over the USB link, waits
for the dongle's restart and reads the new version back; a wrong image or
digest never reaches the dongle; the WiCAN itself never restarts.

Rig (TESTING.md): the DUT on the bench AP reached over HTTP through an ssh
tunnel (--dut localhost:18081; test.ps1 opens it, /ws/cli rides the same
port), the dongle on the connector in usb_rndis mode, rpi001 serving the
release lists and the images over HTTPS with the bench CA (actors/fw_server.py
as the transient unit fw_bench on --port 8446; the server certificate for
--pi-ip in ~/wican-tls-fwbench, --plain = http). Images: the dongle build
named by --dongle-image (default: the newest espnetlink-fw_*.bin in the
dongle repo's build directory) and copies of it with a patched version
(lib/fw_image.py): N1 1.22.1-alfa.1, N0 1.22.0-beta.1; the WiCAN's own image
for the wrong-product leg (tools/fw_bin.py).

Legs:
  o0  preflight + setup: the DUT on the local build, a dongle present and
      reachable over USB with api >= 7, the bench CA as cert set fwbench, the
      fw_update settings staged (base_url, cert_set, channel alfa) and
      submitted (one planned WiCAN restart); the images patched and uploaded,
      the lists published (the WiCAN's with its running build, the dongle's)
  o1  the dongle row: present, reachable, the installed fw_version, N1
      offered with its summary; the notes proxied for the dongle
  o2  the install of N1 over USB: 202, the job through checking, downloading,
      verifying, pushing (transport usb), rebooting, pending_verify, verified;
      METRIC push_s and total_s; the dongle reports N1; the WiCAN did not
      restart; the link is back in ncm_up; a second install = already
  o3  the downgrade to N0, named explicitly: verified, the dongle reports N0
  o4  a wrong digest in the list: failed sha256_mismatch at verifying,
      nothing pushed (the dongle still reports N0)
  o5  the WiCAN's own image listed as a dongle release: failed wrong_product
      at verifying, nothing pushed
  o6  no_memory: max_stage_bytes 65536 refuses before any byte
  o7  (--unplug) the USB link cut over the console (usb vbus 0) during the
      push: push_failed, the dongle back with its old firmware, then o2 again
  o8  restore: the original dongle image back (its own version string)
  close: the settings as found, the cert set deleted, the server stopped, no
      unexpected WiCAN reset, no E line of the own tags, the fw_update task's
      stack headroom, the heap floor, 0 faults

  python tools/testbench/wifi/espnetlink_ota_bench.py [--dut localhost:18081]
      [--dut-ip 10.42.1.194] [--pi rpi001] [--pi-ip 10.42.1.1] [--port 8446]
      [--plain] [--dongle-image PATH] [--unplug] [--only o1,o2] [--logdir DIR]

Prints `ESPNL OTA PASS` (or `ESPNL OTA PARTIAL PASS (legs: ...)`).
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import fw_image  # noqa: E402
import pcbench  # noqa: E402
from ssh_channel import SshChannel  # noqa: E402
from wsmin import OP_TEXT, WS  # noqa: E402

OWN_TAGS = ("fw_update", "ota_manager", "http_client_manager", "espnetlink", "api_http")
LEGS = ["o0", "o1", "o2", "o3", "o4", "o5", "o6", "o8"]
OPT_IN = ["o7"]
VER_W = "1.22.9-alfa.1"  # a foreign-project image; VER_N0/VER_N1 are derived per run (bump_versions)
PI_ROOT = "/home/meatpi/wican-http/fw"
SETTINGS_KEYS = ("enabled", "channel", "check_interval_h", "base_url", "cert_set")
DONGLE_REPO_BUILD = r"C:\Users\Ali\Desktop\Projects\ESPNetLink_Project\firmware\espnetlink-fw\build"
JOB_ORDER = ["checking", "downloading", "verifying", "pushing", "rebooting", "pending_verify", "verified"]


def raw(base, path, method="GET", data=None, ctype="application/octet-stream", timeout=120):
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def newest_dongle_image():
    cands = [p for p in glob.glob(os.path.join(DONGLE_REPO_BUILD, "espnetlink-fw*.bin")) if "ota_data" not in p]
    if not cands:
        return ""
    return max(cands, key=os.path.getmtime)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="", help="host:port of the DUT's API (a tunnel)")
    ap.add_argument("--dut-ip", default="10.42.1.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--pi-ip", default="10.42.1.1", help="the Pi as the DUT reaches it")
    ap.add_argument("--port", type=int, default=8446)
    ap.add_argument("--plain", action="store_true", help="serve over http (no bench CA)")
    ap.add_argument("--tls-dir", default="/home/meatpi/wican-tls-fwbench")
    ap.add_argument("--dongle-image", default="", help="the dongle build (default: the newest in the dongle repo)")
    ap.add_argument("--unplug", action="store_true", help="run o7 (the USB link cut mid-push over the console)")
    ap.add_argument("--only", default="")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS + (OPT_IN if a.unplug else []) if not only or l in only or l == "o0"]
    logdir = a.logdir or os.path.join(HERE, "..", "..", "..", "test-reports", "logs",
                                      time.strftime("espnetlink_ota_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("ESPNL OTA")
    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18081"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-N", "-L", "18081:%s:80" % a.dut_ip, a.pi])
        time.sleep(2)
    host, port = hostport.split(":")[0], int(hostport.split(":")[1]) if ":" in hostport else 80
    dut = pcbench.Dut(hostport, own_tags=OWN_TAGS, tasks=("fw_update",))
    base = dut.base
    pi = SshChannel(a.pi)
    scheme = "http" if a.plain else "https"
    base_url = "%s://%s:%d/" % (scheme, a.pi_ip, a.port)

    def save(name, text):
        with open(os.path.join(logdir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def pi_run(cmd, timeout=60):
        return pi.run(cmd, timeout=timeout)

    def scp(local, remote):
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", local, "%s:%s" % (a.pi, remote)], check=True, timeout=600)

    def server_reset():
        pi_run("curl -sk -m 5 -X POST '%s://127.0.0.1:%d/__reset'" % (scheme, a.port))

    def server_dump():
        rc, out = pi_run("curl -sk -m 5 '%s://127.0.0.1:%d/__dump'" % (scheme, a.port))
        try:
            return json.loads(out)
        except ValueError:
            return {"requests": []}

    images = {}   # name -> {"path", "file", "size", "sha256", "version", "project"}

    def dongle_release(name, version, channel="alfa", **extra):
        img = images[name]
        rel = {"version": version, "fw_version": extra.pop("fw_version", img["version"]), "tag": "v%s" % version,
               "channel": channel, "date": "2026-10-19", "summary": "Bench dongle release %s (%s)." % (version, name),
               "notes": "espnetlink/firmware/notes/%s.md" % version,
               "image": {"url": "%s://%s:%d/dl/%s" % (scheme, a.pi_ip, a.port, img["file"]), "size": img["size"],
                         "sha256": extra.pop("sha256", None) or img["sha256"], "project_name": img["project"]},
               "layout": {"md5": "4ab851ed091a20b0eb35046eba065189", "table": "espnetlink/firmware/layouts/x.csv"},
               "yanked": False}
        rel.update(extra)
        return rel

    def publish_dongle(releases):
        doc = {"schema": 1, "device_type": "espnetlink", "model": "ESPNetLink", "project_name": "espnetlink-fw",
               "source_repo": "meatpiHQ/espnetlink-fw", "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "channels": {"stable": {"latest": None}, "beta": {"latest": None}, "alfa": {"latest": None}},
               "releases": releases}
        tmp = tempfile.mkdtemp(prefix="espnlota_")
        mpath = os.path.join(tmp, "manifest.json")
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        for r in releases:
            npath = os.path.join(tmp, "%s.md" % r["version"])
            with open(npath, "w", encoding="utf-8") as f:
                f.write("# ESPNetLink %s\n\n## New features\n- The bench dongle release %s.\n- **Bold** and `code` render.\n"
                        % (r["version"], r["version"]))
            scp(npath, "%s/espnetlink/firmware/notes/%s.md" % (PI_ROOT, r["version"]))
        scp(mpath, "%s/espnetlink/firmware/manifest.json" % PI_ROOT)
        save("dongle_manifest_%s.json" % time.strftime("%H%M%S"), json.dumps(doc, indent=1))
        server_reset()

    def publish_wican(a_info):
        """The WiCAN's own list: the running build as its one stable release, so the self check is clean."""
        doc = {"schema": 1, "device_type": "wican_pro", "model": "WiCAN PRO", "project_name": "wican-fw_obd_pro",
               "source_repo": "meatpiHQ/wican-fw", "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "channels": {"stable": {"latest": None}, "beta": {"latest": None}, "alfa": {"latest": None}},
               "releases": [{"version": a_info["version"], "fw_version": a_info["version"], "tag": "v%sp" % a_info["version"],
                             "channel": "stable", "date": "2026-10-19", "summary": "The running build.",
                             "notes": "wican_pro/firmware/notes/%s.md" % a_info["version"],
                             "image": {"url": "%s://%s:%d/dl/none.bin" % (scheme, a.pi_ip, a.port), "size": a_info["size"],
                                       "sha256": a_info["sha256"], "project_name": a_info["project_name"]},
                             "layout": {"md5": a_info["layout_md5"], "table": "wican_pro/firmware/layouts/x.csv"},
                             "yanked": False}]}
        tmp = tempfile.mkdtemp(prefix="espnlota_w_")
        mpath = os.path.join(tmp, "manifest.json")
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        scp(mpath, "%s/wican_pro/firmware/manifest.json" % PI_ROOT)

    # ---- the device -----------------------------------------------------------------------
    def fwu():
        return dut.get("/api/fw_update")

    def dongle():
        return (fwu().get("targets") or {}).get("dongle") or {}

    def check(wait=45):
        """POST check, then wait for the check (both lists) to finish."""
        before = fwu().get("last_check") or {}
        n0 = before.get("count", 0)
        code, r = dut.api("/api/fw_update/check", "POST", {})
        if code not in (200, 202):
            raise pcbench.Bench("POST check: %s %s" % (code, r))
        end = time.time() + wait
        while time.time() < end:
            st = fwu()
            lc = st.get("last_check") or {}
            if lc.get("count", 0) > n0 and not (st.get("job") or {}).get("active"):
                return st
            time.sleep(1)
        raise pcbench.Bench("the check did not finish within %d s" % wait)

    def install(body):
        return dut.api("/api/fw_update/install", "POST", body, timeout=30)

    def follow_job(job_id, limit=360):
        """Poll the job to its end; the states seen in order with their first stamps."""
        seen = []
        stamps = {}
        t0 = time.time()
        last = None
        while time.time() - t0 < limit:
            code, st = dut.api("/api/fw_update", timeout=6)
            if code == 0:
                time.sleep(1)   # a poll can time out mid-stream: keep going
                continue
            if code == 200 and isinstance(st, dict):
                last = st
                j = st.get("job") or {}
                if j.get("id") == job_id:
                    s = j.get("state")
                    if s and (not seen or seen[-1] != s):
                        seen.append(s)
                        stamps[s] = round(time.time() - t0, 1)
                        print("   %6.1f s  %s %s%%" % (time.time() - t0, s, j.get("pct")), flush=True)
                    if not j.get("active"):
                        return st, seen, stamps
            time.sleep(1)
        raise pcbench.Bench("the dongle job %s did not end within %d s (%s)" % (job_id, limit, json.dumps((last or {}).get("job"))[:300]))

    def cli(cmd, marker="OK", timeout=20):
        ws = WS(host, port, "/ws/cli", timeout=10)
        try:
            ws.send((cmd + "\n").encode(), opcode=OP_TEXT)
            out, end = "", time.time() + timeout
            while time.time() < end:
                try:
                    op, payload = ws.recv_frame()
                except Exception:
                    break
                if op in (OP_TEXT, 2):
                    out += payload.decode("utf-8", "replace")
                    if marker and marker in out:
                        break
            return out
        finally:
            try:
                ws.s.close()
            except Exception:
                pass

    def espnl():
        return dut.get("/api/espnetlink")

    def wait_link(limit=90):
        """The dongle paired again over USB (ncm_up) after its restart."""
        t0 = time.time()
        while time.time() - t0 < limit:
            e = espnl()
            if (e.get("usb") or {}).get("pair_state") in ("ncm_up",) and e.get("on_link"):
                return e
            time.sleep(2)
        return espnl()

    def running():
        return (dongle().get("installed") or {}).get("fw_version")

    def bump_versions(cur):
        """Two alfa releases strictly NEWER than the dongle's installed
        version, newest last: (N0, N1) with installed < N0 < N1 on the same
        major.minor.patch. For a real semver like 1.23.0-alfa.1 that is
        1.23.0-alfa.2 / -alfa.3 (so the row offers N1 and o3 downgrades to
        N0); for a tolerant/dev describe (v1.22-...-g...) a fixed pair above
        its tolerant 1.22.0. The dongle may already run a prior run's N1, so
        bumping off the installed keeps climbing (2026-10-19)."""
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)-alfa\.(\d+)$", cur or "")
        if m:
            base = "%s.%s.%s-alfa." % (m.group(1), m.group(2), m.group(3))
            n = int(m.group(4))
            return base + str(n + 1), base + str(n + 2)
        return "1.22.1-alfa.1", "1.22.1-alfa.2"

    VER_N0, VER_N1 = "1.22.1-alfa.1", "1.22.1-alfa.2"
    found = {}
    exit_code = 1
    st0 = None
    server_up = False
    cert_uploaded = False
    try:
        # ---- o0: preflight + setup -----------------------------------------------------------
        print("== o0 preflight + setup", flush=True)
        dut.as_found()
        st0 = dut.get("/api/status")
        inf = dut.get("/api/info")
        code, st = dut.api("/api/fw_update")
        run.check("o0_fw_update_route", code == 200 and isinstance(st, dict) and "targets" in st, "%s %s" % (code, str(st)[:120]))
        a_path = subprocess.check_output([sys.executable, os.path.join(HERE, "..", "..", "fw_bin.py")], text=True).strip()
        with open(a_path, "rb") as f:
            a_data = f.read()
        a_info = fw_image.info(a_data)
        a_info["layout_md5"] = inf.get("layout_md5", "")
        run.check("o0_dut_runs_the_local_build", inf.get("fw_version") == a_info["version"],
                  "DUT %s, build %s (%s)" % (inf.get("fw_version"), a_info["version"], a_path))
        if inf.get("fw_version") != a_info["version"]:
            raise pcbench.Bench("flash the DUT with this build first (python tools/fw_bin.py)")
        d = (st.get("targets") or {}).get("dongle") or {}
        run.check("o0_relay_wired", d.get("verdict") != "not_built", json.dumps(d)[:200])
        run.check("o0_dongle_present_over_usb", d.get("present") is True and d.get("reachable") is True and d.get("transport") == "usb",
                  json.dumps({k: d.get(k) for k in ("present", "reachable", "transport", "verdict")}))
        inst = d.get("installed") or {}
        run.check("o0_dongle_api_level_7_or_more", (inst.get("api_level") or 0) >= 7, json.dumps(inst))
        run.metric("dongle_fw_as_found", inst.get("fw_version"))
        if not d.get("present") or not d.get("reachable"):
            raise pcbench.Bench("no dongle reachable over USB: plug the ESPNetLink into the DUT in usb_rndis mode")
        found["settings"] = dut.settings("fw_update")
        found["dongle_fw"] = inst.get("fw_version", "")
        VER_N0, VER_N1 = bump_versions(found["dongle_fw"])
        run.metric("bench_up_versions", "%s then %s (installed %s)" % (VER_N0, VER_N1, found["dongle_fw"]))
        # the images
        d_path = a.dongle_image or newest_dongle_image()
        if not d_path or not os.path.exists(d_path):
            raise pcbench.Bench("no dongle image: pass --dongle-image (the build in %s)" % DONGLE_REPO_BUILD)
        with open(d_path, "rb") as f:
            d_data = f.read()
        d_info = fw_image.info(d_data)
        run.check("o0_dongle_image_is_espnetlink", d_info["project_name"].startswith("espnetlink-fw") and d_info["checksum_ok"] and d_info["digest_ok"],
                  json.dumps({k: d_info.get(k) for k in ("project_name", "version", "size")}))
        run.metric("dongle_image", "%s (%s, %d B)" % (os.path.basename(d_path), d_info["version"], d_info["size"]))
        tmp = tempfile.mkdtemp(prefix="espnlota_img_")
        # W: the dongle image under a foreign project name (the relay's product check);
        # WX: the WiCAN's own image, which cannot even be staged on the 4 MB PSRAM
        variants = {"ORIG": (None, None, d_data), "N1": (VER_N1, None, d_data), "N0": (VER_N0, None, d_data),
                    "W": (VER_W, "wican-fw_bench", d_data), "WX": (None, None, a_data)}
        for name, (ver, proj, src) in variants.items():
            data = src if ver is None and proj is None else fw_image.patch(src, version=ver, project=proj)
            if not fw_image.verify(data):
                raise pcbench.Bench("patched image %s does not verify" % name)
            fn = "espnetlink-fw_bench_%s.bin" % name if name != "WX" else "wican-fw_obd_pro_bench_WX.bin"
            p = os.path.join(tmp, fn)
            with open(p, "wb") as f:
                f.write(data)
            i = fw_image.info(data)
            images[name] = {"path": p, "file": fn, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                            "version": i["version"], "project": i["project_name"]}
        run.check("o0_images_patched", images["N1"]["version"] == VER_N1 and images["N0"]["version"] == VER_N0 and images["W"]["project"] == "wican-fw_bench" and images["WX"]["project"].startswith("wican-fw"))
        # the server
        pi_run("mkdir -p %s/wican_pro/firmware/notes %s/espnetlink/firmware/notes" % (PI_ROOT, PI_ROOT))
        tls_dir = a.tls_dir
        if not a.plain:
            rc, _ = pi_run("test -d %s" % a.tls_dir)
            if rc != 0:
                tls_dir = "/home/meatpi/wican-tls"
        scp(os.path.join(HERE, "..", "actors", "fw_server.py"), "/tmp/fw_server.py")
        for name in images:
            scp(images[name]["path"], "%s/%s" % (PI_ROOT, images[name]["file"]))
        pi_run("sudo -n systemctl stop fw_bench 2>/dev/null; sudo -n systemd-run --unit=fw_bench --collect -p User=meatpi "
               "-p WorkingDirectory=/home/meatpi python3 -u /tmp/fw_server.py --root %s --port %d %s --redirect-images"
               % (PI_ROOT, a.port, "--plain" if a.plain else "--tls-dir %s" % tls_dir))
        time.sleep(2)
        rc, out = pi_run("curl -sk -m 5 '%s://127.0.0.1:%d/__dump' | head -c 80" % (scheme, a.port))
        server_up = rc == 0 and "requests" in out
        run.check("o0_server_up", server_up, out[:160])
        if not server_up:
            raise pcbench.Bench("the release server did not come up on the Pi (journalctl -u fw_bench)")
        if not a.plain:
            rc, ca = pi_run("cat %s/ca.pem" % tls_dir)
            code, r = raw(base, "/api/certs/upload?set=fwbench&type=ca", "POST", ca.encode(), "application/x-pem-file", 30)
            cert_uploaded = code == 200
            run.check("o0_bench_ca_uploaded", cert_uploaded, "%s %s" % (code, r[:120]))
        publish_wican(a_info)
        publish_dongle([dongle_release("N1", VER_N1), dongle_release("N0", VER_N0, "beta")])
        dut.restart({"fw_update": {"enabled": True, "channel": "alfa", "check_interval_h": 24,
                                   "base_url": base_url, "cert_set": "" if a.plain else "fwbench"}})
        st = fwu()
        run.check("o0_settings_applied", st.get("channel") == "alfa" and st.get("enabled") is True, json.dumps({k: st.get(k) for k in ("channel", "enabled")}))
        e = wait_link(60)
        run.check("o0_dongle_back_after_the_restart", (e.get("usb") or {}).get("pair_state") == "ncm_up", json.dumps(e.get("usb")))

        # ---- o1: the row ---------------------------------------------------------------------------
        if "o1" in legs:
            print("== o1 the dongle row", flush=True)
            st = check()
            d = (st.get("targets") or {}).get("dongle") or {}
            run.check("o1_self_check_ok", (st.get("last_check") or {}).get("ok") is True, json.dumps(st.get("last_check")))
            run.check("o1_dongle_check_ok", (d.get("last_check") or {}).get("ok") is True, json.dumps(d.get("last_check")))
            run.check("o1_dongle_installed_as_found", (d.get("installed") or {}).get("fw_version") == found["dongle_fw"], json.dumps(d.get("installed")))
            run.check("o1_n1_offered", d.get("verdict") == "update_available" and d.get("available") is True and d.get("installable") is True and d.get("target_version") == VER_N1,
                      json.dumps({k: d.get(k) for k in ("verdict", "available", "installable", "target_version", "reason")}))
            run.check("o1_latest_summary", "Bench dongle release" in (d.get("latest") or {}).get("summary", "") and (d.get("latest") or {}).get("size") == images["N1"]["size"], json.dumps(d.get("latest")))
            code, txt = raw(base, "/api/fw_update/notes?target=dongle&version=%s" % VER_N1, "GET", None, "text/plain", 30)
            run.check("o1_dongle_notes_proxied", code == 200 and txt.startswith("# ESPNetLink %s" % VER_N1), "%s %s" % (code, txt[:80]))
            run.check("o1_uplink_is_wifi", (st.get("cellular") or {}).get("is_cellular") is False, json.dumps(st.get("cellular")))

        # ---- o2: the install over USB ------------------------------------------------------------
        if "o2" in legs:
            print("== o2 install %s over USB" % VER_N1, flush=True)
            b0 = dut.boot_count()
            code, r = install({"target": "dongle", "version": VER_N1})
            run.check("o2_install_accepted", code == 202 and isinstance(r, dict) and r.get("target") == "dongle" and r.get("version") == VER_N1, "%s %s" % (code, str(r)[:160]))
            if code != 202:
                raise pcbench.Bench("the dongle install was refused: %s %s" % (code, r))
            job_id = r.get("job_id")
            t0 = time.time()
            st, seen, stamps = follow_job(job_id)
            total_s = round(time.time() - t0, 1)
            j = st.get("job") or {}
            save("o2_job.json", json.dumps({"seen": seen, "stamps": stamps, "job": j}, indent=1))
            run.check("o2_job_verified", j.get("state") == "verified" and not j.get("active"), json.dumps(j)[:300])
            order = [s for s in seen if s in JOB_ORDER]
            run.check("o2_states_in_order", order == sorted(order, key=JOB_ORDER.index) and "pushing" in order and "rebooting" in order, json.dumps(seen))
            run.check("o2_transport_usb", j.get("transport") == "usb", j.get("transport"))
            if "pushing" in stamps and "rebooting" in stamps:
                run.metric("o2_push_s", round(stamps["rebooting"] - stamps["pushing"], 1), "s")
            run.metric("o2_total_s", total_s, "s")
            d = dongle()
            run.check("o2_dongle_reports_n1", (d.get("installed") or {}).get("fw_version") == VER_N1, json.dumps(d.get("installed")))
            lr = d.get("last_result") or {}
            run.check("o2_last_result_verified", lr.get("state") == "verified" and lr.get("to") == VER_N1 and lr.get("from") == found["dongle_fw"], json.dumps(lr))
            run.metric("o2_push_s_reported", lr.get("push_s"), "s")
            run.check("o2_wican_did_not_restart", dut.boot_count() == b0, "boot_count %s -> %s" % (b0, dut.boot_count()))
            e = wait_link(30)
            run.check("o2_link_back_ncm_up", (e.get("usb") or {}).get("pair_state") == "ncm_up" and e.get("dongle_fw") == VER_N1, json.dumps({"usb": e.get("usb"), "dongle_fw": e.get("dongle_fw")}))
            run.check("o2_row_up_to_date_now", d.get("verdict") == "up_to_date" and d.get("available") is False, json.dumps({k: d.get(k) for k in ("verdict", "available", "reason")}))
            # the pairing pass after the dongle's restart enforces AGNSS (Ali,
            # 2026-10-19): the ensure step runs within the re-pair sequence, so
            # give it a few seconds past ncm_up to report
            prefs = {}
            for _ in range(10):
                prefs = (espnl().get("prefs") or {})
                if prefs.get("agnss") in ("on", "enabled"):
                    break
                time.sleep(2)
            run.check("o2_agnss_on_after_the_pass", prefs.get("agnss") in ("on", "enabled"), json.dumps(prefs))
            run.metric("o2_prefs", json.dumps(prefs))
            code, r = install({"target": "dongle", "version": VER_N1})
            run.check("o2_second_install_is_already", code == 200 and isinstance(r, dict) and r.get("already") is True and r.get("installed") == VER_N1, "%s %s" % (code, str(r)[:120]))

        # ---- o3: the downgrade -----------------------------------------------------------------
        if "o3" in legs:
            print("== o3 downgrade to %s" % VER_N0, flush=True)
            code, r = install({"target": "dongle", "version": VER_N0})
            run.check("o3_explicit_downgrade_accepted", code == 202, "%s %s" % (code, str(r)[:160]))
            if code == 202:
                st, seen, stamps = follow_job(r.get("job_id"))
                j = st.get("job") or {}
                run.check("o3_job_verified", j.get("state") == "verified", json.dumps(j)[:300])
                d = dongle()
                run.check("o3_dongle_reports_n0", (d.get("installed") or {}).get("fw_version") == VER_N0, json.dumps(d.get("installed")))
                wait_link(30)

        # ---- o4: a wrong digest ----------------------------------------------------------------
        if "o4" in legs:
            print("== o4 wrong sha256 in the list", flush=True)
            # whichever of the two the dongle does not run (o3 may have failed)
            bad_name, bad_ver = ("N0", VER_N0) if running() == VER_N1 else ("N1", VER_N1)
            publish_dongle([dongle_release("N1", VER_N1, sha256="0" * 64 if bad_name == "N1" else None),
                            dongle_release("N0", VER_N0, "beta", sha256="0" * 64 if bad_name == "N0" else None)])
            check()
            code, r = install({"target": "dongle", "version": bad_ver})
            run.check("o4_install_accepted", code == 202, "%s %s" % (code, str(r)[:160]))
            if code == 202:
                st, seen, stamps = follow_job(r.get("job_id"))
                j = st.get("job") or {}
                run.check("o4_failed_sha256_mismatch", j.get("state") == "failed" and j.get("error") == "sha256_mismatch", json.dumps(j)[:300])
                run.check("o4_nothing_pushed", "pushing" not in seen and running() != bad_ver, json.dumps(seen))

        # ---- o5: the WiCAN's image as a dongle release ----------------------------------------------
        if "o5" in legs:
            print("== o5 wrong product (a foreign project name), the oversize image", flush=True)
            # the WiCAN's own 3.8 MB image: refused before a byte, it cannot be staged
            publish_dongle([dongle_release("WX", "1.22.8-alfa.1", fw_version="1.22.8-alfa.1"), dongle_release("N0", VER_N0, "beta")])
            check()
            code, r = install({"target": "dongle", "version": "1.22.8-alfa.1"})
            run.check("o5_oversize_refused_no_memory", code == 409 and isinstance(r, dict) and r.get("code") == "no_memory", "%s %s" % (code, str(r)[:160]))
            publish_dongle([dongle_release("W", VER_W), dongle_release("N0", VER_N0, "beta")])
            check()
            code, r = install({"target": "dongle", "version": VER_W})
            run.check("o5_install_accepted", code == 202, "%s %s" % (code, str(r)[:160]))
            if code == 202:
                st, seen, stamps = follow_job(r.get("job_id"))
                j = st.get("job") or {}
                run.check("o5_failed_wrong_product", j.get("state") == "failed" and j.get("error") == "wrong_product", json.dumps(j)[:300])
                run.check("o5_nothing_pushed", "pushing" not in seen and running() != VER_W, json.dumps(seen))
            publish_dongle([dongle_release("N1", VER_N1), dongle_release("N0", VER_N0, "beta")])
            check()

        # ---- o6: no memory -----------------------------------------------------------------------
        if "o6" in legs:
            print("== o6 no_memory", flush=True)
            code, r = install({"target": "dongle", "version": VER_N0 if running() == VER_N1 else VER_N1, "max_stage_bytes": 65536})
            run.check("o6_refused_no_memory", code == 409 and isinstance(r, dict) and r.get("code") == "no_memory", "%s %s" % (code, str(r)[:160]))
            d = server_dump()
            run.check("o6_no_download_started", not any(r2["path"].endswith(images["N1"]["file"]) for r2 in d.get("requests", []) if r2.get("t", 0)), "")

        # ---- o7: the USB link cut mid-push (opt-in) -------------------------------------------------
        if "o7" in legs:
            print("== o7 unplug mid-push", flush=True)
            code, r = install({"target": "dongle", "version": VER_N1})
            if code != 202:
                raise pcbench.Bench("o7: the install was refused: %s %s" % (code, r))
            cut = False
            t0 = time.time()
            while time.time() - t0 < 300:
                j = fwu().get("job") or {}
                if j.get("state") == "pushing" and (j.get("pct") or 0) >= 25 and not cut:
                    cli("usb vbus 0", None, 3)
                    cut = True
                    time.sleep(5)
                    cli("usb vbus 1", None, 3)
                if not j.get("active"):
                    break
                time.sleep(1)
            j = fwu().get("job") or {}
            run.check("o7_push_failed", cut and j.get("state") == "failed" and j.get("error") in ("push_failed", "timeout_reboot"), json.dumps(j)[:300])
            e = wait_link(90)
            run.check("o7_dongle_back_old_fw", (e.get("usb") or {}).get("pair_state") == "ncm_up" and e.get("dongle_fw") == VER_N0, json.dumps({"usb": e.get("usb"), "dongle_fw": e.get("dongle_fw")}))
            code, r = install({"target": "dongle", "version": VER_N1})
            if code == 202:
                st, seen, stamps = follow_job(r.get("job_id"))
                run.check("o7_install_again_verified", (st.get("job") or {}).get("state") == "verified", json.dumps(st.get("job"))[:200])
                wait_link(30)

        # ---- o8: restore ---------------------------------------------------------------------------
        if "o8" in legs:
            print("== o8 restore the original dongle image", flush=True)
            orig = images["ORIG"]
            publish_dongle([dongle_release("ORIG", "1.22.99-alfa.1", fw_version=orig["version"]), dongle_release("N0", VER_N0, "beta")])
            check()
            code, r = install({"target": "dongle", "version": "1.22.99-alfa.1"})
            run.check("o8_restore_accepted", code == 202 or (code == 200 and isinstance(r, dict) and r.get("already")), "%s %s" % (code, str(r)[:160]))
            if code == 202:
                st, seen, stamps = follow_job(r.get("job_id"))
                run.check("o8_restore_verified", (st.get("job") or {}).get("state") == "verified", json.dumps(st.get("job"))[:200])
            e = wait_link(60)
            run.check("o8_dongle_reports_the_original_version", e.get("dongle_fw") == orig["version"], e.get("dongle_fw"))

        # ---- close ----------------------------------------------------------------------------------
        print("== close", flush=True)
        if found.get("settings"):
            dut.restart({"fw_update": {k: found["settings"][k] for k in SETTINGS_KEYS if k in found["settings"]}})
        if cert_uploaded:
            dut.api("/api/certs?set=fwbench", "DELETE")
        wait_link(60)
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        run.check("close_no_latched_faults", dut.get("/api/status").get("health", {}).get("faults", 0) == 0)
        exit_code = run.verdict("legs: " + " ".join(legs) if only else "")
    except pcbench.Bench as e:
        print("BENCH:", e, flush=True)
        exit_code = 2
    finally:
        try:
            if server_up:
                pi_run("sudo -n systemctl stop fw_bench 2>/dev/null; true")
            pi.close()
        except Exception:
            pass
        if tunnel is not None:
            tunnel.terminate()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
