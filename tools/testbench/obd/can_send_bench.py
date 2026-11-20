#!/usr/bin/env python3
"""can.send: a raw frame on any bus of the OBD connector  ->  CAN SEND PASS / FAIL

`POST /api/can/send`, the rule action `can.send` and the script binding
`can_send()` put raw frames on a bus by name (GitHub issue #1072): `can` is
WiCAN's own CAN controller, `obd` the car's OBD protocol through the OBD
chip, `ms`, `sw` and `ch` MS, single-wire and CH CAN through the chip, and
`wake` sends a single-wire frame in the transceiver's high-voltage wake
mode. Nothing of it may write the chip's memory, and the chip must be on the
car's protocol again behind every send.

Against ECU sim v2 with every bus live. Its receive log (`/api/ecu/rxlog`:
the bus, the id and its length, the data length, the bytes of every frame it
read) is the witness of what was on the wire.

  route     what is refused before anything is sent: no bus, an unknown one,
            a bad id, an id over 11 bits called an 11-bit one, bad data, a
            frame without data through the chip, wake on another bus than
            single-wire CAN, a speed for `obd`, both forms at once, nine
            frames, a gap too long (400 with a sentence, nothing on any bus)
  buses     one frame on each bus through the chip, with an 11-bit and with
            a 29-bit id (`obd` on the car's CAN pins, `ms`, `ch`, `sw`):
            answered `sent`, read by the simulator on that bus and on no
            other, the id length, the data length and the bytes as given;
            the transcript shows the slot defined in the chip's RAM and
            chosen, no word the chip stores, and behind the frame the car's
            protocol, the format words undone and the slot taken back
  can       WiCAN's own CAN controller: frames with 2, 0 and 8 data bytes,
            11 and 29 bit, read by the simulator on the HS pins; the
            controller's own count of sent frames moves by as many
  seq       a sequence in one request: the frames in order, each one's start
            (`at_ms`) at its gap, 11 and 29 bit ids mixed on one bus
  noack     nobody on the bus (the simulator's single-wire transceiver
            asleep): `no_ack`, `ok` false, HTTP 200, nothing read
  wake      the wake mode against the simulator's SLEEPING single-wire
            transceiver, which passes the high-voltage level alone: frames
            in normal mode leave its controller untouched, one frame with
            `wake` trips it (twice, from a fresh controller each time); a
            normal frame behind a wake frame does not (the transceiver is
            back in normal mode). Then the issue's own sequence with the
            simulator awake: the wake frame, the network frame, the command
            and its second half 360 ms later, all four read as sent
  script    `can_send()` from a script: true and the frame on the bus, 29
            bits by the id's size, false with `can_send_error` when nobody
            acknowledges, an exception for a bus that does not exist
  app       an ELM app on /ws/obd: a send through the chip is refused (409)
            while the app has it and for ten seconds after, `can` still goes
  precan    (not with --skip-precan) the simulator a SAE J1850 VPW car, then
            an ISO 9141-2 car, the unit's protocol pinned to it: `obd` sends
            the three header bytes and the data on that protocol, the ECU
            answers (the header and the checksum were right), the
            simulator's own message count moves; a CAN id and eight data
            bytes are refused
  polling   Automate on with the tables as found and a timer rule with
            `can.send`: the rule's frames arrive on MS CAN, sends by the
            route in the middle of the polling go out, and the polled rows
            keep being answered with no failure
  the chip  chip_nvm's snapshot the same before and after, no protocol save,
            the stored protocol as found; no E line; every restart planned

The unit's settings (Automate, the rules, the script engine), its tables
and the simulator are put back as found.

usage: can_send_bench.py [--dut localhost:18081] [--skip-precan]
                         [--precan vpw,iso9141]   (also: pwm, kwp5, kwpfast)
                         [--only route,buses,can,seq,noack,wake,script,app,precan,polling]
  the DUT through an ssh tunnel (`ssh -N -L 18081:<dut>:80 rpi001`), ECU sim
  v2 at its own address (it is the unit's supply too). About 5 min, up to
  six planned restarts (three with --skip-precan; one more for every
  protocol added to --precan).
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)
import bench_bus  # noqa: E402
import chip_nvm   # noqa: E402

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
SIM = bench_bus.SIM_URL
ALL_LEGS = "route,buses,can,seq,noack,wake,script,app,precan,polling"
ONLY = [x for x in arg("--only", ALL_LEGS).split(",") if x]
if "--skip-precan" in ARGS and "precan" in ONLY:
    ONLY.remove("precan")
SIM_GROUPS = ("ecu_sim", "kline", "j1850")
#: every CAN interface of the simulator on its usual speed, 11 bit OBD
ALL_LIVE = {"enabled": True, "hs_bitrate_kbps": 500, "ms_bitrate_kbps": 125, "ch_bitrate_kbps": 500}
for _b in ("hs", "ms", "ch", "sw"):
    ALL_LIVE.update({_b + "_enabled": True, _b + "_obd11": True, _b + "_obd29": False, _b + "_uds": True})
#: the slot of the chip a bus is reached through, and its transceiver word
SLOT = {"obd": ("D", "HS_CAN"), "ms": ("B", "MS_CAN"), "ch": ("C", "CH_CAN"), "sw": ("F", "SW_CAN")}
#: where the simulator reads a frame sent on a bus
SIM_BUS = {"obd": "hs", "can": "hs", "ms": "ms", "ch": "ch", "sw": "sw"}
STORED_WORDS = ("> STP", "> VTP", "> ATLNK", "> ATSP", "> VTCFG", "> ATPP", "> ATM1", "> ATZ", "> ATD\n", "> ATWS")
RULE = "bench_can_send"
CHECKS = []
PLANNED = [0]


def now():
    return time.strftime("%H:%M:%S")


def say(text):
    print("%s %s" % (now(), text), flush=True)


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:340] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


def http(path, method="GET", body=None, timeout=30, base=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request((base or DUT) + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(text)
            except ValueError:
                return r.status, text
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(text)
        except ValueError:
            return exc.code, text
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def send(body, timeout=30):
    return http("/api/can/send", "POST", body, timeout=timeout)


def err_of(r):
    return r.get("error", "") if isinstance(r, dict) else str(r)


# ---- the simulator's receive log --------------------------------------------

class SimLog:
    """Every frame ECU sim v2 logs from a mark on. Its log is a ring of 64
    frames of which a read lists 60, the OLDEST ones: with the ring full the
    newest four are missing until later traffic pushes them down, so on a
    quiet bus a frame just sent would never show (found 2026-11-21). A mark
    therefore clears the simulator's log (DELETE), and the ring is read all
    the time so that a busy bus cannot push a frame out unseen."""

    def __init__(self):
        self.frames = {}
        self.gen = 0
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _read(self):
        gen = self.gen
        st, log = http("/api/ecu/rxlog", base=SIM, timeout=4)
        if isinstance(log, dict):
            with self.lock:
                if gen == self.gen:         # not a read from before the mark
                    for f in log.get("frames") or []:
                        self.frames[f["seq"]] = f

    def _run(self):
        while not self.stop.is_set():
            self._read()
            time.sleep(0.04)

    def mark(self):
        """forget everything read so far, the simulator's ring included"""
        with self.lock:
            self.gen += 1
            http("/api/ecu/rxlog", "DELETE", base=SIM, timeout=4)
            self.frames = {}
            return self.gen

    def since(self, mark):
        with self.lock:
            return [self.frames[k] for k in sorted(self.frames)] if mark == self.gen else []

    def find(self, mark, bus, ident, ext, data, wait=4.0):
        """the frames since `mark` with that id (any bus), once one of them
        is on `bus` as given, or the wait is over"""
        want = " ".join("%02X" % b for b in data)
        t0 = time.time()
        while True:
            same_id = [f for f in self.since(mark) if int(f["id"], 16) == ident]
            hit = [f for f in same_id if f["bus"] == bus and bool(f["ext"]) == ext and
                   f["dlc"] == len(data) and f["data"].strip().upper() == want]
            if hit or time.time() - t0 > wait:
                return hit, same_id
            time.sleep(0.05)

    def close(self):
        self.stop.set()
        self.thread.join(2)


