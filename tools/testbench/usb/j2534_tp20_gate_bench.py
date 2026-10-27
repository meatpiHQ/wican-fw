#!/usr/bin/env python3
"""Raw CAN forwards whatever the tool sends: the VW TP 2.0 frame shapes.

The Discord report of 2026-10-27 (elias): a custom flasher speaking KWP2000
over VW TP 2.0 on a raw CAN channel got `PassThruWriteMsgs: NotSupported
(0x01)` at random, GetLastError "no error". Cause: the device's reflash
gate read every raw CAN frame as ISO-TP (first nibble 0 -> byte 1 "the
service", nibble 1 -> byte 2) and refused 0x34..0x37 while allow_reflash
was off; TP 2.0 data frames start 0x0N / 0x1N (sequence numbers), so any
payload byte 0x34..0x37 behind them was refused. The gate was removed the
same day (j2534_channel.c: no content gate on any channel; the services a
tool uses on ISO15765 are counted as /api/j2534 reflash_seen).

This bench is the regression: every TP 2.0 shape, every payload byte, goes
out. Legs (planned restarts: configure, restore):
  C  configure: j2534 on with allow_lan
  T  a TP 2.0 session's frame shapes go through (channel setup, params,
     a single-frame KWP request, ACKs, channel test, disconnect)
  S  the sweep: 0x1N 00 xx, 0x0N xx, 0x2N xx, 0x3N 00 xx for xx in 0..255
     (three sequence numbers each): nothing refused, every frame on the
     bus (PCAN), no gate line in the device log, reflash_seen unchanged
     (raw CAN is not inspected)
  D  the installed wican_j2534.dll (32-bit Python): the two frames the
     old gate refused pass with status 0x0 (j2534_tp20_dll_check.py);
     per-write round trip and the 7DF request/answer over the path in use
  X  restore: settings as found, planned restarts only, no new E lines

usage (PC, PCAN wired, the tunnel of ports 80 and 6809 to the DUT):
  python j2534_tp20_gate_bench.py --dut localhost:18081 --j2534 localhost:18534
Prints TP20 RAW CAN PASS or TP20 RAW CAN FAIL.
"""
import argparse
import json
import os
import statistics
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_argv = sys.argv
sys.argv = [sys.argv[0]]
import j2534_bench as b  # noqa: E402
import j2534_auto_baud_bench as ab  # noqa: E402  (Dut, Pcan)
sys.argv = _argv

HERE = os.path.dirname(os.path.abspath(__file__))
GATE_LINE = "ECU flashing disabled"
TP_ID = 0x740          # the tester's TP 2.0 transmit id (the ECU listens here)
SETUP_ID = 0x200

fails, passes = [], []


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


def can_msg(can_id, frame):
    data = struct.pack(">I", can_id) + bytes(frame)
    return b.MSG.pack(b.PROT_CAN, 0, 0, 0, 0, len(data)) + data


def write_raw(c, ch, can_id, frame):
    """WRITE_MSGS one raw CAN frame; returns (status, seconds)."""
    t0 = time.perf_counter()
    st, _, _ = c.call(b.WRITE_MSGS, ch, struct.pack("<I", 1) + can_msg(can_id, frame))
    return st, time.perf_counter() - t0


def open_tester(jh, jp):
    c = b.Client(jh, jp)
    c.call(b.HELLO, payload=struct.pack("<I", 1))
    c.call(b.OPEN)
    st, ch, _ = c.call(b.CONNECT, payload=struct.pack("<III", b.PROT_CAN, 0, 500000))
    if st != 0:
        raise RuntimeError("CONNECT CAN refused: 0x%X" % st)
    return c, ch


SESSION = [
    ("channel setup on 200: 01 C0 00 10 00 03 01", SETUP_ID, [0x01, 0xC0, 0x00, 0x10, 0x00, 0x03, 0x01]),
    ("parameters A0 0F 8A FF 32 FF", TP_ID, [0xA0, 0x0F, 0x8A, 0xFF, 0x32, 0xFF]),
    ("channel test A3 0F 8A FF 32 FF", TP_ID, [0xA3, 0x0F, 0x8A, 0xFF, 0x32, 0xFF]),
    ("a single-frame KWP request 10 00 02 1A 9B", TP_ID, [0x10, 0x00, 0x02, 0x1A, 0x9B]),
    ("ACK B1", TP_ID, [0xB1]),
    ("ACK B5 (padded to 8)", TP_ID, [0xB5, 0, 0, 0, 0, 0, 0, 0]),
    ("disconnect A8", TP_ID, [0xA8]),
]


