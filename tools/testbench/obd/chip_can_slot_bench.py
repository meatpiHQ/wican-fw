#!/usr/bin/env python3
"""MS, CH and single-wire CAN through the OBD chip without a write of its store  ->  CHIP CAN SLOTS PASS / FAIL

The OBD chip reaches the CAN buses beside HS CAN (MS CAN on pins 3 and 11,
CH CAN on 12 and 13, single-wire CAN on pin 1) in three ways, and two of
them write its store, which is rated for a limited number of writes:

  a preset     `STP nn`, `VTP 2Cn`: STORED at every selection, `ATM0` or not
  a link       `ATLNKMS Ph` and its siblings: STORED (kept through a power-off)
  a definition `VTSET_CAN <slot>,<option>,<divisor>,<transceiver>[,<mode>]`:
               the id length and format, the bit rate and the transceiver of
               one of the AT user protocols B to F, in the chip's RAM. Gone
               with a reset; the stored link table is not touched

This bench proves the third on the image and the chip it runs against,
because a car with rows on two buses changes bus at every poll round and
cannot pay a write for it.

  one bus     ECU sim v2 in a one-bus state (MS CAN 11 bit, MS CAN 29 bit,
              CH CAN, single-wire CAN): `ATM0` acknowledged, the definition,
              the link table shows the slot on that transceiver, `ATTP<slot>`,
              the simulator's bus waited for (its interfaces do not all
              answer at once behind a restart: see wait_bus), then `0100`
              answered with the right id; the same request on protocol
              6 (HS CAN) is NOT answered, so the answer came over that
              transceiver; after a warm start the link table and the protocol
              table read as found
  rotation    every bus of the simulator live: the three definitions once,
              then HS, MS, CH and single-wire CAN in turn by `ATTP` alone,
              two requests on each, `--rounds` times. Every request answered;
              the time of a switch, of the first request behind it and of a
              later one are printed as FACT lines
  lifetime    a definition is still there behind `ATD` and `ATPC`, the
              one-argument form deletes that slot alone, a warm start clears
              all of them
  the chip    chip_nvm's snapshot (every readable stored cell, the link table
              among them) the same before and after; the stored protocol and
              the firmware's count of protocol saves as found

No preset, no link word, no `ATSP`: nothing here writes the chip. AutoPID
must be off. The simulator's settings and its single-wire transceiver are
put back as found. With the simulator in a one-bus state the unit's own CAN
node (on the HS pins) counts receive errors and may be demoted to listening:
the bench restarts the unit once at the end when that node is not as found.

usage: chip_can_slot_bench.py [--dut localhost:18081] [--rounds 5]
  the DUT through an ssh tunnel (`ssh -N -L 18081:<dut>:80 rpi001`), ECU sim
  v2 at its own address. About 3 min, at most one planned restart.
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)
import bench_bus  # noqa: E402  (ECU sim v2's one-bus states)
import chip_nvm   # noqa: E402  (the readable stored cells)
import wsmin      # noqa: E402

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
ROUNDS = int(arg("--rounds", "5"))
#: the simulator's one-bus state, the slot, its definition, the transceiver
#: as the link table names it, what ATDP then says, the id of the reply
LEGS = [
    ("ms11", "B", "VTSET_CAN B,81,04,MS_CAN", "MS_CAN", "ISO 15765-4 (CAN 11/125)", "7E8"),
    ("ms29", "B", "VTSET_CAN B,01,04,MS_CAN", "MS_CAN", "ISO 15765-4 (CAN 29/125)", "18 DA F1 10"),
    ("ch11", "C", "VTSET_CAN C,81,01,CH_CAN", "CH_CAN", "ISO 15765-4 (CAN 11/500)", "7E8"),
    ("sw11", "F", "VTSET_CAN F,81,0F,SW_CAN,3", "SW_CAN", "ISO 15765-4 (CAN 11/33)", "7E8"),
]
#: the rotation: the bus, the word that switches to it
ROTATION = [("HS CAN", "ATTP6", "6"), ("MS CAN", "ATTPB", "B"), ("CH CAN", "ATTPC", "C"), ("single-wire CAN", "ATTPF", "F")]
#: every CAN interface of the simulator answering 11 bit OBD, for the rotation
ALL_LIVE = {"enabled": True, "hs_bitrate_kbps": 500, "ms_bitrate_kbps": 125, "ch_bitrate_kbps": 500}
for _b in ("hs", "ms", "ch", "sw"):
    ALL_LIVE.update({_b + "_enabled": True, _b + "_obd11": True, _b + "_obd29": False, _b + "_uds": True})
SIM_GROUPS = ("ecu_sim", "kline", "j1850")
CHECKS = []
PLANNED = [0]


def now():
    return time.strftime("%H:%M:%S")


def say(text):
    print("%s %s" % (now(), text), flush=True)


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


def http(path, method="GET", body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(DUT + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(text)
            except ValueError:
                return r.status, text
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


class Chip:
    """the chip over /ws/obd, the socket polled every 2 ms so that a word's
    time to the prompt can be measured"""

    def __init__(self):
        host, _, port = DUT_HP.partition(":")
        self.ws = wsmin.WS(host, int(port or 80), "/ws/obd", timeout=4)
        self.ws.settimeout(0.002)
        self.timed("", 1.0)
        self.cmd("ATE0")

    def timed(self, text, wait_s=8.0):
        """(answer, ms to the prompt)"""
        self.ws.pump()
        self.ws.send((text + "\r").encode(), wsmin.OP_BIN)
        buf = b""
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < wait_s:
            for _, payload in self.ws.pump():
                buf += payload
            if buf == b">" or buf.endswith((b"\r>", b"\n>")):
                buf = buf[:-1]
                break
        ms = (time.perf_counter() - t0) * 1000
        lines = [ln.strip() for ln in buf.decode(errors="replace").replace("\n", "\r").split("\r")]
        return " | ".join(ln for ln in lines if ln), ms

    def cmd(self, text, wait_s=8.0):
        return self.timed(text, wait_s)[0]

    def warm_start(self):
        self.cmd("ATZ", 4.0)            # the firmware sends it as ATWS
        self.cmd("ATE0")

    def close(self):
        self.ws.close()


def sim_count():
    """the simulator's own count of OBD requests heard and answers sent"""
    st = bench_bus.sim_status() or {}
    return st.get("requests") or 0, st.get("responses") or 0


