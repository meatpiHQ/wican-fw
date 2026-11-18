#!/usr/bin/env python3
"""A firmware file installed by upload is confirmed once it has settled
-> UPLOAD CONFIRM PASS.

The web page's manual install sends the file to POST /api/ota/upload; the
unit writes the other slot and restarts into it. On a unit whose bootloader
can fall back (`rollback.bootloader_rollback` true) the new image starts
unconfirmed, and a reset before it is confirmed makes the bootloader go back
to the old one. Updates from the release list are confirmed when the run
has settled (10 minutes without a crash). Until 2026-11-18 an uploaded image
was not: it stayed unconfirmed until the next planned restart.

The bench uploads an image (by default the one in build/, which may be the
one the unit runs: the slot changes all the same), checks that it starts
unconfirmed, leaves the unit ALONE until its run has settled, and checks
that it is confirmed then, with no restart in between.

  python upload_confirm_bench.py --dut localhost:18081 --dut-ip <as the Pi sees it>
         [--image build/wican-fw_obd_pro.bin] [--pi rpi001]
About 11 minutes. Expected final line: UPLOAD CONFIRM PASS
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

fails = []
passes = []


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--dut-ip", required=True, help="the unit's address as the Pi sees it (the upload runs from there)")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--image", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
                                                    "build", "wican-fw_obd_pro.bin"))
    ap.add_argument("--settle-s", type=int, default=600)
    a = ap.parse_args()
    base = "http://" + a.dut
    t0 = time.time()

    def get(path, timeout=8):
        try:
            with urllib.request.urlopen(base + path, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    def wait_up(secs=150):
        end = time.time() + secs
        while time.time() < end:
            st = get("/api/status", 4)
            if st:
                return st
            time.sleep(2)
        return None

    def rollback():
        return (get("/api/fw_update") or {}).get("rollback") or {}

    st = wait_up(40)        # the caller's tunnel may not stand yet (an ssh can take seconds to begin)
    if not st:
        print("UPLOAD CONFIRM FAIL: no DUT at", a.dut)
        sys.exit(1)
    rb = rollback()
    unexp0 = st.get("unexpected_resets")
    err0 = (st.get("health") or {}).get("log_errors")
    print("dut: %s, running %s, %s, bootloader_rollback=%s" % (st.get("version"), rb.get("running"), rb.get("ota_state"),
                                                              rb.get("bootloader_rollback")), flush=True)
    if not rb.get("bootloader_rollback"):
        print("UPLOAD CONFIRM SKIP: this unit's bootloader never falls back, an image is never unconfirmed on it")
        sys.exit(0)
    if rb.get("ota_state") != "valid":
        print("UPLOAD CONFIRM FAIL: the running image is not confirmed at the start (%s): restart the unit once"
              % rb.get("ota_state"))
        sys.exit(1)

    with open(a.image, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    print("uploading %s (sha256 %s...)" % (os.path.basename(a.image), sha[:12]), flush=True)
    subprocess.run(["scp", "-q", a.image, a.pi + ":/tmp/upload_confirm.bin"], check=True, timeout=120)
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", a.pi,
                              "curl -s -m 240 -X POST --data-binary @/tmp/upload_confirm.bin "
                              "'http://%s/api/ota/upload?sha256=%s'" % (a.dut_ip, sha)],
                             capture_output=True, text=True, timeout=300).stdout
    finally:
        subprocess.run(["ssh", "-o", "BatchMode=yes", a.pi, "rm -f /tmp/upload_confirm.bin"], timeout=30)
    if not check("U1 the unit took the file and restarts into it", '"ok":true' in out and '"reboot":true' in out, out[:120]):
        print("UPLOAD CONFIRM FAIL: " + ", ".join(fails))
        sys.exit(1)
    time.sleep(8)
    st = wait_up()
    rb1 = rollback()
    boot1 = (st or {}).get("boot_count")
    check("U2 the new image runs from the other slot and starts unconfirmed",
          bool(st) and rb1.get("running") != rb.get("running") and rb1.get("ota_state") == "pending_verify"
          and rb1.get("pending_verify") is True,
          {"running": rb1.get("running"), "ota_state": rb1.get("ota_state")})

    # leave it alone until its run has settled: no request that restarts,
    # one status read every 30 s
    print("waiting for the run to settle (%d s), hands off..." % a.settle_s, flush=True)
    settled_at = None
    end = time.time() + a.settle_s + 90
    while time.time() < end:
        hist = get("/api/restart/history") or {}
        if (hist.get("brake") or {}).get("settled"):
            settled_at = time.time()
            break
        time.sleep(30)
    check("U3 the run settled", settled_at is not None)
    time.sleep(15)                   # the updater looks every 5 s
    st2 = get("/api/status") or {}
    rb2 = rollback()
    check("U4 the uploaded image is confirmed once it has settled, with no restart",
          rb2.get("ota_state") == "valid" and rb2.get("pending_verify") is False
          and rb2.get("running") == rb1.get("running") and st2.get("boot_count") == boot1,
          {"ota_state": rb2.get("ota_state"), "running": rb2.get("running"),
           "boot_count": (boot1, st2.get("boot_count"))})
    check("U5 no unexpected reset, no new E line",
          st2.get("unexpected_resets") == unexp0 and (st2.get("health") or {}).get("log_errors") == err0,
          {"unexpected": (unexp0, st2.get("unexpected_resets")),
           "log_errors": (err0, (st2.get("health") or {}).get("log_errors"))})

    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t0), flush=True)
    if fails:
        print("UPLOAD CONFIRM FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("UPLOAD CONFIRM PASS")


if __name__ == "__main__":
    main()
