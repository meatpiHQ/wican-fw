"""The release checklist's last step from the bench: a DUT running the
PREVIOUS published release takes the new one from the LIVE list, the way a
user's device does (manifest on raw.githubusercontent.com, the notes, the
download from the GitHub release, the install, the restart, the
confirmation). Runs ON the Pi (python3, no extras), prints a stamped
transcript, ends with `VERDICT: FIELD CHECK PASS|FAIL`.

The bench is air-gapped, so around this script, by hand:
  1. flash the previous release's published flash set on the DUT over the
     console port (the zip from the GitHub release, sha256 against the
     manifest): esptool write-flash @flash_args.txt in the unzipped folder
  2. on the Pi: sudo -n ip route add default via 192.168.8.1 dev eth0 metric 500
     (NetworkManager's shared profile already masquerades 10.42.1.0/24 and
     forwards; the DUT's gateway and resolver are the Pi)
  3. scp this file to the Pi and run: python3 -u field_check_pi.py <from> <to>
  4. on the Pi: sudo -n ip route del default via 192.168.8.1 dev eth0
The script switches the DUT's channel (a settings apply = a planned
restart), checks, reads the notes, installs, waits for the new image's
boot, restarts once more so the pre-restart hook confirms the image, and
puts the channel back to stable. First run 2026-10-27 (6.0.0-beta.4 to
beta.5): the download of 3.9 MB took 38 s through the Pi.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://" + (sys.argv[3] if len(sys.argv) > 3 else "10.42.1.194")
FROM_V = sys.argv[1] if len(sys.argv) > 1 else "6.0.0-beta.4"
TO_V = sys.argv[2] if len(sys.argv) > 2 else "6.0.0-beta.5"
CHANNEL = sys.argv[4] if len(sys.argv) > 4 else "beta"
fails = []


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def check(ok, what):
    log(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


def get(path, timeout=6):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, r.read().decode()


def send(path, body, method="POST", timeout=12):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def wait_version(v, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            s, b = get("/api/info", 4)
            d = json.loads(b)
            if d.get("fw_version") == v:
                return d
        except Exception:
            pass
        time.sleep(2)
    return None


def fw_update():
    s, b = get("/api/fw_update", 8)
    return json.loads(b)


def set_channel(ch):
    s, b = get("/api/settings/fw_update")
    cfg = json.loads(b)
    for k in ("degraded", "pending_reboot"):
        cfg.pop(k, None)
    cfg["channel"] = ch
    s, b = send("/api/settings/fw_update", cfg, "PUT")
    log("PUT channel=%s -> %d %s" % (ch, s, b[:160]))
    s2, b2 = send("/api/restart", {})
    log("POST /api/restart -> %d %s" % (s2, b2[:80]))
    time.sleep(6)
    return s


# A. the flashed previous release boots
info = wait_version(FROM_V, 120)
check(info is not None, "the DUT boots the flashed %s" % FROM_V)
if info is None:
    print("VERDICT: FIELD CHECK FAIL (no %s boot)" % FROM_V)
    sys.exit(1)
log("info: build %s partition %s" % (info.get("fw_build"), info.get("partition")))

# B. the release's channel (a settings apply = a planned restart)
set_channel(CHANNEL)
info = wait_version(FROM_V, 120)
check(info is not None, "the DUT is back after the settings restart")
fu = fw_update()
check(fu.get("channel") == CHANNEL, "channel is %s after the apply (%s)" % (CHANNEL, fu.get("channel")))

# C. Check now against the live list
s, b = send("/api/fw_update/check", {})
check(s == 202, "POST /api/fw_update/check -> %d %s" % (s, b[:100]))
t0 = time.time()
lc = {}
while time.time() - t0 < 90:
    time.sleep(3)
    fu = fw_update()
    lc = fu.get("last_check") or {}
    if lc.get("age_s", 9999) < 60 and (lc.get("ok") or lc.get("error")):
        break
log("last_check: %s" % json.dumps(lc))
check(lc.get("ok") is True, "the live manifest was fetched (ok=%s error=%s)" % (lc.get("ok"), lc.get("error")))
me = fu.get("targets", {}).get("self", {})
latest = me.get("latest") or {}
log("targets.self: installed=%s latest=%s available=%s installable=%s reason=%s" % (
    me.get("installed"), latest.get("version"), me.get("available"), me.get("installable"), me.get("reason")))
check(latest.get("version") == TO_V, "the list offers %s" % TO_V)
check(me.get("installable") is True, "it is installable from %s" % FROM_V)

# D. the notes the device shows (rendered by the device from the published file)
try:
    s, b = get("/api/fw_update/notes?target=self&version=" + TO_V, 20)
    check(s == 200 and "WiCAN PRO " + TO_V in b, "the notes of %s arrive through the device (%d, %d bytes)" % (TO_V, s, len(b)))
except Exception as e:
    check(False, "notes fetch raised %s" % e)

# E. install
s, b = send("/api/fw_update/install", {"target": "self"})
check(s == 202, "POST /api/fw_update/install -> %d %s" % (s, b[:160]))
last_state = ""
t0 = time.time()
gone = False
while time.time() - t0 < 400:
    time.sleep(2)
    try:
        fu = fw_update()
    except Exception:
        gone = True
        log("the DUT stopped answering (restarting into the new image) after %.0f s" % (time.time() - t0))
        break
    job = fu.get("job") or {}
    st = json.dumps({k: job.get(k) for k in ("state", "received", "total", "error", "version") if k in job})
    if st != last_state:
        log("job: %s" % st)
        last_state = st
    if job.get("error"):
        check(False, "the install job reported %s" % job.get("error"))
        break
    if job.get("state") in ("failed", "aborted"):
        check(False, "the install job ended %s" % job.get("state"))
        break
check(gone, "the install job reached the restart")

# F. the new image boots
info = wait_version(TO_V, 180)
check(info is not None, "the DUT boots %s after the update" % TO_V)
if info:
    log("info: build %s partition %s api_level %s" % (info.get("fw_build"), info.get("partition"), info.get("api_level")))
fu = fw_update()
log("last_result after the boot: " + json.dumps(fu.get("last_result")))
log("rollback after the boot: " + json.dumps(fu.get("rollback")))

# G. the confirmation: a planned restart confirms the image (the pre-restart hook)
log("settling 45 s before the planned restart")
time.sleep(45)
s, b = send("/api/restart", {})
log("POST /api/restart -> %d" % s)
time.sleep(6)
info = wait_version(TO_V, 120)
check(info is not None, "the DUT is back on %s after the planned restart" % TO_V)
fu = fw_update()
lr = fu.get("last_result") or {}
rb = fu.get("rollback") or {}
log("last_result: " + json.dumps(lr))
log("rollback: " + json.dumps(rb))
check(lr.get("state") == "verified" and lr.get("to") == TO_V and lr.get("from") == FROM_V,
      "the updater's record reads verified, %s to %s" % (FROM_V, TO_V))
check(rb.get("ota_state") == "valid" and rb.get("pending_verify") is False,
      "the running image is valid (ota_state=%s, pending_verify=%s)" % (rb.get("ota_state"), rb.get("pending_verify")))

# H. restore the shipping channel
set_channel("stable")
info = wait_version(TO_V, 120)
check(info is not None, "the DUT is back after the restore restart")
s, b = get("/api/settings/fw_update")
check(json.loads(b).get("channel") == "stable", "channel back to stable")
s, b = get("/api/status", 8)
st = json.loads(b)
check(not st.get("faults"), "no fault on the DUT (faults=%s)" % st.get("faults"))

print("checks failed: %d %s" % (len(fails), fails))
print("VERDICT: FIELD CHECK " + ("PASS" if not fails else "FAIL"))
