#!/usr/bin/env python3
"""The Bluetooth data pipe under overload: nothing cut, nothing lost uncounted,
the central answered all through it.

Runs on the PC (python-can, the PCAN on the unit's bus). The central is
rpi001 (ble_overload_pi.py, started through ble_pi_run.py); this side owns the
bus and makes the verdict. The bridge is `can <-slcan-> ble` on the data pipe
(FFF1 notify, FFF2 write), slcan text, one line per frame. ECU sim v2 is made
quiet for the run and AutoPID is switched off, so the bus carries the bench's
frames alone; both are put back.

  leg S  the control: five lines written on a quiet bus reach it (S0), which
         also shows that the PCAN hears the unit.
  leg T  more frames from the bus than the link carries: the PCAN sends
         frame 0x123 with 8 data bytes, its number in the first four, 2,000 a
         second for 12 s (44 KB a second of slcan text, 22 bytes a line).
         T0  the unit's CAN interface received every frame, and the bridge
             took every one
         T1  the link was overloaded (frames did not arrive), or the leg
             proves nothing
         T2  what arrived is whole lines only: nothing that is no slcan line,
             no line that is not the flood's, the numbers rising
         T3  every byte that did not arrive is counted by the unit
             (`pipe.tx_drop_bytes` + `pipe.notify_drop_bytes` on /api/ble)
         T4  a line written every half second all through the flood is
             answered within 2 s each time, and its frame reaches the bus
  leg R  writes while the unit cannot send: the PCAN keeps the bus full with
         frame 0x001, which wins every arbitration (and the pipe toward the
         central is overloaded by that same flood), and the central writes
         60 times 35 slcan lines for frame 0x700 to FFF2 WITH response.
         R0  every write was answered, none with an error
         R1  every line of every write was handed to the CAN interface
             (`tx` on /api/can)
         R2  each of those frames is on the bus (`tx_done`, and the PCAN saw
             as many) or in the interface's loss counter (`tx_lost`), and
             nothing else is on the bus
         R4  the link stayed up through both legs

Usage:
  python tools/testbench/ble/ble_overload_bench.py --dut-ip <the unit, as rpi001 sees it>
         [--pcan PCAN_USBBUS2] [--legs str] [--t-fps 2000] [--t-secs 12] [--r-writes 60]
Expected final line: BLE OVERLOAD PASS
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import threading
import time

import can

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import bench_bus  # noqa: E402

FLOOD_ID, HOLD_ID, WRITE_ID = 0x123, 0x001, 0x700
LINE_LEN = 22           # leg T: `t1238` + 16 hex digits + CR
WIRE_LEN = 18           # the same frame as the bridge counts it on its CAN side (10 + dlc)
S_BASE, T_BASE = 1000000, 2000000   # the numbers of the central's own lines in legs S and T
OURS = "%X/4" % WRITE_ID
SIM_HS_QUIET = ("hs_obd11", "hs_obd29", "hs_uds", "hs_j1939", "hs_broadcast")

fails = []
passes = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name + ("  [%s]" % (detail,) if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


class Flood(threading.Thread):
    """Frames from the PCAN: `fps` a second, or as fast as it takes them."""

    def __init__(self, bus, can_id, dlc, fps):
        super().__init__(daemon=True)
        self.bus, self.can_id, self.dlc, self.fps = bus, can_id, dlc, fps
        self.halt = threading.Event()
        self.sent = 0

    def run(self):
        t0 = time.perf_counter()
        pad = bytes(self.dlc - 4)
        while not self.halt.is_set():
            if self.fps and self.sent >= int((time.perf_counter() - t0) * self.fps):
                time.sleep(0.0003)
                continue
            msg = can.Message(arbitration_id=self.can_id, is_extended_id=False,
                              data=struct.pack("<I", self.sent) + pad)
            try:
                self.bus.send(msg, timeout=0.2)
                self.sent += 1
            except can.CanError:
                time.sleep(0.001)          # the adapter's queue is full

    def stop(self):
        self.halt.set()
        self.join(5.0)
        return self.sent


class Sniffer(threading.Thread):
    """Everything on the bus that the PCAN did not send itself."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.halt = threading.Event()
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.frames, self.rec_bytes, self.ids, self.nums = 0, 0, {}, []

    def snapshot(self):
        with self.lock:
            return {"frames": self.frames, "rec_bytes": self.rec_bytes, "ids": dict(self.ids),
                    "nums": list(self.nums)}

    def run(self):
        while not self.halt.is_set():
            try:
                m = self.bus.recv(timeout=0.05)
            except can.CanError:
                continue
            if m is None or m.is_error_frame:
                continue
            with self.lock:
                self.frames += 1
                self.rec_bytes += 10 + (0 if m.is_remote_frame else m.dlc)
                key = "%X/%d" % (m.arbitration_id, m.dlc)
                self.ids[key] = self.ids.get(key, 0) + 1
                if m.arbitration_id == WRITE_ID and m.dlc == 4 and not m.is_extended_id:
                    self.nums.append(struct.unpack("<I", bytes(m.data))[0])


