#!/usr/bin/env python3
"""The CAN transmitter after bus-off, again and again -> CAN BUSOFF SLOTS PASS.

The CAN driver keeps a frame in one of 16 slots from the transmit call to
the interrupt that ends its attempts. A frame that is inside the controller
at the moment the controller goes bus-off gets no such interrupt, so its
slot stayed taken until the CAN node was next restarted, and the frame was
counted neither as sent nor as lost: `tx` ran ahead of `tx_done` +
`tx_lost` by one with every bus-off. Sixteen bus-offs without a restart of
the node in between and the unit transmits nothing.

The same rounds found a second defect on 2026-11-18: the link policy takes
a talking node off a bus it cannot read and starts it again (`link_demotions`
on /api/can), and a node deleted right behind a transmission left the
driver's deferred event running on freed memory. The unit was reset by its
interrupt watchdog in round 16 of the first run. So the bench also counts
those restarts of the node and asserts that the unit never reset.

How a real bus-off is made (the receive side stays readable, so the link
policy does not take the node off the bus first): the PCAN keeps the bus
full of id 0x000 frames with eight 00 bytes while the unit sends id 0x000
with eight FF bytes. Both start together and tie in the arbitration; at the
first data bit the unit sends recessive, reads dominant and raises an
error: +8 on its transmit error counter each time, bus-off at 256, after
32 attempts (8 frames, each tried 4 times). The PCAN's frames between two
of the unit's attempts go through, so the unit keeps reading good frames.

Legs (each check prints PASS/FAIL; the verdict is the last line):
  B  before: the unit transmits (one frame, PCAN sees it) and has no frame
     in flight: `tx` = `tx_done` + `tx_lost` on /api/can.
  O  --rounds bus-offs, one after the other. After each: the controller is
     back on the bus by itself, and every frame the unit took is accounted
     for: `tx` = `tx_done` + `tx_lost`, read half a second after the storm.
     The unit answers all through it (no reset).
  T  after the last round: 20 frames written one by one all reach the bus.
  E  the firmware reports each bus-off with one E line: exactly --rounds
     new ones, no other.
  R  no reset of its own. A unit left without transmit slots (a firmware
     before the fix) is restarted, as a planned restart, so the bench
     leaves it able to transmit.

  python can_busoff_slots_bench.py --dut localhost:18081
         [--pcan PCAN_USBBUS2] [--rounds 18]
The tunnel to the unit's port 80 is the caller's. Needs python-can on the
PC and the PCAN on the unit's bus. ECU sim v2 stays on the bus: it
acknowledges, and it is not touched.
"""
import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from dp_bench import Checks, Dut  # noqa: E402
from wsmin import WS, OP_TEXT  # noqa: E402

ck = Checks("CAN BUSOFF SLOTS")


class Console:
    """one /ws/cli connection kept open: a `can send` every few ms"""

    def __init__(self, dut):
        self.ws = WS(dut.host, dut.port, "/ws/cli", timeout=8)
        self.ws.settimeout(2)

    def run(self, line):
        """the command's verdict: 'OK', 'ERROR' or '' (no answer in time)"""
        self.ws.send((line + "\n").encode(), opcode=OP_TEXT)
        out = ""
        end = time.time() + 3
        while time.time() < end:
            try:
                _, payload = self.ws.recv_frame()
            except Exception:
                break
            out += payload.decode("utf-8", "replace")
            if "OK" in out or "ERROR" in out:
                break
        return "ERROR" if "ERROR" in out else "OK" if "OK" in out else ""

    def close(self):
        self.ws.close()


class Flood(threading.Thread):
    """the PCAN keeps the bus full of id 0x000, eight 00 bytes"""

    def __init__(self, can, channel):
        super().__init__(daemon=True)
        self.can = can
        self.bus = can.Bus(interface="pcan", channel=channel, bitrate=500000, auto_reset=True)
        self.halt = threading.Event()
        self.sent = 0
        self.start()

    def run(self):
        msg = self.can.Message(arbitration_id=0x000, is_extended_id=False, data=bytes(8))
        while not self.halt.is_set():
            try:
                self.bus.send(msg, timeout=0.02)
                self.sent += 1
            except Exception:
                time.sleep(0.002)   # its own error counter while the unit's flags hit it

    def close(self):
        self.halt.set()
        self.join(timeout=3)
        self.bus.shutdown()


