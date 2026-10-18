#!/usr/bin/env python3
"""Firmware updates pulled from a manifest (2026-10-18, internal/TASK_fw_update.md
section 9.2): the bench.

The claim: the device reads the release list for its device type, offers
what its channel allows and nothing else, installs only an image whose size,
sha256 and product match the list, records the job across the restart, and a
new image that crashes three times before it settled is rolled back to the
previous slot.

Rig (TESTING.md): the DUT on the bench AP reached over HTTP through an ssh
tunnel (--dut localhost:18081, test.ps1 opens it; the WebSocket console
/ws/cli rides the same port: the `cli <-> ws_cli` bridge of the canonical
bench settings), rpi001 serving the release list and the images over HTTPS
with the bench CA (actors/fw_server.py as a transient unit on --port 8446;
--plain = http when the server certificate does not cover --pi-ip). No PSU,
no PCAN, no serial console. Images: A = the current build (tools/fw_bin.py:
the DUT must run it, the bench refuses otherwise), B / B2 / BETA / W = copies
of A with a patched version or project name (lib/fw_image.py).

Legs:
  f0  preflight + setup: GET /api/fw_update 200, the registry headroom, the
      DUT runs the local build; the bench CA as cert set `fwbench`, the
      fw_update settings staged (base_url, cert_set, channel alfa) and
      submitted (a restart); the images patched and uploaded, the server up
  f1  nothing newer: available false, up_to_date; the second check hits the
      ETag (304 at the server, etag_hit on the device); no flash write per check
  f2  B offered: the target, the summary and the date, the notes proxied
  f3  the install: 202, the job through downloading / verifying / rebooting,
      the device back on B, last_result pending_verify with the job id, the
      ota_apply/ota restart record; `restart_tracker --settle` on the console
      = the milestone: verified, the slot valid; a second install = already
  f4  a wrong sha256 in the list: failed sha256_mismatch, nothing activated
  f5  a foreign image (project espnetlink-fw_bench): failed wrong_product;
      the same image uploaded by hand = 400 not a WiCAN PRO image; an upload
      with ?sha256=<wrong> = 400 sha256 mismatch
  f6  the layout verdicts: a wipe = 409 needs_confirm_wipe with the labels,
      confirm_wipe runs the backup step (job.backup.sd with a card) and is
      aborted before any activation; serial = 409; unknown = 409
  f7  channel visibility: on alfa the beta release is the target, on stable
      nothing is (two restarts)
  f8  stepping and yanked: a release that requires a stone = the stone with
      step:true; a yanked newest is skipped; a yanked stone = no_path
  f9  (--cellular) on a mobile uplink the install is refused and nothing in
      the request overrides it (downloads go over WiFi only, 2026-10-19)
  f10 the rollback: B2 installed, then crashed over the console up to three
      times; the device comes back on B with last_result rolled_back (by the
      app after three crashes, or by a rollback-enabled bootloader after one)
  f11 the legacy push: A multipart on /upload/ota.bin, the device back on A
  f12 mutual exclusion: a slowed install running, an upload answers 409, the
      abort leaves /api/ota/status idle
  close: the settings as found, the cert set deleted, the server stopped, no
      unexpected reset beyond the three asked for, no E line of the own tags,
      the fw_update task's stack headroom, the heap floor

  python tools/testbench/system/fw_update_bench.py [--dut localhost:18081]
      [--dut-ip 10.42.1.194] [--pi rpi001] [--pi-ip 10.42.1.1] [--port 8446]
      [--plain] [--cellular] [--only f1,f2] [--logdir DIR]

Prints `FW UPDATE PASS` (or `FW UPDATE PARTIAL PASS (legs: ...)`).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import fw_image  # noqa: E402
import pcbench  # noqa: E402
from ssh_channel import SshChannel  # noqa: E402
from wsmin import OP_TEXT, WS  # noqa: E402

OWN_TAGS = ("fw_update", "ota_manager", "http_client_manager", "api_http")
LEGS = ["f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f10", "f11", "f12"]
OPT_IN = ["f9"]
VER_B, VER_B2, VER_BETA, VER_W = "6.1.0-alfa.90", "6.1.0-alfa.91", "6.1.0-beta.90", "6.1.0-alfa.95"
MD5_FAKE = "0123456789abcdef0123456789abcdef"
PI_ROOT = "/home/meatpi/wican-http/fw"
SETTINGS_KEYS = ("enabled", "channel", "check_interval_h", "base_url", "cert_set")


def raw(base, path, method="GET", data=None, ctype="application/octet-stream", timeout=120):
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def multipart(field, filename, data):
    boundary = "----wicanbench" + uuid.uuid4().hex
    body = (("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n") % (boundary, field, filename)).encode()
    body += data + ("\r\n--%s--\r\n" % boundary).encode()
    return body, "multipart/form-data; boundary=" + boundary


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="", help="host:port of the DUT's API (a tunnel)")
    ap.add_argument("--dut-ip", default="10.42.1.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--pi-ip", default="10.42.1.1", help="the Pi as the DUT reaches it")
    ap.add_argument("--port", type=int, default=8446)
    ap.add_argument("--plain", action="store_true", help="serve over http (no bench CA)")
    ap.add_argument("--tls-dir", default="/home/meatpi/wican-tls-fwbench",
                    help="the server certificate for --pi-ip signed by the bench CA (falls back to ~/wican-tls)")
    ap.add_argument("--cellular", action="store_true", help="run f9 (the DUT on a mobile uplink)")
    ap.add_argument("--only", default="")
    ap.add_argument("--logdir", default="")
    ap.add_argument("--image", default="", help="image A (default: tools/fw_bin.py)")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS + (OPT_IN if a.cellular else []) if not only or l in only or l == "f0"]
    logdir = a.logdir or os.path.join(HERE, "..", "..", "..", "test-reports", "logs",
                                      time.strftime("fw_update_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("FW UPDATE")
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
        rc, out = pi.run(cmd, timeout=timeout)
        return rc, out

    def scp(local, remote):
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", local, "%s:%s" % (a.pi, remote)], check=True, timeout=600)

    # ---- the server and the release list --------------------------------------------------
    def server_ctl(**knobs):
        q = "&".join("%s=%s" % (k, v) for k, v in knobs.items())
        pi_run("curl -sk -m 5 -X POST '%s://127.0.0.1:%d/__ctl?%s'" % (scheme, a.port, q))

    def server_dump():
        rc, out = pi_run("curl -sk -m 5 '%s://127.0.0.1:%d/__dump'" % (scheme, a.port))
        try:
            return json.loads(out)
        except ValueError:
            return {"requests": []}

    def server_reset():
        pi_run("curl -sk -m 5 -X POST '%s://127.0.0.1:%d/__reset'" % (scheme, a.port))

    images = {}   # name -> {"path", "size", "sha256", "version", "project"}

    def release(name, version, channel="alfa", **extra):
        img = images[name]
        rel = {"version": version, "fw_version": img["version"], "tag": "v%sp" % version, "channel": channel,
               "date": "2026-10-18", "summary": "Bench release %s (%s)." % (version, name),
               "notes": "wican_pro/firmware/notes/%s.md" % version,
               "image": {"url": "%s://%s:%d/dl/%s" % (scheme, a.pi_ip, a.port, img["file"]), "size": img["size"],
                         "sha256": extra.pop("sha256", img["sha256"]), "project_name": img["project"]},
               "layout": {"md5": extra.pop("layout_md5", images["A"]["layout_md5"]),
                          "table": "wican_pro/firmware/layouts/x.csv"}, "yanked": extra.pop("yanked", False)}
        rel.update(extra)
        return rel

    def publish(releases):
        """Write the manifest and the notes for every release, upload them."""
        doc = {"schema": 1, "device_type": "wican_pro", "model": "WiCAN PRO", "project_name": "wican-fw_obd_pro",
               "source_repo": "meatpiHQ/wican-fw", "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "channels": {"stable": {"latest": None}, "beta": {"latest": None}, "alfa": {"latest": None}},
               "releases": releases}
        tmp = tempfile.mkdtemp(prefix="fwbench_")
        mpath = os.path.join(tmp, "manifest.json")
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        for r in releases:
            npath = os.path.join(tmp, "%s.md" % r["version"])
            with open(npath, "w", encoding="utf-8") as f:
                f.write("# WiCAN PRO %s\n\n## Before you update\nUnits on this layout update in place.\n\n"
                        "## New features\n- The bench release %s.\n- **Bold** and `code` render.\n" % (r["version"], r["version"]))
            scp(npath, "%s/wican_pro/firmware/notes/%s.md" % (PI_ROOT, r["version"]))
        scp(mpath, "%s/wican_pro/firmware/manifest.json" % PI_ROOT)
        save("manifest_%s.json" % time.strftime("%H%M%S"), json.dumps(doc, indent=1))
        server_reset()

    # ---- the device -----------------------------------------------------------------------
    def fwu():
        return dut.get("/api/fw_update")

    def check(wait=40):
        """POST check, then wait for the check's result."""
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

    def install(body, expect=202):
        code, r = dut.api("/api/fw_update/install", "POST", body, timeout=30)
        return code, r

    def wait_job(pred, limit, tag):
        # A status poll can time out for seconds at a stretch while the device
        # streams an image through TLS and writes the flash: its one httpd
        # worker is busy (2026-10-19, the f4 blackout). So the per-poll timeout
        # is short, a timeout never counts as "the device is gone" on its own,
        # and /api/ota/status (a cheaper route) is the second signal that a job
        # ended. Only many timeouts in a row with no answer anywhere = a drop.
        end = time.time() + limit
        last = None
        gone = 0
        while time.time() < end:
            code, st = dut.api("/api/fw_update", timeout=4)
            if code == 200 and isinstance(st, dict):
                last = st
                gone = 0
                if pred(st):
                    return st
                time.sleep(1)
                continue
            # the fw_update route did not answer: ask the lighter ota route
            ocode, o = dut.api("/api/ota/status", timeout=4)
            if ocode == 200:
                gone = 0
            elif code == 0 and ocode == 0:
                gone += 1
                if gone >= 8:
                    return None   # neither route answers: the restart
            time.sleep(1)
        raise pcbench.Bench("%s: %s (last ota %s)"
                            % (tag, json.dumps((last or {}).get("job"))[:260],
                               json.dumps(o if 'o' in dir() else None)[:120]))

    def wait_refused(ota_error, job_code, limit, tag):
        # A refused image, read off two routes because they resolve
        # differently (2026-10-19): a wrong DIGEST is caught at the end, so
        # /api/ota/status ends `failed` with the text; a wrong PRODUCT is
        # caught at the head and the pipe aborts the session to `idle` with
        # no text, the classification living only in the fw_update job. And
        # the heavy /api/fw_update snapshot, polled per second, can starve
        # the download, so the light ota route leads. Returns (ota, job,
        # saw): `saw` is every distinct job state seen, for the report.
        # Each leg's error text is its own, so a lingering status from the
        # previous leg is ignored.
        end = time.time() + limit
        ota = {}
        job = {}
        saw = []
        gone = 0
        resolved = False
        st = ""
        while time.time() < end:
            code, o = dut.api("/api/ota/status", timeout=4)
            if code == 200 and isinstance(o, dict):
                ota = o
                gone = 0
                st = o.get("state")
                # the digest case (ota_error given): failed with this leg's
                # text. the product case (no ota_error): the session aborts to
                # idle with a short received and the job carries the reason.
                if ota_error:
                    if st == "failed" and ota_error in o.get("error", ""):
                        resolved = True
                elif st == "idle" and (o.get("received") or 0) < 1000000:
                    resolved = True
            elif code == 0:
                gone += 1
                if gone >= 8:
                    break
            # read the job too (cheap once the download is not being starved)
            j = (dut.get("/api/fw_update") or {}).get("job") or {}
            s = j.get("state")
            if s and (not saw or saw[-1] != s):
                saw.append(s)
            if j.get("state") == "failed" and j.get("error") == job_code:
                job = j
                resolved = True
            if resolved and job:
                return ota, job, saw
            if resolved and st in ("failed", "idle"):
                # the ota resolved; the engine runs its due check the moment the
                # install returns (the job cycles checking -> ... and the keep
                # logic restores the failed job after it). Wait past a check
                # (its dongle manifest fetch can take tens of seconds).
                for _ in range(75):
                    j = (dut.get("/api/fw_update") or {}).get("job") or {}
                    s = j.get("state")
                    if s and (not saw or saw[-1] != s):
                        saw.append(s)
                    if j.get("state") == "failed" and j.get("error") == job_code:
                        return ota, j, saw
                    time.sleep(1)
                return ota, job, saw
            time.sleep(1)
        raise pcbench.Bench("%s: ota %s job states %s" % (tag, json.dumps(ota)[:180], saw))

    def wait_boot(b0, limit=180):
        t0 = time.time()
        time.sleep(3)
        while time.time() - t0 < limit:
            b = dut.boot_count()
            if b is not None and b != b0:
                dut.restarts += 1
                time.sleep(4)
                return
            time.sleep(1)
        raise pcbench.Bench("the DUT did not come back within %d s" % limit + pcbench.GONE_HINT)

    def info():
        return dut.get("/api/info")

    def cli(cmd, marker="OK", timeout=20):
        """One command over /ws/cli through the tunnel (the bridge of the bench settings)."""
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

    def cli_available():
        try:
            return "firmware" in cli("version", "OK", 8) or "OK" in cli("version", "OK", 8)
        except Exception:
            return False

    def flash_writes():
        st = dut.get("/api/status")
        return (st.get("health", {}).get("flash") or {}).get("writes")

    found = {}
    exit_code = 1
    st0 = None
    server_up = False
    cert_uploaded = False
    try:
        # ---- f0: preflight + setup -----------------------------------------------------------
        print("== f0 preflight + setup", flush=True)
        dut.as_found()
        st0 = dut.get("/api/status")
        inf = info()
        code, st = dut.api("/api/fw_update")
        run.check("f0_fw_update_route", code == 200 and isinstance(st, dict) and "targets" in st, "%s %s" % (code, str(st)[:120]))
        caps = (st0.get("health", {}).get("caps") or {})
        low = [k for k, v in caps.items() if isinstance(v, dict) and v.get("cap", 0) - v.get("used", 0) < 2]
        run.check("f0_registry_headroom_2", not low, json.dumps({k: caps[k] for k in low}))
        for k in ("settings", "cmdline", "http_routes"):
            if k in caps:
                run.metric("caps_%s" % k, "%s/%s" % (caps[k].get("used"), caps[k].get("cap")))
        a_path = a.image or subprocess.check_output([sys.executable, os.path.join(HERE, "..", "..", "fw_bin.py")],
                                                    text=True).strip()
        with open(a_path, "rb") as f:
            a_data = f.read()
        a_info = fw_image.info(a_data)
        run.check("f0_dut_runs_the_local_build", inf.get("fw_version") == a_info["version"],
                  "DUT %s, build %s (%s)" % (inf.get("fw_version"), a_info["version"], a_path))
        if inf.get("fw_version") != a_info["version"]:
            raise pcbench.Bench("flash the DUT with this build first (python tools/fw_bin.py)")
        run.check("f0_info_carries_build_and_layout", bool(inf.get("fw_build")) and len(inf.get("layout_md5", "")) == 32,
                  json.dumps({k: inf.get(k) for k in ("fw_build", "layout_md5", "partition")}))
        flashed_md5 = inf.get("layout_md5", "")
        found["settings"] = dut.settings("fw_update")
        found["partition"] = inf.get("partition")
        run.metric("image_a_size", a_info["size"], "B")
        # the images
        tmp = tempfile.mkdtemp(prefix="fwbench_img_")
        variants = {"A": (None, None), "B": (VER_B, None), "B2": (VER_B2, None), "BETA": (VER_BETA, None),
                    "W": (VER_W, "espnetlink-fw_bench")}
        for name, (ver, proj) in variants.items():
            data = a_data if name == "A" else fw_image.patch(a_data, version=ver, project=proj)
            if not fw_image.verify(data):
                raise pcbench.Bench("patched image %s does not verify" % name)
            fn = "wican-fw_obd_pro_bench_%s.bin" % name
            p = os.path.join(tmp, fn)
            with open(p, "wb") as f:
                f.write(data)
            i = fw_image.info(data)
            images[name] = {"path": p, "file": fn, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                            "version": i["version"], "project": i["project_name"], "layout_md5": flashed_md5}
        run.check("f0_images_patched", images["B"]["version"] == VER_B and images["W"]["project"] == "espnetlink-fw_bench")
        # the server
        pi_run("mkdir -p %s/wican_pro/firmware/notes" % PI_ROOT)
        tls_dir = a.tls_dir
        if not a.plain:
            rc, _ = pi_run("test -d %s" % a.tls_dir)
            if rc != 0:
                tls_dir = "/home/meatpi/wican-tls"
                print("  (no %s on the Pi: using %s; its certificate must name %s)" % (a.tls_dir, tls_dir, a.pi_ip))
        scp(os.path.join(HERE, "..", "actors", "fw_server.py"), "/tmp/fw_server.py")
        for name in images:
            scp(images[name]["path"], "%s/%s" % (PI_ROOT, images[name]["file"]))
        pi_run("sudo -n systemctl stop fw_bench 2>/dev/null; sudo -n systemd-run --unit=fw_bench --collect -p User=meatpi "
               "-p WorkingDirectory=/home/meatpi python3 -u /tmp/fw_server.py --root %s --port %d %s --redirect-images"
               % (PI_ROOT, a.port, "--plain" if a.plain else "--tls-dir %s" % tls_dir))
        time.sleep(2)
        rc, out = pi_run("curl -sk -m 5 '%s://127.0.0.1:%d/__dump' | head -c 80" % (scheme, a.port))
        server_up = rc == 0 and "requests" in out
        run.check("f0_server_up", server_up, out[:160])
        if not server_up:
            raise pcbench.Bench("the release server did not come up on the Pi (journalctl -u fw_bench)")
        # the bench CA as a cert set, the settings, one restart
        if not a.plain:
            rc, ca = pi_run("cat /home/meatpi/wican-tls/ca.pem")
            code, r = raw(base, "/api/certs/upload?set=fwbench&type=ca", "POST", ca.encode(), "application/x-pem-file", 30)
            cert_uploaded = code == 200
            run.check("f0_bench_ca_uploaded", cert_uploaded, "%s %s" % (code, r[:120]))
        dut.restart({"fw_update": {"enabled": True, "channel": "alfa", "check_interval_h": 24,
                                   "base_url": base_url, "cert_set": "" if a.plain else "fwbench"}})
        st = fwu()
        run.check("f0_settings_applied", st.get("channel") == "alfa" and st.get("enabled") is True, json.dumps({k: st.get(k) for k in ("channel", "enabled")}))
        publish([release("A", a_info["version"], "stable")])

        # ---- f1: nothing newer, the ETag ------------------------------------------------------
        if "f1" in legs:
            print("== f1 up to date, ETag", flush=True)
            w0 = flash_writes()
            st = check()
            self = st["targets"]["self"]
            run.check("f1_check_ok", (st.get("last_check") or {}).get("ok") is True, json.dumps(st.get("last_check")))
            run.check("f1_not_available", self.get("available") is False and self.get("reason") == "up_to_date", json.dumps({k: self.get(k) for k in ("available", "reason", "release_known")}))
            run.check("f1_installed_release_known", self.get("release_known") is True)
            st = check()
            run.check("f1_second_check_hits_the_etag", (st.get("last_check") or {}).get("etag_hit") is True, json.dumps(st.get("last_check")))
            d = server_dump()
            reqs = [r for r in d.get("requests", []) if r["path"].endswith("manifest.json")]
            run.check("f1_server_saw_if_none_match_and_304", any(r["inm"] for r in reqs) and any(r["status"] == 304 for r in reqs), json.dumps(reqs)[:300])
            w1 = flash_writes()
            run.check("f1_no_flash_write_per_check", w0 is not None and w1 == w0, "writes %s -> %s" % (w0, w1))

        # ---- f2: an update offered -------------------------------------------------------------
        if "f2" in legs:
            print("== f2 available", flush=True)
            publish([release("B", VER_B), release("A", a_info["version"], "stable")])
            st = check()
            self = st["targets"]["self"]
            tg = self.get("target") or {}
            run.check("f2_available", self.get("available") is True and self.get("installable") is True, json.dumps({k: self.get(k) for k in ("available", "installable", "reason")}))
            run.check("f2_target_is_b", tg.get("version") == VER_B and tg.get("verdict") == "none", json.dumps(tg))
            run.check("f2_latest_summary_and_date", (self.get("latest") or {}).get("date") == "2026-10-18" and "Bench release" in (self.get("latest") or {}).get("summary", ""), json.dumps(self.get("latest")))
            code, txt = raw(base, "/api/fw_update/notes?target=self&version=%s" % VER_B, "GET", None, "text/plain", 30)
            run.check("f2_notes_proxied", code == 200 and txt.startswith("# WiCAN PRO %s" % VER_B) and "**Bold**" in txt, "%s %s" % (code, txt[:80]))

        # ---- f3: the install -------------------------------------------------------------------
        if "f3" in legs:
            print("== f3 install", flush=True)
            publish([release("B", VER_B), release("A", a_info["version"], "stable")])
            check()
            b0 = dut.boot_count()
            mem0 = dut.get("/api/status").get("memory", {}).get("internal", {}).get("min_free")
            t_start = time.time()
            code, r = install({"target": "self"})
            run.check("f3_install_accepted_202", code == 202 and isinstance(r, dict) and r.get("job_id"), "%s %s" % (code, str(r)[:200]))
            job_id = (r or {}).get("job_id") if isinstance(r, dict) else None
            seen = []
            end = time.time() + 600
            while time.time() < end:
                code, st = dut.api("/api/fw_update", timeout=6)
                if code == 0:
                    break
                if code == 200 and isinstance(st, dict) and st.get("job"):
                    s = st["job"]["state"]
                    if not seen or seen[-1] != s:
                        seen.append(s)
                        print("   job %s %s/%s" % (s, st["job"].get("received"), st["job"].get("total")), flush=True)
                    if s in ("failed", "aborted"):
                        run.check("f3_job_did_not_fail", False, json.dumps(st["job"]))
                        break
                    if s == "rebooting":
                        break
                time.sleep(1)
            run.metric("f3_download_s", round(time.time() - t_start, 1), "s")
            run.check("f3_states_in_order", [s for s in ("checking", "downloading", "rebooting") if s in seen] == ["checking", "downloading", "rebooting"][:len([s for s in ("checking", "downloading", "rebooting") if s in seen])] and "rebooting" in seen, json.dumps(seen))
            wait_boot(b0)
            inf = info()
            run.check("f3_running_b", inf.get("fw_version") == VER_B, inf.get("fw_version"))
            st = fwu()
            lr = st.get("last_result") or {}
            run.check("f3_last_result_pending_verify", lr.get("id") == job_id and lr.get("state") == "pending_verify" and lr.get("to") == VER_B and lr.get("from") == a_info["version"], json.dumps(lr))
            run.metric("f3_ota_state_after_boot", (st.get("rollback") or {}).get("ota_state"))
            run.metric("f3_bootloader_rollback", (st.get("rollback") or {}).get("bootloader_rollback"))
            code, h = dut.api("/api/restart/history", timeout=15)
            rec = (h.get("records") or [{}])[0] if isinstance(h, dict) else {}
            run.check("f3_restart_record_ota_apply_ota", rec.get("planned") and rec.get("planned_reason") == "ota_apply" and rec.get("source") == "ota", json.dumps(rec)[:300])
            if cli_available():
                out = cli("restart_tracker --settle", "OK", 15)
                st = wait_job(lambda s: (s.get("last_result") or {}).get("state") == "verified", 30, "no verified after --settle")
                run.check("f3_verified_at_the_milestone", st is not None and (st.get("last_result") or {}).get("state") == "verified", json.dumps((st or {}).get("last_result")))
                run.check("f3_slot_valid_after_verify", (st or {}).get("rollback", {}).get("ota_state") == "valid" and not (st or {}).get("rollback", {}).get("pending_verify"), json.dumps((st or {}).get("rollback")))
            else:
                run.metric("f3_settle_skipped", "no /ws/cli")
            w = flash_writes()
            run.metric("f3_flash_writes_after_boot", w)
            # the first boot of a new image writes: the job record outcome, the restart
            # history, config_backup refreshing a copy whose fw_version moved (11 measured
            # 2026-10-19); the bound catches the rewrote-everything class (37 files)
            run.check("f3_flash_writes_bounded", w is not None and w <= 32, str(w))
            # the view is rebuilt by the first check after the restart (the device
            # schedules one 20 s after the network is up following an update; the bench
            # asks now instead of waiting)
            check()
            code, r = install({"target": "self", "version": VER_B})
            run.check("f3_second_install_is_already", code == 200 and isinstance(r, dict) and r.get("already") is True, "%s %s" % (code, str(r)[:120]))
            mem1 = dut.get("/api/status").get("memory", {}).get("internal", {}).get("min_free")
            run.metric("f3_internal_min_free_before", mem0)
            run.metric("f3_internal_min_free_after_boot", mem1)

        # ---- f4: a wrong sha256 -------------------------------------------------------------
        if "f4" in legs:
            print("== f4 sha256 mismatch", flush=True)
            publish([release("B2", VER_B2, sha256="00" * 32), release("B", VER_B), release("A", a_info["version"], "stable")])
            check()
            b0 = dut.boot_count()
            code, r = install({"target": "self"})
            run.check("f4_install_accepted", code == 202, "%s %s" % (code, str(r)[:120]))
            o, job, saw = wait_refused("sha256 mismatch", "sha256_mismatch", 600, "the wrong-sha image never resolved")
            run.check("f4_ota_sha256_mismatch", o.get("state") == "failed" and o.get("error") == "sha256 mismatch", json.dumps(o)[:260])
            run.check("f4_job_failed_sha256_mismatch", job.get("state") == "failed" and job.get("error") == "sha256_mismatch", "job %s, states seen %s" % (json.dumps(job)[:200], saw))
            run.check("f4_no_restart", dut.boot_count() == b0)
            run.check("f4_still_running_b_or_a", info().get("fw_version") in (VER_B, a_info["version"]))

        # ---- f5: a foreign image ------------------------------------------------------------
        if "f5" in legs:
            print("== f5 wrong product", flush=True)
            publish([release("W", VER_W), release("B", VER_B), release("A", a_info["version"], "stable")])
            check()
            code, r = install({"target": "self"})
            run.check("f5_install_accepted", code == 202, "%s %s" % (code, str(r)[:120]))
            # the head check refuses a foreign image at its first 288 bytes:
            # the pipe aborts the session to idle (short received), and the
            # classification lives in the fw_update job (wrong_product)
            o, job, saw = wait_refused("", "wrong_product", 600, "the foreign image never resolved")
            run.check("f5_session_not_activated", o.get("state") in ("idle", "failed") and (o.get("received") or 0) < 1000000, json.dumps(o)[:260])
            run.check("f5_job_wrong_product", job.get("state") == "failed" and job.get("error") == "wrong_product" and "espnetlink-fw_bench" in job.get("detail", ""), "job %s, states seen %s" % (json.dumps(job)[:200], saw))
            with open(images["W"]["path"], "rb") as f:
                wdata = f.read()
            code, txt = raw(base, "/api/ota/upload", "POST", wdata, "application/octet-stream", 300)
            run.check("f5_upload_refuses_foreign_image_400", code == 400 and "not a WiCAN PRO image" in txt, "%s %s" % (code, txt[:160]))
            code, txt = raw(base, "/api/ota/upload?sha256=%s" % ("ab" * 32), "POST", a_data, "application/octet-stream", 300)
            run.check("f5_upload_with_wrong_sha256_400", code == 400 and "sha256 mismatch" in txt, "%s %s" % (code, txt[:160]))
            code, o = dut.api("/api/ota/status")
            run.check("f5_ota_idle_after", code == 200 and o.get("state") in ("idle", "failed"), json.dumps(o))

        # ---- f6: the layout verdicts ---------------------------------------------------------
        if "f6" in legs:
            print("== f6 layout verdicts", flush=True)
            card = bool(dut.get("/api/status").get("bits", {}).get("sdcard_mounted"))
            publish([release("B2", VER_B2, layout_md5=MD5_FAKE, layout_from={flashed_md5: {"migration": "wipe", "wipes": ["settings"]}}),
                     release("B", VER_B), release("A", a_info["version"], "stable")])
            st = check()
            tg = st["targets"]["self"].get("target") or {}
            run.check("f6_verdict_wipe_settings", tg.get("verdict") == "wipe" and tg.get("wipes") == ["settings"], json.dumps(tg))
            code, r = install({"target": "self"})
            run.check("f6_409_needs_confirm_wipe", code == 409 and isinstance(r, dict) and r.get("code") == "needs_confirm_wipe" and r.get("wipes") == ["settings"], "%s %s" % (code, str(r)[:200]))
            server_ctl(delay_ms=300)
            code, r = install({"target": "self", "confirm_wipe": True})
            run.check("f6_confirm_wipe_accepted", code == 202, "%s %s" % (code, str(r)[:120]))
            st = wait_job(lambda s: s.get("job") and (s["job"]["state"] in ("downloading", "verifying") or not s["job"]["active"]), 120, "no download")
            job = (st or {}).get("job") or {}
            run.check("f6_backup_step_reported", job.get("state") == "downloading" and (job.get("backup") or {}).get("sd") == card, "card %s, backup %s, state %s" % (card, json.dumps(job.get("backup")), job.get("state")))
            code, r = dut.api("/api/fw_update/abort", "POST", {})
            run.check("f6_abort_200", code == 200 and isinstance(r, dict) and r.get("aborted") is True, "%s %s" % (code, str(r)[:120]))
            st = wait_job(lambda s: s.get("job") and not s["job"]["active"], 60, "the job did not stop")
            run.check("f6_job_aborted", ((st or {}).get("job") or {}).get("state") == "aborted", json.dumps((st or {}).get("job"))[:200])
            server_ctl(delay_ms=0)
            publish([release("B2", VER_B2, layout_md5=MD5_FAKE, layout_from={flashed_md5: {"migration": "serial"}}),
                     release("B", VER_B), release("A", a_info["version"], "stable")])
            check()
            code, r = install({"target": "self", "version": VER_B2})
            run.check("f6_serial_409", code == 409 and isinstance(r, dict) and r.get("code") == "layout_serial", "%s %s" % (code, str(r)[:160]))
            publish([release("B2", VER_B2, layout_md5=MD5_FAKE), release("B", VER_B), release("A", a_info["version"], "stable")])
            st = check()
            run.check("f6_unknown_layout_not_installable", st["targets"]["self"].get("installable") is False and st["targets"]["self"].get("reason") == "layout_unknown", json.dumps(st["targets"]["self"].get("target")))
            code, r = install({"target": "self", "version": VER_B2})
            run.check("f6_unknown_409", code == 409 and isinstance(r, dict) and r.get("code") == "layout_unknown", "%s %s" % (code, str(r)[:160]))

        # ---- f7: channel visibility ------------------------------------------------------------
        if "f7" in legs:
            print("== f7 channels", flush=True)
            publish([release("BETA", VER_BETA, "beta"), release("B", VER_B, "stable"), release("A", a_info["version"], "stable")])
            st = check()
            tg = st["targets"]["self"].get("target") or {}
            run.check("f7_alfa_sees_the_beta", tg.get("version") == VER_BETA, json.dumps(tg))
            dut.restart({"fw_update": {"channel": "stable"}})
            st = check()
            self = st["targets"]["self"]
            run.check("f7_stable_offers_nothing_newer", self.get("available") is False and self.get("reason") == "up_to_date" and (self.get("latest") or {}).get("version") == VER_B, json.dumps({k: self.get(k) for k in ("available", "reason", "latest")}))
            dut.restart({"fw_update": {"channel": "alfa"}})
            st = check()
            run.check("f7_alfa_again", (st["targets"]["self"].get("target") or {}).get("version") == VER_BETA)

        # ---- f8: stepping and yanked -----------------------------------------------------------
        if "f8" in legs:
            print("== f8 stepping, yanked", flush=True)
            publish([release("BETA", VER_BETA, "beta", requires={"min_version": VER_B2}), release("B2", VER_B2),
                     release("B", VER_B, "stable"), release("A", a_info["version"], "stable")])
            st = check()
            tg = st["targets"]["self"].get("target") or {}
            run.check("f8_stepping_stone_first", tg.get("version") == VER_B2 and tg.get("step") is True, json.dumps(tg))
            publish([release("BETA", VER_BETA, "beta", yanked=True, yank_reason="bench: withdrawn"), release("B2", VER_B2),
                     release("B", VER_B, "stable"), release("A", a_info["version"], "stable")])
            st = check()
            tg = st["targets"]["self"].get("target") or {}
            run.check("f8_yanked_latest_skipped", tg.get("version") == VER_B2 and not tg.get("step"), json.dumps(tg))
            publish([release("BETA", VER_BETA, "beta", requires={"min_version": VER_B2}),
                     release("B2", VER_B2, yanked=True, yank_reason="bench: withdrawn"),
                     release("B", VER_B, "stable"), release("A", a_info["version"], "stable")])
            st = check()
            self = st["targets"]["self"]
            run.check("f8_yanked_stone_no_path", self.get("available") is False and self.get("reason") == "no_path" and (self.get("latest") or {}).get("version") == VER_BETA, json.dumps({k: self.get(k) for k in ("available", "reason", "latest")}))
            code, r = install({"target": "self", "version": VER_B2})
            run.check("f8_install_of_a_yanked_release_404", code == 404 and isinstance(r, dict) and r.get("code") == "yanked", "%s %s" % (code, str(r)[:120]))
            code, r = install({"target": "self", "version": "9.9.9"})
            run.check("f8_unknown_version_404", code == 404 and isinstance(r, dict) and r.get("code") == "unknown_version", "%s %s" % (code, str(r)[:120]))

        # ---- f9: cellular (opt-in) ---------------------------------------------------------------
        if "f9" in legs:
            print("== f9 cellular", flush=True)
            publish([release("B2", VER_B2), release("B", VER_B), release("A", a_info["version"], "stable")])
            st = check()
            cel = st.get("cellular") or {}
            run.check("f9_uplink_is_cellular", cel.get("is_cellular") is True, json.dumps(cel))
            code, r = install({"target": "self"})
            run.check("f9_install_blocked_409", code == 409 and isinstance(r, dict) and r.get("code") == "cellular_blocked", "%s %s" % (code, str(r)[:120]))
            # 2026-10-19 (Ali): no override: the image waits for WiFi, whatever the body says
            code, r = install({"target": "self", "allow_cellular": True})
            run.check("f9_no_override_over_lte", code == 409 and isinstance(r, dict) and r.get("code") == "cellular_blocked", "%s %s" % (code, str(r)[:120]))
            wait_job(lambda s: s.get("job") and (s["job"]["state"] == "downloading" or not s["job"]["active"]), 120, "no download")
            dut.api("/api/fw_update/abort", "POST", {})
            wait_job(lambda s: s.get("job") and not s["job"]["active"], 60, "the job did not stop")

        # ---- f10: the rollback -------------------------------------------------------------------
        if "f10" in legs:
            print("== f10 rollback", flush=True)
            if not cli_available():
                run.check("f10_console_over_ws", False, "no /ws/cli: the bench settings need the cli <-> ws_cli bridge")
            else:
                publish([release("B2", VER_B2), release("B", VER_B), release("A", a_info["version"], "stable")])
                check()
                before = info().get("fw_version")
                b0 = dut.boot_count()
                code, r = install({"target": "self"})
                run.check("f10_install_b2_accepted", code == 202, "%s %s" % (code, str(r)[:120]))
                job_id = (r or {}).get("job_id") if isinstance(r, dict) else None
                wait_job(lambda s: s.get("job") and s["job"]["state"] in ("rebooting", "failed", "aborted"), 600, "the install did not reach rebooting")
                wait_boot(b0)
                inf = info()
                run.check("f10_running_b2", inf.get("fw_version") == VER_B2, inf.get("fw_version"))
                st = fwu()
                run.check("f10_pending_verify", (st.get("last_result") or {}).get("state") == "pending_verify", json.dumps(st.get("last_result")))
                crashes = 0
                by = None
                for i in range(3):
                    b = dut.boot_count()
                    try:
                        cli("restart_tracker --panic=fault", None, 3)
                    except Exception:
                        pass
                    crashes += 1
                    wait_boot(b, 120)
                    inf = info()
                    print("   crash %d: running %s" % (crashes, inf.get("fw_version")), flush=True)
                    if inf.get("fw_version") != VER_B2:
                        by = "bootloader" if crashes < 3 else "app"
                        break
                st = fwu()
                lr = st.get("last_result") or {}
                run.metric("f10_crashes_until_rollback", crashes)
                run.check("f10_back_on_the_previous_image", inf.get("fw_version") == before, "%s (before %s)" % (inf.get("fw_version"), before))
                run.check("f10_last_result_rolled_back", lr.get("id") == job_id and lr.get("state") == "rolled_back" and lr.get("by") in ("app", "bootloader"), json.dumps(lr))
                run.metric("f10_rolled_back_by", lr.get("by"))
                code, h = dut.api("/api/restart/history", timeout=15)
                rec = (h.get("records") or [{}])[0] if isinstance(h, dict) else {}
                if lr.get("by") == "app":
                    run.check("f10_restart_record_ota_rollback_boot", rec.get("planned") and rec.get("planned_reason") == "ota_rollback" and rec.get("source") == "boot", json.dumps(rec)[:300])
                    brake = h.get("brake") or {}
                    run.check("f10_brake_streak_reset", brake.get("streak", 0) == 0, json.dumps(brake))
                else:
                    run.metric("f10_note", "a rollback-enabled bootloader fell back after one crash")
                # the three crashes were asked for: a fresh baseline for the closing check
                time.sleep(2)
                st0 = dut.get("/api/status")
                dut.unexpected = st0.get("unexpected_resets")
                dut.restarts = 0
                run.check("f10_fw_update_up_after_rollback", dut.api("/api/fw_update")[0] == 200)

        # ---- f11: the legacy push ----------------------------------------------------------------
        if "f11" in legs:
            print("== f11 legacy push", flush=True)
            b0 = dut.boot_count()
            body, ctype = multipart("ota_file", images["A"]["file"], a_data)
            code, txt = raw(base, "/upload/ota.bin", "POST", body, ctype, 600)
            run.check("f11_legacy_push_200", code == 200 and '"ok":true' in txt.replace(" ", ""), "%s %s" % (code, txt[:160]))
            wait_boot(b0)
            inf = info()
            run.check("f11_running_a_again", inf.get("fw_version") == a_info["version"], inf.get("fw_version"))
            st = fwu()
            run.metric("f11_last_result_state", (st.get("last_result") or {}).get("state"))
            if cli_available():
                cli("restart_tracker --settle", "OK", 15)
                cli("fwupdate confirm", "OK", 15)

        # ---- f12: mutual exclusion ---------------------------------------------------------------
        if "f12" in legs:
            print("== f12 mutual exclusion", flush=True)
            publish([release("B2", VER_B2), release("B", VER_B), release("A", a_info["version"], "stable")])
            check()
            server_ctl(delay_ms=400)
            code, r = install({"target": "self"})
            run.check("f12_install_accepted", code == 202, "%s %s" % (code, str(r)[:120]))
            wait_job(lambda s: s.get("job") and (s["job"]["state"] == "downloading" or not s["job"]["active"]), 120, "no download")
            code, txt = raw(base, "/api/ota/upload", "POST", b"\xe9" + b"\0" * 4095, "application/octet-stream", 30)
            run.check("f12_upload_refused_409_while_installing", code == 409 and "in progress" in txt, "%s %s" % (code, txt[:120]))
            code, r = dut.api("/api/fw_update/abort", "POST", {})
            run.check("f12_abort_200", code == 200, "%s %s" % (code, str(r)[:120]))
            st = wait_job(lambda s: s.get("job") and not s["job"]["active"], 60, "the job did not stop")
            run.check("f12_job_aborted", ((st or {}).get("job") or {}).get("state") == "aborted", json.dumps((st or {}).get("job"))[:200])
            end = time.time() + 15
            idle = False
            while time.time() < end and not idle:
                code, o = dut.api("/api/ota/status")
                idle = code == 200 and o.get("state") == "idle"
                time.sleep(1)
            run.check("f12_ota_status_idle_after_abort", idle, json.dumps(o))
            server_ctl(delay_ms=0)

        # ---- close ----------------------------------------------------------------------------------
        print("== close", flush=True)
        if found.get("settings"):
            dut.restart({"fw_update": {k: found["settings"][k] for k in SETTINGS_KEYS if k in found["settings"]}})
        if cert_uploaded:
            dut.api("/api/certs?set=fwbench", "DELETE")
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