def frame_text(f):
    return "%s %s%s [%d] %s" % (f["bus"], f["id"], " ext" if f["ext"] else "", f["dlc"], f["data"])


def sim_status():
    st, s = http("/api/ecu/status", base=SIM, timeout=5)
    return s if isinstance(s, dict) else {}


def sw_state():
    return (sim_status().get("sw_can") or {}).get("bridge_state")


def sw_settled(seconds=3.0):
    """the single-wire controller's state once the simulator had time to
    report it (its co-processor's state arrives about a second late): as
    soon as it is not `active` any more, or after the wait"""
    t0 = time.time()
    last = None
    while True:
        state = sw_state()          # None for a moment behind the co-processor's reset
        last = state if state is not None else last
        if state not in (None, "active") or time.time() - t0 > seconds:
            return last
        time.sleep(0.25)


def sw_fresh(mode):
    """the simulator's single-wire controller from its reset (error counters
    at zero), its transceiver in `mode`"""
    http("/api/c5/reset", "POST", base=SIM, timeout=8)
    t0 = time.time()
    while time.time() - t0 < 10:
        time.sleep(0.5)
        if sw_state() == "active":
            break
    bench_bus.sim_tester(swcan=mode)
    time.sleep(0.5)
    return sw_state()


# ---- the unit's settings -------------------------------------------------------

def settings(ns):
    st, cur = http("/api/settings/" + ns)
    return {k: v for k, v in cur.items() if k not in ("degraded", "pending_reboot")} if isinstance(cur, dict) else {}


def chip_ready():
    st, oc = http("/api/obd_chip", timeout=4)
    return isinstance(oc, dict) and oc.get("ready")


def apply_many(wanted):
    """keys of several settings groups behind ONE planned restart; none when
    nothing changes"""
    changed = False
    for ns, keys in wanted.items():
        cur = settings(ns)
        if all(cur.get(k) == v for k, v in keys.items()):
            continue
        new = dict(cur)
        new.update(keys)
        st, r = http("/api/settings/" + ns, "PUT", new)
        if st != 200:
            say("settings %s refused: %s" % (ns, r))
            return False
        changed = True
    if not changed:
        return True
    st, h = http("/api/restart/history")
    b0 = h.get("boot_count") if isinstance(h, dict) else None
    http("/api/settings/submit", "POST", {})
    PLANNED[0] += 1
    time.sleep(6)
    t0 = time.time()
    while time.time() - t0 < 150:
        st, h = http("/api/restart/history", timeout=4)
        if isinstance(h, dict) and h.get("boot_count") not in (None, b0) and chip_ready():
            time.sleep(3)
            return all(settings(ns).get(k) == v for ns, keys in wanted.items() for k, v in keys.items())
        time.sleep(1)
    return False


