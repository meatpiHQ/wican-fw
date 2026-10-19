#!/usr/bin/env python3
"""A standard J2534 tool through the PassThru server  ->  J2534 STD TOOL PASS

The field report of 2026-10-19: every UDS request of a udsoncan tester timed out
because the device took an ISO15765 message's data as the bare payload while SAE
J2534-1 (and every tool) puts the 4-byte CAN id first and the pad flag on each
message. The Windows DLL copies PASSTHRU_MSGs verbatim, so a tool's bytes can be
replayed byte-exact over the wire protocol; the real DLL + udsoncan run too when
the 32-bit Python is present. PCAN_USBBUS2 is the witness on the bus; the ECU
simulator answers 7E0 (and 7E1) with UDS and 7DF with OBD-II.

Legs (one planned restart to configure, one to restore):
  C  configure: j2534 on with allow_lan, autopid's groups paused
  U  udsoncan 1.26.1 byte-exact: CONNECT(ISO15765, flags 0, no ids), SET_CONFIG,
     CLEAR_MSG_FILTERS, the FLOW_CONTROL filter (pad in its TxFlags), WRITE
     [00 00 07 E0][10 03] pad -> the bus sees 7E0 dlc 8 [02 10 03 AA ..], the TxDone
     indication (rx_status 0x09, data = the id), the reply [00 00 07 E8][50 03 ..]
  M  multi-frame: 22 F1 90 -> START_OF_MESSAGE (0x02, data 00 00 07 E8) then the
     17-byte VIN behind 62 F1 90, the id first
  P  padding per message: tx_flags 0 -> 7E0 dlc 3 [02 10 03], still answered
  E  two ECUs on one channel: a second FLOW_CONTROL filter 7E1/7E9 answers too, a
     long request to an id without a session is ERR_NO_FLOW_CONTROL, STOP_FILTER
     closes the session (its long request refused after), the first ECU still works
  F  functional addressing (the filters cleared first: a flow-control session
     passes its id's frames too): [00 00 07 DF][01 00] goes out as one frame and
     nothing is delivered; with a PASS filter on 7E8 the answer 41 00 comes back
     as an ISO15765 message with the id
  G  the reflash gate reads the SID behind the id: 34 .. -> ERR_NOT_SUPPORTED;
     ISO15765_ADDR_TYPE -> ERR_INVALID_MSG
  D  the installed wican_j2534.dll driven by udsoncan under the 32-bit Python
     (--py32; skipped with a note when that interpreter is absent)
  X  restore: settings as found, only the planned restarts, no new E lines

usage (PC, PCAN wired, the tunnel of ports 80 and 6809 to the DUT):
  python j2534_std_tool_bench.py --dut localhost:18081 --j2534 localhost:18534
       [--pcan PCAN_USBBUS2] [--py32 C:\\Users\\Ali\\idf_tmp\\py32\\python.exe]
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_argv = sys.argv
sys.argv = [sys.argv[0]]
import j2534_bench as b  # noqa: E402  (its module-level argv parsing wants no args)
import j2534_auto_baud_bench as ab  # noqa: E402  (Dut, Pcan)
sys.argv = _argv

START_FILTER, STOP_FILTER, IOCTL = 0x11, 0x12, 0x15
PASS_FILTER, FLOW_CONTROL_FILTER = 1, 3
IOCTL_SET_CONFIG, IOCTL_CLEAR_TX, IOCTL_CLEAR_RX, IOCTL_CLEAR_FILTERS = 0x02, 0x07, 0x08, 0x0A
ERR_NOT_SUPPORTED, ERR_INVALID_MSG, ERR_NO_FLOW_CONTROL = 0x01, 0x0A, 0x16
TX_ADDR_TYPE = 0x80
RX_TXDONE = b.RX_TX_MSG_TYPE | b.RX_TX_INDICATION
VIN = b"1WCAN0FW0P0000001"

fails, passes = [], []


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


def msg(data, tx_flags=0, rx_status=0, extra=0):
    return b.MSG.pack(b.PROT_ISO15765, rx_status, tx_flags, 0, extra, len(data)) + data


def write_collect(c, channel, data, tx_flags, secs):
    """WRITE_MSGS one ISO15765 message; every RX_MSG of the channel from that instant
    as (rx_status, data). Returns (ack, [(rx_status, data), ...])."""
    payload = struct.pack("<I", 1) + msg(data, tx_flags)
    c.seq = (c.seq + 1) & 0xFFFF
    c.s.sendall(b.HDR.pack(b.MAGIC, 1, b.WRITE_MSGS, c.seq, channel, len(payload)) + payload)
    c.s.settimeout(0.2)
    ack, got = None, []
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            rt, rseq, rch, body = c._read_frame()
        except Exception:
            continue
        if rt == b.ACK:
            ack = struct.unpack_from("<I", body, 0)[0]
        elif rt == b.RX_MSG and rch == channel:
            f = b.MSG.unpack_from(body, 0)
            got.append((f[1], body[b.MSG.size:b.MSG.size + f[5]]))
    c.s.settimeout(5)
    return ack, got


def replies(got):
    return [d for s, d in got if not (s & (b.RX_TX_MSG_TYPE | b.RX_START_OF_MESSAGE))]


def indications(got, rx_status):
    return [d for s, d in got if (s & 0x0B) == rx_status]


def fc_filter(c, ch, rx, tx, pad=True):
    fl = b.TX_PAD if pad else 0
    mask = msg(b"\xFF\xFF\xFF\xFF", fl)
    patt = msg(struct.pack(">I", rx), fl)
    flow = msg(struct.pack(">I", tx), fl)
    return c.call(START_FILTER, ch, struct.pack("<I", FLOW_CONTROL_FILTER) + mask + patt + flow)


def pass_filter(c, ch, can_id):
    mask = msg(b"\xFF\xFF\xFF\xFF")
    patt = msg(struct.pack(">I", can_id))
    return c.call(START_FILTER, ch, struct.pack("<I", PASS_FILTER) + mask + patt)


def open_tester(jh, jp):
    c = b.Client(jh, jp)
    c.call(b.HELLO, payload=struct.pack("<I", 1))   # the DLL sends its wire version
    c.call(b.OPEN)
    return c


def hexs(d):
    return d.hex(" ").upper()


def leg_u(c, pcan):
    print("== U: udsoncan through the DLL, byte-exact", flush=True)
    st, ch, _ = c.call(b.CONNECT, payload=struct.pack("<IIIII", b.PROT_ISO15765, 0, 500000, 0, 0))
    check("U1 CONNECT(ISO15765, flags 0, no ids) accepted", st == 0 and ch == 1, (hex(st), ch))
    st1, _, _ = c.call(IOCTL, ch, struct.pack("<I", IOCTL_SET_CONFIG))
    st2, _, _ = c.call(IOCTL, ch, struct.pack("<I", IOCTL_CLEAR_FILTERS))
    st3, fid, _ = fc_filter(c, ch, 0x7E8, 0x7E0)
    st4, _, _ = c.call(IOCTL, ch, struct.pack("<I", IOCTL_CLEAR_RX))
    st5, _, _ = c.call(IOCTL, ch, struct.pack("<I", IOCTL_CLEAR_TX))
    check("U2 SET_CONFIG, CLEAR_MSG_FILTERS, FLOW_CONTROL 7E0/7E8, CLEAR buffers all NOERROR",
          st1 == 0 and st2 == 0 and st3 == 0 and st4 == 0 and st5 == 0, (hex(st1), hex(st2), hex(st3), hex(st4), hex(st5)))
    pcan.clear()
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7E0) + b"\x10\x03", b.TX_PAD, 1.0)
    rep = replies(got)
    check("U3 WRITE [00 00 07 E0][10 03] pad -> ACK NOERROR", ack == 0, ack)
    check("U4 the bus saw 7E0 dlc 8 [02 10 03 AA AA AA AA AA]", pcan.saw(0x7E0, "021003aaaaaaaaaa", 1.0),
          {hex(k): v[:1] for k, v in pcan.seen.items()})
    check("U5 the TxDone indication came (rx_status 0x09, data 00 00 07 E0)",
          any(d == b"\x00\x00\x07\xE0" for d in indications(got, RX_TXDONE)), [(hex(s), hexs(d)) for s, d in got][:3])
    check("U6 the reply is [00 00 07 E8][50 03 ..] (the id first)", any(d[:6] == b"\x00\x00\x07\xE8\x50\x03" for d in rep),
          [hexs(d) for d in rep][:2])
    return ch


def leg_m(c, ch):
    print("== M: a multi-frame answer", flush=True)
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7E0) + b"\x22\xF1\x90", b.TX_PAD, 1.5)
    rep = replies(got)
    vin = [d for d in rep if d[:7] == b"\x00\x00\x07\xE8\x62\xF1\x90"]
    check("M1 22 F1 90 -> [00 00 07 E8][62 F1 90 + the 17-byte VIN]", ack == 0 and vin and vin[0][7:24] == VIN,
          [hexs(d) for d in rep][:2])
    check("M2 START_OF_MESSAGE (0x02, data 00 00 07 E8) before it",
          any(d == b"\x00\x00\x07\xE8" for d in indications(got, b.RX_START_OF_MESSAGE)), [(hex(s), len(d)) for s, d in got][:4])
    order = [s & 0x0B for s, d in got]
    check("M3 the order: TxDone, START_OF_MESSAGE, the message", order[:3] == [RX_TXDONE, b.RX_START_OF_MESSAGE, 0], order)


def leg_p(c, ch, pcan):
    print("== P: padding follows each message", flush=True)
    pcan.clear()
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7E0) + b"\x10\x03", 0, 1.0)
    check("P1 tx_flags 0 -> the bus saw 7E0 dlc 3 [02 10 03] (no padding)", ack == 0 and pcan.saw(0x7E0, "021003", 1.0) and
          not any(d.startswith("021003aa") for d in pcan.seen.get(0x7E0, [])), pcan.seen.get(0x7E0, [])[:2])
    check("P2 still answered 50 03", any(d[:6] == b"\x00\x00\x07\xE8\x50\x03" for d in replies(got)), [hexs(d) for d in replies(got)][:2])
    pcan.clear()
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7E0) + b"\x10\x03", b.TX_PAD, 1.0)
    check("P3 pad again -> dlc 8 again", ack == 0 and pcan.saw(0x7E0, "021003aaaaaaaaaa", 1.0), pcan.seen.get(0x7E0, [])[:2])


def leg_e(c, ch):
    print("== E: two ECUs on one channel", flush=True)
    st, fid2, _ = fc_filter(c, ch, 0x7E9, 0x7E1)
    check("E1 a second FLOW_CONTROL filter 7E1/7E9 accepted", st == 0 and fid2, (hex(st), fid2))
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7E1) + b"\x10\x03", b.TX_PAD, 1.0)
    check("E2 10 03 to 7E1 answered from 7E9", ack == 0 and any(d[:6] == b"\x00\x00\x07\xE9\x50\x03" for d in replies(got)),
          [hexs(d) for d in replies(got)][:2])
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7E0) + b"\x10\x03", b.TX_PAD, 1.0)
    check("E3 the first ECU still answers", ack == 0 and any(d[:6] == b"\x00\x00\x07\xE8\x50\x03" for d in replies(got)),
          [hexs(d) for d in replies(got)][:2])
    long_req = struct.pack(">I", 0x7E2) + b"\x2E\xF1\x90" + VIN
    ack, got = write_collect(c, ch, long_req, b.TX_PAD, 0.5)
    check("E4 a 20-byte request to an id without a session is ERR_NO_FLOW_CONTROL", ack == ERR_NO_FLOW_CONTROL, ack)
    st, _, _ = c.call(STOP_FILTER, ch, struct.pack("<I", fid2))
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7E1) + b"\x2E\xF1\x90" + VIN, b.TX_PAD, 0.5)
    check("E5 STOP_FILTER closes 7E1's session: its long request is refused", st == 0 and ack == ERR_NO_FLOW_CONTROL, (hex(st), ack))


def leg_f(c, ch):
    print("== F: functional addressing", flush=True)
    # a flow-control session delivers everything on its id (J2534-1: it passes
    # as a filter too), so the negative check runs with the sessions closed
    st, _, _ = c.call(IOCTL, ch, struct.pack("<I", IOCTL_CLEAR_FILTERS))
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7DF) + b"\x01\x00", b.TX_PAD, 0.8)
    check("F1 with the filters cleared, [00 00 07 DF][01 00] goes out as one frame (ACK NOERROR) and nothing is delivered",
          st == 0 and ack == 0 and not replies(got), (hex(st), ack, [hexs(d) for d in replies(got)][:2]))
    st, pid, _ = pass_filter(c, ch, 0x7E8)
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7DF) + b"\x01\x00", b.TX_PAD, 1.0)
    rep = replies(got)
    check("F2 with a PASS filter on 7E8 the answer comes as [00 00 07 E8][41 00 ..]",
          st == 0 and ack == 0 and any(d[:6] == b"\x00\x00\x07\xE8\x41\x00" for d in rep), (hex(st), [hexs(d) for d in rep][:2]))
    c.call(STOP_FILTER, ch, struct.pack("<I", pid))


def leg_g(c, ch):
    print("== G: the reflash gate and the unsupported flag", flush=True)
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7E0) + b"\x34\x00\x44\x00\x00\x00\x04\x00", b.TX_PAD, 0.3)
    check("G1 34 RequestDownload behind the id -> ERR_NOT_SUPPORTED", ack == ERR_NOT_SUPPORTED, ack)
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7E0) + b"\xF1\x10\x03", b.TX_PAD | TX_ADDR_TYPE, 0.3)
    check("G2 ISO15765_ADDR_TYPE -> ERR_INVALID_MSG", ack == ERR_INVALID_MSG, ack)
    ack, got = write_collect(c, ch, struct.pack(">I", 0x7E0), b.TX_PAD, 0.3)
    check("G3 the id alone -> ERR_INVALID_MSG", ack == ERR_INVALID_MSG, ack)


def leg_d(py32, jh, jp, dll=None):
    print("== D: the %s driven by udsoncan" % (dll or "installed DLL"), flush=True)
    if not py32 or not os.path.exists(py32):
        print("SKIP: no 32-bit Python at %r (--py32): the real-DLL leg needs it" % py32, flush=True)
        return
    env = dict(os.environ, WICAN_J2534_HOST=jh if jh != "localhost" else "127.0.0.1", WICAN_J2534_PORT=str(jp),
               WICAN_UDS_TXID="7E0", WICAN_UDS_RXID="7E8")
    if dll:
        env["WICAN_J2534_DLL"] = dll   # a freshly built DLL, before it is installed
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "j2534_udsoncan_check.py")
    try:
        p = subprocess.run([py32, "-I", script], env=env, capture_output=True, text=True, timeout=60)
        out = p.stdout + p.stderr
    except Exception as e:
        out, p = "%s: %s" % (type(e).__name__, e), None
    for ln in out.rstrip().splitlines():
        print("   " + ln, flush=True)
    ok = p is not None and p.returncode == 0
    check("D1 udsoncan over wican_j2534.dll: 10 03, the VIN and TesterPresent answered", ok and "OK: DiagnosticSessionControl" in out
          and ("OK: ReadDataByIdentifier" in out) and "OK: TesterPresent" in out, "exit %s" % (p.returncode if p else "none"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--j2534", default="localhost:18534")
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--py32", default=r"C:\Users\Ali\idf_tmp\py32\python.exe")
    ap.add_argument("--dll", default=None, help="a wican_j2534.dll to drive instead of the installed one")
    a = ap.parse_args()

    dut = ab.Dut(a.dut)
    jh, jp = a.j2534.split(":")[0], int(a.j2534.split(":")[1])
    t0 = time.time()
    planned = 0

    st = dut.get("/api/status")
    if not st:
        print("J2534 STD TOOL FAIL: no DUT at", a.dut)
        sys.exit(1)
    boot0, unexp0 = st.get("boot_count"), st.get("unexpected_resets")
    err0 = (st.get("health") or {}).get("log_errors")
    print("dut: %s boot_count=%s unexpected=%s log_errors=%s" % (st.get("version"), boot0, unexp0, err0), flush=True)
    found = {c: dut.settings(c) for c in ("j2534_server",)}
    groups = (dut.get("/api/autopid") or {}).get("groups", [])
    group_on = [g["name"] for g in groups if g.get("enabled")]
    try:
        pcan = ab.Pcan(a.pcan)
    except Exception as e:
        print("J2534 STD TOOL FAIL: PCAN %s not usable: %s" % (a.pcan, e))
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
                print("J2534 STD TOOL FAIL: the DUT did not come back (%s)" % why)
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
    ring0 = dut.ring_count("E (")

    try:
        c = open_tester(jh, jp)
        ch = leg_u(c, pcan)
        leg_m(c, ch)
        leg_p(c, ch, pcan)
        leg_e(c, ch)
        leg_f(c, ch)
        leg_g(c, ch)
        c.call(b.DISCONNECT, channel=ch)
        c.call(b.CLOSE)
        c.s.close()
        time.sleep(0.5)
        leg_d(a.py32, jh, jp, a.dll)
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
        print("J2534 STD TOOL FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("J2534 STD TOOL PASS")


if __name__ == "__main__":
    main()