def leg_t(c, ch, pcan):
    print("== T: a TP 2.0 session's frame shapes", flush=True)
    pcan.clear()
    bad = []
    for name, cid, fr in SESSION:
        st, _ = write_raw(c, ch, cid, fr)
        if st != 0:
            bad.append((name, hex(st)))
    check("T1 every ordinary TP 2.0 frame is accepted", not bad, bad)
    time.sleep(0.3)
    n = sum(len(v) for v in pcan.seen.values())
    check("T2 all %d reached the bus" % len(SESSION), n == len(SESSION), n)


def sweep(c, ch, pcan, label, make, xs, seqs):
    """make(seq, x) -> frame bytes. Returns (refused list, statuses, n, on-bus count, rtts)."""
    pcan.clear()
    refused, rtts = [], []
    n = 0
    for sq in seqs:
        for x in xs:
            st, dt = write_raw(c, ch, TP_ID, make(sq, x))
            rtts.append(dt)
            n += 1
            if st != 0:
                refused.append((sq, hex(x), hex(st)))
    time.sleep(0.4)
    on_bus = len(pcan.seen.get(TP_ID, []))
    print("   %s: %d writes, %d refused, %d on the bus, rtt median %.1f ms" % (
        label, n, len(refused), on_bus, statistics.median(rtts) * 1000), flush=True)
    return refused, n, on_bus, rtts


def leg_s(c, ch, pcan, dut):
    print("== S: the sweep over the bytes the old gate read", flush=True)
    xs = list(range(256))
    seqs = [0, 7, 15]
    w0 = dut.ring_count(GATE_LINE)
    seen0 = (dut.get("/api/j2534") or {}).get("reflash_seen")
    r1, n1, bus1, rt1 = sweep(c, ch, pcan, "0x1N 00 xx ..", lambda sq, x: [0x10 | sq, 0x00, x, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5], xs, seqs)
    check("S1 0x1N 00 xx: nothing refused, all %d on the bus" % n1, not r1 and bus1 == n1, (r1[:6], bus1))
    r0, n0, bus0, rt0 = sweep(c, ch, pcan, "0x0N xx ..", lambda sq, x: [0x00 | sq, x, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6], xs, seqs)
    check("S2 0x0N xx: nothing refused, all %d on the bus" % n0, not r0 and bus0 == n0, (r0[:6], bus0))
    xs2 = list(range(0x30, 0x40))
    r2, n2, bus2, _ = sweep(c, ch, pcan, "0x2N xx ..", lambda sq, x: [0x20 | sq, x, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6], xs2, seqs)
    r3, n3, bus3, _ = sweep(c, ch, pcan, "0x3N 00 xx ..", lambda sq, x: [0x30 | sq, 0x00, x, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5], xs2, seqs)
    check("S3 0x2N / 0x3N: nothing refused, all on the bus", not r2 and not r3 and bus2 == n2 and bus3 == n3, (r2[:3], r3[:3], bus2, n2, bus3, n3))
    short = [([0x03, 0x34], "03 34"), ([0x13, 0x34], "13 34"), ([0x13, 0x00, 0x36], "13 00 36")]
    bad = []
    for fr, name in short:
        st, _ = write_raw(c, ch, TP_ID, fr)
        if st != 0:
            bad.append((name, hex(st)))
    check("S4 the short forms the old gate refused (03 34, 13 00 36) pass", not bad, bad)
    time.sleep(0.5)
    w1 = dut.ring_count(GATE_LINE)
    seen1 = (dut.get("/api/j2534") or {}).get("reflash_seen")
    check("S5 no gate line in the device log (%s -> %s)" % (w0, w1), w1 == w0, (w0, w1))
    check("S6 reflash_seen unchanged by raw CAN (%s -> %s): raw frames are not inspected" % (seen0, seen1), seen0 == seen1, (seen0, seen1))
    rts = rt1 + rt0
    print("   WRITE round trip over this path: median %.1f ms, p95 %.1f ms, max %.1f ms (n=%d)" % (
        statistics.median(rts) * 1000, sorted(rts)[int(len(rts) * 0.95)] * 1000, max(rts) * 1000, len(rts)), flush=True)