def heard(before):
    """for a request nobody answered: what the simulator saw of it"""
    time.sleep(0.12)
    after = sim_count()
    return "the simulator heard %d, answered %d" % (after[0] - before[0], after[1] - before[1])


def wait_bus(chip, limit=30.0):
    """The simulator's bus behind one of its restarts. Its interfaces do not
    all answer at once: on 2026-11-11 CH and single-wire CAN took up to 9 s
    while its status already said running (the request was on the bus and
    acknowledged, a listen-only adapter saw it, and the simulator's own
    request counter did not move). That is the rig's matter, so the bench
    waits for it here and judges the chip on the requests after it.
    (seconds to the first answer, "") or (None, why). A request the
    simulator HEARD and the chip has no answer to is not waited for."""
    t0 = time.time()
    while time.time() - t0 < limit:
        c0 = sim_count()
        reply = chip.cmd("0100")
        if "41 00" in reply:
            return time.time() - t0, ""
        time.sleep(0.12)
        c1 = sim_count()
        if c1[0] != c0[0]:
            return None, "%s, and the simulator heard %d, answered %d" % (reply[:24], c1[0] - c0[0], c1[1] - c0[1])
        time.sleep(0.4)
    return None, "no answer in %d s, and the simulator heard none of the requests" % limit


def slot_of(table, slot):
    """the transceiver of one slot in an ATDPCLK answer"""
    for part in table.split("|"):
        name, _, where = part.strip().partition(":")
        if name.strip() == "P" + slot:
            return where.strip()
    return ""


def sim_groups():
    return {g: {k: v for k, v in bench_bus.sim_get(g).items() if k not in ("degraded", "pending_reboot")}
            for g in SIM_GROUPS}


def sim_back(found, tester):
    """the simulator's settings as found (the whole groups: sim_only()'s own
    `previous` holds the keys of one call only), and its transceiver's mode"""
    cur = sim_groups()
    back = {g: {k: v for k, v in found[g].items() if cur[g].get(k) != v} for g in SIM_GROUPS}
    back = {g: ch for g, ch in back.items() if ch}
    if back:
        bench_bus.sim_patch(back)
    bench_bus.sim_tester(swcan=tester.get("swcan", "sleep"))
    return sim_groups() == found


def restart():
    st, h = http("/api/restart/history")
    b0 = h.get("boot_count") if isinstance(h, dict) else None
    http("/api/restart", "POST", {})
    PLANNED[0] += 1
    time.sleep(6)
    t0 = time.time()
    while time.time() - t0 < 150:
        st, h = http("/api/restart/history", timeout=4)
        st2, oc = http("/api/obd_chip", timeout=4)
        if (isinstance(h, dict) and h.get("boot_count") not in (None, b0) and
                isinstance(oc, dict) and oc.get("ready")):
            break
        time.sleep(1)
    time.sleep(3)


