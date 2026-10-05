#!/usr/bin/env python3
"""J1939 active-mode bench (TASK_j1939_wwh.md, phase 6): the `j1939`
component as a NODE of the network, against a truck played by the PCAN
adapter.

The claim under test: with `mode active` the device claims an address
(J1939-81) and keeps or moves it on a contest, answers a Request for Address
Claimed and negatively acknowledges requests it cannot serve, asks the
vehicle for groups (single frame, BAM and RTS/CTS answers, the last with the
clear-to-send and end-of-message frames of a destination), learns a NACK,
keeps every frame under a bus flood (the owned TX slots and the bounded
retry), serves autopid's `?` rows and the DM2 / DM3 / DM11 of the DTC page,
stops asking under a diagnostics hold, and in `mode listen` (or on a
listen-only bus) still never puts a frame on the bus.

The vehicle is actors/pcan_j1939_truck.py (codec lib/j1939_ref.py); the
ECU simulator stays on the bus as the ACK source with its own ECU off. The
truck is started BEFORE every DUT restart (a 500 kbit/s car's rows polled on
a silent bus collide with a truck waking up at 250, bench 2026-10-03).
Every leg judges observed state: GET /api/j1939 (claim, tx, store), GET
/api/can, GET /api/autopid, the DTC report, and the truck's log (claims it
saw, requests, acknowledgments, RTS/CTS outcomes, bus errors).

Legs:
  a1  claim: the truck sees one Address Claimed from 249 with the device's
      NAME; the node is `claimed`, the TX counters moved, no bus error
  a2  asking: a single-frame group answered to our address, the VIN by BAM,
      DM2 by RTS/CTS (we send CTS and the end-of-message), an unknown group
      NACKed by the truck and counted
  a3  asked: the truck asks us for our address (answered with the claim)
      and for a group we do not have (NACKed, naming the truck)
  a4  contention: a lesser NAME claims our address (kept, defended); a
      better NAME does (moved to 250, claimed there)
  a5  flood: 1000 filler frames/s; 20 requests all answered; nothing lost,
      retries counted
  a6  autopid: the truck becomes the current car BY ITS VIN (asked for);
      a `?` row asks at its period and publishes; a `?` row the truck
      refuses fails and is held; the DTC report carries DM2 as pending
      items; the clear (DM11 + DM3) is acknowledged and empties the list
  a7  a diagnostics hold (a J2534 tester attached, --diag) stops the
      asking, the reading goes on; released, the asking resumes
  a8  mode listen: not one frame, a request is refused (409), no claim
  a9  mode active on a listen-only bus: a listener, says so once, no frame
  restore: every setting, the vehicle store, the tables and the simulator
  as found.

Verdict: `J1939 ACTIVE PASS`. Run on the PC (IDF venv python; python-can):
  python j1939_active_bench.py [host[:port]] [--pcan PCAN_USBBUS2]
        [--diag host:port] [--only a1,a2] [--logdir DIR]
`--diag` is the DUT's J2534 TCP port (6809) as reachable from here; without
it leg a7 is not run. `--only` runs chosen legs and says `J1939 ACTIVE
PARTIAL PASS`.
"""
import json
import os
import socket
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
DIAG = ""
ONLY = []
LOGDIR = tempfile.gettempdir()
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--pcan":
        PCAN = args[i + 1]; i += 2
    elif args[i] == "--diag":
        DIAG = args[i + 1]; i += 2
    elif args[i] == "--only":
        ONLY = [x.strip() for x in args[i + 1].split(",") if x.strip()]; i += 2
    elif args[i] == "--logdir":
        LOGDIR = args[i + 1]; i += 2
    else:
        DUT_HOST = args[i]; i += 1

ALL_LEGS = ["a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9"]
VIN = "1WCANJ1939TRUCK01"
ACTIVE = [(110, 0, 5)]
ACTIVE_ARG = "110-0-5"
PREV = [(100, 1, 2), (3226, 4, 7)]
PREV_ARG = "100-1-2,3226-4-7"
TOOL1, TOOL2 = 0xF9, 0xFA
UNKNOWN_PGN = 0xFE00       # a group the truck does not have (not reserved)
HOURS_EXPR = "(B0+B1*256+B2*65536+B3*16777216)*0.05"

run = Run("J1939 ACTIVE")
dut = Dut(DUT_HOST, own_tags=("j1939", "can_manager", "can_core", "autopid"),
          tasks=("j1939", "can_core_rx", "autopid"))
truck = Actor(os.path.join(HERE, "..", "actors", "pcan_j1939_truck.py"),
              LOGDIR, "j1939_active_truck", "TRUCK READY", "TRUCK DONE ")
bus_kbps = [0]
truck_key = [""]

ACTIVE_CFG = {"can_manager": {"enabled": True, "baud": "auto", "silent": False},
              "j1939": {"enabled": True, "mode": "active", "address": TOOL1},
              "autopid": {"enabled": False}}


