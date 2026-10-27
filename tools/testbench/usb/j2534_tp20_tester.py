"""The tool side of a TP 2.0-shaped block transfer through the WiCAN J2534
DLL (32-bit Python, ctypes). Pairs with j2534_tp20_responder.py on PCAN.
Run it under C:\\Users\\Ali\\idf_tmp\\py32\\python.exe -I (the DLL is 32-bit).

Per block: PassThruWriteMsgs the request [0x1N 00 02 1A bb], then
PassThruReadMsgs until the 37 data frames arrived, answering every
0x0N / 0x1N frame with the ACK [0xB(N+1)] through PassThruWriteMsgs, the
way a TP 2.0 tool does. Reports bytes/s and the projected time for a 2 MB
read, the write round trip and the frame-read latency.

Env: WICAN_J2534_HOST/PORT (TCP) or WICAN_J2534_COM=auto (CDC serial),
WICAN_J2534_LOG, WICAN_J2534_DLL. Args: --blocks N (default 40).
Prints TESTER DONE ... or TESTER FAIL ...
"""
import ctypes
import os
import statistics
import struct
import sys
import time

DLL = os.environ.get("WICAN_J2534_DLL", r"C:\Program Files (x86)\WiCAN\wican_j2534.dll")
CAN = 5
REQ_ID, DATA_ID = 0x740, 0x300
BLOCKS = int(sys.argv[sys.argv.index("--blocks") + 1]) if "--blocks" in sys.argv else 40
BATCH = int(sys.argv[sys.argv.index("--batch") + 1]) if "--batch" in sys.argv else 1   # messages per PassThruReadMsgs
FRAMES_PER_BLOCK = 37


class PASSTHRU_MSG(ctypes.Structure):
    _fields_ = [("ProtocolID", ctypes.c_ulong), ("RxStatus", ctypes.c_ulong),
                ("TxFlags", ctypes.c_ulong), ("Timestamp", ctypes.c_ulong),
                ("DataSize", ctypes.c_ulong), ("ExtraDataIndex", ctypes.c_ulong),
                ("Data", ctypes.c_ubyte * 4128)]