def sim_groups():
    return {g: {k: v for k, v in bench_bus.sim_get(g).items() if k not in ("degraded", "pending_reboot")} for g in SIM_GROUPS}


def sim_back(found, tester):
    cur = sim_groups()
    back = {g: {k: v for k, v in found[g].items() if cur[g].get(k) != v} for g in SIM_GROUPS}
    back = {g: ch for g, ch in back.items() if ch}
    if back:
        bench_bus.sim_patch(back)
    bench_bus.sim_tester(swcan=tester.get("swcan", "sleep"), relay_db9=tester.get("relay_db9", "ms"))
    return sim_groups() == found


def autopid():
    st, a = http("/api/autopid")
    return a if isinstance(a, dict) else {}


def chip_home():
    return ((autopid().get("stats") or {}).get("buses") or {}).get("now")


def stack_left(name):
    """the least stack a task of that name has ever had left in this boot
    (its high-water mark, bytes); None when there is no such task"""
    st, t = http("/api/status/tasks")
    left = [x.get("stack_hw") for x in (t.get("tasks") or []) if x.get("name") == name] if isinstance(t, dict) else []
    return min(left) if left else None


def wait_chip_free(seconds=11.5):
    """an app on /ws/obd (chip_nvm is one) keeps the chip for ten seconds
    behind its last word"""
    time.sleep(seconds)


# ---- the legs ----------------------------------------------------------------

def leg_route(log):
    mark = log.mark()
    cases = [
        ("no bus", {"id": "7DF", "data": "01"}, "bus is one of"),
        ("a bus that does not exist", {"bus": "hs", "id": "7DF", "data": "01"}, "bus is one of"),
        ("no id", {"bus": "ms", "data": "01"}, "id is hex"),
        ("an id that is no hex", {"bus": "ms", "id": "7DG", "data": "01"}, "id is hex"),
        ("an id over 29 bits", {"bus": "ms", "id": "20000000", "data": "01"}, "id is hex"),
        ("an id over 11 bits called an 11-bit one", {"bus": "ms", "id": "800", "ext": False, "data": "01"}, "11 bits"),
        ("half a byte of data", {"bus": "ms", "id": "7DF", "data": "012"}, "0 to 8 bytes"),
        ("nine data bytes", {"bus": "ms", "id": "7DF", "data": "010203040506070809"}, "0 to 8 bytes"),
        ("a frame without data through the chip", {"bus": "ms", "id": "7DF", "data": ""}, "without data"),
        ("wake on MS CAN", {"bus": "ms", "id": "100", "data": "00", "wake": True}, "bus sw alone"),
        ("a speed for the car's own protocol", {"bus": "obd", "bus_kbps": 250, "id": "7DF", "data": "01"}, "bus_kbps"),
        ("a speed the chip cannot make", {"bus": "ms", "bus_kbps": 333, "id": "7DF", "data": "01"}, "bus_kbps"),
        ("both forms at once", {"bus": "ms", "id": "7DF", "data": "01", "frames": [{"id": "7DF", "data": "01"}]}, "not both"),
        ("nine frames", {"bus": "ms", "frames": [{"id": "7DF", "data": "01"}] * 9}, "1 to 8"),
        ("a gap of three seconds", {"bus": "ms", "frames": [{"id": "7DF", "data": "01", "gap_ms": 3000}, {"id": "7DF", "data": "01"}]}, "gap_ms"),
    ]
    for name, body, want in cases:
        st, r = send(body)
        check("route: %s is refused with 400 and the reason" % name, st == 400 and want in err_of(r), "%s %s" % (st, err_of(r)))
    st, r = http("/api/can/send", "POST", raw=b"{not json")
    check("route: a body that is no JSON is refused with 400", st == 400, "%s %s" % (st, err_of(r)))
    time.sleep(2.0)
    got = [f for f in log.since(mark) if f["bus"] != "hs" or int(f["id"], 16) == 0x7DF]
    check("route: nothing went out for any of them", not got, [frame_text(f) for f in got][:4])
    st, acts = http("/api/events/actions")
    mine = [a for a in acts if a.get("name") == "can.send"] if isinstance(acts, list) else []
    props = ((mine[0].get("params_schema") or {}).get("properties") or {}) if mine else {}
    check("route: the rule action can.send is registered with its fields", all(k in props for k in ("bus", "id", "data", "ext", "wake")), list(props))


def leg_buses(log):
    n = 0
    for bus in ("obd", "ms", "ch", "sw"):
        for ext, ident, data in ((False, 0x300, [0x11]), (True, 0x18DA00F1, [0xDE, 0xAD, 0xBE]),
                                 (False, 0x340, [1, 2, 3, 4, 5, 6, 7, 8])):
            n += 1
            ident = ident + n          # every frame of the run has its own id
            mark = log.mark()
            st, r = send({"bus": bus, "id": "%X" % ident, "data": " ".join("%02X" % b for b in data)})
            ok = st == 200 and isinstance(r, dict) and r.get("ok") is True
            first = (r.get("frames") or [{}])[0] if isinstance(r, dict) else {}
            what = "%s, %d-bit id, %d byte(s)" % (bus, 29 if ext else 11, len(data))
            check("buses: %s: answered sent through the chip" % what,
                  ok and first.get("result") == "sent" and r.get("via") == "obd_chip" and r.get("sent") == 1,
                  r if not isinstance(r, dict) else (st, r.get("frames"), err_of(r)))
            hit, same = log.find(mark, SIM_BUS[bus], ident, ext, data)
            check("buses: %s: the simulator read it on %s exactly as given, and on no other bus" % (what, SIM_BUS[bus]),
                  len(hit) == 1 and len(same) == 1, [frame_text(f) for f in same])
            tr = (r.get("transcript") or "") if isinstance(r, dict) else ""
            slot, xcvr = SLOT[bus]
            define = "VTSET_CAN %s,%s," % (slot, "01" if ext else "81")
            if ext is False and len(data) == 1:
                behind = tr[tr.find("> %02X\n" % data[0]):]
                check("buses: %s: the slot defined in RAM and chosen (%s.. %s, ATTP%s), no word the chip stores" % (bus, define, xcvr, slot),
                      define in tr and xcvr in tr and -1 < tr.find(define) < tr.find("> ATTP%s" % slot) and
                      not any(w in tr for w in STORED_WORDS), tr.replace("\n", " ")[:300])
                check("buses: %s: behind the frame the car's protocol, the format words undone, the slot taken back" % bus,
                      all(w in behind for w in ("> ATTP", "> ATCAF1", "> ATV0", "> ATAT1", "> VTSET_CAN %s\n" % slot)) and
                      chip_home() == "obd", behind.replace("\n", " ")[:300])