# ---- the rig ---------------------------------------------------------------------

def sim_ack_only(kbps):
    if bus_kbps[0] == kbps:
        return
    bench_bus.sim_set(bitrate=kbps, enabled=True)
    bench_bus.sim_set(enabled=False)
    bus_kbps[0] = kbps


def start_truck(tag, extra=()):
    """The plain truck of these legs: one active code, two previously
    active ones, no on-request broadcasts (we ask)."""
    truck.start(tag, ["--pcan", PCAN, "--bitrate", "250", "--vin", VIN,
                      "--dtcs", ACTIVE_ARG, "--prev", PREV_ARG,
                      "--lamps", "mil=1"] + list(extra))


def status():
    return dut.get("/api/j1939")


def can():
    return dut.get("/api/can")


def wait_for(what, secs, fn):
    end = time.time() + secs
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(0.3)
    raise Bench(f"{what}: not within {secs} s")


def wait_true(what, secs, fn):
    """As wait_for, but a plain condition; False instead of Bench."""
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.3)
    return False


def entry(pgn, sa, da=None):
    """The store's newest message of a group (a PDU2 group's destination is
    always everyone, whoever asked for it; a reassembled connection keeps
    the destination it was sent to)."""
    q = f"/api/j1939?pgn={pgn:04X}&sa={sa}" + (f"&da={da}" if da is not None else "")
    code, m = dut.api(q)
    return m if code == 200 and isinstance(m, dict) else {}


def ask(pgn, da=None):
    q = f"/api/j1939?request={pgn:04X}" + (f"&da={da}" if da is not None else "")
    return dut.api(q)


def my_address():
    return status().get("claim", {}).get("address")


def events(prefix_ev, start=0):
    return [e for e in truck.events("TRUCK ", start) if e.get("ev") == prefix_ev]


def autopid():
    return dut.get("/api/autopid")


def params(doc=None):
    doc = doc or autopid()
    return {p["name"]: p for p in doc.get("params", [])}


def stats(doc=None):
    return (doc or autopid()).get("stats", {})


def vehicles():
    code, v = dut.api("/api/autopid/vehicles")
    return v if code == 200 and isinstance(v, dict) else {}


def current_car():
    v = vehicles()
    for e in v.get("vehicles", []):
        if e.get("key") == v.get("current"):
            return e
    return {}


def forget(key):
    return dut.api("/api/autopid/vehicles/" + key, "DELETE")[0]


def close_enough(got, want, step):
    return isinstance(got, (int, float)) and abs(got - want) <= step / 2 + 1e-6


def dtc():
    return dut.get("/api/autopid/dtc")


def wait_scan(secs):
    return wait_for("the DTC scan to finish", secs,
                    lambda: (lambda x: x if x.get("scanning") is False
                             and x.get("report", {}).get("valid") else None)(dtc()))


# ---- legs ------------------------------------------------------------------------

def leg_a1():
    """The claim, as the truck saw it and as the node reports it."""
    doc = wait_for("the address claim", 20,
                   lambda: (lambda d: d if d.get("claim", {}).get("state")
                            == "claimed" else None)(status()))
    cl = doc["claim"]
    seen = events("claim_seen")
    ours = [e for e in seen if e.get("sa") == f"{TOOL1:02X}"]
    run.check("a1_truck_saw_our_claim",
              len(ours) >= 1 and ours[0].get("name", "").upper() == cl.get("name", "").upper(),
              f"{len(ours)} claim(s) from F9 seen, NAME {ours[0].get('name') if ours else None} "
              f"vs ours {cl.get('name')}")
    run.check("a1_claimed_249",
              doc.get("mode") == "active" and cl.get("state") == "claimed"
              and cl.get("address") == TOOL1 and cl.get("tx_ready") is True
              and cl.get("claims_sent") >= 1 and cl.get("contests") == 0,
              json.dumps(cl))
    name = bytes.fromhex(cl.get("name", "00" * 8))
    run.check("a1_name_is_a_service_tool",
              len(name) == 8 and name[5] == 129 and name[7] & 0x80
              and name[3] == 0 and (name[2] & 0xE0) == 0,
              f"NAME {cl.get('name')}: function {name[5] if len(name) == 8 else None}, "
              f"byte 7 {name[7]:02X}" if len(name) == 8 else "")
    tx = doc["tx"]
    c = can()
    run.check("a1_tx_counters_moved",
              tx["frames"] >= 1 and tx["failed"] == 0 and c["tx"] >= 1
              and c.get("tx_done", 0) >= 1 and c.get("tx_lost", 0) == 0,
              json.dumps({"j1939_tx": tx, "can_tx": c["tx"], "tx_done": c.get("tx_done"),
                          "tx_lost": c.get("tx_lost"), "tx_refused": c.get("tx_refused")}))
    run.check("a1_listening_as_well",
              doc["state"] == "listening" and doc["bus"] == "j1939"
              and doc["can"]["listen_only"] is False and doc["can"]["baud_kbps"] == 250,
              json.dumps({"state": doc["state"], "bus": doc["bus"], "can": doc["can"]}))


