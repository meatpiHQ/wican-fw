#!/usr/bin/env python3
"""CAN over MQTT with a small batch on a busy bus -> MQTT CAN BATCH PASS.

A broker client parses every MQTT message as one JSON document. Since the
bridge gathers a translator's output into one send (2026-11-17), two batches
that are finished in the same pass of the bridge leave together: with a
small `batch_frames` that is one MQTT message holding two JSON objects back
to back, which a JSON parser refuses. The default batch of 24 frames is
longer than what the bridge gathers, so it never met this.

Legs (each check prints PASS/FAIL; the verdict is the last line):
  A  for each tier "<batch_frames>@<frames a second>[x<group>]" (one
     restart each): mqtt_can on with both gates open and br_mqtt_can = can
     <-canmqtt-> mqtt0; PCAN puts exactly N frames of id 0x123 on the bus,
     a counter in the data, in groups of <group> back to back;
     mosquitto_sub on the Pi keeps every message. The first tier, one
     frame a message and three frames at a time, puts three objects into
     every send of a firmware before the fix.
     A1 every MQTT message is ONE JSON object.
     A2 every frame is in a message, once and in order, or in a counter
        that a route of the web API shows.
     A3 no loss counter of the data path routes moved without a frame lost.
  T  the other way: the documented tx message reaches the bus (PCAN).
  C  a loss on the way in: one tx message longer than the reassembler
     takes. The console command `mqtt_can` counts it (tx-oversize); the
     same count must be on /api/bridges, in the bridge row's `losses` for
     the end that is `mqtt0`. And every running bridge reports what the
     jacks at its two ends lost.
  R  settings back as found (one restart), planned restarts only, no reset
     of its own, no new E line.

  python mqtt_can_batch_bench.py --dut localhost:18081 --pi rpi001
         [--pcan PCAN_USBBUS2] [--tiers 1@300x3,4@2000,24@2000] [--n 3000]
The tunnel to the unit's port 80 is the caller's (ssh -N -L
18081:<unit>:80 rpi001). Needs python-can on the PC and mosquitto with its
clients on the Pi, the broker the unit's mqtt_manager already points at.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from dp_bench import Checks, Dut, Staged, moved  # noqa: E402

GEN_ID = 0x123
PUB = "benchmcb/can/rx"
SUB = "benchmcb/can/tx"
ck = Checks("MQTT CAN BATCH")


def pi(host, cmd, timeout=120, stdin=None):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", host, cmd], input=stdin, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.stdout


def publish(host, body):
    """one MQTT message on the unit's tx topic, the body on stdin (no quoting)"""
    pi(host, "mosquitto_pub -h localhost -t '%s' -s" % SUB, stdin=body)


def console_counters(dut):
    """the counters of the console command `mqtt_can`"""
    out = dut.cli("mqtt_can", quiet_s=1.2)
    st = {}
    for pat, keys in ((r"frames rx (\d+) \(batches (\d+)\)\s+tx (\d+)", ("rx", "batches", "tx")),
                      (r"rx-gate (\d+)\s+tx-gate (\d+)\s+q-full (\d+)\s+parse-err (\d+)",
                       ("rx_gate", "tx_gate", "q_full", "parse_err")),
                      (r"tx-cut (\d+)\s+tx-oversize (\d+)\s+rx-batch (\d+)",
                       ("tx_cut", "tx_oversize", "rx_batch"))):
        m = re.search(pat, out)
        if m:
            st.update({k: int(v) for k, v in zip(keys, m.groups())})
    return st


def objects_in(payload):
    """the JSON objects of one MQTT payload, back to back: (objects, clean)"""
    dec = json.JSONDecoder()
    objs, pos, clean = [], 0, True
    while pos < len(payload):
        while pos < len(payload) and payload[pos] in " \r\n\t":
            pos += 1
        if pos >= len(payload):
            break
        try:
            obj, pos = dec.raw_decode(payload, pos)
        except ValueError:
            clean = False
            break
        objs.append(obj)
    return objs, clean


class Subscriber(threading.Thread):
    """mosquitto_sub on the Pi for `secs`, every message a line. `up` is set
    once the shell that starts it runs there: an ssh to the Pi does not
    begin at once (2026-11-18: the lookup of the Pi's name took 7 s on the
    PC, and a subscriber that was assumed to stand after 2.5 s missed the
    first three seconds of frames, 951 of 3000)."""

    def __init__(self, host, topic, secs):
        super().__init__(daemon=True)
        self.up = threading.Event()
        self.lines = []
        self.p = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", host,
             "echo SUBSCRIBER_UP; exec timeout %d mosquitto_sub -h localhost -t '%s' 2>/dev/null" % (secs, topic)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace")
        self.start()

    def run(self):
        for ln in self.p.stdout:
            if not self.up.is_set() and ln.strip() == "SUBSCRIBER_UP":
                self.up.set()
                continue
            self.lines.append(ln)
        self.p.wait()


