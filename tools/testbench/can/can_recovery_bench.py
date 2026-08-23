#!/usr/bin/env python3
"""Bus-off recovery fault-injection bench (PC orchestrator).

Forces a REAL bus-off on the DUT's TWAI controller and verifies the
automatic recovery added 2026-07-10 (can_core_recovery policy):

  leg 1  baseline: DUT TX -> PCAN @500k byte-exact, PCAN TX -> DUT rx++
  leg 2  fault injection: PCAN reopens at 50k (auto_reset=True) and
         blasts while the DUT transmits ID 0x000 (auto-retransmit) ->
         every TX attempt takes a bit error -> TEC +8 -> 256 = BUS-OFF
         in <100 ms.
  leg 3  recovery: /api/can shows recoveries incremented + running —
         no reboot (round 1 restart is immediate; round 2, inside the
         60 s stability window, exercises the 1 s backoff).
  leg 4  traffic after recovery: byte-exact both directions at 500k.
  leg 5  repeat legs 2-4 once (episode #2) — counters must reach 2.

The injection geometry is delicate (probed 2026-07-10, all on the wire):
  - matched-baud or silent peer: no-ACK pins TEC at 128 (error-passive
    ACK exemption) — never bus-off. The PEAK-wedge post-mortem case.
  - fast/saturating storm (125k/250k back-to-back): the inter-frame gap
    is < 11 recessive bits at 500k, the DUT can never START a frame ->
    TEC freezes (~115).
  - without auto_reset the PEAK itself bus-offs first (~0.8 s) — the
    DUT's error flags stomp it 100x more often than vice versa.
  - 50k storm: 2 ms frames with 60 us gaps = 30 bit-times at 500k —
    room to start a frame, never room to finish it. TX id MUST be 0x000:
    an all-dominant arbitration field can't lose arbitration, so the
    error-passive DUT (which restarts 8 suspend-bits later, putting the
    storm's SOF inside its arbitration) takes bit errors (+8) instead
    of arb-lost (+0). To the 20 us/bit PEAK the DUT's 12 us error flags
    are sub-sample glitches -> it keeps blasting.

Topology: DUT over the USB mgmt link (192.168.82.1: HTTP + /ws/cli);
PCAN on this PC wired to the DUT's CAN bus. Self-skips without a PCAN.
Ends with CANREC PASS.

  python can_recovery_bench.py [usb_ip] [--pcan PCAN_USBBUS2]
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
from wsmin import WS, OP_TEXT            # noqa: E402

USB = "192.168.82.1"
PCAN = "PCAN_USBBUS2"
fails = []
metrics = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def metric(name, value):
    metrics.append((name, value))
    print(f"METRIC {name} = {value}")


def api(path, method="GET", body=None, retries=4, timeout=20):
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    last = None
    for _ in range(retries):
        req = urllib.request.Request(f"http://{USB}{path}", data=data,
                                     method=method,
                                     headers={"Content-Type":
                                              "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                t = r.read().decode()
                return r.status, (json.loads(t)
                                  if t.strip().startswith(("{", "[")) else t)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(2)
    raise last


def wait_online(secs=90):
    for _ in range(int(secs / 1.5)):
        try:
            api("/api/status", retries=1, timeout=4)
            return True
        except Exception:
            time.sleep(1.5)
    return False


def cli(cmd, marker, timeout=30):
    """One command over a fresh /ws/cli connection (newline MANDATORY)."""
    ws = WS(USB, 80, "/ws/cli", timeout=10)
    try:
        ws.send((cmd + "\n").encode(), opcode=OP_TEXT)
        out = ""
        end = time.time() + timeout
        while time.time() < end:
            try:
                op, payload = ws.recv_frame()
            except Exception:
                continue
            if op == OP_TEXT or op == 2:
                out += payload.decode("utf-8", "replace")
                if marker in out:
                    return out
        raise TimeoutError(f"cli '{cmd}': no '{marker}' in: {out[-200:]}")
    finally:
        try:
            ws.s.close()
        except Exception:
            pass


def can_status():
    return api("/api/can")[1]


# ---- PCAN ---------------------------------------------------------------------

def open_pcan(bitrate, auto_reset=False):
    import can
    return can.Bus(interface="pcan", channel=PCAN, bitrate=bitrate,
                   auto_reset=auto_reset)


class Blaster:
    """Background frame blaster (the collision source for leg 2)."""

    def __init__(self, bus):
        self.bus = bus
        self.stop_evt = threading.Event()
        self.sent = 0
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        import can
        msg = can.Message(arbitration_id=0x101, dlc=8,
                          data=bytes(range(8)), is_extended_id=False)
        while not self.stop_evt.is_set():
            try:
                self.bus.send(msg, timeout=0.02)
                self.sent += 1
            except Exception:
                # error-passive / TX queue full while the bus is chaos —
                # expected, keep hammering
                time.sleep(0.002)

    def start(self):
        self.t.start()
        return self

    def stop(self):
        self.stop_evt.set()
        self.t.join(timeout=3)


def drain(bus, secs=1.0):
    end = time.time() + secs
    while time.time() < end:
        if bus.recv(timeout=0.05) is None and time.time() > end - 0.5:
            break


def recv_id(bus, want_id, secs=5.0):
    """Wait for a frame with the given id; returns its data or None."""
    end = time.time() + secs
    while time.time() < end:
        m = bus.recv(timeout=0.2)
        if m is not None and m.arbitration_id == want_id:
            return bytes(m.data)
    return None


# ---- legs ---------------------------------------------------------------------

def ensure_can_up():
    st = can_status()
    if st.get("running") and st.get("baud_kbps") == 500 \
            and not st.get("silent"):
        return
    print("  configuring can_manager (enabled, 500k) + reboot ...")
    cur = api("/api/settings/can_manager")[1]
    for k in ("degraded", "pending_reboot"):
        cur.pop(k, None)
    cur.update({"enabled": True, "baud": "500", "silent": False,
                "cli": True})
    code, r = api("/api/settings/can_manager", "PUT", cur)
    if code != 200:
        raise RuntimeError(f"PUT can_manager -> {code}: {r}")
    try:
        api("/api/settings/submit", "POST", retries=1)
    except Exception:
        pass
    time.sleep(12)
    if not wait_online():
        raise RuntimeError("DUT did not come back after settings submit")
    st = can_status()
    if not st.get("running"):
        raise RuntimeError(f"can_manager not running: {st}")


def leg_baseline(bus, tag, payload):
    drain(bus, 0.3)
    cli(f"can send 7AA {payload.hex().upper()}", "OK")
    got = recv_id(bus, 0x7AA)
    check(f"{tag}: DUT TX -> PCAN byte-exact", got == payload,
          f"got {got.hex() if got else None}")

    rx0 = can_status()["rx"]
    import can
    bus.send(can.Message(arbitration_id=0x123, dlc=4,
                         data=b"\xde\xad\xbe\xef", is_extended_id=False),
             timeout=0.5)
    rx1 = rx0
    end = time.time() + 6
    while time.time() < end:
        rx1 = can_status()["rx"]
        if rx1 > rx0:
            break
        time.sleep(0.4)
    check(f"{tag}: PCAN TX -> DUT rx count", rx1 > rx0,
          f"rx {rx0} -> {rx1}")


def force_bus_off(round_no, off_before):
    """Blast at 50k while the DUT transmits id 0x000 (see module doc).

    Occasionally the PEAK enters a reset-thrash mode (auto_reset flushes
    its queue faster than it saturates the wire — seen right after the
    datalog FULL BLAST leg): the mostly-quiet wire turns the DUT's
    attempts into ACK errors, which PIN TEC at 128 instead of climbing
    to 256. A FRESH storm bus clears that mode, so retry once."""
    t0 = time.time()
    st = None
    sent_total = 0

    for attempt in (1, 2):
        storm = open_pcan(50000, auto_reset=True)
        blaster = Blaster(storm).start()
        try:
            # SUSTAINED induction TX (2026-07-22): fail_retry_cnt is
            # FINITE now (512 — the ELM-style give-up that killed the
            # error-passive-forever pathology), so a one-shot queued
            # frame stops erroring after ~130 ms and TEC plateaus below
            # 256. Keep submitting through the window — each frame is
            # another retry burst, exactly what sustained app traffic
            # (autopid polling) does on a faulted bus.
            end = time.time() + 15

            while time.time() < end:
                try:
                    cli("can send 000 11223344", "OK", timeout=5)
                except Exception:
                    pass  # ERROR (queue full / bus down) is fine mid-storm

                st = can_status()

                if st["bus_off"] > off_before:
                    break

                time.sleep(0.3)
        finally:
            blaster.stop()
            storm.shutdown()
            sent_total += blaster.sent

        if st is not None and st["bus_off"] > off_before:
            break
        print(f"  round {round_no} attempt {attempt}: no bus-off "
              f"(blast sent {blaster.sent}) — fresh storm bus")
        time.sleep(1)

    ok = st is not None and st["bus_off"] > off_before
    check(f"round {round_no}: BUS-OFF induced", ok,
          f"bus_off {off_before} -> {st['bus_off'] if st else '?'} "
          f"in {time.time() - t0:.1f}s, blast sent {sent_total}")
    return st["bus_off"] if ok else off_before


def wait_recovery(round_no, rec_before):
    t0 = time.time()
    st = None
    end = time.time() + 40
    while time.time() < end:
        st = can_status()
        if st["state"] == "running" and st["recoveries"] > rec_before:
            break
        time.sleep(0.5)
    ok = (st is not None and st["state"] == "running"
          and st["recoveries"] > rec_before)
    check(f"round {round_no}: automatic recovery", ok,
          f"state={st['state'] if st else '?'} recoveries "
          f"{rec_before} -> {st['recoveries'] if st else '?'}")
    if ok:
        metric(f"recovery latency round {round_no} (blast-stop -> running)",
               f"{time.time() - t0:.1f}s")
    return st["recoveries"] if ok else rec_before


def main():
    global USB, PCAN
    ap = argparse.ArgumentParser()
    ap.add_argument("usb_ip", nargs="?", default=USB)
    ap.add_argument("--pcan", default=PCAN)
    args = ap.parse_args()
    USB = args.usb_ip
    PCAN = args.pcan

    try:
        import can  # noqa: F401
    except ImportError:
        print("SKIP: python-can not installed")
        return 0

    if not wait_online(30):
        print("FAIL: DUT unreachable at " + USB)
        return 1

    ensure_can_up()
    st0 = can_status()
    print(f"  /api/can: {st0}")
    check("state field present", "state" in st0 and "bus_off" in st0,
          str({k: st0.get(k) for k in ('state', 'bus_off', 'recoveries')}))

    bus = open_pcan(500000)
    try:
        leg_baseline(bus, "leg 1", b"\xca\xfe\xba\xbe")
    finally:
        bus.shutdown()

    offs, recs = st0["bus_off"], st0["recoveries"]
    for rnd in (1, 2):
        offs = force_bus_off(rnd, offs)
        recs = wait_recovery(rnd, recs)
        bus = open_pcan(500000)   # fresh controller: PEAK error counters reset
        try:
            drain(bus, 0.5)
            leg_baseline(bus, f"round {rnd} post-recovery",
                         bytes([0xA0 + rnd, 2, 3, 4]))
        finally:
            bus.shutdown()
        time.sleep(1)

    st = can_status()
    check("episode counters", st["bus_off"] >= 2 and st["recoveries"] >= 2,
          f"bus_off={st['bus_off']} recoveries={st['recoveries']}")
    out = cli("can", "OK")
    busoff_line = next((ln for ln in out.splitlines()
                        if ln.startswith("Bus-off:")), "")
    check("CLI shows recovery counters", busoff_line != "", busoff_line)

    print()
    for n, v in metrics:
        print(f"METRIC {n} = {v}")
    if fails:
        print("CANREC FAIL: " + ", ".join(fails))
        return 1
    print("CANREC PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
