#!/usr/bin/env python3
"""A firmware file installed by upload is confirmed at its first start, before
the OBD chip is brought up, and nothing puts the device to sleep while it is
received -> UPLOAD CONFIRM PASS.

The web page's manual install sends the file to POST /api/ota/upload; the
unit writes the other slot and restarts into it. On a unit whose bootloader
can fall back (`rollback.bootloader_rollback` true) the new image starts
unconfirmed, and a reset before it is confirmed makes the bootloader go back
to the old one.

History of the rule: until 2026-11-18 an uploaded image stayed unconfirmed
until the next planned restart; from then until 2026-11-21 it was confirmed
once its run had settled (10 minutes). GitHub issue #1075 (Ali, 2026-11-21):
a power cut in those minutes brought the previous firmware back, and one
from 6.0.0-beta.5 or older then writes its older chip firmware over the OBD
chip's and pulses the chip's reset line. Every new image is now confirmed at
its first start, before the chip hears a word from it, and the sleep manager
stays awake while an image is being received.

  U1  the unit takes the file and restarts into it
  U2  the new image runs from the other slot and reads `valid` at the first
      status read, no ten minutes, no restart
  U3  the log ring holds the confirm ahead of the chip's first word
  U4  (--no-cut skips it) the supply cut inside the image's first minute:
      back on the same slot, `valid`, a power-on reset
  U5  (--no-sleep skips it) the supply under the sleep voltage until the
      sleep countdown is 22 s from its end, then a second upload (about
      40 s): the upload ends well and the unit restarts into the image, it
      did not go to sleep under it. The unit's own sleep settings, nothing
      changed: with the default 5 minutes this leg waits about that long
  U6  no unexpected reset, no E line

  python upload_confirm_bench.py --dut localhost:18081 --dut-ip <as the Pi sees it>
         [--image build/wican-fw_obd_pro.bin] [--pi rpi001] [--psu auto]
About 9 minutes with a 5 minute sleep delay. Expected final line: UPLOAD CONFIRM PASS
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
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
    ap.add_argument("--image", default=os.path.join(HERE, "..", "..", "..", "build", "wican-fw_obd_pro.bin"))
    ap.add_argument("--psu", default="auto")
    ap.add_argument("--no-cut", action="store_true", help="no power cut (a rig without a switchable supply)")
    ap.add_argument("--no-sleep", action="store_true", help="no second upload under a sleep countdown")
    ap.add_argument("--sleep-lead-s", type=int, default=22,
                    help="the second upload starts when the countdown has this long left (an upload takes about 40 s)")
    a = ap.parse_args()
    base = "http://" + a.dut
    t0 = time.time()

    def get(path, timeout=8):
        try:
            with urllib.request.urlopen(base + path, timeout=timeout) as r:
                txt = r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001  (a restart in flight, the tunnel)
            return None
        try:
            return json.loads(txt)
        except ValueError:
            return txt

    def wait_up(secs=150):
        end = time.time() + secs
        while time.time() < end:
            st = get("/api/status", 4)
            if isinstance(st, dict):
                return st
            time.sleep(2)
        return None

    def rollback():
        fu = get("/api/fw_update")
        return (fu.get("rollback") if isinstance(fu, dict) else None) or {}

    def this_boot():
        hist = get("/api/restart/history")
        recs = (hist.get("records") if isinstance(hist, dict) else None) or [{}]
        return max(recs, key=lambda x: x.get("seq") or 0)

    with open(a.image, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()

    def upload(result):
        """The file from the Pi (the page's route), blocking; the answer in result[0]."""
        try:
            result.append(subprocess.run(["ssh", "-o", "BatchMode=yes", a.pi,
                                          "curl -s -m 240 -X POST --data-binary @/tmp/upload_confirm.bin "
                                          "'http://%s/api/ota/upload?sha256=%s'" % (a.dut_ip, sha)],
                                         capture_output=True, text=True, timeout=300).stdout)
        except Exception as e:  # noqa: BLE001
            result.append("<%s>" % e)

    st = wait_up(40)        # the caller's tunnel may not stand yet (an ssh can take seconds to begin)
    if not st:
        print("UPLOAD CONFIRM FAIL: no DUT at", a.dut)
        sys.exit(1)
    rb = rollback()
    unexp0 = st.get("unexpected_resets")
    cut = False
    print("dut: %s, running %s, %s, bootloader_rollback=%s" % (st.get("version"), rb.get("running"), rb.get("ota_state"),
                                                              rb.get("bootloader_rollback")), flush=True)
    if not rb.get("bootloader_rollback"):
        print("UPLOAD CONFIRM SKIP: this unit's bootloader never falls back, an image is never unconfirmed on it")
        sys.exit(0)
    if rb.get("ota_state") != "valid":
        print("UPLOAD CONFIRM FAIL: the running image is not confirmed at the start (%s): restart the unit once"
              % rb.get("ota_state"))
        sys.exit(1)

    print("uploading %s (sha256 %s...)" % (os.path.basename(a.image), sha[:12]), flush=True)
    subprocess.run(["scp", "-q", a.image, a.pi + ":/tmp/upload_confirm.bin"], check=True, timeout=120)
    try:
        # ---- U1, U2, U3: the upload, the first start -------------------------
        out = []
        upload(out)
        if not check("U1 the unit took the file and restarts into it", '"ok":true' in out[0] and '"reboot":true' in out[0],
                     out[0][:120]):
            print("UPLOAD CONFIRM FAIL: " + ", ".join(fails))
            sys.exit(1)
        t_up = time.time()
        time.sleep(4)
        rb1 = {}
        while time.time() - t_up < 120:
            rb1 = rollback()
            if rb1.get("running") and rb1.get("running") != rb.get("running"):
                break
            time.sleep(0.5)
        check("U2 the new image runs from the other slot and is confirmed at its first status read (%.0f s after the upload)"
              % (time.time() - t_up),
              rb1.get("running") not in (None, rb.get("running")) and rb1.get("ota_state") == "valid"
              and rb1.get("pending_verify") is False,
              {"running": rb1.get("running"), "ota_state": rb1.get("ota_state")})
        st1 = wait_up(30) or {}
        time.sleep(6)                    # the chip's bring-up ends
        text = get("/api/logs/ring", 10)
        text = text if isinstance(text, str) else json.dumps(text)
        p_conf = text.find("the running image is new: confirmed before the OBD chip is brought up")
        firsts = [p for p in (text.find("obd_chip: chip awake"), text.find("obd_chip: chip not ready"),
                              text.find("obd_chip: started")) if p >= 0]
        check("U3 the ring holds the confirm, ahead of the chip's first word",
              p_conf >= 0 and bool(firsts) and p_conf < min(firsts), [p_conf, firsts])

        # ---- U4: a power cut inside the first minute ---------------------------
        if not (a.no_cut and a.no_sleep):
            from bench_psu import open_psu
            psu = open_psu(a.psu, verbose=False)
        if not a.no_cut:
            age = time.time() - t_up
            cut = True
            psu.output(False)            # the simulator restarts itself and comes back with the output on
            time.sleep(3)
            psu.output(True)
            time.sleep(4)
            st_c = wait_up(150)
            rb_c = rollback()
            rec = this_boot()
            check("U4 a power cut %.0f s into the new image's first start: back on the same slot, valid, a power-on reset" % age,
                  bool(st_c) and rb_c.get("running") == rb1.get("running") and rb_c.get("ota_state") == "valid"
                  and rec.get("reason") == "poweron",
                  {"running": (rb1.get("running"), rb_c.get("running")), "ota_state": rb_c.get("ota_state"),
                   "reset": rec.get("reason")})

        # ---- U5: no sleep while an image is received ----------------------------
        if not a.no_sleep:
            sl = get("/api/sleep") or {}
            if not (isinstance(sl, dict) and sl.get("enabled")):
                print("U5 skipped: sleep is off on this unit (no countdown to run out under an upload)", flush=True)
            else:
                rb_a = rollback()
                v_awake = psu.voltage_setpoint()
                v_parked = round(float(sl.get("sleep_v") or 13.1) - 0.6, 2)
                out2 = []
                try:
                    psu.set_voltage(v_parked)        # the car is off: the countdown begins
                    left = None
                    end = time.time() + 420
                    while time.time() < end:
                        sl = get("/api/sleep") or {}
                        left = sl.get("sleep_in_s") if isinstance(sl, dict) and sl.get("pending") not in (None, "none") else None
                        if left is not None and left <= a.sleep_lead_s:
                            break
                        time.sleep(2)
                    armed = left is not None and left <= a.sleep_lead_s
                    check("U5 a sleep countdown runs at %.2f V and is %s s from its end when the upload starts" % (v_parked, left),
                          armed, json.dumps(sl)[:160])
                    if armed:
                        t_u = time.time()
                        upload(out2)                 # about 40 s: the countdown runs out in its middle
                        print("the upload took %.0f s; the countdown had %s s left when it began" % (time.time() - t_u, left),
                              flush=True)
                finally:
                    psu.set_voltage(v_awake)         # the car is on again, whatever happened
                if out2:
                    check("U5 the upload ends well although the countdown ran out in its middle",
                          '"ok":true' in out2[0] and '"reboot":true' in out2[0], out2[0][:120])
                    time.sleep(8)
                    st_s = wait_up(150)
                    rb_s = rollback()
                    rec = this_boot()
                    check("U5 the unit restarted into the image (the restart is the upload's, not a wake from sleep)",
                          bool(st_s) and rb_s.get("running") not in (None, rb_a.get("running"))
                          and rb_s.get("ota_state") == "valid" and rec.get("planned_reason") == "ota_apply",
                          {"running": (rb_a.get("running"), rb_s.get("running")), "ota_state": rb_s.get("ota_state"),
                           "restart": (rec.get("reason"), rec.get("planned_reason"))})
    finally:
        subprocess.run(["ssh", "-o", "BatchMode=yes", a.pi, "rm -f /tmp/upload_confirm.bin"], timeout=30)

    st2 = wait_up(30) or {}
    check("U6 no unexpected reset, no E line",
          st2.get("unexpected_resets") == (0 if cut else unexp0)      # a power cut starts the count again
          and (st2.get("health") or {}).get("log_errors") == 0 and (st1.get("health") or {}).get("log_errors") == 0,
          {"unexpected": (unexp0, st2.get("unexpected_resets")),
           "log_errors": ((st1.get("health") or {}).get("log_errors"), (st2.get("health") or {}).get("log_errors"))})

    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t0), flush=True)
    if fails:
        print("UPLOAD CONFIRM FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("UPLOAD CONFIRM PASS")


if __name__ == "__main__":
    main()
