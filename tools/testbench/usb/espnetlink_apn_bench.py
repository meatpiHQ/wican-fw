#!/usr/bin/env python3
"""The carrier settings relay in the USB modes (2026-10-28):

  a0  as found: the WiCAN in a USB mode (`espnetlink.mode` usb_rndis or
      usb_ncm) with the dongle paired over the wire (`pair_state` ncm_up) and
      the ACM console connected; the WiCAN's `apn` / `apn_user`, the dongle's
      own APN (`config get APN` through the ACM bridge), the dongle's boot
      count and unexpected resets (its `restart_tracker`), its LTE state
  a1  the relay: a bench APN set on the WiCAN the product way
      (`PUT /api/settings/espnetlink` + submit = one planned WiCAN restart);
      the pairing pass after the restart stages it on the dongle and submits
      it with the share pass (`carrier settings staged on the dongle (APN
      '...')` and `dongle USB uplink reconfigured (..., the carrier settings)`
      in the log), the dongle restarts ONCE (planned, its boot count +1), the
      WiCAN identifies it again and finds nothing to write (`already
      configured`), `carrier_synced` true, and the dongle's `config get APN`
      reads the bench APN
  a2  idempotence: a plain WiCAN restart; the pass finds the dongle holding
      the APN: no carrier line, no dongle restart (its boot count unchanged),
      so no restart loop
  a3  restore: the WiCAN's APN back as found (when the WiCAN carried none,
      the dongle's own as-found APN goes through the relay first and the
      WiCAN's "" follows: a WiCAN without an APN leaves the dongle's alone),
      the dongle restarted once more by the relay and its APN read back as
      found; the dongle's LTE back up within 240 s when it was up as found

Closing: no unexpected reset on either side, no E line of the bench's own
tags, the internal heap floor. Prints `ESPNL APN RELAY PASS`.

The SIM cannot attach on the bench APN, so the dongle's LTE is down for the
minutes it holds it: run the LTE benches before or after, not alongside.
Before the fix (6.0.0-beta.5 and earlier) a1 fails: the USB-mode pass went
from identify straight to the share pass and never relayed the carrier
settings, so the WiCAN kept the APN and reported `carrier_synced` while the
dongle ran its own (bench 2026-10-28, a Simbase SIM on the default APN).

The DUT sits at home on the bench AP (an ssh tunnel through rpi001 unless
--dut names an address).

usage: python tools/testbench/usb/espnetlink_apn_bench.py [--dut localhost:18081]
       [--dut-ip 10.42.1.194] [--pi rpi001] [--apn wican.bench]
"""
import argparse
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402

OWN_TAGS = ("espnetlink", "usb_eth_host", "usb_host_manager", "api_http",
            "settings_manager")
RELAY_BUDGET_S = 180      # WiCAN boot + the pass + the dongle's restart + re-pair
REPAIR_BUDGET_S = 120     # a WiCAN restart with nothing to write
LTE_BACK_S = 240          # the modem's registration after the dongle's restart


def wait_until(pred, budget, poll=3):
    """(value, seconds) once pred() is truthy, (None, budget) otherwise."""
    t0 = time.time()
    while time.time() - t0 < budget:
        v = pred()
        if v:
            return v, round(time.time() - t0, 1)
        time.sleep(poll)
    return None, round(time.time() - t0, 1)