def leg_can(log):
    st, can0 = http("/api/can")
    running = isinstance(can0, dict) and can0.get("running") and not can0.get("listen_only") and not can0.get("silent")
    if not running:
        st, r = send({"bus": "can", "id": "321", "data": "01"})
        check("can: with the unit's CAN controller not on the bus the send is refused with 409 and the reason",
              st == 409 and "CAN controller" in err_of(r), "%s %s" % (st, err_of(r)))
        say("NOTE: the unit's own CAN controller is not running (%s): the frames of this leg were not sent" % (
            can0.get("state") if isinstance(can0, dict) else can0))
        return
    frames = ((0x5C1, False, [0x0A, 0x0B]), (0x18FF5C02, True, []), (0x5C3, False, [8, 7, 6, 5, 4, 3, 2, 1]))
    for ident, ext, data in frames:
        mark = log.mark()
        st, r = send({"bus": "can", "id": "%X" % ident, "data": " ".join("%02X" % b for b in data)})
        first = (r.get("frames") or [{}])[0] if isinstance(r, dict) else {}
        what = "%d-bit id, %d byte(s)" % (29 if ext else 11, len(data))
        check("can: %s: answered sent by the CAN controller, no chip involved" % what,
              st == 200 and isinstance(r, dict) and r.get("ok") is True and first.get("result") == "sent" and
              r.get("via") == "can_controller" and "transcript" not in r, r)
        hit, same = log.find(mark, "hs", ident, ext, data)
        check("can: %s: the simulator read it on the HS pins exactly as given" % what, len(hit) == 1 and len(same) == 1,
              [frame_text(f) for f in same])
    st, can1 = http("/api/can")
    moved = (can1.get("tx_done", 0) - can0.get("tx_done", 0), can1.get("tx_lost", 0) - can0.get("tx_lost", 0)) if isinstance(can1, dict) else None
    check("can: the controller counts %d frames sent and none lost" % len(frames), moved == (len(frames), 0), moved)


def leg_seq(log):
    mark = log.mark()
    frames = [(0x4B1, False, [0xA1], 300), (0x4B2, False, [0xA2, 0xA2], 200), (0x4B3, False, [0xA3, 0xA3, 0xA3], 0)]
    t0 = time.time()
    st, r = send({"bus": "ms", "frames": [{"id": "%X" % i, "data": " ".join("%02X" % b for b in d), "gap_ms": g} for i, _, d, g in frames]})
    took = (time.time() - t0) * 1000
    got = r.get("frames") or [] if isinstance(r, dict) else []
    at = [f.get("at_ms") for f in got]
    check("seq: three frames in one request, all sent", st == 200 and isinstance(r, dict) and r.get("ok") is True and r.get("sent") == 3, r if not isinstance(r, dict) else got)
    check("seq: each frame starts at its gap (0, 300, 500 ms; 25 ms allowed)",
          len(at) == 3 and at[0] == 0 and abs(at[1] - 300) <= 25 and abs(at[2] - 500) <= 25 and took >= 500, "%s, the call took %.0f ms" % (at, took))
    seqs = []
    for ident, ext, data, _ in frames:
        hit, same = log.find(mark, "ms", ident, ext, data)
        seqs.append(hit[0]["seq"] if len(hit) == 1 else None)
    check("seq: the simulator read the three in the order given", None not in seqs and seqs == sorted(seqs), seqs)
    mark = log.mark()
    mixed = [(0x4C1, False, [0x01]), (0x18DA4CF1, True, [0x02, 0x02]), (0x4C3, False, [0x03]), (0x18DA4CF2, True, [0x04])]
    st, r = send({"bus": "ch", "frames": [{"id": "%X" % i, "data": " ".join("%02X" % b for b in d)} for i, _, d in mixed]})
    check("seq: 11 and 29 bit ids mixed on one bus, all sent", st == 200 and isinstance(r, dict) and r.get("ok") is True and r.get("sent") == 4,
          r if not isinstance(r, dict) else r.get("frames"))
    seqs = []
    for ident, ext, data in mixed:
        hit, same = log.find(mark, "ch", ident, ext, data)
        seqs.append(hit[0]["seq"] if len(hit) == 1 else None)
    check("seq: the simulator read the four on CH CAN, each with its id length, in order", None not in seqs and seqs == sorted(seqs), seqs)
    check("seq: the chip is on the car's protocol again", chip_home() == "obd", chip_home())


