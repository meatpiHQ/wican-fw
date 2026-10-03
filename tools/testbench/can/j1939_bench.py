#!/usr/bin/env python3
"""J1939 listener bench (TASK_j1939_wwh.md, phase 4): the `j1939` component
on the native CAN bus, against a truck played by the PCAN adapter.

The claim under test: with can_manager listening (auto bitrate, silent) the
device reads what a J1939 vehicle broadcasts, value for value and byte for
byte, puts long messages back together, says so when a value is not there,
and NEVER puts a frame on the bus.

The vehicle is actors/pcan_j1939_truck.py (reference codec lib/j1939_ref.py,
itself checked against a J1939 DBC). The ECU simulator stays on the bus as
the ACK source with its own ECU off (a listen-only DUT acknowledges nothing).
Every leg judges observed state: GET /api/j1939 (status, `?pgns=1`,
`?pgn=`), GET /api/can, and the actor's closing statistics (frames it sent,
frames of anybody else it saw, bus errors).

Legs:
  j1  250k, bitrate found by listening: every value of the built-in table
      equals what the truck sends, every stored payload equals the truck's
      bytes, the VIN arrives by BAM, two sources of one group are kept apart
      and the lowest address is the pick; the frame and transport books
      balance; the DUT transmits nothing and the OBD chip is not touched
  j2  DM1: three codes by BAM (lamps, occurrence counts), then one code in
      a frame, then none
  j3  the truck falls silent: ages grow, nothing changes, nothing is
      invented
  j4  "not available" and "error" raw values carry no value
  j5  a BAM with a dropped, a reordered and a missing packet: nothing is
      stored from it, each failure in its counter, the books still balance
  j6  the bus moves to 500k: found again by listening
  j7  can_manager in normal mode (the DUT acknowledges): still not one frame
      from it
  j8  j1939 enabled with the native CAN bus off: state `no_bus`, no error
  restore: every setting and the simulator as found.

Verdict: `J1939 PASS`. Run on the PC (IDF venv python; python-can):
  python j1939_bench.py [host[:port]] [--pcan PCAN_USBBUS2]
                        [--only j1,j4] [--logdir DIR]
`--only` runs chosen legs and says `J1939 PARTIAL PASS`.
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import bench_bus  # noqa: E402
import j1939_ref as J  # noqa: E402
from pcbench import Actor, Bench, Dut, Run  # noqa: E402

DUT_HOST = "localhost:8081"
PCAN = "PCAN_USBBUS2"
ONLY = []
LOGDIR = tempfile.gettempdir()
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--pcan":
        PCAN = args[i + 1]; i += 2
    elif args[i] == "--only":
        ONLY = [x.strip() for x in args[i + 1].split(",") if x.strip()]; i += 2
    elif args[i] == "--logdir":
        LOGDIR = args[i + 1]; i += 2
    else:
        DUT_HOST = args[i]; i += 1

ALL_LEGS = ["j1", "j2", "j3", "j4", "j5", "j6", "j7", "j8"]
VIN = "1WCANJ1939TRUCK01"
TWIN = 0x11
THREE = [(110, 0, 5), (3226, 4, 1), (520192, 31, 126)]
THREE_ARG = "110-0-5,3226-4-1,520192-31-126"

run = Run("J1939")
dut = Dut(DUT_HOST, own_tags=("j1939", "can_manager", "can_core"),
          tasks=("j1939", "can_core_rx"))
truck = Actor(os.path.join(HERE, "..", "actors", "pcan_j1939_truck.py"),
              LOGDIR, "j1939_bench_truck", "TRUCK READY", "TRUCK DONE ")
bus_kbps = [0]                  # where the simulator acknowledges right now


# ---- the rig ---------------------------------------------------------------------

def sim_ack_only(kbps):
    """The simulator as the ACK source at `kbps`, its own ECU off. Its CAN
    controller keeps the bitrate it LAST ran at, so: on at that bitrate
    first, then off."""
    if bus_kbps[0] == kbps:
        return
    bench_bus.sim_set(bitrate=kbps, enabled=True)
    bench_bus.sim_set(enabled=False)
    bus_kbps[0] = kbps


def start_truck(tag, kbps, extra=()):
    truck.start(tag, ["--pcan", PCAN, "--bitrate", str(kbps),
                      "--vin", VIN] + list(extra))


def status():
    return dut.get("/api/j1939")


def values(doc):
    return {v["name"]: v for v in doc.get("values", [])}


def stored():
    """{(pgn hex, sa, da): entry} of everything the listener holds."""
    return {(m["pgn"], m["sa"], m["da"]): m
            for m in dut.get("/api/j1939?pgns=1").get("pgns", [])}


def wait_for(what, secs, fn):
    """Poll fn() until it returns something true. Bench when it never does."""
    end = time.time() + secs
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(0.4)
    raise Bench(f"{what}: not within {secs} s")


def tp_books(tp):
    return tp["started"] == tp["completed"] + tp["seq_errors"] + \
        tp["timeouts"] + tp["aborted"] + tp["replaced"] + tp["open"]


def frame_books(st):
    return st["rx_frames"] == st["rx_data"] + st["rx_tp_cm"] + \
        st["rx_tp_dt"] + st["rx_diag"] + st["rx_foreign"]


def chip_tx():
    code, d = dut.api("/api/obd_chip")
    return d.get("uart", {}).get("tx_bytes") \
        if code == 200 and isinstance(d, dict) else None


def close_enough(got, want, step):
    """Within half a raw step: what encoding to the wire can cost."""
    return isinstance(got, (int, float)) and \
        abs(got - want) <= step / 2 + 1e-6


def all_values_valid(doc):
    v = values(doc)
    return all(v.get(n, {}).get("state") == "valid"
               for _p, n, _w, _t in J.expected())


# ---- legs ------------------------------------------------------------------------

def leg_j1():
    chip0 = chip_tx()
    start_truck("j1", 250, ["--twin", hex(TWIN), "--on-request-period", "2"])
    t0 = time.time()
    doc = wait_for("every value of the table", 25,
                   lambda: (lambda d: d if all_values_valid(d)
                            and d.get("vin") else None)(status()))
    run.metric("j1_all_values_s", round(time.time() - t0, 1), "s")
    # a second round of the slow groups (1 s) and of the VIN (2 s): periods
    # and the transport books need more than one message
    doc = wait_for("a second round of the slow groups", 15,
                   lambda: (lambda d: d if d["tp"]["completed"] >= 2
                            and values(d)["EngineCoolantTemperature"]
                            ["period_ms"] > 0 else None)(status()))
    can = dut.get("/api/can")
    run.check("j1_listening_at_250k",
              doc["state"] == "listening" and doc["bus"] == "j1939"
              and doc["can"] == {"running": True, "baud_kbps": 250,
                                 "link": "running", "listen_only": True},
              json.dumps({"state": doc["state"], "bus": doc["bus"],
                          "can": doc["can"]}))
    v = values(doc)
    bad = {n: v.get(n, {}).get("value") for _p, n, w, tol in J.expected()
           if not close_enough(v.get(n, {}).get("value"), w, tol)}
    run.check("j1_every_value_is_the_trucks", not bad,
              json.dumps(bad) if bad else f"{len(J.expected())} values")
    st = stored()
    wrong = []
    for name, pgn, _prio, _per in J.PGN_SET:
        got = st.get((f"{pgn:04X}", 0, 255), {}).get("data")
        if got != J.encode_pgn(pgn, J.DEFAULTS).hex().upper():
            wrong.append((name, got))
    run.check("j1_every_payload_is_the_trucks_bytes", not wrong,
              json.dumps(wrong) if wrong else f"{len(J.PGN_SET)} groups")
    run.check("j1_vin_by_bam", doc.get("vin") == VIN
              and doc.get("vin_sa") == 0,
              f"{doc.get('vin')} from {doc.get('vin_sa')}")
    twin_rpm = J.encode_pgn(J.PGN_EEC1, dict(
        J.DEFAULTS, rpm=J.DEFAULTS["rpm"] + 1000)).hex().upper()
    code, one = dut.api(f"/api/j1939?pgn=F004&sa={TWIN}")
    code_any, picked = dut.api("/api/j1939?pgn=F004")
    run.check("j1_two_sources_kept_apart",
              code == 200 and one.get("data") == twin_rpm
              and st.get(("F004", 0, 255), {}).get("data") != twin_rpm,
              f"twin {one.get('data') if code == 200 else code}")
    run.check("j1_pick_is_the_lowest_address",
              v["EngineSpeed"]["sa"] == 0
              and v["WheelBasedVehicleSpeed"]["sa"] == 0
              and code_any == 200 and picked.get("sa") == 0,
              f"values from {v['EngineSpeed']['sa']}, "
              f"?pgn=F004 from {picked.get('sa')}")
    heard = sorted(s["sa"] for s in doc["sources"])
    run.check("j1_sources", heard == [0, TWIN], str(heard))
    per = {n: v[n]["period_ms"] for n in ("EngineSpeed",
                                           "EngineCoolantTemperature")}
    run.metric("j1_eec1_period_ms", per["EngineSpeed"], "ms")
    run.check("j1_periods_measured",
              5 <= per["EngineSpeed"] <= 60
              and 800 <= per["EngineCoolantTemperature"] <= 1200, str(per))

    done = truck.stop()
    time.sleep(1.0)
    doc = status()
    tp = doc["tp"]
    run.check("j1_transport_books",
              tp_books(tp) and tp["completed"] >= 2 and tp["open"] == 0
              and not any(tp[k] for k in ("seq_errors", "timeouts", "aborted",
                                          "replaced", "no_session",
                                          "orphan_dt", "bad_cm")),
              json.dumps(tp))
    stats = doc["stats"]
    run.check("j1_frame_books",
              frame_books(stats) and stats["queue_drops"] == 0
              and stats["not_kept"] == 0 and stats["rx_foreign"] == 0
              and stats["rx_diag"] == 0, json.dumps(stats))
    sent = sum(done.get("tx", {}).values())
    lost = sent - stats["rx_frames"]
    run.metric("j1_frames_before_the_bitrate_was_known", lost)
    run.check("j1_all_but_the_first_frames_taken_in", 0 <= lost <= 40,
              f"truck sent {sent}, listener took {stats['rx_frames']}")
    can1 = dut.get("/api/can")
    run.check("j1_dut_transmitted_nothing",
              can1["tx"] == 0 and can["tx"] == 0
              and done.get("foreign_ids") == 0
              and done.get("bus_errors") == 0 and done.get("tx_errors") == 0,
              f"DUT tx {can1['tx']}; the adapter saw "
              f"{done.get('foreign_ids')} foreign ids, "
              f"{done.get('bus_errors')} bus errors, "
              f"{done.get('tx_errors')} tx errors")
    run.check("j1_no_frame_lost_inside_the_dut",
              can1["rx_missed"] == 0 and can1["rx_overrun"] == 0
              and can1["dispatch_drops"] == 0,
              json.dumps({k: can1[k] for k in ("rx_missed", "rx_overrun",
                                               "dispatch_drops")}))
    chip1 = chip_tx()
    run.check("j1_obd_chip_untouched", chip0 is not None and chip1 == chip0,
              f"chip tx_bytes {chip0} -> {chip1}")


def dm1_of(doc, sa=0):
    for d in doc.get("dm1", []):
        if d["sa"] == sa:
            return d
    return {}


def leg_j2():
    start_truck("j2a", 250, ["--dtcs", THREE_ARG, "--lamps", "mil=1,awl=1"])
    d = wait_for("three trouble codes", 15,
                 lambda: (lambda x: x if x.get("count") == 3 else None)(
                     dm1_of(status())))
    codes = [(c["code"], c["oc"]) for c in d["dtcs"]]
    run.check("j2_three_codes_by_bam",
              codes == [(J.dtc_text(s, f), oc) for s, f, oc in THREE]
              and all(c["cm"] is False for c in d["dtcs"]), str(codes))
    run.check("j2_lamps", (d["mil"], d["rsl"], d["awl"], d["pl"])
              == (1, 0, 1, 0), str((d["mil"], d["rsl"], d["awl"], d["pl"])))
    code, raw = dut.api("/api/j1939?pgn=FECA&sa=0")
    want = J.dm_encode({"mil": 1, "awl": 1}, THREE).hex().upper()
    run.check("j2_long_message_bytes", code == 200 and raw.get("len") == 14
              and raw.get("data") == want, str(raw)[:120])

    start_truck("j2b", 250, ["--dtcs", "110-0-5", "--lamps", "mil=1"])
    d = wait_for("one trouble code", 15,
                 lambda: (lambda x: x if x.get("count") == 1 else None)(
                     dm1_of(status())))
    code, raw = dut.api("/api/j1939?pgn=FECA&sa=0")
    run.check("j2_one_code_in_a_frame",
              [c["code"] for c in d["dtcs"]] == ["SPN110-0"]
              and d["mil"] == 1 and d["awl"] == 0 and raw.get("len") == 8,
              f"{d.get('dtcs')} len {raw.get('len')}")

    start_truck("j2c", 250, [])
    d = wait_for("no trouble code", 15,
                 lambda: (lambda x: x if x.get("count") == 0
                          and x.get("mil") == 0 else None)(dm1_of(status())))
    doc = status()
    run.check("j2_no_code", d["dtcs"] == [] and d["mil"] == 0, str(d))
    run.check("j2_books", tp_books(doc["tp"])
              and doc["stats"]["long_evicted"] == 0
              and doc["tp"]["seq_errors"] == 0, json.dumps(doc["tp"]))
    truck.stop()


def leg_j3():
    start_truck("j3", 250, [])
    wait_for("engine speed", 15,
             lambda: values(status()).get("EngineSpeed", {}).get("value"))
    time.sleep(2.0)
    truck.stop()
    t_stop = time.time()
    time.sleep(3.0)
    a = status()
    time.sleep(1.5)
    b = status()
    va, vb = values(a), values(b)
    waited_ms = (time.time() - t_stop) * 1000
    run.check("j3_ages_grow",
              va["EngineSpeed"]["age_ms"] >= 2500
              and vb["EngineSpeed"]["age_ms"] >= va["EngineSpeed"]["age_ms"]
              + 1000 and vb["EngineSpeed"]["age_ms"] <= waited_ms + 1500,
              f"{va['EngineSpeed']['age_ms']} then "
              f"{vb['EngineSpeed']['age_ms']} ms, {waited_ms:.0f} ms after "
              "the truck stopped")
    run.check("j3_nothing_changes",
              a["stats"]["seq"] == b["stats"]["seq"]
              and a["stats"]["rx_frames"] == b["stats"]["rx_frames"]
              and b["state"] == "listening",
              f"seq {a['stats']['seq']} -> {b['stats']['seq']}, frames "
              f"{a['stats']['rx_frames']} -> {b['stats']['rx_frames']}")
    run.check("j3_last_value_kept_with_its_age",
              vb["EngineSpeed"].get("value") == J.DEFAULTS["rpm"]
              and vb["EngineSpeed"]["state"] == "valid",
              str(vb["EngineSpeed"]))


def leg_j4():
    start_truck("j4a", 250, ["--fault", "na"])
    v = wait_for("not available", 15,
                 lambda: (lambda x: x if x.get("EngineSpeed", {}).get("state")
                          == "na" and x.get("EngineCoolantTemperature")
                          else None)(values(status())))
    run.check("j4_not_available_has_no_value",
              all(v[n]["state"] == "na" and "value" not in v[n]
                  for n in ("EngineSpeed", "ActualEnginePercentTorque",
                            "WheelBasedVehicleSpeed"))
              and v["EngineCoolantTemperature"]["state"] == "valid",
              json.dumps({n: v[n].get("state") for n in (
                  "EngineSpeed", "WheelBasedVehicleSpeed",
                  "EngineCoolantTemperature")}))
    start_truck("j4b", 250, ["--fault", "err"])
    v = wait_for("error", 15,
                 lambda: (lambda x: x if x.get("EngineSpeed", {}).get("state")
                          == "error" else None)(values(status())))
    run.check("j4_error_has_no_value",
              v["EngineSpeed"]["state"] == "error"
              and "value" not in v["EngineSpeed"]
              and v["WheelBasedVehicleSpeed"]["state"] == "error",
              str(v["EngineSpeed"]))
    start_truck("j4c", 250, [])
    v = wait_for("valid again", 15,
                 lambda: (lambda x: x if x.get("EngineSpeed", {}).get("state")
                          == "valid" else None)(values(status())))
    run.check("j4_valid_again", v["EngineSpeed"].get("value")
              == J.DEFAULTS["rpm"], str(v["EngineSpeed"]))
    truck.stop()


def leg_j5():
    four = ["--dtcs", "1-1-1,2-2-2,3-3-3,4-4-4"]    # 18 bytes: 3 packets
    start_truck("j5_ok", 250, four)
    wait_for("four trouble codes", 15,
             lambda: dm1_of(status()).get("count") == 4)
    truck.stop()
    time.sleep(1.5)
    base = status()
    code, dm0 = dut.api("/api/j1939?pgn=FECA&sa=0")

    def faulty(tag, fault, secs):
        before = status()["tp"]
        start_truck(tag, 250, four + ["--fault", fault])
        time.sleep(secs)
        done = truck.stop()
        time.sleep(1.6)         # the last session's timeout
        after = status()["tp"]
        return ({k: after[k] - before[k] for k in after
                 if k not in ("open", "max")}, after, done.get("bams", 0))

    d, tp, bams = faulty("j5_drop", "bam_drop:2", 4.5)
    # the stop may cut the last BAM between its packets 1 and 3: that one
    # times out instead of failing on the sequence (gate run 2026-10-03,
    # one in three runs)
    run.check("j5_dropped_packet",
              bams >= 3 and d["seq_errors"] >= bams - 1
              and d["seq_errors"] + d["timeouts"] == d["started"] >= bams - 1
              and d["timeouts"] <= 1 and d["completed"] == 0 and tp_books(tp),
              f"{bams} BAMs: {json.dumps(d)}")
    d, tp, bams = faulty("j5_reorder", "bam_reorder", 4.5)
    run.check("j5_reordered_packets",
              bams >= 3 and d["seq_errors"] == d["started"] >= bams - 1
              and d["orphan_dt"] >= 2 * (bams - 1) and d["completed"] == 0
              and tp_books(tp), f"{bams} BAMs: {json.dumps(d)}")
    d, tp, bams = faulty("j5_stall", "bam_stall", 4.5)
    run.check("j5_missing_packets_time_out",
              bams >= 3 and d["completed"] == 0 and d["started"] >= bams - 1
              and d["timeouts"] + d["replaced"] == d["started"]
              and d["timeouts"] >= 1 and tp_books(tp),
              f"{bams} BAMs: {json.dumps(d)}")
    code, dm1 = dut.api("/api/j1939?pgn=FECA&sa=0")
    doc = status()
    run.check("j5_nothing_stored_from_a_broken_message",
              dm1.get("count") == dm0.get("count")
              and dm1.get("data") == dm0.get("data")
              and dm1_of(doc).get("count") == 4
              and doc["tp"]["completed"] == base["tp"]["completed"],
              f"DM1 messages {dm0.get('count')} -> {dm1.get('count')}, "
              f"completed {base['tp']['completed']} -> "
              f"{doc['tp']['completed']}")


def leg_j6():
    truck.stop()
    sim_ack_only(500)
    start_truck("j6", 500, ["--on-request-period", "2"])
    t0 = time.time()
    doc = wait_for("the 500k bus", 30,
                   lambda: (lambda d: d if d["can"]["baud_kbps"] == 500
                            and d["can"]["link"] == "running"
                            and values(d).get("EngineSpeed", {}).get("age_ms",
                                                                    9999)
                            < 500 else None)(status()))
    run.metric("j6_found_again_s", round(time.time() - t0, 1), "s")
    doc = wait_for("every value at 500k", 20,
                   lambda: (lambda d: d if all(
                       x.get("state") == "valid" and x["age_ms"] < 3000
                       for x in values(d).values()) and len(values(d))
                       == len(J.expected()) else None)(status()))
    v = values(doc)
    bad = {n: v.get(n, {}).get("value") for _p, n, w, tol in J.expected()
           if not close_enough(v.get(n, {}).get("value"), w, tol)}
    run.check("j6_found_at_500k", doc["can"]["baud_kbps"] == 500
              and doc["can"]["listen_only"] is True, json.dumps(doc["can"]))
    run.check("j6_values_at_500k", not bad, json.dumps(bad) if bad else
              f"{len(v)} values")
    done = truck.stop()
    can = dut.get("/api/can")
    run.check("j6_dut_transmitted_nothing",
              can["tx"] == 0 and done.get("foreign_ids") == 0,
              f"DUT tx {can['tx']}, foreign ids {done.get('foreign_ids')}")


def leg_j7():
    truck.stop()
    sim_ack_only(500)
    dut.restart({"can_manager": {"enabled": True, "baud": "auto",
                                 "silent": False},
                 "j1939": {"enabled": True},
                 "autopid": {"enabled": False}})
    start_truck("j7", 500, [])
    doc = wait_for("values in normal mode", 30,
                   lambda: (lambda d: d if values(d).get("EngineSpeed", {})
                            .get("state") == "valid"
                            and d["can"]["link"] == "running" else None)(
                       status()))
    time.sleep(4.0)
    doc = status()
    can = dut.get("/api/can")
    done = truck.stop()
    run.check("j7_normal_mode",
              can["listen_only"] is False and can["state"] == "running"
              and doc["can"]["baud_kbps"] == 500,
              json.dumps({k: can[k] for k in ("state", "listen_only",
                                              "baud_kbps", "verified")}))
    run.check("j7_values", values(doc)["EngineSpeed"].get("value")
              == J.DEFAULTS["rpm"], str(values(doc)["EngineSpeed"]))
    run.check("j7_still_not_one_frame",
              can["tx"] == 0 and can["tx_refused"] == 0
              and done.get("foreign_ids") == 0,
              f"DUT tx {can['tx']}, refused {can['tx_refused']}, foreign "
              f"ids {done.get('foreign_ids')}")


def leg_j8(faults0):
    truck.stop()
    dut.restart({"can_manager": {"enabled": False},
                 "j1939": {"enabled": True}})
    time.sleep(2.0)
    code, doc = dut.api("/api/j1939")
    run.check("j8_no_bus_is_a_state",
              code == 200 and doc.get("state") == "no_bus"
              and doc.get("enabled") is True
              and doc["can"]["running"] is False,
              json.dumps({k: doc.get(k) for k in ("enabled", "state", "can")})
              if code == 200 else str(code))
    said = [ln for ln in dut.ring_lines()
            if "j1939" in ln and "native CAN bus is off" in ln]
    run.check("j8_says_so_once", len(said) >= 1 and said[-1].startswith("W ("),
              said[-1][:120] if said else "no such line in the log ring")
    code, f = dut.api("/api/faults")
    run.check("j8_no_fault", f == faults0, json.dumps(f)[:200])


# ---- main ------------------------------------------------------------------------

def main():
    legs = ALL_LEGS if not ONLY else [x for x in ALL_LEGS if x in ONLY]
    unknown = [x for x in ONLY if x not in ALL_LEGS]
    if unknown or not legs:
        print(f"unknown leg(s) {unknown}; legs: {' '.join(ALL_LEGS)}")
        return 2

    # ---- as found -----------------------------------------------------------------
    st0 = dut.get("/api/status")
    found = {n: dut.settings(n) for n in ("can_manager", "j1939", "autopid")}
    found_sim = bench_bus.sim_get("ecu_sim")
    code, faults0 = dut.api("/api/faults")
    print(f"DUT {DUT_HOST}: boot_count {st0.get('boot_count')}, "
          f"unexpected_resets {st0.get('unexpected_resets')}, version "
          f"{st0.get('version')}")
    print(f"as found: {json.dumps(found)}")
    print(f"as found: simulator {json.dumps(found_sim)}")
    dut.as_found()

    done = 0
    try:
        if legs != ["j8"]:
            sim_ack_only(500 if legs[0] in ("j6", "j7") else 250)
            # autopid off: since phase 5 its contact probes meet a J1939
            # bus with the chip's 0100 / 22F400 and then store the truck;
            # this bench tests the listener alone (the AutoPID stage has
            # the rest)
            dut.restart({"can_manager": {"enabled": True, "baud": "auto",
                                         "silent": True},
                         "j1939": {"enabled": True},
                         "autopid": {"enabled": False}})
            code, doc = dut.api("/api/j1939")
            if code != 200 or doc.get("state") != "listening":
                raise Bench(f"the listener is not up: {code} {doc}")
        for leg in legs:
            print(f"--- {leg}", flush=True)
            if leg == "j8":
                leg_j8(faults0)
            else:
                if leg in ("j1", "j2", "j3", "j4", "j5"):
                    sim_ack_only(250)
                globals()["leg_" + leg]()
            dut.sweep()
            done += 1
            print(f"PROGRESS {done}/{len(legs)}", flush=True)
    except Bench as e:
        run.check("bench_ran_to_the_end", False, str(e))
    finally:
        # ---- restore, as found ------------------------------------------------------
        print("--- restore", flush=True)
        try:
            truck.stop()
            now = {n: dut.settings(n) for n in found}
            if now != found:
                dut.restart(found)
            if bench_bus.sim_get("ecu_sim") != found_sim:
                # on at its bitrate first: the controller keeps the bitrate
                # it last RAN at
                bench_bus.sim_set(bitrate=found_sim["bitrate"],
                                  id_format=found_sim["id_format"],
                                  enabled=True)
                bench_bus.sim_set(enabled=found_sim["enabled"])
            now = {n: dut.settings(n) for n in found}
            run.check("restored_settings", now == found,
                      "" if now == found else json.dumps(now))
            sim_now = bench_bus.sim_get("ecu_sim")
            run.check("restored_simulator", sim_now == found_sim,
                      "" if sim_now == found_sim else json.dumps(sim_now))
            code, f1 = dut.api("/api/faults")
            run.check("no_new_fault", f1 == faults0, json.dumps(f1)[:300])
            st1 = dut.get("/api/status")
            run.check("no_unexpected_reset",
                      st1.get("unexpected_resets")
                      == st0.get("unexpected_resets")
                      and st1.get("boot_count") - st0.get("boot_count")
                      == dut.restarts,
                      f"unexpected_resets {st0.get('unexpected_resets')} -> "
                      f"{st1.get('unexpected_resets')}, boot_count +"
                      f"{st1.get('boot_count') - st0.get('boot_count')} for "
                      f"{dut.restarts} restarts")
            dut.passing_checks(run)
        except Bench as e:
            run.check("restore", False, str(e))

    return run.verdict("legs: " + " ".join(legs) if ONLY else "")


if __name__ == "__main__":
    sys.exit(main())
