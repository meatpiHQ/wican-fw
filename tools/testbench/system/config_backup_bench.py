#!/usr/bin/env python3
"""The configuration backup that carries everything (issue #979, 2026-10-11):
the bench.

The claim: one file carries every part of the device that may leave it and
puts it back; the device keeps a sealed copy of everything, certificates
included, on its internal flash (and the SD card when one is in) and restores
from it; key material never comes back through the API, not through the file
browser either; a backup is checked whole before anything changes.

Rig (TESTING.md): the DUT on the bench AP, reached over HTTP through an ssh
tunnel (--dut localhost:18081, test.ps1 opens it) or directly (--dut <ip:port>).
Nothing else: no PCAN, no PSU, no console. The bench mutates the device
(one harmless setting, the scripts, the dashboard) and relies on the feature
to put it back; the as-found plain backup is saved under the log dir in case
it does not.

Legs:
  b0  as found: status (six parts, the key, the copies), the as-found settings
  b1  the file: plain everything (envelope, five parts, counts as the status);
      redacted (every secret blank); encrypted refused until the key file was
      downloaded, the key file (64 hex), then the encrypted download
  b2  the deny list: /api/fs refuses /data/certs, /data/backups, /sd/backups
      and lists neither under /data
  b3  the round trip from a file: auto_copy off (a planned restart), a harmless
      setting changed and submitted (a restart), a script deleted, the plain
      file inspected (no warning) and restored (a restart), then every part
      equals b1's
  b4  the round trip from the internal copy: refreshed by hand, the same
      mutation, GET copies/internal (six parts, certificates included),
      restored (a restart), every part equals b1's; the SD copy when a card
      is in, else SKIPPED
  b5  refusals: a tampered encrypted file (bad_key, refused), a document with
      wican_backup 3 (newer, refused), today's settings-only document (the
      settings part, restored, a restart)
  b6  the sweep: auto_copy on (a restart), a script uploaded through /api/fs:
      the copy's refresh time moves within 20 s; 150 s idle: no flash write,
      the time stays
  close: no unexpected reset, no E line of the component tags, the worker's
      stack headroom, the heap floor

  python tools/testbench/system/config_backup_bench.py [--dut localhost:18081]
      [--dut-ip 10.42.0.194] [--pi rpi001] [--only b1,b2] [--logdir DIR]

Prints `CONFIG BACKUP PASS` (or `CONFIG BACKUP PARTIAL PASS (legs: ...)`).
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402

OWN_TAGS = ("config_backup", "api_http", "filesystem")
LEGS = ["b0", "b1", "b2", "b3", "b4", "b5", "b6"]
FILE_PARTS = ["settings", "vehicles", "databases", "scripts", "dashboard"]
SECRET_SUFFIXES = ("_password", "private_key", "preshared_key", "auth_key",
                   "_token", "api_key")
SECRET_EXTRA = {("mqtt_manager", "username"), ("espnetlink", "apn_user"),
                ("ha_webhooks", "url"), ("ha_webhooks", "url2"),
                ("data_destinations", "url"),
                ("data_destinations", "basic_username")}
MUTATED = ("rtc_manager", "ntp_server2")   # the harmless setting the legs change
MUTATED_VALUE = "bench-backup.invalid"
BENCH_SCRIPT = "/data/scripts/bench_backup_probe.be"


def raw(base, path, method="GET", data=None, ctype="application/json",
        timeout=60):
    """(status, text) of one request with a raw body."""
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def secret_keys(component, obj, path=""):
    """Every (path, value) of a secret-bearing key in a settings object."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                out += secret_keys(component, v, path + "/" + k)
            elif isinstance(v, str) and (k.endswith(SECRET_SUFFIXES)
                                         or (component, k) in SECRET_EXTRA):
                out.append((path + "/" + k, v))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += secret_keys(component, v, f"{path}[{i}]")
    return out


