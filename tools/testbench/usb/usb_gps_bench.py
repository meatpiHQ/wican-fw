#!/usr/bin/env python3
"""A USB GPS receiver is read by default (issue #978, 2026-10-11): the bench.

The claim: a plain USB GNSS receiver on the connector (the bench u-blox 7,
1546:01a7, NMEA at 1 Hz) is read from the moment the device boots, whatever
the ESPNetLink console switch says; its fix reaches /api/gps, the autopid
gps_ parameters and the Home Assistant push; the one way it is not read is
the USB GPS receiver switch (usb_acm_cli.gps) under USB > Settings.

Rig: the DUT on the bench access point, reached over HTTP through an ssh
tunnel on the Pi; the receiver on the USB connector (near a window if the
fix legs are to pass: without a view of the sky the bench still proves the
reading and reports the no-fix state as a WARN, not a FAIL); the Pi for the
Home Assistant receiver (the gate bench's, on the Pi at --pi-ip:--port).

Legs:
  g0  as found: settings v2 (`gps`, `console`, no `enabled`: the migration
      ran on a unit that stored v1), the receiver on /api/usb
  g1  the fresh install: console off (one planned restart when it was on),
      gps on: within 20 s of boot /api/usb/acm reads `reading true, mode
      nmea, gps true, console false`, the sentences grow over 5 s, the log
      ring has the NMEA line of this boot, POST /api/usb/acm/cmd answers 409
      with the console-off sentence
  g2  the fix: /api/gps valid within --fix-wait (default 120 s): the fields,
      the six gps_ parameters in /api/autopid, the HA push's `gps` block and
      `autopid_data.gps_latitude` at the Pi receiver (the registration put
      back as found); without a fix: WARN, the no-fix state checked instead
  g3  the switch off (one restart): `gps false, reading false` (the console
      is off too), mode console, /api/gps valid false, no gps_ parameter
  g4  back on (one restart): read again within 20 s
  g5  restore: the console as found (one restart when it was on), planned
      restarts only, no E line of the USB tags, the RX task's headroom, the
      internal heap floor

  python tools/testbench/usb/usb_gps_bench.py --dut localhost:18081 --pi rpi001
      [--pi-ip 10.42.1.1] [--port 8197] [--fix-wait 120] [--only g1,g3]
      [--no-ha] [--logdir DIR]

Prints `USB GPS PASS` (or `USB GPS PARTIAL PASS (legs: ...)` with --only).
"""
import argparse
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", "ha"))
import pcbench  # noqa: E402

OWN_TAGS = ("usb_acm_cli", "usb_host_manager", "usb_eth_host")
LEGS = ("g1", "g2", "g3", "g4")
GPS_PARAMS = ("gps_latitude", "gps_longitude", "gps_altitude", "gps_speed",
              "gps_heading", "gps_satellites")


def is_receiver(usb):
    dev = (usb or {}).get("device") or {}
    vid = str(dev.get("vid") or "").lower()
    name = str(dev.get("product") or "")
    return vid == "1546" or "gnss" in name.lower() or "gps" in name.lower()


def wait_acm(dut, want, timeout):
    """Poll /api/usb/acm until `want(acm)` holds; (acm, seconds)."""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        code, a = dut.api("/api/usb/acm", timeout=5)
        if code == 200 and isinstance(a, dict):
            last = a
            if want(a):
                return a, time.time() - t0
        time.sleep(1)
    return last, time.time() - t0


def gps_params(dut):
    code, ap = dut.api("/api/autopid", timeout=8)
    if code != 200 or not isinstance(ap, dict):
        return {}
    return {p.get("name"): p.get("value") for p in ap.get("params", [])
            if str(p.get("name", "")).startswith("gps_")}


def withheld(d):
    """A fix or a parameter set for the log: the bench's position is nobody's
    business (the owner's rule, 2026-10-11): latitude and longitude read
    `withheld`, the rest is printed as it is."""
    if not isinstance(d, dict):
        return d
    out = dict(d)
    for k in ("latitude", "longitude", "gps_latitude", "gps_longitude", "lat", "lon"):
        if k in out:
            out[k] = "withheld"
    return out