def leg_a2():
    """Asking the vehicle: three shapes of answer and a refusal."""
    me = my_address()
    n0 = entry(J.PGN_HOURS, 0).get("count", 0)
    code, r = ask(J.PGN_HOURS)
    run.check("a2_request_sent", code == 200 and r.get("sent") is True
              and r.get("from") == me and r.get("outcome") == "pending", f"{code} {r}")
    # an on-request group nobody broadcasts: the truck sends it once, now
    m = wait_for("engine hours answered", 4,
                 lambda: (lambda x: x if x.get("count", 0) > n0 else None)(
                     entry(J.PGN_HOURS, 0)))
    want = J.encode_pgn(J.PGN_HOURS, J.DEFAULTS).hex().upper()
    run.check("a2_single_frame_answer",
              m.get("data") == want and m.get("sa") == 0 and m.get("count") == n0 + 1
              and m.get("age_ms", 9999) < 4000, json.dumps(m)[:160])
    reqs = events("request")
    run.check("a2_truck_logged_our_request",
              any(e.get("pgn") == f"{J.PGN_HOURS:04X}" and e.get("from") == f"{me:02X}"
                  and e.get("to") == "FF" for e in reqs), json.dumps(reqs[-3:]))

    code, r = ask(J.PGN_VI)
    doc = wait_for("the VIN by BAM", 6,
                   lambda: (lambda d: d if d.get("vin") == VIN else None)(status()))
    run.check("a2_vin_by_bam_on_request", doc.get("vin") == VIN and doc.get("vin_sa") == 0
              and doc["tp"]["completed"] >= 1, f"{doc.get('vin')} tp {json.dumps(doc['tp'])}")

    mark = truck.mark()
    tx0 = status()["tx"]
    code, r = ask(J.PGN_DM2, 0)
    m = wait_for("DM2 by RTS/CTS", 6,
                 lambda: (lambda x: x if x.get("count", 0) >= 1 else None)(
                     entry(J.PGN_DM2, 0, me)))
    want = J.dm_encode({"mil": 1}, PREV).hex().upper()
    doc = status()
    tp = doc["tp"]
    rts = [e for e in truck.events("TRUCK ", mark) if e.get("ev") == "rts"]
    eoma = [e for e in truck.events("TRUCK ", mark) if e.get("ev") == "eoma"]
    run.check("a2_dm2_by_rts_cts_as_destination",
              m.get("data") == want and m.get("len") == len(want) // 2 and m.get("da") == me
              and rts and rts[0].get("da") == f"{me:02X}" and eoma and eoma[0].get("ok") is True,
              f"bytes {m.get('data')} len {m.get('len')}; truck rts {rts[:1]} eoma {eoma[:1]}")
    tx = doc["tx"]
    run.check("a2_we_sent_cts_and_eoma",
              tx["tp_to_me"] - tx0["tp_to_me"] == 1 and tx["tp_cts"] - tx0["tp_cts"] >= 1
              and tx["tp_eoma"] - tx0["tp_eoma"] == 1 and tx["tp_aborts"] == tx0["tp_aborts"]
              and tx["tp_reply_lost"] == 0 and tp["completed"] >= 2 and tp["seq_errors"] == 0,
              json.dumps({k: tx[k] for k in ("tp_to_me", "tp_cts", "tp_eoma", "tp_aborts",
                                             "tp_reply_lost")})
              + json.dumps({k: tp[k] for k in ("completed", "seq_errors", "timeouts")}))

    nacks0 = doc["tx"]["nacks"]
    code, r = ask(UNKNOWN_PGN, 0)
    doc = wait_for("the truck's NACK", 4,
                   lambda: (lambda d: d if d["tx"]["nacks"] > nacks0 else None)(status()))
    code, r = ask(UNKNOWN_PGN, 0)     # asking again reports the slot's outcome: pending anew
    run.check("a2_nack_counted", doc["tx"]["nacks"] == nacks0 + 1 and doc["tx"]["acks"] == 0
              and code == 200 and r.get("outcome") == "pending",
              json.dumps(doc["tx"]) + f" / {r}")
    run.check("a2_no_tx_failure", doc["tx"]["failed"] == 0 and can().get("tx_lost", 0) == 0,
              json.dumps(doc["tx"]))