class Dongle:
    """The dongle's own view through the WiCAN's ACM bridge
    (POST /api/usb/acm/cmd, the dongle's console; busy while a WiCAN poll
    waits on it, gone while the dongle restarts: every read retries)."""

    def __init__(self, dut):
        self.dut = dut

    def cmd(self, text, timeout_ms=5000, tries=8):
        for _ in range(tries):
            code, r = self.dut.api("/api/usb/acm/cmd", "POST",
                                   {"cmd": text, "timeout_ms": timeout_ms},
                                   timeout=timeout_ms / 1000 + 6)
            if code == 200 and isinstance(r, dict) and r.get("ok"):
                return r.get("response", "")
            time.sleep(2)
        return None

    def apn(self):
        r = self.cmd("config get APN")
        m = re.search(r"APN\s*=\s*(\S*)", r or "")
        return m.group(1) if m else None

    def tracker(self):
        """(boots, unexpected) from `restart_tracker`, or None."""
        r = self.cmd("restart_tracker")
        m = re.search(r"Boots:\s*(\d+),\s*unexpected resets:\s*(\d+)", r or "")
        return (int(m.group(1)), int(m.group(2))) if m else None


def espnl(dut):
    code, st = dut.api("/api/espnetlink")
    return st if code == 200 and isinstance(st, dict) else {}


def new_lines(dut, seen):
    return [ln for ln in dut.ring_lines() if ln not in seen]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="")
    ap.add_argument("--dut-ip", default="10.42.1.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--apn", default="wican.bench",
                    help="the APN the bench relays (one the SIM cannot use)")
    a = ap.parse_args()

    run = pcbench.Run("ESPNL APN RELAY")
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
    dongle = Dongle(dut)
    rc = 1
    try:
        rc = legs(run, dut, dongle, a.apn)
    except pcbench.Bench as e:
        print(f"BENCH: {e}")
        run.check("bench_completed", False, str(e)[:200])
        rc = run.verdict()
    finally:
        if tunnel is not None:
            tunnel.terminate()
    return rc


def relay_landed(dut, dongle, apn, boots_before):
    """The pass after a WiCAN restart relayed `apn`: the dongle restarted
    once, the pair is up again and both sides hold the APN."""
    st = espnl(dut)
    if st.get("usb", {}).get("pair_state") != "ncm_up":
        return None
    if st.get("apn") != apn or st.get("carrier_synced") is not True:
        return None
    t = dongle.tracker()
    if t is None or t[0] != boots_before + 1:
        return None
    return {"status": st, "tracker": t}


