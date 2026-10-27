#!/usr/bin/env python3
"""OBD chip firmware update bench  ->  OBD FW PASS / FAIL   (issue #1023)

Proves the chip firmware update paths of obd_chip on the real MIC3624:

  status        GET /api/obd_chip carries fw.builtin (the packaged image) and
                fw.current (the versions the auto-update leaves alone); the
                `obd_chip` console command prints the chip's VTVERS answer
  current-skip  POST /api/obd_chip/update without force on a chip that is
                current: the job ends ESP_OK having sent 0 records
  forced        POST {"force":true}: 3557 records go out, the job ends
                ESP_OK, the chip comes back through a fresh bring-up at
                2 Mbaud reporting the packaged version
  interrupted   `obd_chip update --force` cut by POST /api/restart after
                1200 records: the next boot finds the chip in download mode
                (it answers at 115200, READY high) and the auto-update
                recovers it (ESP_OK, every record sent); the chip is back at
                2 Mbaud on the packaged version
  sleep-waits   `sleep test 25` typed while a forced update streams: the
                device stays up until the job is done (the sleep entry waits
                for the update), then sleeps and wakes by its timer; the
                chip is current afterwards

usage: obd_fw_bench.py <dut_ip> [--skip-sleep] [--interrupt-at N]
  run ON rpi001 (the bench AP side; needs websocket-client for /ws/cli).
Needs: main firmware on the DUT with the ws_cli bridge (br_cli, the bench
DUT's canonical settings) and obd_chip.auto_update on (the default). Nothing
persistent is changed. About 6 min with the sleep leg, 4 without.
"""
import json
import sys
import time
import urllib.request

import websocket  # websocket-client

CHECKS = []


def now():
    return time.strftime("%H:%M:%S")


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + detail + ")") if detail else ""))
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
    if st != 200:
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
    ver = version_of(ip)
    check("`obd_chip` prints the chip's VTVERS", ver.startswith("MIC3624"), ver)
    return builtin


def leg_current_skip(ip, builtin):
    print("== current-skip ==")
    st, r = http(ip, "/api/obd_chip/update", "POST", {})
    check("POST without force -> 202", st == 202, "%s %s" % (st, str(r)[:80]))
    u = wait_job(ip, 60)
    check("job ends ESP_OK with 0 records (chip current)",
          u is not None and u["last"] == "ESP_OK" and u["lines"] == 0,
          json.dumps(u))


def leg_forced(ip, builtin):
    print("== forced ==")
    st, r = http(ip, "/api/obd_chip/update", "POST", {"force": True})
    check("POST force -> 202", st == 202, "%s %s" % (st, str(r)[:80]))
    st, r = http(ip, "/api/obd_chip/update", "POST", {"force": True})
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


def leg_interrupted(ip, builtin, at):
    print("== interrupted (restart at %d records) ==" % at)
    out = cli(ip, "obd_chip update --force", 1.0)
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
    out = cli(ip, "obd_chip update --force", 1.0)
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
    # the device must stay reachable until the job is done
    u = wait_job(ip, 120)
    check("job finished before the device slept (ESP_OK, every record)",
          u is not None and u["last"] == "ESP_OK" and u["lines"] == u["total"], json.dumps(u))
    gone = wait_down(ip, 90)
    check("device slept after the update", gone is not None, "down after %s s" % gone)
    came = wait_up(ip, 180)
    check("device woke by its timer", came is not None, "up after %s s" % came)
    d = wait_ready(ip, 60)
    check("chip ready at 2000000 after the wake", d is not None and d["baud"] == 2000000)
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

    print("%s OBD FW bench on %s" % (now(), ip))
    builtin = leg_status(ip)
    if builtin:
        leg_current_skip(ip, builtin)
        leg_forced(ip, builtin)
        leg_interrupted(ip, builtin, at)
        if not skip_sleep:
            leg_sleep_waits(ip, builtin)
    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    print("OBD FW %s (%d checks, %d failed)" % ("PASS" if not fails else "FAIL",
                                               len(CHECKS), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