def leg_a3():
    """The truck asks us: for our address, and for a group we do not have."""
    me = my_address()
    doc0 = status()
    start_truck("a3", ["--ask", f"0x{me:02X}:EE00,0x{me:02X}:{UNKNOWN_PGN:04X},0xFF:EE00",
                       "--ask-period", "2"])
    doc = wait_for("our answers to the truck's requests", 12,
                   lambda: (lambda d: d if d["claim"]["requests_answered"]
                            >= doc0["claim"]["requests_answered"] + 2
                            and d["tx"]["nacks_sent"] >= doc0["tx"]["nacks_sent"] + 1
                            else None)(status()))
    seen = [e for e in events("claim_seen") if e.get("sa") == f"{me:02X}"]
    acks = events("ackm")
    run.check("a3_claim_answered_to_a_request_for_address_claimed",
              len(seen) >= 2 and doc["claim"]["state"] == "claimed"
              and doc["claim"]["address"] == me,
              f"{len(seen)} claims from {me:02X} seen by the truck, "
              f"requests answered {doc['claim']['requests_answered']}")
    nack = [e for e in acks if e.get("control") == J.NACK and e.get("from") == f"{me:02X}"
            and e.get("for") == "00" and e.get("pgn") == f"{UNKNOWN_PGN:04X}"]
    run.check("a3_unknown_group_nacked_naming_the_truck",
              len(nack) >= 1 and doc["tx"]["nacks_sent"] >= 1,
              f"truck heard {json.dumps(acks[:3])}; nacks_sent {doc['tx']['nacks_sent']}")
    run.check("a3_no_data_answered", doc["tx"]["frames"] >= doc0["tx"]["frames"] + 3
              and doc["tx"]["failed"] == 0, json.dumps(doc["tx"]))


def leg_a4():
    """A contest for our address: lost to a better NAME, kept against a lesser."""
    me = my_address()
    doc0 = status()
    start_truck("a4a", ["--contend", f"0x{me:02X}:lose", "--ask", f"0x{me:02X}:EE00",
                        "--ask-period", "2"])
    doc = wait_for("a contest we win", 12,
                   lambda: (lambda d: d if d["claim"]["won"] > doc0["claim"]["won"]
                            else None)(status()))
    time.sleep(2.0)             # the truck contests every claim it hears
    doc = status()
    run.check("a4_lesser_name_kept_and_defended",
              doc["claim"]["address"] == me and doc["claim"]["state"] == "claimed"
              and doc["claim"]["won"] >= 1 and doc["claim"]["lost"] == doc0["claim"]["lost"],
              json.dumps(doc["claim"]))
    seen = [e for e in events("claim_seen") if e.get("sa") == f"{me:02X}"]
    run.check("a4_defence_seen_by_the_truck", len(seen) >= 2,
              f"{len(seen)} claims from {me:02X} (the answer and the defence)")
    # the truck's contend mode is a broken node: it contests our every
    # defence at wire speed. We answer once per quarter second, not every one
    # (finding, 2026-10-03: 150 rounds in 2 s before the hold)
    cl = doc["claim"]
    rounds = cl["won"] - doc0["claim"]["won"]
    run.metric("a4_contest_rounds", rounds)
    # every claim of ours the truck hears is contested: our answer to its
    # request (every 2 s), then our defence, whose contest is held; so about
    # half the contests are held and we send about one frame per contest
    # round, not one per contest
    run.check("a4_one_defence_per_round_not_a_storm",
              cl["held"] >= 1 and cl["held"] >= rounds // 2 - 1 and len(seen) <= rounds + 2
              and len(seen) <= 12,
              f"{rounds} contests, {cl['held']} not answered again, the truck saw {len(seen)} "
              f"claims from us")

    start_truck("a4b", ["--contend", f"0x{me:02X}:win", "--ask", "0xFF:EE00",
                        "--ask-period", "2"])
    doc = wait_for("a contest we lose", 12,
                   lambda: (lambda d: d if d["claim"]["lost"] > doc0["claim"]["lost"]
                            and d["claim"]["state"] == "claimed" else None)(status()))
    run.check("a4_better_name_moves_us_to_250",
              doc["claim"]["address"] == TOOL2 and doc["claim"]["lost"] == doc0["claim"]["lost"] + 1
              and doc["claim"]["cannot"] == 0, json.dumps(doc["claim"]))
    seen = [e.get("sa") for e in events("claim_seen")]
    run.check("a4_new_claim_seen_by_the_truck", f"{TOOL2:02X}" in seen, str(seen[-4:]))
    code, r = ask(J.PGN_HOURS)
    run.check("a4_requests_go_out_from_the_new_address", code == 200 and r.get("from") == TOOL2,
              f"{code} {r}")
    truck.stop()


