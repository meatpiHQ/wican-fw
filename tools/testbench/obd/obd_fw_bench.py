#!/usr/bin/env python3
"""OBD chip firmware update bench  ->  OBD FW PASS / FAIL   (issue #1023)

Proves the chip firmware update paths of obd_chip on the real MIC3624. Since
2026-11-01 (Ali) there is no forced rewrite: a chip is written only when the
image's version is above the one it reports, never the same version, never a
downgrade. The start-up keeps the v4.52p accept list (a chip on V2.3.22 is
left alone there and flagged outdated) and the user's update lifts it. The
image's version is its file's name (the firmware's contract), which is how
this bench puts the chip on an older firmware at all: the V2.3.22 image
uploaded as V2.3.99.txt counts as newer and is written.

  status        GET /api/obd_chip carries fw.builtin (the packaged image),
                fw.current (the versions the start-up leaves alone) and
                fw.chip (the chip's VTVERS answer); the `obd_chip` console
                command prints that answer
  no-force      POST {"force":true} -> 400; `obd_chip update --force` is
                refused; a file whose name carries no version -> 400
  current-skip  POST {} on a chip at the packaged version: the job ends
                ESP_OK having sent 0 records
  same-file     the packaged image uploaded under its own name
                (/data/V2.3.24.txt): POST {"path"} -> ESP_OK, 0 records
  older-file    the V2.3.22 image uploaded as /data/V2.3.22.txt: ESP_OK,
                0 records (no downgrade)
  to-.22        the V2.3.22 image uploaded as /data/V2.3.99.txt: every
                record goes out, the chip comes back on V2.3.22 and
                fw.outdated is true
  boot-keeps-22 POST /api/restart: the boot's automatic update leaves a
                .22 chip alone (ESP_OK, 0 records), the bring-up's warning
                line is in the log ring, fw.outdated stays true
  lift          POST {} (the Maintenance card's button): every record goes
                out, a second POST meanwhile is 409, the chip comes back
                through a fresh bring-up at 2 Mbaud on the packaged version,
                fw.outdated false
  interrupted   `obd_chip update --file /data/V2.3.99.txt` (the packaged
                image under a higher name) cut by POST /api/restart after
                1200 records: the next boot finds the chip in download mode
                (it answers at 115200, READY high) and the auto-update
                recovers it (ESP_OK, every record sent); the chip is back at
                2 Mbaud on the packaged version
  sleep-waits   `sleep test 25` typed while that file update streams: the
                device stays up until the job is done (the sleep entry waits
                for the update), then sleeps and wakes by its timer; the
                chip is on the packaged version afterwards
  clean-up      the uploaded file is deleted

usage: obd_fw_bench.py <dut_ip> [--fw-dir <dir>] [--skip-sleep] [--interrupt-at N]
  run ON rpi001 (the bench AP side; needs websocket-client for /ws/cli).
  --fw-dir holds the vendor images V2.3.24.txt and V2.3.22.txt (default
  components/obd_chip_manager/obd_fw under the synced tree, ~/wican).
Needs: main firmware on the DUT with the ws_cli bridge (br_cli, the bench
DUT's canonical settings) and obd_chip.auto_update on (the default). Nothing
persistent is changed; 3 planned restarts and 1 sleep/wake. About 8 min with
the sleep leg, 6 without.
"""
import json
import os
import sys
import time
import urllib.request

import websocket  # websocket-client

CHECKS = []
FW_DIR_DEFAULT = os.path.join("components", "obd_chip_manager", "obd_fw")


def now():
    return time.strftime("%H:%M:%S")


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""))
    return bool(ok)