def storm(bus, can, n, fps, burst=1):
    """exactly n frames of GEN_ID, a 16-bit counter in bytes 0..1, in groups
    of `burst` back to back (a group reaches the unit as one chunk of the
    can jack, so the bridge runs the codec on all of it in one pass)"""
    period = 1.0 / fps
    sent = 0
    t0 = time.perf_counter()
    for i in range(n):
        m = can.Message(arbitration_id=GEN_ID, is_extended_id=False,
                        data=[(i >> 8) & 255, i & 255, 3, 4, 5, 6, 7, 8])
        try:
            bus.send(m, timeout=0.5)
            sent += 1
        except can.CanError as e:
            print("pcan: send error:", e, flush=True)
            break
        if (i + 1) % burst:
            continue
        wait = t0 + (i + 1) * period - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
    return sent


def tier(a, dut, st, can, frames, fps, burst):
    tag = "A[%d frames a batch, %d frames/s%s]" % (frames, fps, " in groups of %d" % burst if burst > 1 else "")
    print("== " + tag, flush=True)
    mc = st.copy("mqtt_can")
    mc.update({"enabled": True, "allow_rx": True, "allow_tx": True, "pub_topic": PUB,
               "sub_topic": SUB, "batch_ms": 50, "batch_frames": frames, "filter": "", "mask": ""})
    bm = st.copy("bridge_manager")
    bm["bridges"] = [b for b in bm["bridges"] if b.get("name") != "br_mqtt_can"]
    bm["bridges"].append({"name": "br_mqtt_can", "a": "can", "b": "mqtt0",
                          "translator": "canmqtt", "enabled": True})
    st.submit({"mqtt_can": mc, "bridge_manager": bm}, "batch_frames %d" % frames)
    end = time.time() + 30
    while time.time() < end and not ((dut.get("/api/status") or {}).get("bits") or {}).get("mqtt_connected"):
        time.sleep(1)
    row = next((b for b in (dut.get("/api/bridges") or {}).get("bridges", []) if b["name"] == "br_mqtt_can"), {})
    if not ck.check(tag + " the bridge is up and the broker connected", row.get("up") is True, row.get("reason", "")):
        return
    secs = a.n / float(fps) + 14    # the subscription, the counters read before, the tail
    sub = Subscriber(a.pi, PUB, secs)
    if not sub.up.wait(40):
        ck.check(tag + " the subscriber on the Pi started", False, "no word from its shell in 40 s")
        return
    time.sleep(1.5)                 # mosquitto_sub is subscribed: the broker is on the same host
    l0, c0 = dut.losses(), console_counters(dut)
    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    try:
        sent = storm(bus, can, a.n, fps, burst)
    finally:
        bus.shutdown()
    sub.join(timeout=secs + 40)
    l1, c1 = dut.losses(), console_counters(dut)
    msgs = [ln for ln in sub.lines if ln.strip()]
    several, broken, counters = 0, 0, []
    example = ""
    for m in msgs:
        objs, clean = objects_in(m)
        if not clean:
            broken += 1
        if len(objs) != 1 and not example:
            example = "%d objects in one message of %d bytes: %s ... %s" % (len(objs), len(m), m[:60], m[-40:])
        several += 1 if len(objs) > 1 else 0
        for o in objs:
            for f in o.get("frame", []) if isinstance(o, dict) else []:
                if f.get("id") == GEN_ID:
                    counters.append((f["data"][0] << 8) | f["data"][1])
    ck.check(tag + " A1 every MQTT message is one JSON object", bool(msgs) and several == 0 and broken == 0,
             {"messages": len(msgs), "with_several_objects": several, "not_json": broken, "example": example})
    in_order = counters == sorted(set(counters))
    lost = sent - len(set(counters))
    missing = sorted(set(range(sent)) - set(counters))
    # this path's counters: its bridge, mqtt_can's own, the CAN interface
    # (another bridge's row counts its own traffic, AutoPID's for one)
    mv = {k: v for k, v in moved(l0, l1).items() if "br_mqtt_can" in k or ".mqtt_can." in k or "/api/can" in k}
    counted = sum(v[1] - (v[0] or 0) for v in mv.values())
    ck.check(tag + " A2 every frame arrived once and in order, or is in a counter of a route",
             in_order and len(counters) == len(set(counters)) and (lost == 0 or 0 < lost <= counted),
             {"sent": sent, "arrived": len(counters), "lost": lost, "missing": missing[:6],
              "in_order": in_order, "console_rx": c1.get("rx", 0) - c0.get("rx", 0),
              "console_batches": c1.get("batches", 0) - c0.get("batches", 0), "messages": len(msgs),
              "routes_moved": mv})
    ck.check(tag + " A3 no loss counter of a route moved without a loss", lost > 0 or not mv, mv)