def leg_a5():
    """A flooded bus of frames that beat ours in arbitration (priority 0):
    the single-shot controller loses frames, the bounded retry sends them
    again; every request still answered, nothing lost inside."""
    start_truck("a5", ["--flood", "1000", "--flood-prio", "0"])
    time.sleep(2.0)
    # 60 requests: with one retry budget of 3 for every failure a 50 % flood
    # of priority-0 frames lost about one request in sixteen (2 of 20 in the
    # gate of 2026-10-03); lost arbitrations get their own budget of 16 now
    c0 = can()
    n0 = entry(J.PGN_HOURS, 0).get("count", 0)
    sent = 0
    for _ in range(60):
        code, r = ask(J.PGN_HOURS)
        sent += 1 if code == 200 and r.get("sent") else 0
        time.sleep(0.1)
    time.sleep(1.5)
    n1 = entry(J.PGN_HOURS, 0).get("count", 0)
    c1 = can()
    done = truck.stop()
    retries = c1.get("tx_retries", 0) - c0.get("tx_retries", 0)
    run.metric("a5_tx_retries", retries)
    run.metric("a5_filler_frames", done.get("filler_tx", 0))
    run.check("a5_every_request_answered_under_flood", sent == 60 and n1 - n0 >= 59,
              f"{sent} requests sent, {n1 - n0} answers stored, "
              f"{done.get('filler_tx')} filler frames")
    run.check("a5_lost_arbitrations_were_retried", retries >= 3,
              f"{retries} retries for 60 frames against a priority-0 flood")
    run.check("a5_nothing_lost",
              c1.get("tx_lost", 0) == c0.get("tx_lost", 0)
              and c1["tx_done"] - c0["tx_done"] >= 60
              and c1["rx_missed"] == c0["rx_missed"] and c1["rx_overrun"] == c0["rx_overrun"]
              and done.get("bus_errors") == 0 and done.get("tx_errors") == 0,
              json.dumps({"tx_done": c1["tx_done"] - c0["tx_done"],
                          "tx_lost": c1.get("tx_lost"), "rx_missed": c1["rx_missed"],
                          "rx_overrun": c1["rx_overrun"], "truck_bus_errors": done.get("bus_errors"),
                          "truck_tx_errors": done.get("tx_errors")}))
    st = status()["stats"]
    run.check("a5_listener_kept_up", st["queue_drops"] == 0, json.dumps(st))


def autopid_on_the_truck(leg):
    """autopid on, the truck as the current car. The stored car is an OBD-II
    car (the bench's): its rows fail on the truck's bus, the chip probes and
    finds nobody, then the listener's word counts (a device moved from a car
    to a truck; up to a minute). Nothing to do when it is already so."""
    if dut.settings("autopid").get("enabled") and current_car().get("dialect") == "j1939":
        return
    truck.stop()
    start_truck(leg)
    dut.restart({"autopid": {"enabled": True, "std_protocol": "0", "pause_mode": "requests_only",
                             "dtc_enabled": True, "dtc_allow_clear": True}})
    t0 = time.time()
    e = wait_for("the truck as the current car", 90,
                 lambda: (lambda x: x if x.get("dialect") == "j1939" else None)(current_car()))
    run.metric(f"{leg}_car_switch_s", round(time.time() - t0, 1), "s")
    truck_key[0] = e.get("key", "")
    run.check(f"{leg}_car_keyed_by_the_vin_asked_for",
              e.get("key") == VIN and e.get("vin") == VIN and e.get("j1939") is True,
              json.dumps({k: e.get(k) for k in ("key", "vin", "dialect", "j1939", "ecus")}))
    reqs = [x for x in events("request") if x.get("pgn") == f"{J.PGN_VI:04X}"]
    run.check(f"{leg}_vin_was_requested", len(reqs) >= 1,
              f"{len(reqs)} VIN request(s) at the truck")
    # the detection job stores the new car and reloads the tables: the
    # bench's own table change waits for it (a PUT under the reload lost
    # the race once: "config reload failed (ESP_ERR_INVALID_SIZE)")
    wait_for("the detection job to finish", 60,
             lambda: dut.get("/api/autopid/std_scan").get("status") == "done")
    time.sleep(1.0)