def leg_d(py32, jh, jp, tag):
    print("== D: the installed wican_j2534.dll under the 32-bit Python (%s)" % tag, flush=True)
    if not py32 or not os.path.exists(py32):
        print("SKIP: no 32-bit Python at %r" % py32, flush=True)
        return None
    log = os.path.join(os.environ.get("TEMP", HERE), "wican_j2534_tp20_dll_%s.log" % tag)
    env = dict(os.environ, WICAN_J2534_HOST=jh if jh != "localhost" else "127.0.0.1", WICAN_J2534_PORT=str(jp),
               WICAN_J2534_LOG=log)
    script = os.path.join(HERE, "j2534_tp20_dll_check.py")
    try:
        p = subprocess.run([py32, "-I", script], env=env, capture_output=True, text=True, timeout=180)
        out = p.stdout + p.stderr
    except Exception as e:
        out, p = "%s: %s" % (type(e).__name__, e), None
    for ln in out.rstrip().splitlines():
        print("   " + ln, flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--j2534", default="localhost:18534")
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--py32", default=r"C:\Users\Ali\idf_tmp\py32\python.exe")
    a = ap.parse_args()

    dut = ab.Dut(a.dut)
    jh, jp = a.j2534.split(":")[0], int(a.j2534.split(":")[1])
    t0 = time.time()
    planned = 0

    st = dut.get("/api/status")
    if not st:
        print("TP20 RAW CAN FAIL: no DUT at", a.dut)
        sys.exit(1)
    boot0, unexp0 = st.get("boot_count"), st.get("unexpected_resets")
    err0 = (st.get("health") or {}).get("log_errors")
    print("dut: %s boot_count=%s unexpected=%s log_errors=%s" % (st.get("version"), boot0, unexp0, err0), flush=True)
    found = {c: dut.settings(c) for c in ("j2534_server",)}
    print("as found: j2534_server", json.dumps(found["j2534_server"]), flush=True)
    groups = (dut.get("/api/autopid") or {}).get("groups", [])
    group_on = [g["name"] for g in groups if g.get("enabled")]
    try:
        pcan = ab.Pcan(a.pcan)
    except Exception as e:
        print("TP20 RAW CAN FAIL: PCAN %s not usable: %s" % (a.pcan, e))
        sys.exit(1)

    def submit(objs, why):
        nonlocal planned
        for comp, obj in objs.items():
            dut.api("/api/settings/" + comp, "PUT", obj)
        r = dut.api("/api/settings/submit", "POST")
        if r.get("reboot"):
            planned += 1
            print("restart %d (%s)..." % (planned, why), flush=True)
            time.sleep(6)
            if not dut.wait_up():
                print("TP20 RAW CAN FAIL: the DUT did not come back (%s)" % why)
                sys.exit(1)
            time.sleep(2)

    def pause_autopid(on):
        for g in group_on:
            try:
                dut.api("/api/autopid/group", "POST", {"name": g, "enabled": on})
            except Exception:
                pass

    print("== C: configure", flush=True)
    j = json.loads(json.dumps(found["j2534_server"]))
    j["enabled"] = True
    j["allow_lan"] = True
    submit({"j2534_server": j}, "the configuration")
    pause_autopid(False)
    time.sleep(1.5)

    try:
        c, ch = open_tester(jh, jp)
        leg_t(c, ch, pcan)
        leg_s(c, ch, pcan, dut)
        c.call(b.DISCONNECT, channel=ch)
        c.call(b.CLOSE)
        c.s.close()
        time.sleep(0.5)
        out = leg_d(a.py32, jh, jp, "rawcan")
        check("D1 the DLL passes the two frames the old gate refused (GATE FRAME OK, status 0x0)",
              out is not None and out.count("GATE FRAME OK") == 2 and "REFUSED" not in out, "see the lines above")
        check("D2 the DLL passes the ordinary TP 2.0 frames", out is not None and "ORDINARY OK" in out)
    except Exception as e:
        check("the legs ran to the end", False, "%s: %s" % (type(e).__name__, e))

    print("== X: restore", flush=True)
    submit(found, "the settings as found")
    pause_autopid(True)
    back = {k: dut.settings(k) for k in found}
    check("X1 settings back as found", back == found, {k: "same" if back[k] == found[k] else "DIFFERS" for k in found})
    st = dut.get("/api/status") or {}
    check("X2 only the planned restarts (%d)" % planned, st.get("boot_count") == boot0 + planned and st.get("unexpected_resets") == unexp0,
          {"boot_count": (boot0, st.get("boot_count")), "unexpected": (unexp0, st.get("unexpected_resets"))})
    err1 = (st.get("health") or {}).get("log_errors")
    check("X3 no new E lines", err1 == err0 or err1 == 0, (err0, err1))
    pcan.close()

    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t0), flush=True)
    if fails:
        print("TP20 RAW CAN FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("TP20 RAW CAN PASS")


if __name__ == "__main__":
    main()