def leg_tx(a, dut, can):
    print("== T: a tx message reaches the bus", flush=True)
    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    seen = {}
    try:
        time.sleep(0.3)
        publish(a.pi, json.dumps({"bus": 0, "type": "tx", "frame": [
            {"id": 0x6A1, "dlc": 8, "rtr": False, "extd": False, "data": [1, 2, 3, 4, 5, 6, 7, 8]},
            {"id": 0x6A2, "dlc": 2, "rtr": False, "extd": False, "data": [9, 10]}]}))
        end = time.time() + 6
        while time.time() < end and len(seen) < 2:
            m = bus.recv(timeout=0.5)
            if m is not None and m.arbitration_id in (0x6A1, 0x6A2):
                seen[m.arbitration_id] = bytes(m.data)
    finally:
        bus.shutdown()
    ck.check("T1 both frames of the tx message on the bus, byte exact",
             seen == {0x6A1: bytes(range(1, 9)), 0x6A2: bytes([9, 10])},
             {hex(k): v.hex() for k, v in seen.items()})


def mqtt_end(dut):
    """the `losses` of the mqtt0 end of br_mqtt_can on /api/bridges, {} without"""
    row = next((b for b in (dut.get("/api/bridges") or {}).get("bridges", []) if b["name"] == "br_mqtt_can"), {})
    return (row.get("losses") or {}).get("b") or {}


def leg_counted(a, dut):
    print("== C: a tx message longer than the reassembler takes", flush=True)
    c0 = console_counters(dut)
    b0 = mqtt_end(dut)
    frames = [{"id": 0x6B0 + (i % 8), "dlc": 8, "rtr": False, "extd": False, "data": [i & 255] * 8}
              for i in range(20)]
    body = json.dumps({"bus": 0, "type": "tx", "frame": frames})
    publish(a.pi, body)
    time.sleep(2)
    c1 = console_counters(dut)
    b1 = mqtt_end(dut)
    grew = c1.get("tx_oversize", 0) - c0.get("tx_oversize", 0)
    ck.check("C1 the unit counted the message (console: tx-oversize)", grew == 1,
             {"bytes": len(body), "tx_oversize": (c0.get("tx_oversize"), c1.get("tx_oversize"))})
    ck.check("C2 /api/bridges shows the same count (br_mqtt_can losses.b.tx_oversize)",
             "tx_oversize" in b1 and b1["tx_oversize"] - b0.get("tx_oversize", 0) == grew and grew == 1,
             {"losses.b": b1 or "the bridge row has no losses"})
    rows = [b for b in (dut.get("/api/bridges") or {}).get("bridges", []) if b.get("up")]
    bare = [b["name"] for b in rows
            if not all("rx_drops" in ((b.get("losses") or {}).get(end) or {}) or
                       (b[end] == "mqtt0" and "dropped_q_full" in ((b.get("losses") or {}).get(end) or {}))
                       for end in ("a", "b"))]
    ck.check("C3 every running bridge reports what the jacks at its two ends lost", bool(rows) and not bare,
             {"running": len(rows), "without": bare,
              "br_mqtt_can": next((b.get("losses") for b in rows if b["name"] == "br_mqtt_can"), None)})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--pcan", default=os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"))
    ap.add_argument("--tiers", default="1@300x3,4@2000,24@2000",
                    help="<batch_frames>@<frames a second>[x<frames in a group>], comma separated")
    ap.add_argument("--n", type=int, default=3000)
    a = ap.parse_args()
    import can

    dut = Dut(a.dut)
    h = dut.health(wait_s=40)
    if h["version"] is None:
        print("MQTT CAN BATCH FAIL: no unit at", a.dut)
        return 1
    print("unit: %s boot_count=%s unexpected=%s log_errors=%s" % (
        h["version"], h["boot_count"], h["unexpected_resets"], h["log_errors"]), flush=True)
    st = Staged(dut, ("mqtt_can", "bridge_manager"), "MQTT CAN BATCH")
    mq = dut.settings("mqtt_manager")
    print("as found: mqtt_manager enabled=%s url=%s; mqtt_can=%s" % (
        mq.get("enabled"), mq.get("url"), {k: st.found["mqtt_can"].get(k) for k in
                                           ("enabled", "allow_rx", "allow_tx", "batch_frames")}), flush=True)
    if not mq.get("enabled"):
        print("MQTT CAN BATCH FAIL: mqtt_manager is off on the unit (the bench uses the broker it is set to)")
        return 1
    try:
        for t in a.tiers.split(","):
            rate = t.split("@")[1]
            tier(a, dut, st, can, int(t.split("@")[0]), int(rate.split("x")[0]),
                 int(rate.split("x")[1]) if "x" in rate else 1)
        leg_tx(a, dut, can)
        leg_counted(a, dut)
    except Exception as e:
        ck.check("the legs ran to the end", False, "%s: %s" % (type(e).__name__, e))
    print("== R: restore", flush=True)
    st.restore(ck)
    return ck.finish()


if __name__ == "__main__":
    sys.exit(main())