def leg_a6():
    """autopid on top: the car by its VIN, `?` rows, DM2 in the report, the clear."""
    autopid_on_the_truck("a6")

    code, cfg = dut.api("/api/autopid/config")
    cfg["pids"] = [p for p in cfg.get("pids", [])
                   if p.get("name") not in ("HoursAsked", "NobodyHas")]
    cfg["pids"].append({"name": "HoursAsked", "type": "custom", "cmd": f"PGN:{J.PGN_HOURS:X}?",
                        "group": "default", "period_ms": 2000,
                        "parameters": [{"name": "HoursAsked", "expression": HOURS_EXPR,
                                        "unit": "h", "min": 0, "max": 210554060}]})
    cfg["pids"].append({"name": "NobodyHas", "type": "custom", "cmd": f"PGN:{UNKNOWN_PGN:X}@0?",
                        "group": "default", "period_ms": 2000,
                        "parameters": [{"name": "NobodyHas", "expression": "B0"}]})
    code, r = dut.api("/api/autopid/config", "PUT", cfg)
    run.check("a6_question_rows_accepted", code == 200, f"{code} {r}")
    s0 = stats()
    v = wait_for("the asked group's value", 15,
                 lambda: (lambda x: x if close_enough(x, J.DEFAULTS["hours_h"], 0.05)
                          else None)(params().get("HoursAsked", {}).get("value")))
    run.check("a6_question_row_publishes_the_answer", close_enough(v, J.DEFAULTS["hours_h"], 0.05),
              str(v))
    time.sleep(6.5)
    s1 = stats()
    asked = s1.get("passive_requested", 0) - s0.get("passive_requested", 0)
    run.metric("a6_requests_in_6_5_s", asked)
    run.check("a6_asks_at_its_period_not_more",
              2 <= asked <= 8 and s1.get("j1939_active") is True,
              f"{asked} requests from two rows at 2 s in 6.5 s")
    run.check("a6_refused_row_fails_and_holds",
              s1.get("passive_refused", 0) >= 1
              and params().get("NobodyHas", {}).get("value") is None,
              f"passive_refused {s1.get('passive_refused')}, NobodyHas "
              f"{params().get('NobodyHas')}")
    truck_reqs = [x for x in events("request") if x.get("pgn") == f"{UNKNOWN_PGN:04X}"]
    run.check("a6_refused_group_asked_once_then_held", 1 <= len(truck_reqs) <= 2,
              f"{len(truck_reqs)} request(s) for {UNKNOWN_PGN:04X} at the truck")

    code, d = dut.api("/api/autopid/dtc")
    run.check("a6_path_is_j1939", code == 200 and d.get("path") == "j1939", json.dumps(d)[:160])
    code, r = dut.api("/api/autopid/dtc/scan", "POST", {})
    run.check("a6_scan_started", code in (200, 202), f"{code} {r}")
    rep = wait_scan(30).get("report", {})
    items = rep.get("items", [])
    pend = {it["code"]: it for it in items if it.get("kind") == "pending"}
    run.check("a6_report_active_and_previously_active",
              rep.get("protocol") == "j1939" and rep.get("j1939") is True
              and rep.get("stored") == [J.dtc_text(s, f) for s, f, _ in ACTIVE]
              and sorted(rep.get("pending", [])) == sorted(J.dtc_text(s, f) for s, f, _ in PREV)
              and all(pend.get(J.dtc_text(s, f), {}).get("oc") == oc
                      and pend.get(J.dtc_text(s, f), {}).get("sa") == 0 for s, f, oc in PREV),
              json.dumps({k: rep.get(k) for k in ("protocol", "j1939", "stored", "pending",
                                                  "error")}) + " " + json.dumps(items)[:200])
    dm2_reqs = [x for x in events("request") if x.get("pgn") == f"{J.PGN_DM2:04X}"]
    run.check("a6_dm2_was_asked_at_scan", len(dm2_reqs) >= 1, f"{len(dm2_reqs)} DM2 request(s)")

    mark = truck.mark()
    code, r = dut.api("/api/autopid/dtc/clear", "POST", {"confirm": True, "mode": "always"})
    run.check("a6_clear_acknowledged", code == 200 and r.get("ok") is True
              and r.get("cleared") is True and r.get("before") == len(ACTIVE)
              and r.get("after") == 0, f"{code} {r}")
    clear_reqs = [x for x in truck.events("TRUCK ", mark) if x.get("ev") == "request"
                  and x.get("pgn") in (f"{J.PGN_DM11:04X}", f"{J.PGN_DM3:04X}")]
    run.check("a6_dm11_and_dm3_sent_to_the_truck",
              {x.get("pgn") for x in clear_reqs} == {f"{J.PGN_DM11:04X}", f"{J.PGN_DM3:04X}"}
              and all(x.get("to") == "00" for x in clear_reqs), json.dumps(clear_reqs))
    # the clear queues a scan: the report follows the network
    rep = wait_for("the report after the clear", 30,
                   lambda: (lambda x: x.get("report") if x.get("scanning") is False
                            and x.get("report", {}).get("valid")
                            and x.get("report", {}).get("stored") == [] else None)(dtc()))
    run.check("a6_report_empty_after_the_clear",
              rep.get("stored") == [] and rep.get("pending") == [] and rep.get("mil") is False
              and rep.get("j1939") is True,
              json.dumps({k: rep.get(k) for k in ("stored", "pending", "mil", "j1939", "error")}))
    cfg["pids"] = [p for p in cfg["pids"] if p.get("name") not in ("HoursAsked", "NobodyHas")]
    dut.api("/api/autopid/config", "PUT", cfg)