def http(ip, path, method="GET", body=None, timeout=6):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request("http://%s%s" % (ip, path), data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001  (the DUT rebooting or asleep)
        return 0, str(exc)


def upload(ip, name, data):
    """the raw image to /data/<name> over /api/fs/upload (~490 KB)"""
    req = urllib.request.Request("http://%s/api/fs/upload?path=/data/%s" % (ip, name),
                                 data=data, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()[:120].decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:120].decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def delete(ip, name):
    st, r = http(ip, "/api/fs/file?path=/data/%s" % name, "DELETE")
    check("%s deleted from /data" % name, st == 200, "%s %s" % (st, str(r)[:60]))


def cli(ip, line, wait_s=3.0):
    """One console line over /ws/cli; returns the text up to the prompt."""
    try:
        ws = websocket.create_connection("ws://%s/ws/cli" % ip, timeout=6)
    except Exception as exc:  # noqa: BLE001
        return "<ws: %s>" % exc
    ws.send(line + "\n")
    ws.settimeout(0.5)
    out = b""
    end = time.time() + wait_s
    while time.time() < end:
        try:
            frame = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        except Exception:  # noqa: BLE001
            break
        if frame is None:
            break
        out += frame.encode() if isinstance(frame, str) else frame
        if b"wican>" in out:
            break
    ws.close()
    return out.decode("ascii", "replace")


def up(ip):
    st, _ = http(ip, "/api/info", timeout=3)
    return st == 200


def wait_up(ip, budget_s):
    t0 = time.time()
    while time.time() - t0 < budget_s:
        if up(ip):
            return time.time() - t0
        time.sleep(2)
    return None


def wait_down(ip, budget_s):
    t0 = time.time()
    while time.time() - t0 < budget_s:
        if not up(ip):
            return time.time() - t0
        time.sleep(1)
    return None


def fw_update(ip):
    st, d = http(ip, "/api/obd_chip")
    if st != 200 or not isinstance(d, dict):
        return None, None
    return d, d["fw"]["update"]


def wait_job(ip, budget_s, min_age_s=3):
    """Wait for the update job to finish; returns the final update dict."""
    t0 = time.time()
    last = None
    while time.time() - t0 < budget_s:
        d, u = fw_update(ip)
        if u is not None:
            last = u
            if not u["running"] and u["done"] and time.time() - t0 >= min_age_s:
                return u
        time.sleep(1)
    return last


def wait_ready(ip, budget_s):
    t0 = time.time()
    while time.time() - t0 < budget_s:
        d, _ = fw_update(ip)
        if d is not None and d["ready"] and d["claim"] == "none":
            return d
        time.sleep(1)
    return None


def version_of(ip):
    out = cli(ip, "obd_chip", 5)
    for line in out.splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    return ""


def fw_of(ip):
    st, d = http(ip, "/api/obd_chip")
    return (d.get("fw") if st == 200 and isinstance(d, dict) else None) or {}


def restart_and_wait(ip):
    st, r = http(ip, "/api/restart", "POST", {}, timeout=4)
    if not check("restart sent", st == 200, "%s" % st):
        return False
    gone = wait_down(ip, 20)
    came = wait_up(ip, 150)
    return check("device rebooted and came back", gone is not None and came is not None,
                 "down after %s s, up after %s s" % (gone, came))


# ---- the legs -------------------------------------------------------------

def leg_status(ip):
    print("== status ==")
    d, u = fw_update(ip)
    if d is None:
        check("GET /api/obd_chip", False)
        return None
    builtin = d["fw"]["builtin"]
    check("fw.builtin is a V2.x string", builtin.startswith("V2."), builtin)
    check("fw.current names the builtin", builtin in d["fw"]["current"], d["fw"]["current"])
    check("chip ready at 2000000 baud", d["ready"] and d["baud"] == 2000000,
          "ready=%s baud=%s" % (d["ready"], d["baud"]))
    check("fw.chip is the chip's own answer with the packaged version",
          str(d["fw"].get("chip")).startswith("MIC3624") and builtin in str(d["fw"].get("chip")),
          d["fw"].get("chip"))
    check("fw.outdated is false on a chip at the packaged version", d["fw"].get("outdated") is False)
    ver = version_of(ip)
    check("`obd_chip` prints the chip's VTVERS", ver.startswith("MIC3624"), ver)
    return builtin


def leg_no_force(ip):
    print("== no-force ==")
    # the start-up's own update is already booked (done, 0 records): a
    # refused request must leave that state exactly as it is
    d0, u0 = fw_update(ip)
    st, r = http(ip, "/api/obd_chip/update", "POST", {"force": True})
    check("POST {force:true} -> 400", st == 400, "%s %s" % (st, str(r)[:100]))
    out = cli(ip, "obd_chip update --force", 2.0)
    check("`obd_chip update --force` is refused", "forced rewrite is gone" in out,
          out.strip().splitlines()[-1] if out.strip() else "")
    st, r = http(ip, "/api/obd_chip/update", "POST", {"path": "/data/image.txt"})
    check("a file without a version in its name -> 400", st == 400, "%s %s" % (st, str(r)[:100]))
    out = cli(ip, "obd_chip update --file /data/image.txt", 2.0)
    check("the console refuses it too", "must carry its version" in out,
          out.strip().splitlines()[-1] if out.strip() else "")
    d, u = fw_update(ip)
    check("no job ran (the update state is as before)",
          u is not None and not u["running"] and u == u0, "%s -> %s" % (json.dumps(u0), json.dumps(u)))


def leg_current_skip(ip):
    print("== current-skip ==")
    st, r = http(ip, "/api/obd_chip/update", "POST", {})
    check("POST {} -> 202", st == 202, "%s %s" % (st, str(r)[:80]))
    u = wait_job(ip, 60)
    check("job ends ESP_OK with 0 records (chip at the packaged version)",
          u is not None and u["last"] == "ESP_OK" and u["lines"] == 0, json.dumps(u))


def file_update(ip, name, data, expect_write, label):
    """upload data as /data/<name>, POST {"path"}, wait for the job"""
    st, r = upload(ip, name, data)
    check("%s: %s uploaded to /data (%d bytes)" % (label, name, len(data)), st in (200, 201),
          "%s %s" % (st, r))
    st, r = http(ip, "/api/obd_chip/update", "POST", {"path": "/data/" + name})
    check("%s: POST {path} -> 202" % label, st == 202, "%s %s" % (st, str(r)[:80]))
    u = wait_job(ip, 120 if expect_write else 60)
    if expect_write:
        check("%s: every record went out (ESP_OK)" % label,
              u is not None and u["last"] == "ESP_OK" and u["total"] > 3000 and u["lines"] == u["total"],
              json.dumps(u))
    else:
        check("%s: nothing to do (ESP_OK, 0 records)" % label,
              u is not None and u["last"] == "ESP_OK" and u["lines"] == 0, json.dumps(u))
    return u


def leg_same_and_older(ip, builtin, fw24, fw22):
    print("== same-file / older-file ==")
    file_update(ip, "V2.3.24.txt", fw24, False, "same version")
    delete(ip, "V2.3.24.txt")
    file_update(ip, "V2.3.22.txt", fw22, False, "older image")
    delete(ip, "V2.3.22.txt")
    d = wait_ready(ip, 20)
    check("the chip untouched: ready at 2000000", d is not None and d["baud"] == 2000000)
    ver = version_of(ip)
    check("still on the packaged version", builtin in ver, ver)


def leg_to_22(ip, fw22):
    print("== to-.22 (the V2.3.22 image as V2.3.99.txt) ==")
    file_update(ip, "V2.3.99.txt", fw22, True, "the .22 image under a higher name")
    d = wait_ready(ip, 40)
    check("chip back through a fresh bring-up at 2000000",
          d is not None and d["baud"] == 2000000, json.dumps(d and {"ready": d["ready"], "baud": d["baud"]}))
    fw = fw_of(ip)
    check("fw.chip reports V2.3.22 and fw.outdated is true",
          "V2.3.22" in str(fw.get("chip")) and fw.get("outdated") is True, json.dumps(fw))
    delete(ip, "V2.3.99.txt")


def leg_boot_keeps_22(ip):
    print("== boot-keeps-22 ==")
    if not restart_and_wait(ip):
        return
    u = wait_job(ip, 90, min_age_s=0)
    check("the start-up update leaves a .22 chip alone (ESP_OK, 0 records)",
          u is not None and u["done"] and u["last"] == "ESP_OK" and u["lines"] == 0, json.dumps(u))
    d = wait_ready(ip, 40)
    check("chip ready at 2000000 after the boot", d is not None and d["baud"] == 2000000)
    fw = fw_of(ip)
    check("still V2.3.22 and flagged outdated after the boot",
          "V2.3.22" in str(fw.get("chip")) and fw.get("outdated") is True, json.dumps(fw))
    st, ring = http(ip, "/api/logs/ring")
    text = ring if isinstance(ring, str) else json.dumps(ring)
    check("the bring-up's warning line names the update path",
          st == 200 and "is older than the packaged" in text and "obd_chip update" in text,
          [l for l in text.splitlines() if "older than" in l][-1:] if st == 200 else st)
    ver = version_of(ip)
    out = cli(ip, "obd_chip", 5)
    check("`obd_chip` prints V2.3.22 and the warning line", "V2.3.22" in ver and "warning:" in out, ver)


def leg_lift(ip, builtin):
    print("== lift (the card's button) ==")
    st, r = http(ip, "/api/obd_chip/update", "POST", {})
    check("POST {} -> 202", st == 202, "%s %s" % (st, str(r)[:80]))
    st, r = http(ip, "/api/obd_chip/update", "POST", {})
    check("a second POST while running -> 409", st == 409, "%s" % st)
    seen_running = False
    t0 = time.time()
    while time.time() - t0 < 60:
        d, u = fw_update(ip)
        if u and u["running"] and u["lines"] > 0:
            seen_running = True
            break
        time.sleep(0.5)
    check("progress visible while running", seen_running)
    u = wait_job(ip, 120)
    check("job ends ESP_OK with every record sent",
          u is not None and u["last"] == "ESP_OK" and u["total"] > 3000 and u["lines"] == u["total"],
          json.dumps(u))
    d = wait_ready(ip, 40)
    check("chip back through a fresh bring-up at 2000000",
          d is not None and d["baud"] == 2000000, json.dumps(d and {"ready": d["ready"], "baud": d["baud"]}))
    ver = version_of(ip)
    check("chip reports the packaged version", builtin in ver, ver)
    fw = fw_of(ip)
    check("fw.outdated is false again", fw.get("outdated") is False, json.dumps(fw))


def leg_interrupted(ip, builtin, fw24, at):
    print("== interrupted (restart at %d records) ==" % at)
    st, r = upload(ip, "V2.3.99.txt", fw24)
    check("the packaged image uploaded as V2.3.99.txt (%d bytes)" % len(fw24), st in (200, 201),
          "%s %s" % (st, r))
    out = cli(ip, "obd_chip update --file /data/V2.3.99.txt", 1.0)
    check("console update started", "update started" in out, out.splitlines()[0] if out else "")
    sent = False
    t0 = time.time()
    while time.time() - t0 < 60:
        d, u = fw_update(ip)
        if u and u["running"] and u["lines"] >= at:
            st, r = http(ip, "/api/restart", "POST", {}, timeout=4)
            sent = st == 200
            print("%s restart at %d/%d -> %s" % (now(), u["lines"], u["total"], st))
            break
        time.sleep(0.1)
    if not check("restart sent mid-stream", sent):
        return
    gone = wait_down(ip, 20)
    came = wait_up(ip, 150)
    check("device rebooted and came back", gone is not None and came is not None,
          "down after %s s, up after %s s" % (gone, came))
    # the boot's auto-update recovers the chip: the job ends with every record
    u = wait_job(ip, 200, min_age_s=0)
    check("auto-update recovered the chip (ESP_OK, every record)",
          u is not None and u["last"] == "ESP_OK" and u["total"] > 3000 and u["lines"] == u["total"],
          json.dumps(u))
    d = wait_ready(ip, 60)
    check("chip ready at 2000000 after the recovery",
          d is not None and d["baud"] == 2000000, json.dumps(d and {"ready": d["ready"], "baud": d["baud"]}))
    ver = version_of(ip)
    check("chip reports the packaged version after the recovery", builtin in ver, ver)


def leg_sleep_waits(ip, builtin):
    print("== sleep-waits ==")
    out = cli(ip, "obd_chip update --file /data/V2.3.99.txt", 1.0)
    check("console update started (sleep leg)", "update started" in out)
    armed = False
    t0 = time.time()
    while time.time() - t0 < 60:
        d, u = fw_update(ip)
        if u and u["running"] and u["lines"] >= 300:
            out = cli(ip, "sleep test 25", 1.0)
            armed = True
            print("%s sleep test at %d/%d records -> %s" % (now(), u["lines"], u["total"],
                                                             out.replace("\n", " | ")[:100]))
            break
        time.sleep(0.1)
    if not check("sleep test typed mid-stream", armed):
        return
    # the device must stay up until the job is done. The window between the
    # job's end and the sleep is a few seconds and a poll can miss it (run 2
    # on 2026-11-01 did), so the proof is in the timing and in the boot
    # after the wake: the device goes down no sooner than the rest of the
    # stream needs (about 15 s from 300 records: without the wait the sleep
    # entry drops the device within 2 s), and the boot after the wake finds
    # the chip whole (its own start-up update: 0 records, no download-mode
    # recovery) on the packaged version.
    t_cmd = time.time()
    last = None
    gone = None
    while time.time() - t_cmd < 150:
        d, u = fw_update(ip)
        if u is not None:
            last = u
        elif not up(ip):
            gone = time.time() - t_cmd
            break
        time.sleep(0.5)
    check("device slept after the update, no sooner than 10 s after the command (the sleep entry waited)",
          gone is not None and gone >= 10, "down after %s s; last state seen %s" % (gone, json.dumps(last)))
    came = wait_up(ip, 180)
    check("device woke by its timer", came is not None, "up after %s s" % came)
    d = wait_ready(ip, 60)
    check("chip ready at 2000000 after the wake", d is not None and d["baud"] == 2000000)
    u = wait_job(ip, 60, min_age_s=0)
    check("the boot after the wake found the chip whole (its own update: 0 records, no recovery)",
          u is not None and u["done"] and u["last"] == "ESP_OK" and u["lines"] == 0, json.dumps(u))
    ver = version_of(ip)
    check("chip reports the packaged version after the wake", builtin in ver, ver)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    ip = sys.argv[1]
    skip_sleep = "--skip-sleep" in sys.argv
    at = 1200
    if "--interrupt-at" in sys.argv:
        at = int(sys.argv[sys.argv.index("--interrupt-at") + 1])
    fw_dir = FW_DIR_DEFAULT
    if "--fw-dir" in sys.argv:
        fw_dir = sys.argv[sys.argv.index("--fw-dir") + 1]

    print("%s OBD FW bench on %s (images from %s)" % (now(), ip, fw_dir))
    images = {}
    for name in ("V2.3.24.txt", "V2.3.22.txt"):
        path = os.path.join(fw_dir, name)
        try:
            with open(path, "rb") as f:
                images[name] = f.read()
        except OSError as exc:
            check("%s readable" % path, False, str(exc))
    builtin = leg_status(ip) if len(images) == 2 else None
    if builtin:
        fw24, fw22 = images["V2.3.24.txt"], images["V2.3.22.txt"]
        check("the packaged image on the DUT is the one in --fw-dir", builtin == "V2.3.24", builtin)
        leg_no_force(ip)
        leg_current_skip(ip)
        leg_same_and_older(ip, builtin, fw24, fw22)
        leg_to_22(ip, fw22)
        leg_boot_keeps_22(ip)
        leg_lift(ip, builtin)
        leg_interrupted(ip, builtin, fw24, at)
        if not skip_sleep:
            leg_sleep_waits(ip, builtin)
        print("== clean-up ==")
        wait_up(ip, 30)
        delete(ip, "V2.3.99.txt")
    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    print("OBD FW %s (%d checks, %d failed)" % ("PASS" if not fails else "FAIL",
                                               len(CHECKS), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