def ha_leg(dut, run, a):
    """The Home Assistant push carries the gps block while a fix exists: a
    receiver on the Pi (the gate bench's), the registration put back after."""
    import ha_webhook_gate_bench as gate
    gate.DUTS[:] = [a.dut]
    gate.DUT = a.dut
    args = types.SimpleNamespace(receiver=None, serve=None, local_ip=None,
                                 pi=a.pi, pi_ip=a.pi_ip, port=a.port)
    code, saved = dut.api("/api/webhook")
    saved = saved if code == 200 and isinstance(saved, dict) else {}
    base, dump = gate.start_receiver(args)
    try:
        code, r = dut.api("/api/webhook", "POST",
                          {"url": base + "/api/webhook/usb_gps_bench",
                           "enabled": True, "interval": 5,
                           "manual_override": False})
        run.check("g2_ha_registration_accepted", code in (200, 201), f"{code} {r}")
        pushes = []
        t0 = time.time()
        while time.time() - t0 < 45 and len(pushes) < 2:
            time.sleep(3)
            pushes = gate.received(dump)
        gps_blocks = [p.get("gps") for p in pushes if isinstance(p.get("gps"), dict)]
        run.check("g2_ha_push_has_gps_block", bool(gps_blocks),
                  f"{len(pushes)} pushes in {time.time() - t0:.0f} s")
        if gps_blocks:
            g = gps_blocks[0]
            run.check("g2_ha_gps_block_fields",
                      all(k in g for k in ("latitude", "longitude", "accuracy",
                                           "altitude", "speed", "heading",
                                           "satellites")), str(withheld(g)))
        lat = [p for p in pushes if isinstance(p.get("autopid_data"), dict)
               and "gps_latitude" in p["autopid_data"]]
        run.check("g2_ha_autopid_data_has_gps_latitude", bool(lat))
    finally:
        gate.stop_receiver(args)
        if saved.get("url"):
            body = {k: saved[k] for k in ("url", "urls", "enabled", "interval",
                                          "manual_override") if k in saved}
            dut.api("/api/webhook", "POST", body)
        else:
            dut.api("/api/webhook", "DELETE")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081",
                    help="host:port of the DUT's API (a tunnel on the Pi)")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--pi-ip", default="10.42.1.1",
                    help="the Pi's address on the DUT's network (the HA receiver)")
    ap.add_argument("--port", type=int, default=8197,
                    help="the HA receiver's port on the Pi")
    ap.add_argument("--fix-wait", type=int, default=120,
                    help="seconds to wait for a fix (the receiver needs the sky)")
    ap.add_argument("--no-ha", action="store_true",
                    help="skip the Home Assistant push leg")
    ap.add_argument("--only", default="", help="comma list of legs")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS if not only or l in only]
    run = pcbench.Run("USB GPS")
    dut = pcbench.Dut(a.dut, own_tags=OWN_TAGS, tasks=("usb_acm_rx",))
    found = {}
    exit_code = 1
    try:
        # ---- g0: as found ------------------------------------------------------
        info = dut.get("/api/info")
        st0 = dut.get("/api/status")
        dut.as_found()
        print(f"DUT {info.get('device_id')} {info.get('fw_version')}", flush=True)
        found["acm"] = dut.settings("usb_acm_cli")
        run.check("g0_settings_v2_keys", "gps" in found["acm"] and "console" in found["acm"]
                  and "enabled" not in found["acm"], str(found["acm"]))
        usb = dut.get("/api/usb")
        run.check("g0_receiver_on_the_connector", is_receiver(usb),
                  str((usb or {}).get("device")))
        if not is_receiver(usb):
            raise pcbench.Bench("no GPS receiver on the USB connector (plug the "
                                "u-blox in; the ESPNetLink must be off the cable)")
        print(f"as found: {found['acm']}", flush=True)

        # ---- g1: the fresh install: console off, gps on ------------------------
        if "g1" in legs:
            dut.restart({"usb_acm_cli": {"gps": True, "console": False}})
            acm, took = wait_acm(dut, lambda x: x.get("mode") == "nmea"
                                 and x.get("reading") is True, 20)
            run.check("g1_read_as_nmea_within_20s", bool(acm) and acm.get("mode") == "nmea"
                      and acm.get("reading") is True, f"{acm} after {took:.1f} s")
            run.metric("g1_first_nmea_s", round(took, 1), "s")
            run.check("g1_switches_as_applied", bool(acm) and acm.get("gps") is True
                      and acm.get("console") is False, str(acm))
            n0 = (acm or {}).get("nmea_sentences", 0)
            time.sleep(5)
            code, acm2 = dut.api("/api/usb/acm")
            n1 = (acm2 or {}).get("nmea_sentences", 0) if code == 200 else 0
            run.check("g1_sentences_grow_over_5s", n1 > n0, f"{n0} -> {n1}")
            run.metric("g1_sentences_per_s", round((n1 - n0) / 5.0, 1), "/s")
            lines = [ln for ln in dut.ring_lines() if "NMEA on the console" in ln]
            run.check("g1_log_line_nmea_this_boot", bool(lines), lines[-1:] )
            code, r = dut.api("/api/usb/acm/cmd", "POST", {"cmd": "ver", "timeout_ms": 500})
            run.check("g1_cmd_refused_409_console_off", code == 409 and isinstance(r, dict)
                      and "console is off" in str(r.get("error", "")), f"{code} {r}")

        # ---- g2: the fix --------------------------------------------------------
        if "g2" in legs:
            t0 = time.time()
            g = None
            while time.time() - t0 < a.fix_wait:
                code, g = dut.api("/api/gps", timeout=5)
                if code == 200 and isinstance(g, dict) and g.get("valid"):
                    break
                time.sleep(2)
            fixed = bool(g and g.get("valid"))
            run.metric("g2_fix_after_s", round(time.time() - t0, 1), "s")
            if fixed:
                run.check("g2_fix_fields", all(k in g for k in (
                    "latitude", "longitude", "accuracy", "altitude", "speed",
                    "heading", "satellites", "age_ms")) and g["satellites"] > 0,
                    str(withheld(g)))
                run.metric("g2_satellites", g.get("satellites"))
                run.metric("g2_accuracy_m", g.get("accuracy"), "m")
                time.sleep(3)
                gp = gps_params(dut)
                run.check("g2_gps_params_in_autopid", all(k in gp and gp[k] is not None
                                                           for k in GPS_PARAMS),
                          str(withheld(gp)))
                if not a.no_ha:
                    ha_leg(dut, run, a)
                else:
                    print("g2: the HA leg skipped (--no-ha)", flush=True)
            else:
                print(f"WARN g2: no fix within {a.fix_wait} s (the receiver has no view "
                      "of the sky where it sits?): the no-fix state is checked instead",
                      flush=True)
                code, acm = dut.api("/api/usb/acm")
                run.check("g2_no_fix_state", code == 200 and isinstance(g, dict)
                          and g.get("valid") is False and acm.get("mode") == "nmea"
                          and acm.get("reading") is True, f"{g} {acm}")
                run.check("g2_no_gps_params_without_a_fix", not gps_params(dut),
                          str(gps_params(dut)))
                print("SKIP g2: the HA leg needs a fix", flush=True)

        # ---- g3: the switch off: ignored ----------------------------------------
        if "g3" in legs:
            dut.restart({"usb_acm_cli": {"gps": False, "console": False}})
            time.sleep(12)
            acm, _ = wait_acm(dut, lambda x: x.get("connected") is True, 20)
            run.check("g3_off_bound_but_not_read", bool(acm) and acm.get("connected") is True
                      and acm.get("reading") is False and acm.get("gps") is False
                      and acm.get("mode") == "console"
                      and acm.get("nmea_sentences", 0) == 0, str(acm))
            code, g = dut.api("/api/gps")
            run.check("g3_off_no_fix", code == 200 and isinstance(g, dict)
                      and g.get("valid") is False, str(g))
            run.check("g3_off_no_gps_params", not gps_params(dut), str(gps_params(dut)))
            lines = [ln for ln in dut.ring_lines()
                     if "disabled by settings (gps off, console off)" in ln]
            run.check("g3_off_log_line", bool(lines), lines[-1:])

        # ---- g4: back on --------------------------------------------------------
        if "g4" in legs:
            dut.restart({"usb_acm_cli": {"gps": True, "console": False}})
            acm, took = wait_acm(dut, lambda x: x.get("mode") == "nmea"
                                 and x.get("reading") is True, 20)
            run.check("g4_read_again_within_20s", bool(acm) and acm.get("mode") == "nmea",
                      f"{acm} after {took:.1f} s")

        # ---- g5: restore ----------------------------------------------------------
        cur = dut.settings("usb_acm_cli")
        want = {k: found["acm"][k] for k in ("gps", "console") if k in found["acm"]}
        if any(cur.get(k) != v for k, v in want.items()):
            dut.restart({"usb_acm_cli": want})
            time.sleep(8)
        back = dut.settings("usb_acm_cli")
        run.check("g5_settings_as_found", all(back.get(k) == v for k, v in want.items()),
                  f"{back} vs {want}")
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        exit_code = run.verdict(partial=("legs: " + " ".join(legs)) if only else "")
    except pcbench.Bench as e:
        print(f"BENCH: {e}", flush=True)
        print("USB GPS FAIL: bench", flush=True)
        exit_code = 2
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