def one_bus(leg, found):
    bus, slot, definition, where, text, reply_id = leg
    prev, running = bench_bus.sim_only(bus)
    live = bench_bus.sim_bus_live(running)
    if not check("%s: the simulator answers on that bus alone" % bus, live == [bus], " ".join(live)):
        return
    chip = Chip()
    try:
        chip.cmd("ATH1")
        ok = check("%s: ATM0 acknowledged" % bus, "OK" in chip.cmd("ATM0"))
        ok = ok and check("%s: `%s` taken" % (bus, definition), "OK" in chip.cmd(definition))
        if not ok:
            return
        table = chip.cmd("ATDPCLK")
        check("%s: the link table shows slot %s on %s" % (bus, slot, where), slot_of(table, slot) == where, slot_of(table, slot))
        check("%s: ATTP%s taken, the chip calls it %s" % (bus, slot, text),
              "OK" in chip.cmd("ATTP" + slot) and chip.cmd("ATDP") == text, chip.cmd("ATDP"))
        waited, why = wait_bus(chip)
        if check("%s: the simulator's bus answers behind its restart" % bus, waited is not None,
                 why or "after %.1f s" % waited):
            c0 = sim_count()
            reply, ms = chip.timed("0100")
            check("%s: 0100 answered over slot %s" % (bus, slot), reply.startswith(reply_id) and "41 00" in reply,
                  "%s, %d ms; %s" % (reply[:40], ms, heard(c0)))
        chip.cmd("ATTP6")
        other = chip.cmd("0100")
        check("%s: the same request on protocol 6 (HS CAN) is not answered" % bus, "41 00" not in other, other[:40])
        chip.warm_start()
        after = (chip.cmd("ATDPCLK"), chip.cmd("VT AT_PROTOCOL_ALL"), chip.cmd("ATDPN"))
        check("%s: after a warm start the link table, the protocol table and the protocol as found" % bus,
              after == (found["links"], found["protocols"], found["protocol"]),
              "slot %s on %s, ATDPN %s" % (slot, slot_of(after[0], slot), after[2]))
        chip.cmd("ATPC")
    finally:
        chip.close()