def legs(run, dut, dongle, bench_apn):
    # ---- a0: as found ------------------------------------------------------
    st0 = dut.get("/api/status")
    dut.as_found()
    es0 = espnl(dut)
    cfg0 = dut.settings("espnetlink")
    print("as found:", {k: es0.get(k) for k in ("mode", "apn", "carrier_synced",
                                                   "dongle_fw", "dongle_api")},
          es0.get("usb"), flush=True)
    usb_mode = es0.get("mode") in ("usb_rndis", "usb_ncm")
    run.check("a0_usb_mode", usb_mode, f"mode {es0.get('mode')}")
    run.check("a0_paired_over_the_wire",
              es0.get("usb", {}).get("pair_state") == "ncm_up",
              str(es0.get("usb")))
    code, acm = dut.api("/api/usb/acm")
    run.check("a0_acm_console", code == 200 and isinstance(acm, dict)
              and acm.get("connected") is True and acm.get("console") is True,
              str(acm)[:120])
    if not usb_mode or es0.get("usb", {}).get("pair_state") != "ncm_up":
        raise pcbench.Bench("the bench needs the dongle paired over the wire "
                            "in a USB mode (nothing was changed)")
    dongle_apn0 = dongle.apn()
    trk0 = dongle.tracker()
    run.check("a0_dongle_console_answers", dongle_apn0 is not None
              and trk0 is not None,
              f"APN {dongle_apn0!r}, tracker {trk0}")
    if dongle_apn0 is None or trk0 is None:
        raise pcbench.Bench("the dongle's console does not answer through "
                            "the bridge (nothing was changed)")
    wican_apn0 = cfg0.get("apn", "")
    wican_user0 = cfg0.get("apn_user", "")
    lte0 = bool(es0.get("dongle", {}).get("lte_connected"))
    print(f"as found: WiCAN apn {wican_apn0!r} user {wican_user0!r}; dongle "
          f"APN {dongle_apn0!r}, boots {trk0[0]}, unexpected {trk0[1]}, "
          f"LTE {'up' if lte0 else 'down'} "
          f"(stage {es0.get('dongle', {}).get('stage')})", flush=True)
    # the as-found pair must agree when the WiCAN carries an APN: a
    # disagreement here is the defect this bench exists for
    if wican_apn0:
        run.check("a0_as_found_dongle_holds_the_wican_apn",
                  dongle_apn0 == wican_apn0,
                  f"WiCAN {wican_apn0!r} vs dongle {dongle_apn0!r}")
    if bench_apn in (wican_apn0, dongle_apn0):
        raise pcbench.Bench(f"the bench APN {bench_apn!r} is already on a "
                            "device: pass another --apn")

    # ---- a1: the relay through the share pass ------------------------------
    seen = set(dut.ring_lines())
    t_restart = dut.restart({"espnetlink": {"apn": bench_apn}})
    run.check("a1_wican_restarted_for_the_setting", t_restart is not None)
    landed, secs = wait_until(
        lambda: relay_landed(dut, dongle, bench_apn, trk0[0]),
        RELAY_BUDGET_S, poll=4)
    run.check("a1_relay_landed_one_dongle_restart", landed is not None,
              f"{secs} s" if landed else
              f"after {secs} s: {espnl(dut).get('usb')} "
              f"carrier_synced {espnl(dut).get('carrier_synced')} "
              f"tracker {dongle.tracker()}")
    if landed:
        run.metric("relay_s", secs, "s")
    lines = new_lines(dut, seen)
    staged = [ln for ln in lines if "carrier settings" in ln
              and bench_apn in ln]
    reconf = [ln for ln in lines if "dongle USB uplink reconfigured" in ln
              and "the carrier settings" in ln]
    again = [ln for ln in lines if "dongle USB uplink already configured" in ln]
    run.check("a1_log_carrier_staged", bool(staged),
              staged[0][:160] if staged else "no carrier line in the log")
    run.check("a1_log_one_submit_with_the_share_pass", len(reconf) == 1,
              f"{len(reconf)} reconfigured lines: "
              + " | ".join(x[:120] for x in reconf))
    run.check("a1_log_nothing_left_to_write_after_the_restart", bool(again),
              again[-1][:120] if again else "no 'already configured' line")
    dongle_apn1 = dongle.apn()
    run.check("a1_dongle_holds_the_bench_apn", dongle_apn1 == bench_apn,
              f"dongle APN {dongle_apn1!r}")
    trk1 = dongle.tracker() or trk0
    run.check("a1_dongle_restart_planned", trk1[1] == trk0[1],
              f"unexpected {trk0[1]} -> {trk1[1]}, boots {trk0[0]} -> {trk1[0]}")
    if landed is None:
        print("the relay did not land: restoring and stopping here",
              flush=True)
        restore(run, dut, dongle, wican_apn0, wican_user0, dongle_apn0,
                dongle.tracker() or trk1, lte0)
        dut.reset_check(run, st0)
        dut.passing_checks(run)
        return run.verdict()

    # ---- a2: a restart with nothing to write ---------------------------------
    seen = set(dut.ring_lines())
    dut.restart()
    up, secs = wait_until(
        lambda: espnl(dut).get("usb", {}).get("pair_state") == "ncm_up"
        and espnl(dut).get("carrier_synced") is True,
        REPAIR_BUDGET_S, poll=4)
    run.check("a2_paired_again_after_a_plain_restart", up is not None,
              f"{secs} s")
    time.sleep(8)       # a would-be second relay shows within seconds of ncm_up
    trk2 = dongle.tracker()
    run.check("a2_no_dongle_restart", trk2 is not None and trk2[0] == trk1[0],
              f"boots {trk1[0]} -> {trk2[0] if trk2 else None}")
    lines = new_lines(dut, seen)
    run.check("a2_no_carrier_line",
              not [ln for ln in lines if "carrier settings" in ln],
              " | ".join(ln[:100] for ln in lines if "carrier settings" in ln))
    run.check("a2_log_nothing_to_write", any(
        "dongle USB uplink already configured" in ln for ln in lines))
    run.check("a2_dongle_still_holds_the_bench_apn",
              dongle.apn() == bench_apn, f"dongle APN {dongle.apn()!r}")

    # ---- a3: restore as found --------------------------------------------------
    restore(run, dut, dongle, wican_apn0, wican_user0, dongle_apn0,
            trk2 or trk1, lte0)

    # ---- closing -----------------------------------------------------------------
    trk_end = dongle.tracker()
    run.check("dongle_no_unexpected_reset",
              trk_end is not None and trk_end[1] == trk0[1],
              f"dongle tracker {trk0} -> {trk_end}")
    dut.reset_check(run, st0)
    dut.passing_checks(run)
    return run.verdict()


