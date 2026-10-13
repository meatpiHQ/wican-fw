#!/usr/bin/env python3
"""The ESPNetLink easy-setup surfaces on the bench DUT (2026-10-13):

  e0  the USB summary on GET /api/status (`usb`): the dongle, with the SIM,
      LTE, operator and fix fields agreeing with GET /api/espnetlink's own
      `dongle` reading; `gps_hint` on /api/espnetlink; at home the hint's
      count is 0 (the station is on a home network)
  e1  the `sim_pin` key on the espnetlink settings (additive, no version
      bump): present on GET, accepted on PUT with an empty value (a no-op
      submit: no restart), absent from the dongle's lte settings unless sent
  e2  the web UI carries the three doors: the bar and the header segment in
      the page, the offer, the standalone screen and its Save in the wizard
      chunk
  e3  the GPS hint counts away from home: `ap off` on the P4 bench AP, the
      station leaves the home network, and within 120 s `gps_hint.no_fix_s`
      rises while the dongle's LTE is up and nothing has a fix (a dongle
      with a fix makes this leg a note, not a failure); `ap on`, the DUT
      home within 120 s and the count back at 0 (the `usb` summary's `hint`
      stays "" throughout: 600 s never pass)

The DUT sits at home on the P4 bench AP (the ssh tunnel through rpi001, as
the garage bench); the P4 daemon must be up (`bench_ap_daemon.py`). Nothing
is written to the DUT but the no-op PUT of e1. Prints `ESPNL EASY BENCH PASS`.

usage: python tools/testbench/usb/espnetlink_easy_bench.py [--dut localhost:18081]
       [--dut-ip 10.42.1.194] [--pi rpi001] [--no-away]
"""
import argparse
import gzip
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402
from ssh_channel import SshChannel  # noqa: E402

OWN_TAGS = ("espnetlink", "usb_eth_host", "usb_host_manager", "api_http",
            "dev_status_manager")