def leg_a7():
    """A J2534 tester attached (the exclusive hold): `?` rows stop asking."""
    if not DIAG:
        print("  a7: no --diag host:port given; not run")
        return
    autopid_on_the_truck("a7")
    code, cfg = dut.api("/api/autopid/config")
    cfg["pids"] = [p for p in cfg.get("pids", []) if p.get("name") != "HoursAsked"]
    cfg["pids"].append({"name": "HoursAsked", "type": "custom", "cmd": f"PGN:{J.PGN_HOURS:X}?",
                        "group": "default", "period_ms": 1000,
                        "parameters": [{"name": "HoursAsked", "expression": HOURS_EXPR,
                                        "unit": "h", "min": 0, "max": 210554060}]})
    code, r = dut.api("/api/autopid/config", "PUT", cfg)
    wait_for("the row asking", 15, lambda: params().get("HoursAsked", {}).get("value"))
    host, port = DIAG.rsplit(":", 1)
    s = socket.create_connection((host, int(port)), timeout=5)
    try:
        held = wait_true("the diagnostics hold", 8, lambda: stats().get("paused_diag") is True)
        run.check("a7_tester_holds_the_bus", held, json.dumps(stats()))
        s0 = stats()
        time.sleep(5.0)
        s1 = stats()
        run.check("a7_asking_stops_reading_goes_on",
                  s1.get("passive_requested") == s0.get("passive_requested")
                  and s1.get("passive_ok", 0) > s0.get("passive_ok", 0),
                  f"requested {s0.get('passive_requested')} -> {s1.get('passive_requested')}, "
                  f"looks {s0.get('passive_ok')} -> {s1.get('passive_ok')}")
    finally:
        s.close()
    released = wait_true("the hold released", 8, lambda: stats().get("paused_diag") is False)
    s2 = stats()
    time.sleep(3.5)
    s3 = stats()
    run.check("a7_asking_resumes", released
              and s3.get("passive_requested", 0) >= s2.get("passive_requested", 0) + 2,
              f"requested {s2.get('passive_requested')} -> {s3.get('passive_requested')}")
    cfg["pids"] = [p for p in cfg["pids"] if p.get("name") != "HoursAsked"]
    dut.api("/api/autopid/config", "PUT", cfg)


def leg_a8():
    """Listen mode: the old listener, not one frame."""
    truck.stop()
    start_truck("a8")
    dut.restart({"j1939": {"mode": "listen"}, "autopid": {"enabled": False}})
    wait_for("values in listen mode", 20,
             lambda: (lambda d: d if d["state"] == "listening" and d["bus"] == "j1939"
                      and d["can"]["link"] == "running" else None)(status()))
    time.sleep(3.0)
    doc = status()
    c = can()
    code, r = ask(J.PGN_HOURS)
    done = truck.stop()
    run.check("a8_listen_mode_claims_nothing",
              doc["mode"] == "listen" and doc["claim"]["state"] == "idle"
              and doc["claim"]["address"] == 254 and doc["tx"]["frames"] == 0,
              json.dumps({"mode": doc["mode"], "claim": doc["claim"], "tx": doc["tx"]}))
    run.check("a8_not_one_frame", c["tx"] == 0 and done.get("foreign_ids") == 0
              and not events("claim_seen"),
              f"DUT tx {c['tx']}, truck saw {done.get('foreign_ids')} foreign ids")
    run.check("a8_request_refused", code == 409 and "listen" in json.dumps(r), f"{code} {r}")


def leg_a9():
    """Active mode on a bus can_manager holds listen-only: a listener that
    says so once."""
    start_truck("a9")
    t_boot = dut.restart({"can_manager": {"silent": True}, "j1939": {"mode": "active"}})
    wait_for("listening on the silent bus", 20,
             lambda: (lambda d: d if d["state"] == "listening" and d["bus"] == "j1939"
                      else None)(status()))
    time.sleep(max(0.0, 13.0 - (time.time() - t_boot)))
    doc = status()
    c = can()
    code, r = ask(J.PGN_HOURS)
    done = truck.stop()
    run.check("a9_active_on_a_listen_only_bus_stays_a_listener",
              doc["mode"] == "active" and doc["claim"]["state"] == "idle"
              and doc["claim"]["tx_ready"] is False and doc["tx"]["frames"] == 0
              and c["tx"] == 0 and done.get("foreign_ids") == 0 and code == 409,
              json.dumps({"claim": doc["claim"], "tx": doc["tx"], "can_tx": c["tx"],
                          "foreign": done.get("foreign_ids"), "request": [code, r]}))
    said = [ln for ln in dut.ring_lines() if ln not in dut.ring0
            and "j1939" in ln and "does not let this node transmit" in ln]
    run.check("a9_says_so_once", len(said) == 1 and said[0].startswith("W ("),
              said[0][:140] if said else f"{len(said)} such lines")


# ---- main ------------------------------------------------------------------------