def restore(run, dut, dongle, wican_apn0, wican_user0, dongle_apn0, trk,
            lte0):
    """The APN back as found on both sides, through the relay."""
    boots = trk[0]
    if wican_apn0 or wican_user0:
        dut.restart({"espnetlink": {"apn": wican_apn0,
                                    "apn_user": wican_user0}})
        landed, secs = wait_until(
            lambda: relay_landed(dut, dongle, wican_apn0, boots),
            RELAY_BUDGET_S, poll=4)
        run.check("a3_wican_apn_restored_through_the_relay", landed is not None,
                  f"{secs} s, dongle APN {dongle.apn()!r}")
    else:
        # the WiCAN carried none: hand the dongle its own APN back through
        # the relay, then take the WiCAN's APN away again (nothing relayed)
        dut.restart({"espnetlink": {"apn": dongle_apn0}})
        landed, secs = wait_until(
            lambda: relay_landed(dut, dongle, dongle_apn0, boots),
            RELAY_BUDGET_S, poll=4)
        run.check("a3_dongle_apn_restored_through_the_relay",
                  landed is not None, f"{secs} s, dongle APN {dongle.apn()!r}")
        boots = (dongle.tracker() or (boots + 1, 0))[0]
        dut.restart({"espnetlink": {"apn": ""}})
        up, secs = wait_until(
            lambda: espnl(dut).get("usb", {}).get("pair_state") == "ncm_up"
            and espnl(dut).get("apn") == "",
            REPAIR_BUDGET_S, poll=4)
        time.sleep(8)
        trk3 = dongle.tracker()
        run.check("a3_wican_without_apn_leaves_the_dongle_alone",
                  up is not None and trk3 is not None and trk3[0] == boots,
                  f"{secs} s, boots {boots} -> {trk3[0] if trk3 else None}")
    apn_end = dongle.apn()
    run.check("a3_dongle_apn_as_found", apn_end == dongle_apn0,
              f"dongle APN {apn_end!r} (as found {dongle_apn0!r})")
    cfg = dut.settings("espnetlink")
    run.check("a3_wican_apn_as_found",
              cfg.get("apn") == wican_apn0 and cfg.get("apn_user") == wican_user0,
              f"apn {cfg.get('apn')!r} user {cfg.get('apn_user')!r}")
    if lte0:
        up, secs = wait_until(
            lambda: espnl(dut).get("dongle", {}).get("lte_connected") is True,
            LTE_BACK_S, poll=5)
        run.check("a3_lte_back_up", up is not None,
                  f"{secs} s, stage {espnl(dut).get('dongle', {}).get('stage')}")
        if up:
            run.metric("lte_back_s", secs, "s")
    else:
        print(f"note: the dongle's LTE was down as found (stage "
              f"{espnl(dut).get('dongle', {}).get('stage')}): not judged",
              flush=True)


if __name__ == "__main__":
    sys.exit(main())