def leg_noack(log):
    bench_bus.sim_tester(swcan="sleep")
    time.sleep(0.5)
    mark = log.mark()
    st, r = send({"bus": "sw", "frames": [{"id": "6D1", "data": "01"}, {"id": "6D2", "data": "02"}]})
    got = r.get("frames") or [] if isinstance(r, dict) else []
    check("noack: nobody on single-wire CAN: HTTP 200, ok false, both frames no_ack (the chip said CAN ERROR)",
          st == 200 and isinstance(r, dict) and r.get("ok") is False and r.get("sent") == 0 and
          [f.get("result") for f in got] == ["no_ack", "no_ack"] and "CAN ERROR" in (got[0].get("reply") or ""), r if not isinstance(r, dict) else got)
    time.sleep(2.0)
    read = [f for f in log.since(mark) if f["bus"] == "sw"]
    check("noack: the simulator read nothing, and the chip is on the car's protocol again", not read and chip_home() == "obd",
          [frame_text(f) for f in read])
    bench_bus.sim_tester(swcan="normal")
    time.sleep(0.5)


def leg_wake(log):
    normal = {"bus": "sw", "id": "000", "data": "00 00 00 00 00 00 00 00"}
    wake = dict(normal, wake=True)
    for turn in (1, 2):
        state = sw_fresh("sleep")
        if not check("wake %d: the simulator's single-wire controller starts from its reset, its transceiver asleep" % turn, state == "active", state):
            return
        results = []
        for _ in range(3):
            st, r = send(normal)
            results.append(((r.get("frames") or [{}])[0].get("result")) if isinstance(r, dict) else st)
        state = sw_settled()
        check("wake %d: three frames in normal mode do not reach it (no_ack, its controller untouched 3 s later)" % turn,
              results == ["no_ack"] * 3 and state == "active", "%s, controller %s" % (results, state))
        st, r = send(wake)
        tr = (r.get("transcript") or "") if isinstance(r, dict) else ""
        state = sw_settled()
        check("wake %d: ONE frame with wake does (the high-voltage level passes a sleeping transceiver: its controller %s)" % (turn, state),
              st == 200 and state in ("warning", "passive", "bus-off"), "%s %s" % (st, state))
        if turn == 1:
            last = tr.rfind("VTSET_CAN F,81,0F,SW_CAN,3")
            check("wake: the slot is defined in the wake mode for that frame and normal again before it is taken back",
                  "VTSET_CAN F,81,0F,SW_CAN,2" in tr and tr.find("VTSET_CAN F,81,0F,SW_CAN,2") < last < tr.rfind("> VTSET_CAN F\n") and
                  not any(w in tr for w in STORED_WORDS), tr.replace("\n", " ")[-260:])
            state = sw_fresh("sleep")
            st, r = send(normal)
            after = sw_settled()
            check("wake: a frame in normal mode behind it does not reach the sleeping transceiver (the wake mode did not stay)",
                  state == "active" and after == "active", "%s -> %s" % (state, after))
    # the issue's own sequence, the simulator awake: every frame read as sent
    state = sw_fresh("normal")
    for _ in range(12):         # its controller acknowledges about a second behind its reset
        st, r = send({"bus": "sw", "id": "7FE", "data": "00"})
        if isinstance(r, dict) and r.get("ok") is True:
            break
        time.sleep(0.5)
    mark = log.mark()
    seq = [(0x100, False, [0x00], True, 0), (0x621, False, [0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00], False, 0),
           (0x1024E097, True, [0x80, 0x01, 0xFF], False, 360), (0x1024E097, True, [0x00, 0x00, 0xFF], False, 0)]
    st, r = send({"bus": "sw", "frames": [dict({"id": "%X" % i, "data": " ".join("%02X" % b for b in d), "gap_ms": g}, **({"wake": True} if w else {}))
                                          for i, _, d, w, g in seq]})
    got = r.get("frames") or [] if isinstance(r, dict) else []
    at = [f.get("at_ms") for f in got]
    check("wake: the issue's sequence (wake frame, network frame, command, its second half) is sent whole",
          st == 200 and isinstance(r, dict) and r.get("ok") is True and r.get("sent") == 4, r if not isinstance(r, dict) else got)
    check("wake: the command's second half starts 360 ms behind the first (25 ms allowed)", len(at) == 4 and abs((at[3] - at[2]) - 360) <= 25, at)
    time.sleep(2.0)
    read = [f for f in log.since(mark) if f["bus"] == "sw"]
    want = [("%X" % i, e, len(d), " ".join("%02X" % b for b in d)) for i, e, d, _, _ in seq]
    have = [(f["id"].upper().lstrip("0") or "0", bool(f["ext"]), f["dlc"], f["data"].strip().upper()) for f in read]
    check("wake: the simulator read the four frames on single-wire CAN, in order, as given", have == want, [frame_text(f) for f in read])
    check("wake: the chip is on the car's protocol again", chip_home() == "obd", chip_home())


def run_script(src):
    st, r = http("/api/scripts/run", "POST", {"src": src}, timeout=40)
    return st, (r.get("output") or r.get("error") or "") if isinstance(r, dict) else str(r), r


