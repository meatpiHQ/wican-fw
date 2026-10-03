#!/usr/bin/env python3
"""J1939 counter-conservation bench (MEATPI_COMPONENT_STANDARD §7,
performance-truth rules; TASK_j1939_wwh.md phase 4).

Put EXACTLY N frames of four J1939 keys on the wire with the PCAN adapter,
at three load tiers up to line rate, at 250k and at 500k, twice each, and
make the DUT account for every single one:

  per key     the listener's message count moves by exactly its share, and
              the payload it holds is the LAST one sent (a counter rides in
              the data)
  listener    rx_frames +N, all of them parameter groups
  bus         /api/can rx +N
  losses      rx_overrun (controller FIFO), rx_missed (driver queue),
              queue_drops (the listener's queue), not_kept (its store): all
              zero below line rate. At line rate a loss is tolerated ONLY
              when these counters name every lost frame (and it is printed).

Then, at 80 % load, the same count while the device is kept busy the way a
browser tab or a logger keeps it busy:

  polled      every status route a page polls is requested in turn, ten a
              second (POLLED below)
  flash       a 2 KB file is written to /data twice a second

Both must be exact. This is the leg that found, on 2026-10-03, that
GET /api/status cost 74 frames in 30 s (a heap walk with interrupts off for
3 to 4 ms) and GET /api/status/tasks 478 (the kernel's task snapshot, 6 ms):
the CAN controller's own FIFO holds four frames.

"No crash and plausible numbers" is not a pass. The DUT listens (fixed
bitrate, silent), so it acknowledges nothing: the ECU simulator is the ACK
source, with its own ECU off.

Verdict: `J1939 CONSERVATION PASS`. Run on the PC (IDF venv python;
python-can, the PCAN on the DUT's bus):
  python j1939_conservation_test.py [host[:port]] [--pcan PCAN_USBBUS2]
                                    [--n 20000] [--only 250|500]
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
import bench_bus  # noqa: E402
import j1939_ref as J  # noqa: E402
from pcbench import Bench, Dut, Run  # noqa: E402

DUT_HOST = "localhost:8081"
PCAN = "PCAN_USBBUS2"
N = 20000
ONLY = None
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--pcan":
        PCAN = args[i + 1]; i += 2
    elif args[i] == "--n":
        N = int(args[i + 1]); i += 2
    elif args[i] == "--only":
        ONLY = int(args[i + 1]); i += 2
    else:
        DUT_HOST = args[i]; i += 1

RUNS = 2                        # n >= 2 per tier (performance-truth rules)
# 29-bit identifiers, 8 data bytes: frames a second the bus carries (M4)
LINE_RATE = {250: 1800, 500: 3600}
# (name, frames a second; a fraction = of the line rate; 0 = as fast as the
# bus takes them)
TIERS = [("1000_per_s", 1000), ("80_percent", 0.8), ("line_rate", 0)]

# the four keys: two broadcast groups of the engine, a proprietary group of
# another controller, a PDU1 group sent to the engine (its destination is
# part of the key)
KEYS = [
    ("F004", 0x00, 0xFF, J.can_id(3, J.PGN_EEC1, 0x00)),
    ("FEF1", 0x00, 0xFF, J.can_id(6, J.PGN_CCVS1, 0x00)),
    ("FF10", 0x21, 0xFF, J.can_id(6, 0xFF10, 0x21)),
    ("0000", 0x0B, 0x00, J.can_id(3, 0x0000, 0x0B, 0x00)),
]

# what the web UI's pages (and a script, a dashboard, Home Assistant) poll:
# read-only status routes. No frame may be lost to any of them.
POLLED = [
    "/api/status", "/api/status/tasks", "/api/can", "/api/j1939",
    "/api/j1939?pgns=1", "/api/autopid", "/api/autopid/data",
    "/api/autopid/vehicles", "/api/autopid/dtc", "/api/battery", "/api/ble",
    "/api/bridges", "/api/destinations", "/api/events/log",
    "/api/events/rules", "/api/faults", "/api/imu", "/api/info",
    "/api/j2534", "/api/led", "/api/logger", "/api/logs/ring",
    "/api/logs/status", "/api/obd_chip", "/api/ota/status",
    "/api/restart/history", "/api/rtc", "/api/sleep", "/api/sockets",
    "/api/usb", "/api/vpn", "/api/wifi/status", "/api/settings/j1939",
    "/api/fs/list?path=/data", "/api/fs/info?path=/data",
]
FLASH_FILE = "/data/j1939_conserve.tmp"

run = Run("J1939 CONSERVATION")
dut = Dut(DUT_HOST, own_tags=("j1939", "can_manager", "can_core"),
          tasks=("j1939", "can_core_rx"))


def sim_ack_only(kbps):
    bench_bus.sim_set(bitrate=kbps, enabled=True)
    bench_bus.sim_set(enabled=False)


def counters():
    """Everything that counts a frame on its way in."""
    can = dut.get("/api/can")
    j = dut.get("/api/j1939")["stats"]
    out = {"can_rx": can["rx"], "rx_overrun": can["rx_overrun"],
           "rx_missed": can["rx_missed"],
           "dispatch_drops": can["dispatch_drops"],
           "rx_frames": j["rx_frames"], "rx_data": j["rx_data"],
           "queue_drops": j["queue_drops"], "not_kept": j["not_kept"]}
    for pgn, sa, da, _cid in KEYS:
        code, m = dut.api(f"/api/j1939?pgn={pgn}&sa={sa}&da={da}")
        out[f"n_{pgn}"] = m.get("count", 0) if code == 200 else 0
        out[f"d_{pgn}"] = m.get("data", "") if code == 200 else ""
    return out


def flash_writes():
    st = dut.get("/api/status")
    return st.get("health", {}).get("flash", {}).get("writes", 0)


def upload(path, body):
    """POST a raw body to /api/fs/upload. HTTP status, 0 when no answer."""
    req = urllib.request.Request(
        f"{dut.base}/api/fs/upload?path={path}", data=body, method="POST",
        headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


class Busy:
    """Something that keeps the device busy over HTTP while frames are
    counted: `polled` walks POLLED ten times a second, `flash` writes a 2 KB
    file twice a second."""

    def __init__(self, kind):
        self.kind = kind
        self.stop = threading.Event()
        self.done = 0           # requests answered 200
        self.other = {}         # anything else: {path or status: count}
        self.thread = threading.Thread(target=self.work, daemon=True)

    def work(self):
        n = 0
        body = bytes((n * 7 + 3) & 0xFF for n in range(2048))
        while not self.stop.is_set():
            if self.kind == "polled":
                path = POLLED[n % len(POLLED)]
                code, _ = dut.api(path, timeout=6)
                gap = 0.1
            else:
                path = "upload"
                code = upload(FLASH_FILE, body)
                gap = 0.5
            if code == 200:
                self.done += 1
            else:
                key = f"{path} -> {code}"
                self.other[key] = self.other.get(key, 0) + 1
            n += 1
            self.stop.wait(gap)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=15)
        return False


def send_exactly(bus, can, n, rate):
    """n frames, round robin over the keys, a counter in the data. Returns
    (seconds, the last payload of each key)."""
    gap = 1.0 / rate if rate else 0.0
    last = {}
    t0 = time.perf_counter()
    due = t0
    sent = 0
    beat = t0
    while sent < n:
        pgn, _sa, _da, cid = KEYS[sent % len(KEYS)]
        data = sent.to_bytes(4, "little") + bytes([0xA5, 0x5A, 0xC3, 0x3C])
        msg = can.Message(arbitration_id=cid, is_extended_id=True, data=data)
        while True:
            try:
                bus.send(msg, timeout=0.2)
                break
            except can.CanError:
                continue        # until every one of the n left the adapter
        last[pgn] = data.hex().upper()
        sent += 1
        if gap:
            due += gap
            while time.perf_counter() < due:
                pass            # spin: sleep() is too coarse for this
        now = time.perf_counter()
        if now - beat >= 5:
            beat = now
            print(f"  sent {sent}/{n} ({sent / (now - t0):.0f} frames/s)",
                  flush=True)
    return time.perf_counter() - t0, last


def settle(before_rx, want, secs=40):
    """Wait until the adapter's queue is on the wire: the DUT's frame count
    reached what was sent, or stopped moving for 2 s. Returns when the count
    was last seen moving (time.perf_counter)."""
    end = time.time() + secs
    last = None
    moved = time.perf_counter()
    while time.time() < end:
        rx = dut.get("/api/can")["rx"] - before_rx
        if rx != last:
            last = rx
            moved = time.perf_counter()
        if rx >= want or time.perf_counter() - moved >= 2.0:
            break
        time.sleep(0.1)
    time.sleep(0.5)
    return moved


def tier_run(bus, can, kbps, name, rate, attempt, busy=None):
    """One exact-N run. `busy` = "polled" or "flash": the device is kept
    busy that way while the frames come. Returns the frames lost."""
    tag = f"{kbps}k_{name}_{attempt}"
    before = counters()
    writes0 = flash_writes() if busy == "flash" else 0
    t0 = time.perf_counter()
    if busy:
        with Busy(busy) as b:
            secs, last = send_exactly(bus, can, N, rate)
    else:
        b = None
        secs, last = send_exactly(bus, can, N, rate)
    on_wire = settle(before["can_rx"], N)
    if rate == 0:
        # unpaced: the adapter's queue took them at once, the wire took its
        # time. The rate is what the DUT saw arrive.
        secs = max(on_wire - t0, 0.001)
    after = counters()
    d = {k: after[k] - before[k] for k in before if not k.startswith("d_")}
    share = N // len(KEYS)
    kept = sum(d[f"n_{k[0]}"] for k in KEYS)
    named = d["rx_overrun"] + d["rx_missed"] + d["queue_drops"] + d["not_kept"]
    lost = N - kept
    detail = (f"{N} frames at {N / secs:.0f} frames/s: per key "
              f"{[d['n_' + k[0]] for k in KEYS]}, bus rx +{d['can_rx']}, "
              f"listener +{d['rx_frames']}, overrun +{d['rx_overrun']}, "
              f"missed +{d['rx_missed']}, queue drops +{d['queue_drops']}, "
              f"not kept +{d['not_kept']}")
    if b is not None:
        detail += f"; {b.done} requests answered"
        if b.other:
            detail += f", others {json.dumps(b.other)}"
    exact = all(d[f"n_{k[0]}"] == share for k in KEYS) and lost == 0 \
        and named == 0 and d["can_rx"] == N and d["rx_frames"] == N \
        and d["rx_data"] == N
    if exact:
        run.check(f"conserve_{tag}", True, detail)
    elif rate == 0 and lost > 0 and lost == named \
            and d["can_rx"] == N - d["rx_overrun"] - d["rx_missed"]:
        # at line rate a loss is acceptable when every frame of it has a name
        print(f"WARN: {tag}: {lost} of {N} frames lost, every one counted",
              flush=True)
        run.check(f"conserve_{tag}", True, "LOSS ACCOUNTED: " + detail)
    else:
        run.check(f"conserve_{tag}", False, detail)
    newest = {k[0]: after[f"d_{k[0]}"] for k in KEYS}
    run.check(f"newest_{tag}", lost > 0 or newest == last,
              "" if newest == last else json.dumps(newest))
    run.metric(f"rate_{tag}", round(N / secs), "frames/s")
    if lost:
        run.metric(f"lost_{tag}", lost, "frames")
    if b is not None:
        # the leg means nothing when the device was not actually kept busy
        want = int(secs * (4 if busy == "polled" else 1))
        run.check(f"kept_busy_{tag}", b.done >= want,
                  f"{b.done} requests answered in {secs:.0f} s"
                  + (f", others {json.dumps(b.other)}" if b.other else ""))
    if busy == "flash":
        w = flash_writes() - writes0
        run.check(f"flash_was_written_{tag}", w > 0, f"flash writes +{w}")
    return lost


def bitrate_block(can, kbps):
    print(f"--- {kbps}k", flush=True)
    sim_ack_only(kbps)
    # autopid off: its contact probes (phase 5) would put the chip's
    # frames on the bus and store the bench's fake truck
    dut.restart({"can_manager": {"enabled": True, "baud": str(kbps),
                                 "silent": True},
                 "j1939": {"enabled": True},
                 "autopid": {"enabled": False}})
    code, doc = dut.api("/api/j1939")
    if code != 200 or doc.get("state") != "listening":
        raise Bench(f"the listener is not up: {code} {doc}")
    a = dut.get("/api/can")["rx"]
    time.sleep(3)
    stray = dut.get("/api/can")["rx"] - a
    run.check(f"bus_quiet_{kbps}k", stray == 0, f"{stray} stray frames in 3 s")

    bus = can.Bus(interface="pcan", channel=PCAN, bitrate=kbps * 1000)
    try:
        # a few frames first: the node proves its bitrate on them, and the
        # keys exist before anything is counted
        send_exactly(bus, can, 40, 200)
        time.sleep(1.0)
        link = dut.get("/api/can")
        run.check(f"listening_{kbps}k", link["state"] == "running"
                  and link["listen_only"] is True and link["verified"] is True
                  and link["baud_kbps"] == kbps,
                  json.dumps({k: link[k] for k in ("state", "listen_only",
                                                   "verified", "baud_kbps")}))
        total_lost = 0
        for name, rate in TIERS:
            fps = rate if rate >= 1 else int(LINE_RATE[kbps] * rate)
            for attempt in range(1, RUNS + 1):
                print(f"tier {kbps}k {name} run {attempt}/{RUNS}: exactly "
                      f"{N} frames" + (f" at {fps} frames/s" if fps else
                                       " as fast as the bus takes them"),
                      flush=True)
                total_lost += tier_run(bus, can, kbps, name, fps, attempt)
        fps = int(LINE_RATE[kbps] * 0.8)
        for busy in ("polled", "flash"):
            print(f"tier {kbps}k 80 % load while {busy}: exactly {N} frames "
                  f"at {fps} frames/s", flush=True)
            total_lost += tier_run(bus, can, kbps, f"while_{busy}", fps, 1,
                                   busy=busy)
        run.metric(f"lost_total_{kbps}k", total_lost, "frames")
        tx = dut.get("/api/can")["tx"]
        run.check(f"dut_transmitted_nothing_{kbps}k", tx == 0, f"tx {tx}")
    finally:
        bus.shutdown()
        dut.api(f"/api/fs/file?path={FLASH_FILE}", "DELETE")
    dut.sweep()


def main():
    import can

    st0 = dut.get("/api/status")
    found = {n: dut.settings(n) for n in ("can_manager", "j1939", "autopid")}
    found_sim = bench_bus.sim_get("ecu_sim")
    code, faults0 = dut.api("/api/faults")
    print(f"DUT {DUT_HOST}: boot_count {st0.get('boot_count')}, "
          f"unexpected_resets {st0.get('unexpected_resets')}, version "
          f"{st0.get('version')}; N = {N}, {RUNS} runs per tier")
    print(f"as found: {json.dumps(found)}")
    dut.as_found()

    try:
        for kbps in (250, 500):
            if ONLY in (None, kbps):
                bitrate_block(can, kbps)
    except Bench as e:
        run.check("bench_ran_to_the_end", False, str(e))
    finally:
        print("--- restore", flush=True)
        try:
            if {n: dut.settings(n) for n in found} != found:
                dut.restart(found)
            if bench_bus.sim_get("ecu_sim") != found_sim:
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
            code, left = dut.api(f"/api/fs/download?path={FLASH_FILE}")
            run.check("probe_file_removed", code != 200, f"HTTP {code}")
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

    return run.verdict(f"{ONLY}k only" if ONLY else "")


if __name__ == "__main__":
    sys.exit(main())