def part_equal(name, a, b):
    """Two parts of two plain documents, compared the way a user cares."""
    if name == "settings":
        return {k: v.get("data") for k, v in a.items()} == \
               {k: v.get("data") for k, v in b.items()}
    return a == b


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
    ap.add_argument("--dut", default="", help="host:port of the DUT's API "
                    "(a tunnel); default: open one to --dut-ip through --pi")
    ap.add_argument("--dut-ip", default="10.42.0.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--only", default="", help="comma list of legs")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS if not only or l in only or l == "b0"]
    logdir = a.logdir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "test-reports", "logs", time.strftime("config_backup_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("CONFIG BACKUP")

    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18081"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-N", "-L",
                                   f"18081:{a.dut_ip}:80", a.pi])
        time.sleep(2)
    dut = pcbench.Dut(hostport, own_tags=OWN_TAGS, tasks=("cb_copies",))
    base = dut.base

    def save(name, text):
        with open(os.path.join(logdir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def status():
        return dut.get("/api/backup/status")

    def plain_doc():
        code, text = raw(base, "/api/backup")
        if code != 200:
            raise pcbench.Bench(f"GET /api/backup: {code} {text[:200]}")
        return json.loads(text), text

    def mutate(tag):
        """A harmless setting changed and submitted (a restart), a script
        gone, the dashboard gone: what a restore must undo."""
        dut.restart({MUTATED[0]: {MUTATED[1]: MUTATED_VALUE}})
        raw(base, "/api/fs/file?path=" + BENCH_SCRIPT, "DELETE")
        for p in ("/data/dashboard.json",):
            raw(base, "/api/fs/file?path=" + p, "DELETE")
        time.sleep(1)
        now = dut.settings(MUTATED[0]).get(MUTATED[1])
        run.check(f"{tag}_mutation_took", now == MUTATED_VALUE, str(now))

    def stage_and_restore(tag, manifest, include=None):
        """Restore the staged backup and wait for the boot."""
        b0 = dut.boot_count()
        dut.sweep()
        code, r = dut.api("/api/backup/restore", "POST",
                          {"include": include or list(manifest.get("parts", {})),
                           "keep_identity": True, "keep_vpn_keys": False},
                          timeout=120)
        ok = code == 200 and isinstance(r, dict) and r.get("reboot") is True
        run.check(f"{tag}_restore_accepted", ok, f"{code} {str(r)[:200]}")
        if not ok:
            raise pcbench.Bench(f"restore refused: {code} {r}")
        wait_boot(dut, b0)
        time.sleep(4)

    def compare(tag, ref):
        doc, _ = plain_doc()
        for name in FILE_PARTS:
            run.check(f"{tag}_{name}_equal",
                      part_equal(name, doc.get("parts", {}).get(name),
                                 ref.get("parts", {}).get(name)))
        run.check(f"{tag}_mutation_undone",
                  dut.settings(MUTATED[0]).get(MUTATED[1]) != MUTATED_VALUE)

    found = {}
    ref = None
    exit_code = 1
    try:
        # ---- b0: as found ---------------------------------------------------------
        info = dut.get("/api/info")
        st0 = dut.get("/api/status")
        dut.as_found()
        print(f"DUT {info.get('device_id')} {info.get('fw_version')}", flush=True)
        found["settings"] = dut.settings(MUTATED[0])
        found["auto_copy"] = dut.settings("config_backup").get("auto_copy")
        s = status()
        save("b0_status.json", json.dumps(s, indent=1))
        run.check("b0_six_parts", sorted(s.get("parts", {})) ==
                  sorted(FILE_PARTS + ["certs"]), str(sorted(s.get("parts", {}))))
        run.check("b0_key_present", isinstance(s.get("key"), dict)
                  and "downloaded" in s["key"], str(s.get("key")))
        run.check("b0_copies_reported", "internal" in s.get("copies", {})
                  and "sd" in s.get("copies", {}))
        run.metric("sd_card", 1 if s["copies"]["sd"].get("card") else 0)
        run.metric("key_downloaded_as_found", 1 if s["key"]["downloaded"] else 0)
        ref, text = plain_doc()
        save("b0_as_found_plain.json", text)

        # ---- b1: the file ------------------------------------------------------------
        if "b1" in legs:
            run.check("b1_envelope", ref.get("wican_backup") == 2
                      and ref.get("device_id") == info.get("device_id")
                      and ref.get("redacted") is False, str({k: ref.get(k) for k in ("wican_backup", "device_id", "redacted")}))
            run.check("b1_five_parts_no_certs", sorted(ref.get("parts", {})) == sorted(FILE_PARTS),
                      str(sorted(ref.get("parts", {}))))
            run.check("b1_settings_groups_match_status",
                      len(ref["parts"]["settings"]) == s["parts"]["settings"]["groups"],
                      f"{len(ref['parts']['settings'])} vs {s['parts']['settings']['groups']}")
            run.check("b1_scripts_count_match_status",
                      len(ref["parts"]["scripts"]) == s["parts"]["scripts"]["count"])
            run.metric("plain_bytes", len(text))
            secrets = []
            for comp, entry in ref["parts"]["settings"].items():
                secrets += [(comp + p, v) for p, v in secret_keys(comp, entry.get("data"))]
            run.metric("secret_keys_in_plain", len(secrets))
            run.check("b1_plain_carries_secrets_verbatim",
                      any(v != "" for _, v in secrets), "no secret has a value on this unit")
            code, rtext = raw(base, "/api/backup?redact=1")
            red = json.loads(rtext) if code == 200 else {}
            save("b1_redacted.json", rtext)
            blank = []
            for comp, entry in red.get("parts", {}).get("settings", {}).items():
                blank += secret_keys(comp, entry.get("data"))
            run.check("b1_redacted_blanks_every_secret", code == 200 and red.get("redacted") is True
                      and blank and all(v == "" for _, v in blank),
                      str([p for p, v in blank if v != ""][:5]))
            run.check("b1_redacted_keeps_the_rest",
                      red.get("parts", {}).get("vehicles") == ref["parts"]["vehicles"])
            code, etext = raw(base, "/api/backup?encrypt=1")
            if s["key"]["downloaded"]:
                print("b1: the key was downloaded before this run: the 409 cannot be shown", flush=True)
            else:
                run.check("b1_encrypted_refused_before_the_key", code == 409, f"{code} {etext[:120]}")
            code, ktext = raw(base, "/api/backup/key")
            hexline = [ln for ln in ktext.splitlines() if ln and not ln.startswith("#")]
            run.check("b1_key_file", code == 200 and len(hexline) == 1
                      and len(hexline[0]) == 64 and all(c in "0123456789abcdef" for c in hexline[0]),
                      ktext[:80].replace("\n", " "))
            save("b1_key.txt", ktext)
            s2 = status()
            run.check("b1_key_marked_downloaded", s2["key"]["downloaded"] is True)
            code, etext = raw(base, "/api/backup?encrypt=1")
            enc = json.loads(etext) if code == 200 else {}
            save("b1_encrypted.json", etext)
            run.check("b1_encrypted_envelope", code == 200 and enc.get("encrypted") is True
                      and enc.get("cipher") == "aes-256-gcm" and len(base64.b64decode(enc.get("nonce", ""))) == 12
                      and len(enc.get("data", "")) > 100 and "parts" not in enc, str({k: enc.get(k) for k in ("encrypted", "cipher")}))
            run.metric("encrypted_bytes", len(etext))
            found["enc"] = etext

        # ---- b2: the deny list ---------------------------------------------------------
        if "b2" in legs:
            for path in ("/api/fs/list?path=/data/certs",
                         "/api/fs/info?path=/data/backups",
                         "/api/fs/download?path=/data/backups/latest/manifest.json",
                         "/api/fs/list?path=/sd/backups"):
                code, t = raw(base, path)
                run.check("b2_refused_" + path.split("path=")[1].replace("/", "_"), code == 404, f"{code} {t[:80]}")
            code, t = raw(base, "/api/fs/list?path=/data")
            names = [e.get("name") for e in json.loads(t).get("entries", [])] if code == 200 else []
            run.check("b2_data_listing_hides_the_folders", code == 200 and "certs" not in names and "backups" not in names, str(names))
            code, t = raw(base, "/api/fs/mkdir?path=/data/backups/x", "POST", b"")
            run.check("b2_mkdir_under_backups_refused", code == 404, str(code))

        # ---- b3: the round trip from a file ----------------------------------------------
        if "b3" in legs:
            dut.restart({"config_backup": {"auto_copy": False}})
            mutate("b3")
            code, t = raw(base, "/api/backup/inspect?name=b0_as_found_plain.json", "POST",
                          found and open(os.path.join(logdir, "b0_as_found_plain.json"), "rb").read())
            man = json.loads(t) if code == 200 else {}
            save("b3_inspect.json", t)
            run.check("b3_inspect_clean", code == 200 and not man.get("locked") and not man.get("refused")
                      and sorted(man.get("parts", {})) == sorted(FILE_PARTS) and man.get("warnings") == [],
                      str({k: man.get(k) for k in ("locked", "refused", "warnings", "errors")}))
            run.check("b3_inspect_facts", man.get("device_id") == info.get("device_id")
                      and man.get("other_device") is False and man.get("source") == "file")
            stage_and_restore("b3", man)
            compare("b3", ref)

        # ---- b4: the round trip from the internal copy ------------------------------------
        if "b4" in legs:
            code, r = dut.api("/api/backup/copies/refresh", "POST", {}, timeout=60)
            run.check("b4_refresh_now", code == 200 and "internal" in (r.get("written") or []), f"{code} {r}")
            t_ref = status()["copies"]["internal"].get("refreshed")
            mutate("b4")
            code, r = dut.api("/api/backup/copies/internal", timeout=60)
            save("b4_internal_manifest.json", json.dumps(r, indent=1))
            run.check("b4_internal_copy_staged", code == 200 and isinstance(r, dict) and not r.get("locked")
                      and not r.get("refused") and r.get("source") == "internal", f"{code} {str(r)[:200]}")
            run.check("b4_internal_copy_has_six_parts_with_certs",
                      isinstance(r, dict) and sorted(r.get("parts", {})) == sorted(FILE_PARTS + ["certs"]),
                      str(sorted(r.get("parts", {})) if isinstance(r, dict) else r))
            run.check("b4_copy_taken_before_the_mutation", isinstance(r, dict) and r.get("created") == t_ref,
                      f"{r.get('created') if isinstance(r, dict) else r} vs {t_ref}")
            stage_and_restore("b4", r)
            compare("b4", ref)
            if status()["copies"]["sd"].get("card"):
                code, r = dut.api("/api/backup/copies/sd", timeout=60)
                run.check("b4_sd_copy_staged", code == 200 and isinstance(r, dict) and not r.get("locked")
                          and sorted(r.get("parts", {})) == sorted(FILE_PARTS + ["certs"]), f"{code} {str(r)[:200]}")
                dut.api("/api/backup/staged", "DELETE")
            else:
                print("b4: no SD card in the DUT: the SD copy is SKIPPED", flush=True)

        # ---- b5: refusals -------------------------------------------------------------------
        if "b5" in legs:
            enc_text = found.get("enc") or raw(base, "/api/backup?encrypt=1")[1]
            enc = json.loads(enc_text)
            data = enc["data"]
            mid = len(data) // 2
            enc["data"] = data[:mid] + ("A" if data[mid] != "A" else "B") + data[mid + 1:]
            code, t = raw(base, "/api/backup/inspect?name=tampered.json", "POST", json.dumps(enc).encode())
            man = json.loads(t) if code == 200 else {}
            run.check("b5_tampered_refused", code == 200 and man.get("refused") is True and "bad_key" in man.get("warnings", []),
                      f"{code} {str(man)[:200]}")
            newer = dict(ref)
            newer["wican_backup"] = 3
            code, t = raw(base, "/api/backup/inspect?name=newer.json", "POST", json.dumps(newer).encode())
            man = json.loads(t) if code == 200 else {}
            run.check("b5_newer_refused", code == 200 and man.get("refused") is True and "newer" in man.get("warnings", []),
                      f"{code} {str(man)[:200]}")
            code, r = dut.api("/api/backup/restore", "POST", {"include": FILE_PARTS})
            run.check("b5_restore_of_a_refused_backup_is_409", code == 409, f"{code} {r}")
            dut.api("/api/backup/staged", "DELETE")
            code, v1 = raw(base, "/api/settings/backup")
            run.check("b5_legacy_settings_backup_still_served", code == 200 and json.loads(v1).get("wican_backup") == 1)
            dut.restart({MUTATED[0]: {MUTATED[1]: MUTATED_VALUE}})
            code, t = raw(base, "/api/backup/inspect?name=v1.json", "POST", v1.encode())
            man = json.loads(t) if code == 200 else {}
            run.check("b5_v1_reads_as_the_settings_part", code == 200 and man.get("version") == 1
                      and list(man.get("parts", {})) == ["settings"] and not man.get("refused"), f"{code} {str(man)[:200]}")
            stage_and_restore("b5", man, ["settings"])
            run.check("b5_v1_restored_the_setting", dut.settings(MUTATED[0]).get(MUTATED[1]) != MUTATED_VALUE)

        # ---- b6: the sweep ----------------------------------------------------------------
        if "b6" in legs:
            dut.restart({"config_backup": {"auto_copy": True}})
            time.sleep(35)                        # the boot sweep runs 20 s after start
            s6 = status()
            t0 = s6["copies"]["internal"].get("refreshed")
            w0 = dut.get("/api/status").get("health", {}).get("flash", {}).get("writes")
            code, t = raw(base, "/api/fs/upload?path=" + BENCH_SCRIPT, "POST",
                          b"# bench_backup_probe.be\nlog('backup bench')\n", "application/octet-stream")
            run.check("b6_script_uploaded", code == 200, f"{code} {t[:80]}")
            moved = None
            for _ in range(25):
                time.sleep(1)
                t1 = status()["copies"]["internal"].get("refreshed")
                if t1 and t1 != t0:
                    moved = t1
                    break
            run.check("b6_copy_refreshed_after_the_upload_within_25s", moved is not None, f"{t0} -> {moved}")
            run.metric("copy_refreshed_after_upload_s", _ + 1 if moved else -1)
            raw(base, "/api/fs/file?path=" + BENCH_SCRIPT, "DELETE")
            time.sleep(12)
            t2 = status()["copies"]["internal"].get("refreshed")
            w1 = dut.get("/api/status").get("health", {}).get("flash", {}).get("writes")
            print(f"b6: idle 150 s from refreshed {t2}, flash writes {w1}", flush=True)
            time.sleep(150)
            t3 = status()["copies"]["internal"].get("refreshed")
            w2 = dut.get("/api/status").get("health", {}).get("flash", {}).get("writes")
            run.check("b6_idle_sweep_writes_nothing", t3 == t2 and (w2 is None or w1 is None or w2 == w1),
                      f"refreshed {t2} -> {t3}, flash writes {w1} -> {w2}")
            run.metric("idle_flash_writes", (w2 - w1) if isinstance(w1, int) and isinstance(w2, int) else -1)

        # ---- close ----------------------------------------------------------------------------
        if found.get("auto_copy") is not None and \
                dut.settings("config_backup").get("auto_copy") != found["auto_copy"]:
            dut.restart({"config_backup": {"auto_copy": found["auto_copy"]}})
        if dut.settings(MUTATED[0]).get(MUTATED[1]) == MUTATED_VALUE:
            dut.restart({MUTATED[0]: {MUTATED[1]: found["settings"].get(MUTATED[1], "")}})
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        exit_code = run.verdict("legs: " + " ".join(legs) if only else "")
    except pcbench.Bench as e:
        print("BENCH:", e, flush=True)
        exit_code = 2
    finally:
        if tunnel is not None:
            tunnel.terminate()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