d = ctypes.WinDLL(DLL)
d.PassThruOpen.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
d.PassThruConnect.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
d.PassThruWriteMsgs.argtypes = [ctypes.c_ulong, ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
d.PassThruReadMsgs.argtypes = [ctypes.c_ulong, ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
d.PassThruGetLastError.argtypes = [ctypes.c_char_p]
d.PassThruDisconnect.argtypes = [ctypes.c_ulong]
d.PassThruClose.argtypes = [ctypes.c_ulong]
for f in ("PassThruOpen", "PassThruConnect", "PassThruWriteMsgs", "PassThruReadMsgs", "PassThruGetLastError",
          "PassThruDisconnect", "PassThruClose"):
    getattr(d, f).restype = ctypes.c_long


def lasterr():
    buf = ctypes.create_string_buffer(256)
    d.PassThruGetLastError(buf)
    return buf.value.decode(errors="replace")


def write(ch, can_id, frame):
    m = PASSTHRU_MSG()
    m.ProtocolID = CAN
    data = struct.pack(">I", can_id) + bytes(frame)
    m.DataSize = len(data)
    ctypes.memmove(m.Data, data, len(data))
    n = ctypes.c_ulong(1)
    t0 = time.perf_counter()
    st = d.PassThruWriteMsgs(ch, ctypes.byref(m), ctypes.byref(n), 0)
    return st, time.perf_counter() - t0


RX = (PASSTHRU_MSG * 16)()


def read_some(ch, timeout_ms):
    n = ctypes.c_ulong(BATCH)
    st = d.PassThruReadMsgs(ch, RX, ctypes.byref(n), timeout_ms)
    out = []
    if st == 0:
        for i in range(n.value):
            out.append(bytes(RX[i].Data[:RX[i].DataSize]))
    return out


dev = ctypes.c_ulong(0)
st = d.PassThruOpen(None, ctypes.byref(dev))
if st != 0:
    print("TESTER FAIL: PassThruOpen 0x%X (%s)" % (st, lasterr()))
    sys.exit(1)
ch = ctypes.c_ulong(0)
st = d.PassThruConnect(dev.value, CAN, 0, 500000, ctypes.byref(ch))
if st != 0:
    print("TESTER FAIL: PassThruConnect 0x%X (%s)" % (st, lasterr()))
    d.PassThruClose(dev.value)
    sys.exit(1)
ch = ch.value
print("tester: open, channel %d, target %s, %d message(s) per PassThruReadMsgs" % (
    ch, os.environ.get("WICAN_J2534_HOST") or os.environ.get("WICAN_J2534_COM") or "registry", BATCH), flush=True)
# what the DLL's Sleep(1) poll in PassThruReadMsgs costs on this PC (the Windows timer granularity)
_k32 = ctypes.windll.kernel32
_t0 = time.perf_counter()
for _ in range(20):
    _k32.Sleep(1)
print("kernel32 Sleep(1) granularity here: %.2f ms per call" % ((time.perf_counter() - _t0) / 20 * 1000), flush=True)

while read_some(ch, 0):
    pass

wr_rtt, ack_rtt, blk_times, fails = [], [], [], []
total_bytes = 0
seq = 0
t_all = time.perf_counter()
for blk in range(BLOCKS):
    t0 = time.perf_counter()
    st, dt = write(ch, REQ_ID, [0x10 | seq, 0x00, 0x02, 0x1A, blk & 0xFF])
    seq = (seq + 1) & 0xF
    wr_rtt.append(dt)
    if st != 0:
        fails.append("block %d: request refused 0x%X (%s)" % (blk, st, lasterr()))
        break
    got = 0
    expect_seq = None
    deadline = time.perf_counter() + 5.0
    done = False
    while not done and time.perf_counter() < deadline:
        for fr in read_some(ch, 20):
            if len(fr) < 5 or struct.unpack(">I", fr[:4])[0] != DATA_ID:
                continue
            op, s = fr[4] >> 4, fr[4] & 0xF
            if expect_seq is not None and s != expect_seq:
                fails.append("block %d: frame %d sequence %X, expected %X (a lost or reordered frame)" % (blk, got, s, expect_seq))
            expect_seq = (s + 1) & 0xF
            got += 1
            total_bytes += len(fr) - 5
            if op in (0x0, 0x1):
                st, dt = write(ch, REQ_ID, [0xB0 | expect_seq])
                ack_rtt.append(dt)
                if st != 0:
                    fails.append("block %d: ACK refused 0x%X" % (blk, st))
            if op == 0x1:
                done = True
                break
    if not done:
        fails.append("block %d: incomplete, %d of %d frames within 5 s" % (blk, got, FRAMES_PER_BLOCK))
        break
    blk_times.append(time.perf_counter() - t0)
elapsed = time.perf_counter() - t_all
write(ch, REQ_ID, [0xA8])
d.PassThruDisconnect(ch)
d.PassThruClose(dev.value)

if wr_rtt:
    wr_rtt.sort()
    print("request write RTT (n=%d): median %.1f ms, p95 %.1f ms, max %.1f ms" % (
        len(wr_rtt), statistics.median(wr_rtt) * 1000, wr_rtt[int(len(wr_rtt) * 0.95)] * 1000, wr_rtt[-1] * 1000), flush=True)
if ack_rtt:
    ack_rtt.sort()
    print("ACK write RTT (n=%d): median %.1f ms, p95 %.1f ms, max %.1f ms" % (
        len(ack_rtt), statistics.median(ack_rtt) * 1000, ack_rtt[int(len(ack_rtt) * 0.95)] * 1000, ack_rtt[-1] * 1000), flush=True)
if blk_times:
    bps = (len(blk_times) * 255) / sum(blk_times)
    proj_min = 2097152 / bps / 60.0
    print("blocks %d, %d data bytes in %.2f s: %.0f B/s, per block median %.1f ms, max %.1f ms; PROJECTED 2 MB read: %.1f min" % (
        len(blk_times), len(blk_times) * 255, sum(blk_times), bps, statistics.median(blk_times) * 1000, max(blk_times) * 1000, proj_min), flush=True)
if fails:
    print("TESTER FAIL: " + "; ".join(fails[:6]), flush=True)
    sys.exit(1)
print("TESTER DONE", flush=True)
