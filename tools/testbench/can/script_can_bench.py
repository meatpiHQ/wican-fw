#!/usr/bin/env python3
"""A script's raw CAN functions and its CAN_TX pulse (2026-11-17) -> SCRIPT CAN PASS.

The bindings came with meatpi-components pull request 2 (Elias Kotlyar):
`can_rx`, `can_start`, `can_stop`, `digitalWrite`, `digitalPWM`. PCAN is the
witness on the unit's bus and the generator; the scripts run inline through
`POST /api/scripts/run`.

Legs:
  c1  can_rx hands the script a frame PCAN puts on the bus (data, id, dlc)
  c2  can_rx with nothing on that id answers nil when its time is up
  c3  the pin rule: a board pin and CAN_RX are refused, CAN_TX is refused
      while the bus runs; the bus runs after each refusal
  c4  can_stop, ten dominant pulses on CAN_TX, can_start: the script ends
      well, PCAN saw the bus disturbed while it ran, the controller is not
      bus-off afterwards
  c5  after the pulse a frame from the script reaches PCAN and a frame from
      PCAN reaches the script
  c6  a script that stops the bus and ends without can_start: the bus runs
      again by itself, the next script transmits
  c7  the CAN monitor's bridge (can <-slcan-> ws_can) still receives every
      frame after a script stopped and started the bus
  c8  settings back as found, planned restarts only, no E line of the tags

The bench unit keeps the script engine and the /ws/can channel off and has
no bridge on the CAN jack: all three are staged for the run (one restart)
and put back (one more).

  python tools/testbench/can/script_can_bench.py [--dut localhost:18081]
      [--pcan PCAN_USBBUS1]

Needs python-can with the PCAN driver on this PC and PCAN on the unit's bus
(`WICAN_PCAN_DUT` names the channel when the adapter's plugs are swapped).
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402
from wsmin import WS  # noqa: E402

OWN_TAGS = ("script_engine", "script_gpio", "can_manager", "can_core",
            "bridge_endpoints", "bridge_manager")
GEN_ID = 0x123


class Wire:
    """PCAN: what is on the bus (frames by id, real bus errors), and a sender."""

    def __init__(self, channel):
        import can
        self.can = can
        self.bus = can.Bus(interface="pcan", channel=channel, bitrate=500000)
        self.frames = {}        # id -> [data hex]
        self.errors = 0         # bus errors the adapter saw (not counter notes)
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self._th = threading.Thread(target=self._watch, daemon=True)
        self._th.start()

    def _watch(self):
        while not self._halt.is_set():
            m = self.bus.recv(timeout=0.2)
            if m is None:
                continue
            with self._lock:
                if m.is_error_frame:
                    # id 0 is a note that the adapter's error counters moved
                    self.errors += 1 if m.arbitration_id != 0 else 0
                else:
                    self.frames.setdefault(m.arbitration_id, []).append(m.data.hex())

    def clear(self):
        with self._lock:
            self.frames.clear()
            self.errors = 0

    def saw(self, can_id, data_hex, within=2.0):
        end = time.time() + within
        while time.time() < end:
            with self._lock:
                if data_hex in self.frames.get(can_id, []):
                    return True
            time.sleep(0.05)
        return False

    def send(self, can_id, data):
        self.bus.send(self.can.Message(arbitration_id=can_id, is_extended_id=False,
                                       data=data), timeout=0.5)

    def send_later(self, delay_s, can_id, data):
        t = threading.Timer(delay_s, self.send, args=(can_id, data))
        t.daemon = True
        t.start()

    def generate(self, n, fps=100.0):
        for i in range(n):
            self.send(GEN_ID, [i & 0xFF, (i >> 8) & 0xFF, 0xAA, 0x55, 0, 0, 0, 0])
            time.sleep(1.0 / fps)

    def close(self):
        self._halt.set()
        time.sleep(0.3)
        self.bus.shutdown()


class Monitor(threading.Thread):
    """the CAN monitor's path: slcan lines over /ws/can, the generator's counters"""

    def __init__(self, hostport):
        super().__init__(daemon=True)
        host, port = hostport.split(":")[0], int(hostport.split(":")[1]) if ":" in hostport else 80
        self.ws = WS(host, port, "/ws/can", timeout=0.5)
        self.got = []
        self.buf = b""
        self._halt = threading.Event()
        self.start()

    def run(self):
        while not self._halt.is_set():
            try:
                _, payload = self.ws.recv_frame()
            except socket.timeout:
                continue
            except Exception:
                break
            self.buf += payload or b""
            while b"\r" in self.buf:
                line, self.buf = self.buf.split(b"\r", 1)
                if line.startswith(b"t123") and len(line) >= 9:
                    try:
                        d = bytes.fromhex(line[5:].decode())
                        self.got.append(d[0] | (d[1] << 8))
                    except ValueError:
                        pass

    def close(self):
        self._halt.set()
        self.join(timeout=2)
        try:
            self.ws.s.close()
        except Exception:
            pass


def run_script(dut, src, timeout=30):
    """(ok, output text, seconds)"""
    t0 = time.time()
    code, r = dut.api("/api/scripts/run", "POST", {"src": src}, timeout=timeout)
    dt = time.time() - t0
    if code != 200 or not isinstance(r, dict):
        return False, "HTTP %s %s" % (code, str(r)[:160]), dt
    return r.get("ok") is True, str(r.get("output", "")), dt


def can_running(dut, within=3.0):
    end = time.time() + within
    last = {}
    while time.time() < end:
        code, last = dut.api("/api/can")
        if code == 200 and isinstance(last, dict) and last.get("running") is True \
                and last.get("state") == "running":
            return True, last
        time.sleep(0.3)
    return False, last


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--pcan", default=os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"))
    a = ap.parse_args()

    run = pcbench.Run("SCRIPT CAN")
    dut = pcbench.Dut(a.dut, own_tags=OWN_TAGS, tasks=("httpd",))
    exit_code = 1
    found = None
    wire = None
    try:
        dut.as_found()
        st0 = dut.get("/api/status")
        found = {"script_engine": dut.settings("script_engine"),
                 "bridge_manager": dut.settings("bridge_manager"),
                 "can_manager": dut.settings("can_manager"),
                 "websocket_manager": dut.settings("websocket_manager")}
        bridges = [b for b in found["bridge_manager"]["bridges"]
                   if "ws_can" not in (b.get("a"), b.get("b"))]
        bridges.append({"name": "br_can", "a": "can", "b": "ws_can",
                        "translator": "slcan", "enabled": True})
        if len(bridges) > 6:
            raise pcbench.Bench("no free connection row for the CAN monitor's bridge")
        channels = json.loads(json.dumps(found["websocket_manager"]["channels"]))
        for ch in channels:
            if ch.get("name") == "ws_can":
                ch["enabled"] = True
        staged = {"script_engine": {"enabled": True},
                  "bridge_manager": {"bridges": bridges},
                  "websocket_manager": {"channels": channels},
                  "can_manager": {"enabled": True, "baud": "500", "silent": False}}
        print("staging: the script engine on, the /ws/can channel on, "
              "br_can = can <-slcan-> ws_can (a restart)", flush=True)
        dut.restart(staged)
        time.sleep(3)
        try:
            wire = Wire(a.pcan)
        except Exception as e:
            raise pcbench.Bench("PCAN %s not usable: %s" % (a.pcan, e))
        ok, can0 = can_running(dut, 10)
        if not ok:
            raise pcbench.Bench("the CAN bus is not running: %s" % json.dumps(can0)[:200])

        # c1: a frame from the bus reaches the script
        wire.send_later(0.7, 0x6A1, [0x11, 0x22, 0x33])
        ok, out, dt = run_script(dut, "var f = can_rx(0x6A1, 3000)\nprint(f)\n"
                                      "print(can_rx_id)\nprint(can_rx_dlc)\n")
        lines = out.split()
        run.check("c1_can_rx_hands_over_the_frame",
                  ok and "11 22 33" in out and str(0x6A1) in lines and "3" in lines,
                  "%s %.1f s: %r" % (ok, dt, out[:120]))

        # c2: nothing on that id: nil when the time is up
        ok, out, dt = run_script(dut, "print(can_rx(0x6A3, 400))\n")
        run.check("c2_can_rx_times_out_with_nil", ok and "nil" in out and 0.3 <= dt < 3.0,
                  "%s %.2f s: %r" % (ok, dt, out[:80]))

        # c3: the pin rule
        ok, out, _ = run_script(dut, "can_stop()\ndigitalWrite(15, HIGH)\n")
        run.check("c3_a_board_pin_is_refused", not ok and "pin refused" in out, out[:160])
        up, cs = can_running(dut)
        run.check("c3_the_bus_runs_after_the_refusal", up, {k: cs.get(k) for k in ("running", "state")})
        ok, out, _ = run_script(dut, "can_stop()\ndigitalWrite(CAN_RX, HIGH)\n")
        run.check("c3_can_rx_is_refused", not ok and "pin refused" in out and "transceiver" in out, out[:160])
        ok, out, _ = run_script(dut, "digitalWrite(CAN_TX, LOW)\n")
        run.check("c3_can_tx_refused_while_the_bus_runs", not ok and "CAN pin in use" in out, out[:160])
        ok, out, _ = run_script(dut, "digitalPWM(21, 1, 1, 3)\n")
        run.check("c3_digitalpwm_follows_the_same_rule", not ok and "pin refused" in out, out[:160])

        # c4: the pulse
        up, cs0 = can_running(dut)
        wire.clear()
        ok, out, dt = run_script(dut, "can_stop()\ndigitalPWM(CAN_TX, 1.6, 0.4, 10)\n"
                                      "can_start()\nprint('pulsed')\n")
        time.sleep(0.5)
        run.check("c4_the_pulse_script_ends_well", ok and "pulsed" in out, "%.2f s: %r" % (dt, out[:120]))
        run.check("c4_pcan_saw_the_bus_disturbed", wire.errors > 0, "%d bus error(s)" % wire.errors)
        up, cs1 = can_running(dut)
        run.check("c4_the_controller_runs_and_is_not_bus_off",
                  up and cs1.get("bus_off", 0) == cs0.get("bus_off", 0),
                  {k: cs1.get(k) for k in ("running", "state", "bus_off", "recoveries")})
        run.metric("pulse_script_s", round(dt, 2), "s")

        # c5: both directions after the pulse
        wire.clear()
        ok, out, _ = run_script(dut, "print(can_tx(0x6A2, 0, '01 02 03'))\n")
        run.check("c5_a_script_frame_reaches_the_bus", ok and "true" in out and wire.saw(0x6A2, "010203"),
                  "%r, on the bus: %s" % (out[:60], dict(wire.frames)))
        wire.send_later(0.7, 0x6A1, [0x44, 0x55])
        ok, out, _ = run_script(dut, "print(can_rx(0x6A1, 3000))\n")
        run.check("c5_a_bus_frame_reaches_the_script", ok and "44 55" in out, out[:80])

        # c6: the bus comes back by itself
        ok, out, _ = run_script(dut, "print(can_stop())\n")
        up, cs = can_running(dut)
        run.check("c6_the_bus_restarts_when_the_script_ends", ok and "true" in out and up,
                  "%r, %s" % (out[:40], {k: cs.get(k) for k in ("running", "state")}))
        wire.clear()
        ok, out, _ = run_script(dut, "print(can_tx(0x6A4, 0, 'AA'))\n")
        run.check("c6_the_next_script_transmits", ok and wire.saw(0x6A4, "aa"), out[:60])

        # c7: the monitor's bridge after all those stops
        mon = Monitor(a.dut)
        time.sleep(1.0)
        wire.generate(100, 100.0)
        time.sleep(1.5)
        got = list(mon.got)
        mon.close()
        run.check("c7_the_can_monitor_still_receives_every_frame", got == list(range(100)),
                  "%d of 100, first %s last %s" % (len(got), got[:2], got[-2:]))

        # c8: as found
        dut.reset_check(run, st0)
        wire.close()
        wire = None
        dut.restart(found)
        back = {c: dut.settings(c) for c in found}
        run.check("c8_settings_back_as_found", back == found,
                  {c: "same" if back[c] == found[c] else "DIFFERS" for c in found})
        found = None
        dut.passing_checks(run)
        exit_code = run.verdict()
    except pcbench.Bench as e:
        print("BENCH:", e, flush=True)
        run.fails.append("bench: %s" % e)
        run.verdict()
        exit_code = 2
    finally:
        if wire is not None:
            wire.close()
        if found is not None:
            try:
                dut.restart(found)
            except pcbench.Bench as e:
                print("restore by hand (script_engine, bridge_manager, can_manager, "
                      "websocket_manager):", e, flush=True)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