def rotation(found):
    cur = sim_groups()["ecu_sim"]
    want = {k: v for k, v in ALL_LIVE.items() if cur.get(k) != v}
    if want:
        bench_bus.sim_patch({"ecu_sim": want})
    bench_bus.sim_tester(swcan="normal")
    time.sleep(0.3)
    live = bench_bus.sim_bus_live(bench_bus.sim_status())
    if not check("rotation: the simulator's four CAN buses are live", all(b in live for b in ("hs11_500", "ms11", "ch11", "sw11")),
                 " ".join(live)):
        return
    chip = Chip()
    try:
        chip.cmd("ATH1")
        if not check("rotation: ATM0 acknowledged", "OK" in chip.cmd("ATM0")):
            return
        t_def = 0.0
        taken = True
        for definition in ("VTSET_CAN B,81,04,MS_CAN", "VTSET_CAN C,81,01,CH_CAN", "VTSET_CAN F,81,0F,SW_CAN,3"):
            a, ms = chip.timed(definition)
            t_def += ms
            taken = taken and "OK" in a
        check("rotation: the three definitions taken, once", taken, "%d ms together" % t_def)
        ready, notes = True, []
        for name, word, _ in ROTATION:
            chip.cmd(word)
            waited, why = wait_bus(chip)
            ready = ready and waited is not None
            notes.append("%s %s" % (name, why or "after %.1f s" % waited))
        if not check("rotation: every bus of the simulator answers behind its restart", ready, "; ".join(notes)):
            return
        times = {name: {"switch": [], "first": [], "later": []} for name, _, _ in ROTATION}
        asked = answered = wrong_slot = 0
        silent = []                     # round, bus, the two replies, what the simulator saw of the pair
        for rnd in range(ROUNDS):
            for name, word, slot in ROTATION:
                c0 = sim_count()
                a, t_sw = chip.timed(word)
                r1, t1 = chip.timed("0100")
                r2, t2 = chip.timed("010C")
                asked += 2
                answered += ("41 00" in r1) + ("41 0C" in r2)
                if "41 00" not in r1 or "41 0C" not in r2:
                    silent.append((rnd + 1, name, r1[:12], r2[:12], heard(c0)))
                wrong_slot += 0 if ("OK" in a and chip.cmd("ATDPN") == slot) else 1
                times[name]["switch"].append(t_sw)
                times[name]["first"].append(t1)
                times[name]["later"].append(t2)
        check("rotation: every switch taken and the chip on that slot (%d switches)" % (ROUNDS * len(ROTATION)), wrong_slot == 0, wrong_slot)
        check("rotation: every request answered on every bus", answered == asked,
              "%d of %d%s" % (answered, asked, "".join("; round %d %s: %s, %s (%s)" % s for s in silent[:8])))
        for name, _, _ in ROTATION:
            t = times[name]
            med = lambda v: sorted(v)[len(v) // 2]  # noqa: E731
            say("FACT %-16s the switch %3.0f ms, the first request behind it %3.0f ms (longest %3.0f), a later one %3.0f ms" % (
                name + ":", med(t["switch"]), med(t["first"]), max(t["first"]), med(t["later"])))

        # what ends a definition
        chip.cmd("ATD")
        chip.cmd("ATE0")
        chip.cmd("ATPC")
        table = chip.cmd("ATDPCLK")
        check("lifetime: the definitions are still there behind ATD and ATPC",
              (slot_of(table, "B"), slot_of(table, "C")) == ("MS_CAN", "CH_CAN"), table[-60:])
        chip.cmd("VTSET_CAN B")
        table = chip.cmd("ATDPCLK")
        check("lifetime: `VTSET_CAN B` puts slot B back as found and leaves slot C",
              slot_of(table, "B") == slot_of(found["links"], "B") and slot_of(table, "C") == "CH_CAN", table[-60:])
        chip.warm_start()
        after = (chip.cmd("ATDPCLK"), chip.cmd("VT AT_PROTOCOL_ALL"), chip.cmd("ATDPN"))
        check("lifetime: a warm start clears them: the link table, the protocol table and the protocol as found",
              after == (found["links"], found["protocols"], found["protocol"]), after[0][-60:])
        chip.cmd("ATPC")
    finally:
        chip.close()


def main():
    st, ap = http("/api/settings/autopid")
    st2, oc = http("/api/obd_chip")
    if not (isinstance(ap, dict) and isinstance(oc, dict) and oc.get("ready")):
        print("CHIP CAN SLOTS FAIL (the unit does not answer at %s, or its chip is not ready)" % DUT_HP)
        return 1
    if not check("AutoPID is off (its requests would land between the words)", not ap.get("enabled")):
        print("CHIP CAN SLOTS FAIL (switch AutoPID off for the run)")
        return 1
    g0 = oc.get("eeprom_guard", {})
    st, h0 = http("/api/restart/history")
    can0 = bench_bus.dut_can(DUT_HP)
    sim0 = sim_groups()
    tester0 = bench_bus.sim_tester()
    snap0 = chip_nvm.snapshot(DUT_HP)
    chip = Chip()
    found = {"links": chip.cmd("ATDPCLK"), "protocols": chip.cmd("VT AT_PROTOCOL_ALL"), "protocol": chip.cmd("ATDPN")}
    chip.cmd("ATPC")
    chip.close()
    say("as found: the chip %s on protocol %s, store %s; links %s" % (
        (oc.get("fw") or {}).get("chip"), found["protocol"], g0.get("stored_protocol"), found["links"]))

    try:
        for leg in LEGS:
            one_bus(leg, found)
        rotation(found)
    finally:
        check("the simulator's settings as found", sim_back(sim0, tester0))

    can1 = bench_bus.dut_can(DUT_HP)
    if isinstance(can0, dict) and isinstance(can1, dict) and can1.get("state") != can0.get("state"):
        say("the unit's own CAN node is %s (as found %s; %s receive errors, counted in the one-bus states): a planned restart" % (
            can1.get("state"), can0.get("state"), can1.get("rx_errors")))
        restart()
        can1 = bench_bus.dut_can(DUT_HP)
    check("the unit's own CAN node as found", isinstance(can1, dict) and can1.get("state") == (can0 or {}).get("state"),
          (can1 or {}).get("state") if isinstance(can1, dict) else can1)
    snap1 = chip_nvm.snapshot(DUT_HP)
    diff = chip_nvm.diff(snap0, snap1)
    check("the chip's stored cells the same before and after (%d cells)" % len(snap0), not diff, [w for w, _, _ in diff])
    st, oc1 = http("/api/obd_chip")
    g1 = oc1.get("eeprom_guard", {}) if isinstance(oc1, dict) else {}
    check("the chip's stored protocol as found, no protocol save", g1.get("stored_protocol") == g0.get("stored_protocol") and
          g1.get("protocol_saves") == (0 if PLANNED[0] else g0.get("protocol_saves")),
          "%s -> %s, protocol_saves %s" % (g0.get("stored_protocol"), g1.get("stored_protocol"), g1.get("protocol_saves")))
    st, h1 = http("/api/restart/history")
    check("the unit rode through", isinstance(h0, dict) and isinstance(h1, dict) and
          h1.get("boot_count") == h0.get("boot_count") + PLANNED[0] and
          h1.get("unexpected_resets") == h0.get("unexpected_resets"),
          "boot %s -> %s with %d planned" % ((h0 or {}).get("boot_count"), (h1 or {}).get("boot_count"), PLANNED[0]))
    failed = [n for n, ok in CHECKS if not ok]
    print("CHIP CAN SLOTS %s (%d one-bus legs, %d rounds of %d buses, %d checks, %d failed, %d planned restarts)" % (
        "PASS" if not failed else "FAIL", len(LEGS), ROUNDS, len(ROTATION), len(CHECKS), len(failed), PLANNED[0]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