def run_pi(args, on_line=None):
    cmd = [sys.executable, "-u", os.path.join(HERE, "ble_pi_run.py"), "ble_overload_pi.py"] + args
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    for line in p.stdout:
        line = line.rstrip("\r\n")
        print("  pi| " + line, flush=True)
        if on_line:
            on_line(line)
    return p.wait()


def judge_s(s, sniff):
    check("S0 the control: five lines written on a quiet bus reach it, and the PCAN hears the unit",
          s["acked"] == 5 and sniff["nums"] == [S_BASE + i for i in range(5)] and list(sniff["ids"]) == [OURS],
          {"acked": s["acked"], "on_the_bus": sniff["nums"], "ids": sniff["ids"]})


def judge_t(t, sent, sniff):
    lost = sent - t["flood_lines"]
    handed = t["bridge"].get("a2b_bytes", 0) / WIRE_LEN
    others = {k: v for k, v in sniff["ids"].items() if k != OURS}
    check("T0 the unit's CAN interface received every frame and the bridge took every one",
          t["can_rx"] == sent and handed == sent and not others,
          {"sent": sent, "others_on_the_bus": others, "can_rx": t["can_rx"], "bridge_took": handed})
    check("T1 the link was overloaded", lost > 0,
          {"sent": sent, "arrived": t["flood_lines"], "lost": lost, "link_bytes_per_s": t["link_bytes_per_s"]})
    check("T2 what arrived is whole lines only, the flood's, the numbers rising",
          t["malformed"] == 0 and t["foreign"] == 0 and t["leftover"] == 0 and t["in_order"]
          and t["unique"] == t["flood_lines"],
          {k: t[k] for k in ("malformed", "samples", "foreign", "leftover", "in_order", "unique", "flood_lines")})
    wrote = [T_BASE + i for i in range(t["writes"])]
    check("T4 a line written every half second through the flood is answered within 2 s, its frame on the bus",
          t["writes"] > 0 and t["acked"] == t["writes"] and (t["slowest_ack_s"] or 99) < 2.0
          and sniff["nums"] == wrote,
          {"writes": t["writes"], "acked": t["acked"], "slowest_ack_s": t["slowest_ack_s"],
           "on_the_bus": len(sniff["nums"]), "errors": t["write_errors"]})
    made = int(handed) * LINE_LEN if not others and handed == int(handed) else None
    counted = t["pipe"].get("tx_drop_bytes", 0) + t["pipe"].get("notify_drop_bytes", 0)
    check("T3 every byte that did not arrive is counted by the unit",
          t["pipe_has_counters"] and made is not None and made - t["bytes"] == counted,
          {"made": made, "arrived": t["bytes"], "not_arrived": None if made is None else made - t["bytes"],
           "counted": counted, "pipe": t["pipe"]})