def main():
    legs = ALL_LEGS if not ONLY else [x for x in ALL_LEGS if x in ONLY]
    unknown = [x for x in ONLY if x not in ALL_LEGS]
    if unknown or not legs:
        print(f"unknown leg(s) {unknown}; legs: {' '.join(ALL_LEGS)}")
        return 2

    # ---- as found -----------------------------------------------------------------
    st0 = dut.get("/api/status")
    comps = ("can_manager", "j1939", "autopid", "j2534_server")
    found = {n: dut.settings(n) for n in comps}
    found_sim = bench_bus.sim_get("ecu_sim")
    for e in list(vehicles().get("vehicles", [])):
        if e.get("vin") == VIN or (e.get("dialect") == "j1939"
                                   and str(e.get("ecus", "")).startswith("0:")):
            print(f"forgetting a truck left behind: {e.get('key')}")
            forget(e["key"])
    found_store = vehicles()
    found_keys = {e["key"] for e in found_store.get("vehicles", [])}
    found_car = found_store.get("current")
    found_cfg = dut.get("/api/autopid/config")
    code, faults0 = dut.api("/api/faults")
    print(f"DUT {DUT_HOST}: boot_count {st0.get('boot_count')}, unexpected_resets "
          f"{st0.get('unexpected_resets')}, version {st0.get('version')}")
    print("as found: " + json.dumps({n: {k: found[n].get(k) for k in
                                         ("enabled", "baud", "silent", "mode", "address",
                                          "pause_mode", "dtc_enabled") if k in found[n]}
                                     for n in found}))
    print(f"as found: simulator {json.dumps(found_sim)}, current car {found_car}")
    dut.as_found()

    done = 0
    try:
        sim_ack_only(250)
        start_truck("pre")              # on the bus before the DUT boots
        cfg = dict(ACTIVE_CFG)
        if DIAG:
            # a tester attached over TCP (from the LAN side of the Pi) is
            # the diagnostics hold of leg a7
            cfg["j2534_server"] = {"enabled": True, "exclusive": True, "allow_lan": True}
        dut.restart(cfg)
        code, doc = dut.api("/api/j1939")
        if code != 200 or doc.get("state") != "listening":
            raise Bench(f"the component is not up: {code} {doc}")
        for leg in legs:
            print(f"--- {leg}", flush=True)
            globals()["leg_" + leg]()
            dut.sweep()
            done += 1
            print(f"PROGRESS {done}/{len(legs)}", flush=True)
    except Exception as e:  # noqa: BLE001 - a bench bug is a verdict too
        if not isinstance(e, Bench):
            import traceback
            traceback.print_exc()
        run.check("bench_ran_to_the_end", False, f"{type(e).__name__}: {e}")
        for ln in dut.ring_lines()[-400:]:
            if " D (" in ln or ": " not in ln or ") " not in ln:
                continue
            tag = ln.split(") ", 1)[1].split(":", 1)[0].strip()
            if tag in ("autopid", "j1939", "can_manager", "can_core"):
                print("  LOG " + ln[:220])
    finally:
        # ---- restore, as found ------------------------------------------------------
        print("--- restore", flush=True)
        try:
            truck.stop()
            for e in list(vehicles().get("vehicles", [])):
                if e["key"] not in found_keys:
                    forget(e["key"])
            if found_car:
                dut.api("/api/autopid/vehicles/" + found_car + "/activate", "POST", {})
            if dut.get("/api/autopid/config") != found_cfg:
                code, r = dut.api("/api/autopid/config", "PUT", found_cfg)
                if code != 200:
                    raise Bench(f"PUT config back: {code} {r}")
            now = {n: dut.settings(n) for n in found}
            if now != found:
                dut.restart(found)
            if bench_bus.sim_get("ecu_sim") != found_sim:
                bench_bus.sim_set(bitrate=found_sim["bitrate"], id_format=found_sim["id_format"],
                                  enabled=True)
                bench_bus.sim_set(enabled=found_sim["enabled"])
            now = {n: dut.settings(n) for n in found}
            run.check("restored_settings", now == found, "" if now == found else json.dumps(now)[:300])
            sv = vehicles()
            same_store = sorted(e["key"] for e in sv.get("vehicles", [])) == \
                sorted(e["key"] for e in found_store.get("vehicles", [])) and sv.get("current") == found_car
            run.check("restored_vehicle_store", same_store, "" if same_store else json.dumps(sv)[:300])
            cfg = dut.get("/api/autopid/config")
            run.check("restored_tables", cfg == found_cfg,
                      "" if cfg == found_cfg else f"{len(cfg.get('pids', []))} pids now")
            sim_now = bench_bus.sim_get("ecu_sim")
            run.check("restored_simulator", sim_now == found_sim, "" if sim_now == found_sim else json.dumps(sim_now))
            code, f1 = dut.api("/api/faults")
            run.check("no_new_fault", f1 == faults0, json.dumps(f1)[:300])
            dut.reset_check(run, st0)
            dut.passing_checks(run)
        except Bench as e:
            run.check("restore", False, str(e))

    return run.verdict("legs: " + " ".join(legs) if ONLY else "")


if __name__ == "__main__":
    sys.exit(main())