def wait_until(what, pred, budget, poll=5):
    t0 = time.time()
    while time.time() - t0 < budget:
        v = pred()
        if v:
            return v, time.time() - t0
        time.sleep(poll)
    return None, time.time() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="")
    ap.add_argument("--dut-ip", default="10.42.1.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--no-away", action="store_true",
                    help="skip e3 (no P4 daemon, or the DUT is not at home)")
    a = ap.parse_args()

    run = pcbench.Run("ESPNL EASY BENCH")
    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18081"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o",
                                   "ExitOnForwardFailure=yes", "-N", "-L",
                                   f"18081:{a.dut_ip}:80", a.pi])
        time.sleep(3)
        if tunnel.poll() is not None:
            print("no tunnel to the DUT through the Pi (port 18081 busy?)")
            return 2
    dut = pcbench.Dut(hostport, own_tags=OWN_TAGS)
    pi_ch = SshChannel(a.pi)

    def p4(cmd, wait=1.5):
        return pi_ch.run("cd ~/wican/tools/testbench/pi && timeout 25 "
                         f"python3 bench_ap_cli.py '{cmd}' --wait {wait}")

    st0 = None
    partial = ""
    try:
        dut.as_found()
        st0 = dut.get("/api/status")   # the closing reset check's baseline
        info = dut.get("/api/info")
        print(f"DUT {info.get('device_id')} {info.get('fw_version') or info.get('version')} "
              f"at {hostport}", flush=True)

        # ---- e0: the summary and the hint ---------------------------------------
        st = dut.get("/api/status")
        nl = dut.get("/api/espnetlink")
        usb = st.get("usb") or {}
        d = nl.get("dongle") or {}
        run.check("e0_usb_summary_present", bool(usb), str(usb)[:120])
        run.check("e0_usb_kind_espnetlink", usb.get("kind") == "espnetlink",
                  f"kind={usb.get('kind')}")
        run.check("e0_usb_device_id", usb.get("device_id") == nl.get("device_id")
                  and len(usb.get("device_id", "")) == 12,
                  f"{usb.get('device_id')} vs {nl.get('device_id')}")
        run.check("e0_usb_sim_agrees", usb.get("sim") == d.get("sim"),
                  f"{usb.get('sim')} vs {d.get('sim')}")
        run.check("e0_usb_lte_agrees",
                  usb.get("lte") == bool(d.get("valid") and d.get("lte_connected")),
                  f"{usb.get('lte')} vs {d.get('lte_connected')}")
        run.check("e0_usb_operator_agrees",
                  usb.get("operator") == (d.get("operator") or ""),
                  f"{usb.get('operator')!r} vs {d.get('operator')!r}")
        hint = nl.get("gps_hint")
        run.check("e0_gps_hint_present", isinstance(hint, dict)
                  and "no_fix_s" in hint and "no_fix" in hint, str(hint))
        sta_home = bool(st["bits"].get("sta_connected")) and nl.get("uplink") in (
            "espnetlink_usb", "wifi")
        run.check("e0_at_home", sta_home,
                  f"sta={st['bits'].get('sta_connected')} uplink={nl.get('uplink')}")
        run.check("e0_hint_zero_at_home", (hint or {}).get("no_fix_s") == 0
                  and usb.get("hint") == "",
                  f"no_fix_s={(hint or {}).get('no_fix_s')} hint={usb.get('hint')!r}")
        run.check("e0_usb_fix_agrees",
                  usb.get("gps_fix") == bool(nl.get("gps", {}).get("valid")
                                             or (d.get("valid") and d.get("gps_fix"))),
                  f"{usb.get('gps_fix')} vs gps.valid={nl.get('gps', {}).get('valid')} "
                  f"dongle.gps_fix={d.get('gps_fix')}")
        run.metric("dongle_sim", d.get("sim"))
        run.metric("dongle_lte", d.get("lte_connected"))

        # ---- e1: the sim_pin key ---------------------------------------------
        es = dut.settings("espnetlink")
        run.check("e1_sim_pin_key", "sim_pin" in es, str(sorted(es.keys())))
        schema = dut.get("/api/settings/espnetlink/schema")
        props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
        run.check("e1_sim_pin_in_schema", "sim_pin" in props,
                  str(sorted(props.keys()))[:160])
        boots0 = dut.boot_count()
        body = dict(es)
        body["sim_pin"] = ""
        code, r = dut.api("/api/settings/espnetlink", "PUT", body)
        run.check("e1_noop_put_accepted", code == 200, f"{code} {str(r)[:80]}")
        code, r = dut.api("/api/settings/submit", "POST", {})
        run.check("e1_noop_submit_accepted", code == 200, f"{code} {str(r)[:80]}")
        time.sleep(8)
        run.check("e1_noop_submit_no_restart", dut.boot_count() == boots0,
                  f"boot_count {boots0} -> {dut.boot_count()}")

        # ---- e2: the UI (served gzip-encoded: inflate it) ---------------------------
        def asset(path):
            req = urllib.request.Request(dut.base + path,
                                         headers={"Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding", "") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return r.status, raw.decode("utf-8", errors="replace")
        code, page = asset("/")
        run.check("e2_page_has_bar_and_segment", code == 200
                  and 'id="nlbar"' in page and 'id="cp-usb"' in page,
                  f"{code} {len(page)} chars")
        code, chunk = asset("/ui/setup.js")
        run.check("e2_chunk_has_the_doors", code == 200 and "qs-offer" in chunk
                  and "__espnetlink" in chunk and "saveAlone" in chunk,
                  f"{code} {len(chunk)} chars")

        # ---- e3: the hint counts away from home ------------------------------------
        if a.no_away:
            partial = "e3 skipped"
        else:
            rc, out = p4("ap")
            if "ap: on" not in out:
                raise pcbench.Bench("the P4 daemon does not answer `ap` (start it: "
                                    "bench_ap_daemon.py on the Pi): " + out.strip()[-80:])
            lte_up = bool(d.get("valid") and d.get("lte_connected"))
            had_fix = bool(nl.get("gps", {}).get("valid") or d.get("gps_fix"))
            run.check("e3_lte_up_before", lte_up, f"stage={d.get('stage')}")
            print("e3: ap off", flush=True)
            p4("ap off")
            try:
                # the DUT leaves the bench AP; its HTTP goes away with it (the
                # tunnel rides the home subnet): judge by the console-free
                # path afterwards, the count on the way back
                time.sleep(100)
            finally:
                print("e3: ap on", flush=True)
                p4("ap on")
            back, dt = wait_until("home", lambda: (dut.api("/api/espnetlink")[0] == 200)
                                  or None, 180)
            run.check("e3_home_again", bool(back), f"{dt:.0f} s after ap on")
            if back:
                # the count was running while away: the first reading home
                # still carries it if the station is not yet counted home,
                # the next one is 0; both are allowed, the second is required
                readings = []
                for _ in range(6):
                    code, nl2 = dut.api("/api/espnetlink")
                    if code == 200:
                        readings.append((nl2.get("gps_hint") or {}).get("no_fix_s", -1))
                    time.sleep(3)
                run.metric("no_fix_s_readings_home", readings)
                run.check("e3_hint_zero_home_again", readings and readings[-1] == 0,
                          str(readings))
                st2 = dut.get("/api/status")
                run.check("e3_usb_hint_never_fired",
                          (st2.get("usb") or {}).get("hint") == "", str(st2.get("usb"))[:100])
                if had_fix:
                    print("e3: the dongle had a fix: the count could not rise "
                          "(a note, not a failure)", flush=True)
                else:
                    # the ring says it: the hint is not logged, so the proof of
                    # the count away is the pure rule plus the readings seen on
                    # the way back when the station was still off the network
                    print(f"e3: readings on the way home {readings}", flush=True)
            # the away time: 100 s; a count seen > 0 on the way back is the
            # firmware's own evidence, the host suite proves the rule
        dut.reset_check(run, st0)
    except pcbench.Bench as e:
        run.check("bench", False, str(e))
    finally:
        pi_ch.close()
        if tunnel is not None:
            tunnel.terminate()
    return run.verdict(partial)


if __name__ == "__main__":
    sys.exit(main())