def judge_r(r, sniff):
    lines = r["writes"] * r["per_write"]
    nums = sniff["nums"]
    others = {k: v for k, v in sniff["ids"].items() if k != OURS}
    can = r["can"]
    check("R0 every write was answered while the unit could not send, none with an error",
          r["writes"] > 0 and not r["refused"],
          {"writes": r["writes"], "errors": r["errors"], "slowest_ack_s": r["slowest_ack_s"],
           "lines_per_s": r["lines_per_s"]})
    check("R1 every line of every write was handed to the CAN interface",
          can.get("tx") == lines, {"lines": lines, "can": can})
    check("R2 each frame is on the bus or in the interface's loss counter, nothing else on the bus",
          len(nums) == len(set(nums)) == can.get("tx_done", -1)
          and can.get("tx_done", 0) + can.get("tx_lost", 0) == can.get("tx", -1) and not others,
          {"on_the_bus": len(nums), "tx_done": can.get("tx_done"), "tx_lost": can.get("tx_lost"),
           "other_ids": others})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut-ip", required=True, help="the unit's address as rpi001 sees it")
    ap.add_argument("--pcan", default=os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"))
    ap.add_argument("--kbit", type=int, default=500)
    ap.add_argument("--legs", default="str")
    ap.add_argument("--t-fps", type=int, default=2000)
    ap.add_argument("--t-secs", type=float, default=12.0)
    ap.add_argument("--r-writes", type=int, default=60)
    ap.add_argument("--keep-sim", action="store_true")
    ap.add_argument("--log-level", default="",
                    help="the unit's log levels for the run, tag=level[,tag=level] ('*' = every tag), "
                         "to see what the console costs: '*=none', 'NimBLE=info'")
    ap.add_argument("--btmon", action="store_true",
                    help="an HCI trace on the Pi: what the central's radio did with its writes")
    ap.add_argument("--stage", choices=["all", "configure", "ble", "restore"], default="all")
    a = ap.parse_args()
    t_start = time.time()
    sim_found = None
    ovl = {}
    marks = {}
    state = {"flood": None}

    try:
        if not a.keep_sim and a.stage in ("all", "ble"):
            cfg = bench_bus.sim_get()
            sim_found = {k: cfg[k] for k in SIM_HS_QUIET if k in cfg}
            print("the simulator's HS CAN as found: %s" % sim_found, flush=True)
            bench_bus.sim_patch({"ecu_sim": {k: False for k in sim_found}})
        if a.stage in ("all", "configure"):
            check("the configure stage ran", run_pi(["--dut", a.dut_ip, "--stage", "configure"]) == 0)
        if a.stage in ("all", "ble"):
            bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=a.kbit * 1000)
            sniff = Sniffer(bus)
            sniff.start()

            def on_line(line):
                if line.startswith("OVL "):
                    _, name, body = line.split(" ", 2)
                    ovl[name] = json.loads(body)
                elif line == "MARK s_on":
                    sniff.reset()
                elif line == "MARK t_on":
                    sniff.reset()
                    state["flood"] = Flood(bus, FLOOD_ID, 8, a.t_fps)
                    state["flood"].start()
                elif line == "MARK r_on":
                    sniff.reset()
                    state["flood"] = Flood(bus, HOLD_ID, 8, None)
                    state["flood"].start()
                elif line in ("MARK t_off", "MARK r_off", "MARK stop"):
                    if state["flood"] is not None:
                        marks[line.split()[1] + "_sent"] = state["flood"].stop()
                        state["flood"] = None
                elif line in ("MARK s_end", "MARK t_end", "MARK r_end"):
                    marks[line.split()[1]] = sniff.snapshot()

            try:
                rc = run_pi(["--dut", a.dut_ip, "--stage", "ble", "--legs", a.legs,
                             "--t-secs", str(a.t_secs), "--r-writes", str(a.r_writes)]
                            + (["--btmon"] if a.btmon else [])
                            + (["--log-level", "'%s'" % a.log_level] if a.log_level else []), on_line)
            finally:
                if state["flood"] is not None:
                    state["flood"].stop()
                sniff.halt.set()
                sniff.join(2.0)
                bus.shutdown()
            print("the PCAN: %s" % {k: (v if not isinstance(v, dict) else
                                        {"frames": v["frames"], "ids": v["ids"]}) for k, v in marks.items()},
                  flush=True)
            check("the central's stage ran to the end", rc == 0 and "END" in ovl and "ERROR" not in ovl,
                  ovl.get("ERROR", ovl.get("END", "no END line")))
            if "S" in ovl and "s_end" in marks:
                judge_s(ovl["S"], marks["s_end"])
            elif "s" in a.legs:
                check("leg S ran", False)
            if "T" in ovl and "t_end" in marks:
                judge_t(ovl["T"], marks.get("t_off_sent", 0), marks["t_end"])
            elif "t" in a.legs:
                check("leg T ran", False)
            if "R" in ovl and "r_end" in marks:
                judge_r(ovl["R"], marks["r_end"])
            elif "r" in a.legs:
                check("leg R ran", False)
            check("R4 the link stayed up through both legs", bool(ovl.get("END", {}).get("connected")),
                  ovl.get("END", ovl.get("ERROR", "")))
    except Exception as e:  # noqa: BLE001
        check("the bench ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        if sim_found is not None:
            try:
                bench_bus.sim_patch({"ecu_sim": sim_found})
            except Exception as e:  # noqa: BLE001
                check("the simulator is back as found", False, "%s: %s" % (type(e).__name__, e))
        if a.stage in ("all", "restore"):
            check("the restore stage ran (settings as found, no new fault, no unexpected reset)",
                  run_pi(["--dut", a.dut_ip, "--stage", "restore"]) == 0)

    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t_start), flush=True)
    if fails:
        print("BLE OVERLOAD FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("BLE OVERLOAD PASS")


if __name__ == "__main__":
    main()