def leg_script(log):
    mark = log.mark()
    st, out, r = run_script("print(can_send('ms', 0x2A1, '01 02 03'))\nprint('[' + can_send_error + ']')")
    hit, same = log.find(mark, "ms", 0x2A1, False, [1, 2, 3])
    check("script: can_send('ms', 0x2A1, '01 02 03') is true, the frame on MS CAN", st == 200 and "true" in out and "[]" in out and len(hit) == 1,
          "%s %s %s" % (st, out.strip(), [frame_text(f) for f in same]))
    mark = log.mark()
    st, out, r = run_script("print(can_send('obd', 0x18DA2AF1, 'AA'))\nprint(can_send('ch', '2A3', 'BB CC'))")
    hit1, same1 = log.find(mark, "hs", 0x18DA2AF1, True, [0xAA])
    hit2, same2 = log.find(mark, "ch", 0x2A3, False, [0xBB, 0xCC])
    check("script: an id above 7FF is a 29-bit one, an id as hex text is taken", st == 200 and out.count("true") == 2 and len(hit1) == 1 and len(hit2) == 1,
          "%s %s %s" % (out.strip(), [frame_text(f) for f in same1], [frame_text(f) for f in same2]))
    bench_bus.sim_tester(swcan="sleep")
    time.sleep(0.5)
    st, out, r = run_script("print(can_send('sw', 0x2A4, '01'))\nprint('[' + can_send_error + ']')")
    check("script: nobody acknowledges: false, and can_send_error says so", st == 200 and "false" in out and "acknowledged" in out, "%s %s" % (st, out.strip()))
    bench_bus.sim_tester(swcan="normal")
    time.sleep(0.5)
    st, out, r = run_script("print(can_send('hs', 0x2A5, '01'))")
    check("script: a bus that does not exist raises can_error with the reason", "can_error" in out and "bus is one of" in out, "%s %s" % (st, out.strip()[:200]))
    st, ref = http("/api/scripts/reference")
    names = [b.get("name") for b in (ref.get("bindings") or [])] if isinstance(ref, dict) else []
    check("script: can_send is in the reference the Scripts page shows", "can_send" in names, len(names))
    left = stack_left("script_run")
    check("script: the script task keeps its stack (%s bytes never used; 2 KB asked)" % left, left is not None and left >= 2048, left)


def leg_app(log):
    st, can = http("/api/can")
    can_up = isinstance(can, dict) and can.get("running") and not can.get("listen_only") and not can.get("silent")
    chip = chip_nvm.Chip(DUT_HP)
    try:
        chip.cmd("ATE0")
        chip.cmd("ATI")
        mark = log.mark()
        st, r = send({"bus": "ms", "id": "6E1", "data": "01"})
        check("app: with an ELM app on the chip a send through the chip is refused with 409 and the reason",
              st == 409 and "app is using the OBD chip" in err_of(r), "%s %s" % (st, err_of(r)))
        if can_up:
            st, r = send({"bus": "can", "id": "6E2", "data": "02"})
            hit, same = log.find(mark, "hs", 0x6E2, False, [2])
            check("app: bus can does not need the chip and still goes", st == 200 and isinstance(r, dict) and r.get("ok") is True and len(hit) == 1,
                  r if not isinstance(r, dict) else r.get("frames"))
        again = chip.cmd("ATI")
        check("app: the app's own conversation is untouched", "ELM327" in again, again[:40])
        time.sleep(1.5)
        got = [f for f in log.since(mark) if int(f["id"], 16) == 0x6E1]
        check("app: nothing of the refused frame is on any bus", not got, [frame_text(f) for f in got])
    finally:
        chip.close()
    wait_chip_free()
    mark = log.mark()
    st, r = send({"bus": "ms", "id": "6E3", "data": "03"})
    hit, same = log.find(mark, "ms", 0x6E3, False, [3])
    check("app: ten seconds after the app's last word the chip sends again", st == 200 and isinstance(r, dict) and r.get("ok") is True and len(hit) == 1,
          r if not isinstance(r, dict) else (r.get("frames"), err_of(r)))


def _j1850_in(s):
    return (s.get("j1850") or {}).get("frames_in", 0)


def _kline_in(s):
    return (s.get("kline") or {}).get("messages_in", 0)


#: the simulator's state (bench_bus.SIM_BUSES): the unit's protocol, its
#: name, the request header of that protocol, the simulator's own counter
PRECAN = {"pwm": ("1", "SAE J1850 PWM", "616AF1", _j1850_in),
          "vpw": ("2", "SAE J1850 VPW", "686AF1", _j1850_in),
          "iso9141": ("3", "ISO 9141-2", "686AF1", _kline_in),
          "kwp5": ("4", "ISO 14230-4 (5 baud init)", "C133F1", _kline_in),
          "kwpfast": ("5", "ISO 14230-4 (fast init)", "C133F1", _kline_in)}
PRECAN_RUN = [x for x in arg("--precan", "vpw,iso9141").split(",") if x in PRECAN]


