"""The installed wican_j2534.dll driven like a TP 2.0 flasher would (raw CAN
channel, PassThruWriteMsgs per frame), under a 32-bit Python via ctypes.

Env: WICAN_J2534_HOST / WICAN_J2534_PORT (the DLL's target), WICAN_J2534_LOG
(the DLL's own log), WICAN_J2534_DLL (default: the installed one).
Prints one line per write with the status and PassThruGetLastError, then the
per-write round trip (200 ACK frames) and the request/answer turnaround to
the ECU simulator on 7DF (if it answers). The two "gate frames" are the TP 2.0
shapes the reflash gate refused until the 2026-10-27 firmware: they print
GATE FRAME OK now, REFUSED 0x1 on older firmware.
"""
import ctypes
import os
import statistics
import struct
import sys
import time

DLL = os.environ.get("WICAN_J2534_DLL", r"C:\Program Files (x86)\WiCAN\wican_j2534.dll")
CAN, ERR_BUFFER_EMPTY = 5, 0x10
TP_ID = 0x740


class PASSTHRU_MSG(ctypes.Structure):
    _fields_ = [("ProtocolID", ctypes.c_ulong), ("RxStatus", ctypes.c_ulong),
                ("TxFlags", ctypes.c_ulong), ("Timestamp", ctypes.c_ulong),
                ("DataSize", ctypes.c_ulong), ("ExtraDataIndex", ctypes.c_ulong),
                ("Data", ctypes.c_ubyte * 4128)]


def msg(can_id, frame):
    m = PASSTHRU_MSG()
    m.ProtocolID = CAN
    data = struct.pack(">I", can_id) + bytes(frame)
    m.DataSize = len(data)
    ctypes.memmove(m.Data, data, len(data))
    return m