def in_flight(st):
    """frames the unit took that are neither sent nor counted lost"""
    return st["tx"] - st["tx_done"] - st["tx_lost"]


def reconnect(dut):
    """a console after the unit dropped the old one (it reset): None when
    the unit does not come back"""
    if not dut.wait_up(90):
        return None
    time.sleep(3)
    try:
        return Console(dut)
    except Exception:
        return None


def settled(dut, secs=3.0):
    """/api/can once the transmit counters stopped moving"""
    last = None
    end = time.time() + secs
    while time.time() < end:
        st = dut.get("/api/can")
        if st and last and (st["tx"], st["tx_done"], st["tx_lost"]) == (last["tx"], last["tx_done"], last["tx_lost"]):
            return st
        last = st
        time.sleep(0.25)
    return last


def one_bus_off(a, dut, can, con, rnd):
    """(the bus-off happened, /api/can after it)"""
    off0 = dut.get("/api/can")["bus_off"]
    flood = Flood(can, a.pcan)
    sent = 0
    st = None
    try:
        time.sleep(0.15)                # the bus is full before the unit's first frame
        end = time.time() + 12
        while time.time() < end:
            for _ in range(4):
                con.run("can send 000 FFFFFFFFFFFFFFFF")
                sent += 1
            st = dut.get("/api/can")
            if st and st["bus_off"] > off0:
                break
    finally:
        flood.close()
    hit = bool(st) and st["bus_off"] > off0
    # the controller comes back by itself once the bus is quiet
    end = time.time() + 6
    while time.time() < end:
        st = dut.get("/api/can")
        if st and st.get("state") != "bus_off":
            break
        time.sleep(0.2)
    time.sleep(0.5)
    st = settled(dut)
    print("   round %d: bus_off %d -> %d after %d frames of the unit; state %s, tx %d = done %d + lost %d "
          "+ unaccounted %d, node restarts by the link policy so far %d" % (
              rnd, off0, st["bus_off"], sent, st["state"], st["tx"], st["tx_done"],
              st["tx_lost"], in_flight(st), st["link_demotions"]), flush=True)
    return hit, st


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--pcan", default=os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"))
    ap.add_argument("--rounds", type=int, default=18)
    a = ap.parse_args()
    import can

    dut = Dut(a.dut)
    h0 = dut.health(wait_s=40)
    if h0["version"] is None:
        print("CAN BUSOFF SLOTS FAIL: no unit at", a.dut)
        return 1
    st0 = dut.get("/api/can") or {}
    print("unit: %s boot_count=%s unexpected=%s log_errors=%s; can: state=%s baud=%s silent=%s bus_off=%s" % (
        h0["version"], h0["boot_count"], h0["unexpected_resets"], h0["log_errors"], st0.get("state"),
        st0.get("baud_kbps"), st0.get("silent"), st0.get("bus_off")), flush=True)
    if st0.get("state") != "running" or st0.get("baud_kbps") != 500 or st0.get("listen_only"):
        print("CAN BUSOFF SLOTS FAIL: the unit's CAN interface must be running at 500 kbit/s and may talk")
        return 1
    planned = 0
    con = Console(dut)

    # ---- B: before ------------------------------------------------------------------
    print("== B: before", flush=True)
    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    try:
        con.run("can send 7AA CAFEBABE")
        got = None
        end = time.time() + 3
        while time.time() < end and got is None:
            m = bus.recv(timeout=0.3)
            if m is not None and m.arbitration_id == 0x7AA:
                got = bytes(m.data)
    finally:
        bus.shutdown()
    ck.check("B1 the unit transmits and the PCAN is on its bus", got == bytes.fromhex("CAFEBABE"), got and got.hex())
    st = settled(dut)
    ck.check("B2 no frame in flight before the first bus-off", in_flight(st) == 0,
             {"tx": st["tx"], "tx_done": st["tx_done"], "tx_lost": st["tx_lost"]})
    base = in_flight(st)

    # ---- O: the bus-offs --------------------------------------------------------------
    print("== O: %d bus-offs" % a.rounds, flush=True)
    hits, left, demoted, rnd = 0, [], 0, 0
    boots = h0["boot_count"]
    try:
        for rnd in range(1, a.rounds + 1):
            hit, st = one_bus_off(a, dut, can, con, rnd)
            now = dut.health()
            if now["boot_count"] != boots:
                raise RuntimeError("the unit reset (boot_count %s -> %s)" % (boots, now["boot_count"]))
            hits += 1 if hit else 0
            left.append(in_flight(st) - base)
            demoted = st["link_demotions"] - st0.get("link_demotions", 0)
    except Exception as e:
        ck.check("O0 the unit answered through every round", False,
                 "round %d: %s: %s" % (rnd, type(e).__name__, e))
        con = reconnect(dut)
        if con is None:
            print("CAN BUSOFF SLOTS FAIL: the unit did not come back")
            return 1
        report = (dut.get("/api/restart/history") or {}).get("report") or {}
        print("   the unit's last crash on record: %s" % ((report.get("crash") or {}).get("summary")), flush=True)
    st = dut.get("/api/can") or {}
    ck.check("O1 every round ended in a real bus-off (%d of %d)" % (hits, a.rounds), hits == a.rounds,
             {"link_demotions": demoted})
    ck.check("O2 the controller came back by itself each time", st.get("state") in ("running", "recovering"),
             st.get("state"))
    ck.check("O3 after every bus-off each frame is sent or counted lost (tx = tx_done + tx_lost)",
             bool(left) and max(left) == 0 and min(left) == 0, {"unaccounted_after_each_round": left})

    # ---- T: the transmitter after it ---------------------------------------------------
    print("== T: 20 frames after the last bus-off", flush=True)
    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    seen, verdicts = [], []
    try:
        time.sleep(0.5)
        for i in range(20):
            verdicts.append(con.run("can send 7AB %02X%02X" % (0xB0, i)))
            time.sleep(0.02)
        end = time.time() + 3
        while time.time() < end and len(seen) < 20:
            m = bus.recv(timeout=0.3)
            if m is not None and m.arbitration_id == 0x7AB:
                seen.append(m.data[1])
    except Exception as e:
        print("   leg T stopped: %s: %s" % (type(e).__name__, e), flush=True)
    finally:
        bus.shutdown()
    ck.check("T1 all 20 frames reach the bus, in order", seen == list(range(20)),
             {"on_the_bus": len(seen), "console_said": {v: verdicts.count(v) for v in set(verdicts)}})

    # ---- E, R ----------------------------------------------------------------------------
    print("== E, R: the log and the unit", flush=True)
    h1 = dut.health()
    st = dut.get("/api/can") or {}
    offs = (st.get("bus_off") or 0) - (st0.get("bus_off") or 0)
    if h1["boot_count"] == h0["boot_count"]:
        ck.check("E1 one E line for each bus-off and no other (%d)" % offs,
                 h1["log_errors"] == (h0["log_errors"] or 0) + offs, (h0["log_errors"], h1["log_errors"]))
    else:
        ck.skip("E1 one E line for each bus-off", "the unit reset: its counters began again")
    con.close()
    st = settled(dut)
    if h1["boot_count"] == h0["boot_count"] and in_flight(st) - base != 0:
        # a firmware before the fix: give the unit its slots back
        print("   %d slot(s) never came back: restarting the unit so it can transmit again" % (in_flight(st) - base),
              flush=True)
        dut.api("/api/restart", "POST", {})
        planned += 1
        time.sleep(8)
        dut.wait_up()
        time.sleep(3)
    h2 = dut.health()
    ck.check("R1 only the planned restarts (%d), no reset of its own" % planned,
             h2["boot_count"] == h0["boot_count"] + planned and h2["unexpected_resets"] == h0["unexpected_resets"],
             {"boot_count": (h0["boot_count"], h2["boot_count"]),
              "unexpected": (h0["unexpected_resets"], h2["unexpected_resets"])})
    return ck.finish()


if __name__ == "__main__":
    sys.exit(main())