def leg_precan(found_proto):
    for name in PRECAN_RUN:
        proto, label, hdr, counter = PRECAN[name]
        bench_bus.sim_only(name)
        if not check("precan: the unit pinned to %s (protocol %s), the simulator that car alone (a planned restart)" % (label, proto),
                     apply_many({"autopid": {"enabled": False, "std_protocol": proto}})):
            continue
        time.sleep(4)
        n0 = counter(sim_status())
        st, r = send({"bus": "obd", "id": hdr, "data": "01 0C"}, timeout=40)
        first = (r.get("frames") or [{}])[0] if isinstance(r, dict) else {}
        tr = (r.get("transcript") or "") if isinstance(r, dict) else ""
        check("precan: %s: three header bytes and two data bytes are sent, and the ECU answers them (41 0C: the header and the checksum were right)" % label,
              st == 200 and isinstance(r, dict) and r.get("ok") is True and first.get("result") == "sent" and "41 0C" in (first.get("reply") or ""),
              r if not isinstance(r, dict) else (first, err_of(r)))
        st, r2 = send({"bus": "obd", "frames": [{"id": hdr, "data": "01 0D"}, {"id": hdr, "data": "01 05"}]}, timeout=40)
        got = r2.get("frames") or [] if isinstance(r2, dict) else []
        n1 = counter(sim_status())
        check("precan: %s: two more messages in one request, both answered; the simulator counted %d messages" % (label, n1 - n0),
              st == 200 and isinstance(r2, dict) and r2.get("ok") is True and "41 0D" in (got[0].get("reply") or "") and
              "41 05" in (got[1].get("reply") or "") and n1 - n0 >= 3, r2 if not isinstance(r2, dict) else got)
        check("precan: %s: on the car's own protocol (no slot, no protocol word of the send's own), the header set, the timing undone" % label,
              "VTSET_CAN" not in tr and ("> ATSH" + hdr) in tr and "> ATAT0" in tr and "> ATAT1" in tr[tr.find("> 010C"):] and
              not any(w in tr for w in STORED_WORDS), tr.replace("\n", " ")[:300])
        st, r = send({"bus": "obd", "id": "18DB33F1", "data": "01 0C"})
        check("precan: %s: a CAN id is refused with 400 (three header bytes here)" % label, st == 400 and "three header bytes" in err_of(r), "%s %s" % (st, err_of(r)))
        st, r = send({"bus": "obd", "id": hdr, "data": "01 02 03 04 05 06 07 08"})
        check("precan: %s: eight data bytes are refused with 400" % label, st == 400 and "1 to 7 data bytes" in err_of(r), "%s %s" % (st, err_of(r)))
    return found_proto


def leg_polling(log, found_em, found_ap):
    em = dict(found_em)
    em["timers"] = [t for t in (found_em.get("timers") or []) if t.get("name") != RULE] + [{"name": RULE, "period_s": 3}]
    em["rules"] = [x for x in (found_em.get("rules") or []) if x.get("name") != RULE] + [
        {"name": RULE, "on": "timer.tick", "match": {"timer": RULE}, "do": "can.send", "with": {"bus": "ms", "id": "5B1", "data": "AB CD"}}]
    if not check("polling: Automate on with the tables as found, and a timer rule with can.send every 3 s (a planned restart)",
                 apply_many({"autopid": {"enabled": True, "std_protocol": found_ap.get("std_protocol", "0")},
                             "event_manager": {"enabled": True, "timers": em["timers"], "rules": em["rules"]}})):
        return
    t0 = time.time()
    while time.time() - t0 < 60 and (autopid().get("stats") or {}).get("polls_ok", 0) < 5:
        time.sleep(1)
    def rule_fired():
        st, rules = http("/api/events/rules")
        mine = [x for x in rules if x.get("name") == RULE] if isinstance(rules, list) else []
        return mine[0].get("fired", 0) if mine else 0

    s0 = autopid().get("stats") or {}
    fired0 = rule_fired()
    ms0 = (sim_status().get("ms_can") or {}).get("rx_frames", 0)
    st, ev0 = http("/api/events/log")
    time.sleep(5)
    sent = []
    for bus, ident, ext, data in (("ms", 0x5B2, False, [0x01]), ("obd", 0x5B3, False, [0x02, 0x02]), ("sw", 0x18DA5BF1, True, [0x03])):
        m = log.mark()
        st, r = send({"bus": bus, "id": "%X" % ident, "data": " ".join("%02X" % b for b in data)})
        hit, same = log.find(m, SIM_BUS[bus], ident, ext, data)
        sent.append(st == 200 and isinstance(r, dict) and r.get("ok") is True and len(hit) == 1)
        time.sleep(1.5)
    check("polling: sends by the route in the middle of the polling go out on MS CAN, the car's bus and single-wire CAN", all(sent), sent)
    time.sleep(7)
    s1 = autopid().get("stats") or {}
    fired = rule_fired() - fired0
    time.sleep(1.0)                 # a frame of the last firing may still be on its way
    ms1 = (sim_status().get("ms_can") or {}).get("rx_frames", 0)
    st, ev1 = http("/api/events/log")
    errs = ((ev1.get("stats") or {}).get("action_errors", 0) - (ev0.get("stats") or {}).get("action_errors", 0)) if isinstance(ev0, dict) and isinstance(ev1, dict) else None
    # every firing is one frame on MS CAN, beside the one the route sent there
    # (a firing under way at either end of the window is one frame of slack)
    rule_frames = ms1 - ms0 - 1
    check("polling: the rule fired %d times and the simulator read %d frames of it on MS CAN, no action error" % (fired, rule_frames),
          fired >= 3 and abs(rule_frames - fired) <= 1 and errs == 0, "fired +%s, MS CAN +%s with the route's one, action errors +%s" % (fired, ms1 - ms0, errs))
    ok, bad = s1.get("polls_ok", 0) - s0.get("polls_ok", 0), s1.get("polls_failed", 0) - s0.get("polls_failed", 0)
    check("polling: the polled rows keep being answered around the sends (%d answered, %d failed)" % (ok, bad), ok >= 10 and bad == 0, "%s ok, %s failed" % (ok, bad))
    check("polling: the chip is found on the car's protocol", chip_home() == "obd", chip_home())
    worker, web = stack_left("em_worker"), stack_left("httpd")
    check("polling: the rules worker and the web server keep their stacks (%s and %s bytes never used; 2 KB asked)" % (worker, web),
          worker is not None and web is not None and worker >= 2048 and web >= 2048, "em_worker %s, httpd %s" % (worker, web))