d = ctypes.WinDLL(DLL)
d.PassThruOpen.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
d.PassThruConnect.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
d.PassThruWriteMsgs.argtypes = [ctypes.c_ulong, ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
d.PassThruReadMsgs.argtypes = [ctypes.c_ulong, ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
d.PassThruGetLastError.argtypes = [ctypes.c_char_p]
d.PassThruDisconnect.argtypes = [ctypes.c_ulong]
d.PassThruClose.argtypes = [ctypes.c_ulong]
d.PassThruReadVersion.argtypes = [ctypes.c_ulong, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
for f in ("PassThruOpen", "PassThruConnect", "PassThruWriteMsgs", "PassThruReadMsgs", "PassThruGetLastError",
          "PassThruDisconnect", "PassThruClose", "PassThruReadVersion"):
    getattr(d, f).restype = ctypes.c_long


def lasterr():
    buf = ctypes.create_string_buffer(256)
    d.PassThruGetLastError(buf)
    return buf.value.decode(errors="replace")


def write(ch, can_id, frame, timeout=0):
    m = msg(can_id, frame)
    n = ctypes.c_ulong(1)
    t0 = time.perf_counter()
    st = d.PassThruWriteMsgs(ch, ctypes.byref(m), ctypes.byref(n), timeout)
    return st, time.perf_counter() - t0


def read_one(ch, timeout_ms):
    m = PASSTHRU_MSG()
    n = ctypes.c_ulong(1)
    st = d.PassThruReadMsgs(ch, ctypes.byref(m), ctypes.byref(n), timeout_ms)
    if st == 0 and n.value == 1:
        return bytes(m.Data[:m.DataSize])
    return None


dev = ctypes.c_ulong(0)
st = d.PassThruOpen(None, ctypes.byref(dev))
fw, dl, api = ctypes.create_string_buffer(80), ctypes.create_string_buffer(80), ctypes.create_string_buffer(80)
d.PassThruReadVersion(dev.value, fw, dl, api)
print("PassThruOpen: 0x%X (%s) device %d; %s / %s / API %s; target %s:%s" % (
    st, lasterr(), dev.value, fw.value.decode(), dl.value.decode(), api.value.decode(),
    os.environ.get("WICAN_J2534_HOST"), os.environ.get("WICAN_J2534_PORT")), flush=True)
if st != 0:
    sys.exit(1)
ch = ctypes.c_ulong(0)
st = d.PassThruConnect(dev.value, CAN, 0, 500000, ctypes.byref(ch))
print("PassThruConnect(CAN, 0, 500000): 0x%X channel %d" % (st, ch.value), flush=True)
if st != 0:
    d.PassThruClose(dev.value)
    sys.exit(1)
ch = ch.value

ordinary = [
    ("channel setup", 0x200, [0x01, 0xC0, 0x00, 0x10, 0x00, 0x03, 0x01]),
    ("parameters A0", TP_ID, [0xA0, 0x0F, 0x8A, 0xFF, 0x32, 0xFF]),
    ("KWP request 10 00 02 1A 9B", TP_ID, [0x10, 0x00, 0x02, 0x1A, 0x9B]),
    ("ACK B1", TP_ID, [0xB1]),
    ("a last frame whose 2nd payload byte is 0x33", TP_ID, [0x12, 0x00, 0x33, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5]),
    ("a block-end frame whose 1st payload byte is 0x38", TP_ID, [0x05, 0x38, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6]),
]
ok_all = True
for name, cid, fr in ordinary:
    st, dt = write(ch, cid, fr)
    print("write %03X [%s] (%s): 0x%X lasterr=%r %.1f ms" % (cid, bytes(fr).hex(" ").upper(), name, st, lasterr(), dt * 1000), flush=True)
    ok_all = ok_all and st == 0
print("ORDINARY OK" if ok_all else "ORDINARY FAILED", flush=True)

gate = [
    ("a last frame whose 2nd payload byte is 0x34", TP_ID, [0x11, 0x00, 0x34, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5]),
    ("a block-end frame whose 1st payload byte is 0x36", TP_ID, [0x02, 0x36, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6]),
]
refused = 0
for name, cid, fr in gate:
    st, dt = write(ch, cid, fr)
    tag = "REFUSED 0x%X lasterr=%r" % (st, lasterr()) if st else "GATE FRAME OK"
    print("write %03X [%s] (%s): %s %.1f ms" % (cid, bytes(fr).hex(" ").upper(), name, tag, dt * 1000), flush=True)
    refused += 1 if st else 0

# per-write round trip: 200 one-byte ACK frames, the flasher's most frequent write
rt = []
for i in range(200):
    st, dt = write(ch, TP_ID, [0xB0 | (i & 0xF)])
    rt.append(dt)
rt.sort()
print("WRITE RTT (200 x ACK frame): median %.1f ms, p95 %.1f ms, max %.1f ms" % (
    statistics.median(rt) * 1000, rt[int(len(rt) * 0.95)] * 1000, rt[-1] * 1000), flush=True)

# an empty read's cost
rd = []
for i in range(100):
    t0 = time.perf_counter()
    read_one(ch, 0)
    rd.append(time.perf_counter() - t0)
print("empty PassThruReadMsgs(timeout 0): median %.3f ms" % (statistics.median(rd) * 1000), flush=True)

# request/answer turnaround: 7DF [02 01 00] -> the ECU simulator's 7E8 answer
turn, miss = [], 0
for i in range(50):
    while read_one(ch, 0) is not None:
        pass
    t0 = time.perf_counter()
    st, _ = write(ch, 0x7DF, [0x02, 0x01, 0x00, 0, 0, 0, 0, 0])
    got = None
    deadline = t0 + 1.0
    while time.perf_counter() < deadline:
        r = read_one(ch, 50)
        if r is not None and r[:4] == b"\x00\x00\x07\xE8":
            got = r
            break
    if got is None:
        miss += 1
    else:
        turn.append(time.perf_counter() - t0)
if turn:
    turn.sort()
    print("REQUEST/ANSWER (7DF -> 7E8, 50 tries, %d unanswered): median %.1f ms, p95 %.1f ms, max %.1f ms" % (
        miss, statistics.median(turn) * 1000, turn[int(len(turn) * 0.95)] * 1000, turn[-1] * 1000), flush=True)
else:
    print("REQUEST/ANSWER: no 7E8 answer in 50 tries (the simulator silent?)", flush=True)

d.PassThruDisconnect(ch)

# a device refusal in words (driver 1.5): CONNECT at a bit rate the bus does not run at
ch2 = ctypes.c_ulong(0)
st = d.PassThruConnect(dev.value, CAN, 0, 250000, ctypes.byref(ch2))
print("PassThruConnect(CAN, 0, 250000): 0x%X lasterr=%r" % (st, lasterr()), flush=True)
print("LASTERR EXPLAINS" if st == 0x18 and "ERR_INVALID_BAUDRATE" in lasterr() else "LASTERR SILENT (driver before 1.5, or the bus runs at 250k)", flush=True)
if st == 0:
    d.PassThruDisconnect(ch2.value)

d.PassThruClose(dev.value)
print("done: %d gate frames refused" % refused, flush=True)