def main():
    st, ap = http("/api/settings/autopid")
    st2, oc = http("/api/obd_chip")
    if not (isinstance(ap, dict) and isinstance(oc, dict) and oc.get("ready")):
        print("CAN SEND FAIL (the unit does not answer at %s, or its chip is not ready)" % DUT_HP)
        return 1
    st, probe = http("/api/can/send", "POST", {})
    if st == 404:
        print("CAN SEND FAIL (this firmware has no POST /api/can/send)")
        return 1
    found = {ns: settings(ns) for ns in ("autopid", "event_manager", "script_engine")}
    st, found_cfg = http("/api/autopid/config")
    st, h0 = http("/api/restart/history")
    g0 = oc.get("eeprom_guard", {})
    sim0 = sim_groups()
    tester0 = bench_bus.sim_tester()
    say("as found: the chip %s, store %s; Automate %s, protocol setting %s; the script engine %s" % (
        (oc.get("fw") or {}).get("chip"), g0.get("stored_protocol"), "on" if found["autopid"].get("enabled") else "off",
        found["autopid"].get("std_protocol"), "on" if found["script_engine"].get("enabled") else "off"))
    log = SimLog()
    snap0 = snap1 = None
    try:
        check("Automate off and the script engine on for the legs (a planned restart when one changed)",
              apply_many({"autopid": {"enabled": False}, "script_engine": {"enabled": True}}))
        live = bench_bus.sim_bus_live(bench_bus.sim_status())
        if not all(b in live for b in ("hs11_500", "ms11", "ch11", "sw11")):
            cur = bench_bus.sim_get("ecu_sim")
            want = {k: v for k, v in ALL_LIVE.items() if cur.get(k) != v}
            if want:
                bench_bus.sim_patch({"ecu_sim": want})
        bench_bus.sim_tester(swcan="normal")
        time.sleep(0.5)
        live = bench_bus.sim_bus_live(bench_bus.sim_status())
        if not check("the simulator's four CAN buses are live", all(b in live for b in ("hs11_500", "ms11", "ch11", "sw11")), " ".join(live)):
            raise SystemExit(1)
        snap0 = chip_nvm.snapshot(DUT_HP)
        wait_chip_free()
        for name, leg in (("route", leg_route), ("buses", leg_buses), ("can", leg_can), ("seq", leg_seq), ("noack", leg_noack),
                          ("wake", leg_wake), ("script", leg_script), ("app", leg_app)):
            if name in ONLY:
                leg(log)
        if "precan" in ONLY:
            leg_precan(found["autopid"].get("std_protocol", "0"))
            check("the simulator's settings as found", sim_back(sim0, tester0))
            bench_bus.sim_tester(swcan="normal")
        check("the unit's protocol setting as found, Automate still off (a planned restart when it changed)",
              apply_many({"autopid": {"enabled": False, "std_protocol": found["autopid"].get("std_protocol", "0")}}))
        time.sleep(2)
        snap1 = chip_nvm.snapshot(DUT_HP)
        if "polling" in ONLY:
            wait_chip_free(3)
            leg_polling(log, found["event_manager"], found["autopid"])
    except SystemExit:
        pass
    finally:
        log.close()
        check("the unit's settings as found (a planned restart when one changed)", apply_many(found))
        if isinstance(found_cfg, dict):
            st, back = http("/api/autopid/config")
            check("the unit's tables as found", st == 200 and back == found_cfg)
        check("the simulator as found", sim_back(sim0, tester0))

    for ns in found:
        check("the settings of %s read as found" % ns, settings(ns) == found[ns])
    if snap0 is not None and snap1 is not None:
        diff = chip_nvm.diff(snap0, snap1)
        check("the chip's stored cells the same before and after (%d cells)" % len(snap0), not diff, [w for w, _, _ in diff])
    else:
        check("the chip's stored cells were read before and after", False, "no snapshot")
    st, oc1 = http("/api/obd_chip")
    g1 = oc1.get("eeprom_guard", {}) if isinstance(oc1, dict) else {}
    check("the chip's stored protocol as found, no protocol save, no refused switch",
          g1.get("stored_protocol") == g0.get("stored_protocol") and g1.get("protocol_saves") == 0 and not g1.get("memory_refused"),
          "%s -> %s, saves %s, memory_refused %s" % (g0.get("stored_protocol"), g1.get("stored_protocol"), g1.get("protocol_saves"), g1.get("memory_refused")))
    st, ring = http("/api/logs/ring", timeout=30)
    text = ring if isinstance(ring, str) else json.dumps(ring)
    e_lines = [ln.strip()[-140:] for ln in text.splitlines() if ln.lstrip("\x1b[0;31m").startswith("E (")]
    check("no E line in the unit's log", not e_lines, e_lines[:3])
    st, h1 = http("/api/restart/history")
    check("the unit rode through", isinstance(h0, dict) and isinstance(h1, dict) and
          h1.get("boot_count") == h0.get("boot_count") + PLANNED[0] and h1.get("unexpected_resets") == h0.get("unexpected_resets"),
          "boot %s -> %s with %d planned" % ((h0 or {}).get("boot_count"), (h1 or {}).get("boot_count") if isinstance(h1, dict) else h1, PLANNED[0]))
    failed = [n for n, ok in CHECKS if not ok]
    print("CAN SEND %s (%d checks, %d failed, %d planned restarts)" % ("PASS" if not failed else "FAIL", len(CHECKS), len(failed), PLANNED[0]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
